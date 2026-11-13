import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from uuid import uuid4
import pytest
from ad_agent.app import build_engine
from ad_agent.config import Settings, ROOT
from ad_agent.conversation import resolve_turn
from ad_agent.models import Request
from ad_agent.tools import Context, BusinessTools, Empty


def turn(engine,message,owner='u',session='s',day='2026-03-26'):
    req=Request(message=message,session_id=session,request_id=uuid4().hex)
    return asyncio.run(engine.run(req,Context(owner,'B1',('A1',),day,session)))


@pytest.fixture
def settings(tmp_path):
    return Settings(trace_db=tmp_path/'runtime.sqlite')


def test_date_completion_restart_feedback_and_reopen(settings):
    engine=build_engine(settings)
    assert turn(engine,'你好，查素材M0001消耗')['status']=='COLLECTING'
    engine=build_engine(settings)
    result=turn(engine,'2026-03-24')
    assert result['status']=='ANSWERED_WAITING'
    assert '3.00' in result['response']
    assert turn(engine,'明白了')['status']=='CONFIRMED_RESOLVED'
    result=turn(engine,'这个数不对，改成2026-03-25')
    assert result['status']=='ANSWERED_WAITING'
    assert '不适用' in result['response']
    events=engine.store.events(result['request_id'],'u:B1')
    assert 'diagnose' in [s['name'] for e in events if e['type']=='documents_loaded' for s in e['payload']['skills']]


def test_relative_dates_and_no_topic_leak(settings):
    engine=build_engine(settings)
    assert '2026-03-25' in turn(engine,'查M0001昨天ROI')['response']
    assert '2026-03-24' in turn(engine,'前天呢')['response']
    assert turn(engine,'ROI怎么算')['actions']==1
    assert turn(engine,'昨天呢')['status']=='COLLECTING'


@pytest.mark.parametrize('message', ['怎么查达人消耗','你好，怎么查消耗','如何查状态'])
def test_usage_route_not_material(settings,message):
    engine=build_engine(settings)
    result=turn(engine,message)
    events=engine.store.events(result['request_id'],'u:B1')
    assert [e['payload']['tool'] for e in events if e['type']=='action']==['search_knowledge']
    assert result['status']=='ANSWERED_WAITING'


def test_no_false_resolution_or_history_leak(settings):
    engine=build_engine(settings)
    assert turn(engine,'明白了')['status']=='COLLECTING'
    turn(engine,'查M0001 2026-03-24 ROI')
    assert turn(engine,'2026-03-25',owner='other')['status']=='COLLECTING'
    assert turn(engine,'2026-03-25',session='other')['status']=='COLLECTING'
    assert turn(engine,'好')['status']!='CONFIRMED_RESOLVED'
    turn(engine,'查M0001 2026-03-24 ROI')
    assert turn(engine,'好个什么，根本没解决')['status']!='CONFIRMED_RESOLVED'


def test_expiry_and_concurrency(settings):
    engine=build_engine(settings)
    turn(engine,'查M0001消耗')
    with sqlite3.connect(settings.trace_db) as db:
        db.execute('UPDATE conversation_state SET updated=0')
    assert turn(engine,'2026-03-24')['status']=='COLLECTING'
    with engine.sessions.turn('u:B1','s','held'):
        with pytest.raises(RuntimeError,match='Session already processing'):
            turn(engine,'你好')


def test_multiple_materials_and_mixed_handoff(settings):
    engine=build_engine(settings)
    assert turn(engine,'素材M0001和M0002昨天表现')['status']=='COLLECTING'
    result=turn(engine,'转人工，查M0001 2026-03-24 ROI')
    assert result['status']=='ANSWERED_WAITING'
    assert result['actions']==2
    assert turn(engine,'转人工')['status']=='UNRESOLVED'


@pytest.mark.parametrize('text,start,end', [('昨天','2026-03-25','2026-03-25'),('前天','2026-03-24','2026-03-24'),('近7天','2026-03-19','2026-03-25'),('2026-02-28','2026-02-28','2026-02-28')])
def test_absolute_dates(text,start,end):
    state=resolve_turn('素材M0001'+text,{},'2026-03-26')
    assert state['slots']['start_date']==start
    assert state['slots']['end_date']==end


def test_new_explicit_query_does_not_inherit_dates():
    old={'intent':'material','slots':{'material_id':'M0001','start_date':'2026-03-24','end_date':'2026-03-24'}}
    assert 'start_date' not in resolve_turn('查素材M0002',old,'2026-03-26')['slots']
    corrected=resolve_turn('不是，是2026-03-25',old,'2026-03-26')
    assert corrected['slots']['material_id']=='M0001'
    assert corrected['slots']['start_date']=='2026-03-25'


def telemetry_file(tmp_path):
    now=datetime.now(timezone.utc)
    def row(kind='view',**overrides):
        return dict(customer_id='u',brand_id='B1',advertiser_id='A1',material_id='M0001',kind=kind,status='open',occurred_at=(now-timedelta(hours=1)).isoformat(),start_date='2026-03-24',end_date='2026-03-24',**overrides)
    rows=[row(),row('alert')]
    rows.append(dict(rows[1],brand_id='B2',material_id='SECRET'))
    rows.append(dict(rows[1],occurred_at=(now-timedelta(days=2)).isoformat(),material_id='STALE'))
    path=tmp_path/'context.json'
    path.write_text(json.dumps(rows),encoding='utf-8')
    return path


def test_single_context_hypothesis_confirmation(settings,tmp_path):
    path=telemetry_file(tmp_path)
    engine=build_engine(replace(settings,customer_context_file=str(path)))
    result=turn(engine,'你好')
    assert result['status']=='COLLECTING'
    assert '指标异常' in result['response']
    assert 'SECRET' not in result['response'] and 'STALE' not in result['response']
    assert '3.00' in turn(engine,'是的')['response']


def test_context_denial_no_repeated_guess(settings,tmp_path):
    engine=build_engine(replace(settings,customer_context_file=str(telemetry_file(tmp_path))))
    turn(engine,'你好')
    assert 'M0001' not in turn(engine,'不是')['response']
    assert 'M0001' not in turn(engine,'你好')['response']


def test_stale_candidate_not_confirmed():
    previous={'suggestion':{'material_id':'M0001'},'suggestion_expires_at':0}
    assert resolve_turn('是的',previous,'2026-03-26')['intent']=='clarify'


def test_knowledge_manifest_and_bad_metadata(settings,tmp_path):
    engine=build_engine(settings)
    result=turn(engine,'ROI怎么算')
    events=engine.store.events(result['request_id'],'u:B1')
    assert len(next(e['payload']['knowledge_version'] for e in events if e['type']=='documents_loaded'))==64
    (tmp_path/'bad.json').write_text('[{"id":"bad"}]',encoding='utf-8')
    with pytest.raises(ValueError,match='provenance'):
        BusinessTools(ROOT/'fixtures/business.json',tmp_path)
