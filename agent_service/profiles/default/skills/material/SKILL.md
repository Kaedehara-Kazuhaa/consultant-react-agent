---
name: material
description: 授权范围内的素材状态与投放日报查询
triggers: [素材, M000, 状态, 在投, 日报]
tools: [resolve_material, get_material_status, get_material_daily_performance]
---
1. 明确素材编号，先 resolve_material 确认授权范围。
2. 状态问题调用 get_material_status；数据问题明确日期后调用 get_material_daily_performance。
3. 日期缺失则澄清；相对日期以服务端 business_day 计算。单次区间最多31天。
4. 展示实际返回的时间范围、数据完整性及来源。ROI 使用工具计算结果，不能将不同天的 ROI 平均。
5. EMPTY 不等于零；PARTIAL 明确缺失天数；NULL 不填零。回答引用工具返回的 evidence_refs。
