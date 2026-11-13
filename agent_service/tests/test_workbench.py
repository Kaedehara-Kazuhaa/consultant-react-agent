import asyncio
from dataclasses import replace
import json
from pathlib import Path
from contextlib import closing
import sqlite3
import pytest
from fastapi.testclient import TestClient
from ad_agent.app import create_app,build_engine
from ad_agent.config import Settings,ROOT
from ad_agent.conversation import resolve_turn
from ad_agent.codex_provider import CodexProvider
from ad_agent.prepare_data import prepare
from ad_agent.models import Request
from ad_agent.tools import Context
from uuid import uuid4


def test_numeric_id_not_date_or_platform_id():
    state=resolve_turn('查询素材339在2026-03-24的整体ROI',{},'2026-11-14')
    assert state['slots']['material_id']=='339'
    assert state['slots']['start_date']=='2026-03-24'
    assert 'material_id' not in resolve_turn('2026-03-24',{},'2026-11-14')['slots']


def test_catalog_scope_pagination_and_assets(tmp_path):
    settings=Settings(trace_db=tmp_path/'runtime.sqlite')
    with TestClient(create_app(settings)) as c:
        data=c.get('/api/materials').json()
        assert [r['id'] for r in data['items']]==['M0001']
        assert data['items'][0]['start_date']=='2026-03-24'
        assert not c.get('/api/materials?q=M0002').json()['items']
        assert c.get('/api/materials?page=0').status_code==422
        assert c.get('/health').json()['data_mode']=='synthetic'
        assert c.get('/').status_code==200


def test_codex_schema_adapter_no_credential_read(tmp_path,monkeypatch):
    import ad_agent.codex_provider as module
    monkeypatch.setattr(module.shutil,'which',lambda value:'codex.exe')
    async def fake(*args,**kwargs):
        assert '--ignore-user-config' in args and '--ephemeral' in args
        assert args[args.index('--sandbox')+1]=='read-only'
        schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
        assert schema['additionalProperties'] is False
        class Process:
            returncode=0
            async def communicate(self,prompt):
                assert b'question' in prompt
                Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(dict(action_type='clarify',decision_summary='Need details',tool_name=None,arguments_json='{}',response='Please clarify',evidence_refs=[])))
        return Process()
    monkeypatch.setattr(module.asyncio,'create_subprocess_exec',fake)
    result=asyncio.run(CodexProvider(Settings()).decide({'message':'question'}))
    assert result.action_type=='clarify'


def test_codex_missing_executable(monkeypatch):
    import ad_agent.codex_provider as module
    monkeypatch.setattr(module.shutil,'which',lambda value:None)
    with pytest.raises(ValueError,match='not installed'):CodexProvider(Settings())


def test_codex_rejects_remote_clients(tmp_path):
    import httpx
    settings=Settings(trace_db=tmp_path/'trace.sqlite')
    engine=build_engine(settings)
    app=create_app(replace(settings,provider='codex'),engine=engine)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('203.0.113.2',123)),base_url='http://test') as c:
            assert (await c.get('/api/materials')).status_code==403
    asyncio.run(check())


def test_prepare_snapshot_only_copies_whitelisted_business_columns(tmp_path):
    source=tmp_path/'full.sqlite'
    with closing(sqlite3.connect(source)) as db:
        db.executescript('''
        CREATE TABLE materials(id,advertiser_id,title,delivery_status,test_status,last_sync_at,report_last_sync_at,secret);
        INSERT INTO materials VALUES('339','acct','Title','active','ok','2026-03-24','2026-03-24','do-not-copy');
        CREATE TABLE material_daily_trend_cache(material_id,advertiser_id,date,total_cost,overall_gmv,overall_roi,synced_at);
        INSERT INTO material_daily_trend_cache VALUES('339','acct','2026-03-24','10','30','3','2026-03-24');
        CREATE TABLE brand_account_bindings(brand_id,advertiser_id);
        INSERT INTO brand_account_bindings VALUES('brand','acct');
        CREATE TABLE credentials(password);
        INSERT INTO credentials VALUES('never-copy');
        ''')
        db.commit()
    destination=tmp_path/'curated/query.sqlite'
    config=prepare(source,destination)
    local=json.loads(config.read_text(encoding='utf-8'))
    assert local['accounts']==['acct'] and local['brand_id']=='brand'
    with closing(sqlite3.connect(destination)) as db:
        assert 'credentials' not in [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        assert 'secret' not in [r[1] for r in db.execute('pragma table_info(materials)')]
    with pytest.raises(ValueError,match='exists'):prepare(source,destination)


def test_full_31_day_range_can_cite_all_daily_records(tmp_path):
    from datetime import date,timedelta
    from ad_agent.tools import BusinessTools
    from ad_agent.engine import Engine
    fixture=json.loads((ROOT/'fixtures/business.json').read_text(encoding='utf-8'))
    fixture['daily']=[dict(material_id='M0001',advertiser_id='A1',date=(date(2026,1,1)+timedelta(days=i)).isoformat(),total_cost='10',overall_gmv='30') for i in range(31)]
    path=tmp_path/'fixture.json';path.write_text(json.dumps(fixture),encoding='utf-8')
    engine=Engine(Settings(trace_db=tmp_path/'trace.sqlite'),BusinessTools(path,ROOT/'profiles/default/knowledge'))
    request=Request(message='素材M0001 2026-01-01至2026-01-31 ROI',request_id=uuid4().hex,session_id='s')
    result=asyncio.run(engine.run(request,Context('u','B1',('A1',),'2026-11-14','s')))
    assert result['status']=='ANSWERED_WAITING'
    assert len(result['evidence_refs'])==31
