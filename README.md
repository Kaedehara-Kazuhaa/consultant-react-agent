# consultant-react-agent

日常查看：双击根目录 **启动项目.cmd**，无需重新安装；首次新环境使用Setup.cmd。[快速上手](agent_service/docs/quickstart.md)。

面向品牌方投手客服的可自定义Agent/Skill ReAct项目，包含结构化工具循环、授权查询、多轮槽位及可导出的执行Trace。

- [启动与使用](agent_service/README.md)
- [架构及实现边界](agent_service/docs/architecture.md)
- [工具与知识合同](agent_service/docs/tool-contracts.md)
- [Skill维护规范](agent_service/docs/skill-authoring.md)
- [运行和排障](agent_service/docs/runbook.md)
- [链路验收说明](agent_service/docs/acceptance.md)

默认使用合成数据及确定性演示决策器。真实模型接口可配置，但Demo通过不等于真实模型效果已达标。备份数据、数据库、运行日志与密钥不纳入仓库。

新版提供React网页、dump只读业务快照和保留的合成样例。[模型调用说明](agent_service/docs/model-access.md) · [ROI与GMV口径](agent_service/docs/metrics.md)。
