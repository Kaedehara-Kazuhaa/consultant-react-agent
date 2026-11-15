"""Conservative, deterministic slot resolver; never derives authorization from text."""
from datetime import date, timedelta
import re
import time

MATERIAL = re.compile(r"(?<![A-Za-z0-9])M\d{4}(?![A-Za-z0-9])", re.I)
NUMERIC_ID = re.compile(r'(?:素材(?:编号|ID|id)?\s*[:：#]?\s*)(\d{1,20})(?![\d-])')
POSITIVE = {"明白了", "解决了", "已解决", "谢谢解决了", "可以了"}
NEGATIVE = ("不对", "没解决", "未解决", "没有解决", "还是不行", "有误", "为什么是0")


def resolve_turn(message, previous, business_day):
    """Only explicit date-only/pronoun/correction continuations inherit slots."""
    text = message.strip()
    normalized = re.sub(r"[\s，。！!？?、]", "", text)
    slots = dict(previous.get("slots", {}))
    old_intent = previous.get("intent")
    explicit_ids = list(dict.fromkeys(m.upper() for m in MATERIAL.findall(text)))
    explicit_ids += [m for m in NUMERIC_ID.findall(text) if m not in explicit_ids]
    shared = re.search(r'素材\s*((?:M\d{4}|\d{1,20})(?:[、,，和\s]+(?:M\d{4}|\d{1,20}))+)(?=在|的|于|$)',text,re.I)
    if shared:
        explicit_ids=list(dict.fromkeys(explicit_ids+re.findall(r'M\d{4}|\d+',shared[1],re.I)))
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    today = date.fromisoformat(business_day)
    if not dates:
        if any(w in text for w in ['近7天','最近7天','最近七天','近七天']):
            dates = [(today-timedelta(days=6)).isoformat(), today.isoformat()]
        else:
            for word, offset in [("前天",2),("昨天",1),("今天",0)]:
                if word in text:
                    dates = [(today-timedelta(days=offset)).isoformat()]
                    break
    negative = any(w in text for w in NEGATIVE)
    feedback = "positive" if normalized in POSITIVE else "negative" if negative else None
    is_rule = any(w in text for w in ["怎么算", "是什么", "定义", "口径", "怎么查", "如何查", "规则", "含义", "计算方法", "计算方式", "查询方法"])
    human = any(w in text for w in ['转人工','人工客服','人工服务','找人工','找真人','真人客服','转接人工','人工坐席'])
    negated = bool(re.search(r'(?:不要|不用|不必|无需|不想|别|不需要)(?:再|给我|帮我)?(?:转|找|接通|联系)?(?:人工|真人)',text))
    human_rule = human and any(w in text for w in ['规则','条件','次数','是什么','为什么','怎么判断','如何判断']) and not any(w in text for w in ['我要','帮我转','给我转','现在转'])
    if human_rule:is_rule=True
    handoff = human and not negated and not human_rule
    date_only = bool(dates) and not re.sub(r"\d{4}-\d{2}-\d{2}|最近七天|最近7天|近七天|近7天|前天|昨天|今天|改成|改为|是|呢|的|[\s，。？?！!]", "", text)
    continuation = old_intent in {"material","analytics"} and (date_only or negative or any(w in text for w in ["这个素材", "该素材", "这个达人", "它", "改成", "改为", "不是", "应该是", "日期是"]))
    paging=re.search(r'第\s*(\d+)\s*页',text)
    if old_intent=='analytics' and paging:continuation=True
    rank=any(w in text.lower() for w in ['排行','排名','最高','最低','top'])
    influencer='达人' in text
    analytics=(not is_rule) and (influencer or rank or len(explicit_ids)>1 or '批量' in text or '全部素材' in text or (continuation and old_intent=='analytics'))
    confirmed_suggestion = previous.get("suggestion") if normalized in {"是", "是的", "对", "确认", "就是这个"} and previous.get('suggestion_expires_at',0)>time.time() else None
    if handoff and not (explicit_ids or dates or influencer or rank or any(w in text.lower() for w in ['roi','gmv','消耗','状态'])):
        intent, slots = 'handoff', {}
    elif confirmed_suggestion:
        intent, slots = previous.get('suggestion_intent','material'), dict(confirmed_suggestion)
    elif is_rule:
        intent, slots = "knowledge", {}
    elif analytics:
        intent='analytics'
        if not continuation:slots={}
        slots['entity']='influencer' if influencer else slots.get('entity','material')
        slots['mode']='ranking' if rank else slots.get('mode','summary' if influencer else 'batch')
        if explicit_ids and not influencer:slots['ids']=explicit_ids
        if influencer:
            ids=re.findall(r'达人(?:编号|ID|id)?\s*[:：#]?\s*(\d+)',text)
            shared_ids=re.search(r'达人\s*(\d+(?:[、,，和]\s*\d+)+)',text)
            if shared_ids:ids=re.findall(r'\d+',shared_ids[1])
            if ids:slots['ids']=list(dict.fromkeys(ids))
            name=re.search(r'达人[“"「](.+?)[”"」]',text)
            if not name and not ids:
                name=re.search(r'达人(?:昵称)?\s*([^\d\s“"「][^“”"「」]{0,59}?)(?=在\d{4}-|的消耗|的整体|的ROI)',text,re.I)
            if name:slots['name']=name[1]
        if rank:
            slots['metric']='roi' if 'roi' in text.lower() else 'gmv' if 'gmv' in text.lower() else 'cost'
            slots['descending']=not any(w in text for w in ['最低','升序','从低到高'])
            top=re.search(r'(?:前|top\s*)(\d+)',text,re.I)
            if top:slots['limit']=int(top[1])
        if paging:slots['page']=int(paging[1])
        elif not continuation:slots['page']=1
    elif explicit_ids or any(w in text for w in ["素材", "在投", "日报"]) or continuation:
        intent = "material"
        if not continuation and not explicit_ids:
            slots = {}
        elif explicit_ids and not continuation:
            slots = {}
    elif feedback is not None:
        intent = "feedback"
    elif handoff:
        intent, slots = "handoff", {}
    elif any(w in text.lower() for w in ["roi", "gmv", "指标", "消耗"]):
        intent, slots = "knowledge", {}
    else:
        intent, slots = "clarify", {}
    if intent=='analytics' and any(w in text for w in ['全部','所有']):slots['scope_all']=True
    if intent == 'analytics' and dates:
        slots.update(start_date=dates[0],end_date=dates[-1])
    if intent == "material":
        if len(explicit_ids) == 1:
            slots["material_id"] = explicit_ids[0]
        if len(explicit_ids) > 1:
            slots.pop("material_id", None)
        if dates:
            slots.update(start_date=dates[0], end_date=dates[-1])
        if "状态" in text or "在投" in text:
            slots["query"] = "status"
        elif dates or any(w in text.lower() for w in ["表现", "消耗", "roi", "gmv"]):
            slots["query"] = "performance"
        slots.setdefault("query", "performance")
    return {"intent":intent, "slots":slots, "feedback":feedback, "handoff_requested":handoff,
            "multiple_materials":len(explicit_ids)>1, "ambiguous_dates":len(dates)>2, "inherited":continuation or bool(confirmed_suggestion)}
