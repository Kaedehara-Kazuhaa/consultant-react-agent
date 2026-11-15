import asyncio
from uuid import uuid4
import pytest
from ad_agent.app import build_engine
from ad_agent.config import Settings
from ad_agent.models import Request
from ad_agent.tools import Context, Daily
from ad_agent.analytics import AnalyticsQuery


def ask(engine,message,session='s'):
    return asyncio.run(engine.run(Request(request_id=uuid4().hex,session_id=session,message=message),Context('u','B1',('A1',),'2026-03-26',session)))

@pytest.mark.parametrize('message',[
    '素材M0001在2026-03-24至2026-04-01的消耗',
    '全部素材在2026-03-24至2026-04-01的ROI排行',
])
def test_future_end_warns_and_returns_existing_data(tmp_path,message):
    e=build_engine(Settings(trace_db=tmp_path/'r.sqlite'))
    r=ask(e,message)
    assert r['status']=='ANSWERED_WAITING'
    assert '晚于今天' in r['response'] and '2026-04-01' in r['response']
    assert '截至' in r['response'] or '至今天' in r['response']
    assert '最新日报日期为2026-03-26' in r['response']
    events=e.store.events(r['request_id'],'u:B1')
    reports=[v['payload'] for v in events if v['type']=='observation' and v['payload']['tool'] in {'query_performance','get_material_daily_performance'}]
    assert reports[0]['data']['end_date']=='2026-03-26'
    assert reports[0]['data']['known_total_cost']=='100.00'
    assert all(v['payload']['description'] for v in events if v['type']=='action')
    assert all(s['description'] for v in events if v['type']=='documents_loaded' for s in v['payload']['skills'])


def test_entirely_future_and_invalid_range(tmp_path):
    e=build_engine(Settings(trace_db=tmp_path/'r.sqlite'))
    r=ask(e,'素材M0001在2026-04-01至2026-04-02的ROI')
    assert r['stop_reason']=='FUTURE_RANGE' and r['actions']==0
    assert '全部晚于今天' in r['response']
    r=ask(e,'素材M0001在2026-03-25至2026-03-24的ROI',session='bad')
    assert r['status']=='UNRESOLVED'


@pytest.mark.parametrize('kind',['material','analytics'])
def test_tools_independently_exclude_future_rows(tmp_path,kind):
    e=build_engine(Settings(trace_db=tmp_path/'r.sqlite'))
    t=e.business_tools
    t.fixtures['daily'].append(dict(material_id='M0001',advertiser_id='A1',date='2026-03-27',total_cost='999999',overall_gmv='999999'))
    ctx=Context('u','B1',('A1',),'2026-03-26','s')
    args=dict(start_date='2026-03-24',end_date='2026-04-01')
    if kind=='material':r=asyncio.run(t.daily(Daily(material_id='M0001',**args),ctx))
    else:r=asyncio.run(t.analytics(AnalyticsQuery(ids=['M0001'],**args),ctx))
    assert r.data['end_date']=='2026-03-26'
    assert r.data['known_total_cost']=='100.00'
    assert r.data['period']['future_end'] is True
    assert r.data['data_through']=='2026-03-26'


def test_restricted_handoff_hides_reason_and_guides_rule_or_data(tmp_path):
    e=build_engine(Settings(trace_db=tmp_path/'r.sqlite'))
    for _ in range(3):ask(e,'人工客服',session='history')
    replies=[ask(e,'人工客服',session='restricted') for _ in range(5)]
    for r in replies:
        assert r['stop_reason']=='HISTORY_RESTRICTED'
        assert r['status']=='COLLECTING'
        assert '规则' in r['response']
        assert not any(w in r['response'] for w in ['近期多次','资格','超限','历史','正在为您转接'])
    assert len({r['response'] for r in replies})==3


def test_skills_follow_primary_intent_and_include_statistics_diagnosis(tmp_path):
    e=build_engine(Settings(trace_db=tmp_path/'r.sqlite'))
    profile=e.profile
    assert {s.name for s in profile.select('怎么查达人消耗','knowledge')}=={'knowledge'}
    assert {s.name for s in profile.select('素材M0001和M0003消耗排行','analytics')}=={'analytics'}
    skills=profile.select('这个达人的数字不对','analytics')
    assert {s.name for s in skills}=={'analytics','diagnose'}
    assert 'query_performance' in next(s.tools for s in skills if s.name=='diagnose')
