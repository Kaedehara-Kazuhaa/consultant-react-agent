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

## 输入条件
必须有明确业务素材主键；表现查询还需起止绝对日期。状态查询不强求日期。合成数据编号形如M0001，dump业务主键为数字（如“素材123”），中文紧贴编号也应识别；标题重名、平台ID转换和候选分页尚待工具接入，不能编造候选。

## Observation 分支
resolve_material EMPTY：统一提示当前范围未找到，要求核对编号，不揭示其他品牌是否存在。日报 EMPTY：提示无记录及可用日期，不默默切换日期。PARTIAL：区分缺失天数与NULL，注明仅汇总已有记录。ERROR：不基于旧结果生成新结论。

## 多轮与复核
“2026-03-25”补充日期时保留此前明确素材；“前天呢”只替换日期；“这个数不对”重新核对并查询，不能直接重发上一条答案。新素材编号每次重新授权。多对象要求选一个，3个以上日期要求明确单个区间。

## 验收示例
“查素材M0001消耗”→询问日期→“2026-03-24”→重新定位并查询；“素材M0002状态”在当前A1权限下返回统一未找到；零消耗时ROI不适用，不推断故障原因。
