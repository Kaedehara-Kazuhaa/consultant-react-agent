from dataclasses import dataclass
import asyncio
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import sqlite3
from pydantic import BaseModel, ConfigDict, Field
from .models import Observation

@dataclass(frozen=True)
class Context:
    customer_id: str
    brand_id: str
    accounts: tuple[str, ...]
    business_day: str
    session_id: str

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
    def __init__(self, fixture_path: Path, knowledge_dir: Path, business_db=""):
        self.fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.knowledge_dir = knowledge_dir
        self.business_db = business_db
        self.registry = {
            t.name: t for t in [
                Tool("search_knowledge", "检索业务指标定义与使用规则", Search, self.search),
                Tool("resolve_material", "在授权范围确认明确素材编号", Material, self.resolve),
                Tool("get_material_status", "查询素材快照状态", Material, self.status),
                Tool("get_material_daily_performance", "查询指定素材闭区间日报，最多31天", Daily, self.daily),
                Tool("get_customer_context", "读取授权用户近期浏览和异常；缺失时返回空", Empty, self.customer_context),
                Tool("handoff_record", "人工衔接扩展点；策略存储未接入时明确返回未就绪", Handoff, self.handoff),
            ]
        }

    def records(self, ctx: Context, material_id):
        if self.business_db:
            uri = Path(self.business_db).resolve().as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as db:
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
        for path in sorted(self.knowledge_dir.glob("*.json")):
            for row in json.loads(path.read_text(encoding="utf-8")):
                if any(word.lower() in args.question.lower() for word in row["keywords"]):
                    hits.append(row)
        return Observation(status="OK" if hits else "EMPTY", data={"entries": hits[:3]}, evidence_refs=[r["id"] for r in hits[:3]])

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
            start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
        except ValueError:
            return Observation(status="ERROR", error_code="INVALID_RANGE")
        if not 0 <= (end-start).days <= 30:
            return Observation(status="ERROR", error_code="INVALID_RANGE")
        material, rows = await asyncio.to_thread(self.records, ctx, args.material_id)
        if not material:
            return Observation(status="EMPTY", data={"message": "当前可查询范围内未找到"})
        selected = [r for r in rows if args.start_date <= r["date"] <= args.end_date]
        if not selected:
            return Observation(status="EMPTY", data={"available_dates": [r["date"] for r in rows], "message": "该区间无记录，不等于消耗为0"})
        dates = sorted({r["date"] for r in selected})
        if len(dates) != len(selected):
            return Observation(status="ERROR", error_code="DUPLICATE_DAILY_KEY")
        missing = (end-start).days+1-len(dates)
        nulls = any(r[k] is None for r in selected for k in ["total_cost", "overall_gmv"])
        sums = {k: sum((Decimal(r[k]) for r in selected if r[k] is not None), Decimal(0)) for k in ["total_cost", "overall_gmv"]}
        roi = None if nulls or not sums["total_cost"] else str((sums["overall_gmv"]/sums["total_cost"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        partial = bool(missing or nulls or "2026-03-27" in dates)
        return Observation(status="PARTIAL" if partial else "OK", data={"material_id": args.material_id, "start_date": args.start_date, "end_date": args.end_date, "daily": selected, "known_total_cost": str(sums["total_cost"]), "known_overall_gmv": str(sums["overall_gmv"]), "roi": roi, "roi_note": "不可完整计算" if nulls else "不适用" if not sums["total_cost"] else "仅汇总已有记录" if partial else "完整记录", "missing_days": missing, "null_fields": nulls, "snapshot": "historical", "synthetic": not bool(self.business_db)}, evidence_refs=[f"daily:{args.material_id}:{d}" for d in dates])

    async def customer_context(self, args, ctx):
        # No invented telemetry: the ingestion interface can be added here later.
        return Observation(status="EMPTY", data={"recent_views": [], "metric_alerts": [], "message": "未接入可核验的近期用户上下文，请用户补充需求"})

    async def handoff(self, args, ctx):
        # Fail closed until real history/attempt policy has persistent, trusted state.
        return Observation(status="ERROR", error_code="HANDOFF_POLICY_NOT_CONNECTED", data={"message": "未生成交接记录：历史资格及有效尝试策略尚未接入"})
