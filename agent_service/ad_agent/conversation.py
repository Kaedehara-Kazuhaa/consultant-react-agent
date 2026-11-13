"""Conservative, deterministic slot resolver; never derives authorization from text."""
from datetime import date, timedelta
import re
import time

MATERIAL = re.compile(r"(?<![A-Za-z0-9])M\d{4}(?![A-Za-z0-9])", re.I)
POSITIVE = {"明白了", "解决了", "已解决", "谢谢解决了", "可以了"}
NEGATIVE = ("不对", "没解决", "未解决", "还是不行", "有误", "为什么是0")


def resolve_turn(message, previous, business_day):
    """Only explicit date-only/pronoun/correction continuations inherit slots."""
    text = message.strip()
    normalized = re.sub(r"[\s，。！!？?、]", "", text)
    slots = dict(previous.get("slots", {}))
    old_intent = previous.get("intent")
    explicit_ids = list(dict.fromkeys(m.upper() for m in MATERIAL.findall(text)))
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    today = date.fromisoformat(business_day)
    if not dates:
        if "近7天" in text:
            dates = [(today-timedelta(days=7)).isoformat(), (today-timedelta(days=1)).isoformat()]
        else:
            for word, offset in [("前天",2),("昨天",1),("今天",0)]:
                if word in text:
                    dates = [(today-timedelta(days=offset)).isoformat()]
                    break
    negative = any(w in text for w in NEGATIVE)
    feedback = "positive" if normalized in POSITIVE else "negative" if negative else None
    is_rule = any(w in text for w in ["怎么算", "是什么", "定义", "口径", "怎么查", "如何查"])
    handoff = "转人工" in text
    date_only = bool(dates) and not re.sub(r"\d{4}-\d{2}-\d{2}|近7天|前天|昨天|今天|改成|改为|是|呢|的|[\s，。？?！!]", "", text)
    continuation = old_intent == "material" and (date_only or negative or any(w in text for w in ["这个素材", "该素材", "它", "改成", "改为", "不是", "应该是", "日期是"]))
    confirmed_suggestion = previous.get("suggestion") if normalized in {"是", "是的", "对", "确认", "就是这个"} and previous.get('suggestion_expires_at',0)>time.time() else None
    if confirmed_suggestion:
        intent, slots = "material", dict(confirmed_suggestion)
    elif is_rule:
        intent, slots = "knowledge", {}
    elif explicit_ids or any(w in text for w in ["素材", "在投", "日报"]) or continuation:
        intent = "material"
        if not continuation and not explicit_ids:
            slots = {}
        elif explicit_ids and not continuation:
            slots = {}
    elif feedback == "positive":
        intent = "feedback"
    elif handoff:
        intent, slots = "handoff", {}
    elif any(w in text.lower() for w in ["roi", "指标", "消耗"]):
        intent, slots = "knowledge", {}
    else:
        intent, slots = "clarify", {}
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
