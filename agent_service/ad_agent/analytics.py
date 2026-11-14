"""Scoped batch/ranking queries; sums and sorting use Decimal, never model arithmetic."""
from contextlib import closing
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Literal
from pydantic import Field
from .models import Observation
from .tools import Arguments


class AnalyticsQuery(Arguments):
    entity: Literal['material','influencer'] = 'material'
    mode: Literal['batch','ranking','summary'] = 'batch'
    ids: list[str] = Field(default_factory=list, max_length=100)
    name: str = Field(default='', max_length=200)
    start_date: str
    end_date: str
    metric: Literal['cost','gmv','roi'] = 'cost'
    descending: bool = True
    limit: int = Field(default=20,ge=1,le=100)
    page: int = Field(default=1,ge=1,le=100000)


def source_rows(tools, ctx, entity):
    if tools.business_db:
        with closing(sqlite3.connect(Path(tools.business_db).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            marks=','.join('?' for _ in ctx.accounts) or 'NULL'
            if entity=='material':
                objects=[dict(r) for r in db.execute(f'SELECT id,title AS name,advertiser_id FROM materials WHERE advertiser_id IN ({marks})',ctx.accounts)]
                daily=[dict(r) for r in db.execute(f'SELECT material_id AS entity_id,advertiser_id,date,total_cost,overall_gmv FROM material_daily_trend_cache WHERE advertiser_id IN ({marks})',ctx.accounts)]
            else:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='influencer_daily_trend_cache'").fetchone():
                    raise ValueError('INFLUENCER_DATA_NOT_PREPARED')
                objects=[dict(r) for r in db.execute(f'SELECT id,nickname AS name,advertiser_id FROM influencers WHERE advertiser_id IN ({marks})',ctx.accounts)]
                daily=[dict(r) for r in db.execute(f'SELECT influencer_id AS entity_id,advertiser_id,date,total_cost,overall_gmv FROM influencer_daily_trend_cache WHERE advertiser_id IN ({marks})',ctx.accounts)]
    else:
        if entity=='material':
            objects=[dict(id=r['id'],name=r.get('title',''),advertiser_id=r['advertiser_id']) for r in tools.fixtures['materials'] if r['advertiser_id'] in ctx.accounts]
            daily=[dict(r,entity_id=r['material_id']) for r in tools.fixtures['daily'] if r['advertiser_id'] in ctx.accounts]
        else:
            objects=[dict(id=r['id'],name=r.get('nickname',''),advertiser_id=r['advertiser_id']) for r in tools.fixtures.get('influencers',[]) if r['advertiser_id'] in ctx.accounts]
            daily=[dict(r,entity_id=r['influencer_id']) for r in tools.fixtures.get('influencer_daily',[]) if r['advertiser_id'] in ctx.accounts]
    return objects,daily


def execute(tools,args,ctx):
    try:
        start,end=date.fromisoformat(args.start_date),date.fromisoformat(args.end_date)
        if end<start:raise ValueError()
    except ValueError:
        return Observation(status='ERROR',error_code='INVALID_RANGE')
    try:
        objects,daily=source_rows(tools,ctx,args.entity)
    except ValueError:
        return Observation(status='ERROR',error_code='INFLUENCER_DATA_NOT_PREPARED')
    if args.name:
        matches=[r for r in objects if r['name']==args.name]
        distinct={r['id'] for r in matches}
        if len(distinct)>1:
            return Observation(status='EMPTY',data={'message':'达人昵称重名，请提供明确达人编号。'})
        objects=matches
    if args.ids:objects=[r for r in objects if r['id'] in args.ids]
    if not objects:
        return Observation(status='EMPTY',data={'message':'当前可查询范围内未找到对应对象，请核对编号或昵称。'})
    chosen={(r['id'],r['advertiser_id']):dict(r) for r in objects}
    groups={k:[] for k in chosen}
    seen=set()
    for row in daily:
        key=(row['entity_id'],row['advertiser_id'])
        if key not in chosen or not args.start_date<=row['date']<=args.end_date:continue
        unique=(*key,row['date'])
        if unique in seen:return Observation(status='ERROR',error_code='DUPLICATE_DAILY_KEY')
        seen.add(unique); groups[key].append(row)
    money=lambda v:str(v.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
    results=[]
    all_cost=all_gmv=Decimal(0)
    incomplete=False
    any_null=False
    for key,rows in groups.items():
        if not rows:continue
        cost=sum((Decimal(str(r['total_cost'])) for r in rows if r['total_cost'] is not None),Decimal(0))
        gmv=sum((Decimal(str(r['overall_gmv'])) for r in rows if r['overall_gmv'] is not None),Decimal(0))
        nulls=any(r[k] is None for r in rows for k in ['total_cost','overall_gmv'])
        any_null |= nulls
        missing=(end-start).days+1-len(rows)
        incomplete |= bool(missing or nulls)
        ratio=gmv/cost if cost and not nulls else None
        results.append({**chosen[key],'cost':money(cost),'gmv':money(gmv),'roi':money(ratio) if ratio is not None else None,'report_days':len(rows),'missing_days':missing,'null_fields':nulls,'_sort':{'cost':cost,'gmv':gmv,'roi':ratio}[args.metric]})
        all_cost+=cost;all_gmv+=gmv
    no_records=[r['id'] for k,r in chosen.items() if not groups[k]]
    not_found=[i for i in dict.fromkeys(args.ids) if i not in {r['id'] for r in objects}]
    if not results:
        return Observation(status='EMPTY',data={'message':'指定区间无日报记录，不等于0。','no_records':no_records,'not_found':not_found})
    # NULL ROI sorts after real values in both directions; stable ID tie-break.
    results.sort(key=lambda r:(r['id'],r['advertiser_id']))
    results.sort(key=lambda r:r['_sort'] if r['_sort'] is not None else Decimal(0),reverse=args.descending)
    results.sort(key=lambda r:r['_sort'] is None)
    offset=(args.page-1)*args.limit
    page=results[offset:offset+args.limit]
    for i,row in enumerate(page,offset+1):row.pop('_sort');row['rank']=i
    # Missing objects/days mean summary contains only known records, not full totals.
    incomplete |= bool(no_records or not_found)
    digest=hashlib.sha256(json.dumps({'scope':[ctx.brand_id,list(ctx.accounts)],'query':args.model_dump(),'rows':page,'cost':str(all_cost),'gmv':str(all_gmv)},sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
    data={'entity':args.entity,'mode':args.mode,'metric':args.metric,'descending':args.descending,'start_date':args.start_date,'end_date':args.end_date,'rows':page,'total':len(results),'page':args.page,'page_size':args.limit,'known_total_cost':money(all_cost),'known_overall_gmv':money(all_gmv),'roi':None if any_null or not all_cost else money(all_gmv/all_cost),'roi_note':'仅代表已有记录' if incomplete else '完整记录','partial':incomplete,'no_records':no_records[:20],'no_records_count':len(no_records),'not_found':not_found,'currency':'CNY','synthetic':not bool(tools.business_db),'source_table':'material_daily_trend_cache' if args.entity=='material' else 'influencer_daily_trend_cache'}
    return Observation(status='PARTIAL' if incomplete else 'OK',data=data,evidence_refs=['analytics:'+digest])
