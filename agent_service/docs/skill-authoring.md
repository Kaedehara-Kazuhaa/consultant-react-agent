# Agent与Skill维护规范

目标：修改业务说明无需改动循环内核；新增可执行能力必须同时实现工具、权限和测试。

## 分工和模板

AGENTS.md写稳定的角色、目标优先级、约束、输出合同及技能清单。Skill只写一个业务能力的处理方法，避免每份重复所有系统规则。知识条目存定义、公式和适用条件；超时、身份和计数放在代码/配置，不让模型自行记账。

```markdown
---
name: example
description: 一句话业务能力
triggers: [明确触发词]
tools: [已注册且Agent允许的工具]
---
# 业务能力名称
## 适用与排除
何时进入，何时应交给别的技能。
## 输入条件
必须具备的对象、日期、范围；缺少时问什么。
## 执行步骤
每步选什么工具，依赖上一步哪些Observation。
## Observation分支
OK/PARTIAL/EMPTY/ERROR分别如何处理。
## 输出合同
应包含的结论、口径、时效、依据与下一步。
## 验收示例
正常、缺条件、无权限、异常、多轮各一条。
## 接入状态
明确哪些工具已可执行，哪些依赖仍待接入。
```

目录名与name必须一致。技能工具必须是总Agent白名单的子集；不能引用相对路径逃出profile，不能编写任意脚本供运行时执行。当前知识引用以注册ID为准，Skill中的引用文本不自动变成可引用的证据。

## 路由和修改验证

优先按conversation的已识别意图加载主要技能；关键词用于自定义技能和复核、人工等辅助技能。定义咨询不会仅因出现“达人”而加载查数技能；日期补充沿用已确认的单素材或统计意图。description及正文均传入模型，Trace保留名称、描述及版本。新业务Skill若只有文档没有路由，可能永不被选中，必须补测试验证 `documents_loaded` 中实际包含该Skill，并验证正文进入provider状态。

修改流程：同步Git→修改Agent/Skill/知识→运行pytest及evals→检查Trace中的加载版本和工具动作→更新验收记录→提交并推送。文档和知识启动时加载，修改后重启服务；浏览器刷新不足以重载后端。

## 当前七项技能

| Skill | 关键合同 | 主要回归 |
|---|---|---|
| clarify | 一项授权候选，确认才查询，无依据不猜 | confirmation/denial/stale |
| knowledge | 规则优先；来源、范围、假设可见 | usage/ROI/netROI |
| analytics | 批量、排行、达人日报；分页不截断汇总 | batch/rank/influencer/future-range |
| material | 明确对象和日期，每次授权 | completion/zero/null/missing |
| diagnose | 只改明确槽位，重新取数 | date-correction/reopen |
| feedback | 有效回答后的明确肯定 | no-false-resolution |
| handoff | 先解业务，不虚构交接 | mixed/pure-handoff |

完成文档后必须核对代码和测试，不能用“应当”替代已实现能力。真实模型的选择质量需要单独测，Demo通过只能证明已编排链路可运行。
