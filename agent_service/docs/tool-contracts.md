# 工具及数据合同

所有工具采用Pydantic严格参数校验，禁止额外字段。customer_id、brand_id、accounts和business_day仅由服务端Context注入。用户与模型不能把身份字段放入工具参数。

| 工具 | 参数 | 成功依据 | 空/错误处理 |
|---|---|---|---|
| search_knowledge | question | knowledge条目ID | EMPTY澄清；检索命中不代表语义验证通过 |
| resolve_material | material_id | material:ID | 不存在和无权限统一EMPTY |
| get_material_status | material_id | material:ID | 状态与报表更新时间分开；未知枚举不推断 |
| get_material_daily_performance | material_id,start_date,end_date | daily:ID:date | 日期非法或>31天ERROR；无记录EMPTY；缺失/NULL/PARTIAL分别表达 |
| get_customer_context | 无 | context:ID | 只选一个授权、24小时内的有效候选；无数据EMPTY |
| handoff_record | summary | 当前不返回成功依据 | HANDOFF_POLICY_NOT_CONNECTED，不产生记录 |

Observation统一为 `status,data,evidence_refs,error_code,retryable`。只有明确可重试错误才进行一次工具重试；不可无限重试。禁止向用户返回原始异常、SQL、堆栈或上游响应全文。

## 知识条目

```json
{
  "id":"knowledge:example:v1",
  "title":"指标名称",
  "content":"定义、边界和处理方式",
  "keywords":["指标名称"],
  "scope":"品牌方投手客服",
  "source":"PRD章节或经核验的正式来源",
  "version":"1",
  "rule_type":"字段事实/推导规则/项目约定",
  "assumption":true
}
```

启动时校验必需元数据、重复ID、文件大小与路径，固定知识快照；Trace含其哈希。当前24条均保守标注为项目约定/开发假设，不代表已对外部平台规则完成核验。关键词命中按匹配长度排序取前三，后续可替换检索器，但引用ID与来源合同不变。

## 可选后台上下文

`AGENT_CUSTOMER_CONTEXT_FILE` 指向受信JSON数组，单行格式：

```json
{
  "customer_id":"demo-operator",
  "brand_id":"B1",
  "advertiser_id":"A1",
  "kind":"view",
  "material_id":"M0001",
  "start_date":"2026-03-24",
  "end_date":"2026-03-24",
  "occurred_at":"2026-11-13T09:00:00+08:00"
}
```

kind为view或alert；alert还必须为`status: open`。时间必须带时区，按真实发生时间计算24小时，未来与过期记录忽略。示例日期不会在导入时自动变新。先筛选同用户、品牌和授权账户，再优先最新开放异常，其次最新浏览，只返回一项。确认前不查询表现；确认后重新授权。否认后不连续猜测。默认不配置该文件，也不生成虚构后台行为。

该文件是部署方导出接口，不是供普通用户上传的任意JSON。生产接入应限制记录量、字段格式与读取权限；当前全文件读取适用于小规模本地验证。记录的异常类型不等于模型自行诊断的异常原因。

## SQLite快照适配

`AGENT_BUSINESS_DB` 只读打开已有SQLite，不接受PostgreSQL dump路径。需要：

- materials：id,advertiser_id,title,delivery_status,test_status,last_sync_at,report_last_sync_at。
- material_daily_trend_cache：material_id,advertiser_id,date,total_cost,overall_gmv,synced_at。

素材与日报按服务端账户过滤，重复日报键拒绝计算。金额用Decimal，汇总后舍入展示；没有记录不按零填充，必需字段NULL时ROI不计算。当前历史适配将2026-03-27视为可能不完整，生产需要用数据源覆盖元信息替代该历史规则。暂不支持净ROI与标题查找，不将其他字段改名充当这些能力。
