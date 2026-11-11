---
name: knowledge
description: 业务规则及指标口径查询
triggers: [ROI, 指标, 消耗, 怎么算, 规则, 口径]
tools: [search_knowledge]
---
1. 使用 search_knowledge 检索问题涉及的规则。
2. 只根据命中的条目解释定义、适用条件和限制，引用返回的 evidence_refs。
3. 无命中时澄清问题，不编造业务规则；遇到具体素材查询交给同时加载的 material 技能。
