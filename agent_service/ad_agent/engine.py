import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from pydantic import ValidationError
import httpx
from .config import Settings
from .documents import load_profile
from .models import Decision, Observation, Request
from .providers import CompatibleProvider, DemoProvider
from .trace import TraceStore
from .sessions import Sessions
from .conversation import resolve_turn
from .codex_provider import CodexProvider
from .support import SupportLedger

class Engine:
    def __init__(self, settings: Settings, business_tools, provider=None):
        self.settings = settings
        self.tools = business_tools.registry
        self.business_tools = business_tools
        self.catalog = business_tools.catalog
        self.knowledge_version = getattr(business_tools,'knowledge_version',None)
        self.profile = load_profile(settings.profile_dir, set(self.tools))
        self.store = TraceStore(settings.trace_db)
        self.sessions = Sessions(settings.trace_db, lease=settings.turn_timeout+30)
        self.support = SupportLedger(settings.trace_db, settings)
        self.provider = provider or (DemoProvider() if settings.provider == "demo" else CompatibleProvider(settings) if settings.provider == "compatible" else CodexProvider(settings) if settings.provider=='codex' else self.invalid_provider())

    @staticmethod
    def invalid_provider():
        raise ValueError("AGENT_PROVIDER must be demo, compatible or codex")

    async def run(self, request: Request, context):
        owner = context.customer_id+":"+context.brand_id
        self.support.reconcile()
        with self.sessions.turn(owner, request.session_id, request.request_id) as previous:
            return await self._run(request, context, previous)

    async def _run(self, request, context, previous):
        if request.session_id != context.session_id:
            raise ValueError("Session context mismatch")
        owner = context.customer_id+":"+context.brand_id
        fingerprint = hashlib.sha256(json.dumps({"request": request.model_dump(), "context": asdict(context)}, sort_keys=True).encode()).hexdigest()
        cached = self.store.claim(request.request_id, owner, fingerprint)
        if cached is not None:
            return cached
        rid = request.request_id
        started = time.monotonic()
        turn = resolve_turn(request.message, previous, context.business_day)
        issue=self.support.issue(owner,request.session_id,turn,previous)
        self.support.handoff_request(owner,request.session_id,rid,issue['id'],record=False)
        # Resolution by silence updates the issue, not the saved chat slots.
        if previous.get('issue_id')==issue['id']:
            previous=dict(previous,last_status=issue['status'])
        skills = self.profile.select(request.message, turn["intent"])
        permitted = set(self.profile.allowed_tools) & {t for s in skills for t in s.tools}
        state = {"message": request.message, "turn": turn, "previous": previous, "context": asdict(context), "agent_document": self.profile.instructions, "skills": [{"name": s.name, "instructions": s.body} for s in skills], "tools": [self.tools[n].schema() for n in sorted(permitted)], "observations": [], "decision_error": None}
        self.store.emit(rid, "run_started", {"profile": self.profile.name, "profile_version": self.profile.digest, "mode": self.settings.provider, "context": asdict(context), "message": request.message})
        self.store.emit(rid, "documents_loaded", {"skills": [{"name": s.name, "version": s.digest} for s in skills], "knowledge_version":self.knowledge_version, "allowed_tools": sorted(permitted)})
        self.store.emit(rid, "context_resolved", turn)
        seen, evidence = set(), set()
        actions = 0
        handoff_policy=None

        def finish(status, response, refs=(), reason="finished"):
            if turn.get('feedback')=='negative' and status=='ANSWERED_WAITING':
                status='UNRESOLVED'
            signature=hashlib.sha256(json.dumps([o for o in state['observations'] if o['observation']['status'] in {'OK','PARTIAL'}],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            effective=status in {'ANSWERED_WAITING','UNRESOLVED'} and bool(refs)
            self.support.update(issue['id'],rid,status,turn.get('feedback'),effective=effective,signature=signature)
            result = {"request_id": rid, "session_id": request.session_id, "issue_id":issue['id'], "status": status, "response": response, "evidence_refs": list(refs), "actions": actions, "mode": self.settings.provider, "profile_version": self.profile.digest, "stop_reason": reason, "elapsed_ms": round((time.monotonic()-started)*1000)}
            self.store.emit(rid, "run_finished", result)
            self.store.finish(rid, result)
            suggestion = next((o['observation']['data'].get('suggestion') for o in state['observations'] if o['tool']=='get_customer_context'),None)
            suggestion_expires_at = next((o['observation']['data'].get('suggestion_expires_at',0) for o in state['observations'] if o['tool']=='get_customer_context'),0)
            is_feedback = turn['intent'] in {'feedback','handoff'}
            self.sessions.save(owner, request.session_id, {"intent":previous.get('intent') if is_feedback else turn["intent"], "slots":previous.get('slots',{}) if is_feedback else turn["slots"], "issue_id":issue['id'], "suggestion":suggestion, "suggestion_expires_at":suggestion_expires_at, "hypothesis_attempted":bool(suggestion or (turn['intent']=='clarify' and previous.get('hypothesis_attempted'))), "last_status":status, "last_request_id":rid})
            return result

        try:
            if turn.get('feedback')=='negative':
                self.support.update(issue['id'],rid,'UNRESOLVED',feedback='negative')
                self.store.emit(rid,'issue_reopened',{'issue_id':issue['id'],'status':'UNRESOLVED'})
            if turn.get('handoff_requested'):
                handoff_policy=self.support.handoff_request(owner,request.session_id,rid,issue['id'])
                self.store.emit(rid,'handoff_policy',handoff_policy)
                # Mixed requests first answer the explicit business question.
                explicit_business=any(w in request.message.lower() for w in ['素材','达人','roi','gmv','消耗','状态','怎么算','口径','日报'])
                if not explicit_business:
                    if not handoff_policy['eligible']:
                        return finish('UNRESOLVED','您近期多次提出转人工请求，本次继续由智能体协助。请说明尚未解决的问题或需要核对的数据。',reason='HISTORY_RESTRICTED')
                    if handoff_policy['allow']:
                        return finish('HANDOFF_PENDING','正在为您转接。',reason='HANDOFF_NOTICE_ONLY')
                    prompts=['我先协助您处理，请说明遇到的具体问题。','请补充需要查询的素材或达人编号，以及日期或需要解释的规则。','请指出前面答复中仍未解决的部分，我会继续核对。']
                    index=min(handoff_policy['attempts'],len(prompts)-1)
                    self.support.update(issue['id'],rid,'COLLECTING',effective=True,signature='handoff-guidance-'+str(index))
                    return finish('COLLECTING',prompts[index],reason='HANDOFF_INTERCEPTED')
            if turn["intent"] == "feedback":
                if turn.get('feedback')=='negative':
                    return finish('UNRESOLVED','已将该问题更新为未解决，请说明需要继续核对的部分。',reason='NEGATIVE_FEEDBACK')
                confirmed = self.support.has_answer(issue['id']) and issue['status']!='HANDOFF_PENDING'
                return finish("CONFIRMED_RESOLVED" if confirmed else "COLLECTING", "已记录：您确认上一问题已解决。" if confirmed else "请先说明您需要处理的具体问题。", reason="EXPLICIT_FEEDBACK" if confirmed else "NO_PENDING_ANSWER")
            async with asyncio.timeout(self.settings.turn_timeout):
                while True:
                    decision = None
                    for attempt in range(self.settings.retries+1):
                        call_start = time.monotonic()
                        self.store.emit(rid, "plan_started", {"iteration": actions+1, "attempt": attempt+1, "observation_count": len(state["observations"])})
                        try:
                            async with asyncio.timeout(self.settings.call_timeout):
                                raw = await self.provider.decide(state)
                                decision = raw if isinstance(raw, Decision) else Decision.model_validate(raw)
                            self.store.emit(rid, "decision", {**decision.model_dump(), "elapsed_ms": round((time.monotonic()-call_start)*1000)})
                            break
                        except (ValidationError, ValueError, TimeoutError, httpx.HTTPError, KeyError, IndexError) as exc:
                            # Never trace raw provider bodies, exception messages or authorization headers.
                            state["decision_error"] = "Invalid schema or provider unavailable. Return a valid Decision object."
                            self.store.emit(rid, "retry", {"phase": "model", "attempt": attempt+1, "error_type": type(exc).__name__, "will_retry": attempt < self.settings.retries})
                    if decision is None:
                        return finish("UNRESOLVED", "模型调用未完成，请稍后重试。", reason="MODEL_UNAVAILABLE")
                    if decision.action_type != "tool":
                        if not set(decision.evidence_refs) <= evidence or (decision.action_type == "answer" and not decision.evidence_refs):
                            self.store.emit(rid, "guard_rejected", {"reason": "UNSUPPORTED_EVIDENCE"})
                            return finish("UNRESOLVED", "当前依据不足，未生成业务结论。", reason="UNSUPPORTED_EVIDENCE")
                        status = {"answer": "ANSWERED_WAITING", "clarify": "COLLECTING", "unresolved": "UNRESOLVED"}[decision.action_type]
                        self.store.emit(rid, "evaluation", {"evidence_valid": True, "next": "finish", "status": status})
                        return finish(status, decision.response, decision.evidence_refs)
                    if actions >= self.settings.max_actions:
                        return finish("UNRESOLVED", "已达到本轮处理预算，请补充条件后继续。", reason="ACTION_BUDGET")
                    if decision.tool_name not in permitted:
                        self.store.emit(rid, "guard_rejected", {"reason": "TOOL_NOT_ALLOWED", "tool": decision.tool_name})
                        return finish("UNRESOLVED", "该处理超出当前智能体允许的能力范围。", reason="TOOL_NOT_ALLOWED")
                    tool = self.tools[decision.tool_name]
                    try:
                        args = tool.arguments.model_validate(decision.arguments)
                    except ValidationError:
                        self.store.emit(rid, "guard_rejected", {"reason": "INVALID_ARGUMENTS", "tool": tool.name})
                        return finish("UNRESOLVED", "查询参数未通过校验，请补充或核对条件。", reason="INVALID_ARGUMENTS")
                    signature = tool.name+json.dumps(args.model_dump(), sort_keys=True)
                    if signature in seen:
                        self.store.emit(rid, "guard_rejected", {"reason": "REPEATED_ACTION", "tool": tool.name})
                        return finish("UNRESOLVED", "重复处理没有产生新依据，本轮已停止。", reason="REPEATED_ACTION")
                    seen.add(signature)
                    actions += 1
                    self.store.emit(rid, "action", {"step": actions, "tool": tool.name, "arguments": args.model_dump()})
                    tool_start = time.monotonic()
                    obs = Observation(status="ERROR", error_code="TOOL_FAILED")
                    for attempt in range(self.settings.retries+1):
                        try:
                            async with asyncio.timeout(self.settings.call_timeout):
                                if tool.name=='get_customer_context' and previous.get('hypothesis_attempted') and turn['intent']=='clarify':
                                    obs = Observation(status='EMPTY',data={'message':'此前已提出候选问题，请用户主动说明具体需求'})
                                else:
                                    obs = await tool.handler(args, context)
                        except TimeoutError:
                            obs = Observation(status="ERROR", error_code="TOOL_TIMEOUT", retryable=True)
                        except Exception as exc:
                            obs = Observation(status="ERROR", error_code="TOOL_FAILED", data={"error_type": type(exc).__name__})
                        if not obs.retryable or attempt == self.settings.retries:
                            break
                        self.store.emit(rid, "retry", {"phase": "tool", "attempt": attempt+1, "error_code": obs.error_code, "will_retry": True})
                    evidence.update(obs.evidence_refs)
                    self.store.emit(rid, "observation", {"step": actions, "tool": tool.name, **obs.model_dump(), "elapsed_ms": round((time.monotonic()-tool_start)*1000)})
                    state["observations"].append({"tool": tool.name, "arguments": args.model_dump(), "observation": obs.model_dump()})
                    self.store.emit(rid, "evaluation", {"step": actions, "next": "decide", "observation_status": obs.status, "actions_remaining": self.settings.max_actions-actions})
        except TimeoutError:
            return finish("UNRESOLVED", "本轮处理超时，未生成未经核实的结果。", reason="TURN_TIMEOUT")
        except asyncio.CancelledError:
            finish("UNRESOLVED", "处理已取消。", reason="CANCELLED")
            raise
        except Exception as exc:
            self.store.emit(rid, "error", {"error_type": type(exc).__name__})
            return finish("UNRESOLVED", "本次处理发生错误，未生成业务结论。", reason="INTERNAL_ERROR")
