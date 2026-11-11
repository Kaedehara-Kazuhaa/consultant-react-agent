---
name: clarify
description: 模糊进线需求引导
triggers: [你好, 您好, 帮忙]
tools: [get_customer_context]
---
提醒用户说明需求。调用 get_customer_context，若有近期浏览或异常，选择最相关的一个问题假设向用户确认，不同时抛出多个推测。无可用记录则只引导用户补充，不虚构行为。
