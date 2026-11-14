import asyncio
import json
import os
from dataclasses import replace
import httpx
import pytest
from ad_agent.app import build_engine,create_app
from ad_agent.config import Settings
from ad_agent.local_config import save_secret,read_secret
from ad_agent.providers import CompatibleProvider


@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI secret storage')
def test_encrypted_model_config_env_override_and_repr(tmp_path,monkeypatch):
    path=tmp_path/'model.local.json'
    value='fake-test-credential-not-a-real-key'
    save_secret(tmp_path/'secrets/key.dpapi',value)
    save_secret(tmp_path/'secrets/token.dpapi','local-test-token')
    assert value.encode() not in (tmp_path/'secrets/key.dpapi').read_bytes()
    assert read_secret(path,'key.dpapi')==value
    path.write_text(json.dumps({'provider':'compatible','base_url':'https://api.deepseek.com','model':'deepseek-flash','key_file':'key.dpapi','token_file':'token.dpapi','thinking':'disabled','local_browser_auth':True}),encoding='utf-8')
    monkeypatch.setenv('AGENT_MODEL_CONFIG',str(path))
    for key in ['AGENT_PROVIDER','MODEL_API_KEY','AGENT_API_TOKEN','MODEL_NAME','MODEL_BASE_URL']:
        monkeypatch.delenv(key,raising=False)
    settings=Settings.from_env()
    assert settings.api_key==value and settings.provider=='compatible'
    assert settings.model=='deepseek-flash' and settings.local_browser_auth
    assert value not in repr(settings) and 'local-test-token' not in repr(settings)
    monkeypatch.setenv('MODEL_API_KEY','env-key-overrides-file')
    assert Settings.from_env().api_key=='env-key-overrides-file'
    monkeypatch.setenv('AGENT_PROVIDER','demo')
    monkeypatch.delenv('MODEL_API_KEY')
    assert Settings.from_env().api_key==''


def test_secret_path_cannot_escape_config_directory(tmp_path):
    with pytest.raises(ValueError,match='filename'):
        read_secret(tmp_path/'model.local.json','../outside.dpapi')


def test_local_cookie_auth_is_loopback_only_and_httponly(tmp_path):
    settings=Settings(trace_db=tmp_path/'runtime.sqlite')
    engine=build_engine(settings)
    secured=replace(settings,provider='compatible',api_key='not-real-key',api_token='local-secret',local_browser_auth=True,model='deepseek-flash')
    app=create_app(secured,engine=engine)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',8000)),base_url='http://127.0.0.1') as client:
            assert (await client.get('/api/materials')).status_code==401
            page=await client.get('/')
            cookie=page.headers['set-cookie']
            assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie
            assert 'local-secret' not in cookie and 'not-real-key' not in page.text
            assert (await client.get('/api/materials')).status_code==200
            saved=client.cookies.get('consultant_local')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('203.0.113.1',8000)),base_url='http://127.0.0.1') as remote:
            assert 'set-cookie' not in (await remote.get('/')).headers
            remote.cookies.set('consultant_local',saved)
            assert (await remote.get('/api/materials')).status_code==401
            assert (await remote.get('/api/materials',headers={'Authorization':'Bearer local-secret'})).status_code==200
    asyncio.run(check())


def test_deepseek_request_contract_thinking_and_tokens():
    def handler(request):
        data=json.loads(request.content)
        assert data['model']=='deepseek-flash'
        assert data['thinking']=={'type':'disabled'}
        assert data['max_tokens']==2048
        assert data['response_format']=={'type':'json_object'}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'action_type':'clarify','decision_summary':'需要条件','response':'请说明需求。'})}}]})
    settings=Settings(base_url='https://api.deepseek.com',model='deepseek-flash',api_key='fake',model_thinking='disabled')
    result=asyncio.run(CompatibleProvider(settings,httpx.MockTransport(handler)).decide({'message':'你好'}))
    assert result.action_type=='clarify'
