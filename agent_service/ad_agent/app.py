from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
import hmac
import json
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from .config import ROOT, Settings
from .engine import Engine
from .models import Request
from .tools import BusinessTools, Context

def build_engine(settings):
    return Engine(settings, BusinessTools(ROOT / "fixtures/business.json", settings.profile_dir / "knowledge", settings.business_db, settings.customer_context_file))

def create_app(settings=None, engine=None):
    settings = settings or Settings.from_env()
    engine = engine or build_engine(settings)
    app = FastAPI(title="广告业务 ReAct 调试服务", version="0.1.0")
    app.state.engine = engine
    frontend=ROOT/'frontend/dist'
    if (frontend/'assets').exists():
        app.mount('/assets',StaticFiles(directory=frontend/'assets'),name='assets')

    def auth(authorization):
        if settings.api_token and not hmac.compare_digest(authorization or "", "Bearer "+settings.api_token):
            raise HTTPException(401, "Invalid API token")
        # This scaffold intentionally exposes one deployment-owned demo identity only.
        # Production authentication must replace this with verified business login claims.
        if settings.provider not in {"demo", "codex"} and not settings.api_token:
            raise HTTPException(503, "Configure AGENT_API_TOKEN before using a real provider")
        return settings.customer_id+':'+settings.brand_id

    def context(session_id):
        return Context(settings.customer_id, settings.brand_id, settings.accounts, datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(), session_id)

    @app.middleware('http')
    async def local_codex_only(request,call_next):
        if settings.provider=='codex' and request.client and request.client.host not in {'127.0.0.1','::1','localhost','testclient'}:
            from fastapi.responses import JSONResponse
            return JSONResponse({'detail':'Codex adapter is local-only'},status_code=403)
        return await call_next(request)

    @app.get("/")
    async def page():
        return FileResponse(frontend/'index.html' if (frontend/'index.html').exists() else ROOT/'web/index.html')

    @app.get("/health")
    async def health():
        return {"status": "ok", "provider": settings.provider, "profile": engine.profile.name, "identity_mode": "local_snapshot_viewer" if settings.business_db else "single_demo_identity", "data_mode":"dump_snapshot" if settings.business_db else "synthetic", "trace_semantics": "decision_summary_not_hidden_reasoning"}

    @app.get('/api/materials')
    async def materials(q:str=Query(default='',max_length=200),page:int=Query(default=1,ge=1,le=1000000),authorization:str|None=Header(default=None)):
        auth(authorization)
        return await asyncio.to_thread(engine.catalog,context('catalog'),q,page)

    @app.post("/api/run")
    async def run(request: Request, authorization: str | None = Header(default=None)):
        auth(authorization)
        try:
            return await engine.run(request, context(request.session_id))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/traces/{request_id}")
    async def traces(request_id: str, authorization: str | None = Header(default=None)):
        owner = auth(authorization)
        try:
            return {"request_id": request_id, "events": engine.store.events(request_id, owner)}
        except KeyError as exc:
            raise HTTPException(404, "Trace not found") from exc

    @app.get("/api/traces/{request_id}/stream")
    async def stream(request_id: str, authorization: str | None = Header(default=None)):
        owner = auth(authorization)
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
