# 首次准备与日常查看

## 当前电脑：直接查看

双击项目根目录 **启动项目.cmd**（或Start.cmd），浏览器自动打开。启动窗口保持运行；关闭它会停止服务。日常启动不运行pip install或npm install。

本次依赖、React构建和dump查询快照已准备完成，不需要重新安装。默认优先使用`var/local-data.json`中的dump快照；不存在时使用保留的合成样例，并在页面显示来源。

本机已保存DeepSeek加密配置，原启动文件会默认连接`deepseek-flash`；无需每次输入密钥。本地网页自动授权。需要离线演示时运行`Start.cmd --provider demo`。密钥不会随GitHub同步，新电脑需要自己的本地配置。

## 新电脑或依赖变化

首次运行Setup.cmd：创建/使用根目录Python虚拟环境、安装Python依赖、安装前端依赖并构建。`pip install -e`表示开发源码直接生效；通常不需要每次改Python代码后重装。

Python库放`.venv`；React/Vite是JavaScript依赖，放`frontend/node_modules`，不能用Python虚拟环境代替npm依赖目录。两者均不提交Git。仅修改前端源码时执行`npm run build`再重启/刷新，无需重新安装全部依赖。

## 切换数据

```powershell
.\Start.cmd --data dump
.\Start.cmd --data synthetic
```

先关闭前一个服务窗口再启动。数据源在服务端选择，不接受浏览器指定任意数据库路径或账户。不同数据/模型模式使用独立Trace数据库，避免混入上下文。

dump读取流程：原始dump→先前已验证提取的完整SQLite→本次按字段白名单建立的只读查询快照。原始dump和完整SQLite不改动；仅复制materials、material_daily_trend_cache、brand_account_bindings、influencers、influencer_daily_trend_cache所需列，不复制凭证、对话正文或媒体链接。通过真实品牌账户绑定选一个有日报的品牌作为本地查看范围，不代表已登录线上业务系统。

重新准备新快照（输出文件必须不存在）：

```powershell
.\.venv\Scripts\python.exe -m ad_agent.prepare_data --source "你的完整SQLite路径" --output "D:\cst_proj\agent_service\var\new-business.sqlite"
```

合成样例与固定日期离线回归继续保留。dump主键为数字，请输入“素材339”这类业务素材编号，或直接在页面选择。历史数据查询使用页面显示的可用日期，不把历史快照说成当天实时数据。

## 页面操作

左侧搜索/选择素材→确认起止日期→查询表现或状态。中间展示对话、金额/ROI卡片及可折叠日报；右侧（窄屏下方）展示执行Trace，可展开和导出。也可直接输入规则或数据问题。真实模型未接通时有明确提示，不将规则演示当作语言模型效果。

端口被占用时先关闭旧窗口，或`Start.cmd --port 8767`。访问令牌仅在配置AGENT_API_TOKEN时需要，不写入浏览器持久存储。


## 批量与排行

打开页面“批量查询 / 排行 / 达人汇总”，选择对象、方式、日期、编号或完整昵称；编号留空查询授权范围内全部对象。日期不限31天。排行可选消耗/GMV/ROI与升降序；输入“第2页”续查。正式模式刷新保留消息和槽位；本机单次网页测试模式刷新会创建独立测试身份并清零，“新对话”只更换会话、保留本轮客户历史。有效答复后30分钟无负反馈自动确认解决，仍可输入“没解决”撤销。人工策略和未确认默认参数见[决策清单](decisions.md)。

## 作品集展示

本地端口网页适合开发、面试现场演示及克隆运行。127.0.0.1只指向访问者自己的电脑，不能作为公开演示地址。GitHub保存源码；访客按Setup.cmd准备后可使用合成数据与离线规则演示，无需你的密钥或dump。公开在线演示需要另行部署完整后端及前端，当前尚未部署。详见[作品集运行说明](portfolio.md)。
