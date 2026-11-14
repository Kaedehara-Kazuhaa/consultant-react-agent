# 模型调用与无密钥选项

## 当前三种模式

| 模式 | 调用方式 | 是否真实模型 | 所需条件 |
|---|---|---|---|
| demo | 本地固定规则按意图调用业务工具 | 否 | 无网络、无密钥；支持dump或合成数据 |
| compatible | httpx向MODEL_BASE_URL/chat/completions发请求，返回结构化Decision | 是 | 服务地址、模型名、服务商密钥及本地API Token |
| codex | 调用官方codex exec，让你自己已登录的账号产生Decision | 是，但本机尚未完成真实登录调用验证 | 官方CLI、有效账号授权与可用额度；仅限本地原型 |

**当前聊天会话里的模型不是一个可以交给项目直接调用的免费API。** 不能提取桌面应用令牌、复用会话内部地址或宣称订阅自动等同API密钥。ChatGPT订阅登录与API计费是不同路径。

官方文档：[Codex身份验证](https://developers.openai.com/codex/auth/)、[非交互运行](https://developers.openai.com/codex/noninteractive/)、[CLI参数](https://developers.openai.com/codex/cli/reference/)。官方支持本地CLI使用ChatGPT账号登录；程序化自动化仍推荐API密钥，不能将账号CLI做成公开或不可信环境的共享服务。

## 本地原型使用自己的Codex账号

在项目目录终端执行一次：

也可双击根目录“启动账号模型.cmd”：未登录时启动官方登录流程，成功后启动项目；已登录则直接启动。先关闭原有项目服务窗口，以免端口占用。

```powershell
codex login
codex login status
.\Start.cmd --provider codex
```

登录由官方CLI处理，项目不读取auth.json，不抽取或记录令牌。CLI未登录时启动器直接提示，不假装已接通模型。实际使用取决于账号可用额度、模型权限和网络；不能保证与本对话使用同一模型。

每次模型决策在临时目录执行，使用read-only sandbox、禁用shell工具、不加载用户项目配置、使用结构化输出；项目工具由自身Engine校验并执行。只收集最终Decision，不采集CLI原始推理输出。适配器只允许本机访问，仍是本地原型，不能等同经过生产验证的模型网关。

本机验证记录：已检测到codex可执行文件；`codex login status`返回Not logged in。结构化适配器通过模拟进程测试；尚未进行真实模型完整ReAct验收。Codex模式仍未登录；本机现在另已接通DeepSeek兼容API，见下文。

## 不需要云模型密钥的其他方式

可以另行安装Ollama或LM Studio运行本地开源模型，使用其兼容端点；硬件、模型下载量与效果需评估。本轮没有自动下载大模型，也没有把固定规则模式包装成真实AI。后续选定本地模型时再配置适配和参数。


## DeepSeek本地加密配置

本机已使用DeepSeek官方API完成真实测试，日期2026-11-15。默认模型为`deepseek-flash`，另可选`deepseek-v4-pro`；通过当前账号的GET /models实际核验。API Key是身份凭证，创建时的名称只是备注；每次请求的model字段决定调用哪个模型。

官方文档：https://api-docs.deepseek.com/quick_start 。当前文档将deepseek-flash映射到DeepSeek-V4.1-Flash-1017；这是可随平台更新的模型别名，不能把别名当成永久冻结版本。本次没有选更昂贵的Pro进行额外调用。此前示例中的deepseek-chat属于旧名称，不再作为当前默认。

- 本地配置：`agent_service/var/model.local.json`，仅保存模型名、官方地址、参数及加密文件名。
- 密钥：`agent_service/var/secrets/deepseek.key.dpapi`，Windows DPAPI当前用户加密。
- 本地访问令牌：`agent_service/var/secrets/local-api.token.dpapi`，独立随机生成并加密，不是DeepSeek密钥。
- 整个var目录被Git忽略。配置和凭证不会随仓库推送；其他人克隆后需要配置自己的密钥。DPAPI文件换电脑或Windows用户无法直接复用。
- 启动器自动读取本地模型配置；环境变量优先。`Start.cmd --provider demo`可临时回到规则模式；未配置本地模型的新环境仍默认demo。
- 若显式设置`AGENT_MODEL_CONFIG`，从该路径读配置；设置为空字符串可禁用本地模型文件。此配置不等于自动加载.env。
- 当前请求使用JSON输出、`thinking: disabled`和2048个最大输出token，服务端只保存Decision摘要及工具轨迹，不保存模型隐藏推理内容。

本机网页首次打开时仅对loopback客户端和localhost/127.0.0.1主机签发HttpOnly、SameSite=Strict会话cookie，方便直接查看。密钥和本地访问令牌均不会写入前端构建或发送到网页；远端不能凭该cookie认证。正式对外服务仍需要独立登录系统，不能直接公开此本地原型。

真实测试覆盖规则、单素材、日期纠正、批量、达人五组，并另完成浏览器真实查数；实际数字与工具结果一致。测试样本数量有限，不表示所有语言表达均已验收。个别回复仍有术语文案瑕疵，例如将“无NULL”表述为“无非空字段”，后续应纳入回答语义评测。业务测试记录保存在被忽略的var目录，不随代码上传。
