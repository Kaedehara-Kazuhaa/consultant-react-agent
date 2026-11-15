import asyncio
from uuid import uuid4
import httpx
import pytest
from ad_agent.app import create_app,build_engine
from ad_agent.config import Settings
from ad_agent.conversation import resolve_turn
from ad_agent.models import Request
from ad_agent.tools import Context

@pytest.mark.parametrize('text',['人工客服','找真人','请转接人工','我要人工客服','转人工'])
def test_handoff_alias(text):
    assert resolve_turn(text,{},'2026-11-15')['handoff_requested']

@pytest.mark.parametrize('text',['不用转人工','不要人工客服','人工客服的规则是什么','转人工次数怎么判断'])
def test_not_handoff(text):
    assert not resolve_turn(text,{},'2026-11-15')['handoff_requested']

def test_local_test_lifecycle_and_roles(tmp_path):
    async def check():
        settings=Settings(trace_db=tmp_path/'test.sqlite',test_mode=True,local_browser_auth=True)
        engine=build_engine(settings)
        transport=httpx.ASGITransport(app=create_app(settings,engine),client=('127.0.0.1',1234))
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1') as c:
            async def open(role='trader',restricted=False):
                r=await c.post('/api/test/open',json={'role':role,'restricted_history':restricted})
                assert r.status_code==200,r.text
                return {'X-Test-Session':r.json()['token']}
            async def run(h,text,session='one'):
                r=await c.post('/api/run',headers=h,json={'message':text,'request_id':uuid4().hex,'session_id':session})
                assert r.status_code==200,r.text
                return r.json()
            trader=await open()
            result=await run(trader,'素材M0001在2026-03-24的ROI')
            assert result['status']=='ANSWERED_WAITING'
            vague=await run(trader,'你好')
            assert vague['stop_reason']=='PREVIOUS_QUERY_HYPOTHESIS'
            assert 'M0001' in vague['response']
            assert (await run(trader,'是的'))['status']=='ANSWERED_WAITING'
            results=[await run(trader,'人工客服') for _ in range(4)]
            assert results[-1]['status']=='HANDOFF_PENDING'
            assert results[-1]['response']=='正在为您转接。'
            assert (await c.get('/api/support/one',headers=trader)).json()['handoff_request_count']==4
            other=await open()
            assert (await c.get('/api/support/one',headers=other)).json()['handoff_request_count']==0
            guest=await open('guest')
            assert not (await c.get('/api/materials',headers=guest)).json()['items']
            assert not (await c.get('/api/influencers',headers=guest)).json()['items']
            assert (await run(guest,'ROI怎么算'))['status']=='ANSWERED_WAITING'
            denied=await run(guest,'素材M0001在2026-03-24的ROI')
            assert denied['stop_reason']=='PERMISSION_DENIED' and not denied['evidence_refs']
            restricted=await open(restricted=True)
            assert (await run(restricted,'人工客服'))['stop_reason']=='HISTORY_RESTRICTED'
            missing=await run(other,'素材999999999999在2026-03-24的ROI')
            assert '未找到' in missing['response'] and not missing['evidence_refs']
            assert missing['stop_reason']=='EMPTY_BUSINESS_RESULT'
            missing=await run(other,'达人999999999999在2026-03-24的消耗')
            assert '未找到' in missing['response'] and not missing['evidence_refs']
            assert missing['stop_reason']=='EMPTY_BUSINESS_RESULT'
            closed=await c.post('/api/test/close',json={'token':trader['X-Test-Session']})
            assert closed.status_code==200,closed.text
            assert (await c.get('/api/support/one',headers=trader)).status_code==409
            assert (await c.get('/api/support/one',headers=other)).status_code==200
        for enabled,host in [(False,'127.0.0.1'),(True,'203.0.113.1')]:
            conf=Settings(trace_db=tmp_path/('gate'+str(enabled)+'.sqlite'),test_mode=enabled,local_browser_auth=True)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(conf),client=(host,1234)),base_url='http://127.0.0.1') as c:
                assert (await c.post('/api/test/open',json={})).status_code==403
    asyncio.run(check())
