import asyncio
import json
import sqlite3
from dataclasses import replace
from uuid import uuid4
import pytest
from ad_agent.app import build_engine, create_app
from ad_agent.config import Settings, ROOT
from ad_agent.models import Request
from ad_agent.tools import Context, BusinessTools
from ad_agent.analytics import AnalyticsQuery
from ad_agent.conversation import resolve_turn
from fastapi.testclient import TestClient


def run(engine, message, session='session1', customer='u', request_id=None):
    req=Request(message=message,session_id=session,request_id=request_id or uuid4().hex)
    return asyncio.run(engine.run(req,Context(customer,'B1',('A1',),'2026-03-26',session)))


def test_silence_reopen_and_preserve_slots(tmp_path, monkeypatch):
    settings=Settings(trace_db=tmp_path/'run.sqlite')
    engine=build_engine(settings)
    first=run(engine,'查询素材M0001在2026-03-24的消耗')
    assert first['status']=='ANSWERED_WAITING'
    with sqlite3.connect(settings.trace_db) as db:
        db.execute('UPDATE support_issues SET answered_at=answered_at-1801')
        db.execute('UPDATE conversation_state SET updated=0')
    engine=build_engine(settings) # restart cannot discard slots, timer or state
    state=engine.support.state('u:B1','session1')
    assert state['issues'][0]['status']=='CONFIRMED_RESOLVED'
    assert state['issues'][0]['resolution_kind']=='silence'
    assert sum(r['kind']=='silence_resolution' for r in state['events'])==1
    rejected=run(engine,'这个问题没解决')
    assert rejected['issue_id']==first['issue_id']
    assert rejected['status']=='UNRESOLVED'
    assert sum(r['kind']=='negative_feedback' for r in engine.support.state('u:B1','session1')['events'])==1
    with sqlite3.connect(settings.trace_db) as db:
        db.execute('UPDATE support_issues SET answered_at=0')
    engine.support.reconcile()
    assert engine.support.state('u:B1','session1')['issues'][0]['status']=='UNRESOLVED'
    later=run(engine,'2026-03-25')
    assert later['status']=='ANSWERED_WAITING' and '不适用' in later['response']


def test_no_silence_resolution_for_clarification_or_failed_query(tmp_path):
    settings=Settings(trace_db=tmp_path/'run.sqlite')
    e=build_engine(settings)
    run(e,'你好')
    run(e,'查询素材M0001在2026-01-01的消耗',session='empty')
    with sqlite3.connect(settings.trace_db) as db:db.execute('UPDATE support_issues SET answered_at=0')
    e.support.reconcile()
    assert all(r['status']!='CONFIRMED_RESOLVED' for s in ['session1','empty'] for r in e.support.state('u:B1',s)['issues'])


def test_handoff_counts_dedup_restart_history_scope_and_frozen_eligibility(tmp_path):
    settings=Settings(trace_db=tmp_path/'run.sqlite')
    e=build_engine(settings)
    rid=uuid4().hex
    first=run(e,'转人工',request_id=rid)
    assert run(e,'转人工',request_id=rid)==first
    assert e.support.state('u:B1','session1')['handoff_request_count']==1
    assert run(e,'转人工')['status']=='COLLECTING'
    e=build_engine(settings)
    assert run(e,'转人工')['status']=='COLLECTING'
    result=run(e,'转人工')
    assert result['status']=='HANDOFF_PENDING' and result['response']=='正在为您转接。'
    assert run(e,'转人工',session='new')['stop_reason']=='HISTORY_RESTRICTED'
    assert run(e,'转人工',customer='other')['status']=='COLLECTING'
    assert e.support.state('other:B1','session1')['handoff_request_count']==1
    assert e.support.state('u:B1','session1')['handoff_request_count']==5
    # Freeze on first message, not on first handoff request.
    run(e,'你好',session='frozen',customer='fresh')
    for _ in range(3):run(e,'转人工',session='other',customer='fresh')
    assert run(e,'转人工',session='frozen',customer='fresh')['stop_reason']=='HANDOFF_INTERCEPTED'


def test_repeat_business_result_does_not_inflate_attempts(tmp_path):
    e=build_engine(Settings(trace_db=tmp_path/'run.sqlite'))
    for _ in range(3):run(e,'查素材M0001在2026-03-24的消耗')
    assert e.support.state('u:B1','session1')['issues'][0]['attempts']==1


@pytest.fixture
def analytics_tools(tmp_path):
    data=json.loads((ROOT/'fixtures/business.json').read_text(encoding='utf-8'))
    data['materials'] += [dict(data['materials'][0],id='M0003')]
    data['daily'] += [dict(material_id='M0003',advertiser_id='A1',date='2026-03-24',total_cost='200',overall_gmv='400')]
    data['influencers']=[dict(id='10',advertiser_id='A1',nickname='测试达人'),dict(id='11',advertiser_id='A2',nickname='秘密达人')]
    data['influencer_daily']=[dict(influencer_id='10',advertiser_id='A1',date='2026-03-24',total_cost='500',overall_gmv='2500'),dict(influencer_id='11',advertiser_id='A2',date='2026-03-24',total_cost='9999',overall_gmv='99999')]
    path=tmp_path/'fixture.json';path.write_text(json.dumps(data),encoding='utf-8')
    return BusinessTools(path,ROOT/'profiles/default/knowledge')


def query(tools,**kwargs):
    return asyncio.run(tools.analytics(AnalyticsQuery(start_date='2026-03-24',end_date='2026-03-24',**kwargs),Context('u','B1',('A1',),'2026-03-26','s')))


def test_batch_ranking_decimal_and_pagination(analytics_tools):
    result=query(analytics_tools,ids=['M0001','M0003','M0002'],mode='ranking',metric='roi',limit=1)
    assert result.status=='PARTIAL'
    d=result.data
    assert d['rows'][0]['id']=='M0001' # 3 vs 2
    assert d['known_total_cost']=='300.00' and d['known_overall_gmv']=='700.00'
    assert d['not_found']==['M0002'] and d['total']==2
    page2=query(analytics_tools,ids=['M0001','M0003'],mode='ranking',metric='roi',limit=1,page=2).data
    assert page2['rows'][0]['id']=='M0003'
    assert page2['roi']=='2.33' # weighted, not mean(3,2)
    assert page2['known_total_cost']=='300.00' # pagination must not truncate totals


def test_influencer_uses_own_reports_not_material_sum(analytics_tools):
    result=query(analytics_tools,entity='influencer',name='测试达人',mode='summary')
    assert result.data['known_total_cost']=='500.00'
    assert result.data['roi']=='5.00'
    assert result.data['source_table']=='influencer_daily_trend_cache'
    assert query(analytics_tools,entity='influencer',ids=['11']).status=='EMPTY'


def test_ambiguous_name_null_zero_and_unrounded_sort(analytics_tools):
    analytics_tools.fixtures['influencers'].append(dict(id='12',advertiser_id='A1',nickname='测试达人'))
    assert query(analytics_tools,entity='influencer',name='测试达人').status=='EMPTY'
    data=analytics_tools.fixtures
    data['daily']=[dict(material_id='M0001',advertiser_id='A1',date='2026-03-24',total_cost='100',overall_gmv='300.001'),dict(material_id='M0003',advertiser_id='A1',date='2026-03-24',total_cost='100',overall_gmv='300.002')]
    rows=query(analytics_tools,mode='ranking',metric='roi').data['rows']
    assert [r['id'] for r in rows]==['M0003','M0001']
    assert rows[0]['roi']==rows[1]['roi']=='3.00'
    data['daily'][0]['total_cost']='0'
    data['daily'][0]['overall_gmv']='0'
    rows=query(analytics_tools,mode='ranking',metric='roi',descending=False).data['rows']
    assert rows[-1]['id']=='M0001' and rows[-1]['roi'] is None
    data['daily'][1]['total_cost']=None
    result=query(analytics_tools)
    assert result.data['roi'] is None and result.status=='PARTIAL'


def test_long_range_and_inclusive_week(analytics_tools):
    ctx=Context('u','B1',('A1',),'2026-03-26','s')
    from ad_agent.tools import Daily
    result=asyncio.run(analytics_tools.daily(Daily(material_id='M0001',start_date='2025-12-01',end_date='2026-03-24'),ctx))
    assert result.status=='PARTIAL' and result.data['known_total_cost']=='100.00'
    for phrase in ['近7天','最近七天','最近7天']:
        slots=resolve_turn('素材M0001'+phrase,{},'2026-03-26')['slots']
        assert slots['start_date']=='2026-03-20' and slots['end_date']=='2026-03-26'
    parsed=resolve_turn('素材339、127在2026-03-24的消耗',{},'2026-03-26')
    assert parsed['intent']=='analytics' and parsed['slots']['ids']==['339','127']
    explicit=resolve_turn('查询素材339和素材127在2026-03-24的消耗、整体GMV和整体ROI',{},'2026-03-26')
    assert explicit['intent']=='analytics' and explicit['slots']['ids']==['339','127']


def test_lifecycle_http_poll(tmp_path):
    settings=Settings(trace_db=tmp_path/'http.sqlite')
    with TestClient(create_app(settings)) as c:
        result=c.post('/api/run',json={'message':'ROI怎么算','session_id':'s','request_id':uuid4().hex}).json()
        with sqlite3.connect(settings.trace_db) as db:db.execute('UPDATE support_issues SET answered_at=0')
        state=c.get('/api/support/s').json()
        assert state['issues'][0]['id']==result['issue_id']
        assert state['issues'][0]['status']=='CONFIRMED_RESOLVED'
