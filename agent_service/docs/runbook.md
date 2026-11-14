# 本地运行、排障与回归

日常使用根目录启动项目.cmd，不需要每次安装。新前端、数据切换和模型方式见[快速使用](quickstart.md)。以下安装命令只用于首次准备或依赖变化。

仓库：consultant-react-agent。执行目录为`D:\cst_proj\agent_service`。

```powershell
..\.venv\Scripts\python.exe -m pip install -e ".[test]"
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ad_agent.evals
..\.venv\Scripts\python.exe -m uvicorn ad_agent.app:create_app --factory --host 127.0.0.1 --port 8765
```

访问 http://127.0.0.1:8765 。页面以浏览器演示线程ID续接槽位；建议依次输入“查素材M0001消耗”→“2026-03-24”→“前天呢”→“明白了”。历史示例数据不是当天实时数据，先用明确示例日期验证，不能因为“昨天”无记录就认为链路失败。

CLI同一线程：

```powershell
..\.venv\Scripts\python.exe -m ad_agent "查素材M0001消耗" --session my-check
..\.venv\Scripts\python.exe -m ad_agent "2026-03-24" --session my-check --export var/my-check.jsonl
```

相对日期按真实消息当天换算。evals使用固定测试基准日，使回归不随日历变化。全量回归结果在var，合成场景可分享，真实客户Trace不要提交。

## 检查链路

1. /health确认provider和身份模式；读取状态不代表真实业务登录已接入。
2. documents_loaded核对技能和知识版本；context_resolved检查槽位是否继承正确。
3. action检查实际工具及参数；observation检查状态、依据、覆盖与耗时。
4. evaluation及run_finished核对终止原因，不只看是否HTTP200。
5. 模型模式还要人工抽样核对：结论是否由引用支持、是否答非所问、是否多余转人工。

| 症状 | 处理 |
|---|---|
| 端口拒绝连接 | 确认uvicorn仍运行、端口与地址一致 |
| 401 | 页面或HTTP发送配置的AGENT_API_TOKEN |
| 503模型入口拒绝 | compatible模式必须配置API Token，另需模型地址/名称/密钥 |
| 409请求ID冲突 | 同输入重放使用同ID；新问题使用新ID |
| 409线程正在处理 | 等待前一轮完成；不要用新线程绕过业务政策 |
| RUNNING进程中断 | 检查Trace，租约到期后用新请求ID；不能直接宣称操作已完成 |
| 模型重试后失败 | 检查端点JSON object支持、模型名及网络；不把原始密钥或响应写日志 |
| 日期查不到 | 检查历史覆盖；不静默回退到有数据的日期 |
| 文档修改无效 | 重启后端，核对文档哈希 |

## Git工作流

开始前检查`git status`，干净时`git pull --ff-only`；有用户未提交修改先保留，不能强制覆盖。测试通过后只暂存本任务文件，`git diff --cached --stat`确认，再commit和push。网络中断分别报告本地提交、远程推送的真实状态，不使用force push。

正式对外服务前仍需完成真实鉴权、跨会话语义事项关联、真实人工系统与模型质量评测，见architecture及decisions.md。


support_issues、support_events、handoff_sessions存储在与Trace相同的本地运行SQLite中；不要删除运行库以排障，否则历史统计与槽位会丢失。新对话只改变会话，不清空用户历史。进程停止时不扫描静默，重启会补算。
