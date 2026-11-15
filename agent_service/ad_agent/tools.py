from dataclasses import dataclass
from contextlib import closing
import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
import hashlib
from pathlib import Path
import sqlite3
from pydantic import BaseModel, ConfigDict, Field
from .models import Observation
from .periods import bounded_period

@dataclass(frozen=True)
class Context:
    customer_id: str
    brand_id: str
    accounts: tuple[str, ...]
    business_day: str
    session_id: str
    role: str = 'trader'

class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class Search(Arguments):
    question: str = Field(min_length=1, max_length=2000)

class Material(Arguments):
    material_id: str = Field(min_length=1, max_length=80)

class Daily(Material):
    start_date: str
    end_date: str

class Empty(Arguments):
    pass

class Handoff(Arguments):
    summary: str = Field(min_length=1, max_length=2000)

@dataclass
class Tool:
    name: str
    description: str
    arguments: type[Arguments]
    handler: object

    def schema(self):
        return {"name": self.name, "description": self.description, "parameters": self.arguments.model_json_schema()}

class BusinessTools:
    """Fixed SQL templates only. No SQL or identity parameters are model-controlled."""
    def __init__(self, fixture_path: Path, knowledge_dir: Path, business_db="", customer_context_file=""):
        from .analytics import AnalyticsQuery
        self.fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.knowledge_dir = knowledge_dir
        self.business_db = business_db
        self.customer_context_file = customer_context_file
        self.knowledge = []
        for path in sorted(knowledge_dir.glob('*.json')):
            if not path.resolve().is_relative_to(knowledge_dir.resolve()) or path.stat().st_size > 256_000:
                raise ValueError('Invalid knowledge document path or size')
            rows=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(rows,list):
                raise ValueError('Knowledge document must be an array')
            for row in rows:
                if not all(isinstance(row.get(k),str) and row[k] for k in ['id','title','content','version','source','scope','rule_type']) or not isinstance(row.get('assumption'),bool):
                    raise ValueError('Knowledge entry is missing provenance metadata')
                if not isinstance(row.get('keywords'),list) or not row['keywords'] or not all(isinstance(k,str) and k for k in row['keywords']):
                    raise ValueError('Invalid knowledge keywords')
            self.knowledge.extend(rows)
        if len({r['id'] for r in self.knowledge}) != len(self.knowledge):
            raise ValueError('Duplicate knowledge IDs')
        self.knowledge_version=hashlib.sha256(json.dumps(self.knowledge,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        self.registry = {
            t.name: t for t in [
                Tool("search_knowledge", "检索业务指标定义与使用规则", Search, self.search),
                Tool("resolve_material", "在授权范围确认明确素材编号", Material, self.resolve),
                Tool("get_material_status", "查询素材快照状态", Material, self.status),
                Tool("get_material_daily_performance", "查询指定素材闭区间日报，金额人民币；不限31天", Daily, self.daily),
                Tool("query_performance", "按日期批量查询素材或达人，汇总及按消耗/GMV/ROI排行；支持分页", AnalyticsQuery, self.analytics),
                Tool("get_customer_context", "读取授权用户近期浏览和异常；缺失时返回空", Empty, self.customer_context),
                Tool("handoff_record", "人工策略由服务端执行；此工具不具备外部转接权限", Handoff, self.handoff),
            ]
        }

    def records(self, ctx: Context, material_id):
        if ctx.role!='trader':
            return None,[]
        if self.business_db:
            uri = Path(self.business_db).resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as db:
                db.row_factory = sqlite3.Row
                placeholders = ",".join("?" for _ in ctx.accounts) or "NULL"
                row = db.execute(f"SELECT id,advertiser_id,title,delivery_status,test_status,last_sync_at,report_last_sync_at FROM materials WHERE id=? AND advertiser_id IN ({placeholders})", (material_id, *ctx.accounts)).fetchone()
                if not row:
                    return None, []
                daily = [dict(r) for r in db.execute("SELECT date,total_cost,overall_gmv,synced_at FROM material_daily_trend_cache WHERE material_id=? AND advertiser_id=? ORDER BY date", (material_id, row["advertiser_id"]))]
                return dict(row), daily
        material = next((r for r in self.fixtures["materials"] if r["id"] == material_id and r["advertiser_id"] in ctx.accounts), None)
        return material, [r for r in self.fixtures["daily"] if material and r["material_id"] == material_id and r["advertiser_id"] in ctx.accounts]

    async def search(self, args: Search, ctx):
        hits = []
        for row in self.knowledge:
            score = sum(len(word) for word in row["keywords"] if word.lower() in args.question.lower())
            if score:
                hits.append((score,row))
        entries = [r for _,r in sorted(hits,key=lambda h:(-h[0],h[1]['id']))[:3]]
        return Observation(status="OK" if entries else "EMPTY", data={"entries": entries}, evidence_refs=[r["id"] for r in entries])

    def catalog(self,ctx,q='',page=1):
        if ctx.role!='trader':
            return {'items':[],'total':0,'page':page,'page_size':20,'brand_id':ctx.brand_id,'account_count':0}
        size=20
        if self.business_db:
            with closing(sqlite3.connect(Path(self.business_db).resolve().as_uri()+'?mode=ro',uri=True)) as db:
                db.row_factory=sqlite3.Row
                marks=','.join('?' for _ in ctx.accounts) or 'NULL'
                where=f'm.advertiser_id IN ({marks}) AND (m.id=? OR m.title LIKE ?)'
                args=(*ctx.accounts,q,'%'+q+'%')
                total=db.execute(f'SELECT COUNT(*) FROM materials m WHERE {where}',args).fetchone()[0]
                rows=[dict(r) for r in db.execute(f'''SELECT m.id,m.title,m.delivery_status,
                  MIN(d.date) AS start_date,MAX(d.date) AS end_date,COUNT(d.date) AS report_days
                  FROM materials m LEFT JOIN material_daily_trend_cache d ON d.material_id=m.id AND d.advertiser_id=m.advertiser_id
                  WHERE {where} GROUP BY m.id,m.advertiser_id ORDER BY COUNT(d.date) DESC,m.id LIMIT ? OFFSET ?''',(*args,size,(page-1)*size))]
        else:
            all_rows=[]
            for m in self.fixtures['materials']:
                if m['advertiser_id'] not in ctx.accounts or (q and q not in m['id'] and q not in m.get('title','')):continue
                days=sorted(r['date'] for r in self.fixtures['daily'] if r['material_id']==m['id'] and r['advertiser_id'] in ctx.accounts)
                all_rows.append({**{k:m.get(k) for k in ['id','title','delivery_status']},'start_date':days[0] if days else None,'end_date':days[-1] if days else None,'report_days':len(days)})
            total=len(all_rows); rows=all_rows[(page-1)*size:page*size]
        return {'items':rows,'total':total,'page':page,'page_size':size,'data_mode':'dump_snapshot' if self.business_db else 'synthetic','brand_id':ctx.brand_id,'account_count':len(ctx.accounts)}

    async def resolve(self, args: Material, ctx):
        material, _ = await asyncio.to_thread(self.records, ctx, args.material_id)
        if not material:
            return Observation(status="EMPTY", data={"message": "当前可查询范围内未找到"})
        return Observation(status="OK", data={"material_id": material["id"], "title": material["title"]}, evidence_refs=["material:"+material["id"]])

    async def status(self, args: Material, ctx):
        material, _ = await asyncio.to_thread(self.records, ctx, args.material_id)
        if not material:
            return Observation(status="EMPTY", data={"message": "当前可查询范围内未找到"})
        return Observation(status="OK", data={k: material.get(k) for k in ["id", "title", "delivery_status", "test_status", "last_sync_at", "report_last_sync_at"]}, evidence_refs=["material:"+material["id"]])

    async def daily(self, args: Daily, ctx):
        try:
            start, end, period = bounded_period(args.start_date,args.end_date,ctx.business_day)
        except ValueError:
            return Observation(status="ERROR", error_code="INVALID_RANGE")
        if period['entirely_future']:
            return Observation(status='EMPTY',data={'message':period['notice'],'period':period})
        args=args.model_copy(update={'end_date':end.isoformat()})
        material, rows = await asyncio.to_thread(self.records, ctx, args.material_id)
        if not material:
            return Observation(status="EMPTY", data={"message": "当前可查询范围内未找到"})
        selected = [r for r in rows if args.start_date <= r["date"] <= args.end_date]
        if not selected:
            return Observation(status="EMPTY", data={"available_dates": [r["date"] for r in rows if r['date']<=ctx.business_day], "period":period, "message": "该区间无记录，不等于消耗为0"})
        dates = sorted({r["date"] for r in selected})
        if len(dates) != len(selected):
            return Observation(status="ERROR", error_code="DUPLICATE_DAILY_KEY")
        missing = (end-start).days+1-len(dates)
        nulls = any(r[k] is None for r in selected for k in ["total_cost", "overall_gmv"])
        sums = {k: sum((Decimal(r[k]) for r in selected if r[k] is not None), Decimal(0)) for k in ["total_cost", "overall_gmv"]}
        roi = None if nulls or not sums["total_cost"] else str((sums["overall_gmv"]/sums["total_cost"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        partial = bool(missing or nulls)
        refs=[f"daily:{args.material_id}:{d}" for d in dates]
        if len(refs)>60:
            digest=hashlib.sha256(json.dumps(selected,sort_keys=True).encode()).hexdigest()[:24]
            refs=['daily-range:'+digest]
        return Observation(status="PARTIAL" if partial else "OK", data={"period":period, "data_from":dates[0], "data_through":dates[-1], "material_id": args.material_id, "start_date": args.start_date, "end_date": args.end_date, "daily": selected, "known_total_cost": str(sums["total_cost"].quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)), "known_overall_gmv": str(sums["overall_gmv"].quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)), "roi": roi, "roi_note": "不可完整计算" if nulls else "不适用" if not sums["total_cost"] else "仅汇总已有记录" if partial else "完整记录", "missing_days": missing, "null_fields": nulls, "snapshot": "historical", "currency":"CNY", "synthetic": not bool(self.business_db)}, evidence_refs=refs)

    async def analytics(self,args,ctx):
        from .analytics import execute
        return await asyncio.to_thread(execute,self,args,ctx)

    async def customer_context(self, args, ctx):
        if ctx.role!='trader':
            return Observation(status='EMPTY',data={'message':'访客不能读取用户业务上下文'})
        if self.customer_context_file:
            rows = await asyncio.to_thread(lambda: json.loads(Path(self.customer_context_file).read_text(encoding="utf-8")))
            candidates = []
            for row in rows:
                if row.get('customer_id') != ctx.customer_id or row.get('brand_id') != ctx.brand_id or row.get('advertiser_id') not in ctx.accounts:
                    continue
                try:
                    occurred = datetime.fromisoformat(row['occurred_at'])
                    age = (datetime.now(timezone.utc)-occurred).total_seconds()
                    if occurred.tzinfo is None or not 0 <= age <= 86400:
                        continue
                    if row.get('kind') not in {'alert','view'} or (row['kind']=='alert' and row.get('status')!='open'):
                        continue
                    date.fromisoformat(row['start_date'])
                    date.fromisoformat(row['end_date'])
                    if not row.get('material_id') or row['start_date'] > row['end_date']:
                        continue
                except (ValueError, TypeError, KeyError):
                    continue
                candidates.append((row['kind']=='alert', occurred, row))
            if candidates:
                row = max(candidates,key=lambda r:(r[0],r[1]))[2]
                suggestion = {k:row[k] for k in ['material_id','start_date','end_date']}
                suggestion['query']='performance'
                return Observation(status='OK',data={'suggestion':suggestion,'suggestion_expires_at':datetime.fromisoformat(row['occurred_at']).timestamp()+86400,'source_kind':row['kind'],'occurred_at':row['occurred_at'],'requires_confirmation':True},evidence_refs=['context:'+str(row['material_id'])])
        return Observation(status="EMPTY", data={"recent_views": [], "metric_alerts": [], "message": "未接入可核验的近期用户上下文，请用户补充需求"})

    async def handoff(self, args, ctx):
        return Observation(status="ERROR", error_code="HANDOFF_SERVER_POLICY_REQUIRED", data={"message": "转接提示只能由服务端策略判定，不接外部人工接口"})
