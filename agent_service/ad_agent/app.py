from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
import hmac
import hashlib
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi import Request as HTTPRequest
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from .config import ROOT, Settings
from .engine import Engine
from .models import Request
from .tools import BusinessTools, Context
from .test_sessions import TestSessions
from pydantic import BaseModel
from typing import Literal

class TestOpen(BaseModel):
    role: Literal['guest','trader'] = 'trader'
    restricted_history: bool = False

class TestClose(BaseModel):
    token: str

def build_engine(settings):
    return Engine(settings, BusinessTools(ROOT / "fixtures/business.json", settings.profile_dir / "knowledge", settings.business_db, settings.customer_context_file))

def create_app(settings=None, engine=None):
    settings = settings or Settings.from_env()
    engine = engine or build_engine(settings)
    @asynccontextmanager
    async def lifespan(app):
        async def sweep():
            while True:
                await asyncio.to_thread(engine.support.reconcile)
                await asyncio.sleep(15)
        task=asyncio.create_task(sweep())
        try:
            yield
        finally:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
    app = FastAPI(title="广告业务 ReAct 调试服务", version="0.2.0",lifespan=lifespan)
    app.state.engine = engine
    test_sessions = TestSessions()
    frontend=ROOT/'frontend/dist'
    if (frontend/'assets').exists():
        app.mount('/assets',StaticFiles(directory=frontend/'assets'),name='assets')

    def local_browser(request):
        return bool(settings.local_browser_auth and request and request.client and request.client.host in {'127.0.0.1','::1'} and request.url.hostname in {'localhost','127.0.0.1','::1'})

    def cookie_value():
        return hmac.new(settings.api_token.encode(),b'consultant-local-browser-v1',hashlib.sha256).hexdigest()

    def auth(authorization,request=None):
        cookie_ok=bool(not authorization and settings.api_token and local_browser(request) and hmac.compare_digest(request.cookies.get('consultant_local',''),cookie_value()))
        if settings.api_token and not cookie_ok and not hmac.compare_digest(authorization or "", "Bearer "+settings.api_token):
            raise HTTPException(401, "Invalid API token")
        # This scaffold intentionally exposes one deployment-owned demo identity only.
        # Production authentication must replace this with verified business login claims.
        if settings.provider not in {"demo", "codex"} and not settings.api_token:
            raise HTTPException(503, "Configure AGENT_API_TOKEN before using a real provider")
        identity = test_identity(request)
        return (identity.customer_id if identity else settings.customer_id)+':'+settings.brand_id

    def test_identity(request):
        token = request.headers.get('X-Test-Session') if request else None
        if not token:return None
        if not settings.test_mode or not local_browser(request):
            raise HTTPException(403,'测试身份仅在本机测试模式可用')
        identity = test_sessions.get(token)
        if not identity:raise HTTPException(409,'测试轮次已结束，请刷新网页')
        return identity

    def context(session_id,request=None):
        identity = test_identity(request)
        role = identity.role if identity else 'trader'
        return Context(identity.customer_id if identity else settings.customer_id, settings.brand_id, settings.accounts if role=='trader' else (), datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(), session_id, role)

    @app.post('/api/test/open')
    async def test_open(body:TestOpen,request:HTTPRequest,authorization:str|None=Header(default=None)):
        if not settings.test_mode or not local_browser(request):raise HTTPException(403,'仅限本机测试模式')
        auth(authorization,request)
        token,identity=test_sessions.open(body.role,body.restricted_history)
        test_sessions.seed(identity,engine,settings.brand_id)
        return {'token':token,'role':identity.role}

    @app.post('/api/test/close')
    async def test_close(body:TestClose,request:HTTPRequest,authorization:str|None=Header(default=None)):
        if not settings.test_mode or not local_browser(request):raise HTTPException(403,'仅限本机测试模式')
        auth(authorization,request)
        test_sessions.close(body.token,engine,settings.brand_id)
        return {'closed':True}

    @app.middleware('http')
    async def local_codex_only(request,call_next):
        if settings.provider=='codex' and request.client and request.client.host not in {'127.0.0.1','::1','localhost','testclient'}:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':'Codex adapter is local-only'},status_code=403)
        return await call_next(request)

    @app.get("/")
    async def page(request:HTTPRequest):
        response=FileResponse(frontend/'index.html' if (frontend/'index.html').exists() else ROOT/'web/index.html')
        if settings.api_token and local_browser(request):
            response.set_cookie('consultant_local',cookie_value(),httponly=True,samesite='strict',path='/')
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok", "test_mode":settings.test_mode, "provider": settings.provider, "model":settings.model if settings.provider=='compatible' else '', "local_browser_auth":settings.local_browser_auth,"profile": engine.profile.name, "identity_mode": "local_snapshot_viewer" if settings.business_db else "single_demo_identity", "data_mode":"dump_snapshot" if settings.business_db else "synthetic", "trace_semantics": "decision_summary_not_hidden_reasoning"}

    @app.get('/api/materials')
    async def materials(request:HTTPRequest,q:str=Query(default='',max_length=200),page:int=Query(default=1,ge=1,le=1000000),authorization:str|None=Header(default=None)):
        auth(authorization,request)
        return await asyncio.to_thread(engine.catalog,context('catalog',request),q,page)

    @app.post("/api/run")
    async def run(request: Request,http_request:HTTPRequest, authorization: str | None = Header(default=None)):
        auth(authorization,http_request)
        try:
            return await engine.run(request, context(request.session_id,http_request))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/api/support/{session_id}')
    async def support_state(session_id:str,request:HTTPRequest,authorization:str|None=Header(default=None)):
        owner=auth(authorization,request)
        return await asyncio.to_thread(engine.support.state,owner,session_id)

    @app.get('/api/influencers')
    async def influencers(request:HTTPRequest,q:str=Query(default='',max_length=200),page:int=Query(default=1,ge=1),authorization:str|None=Header(default=None)):
        auth(authorization,request)
        from .analytics import source_rows
        try:
            rows,_=await asyncio.to_thread(source_rows,engine.business_tools,context('catalog',request),'influencer')
        except ValueError:
            raise HTTPException(409,'请先更新本地达人快照')
        rows=[r for r in rows if not q or q==r['id'] or q in (r['name'] or '')]
        return {'items':rows[(page-1)*20:page*20],'total':len(rows),'page':page}

    @app.get("/api/traces/{request_id}")
    async def traces(request_id: str,request:HTTPRequest, authorization: str | None = Header(default=None)):
        owner = auth(authorization,request)
        try:
            return {"request_id": request_id, "events": engine.store.events(request_id, owner)}
        except KeyError as exc:
            raise HTTPException(404, "Trace not found") from exc

    @app.get("/api/traces/{request_id}/stream")
    async def stream(request_id: str,request:HTTPRequest, authorization: str | None = Header(default=None)):
        owner = auth(authorization,request)
        try:
            engine.store.events(request_id, owner)
        except KeyError as exc:
            raise HTTPException(404, "Trace not found") from exc
        async def generate():
            seq = 0
            for _ in range(400):
                events = engine.store.events(request_id, owner)
                for event in events:
                    if event["seq"] > seq:
                        seq = event["seq"]
                        yield "data: "+json.dumps(event, ensure_ascii=False)+"\n\n"
                    if event["type"] == "run_finished":
                        return
                await asyncio.sleep(.25)
        return StreamingResponse(generate(), media_type="text/event-stream")

    return app
