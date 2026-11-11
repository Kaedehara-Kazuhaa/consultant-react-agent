from datetime import datetime
from zoneinfo import ZoneInfo
import asyncio
import hmac
import json
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from .config import ROOT, Settings
from .engine import Engine
from .models import Request
from .tools import BusinessTools, Context

def build_engine(settings):
    return Engine(settings, BusinessTools(ROOT / "fixtures/business.json", settings.profile_dir / "knowledge", settings.business_db))

def create_app(settings=None, engine=None):
    settings = settings or Settings.from_env()
    engine = engine or build_engine(settings)
    app = FastAPI(title="广告业务 ReAct 调试服务", version="0.1.0")
    app.state.engine = engine

    def auth(authorization):
        if settings.api_token and not hmac.compare_digest(authorization or "", "Bearer "+settings.api_token):
            raise HTTPException(401, "Invalid API token")
        # This scaffold intentionally exposes one deployment-owned demo identity only.
        # Production authentication must replace this with verified business login claims.
        if settings.provider != "demo" and not settings.api_token:
            raise HTTPException(503, "Configure AGENT_API_TOKEN before using a real provider")
        return "demo-operator:B1"

    def context(session_id):
        return Context("demo-operator", "B1", ("A1",), datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(), session_id)

    @app.get("/")
    async def page():
        return FileResponse(ROOT / "web/index.html")

    @app.get("/health")
    async def health():
        return {"status": "ok", "provider": settings.provider, "profile": engine.profile.name, "identity_mode": "single_demo_identity", "trace_semantics": "decision_summary_not_hidden_reasoning"}

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
