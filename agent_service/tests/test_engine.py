import asyncio
from dataclasses import replace
import json
import shutil
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from ad_agent.app import build_engine, create_app
from ad_agent.config import ROOT, Settings
from ad_agent.documents import load_profile
from ad_agent.engine import Engine
from ad_agent.models import Decision, Request
from ad_agent.providers import CompatibleProvider
from ad_agent.tools import BusinessTools, Context
from ad_agent.trace import redact


@pytest.fixture
def settings(tmp_path):
    return Settings(trace_db=tmp_path / 'trace.sqlite')


def ctx(owner='operator'):
    return Context(owner, 'B1', ('A1',), '2026-03-26', 's1')


def request(message='查询素材 M0001 2026-03-24 ROI', rid=None):
    return Request(message=message, request_id=rid or uuid4().hex, session_id='s1')


def run(engine, req=None):
    return asyncio.run(engine.run(req or request(), ctx()))


class Sequence:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.calls = 0

    async def decide(self, state):
        item = self.decisions[min(self.calls, len(self.decisions)-1)]
        self.calls += 1
        return item


def with_provider(settings, provider):
    return Engine(settings, BusinessTools(ROOT/'fixtures/business.json', settings.profile_dir/'knowledge'), provider)


def action(name='resolve_material', **args):
    return {'action_type':'tool','decision_summary':'验证对象后选择下一步。','tool_name':name,'arguments':args or {'material_id':'M0001'}}


def test_material_end_to_end_persistent_trace(settings):
    engine = build_engine(settings)
    req = request()
    result = run(engine, req)
    assert result['status'] == 'ANSWERED_WAITING'
    assert result['actions'] == 2
    assert '3.00' in result['response']
    assert result['evidence_refs'] == ['daily:M0001:2026-03-24']
    events = engine.store.events(req.request_id, 'operator:B1')
    assert [e['seq'] for e in events] == list(range(1, len(events)+1))
    assert {'documents_loaded','decision','action','observation','evaluation','run_finished'} <= {e['type'] for e in events}
    assert build_engine(settings).store.events(req.request_id,'operator:B1') == events


@pytest.mark.parametrize('message,status', [('ROI怎么算','ANSWERED_WAITING'),('你好','COLLECTING'),('我要转人工','COLLECTING'),('查询素材 M0002 状态','COLLECTING')])
def test_branches(settings,message,status):
    result = run(build_engine(settings),request(message))
    assert result['status'] == status
    assert '其他品牌素材' not in result['response']


@pytest.mark.parametrize('date,expected', [('2026-03-25','不适用'),('2026-03-26','不可完整计算'),('2026-03-23','无记录')])
def test_zero_null_missing(settings,date,expected):
    result=run(build_engine(settings),request(f'素材 M0001 {date} ROI'))
    assert expected in result['response']


def test_repeat_and_budget(settings):
    p=Sequence(action())
    result=run(with_provider(settings,p))
    assert result['stop_reason']=='REPEATED_ACTION'
    p=Sequence(action(),action('get_material_status'))
    result=run(with_provider(replace(settings,max_actions=1,trace_db=settings.trace_db.parent/'budget.sqlite'),p))
    assert result['stop_reason']=='ACTION_BUDGET'


@pytest.mark.parametrize('decision,reason',[(action('delete_everything'),'TOOL_NOT_ALLOWED'),(action(material_id='M0001',customer_id='other'),'INVALID_ARGUMENTS'),({'action_type':'answer','decision_summary':'回答','response':'没有依据','evidence_refs':['fake']},'UNSUPPORTED_EVIDENCE')])
def test_guards(settings,decision,reason):
    assert run(with_provider(settings,Sequence(decision)))['stop_reason']==reason


def test_bad_schema_retry_capped(settings):
    provider=Sequence({'bad':'shape'})
    assert run(with_provider(settings,provider))['stop_reason']=='MODEL_UNAVAILABLE'
    assert provider.calls==2


def test_timeout(settings):
    class Slow:
        async def decide(self,state):
            await asyncio.sleep(.1)
    result=run(with_provider(replace(settings,call_timeout=.01,turn_timeout=.015),Slow()))
    assert result['stop_reason']=='TURN_TIMEOUT'


def test_idempotency_and_owner(settings):
    engine=build_engine(settings)
    req=request()
    first=run(engine,req)
    count=len(engine.store.events(req.request_id,'operator:B1'))
    assert run(engine,req)==first
    assert len(engine.store.events(req.request_id,'operator:B1'))==count
    with pytest.raises(ValueError):
        run(engine,request('你好',req.request_id))
    with pytest.raises(KeyError):
        engine.store.events(req.request_id,'different:B1')


def test_profile_customization_and_tool_expansion(settings,tmp_path):
    root=tmp_path/'profile'
    shutil.copytree(settings.profile_dir,root)
    tools=set(build_engine(settings).tools)
    initial=load_profile(root,tools)
    doc=root/'skills/material/SKILL.md'
    doc.write_text(doc.read_text(encoding='utf-8')+'\n回答需清晰标注时间范围。',encoding='utf-8')
    assert load_profile(root,tools).digest!=initial.digest
    doc.write_text(doc.read_text(encoding='utf-8').replace('tools: [resolve_material','tools: [delete_all, resolve_material'),encoding='utf-8')
    with pytest.raises(ValueError):
        load_profile(root,tools)


def test_trace_redaction():
    value=redact({'nested':[{'api_key':'dont-leak','text':'Bearer abc123 and sk-abcXYZ'}]})
    assert 'dont-leak' not in json.dumps(value)
    assert 'abc123' not in json.dumps(value)
    assert 'sk-abcXYZ' not in json.dumps(value)


def test_compatible_provider(settings):
    def handler(req):
        body=json.loads(req.content)
        assert body['model']=='test-model'
        assert req.headers['Authorization']=='Bearer test-key'
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'action_type':'clarify','decision_summary':'需要补充信息','response':'请提供素材编号'})}}]})
    provider=CompatibleProvider(replace(settings,base_url='https://example.invalid/v1',model='test-model',api_key='test-key'),httpx.MockTransport(handler))
    assert asyncio.run(provider.decide({'message':'你好'})).action_type=='clarify'


def test_http_api_and_sse(settings):
    settings=replace(settings,api_token='local-test')
    with TestClient(create_app(settings)) as client:
        assert client.get('/health').status_code==200
        assert client.get('/').status_code==200
        req=request().model_dump()
        assert client.post('/api/run',json=req).status_code==401
        headers={'Authorization':'Bearer local-test'}
        result=client.post('/api/run',json=req,headers=headers)
        assert result.status_code==200
        assert result.json()['status']=='ANSWERED_WAITING'
        path='/api/traces/'+req['request_id']
        assert client.get(path,headers=headers).json()['events'][-1]['type']=='run_finished'
        assert 'run_finished' in client.get(path+'/stream',headers=headers).text
        assert client.get('/api/traces/missing',headers=headers).status_code==404
