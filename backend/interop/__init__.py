"""interop —— EduMate-AIP 的 ACPs 协议适配层。

把现有教学业务能力（Planner → Workflow → Executor → 文档服务）翻译为
AIP 智能体互联国标语义：AIC（身份码）、ACS（能力描述）、AIP（交互）、
ADP（发现）、AMP（监控日志）。

设计原则（详见 docs/AIC_TARGET_ARCHITECTURE.md）：
- 业务层不变，协议层外挂：本包不修改 app/ 下任何业务文件。
- 数据模型是对「已核实 schema」的忠实复刻，用于离线生成 acs.json 与契约单测；
  真实注册/发现/互联/跨端访问必须走梧桐平台 + 官方 acps_sdk，本包绝不静默回退到假实现。
- 官方 acps_sdk 为可选依赖：未安装时本包仍可运行离线部分（模型/校验/日志构造），
  涉及真实网络 I/O 的路径会明确报错并提示安装官方 SDK。

来源核实：ACPs-community/acps-sdk/acps_sdk/{aic,acs,adp,aip,amp}，核查于本地克隆的
ACPs-community 官方项目源码（AtomGit `AIP-PUB/ACPs-community`）。
"""

from .config import AcpSettings, load_acp_settings

__all__ = ["AcpSettings", "load_acp_settings"]
