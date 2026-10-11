# AIC「智能体互联」升级差距分析（EduMate-AIP）

> 本文档基于对 **现有 EduAgent 代码** 与 **官方 AIP/ACPs 开源代码** 的逐行核实写成。
> 所有「官方结论」均标注来源文件路径；未标注的字段与结论一律不采用。
> 官方代码来源：AtomGit `AIP-PUB/ACPs-community`（GitHub 不可达，改用 AtomGit 镜像克隆），
> 核查基于本地克隆的 ACPs-community 官方项目源码（下称 `ACPs/`）。

---

## 1. 结论摘要

| 维度 | 现状 | 差距 | 优先级 |
|---|---|---|---|
| 协议基础 | 无任何 AIP/ACPs 代码 | 需从 0 接入 ACPs SDK | P0 |
| 注册/发现/互联/跨端访问 | 无 | 需在梧桐平台完成真实闭环 | P0（依赖平台审核） |
| 身份码 AIC / mTLS | 无 | 需注册后获取 AIC + 签发证书 | P0（依赖平台审核） |
| 监控日志 AMP | 现有统一日志（非 AMP） | 需新增 AMP 兼容字段（correlationId/messageId/eventType） | P0 |
| 能力描述 ACS | 无 | 需产出符合 ACS v02.02 的 acs.json | P0 |
| 教学业务闭环 | 已完整（34 测试通过） | 无需重做，仅需暴露为 AIP 能力 | P1 |

**一句话**：现有 EduAgent 的教学业务闭环（组卷/答题卡/学情报告/教案）已经成熟，
差距几乎全部集中在「把该业务能力以 ACPs 协议对外暴露」这一层——即 AIC/ACS/AIP/ADP/AMP
五件套 + 梧桐平台真实闭环。

---

## 2. 官方技术事实（已核实，非凭印象）

### 2.1 ACPs SDK 是 Python，可直接与 FastAPI 集成

- 来源：`ACPs/acps-sdk/pyproject.toml`
- 版本 `2.2.0`，许可证 `MulanPSL-2.0`（宽松许可，允许集成与再分发）。
- 依赖：`pydantic>=2.0`、`httpx>=0.24`、`aio-pika>=9.0`（RabbitMQ）、`fastapi>=0.100`、`uvicorn>=0.30`；
  可选 `amp-sign`（jcs+cryptography）、`oidc`（PyJWT）。
- 与现有 EduAgent 的 FastAPI + pydantic v2 + Python 3.13 技术栈**天然兼容**。

### 2.2 五件套模块（来源：`ACPs/acps-sdk/acps_sdk/`）

| 协议 | SDK 模块 | 职责 |
|---|---|---|
| AIC | `acps_sdk.aic` | 身份码解析/校验（`validate_aic_format`/`parse_aic`/`AICValidator`） |
| ACS | `acps_sdk.acs.models` | 能力描述数据模型 `AgentCapabilitySpec` |
| ADP | `acps_sdk.adp.models` | 发现协议 `DiscoveryRequest`/`DiscoveryResponse` |
| AIP | `acps_sdk.aip` | 交互协议（`aip_rpc_server`/`aip_group_partner`） |
| AMP | `acps_sdk.amp.models` | 监控日志 6 类模型（heartbeat/metrics/access/message/audit/system） |

### 2.3 AIC 结构（来源：`ACPs/acps-sdk/acps_sdk/aic/__init__.py`）

- 10 级点分结构：`1.2.156.3088`（OID 前缀）`. 版本(1位) . ARSP(1-6位) . Vendor(1-6位) . Ontology SN(1-9位) . Instance SN(1-9位) . CRC(4位)`。
- 第 5–10 级为 Base36；CRC 用 `CRC-16/CCITT-FALSE` + ARSP 内部 SALT 计算。
- 示例（来自官方 demo）：`1.2.156.3088.1.1.D55UOU.NEBZUA.1.0QLD`。

### 2.4 ACS 字段（来源：`ACPs/acps-sdk/acps_sdk/acs/models.py` + `demo-partner/.../acs.json`）

根对象 `AgentCapabilitySpec` 关键字段（camelCase 为 JSON 线格式）：

```
aic, active, lastModifiedTime, protocolVersion("02.02"),
name, description, version,
iconUrl?, documentationUrl?, webAppUrl?,
provider{ countryCode, organization, department, url, license, name, email },
securitySchemes{ mtls: {type:"mutualTLS"} | ... },
endPoints[{ url, transport("JSONRPC"|"HTTP_JSON"), security:[{mtls:[]}] }],
capabilities{ streaming, notification, messageQueue[] },
defaultInputModes[], defaultOutputModes[],
skills[{ id, name, description, version, tags[], examples[], inputModes[], outputModes[] }]
```

### 2.5 AIP 交互模型（来源：`ACPs/acps-sdk/acps_sdk/aip/aip_base_model.py` + `aip_rpc_server.py`）

- RPC：JSON-RPC 2.0，`POST /rpc`，`method="rpc"`，`params.command` 为 `TaskCommand`。
- `TaskCommand` 继承 `Message`：`type="task-command"`、`command`（`get/start/continue/cancel/complete/re-stream`）、`commandParams`、`taskId`、`senderRole`、`senderId`、`sessionId`、`dataItems`。
- `TaskResult` 继承 `Message`：`type="task-result"`、`taskId`、`status{state,stateChangedAt,dataItems}`、`products[]`、`commandHistory[]`、`statusHistory[]`。
- `TaskState`：`accepted/working/awaiting-input/awaiting-completion/completed/canceled/failed/rejected`。
- SDK 提供 `add_aip_rpc_router(app, endpoint, handlers, local_aic, identity_binding_enabled)` 与
  `CommandHandlers(on_start/on_get/on_cancel/on_complete/on_continue/on_message)`。
- 群组模式走 RabbitMQ：`acps_sdk.aip.aip_group_partner.GroupPartnerMqClient`。

### 2.6 AMP 日志字段（来源：`ACPs/acps-sdk/acps_sdk/amp/models.py`）

顶层 `LogRecord`（snake_case 线格式）：`schema_version, timestamp, aic, log_type, log_id, trace_id, span_id, parent_span_id, correlation_id, severity_text, severity_number, body, ...`。

- **`correlation_id` = 业务关联 ID**；在 AIP 场景下，发送/接收双方用**同一 `sessionId`** 作为 `correlation_id`（见 `aip_group_partner.py` 中 `correlation_id=body.get("sessionId")`）。
- `MessageBody`（`log_type="message"`）关键字段：`eventType`（`send/receive/ack/nack/reject/timeout/dead_letter`）、`system`、`destination`、`messageId`、`payloadSizeBytes`、`deliveryAttempt`、`settlement`、`error`。
- **同一消息的 send 与 receive 各是一条独立日志，通过相同 `messageId` + `correlationId` 在查询层归并为一条生命周期**（`models.py` 文档注释原文）。
- `AccessBody`（`log_type="access"`）：`durationMs, request{method,url,route,headers,bodySizeBytes}, response{statusCode,bodySizeBytes}, caller{aic,serviceName}, callee{aic,serviceName}, error`。
- 上报通道：Kafka/TLS `wt.ioa.pub:19092`（AMP 监控面）。

### 2.7 发现协议 ADP（来源：`ACPs/acps-sdk/acps_sdk/adp/models.py`）

- `DiscoveryRequest`：`type(explicit/exploratory/trending/filtered), query, context, limit, filter, forward*`。
- `DiscoveryResponse`：`result{ acsMap{AIC→ACS}, agents[], routes[] } | error`。
- 平台发现端点：`POST https://wt.ioa.pub/discovery/acps-adp-v2/discover`（ADP v2）。

### 2.8 注册平台 API（来源：`ACPs/registry-server/app/agent/api.py`）

- 公开：`GET /agent/public/acs_example`（返回 ACS 示例 JSONC）、`POST /agent/public/send_code`、`GET /agent/public/recent`。
- 客户端（CLIENT 角色，登录后）：`POST /agent/client`（建草稿）、`GET /agent/client`（列表）、`GET/PUT /agent/client/{id}`、`POST /agent/client/{id}/submit`（提交审核）、`POST /agent/client/{id}/cancel`、`DELETE /agent/client/{id}`。
- base_url：`https://wt.ioa.pub/registry-server`。

### 2.9 mTLS 证书链路（已核实，来自 `acps-cli` 与 `docs.html`）

```
注册(草稿) → 提交审核 → 审核通过 → 获得 AIC
→ acps-cli auth login
→ acps-cli cert eab fetch --aic <aic>          # 得到 eab.json
→ acps-cli cert issue -a <aic> --eab-file eab.json -u clientAuth \
     --key-path agent-key.pem --cert-path agent-cert.pem --trust-bundle-path trust-bundle.pem
```
- `acps-cli.toml`：`[registry] base_url`、`[ca] base_url`、`[discovery] base_url`。
- `demo-partner` 侧用 `server.pem`/`server.key`/`trust-bundle.pem` 配置 `[server.mtls]`（`tls_enabled=true, verify_client=true`）。

---

## 3. 现有 EduAgent 现状（以实际代码为准）

| 组件 | 位置 | 现状 |
|---|---|---|
| 入口 | `backend/main.py` | FastAPI + CORS `*` + 静态托管 + 启动 seed 校验 |
| 规划 | `backend/app/agent/planner.py` | `plan()`：1 次 LLM + 关键词兜底 |
| 编排 | `backend/app/agent/workflow.py` | 3 个固定工作流（exam_loop/question_gen/lesson_plan），超时 180s |
| 执行 | `backend/app/agent/executor.py` | `run_tool()` 单工具薄封装，无自由选工具 |
| 工具 | `backend/app/tools/registry.py` | `ToolRegistry` + 3 个 LLM 工具 |
| LLM | `backend/app/agent/llm_client.py` | `LLMService`/`DeepSeekProvider`（OpenAI 兼容） |
| 文档服务 | `backend/app/services/*` | 5 个确定性文档服务 |
| API | `backend/app/api/{chat,health,tasks}.py` | `/api/health`、`/api/chat`、`/api/tasks*`、SSE、下载/预览 |
| 测试 | `backend/tests/` | 34 项（FakeLLM 离线模式） |
| AIP/ACPs | 无 | 全项目 grep 无任何 `acps`/`AIC`/`ACS`/`ADP`/`AMP` 相关代码 |

**未发现**：任何 AIC 身份码、ACS 能力描述、AIP 交互端点、ADP 发现、AMP 日志、mTLS 证书。
因此本项目的升级是「从 0 接入协议层」，而非「改造既有协议实现」。

---

## 4. 与竞赛硬性要求的逐条差距

| 竞赛要求 | 现状 | 差距动作 |
|---|---|---|
| 基于 AIP 国标 + 官方 ACPs 开源代码 | 未接入 | 引入 `acps-sdk==2.2.0` 作为依赖，实现 interop 适配层 |
| 完整多智能体互联逻辑 | 无 | 将 EduAgent 封装为 AIP Partner，暴露 `/rpc` + `/health` |
| 注册/发现/互联/跨端访问 在梧桐平台真实验证 | 无 | 用户在平台注册→审核→取 AIC→签证书→部署→discover→RPC 实测 |
| 保存平台后台截图/交互记录/验证日志 | 无 | 需平台操作留痕（见实施计划 §5） |
| 不得用本地 Mock / 两实例冒充平台验证 | — | 明确：离线单测只验数据契约；真实闭环必须走平台 |
| 不得编造时延/访问量/并发/成效 | — | 材料只写实测值，未测项标注「未测」 |

---

## 5. 关键风险与对策

| 风险 | 说明 | 对策 |
|---|---|---|
| 平台审核周期不可控 | 注册→审核通过→发 AIC 需要平台人工审核 | 尽早提交注册；开发与审核并行 |
| RabbitMQ/AMQP 群组模式依赖重 | demo-partner 需 RabbitMQ `amqps://localhost:5671` | P0 先用 **AIP RPC（点对点 `/rpc`）** 满足互联闭环；群组模式作为 P1 增强 |
| mTLS 证书需平台签发 | 本地无法自签冒充 | 严格按 2.9 流程，证书由平台 CA 签发 |
| 官方 SDK 未上 PyPI | 需从 AtomGit 源安装 | 锁定 `git+https://atomgit.com/AIP-PUB/ACPs-community.git#subdirectory=acps-sdk` |
| AMP Kafka 上报需证书 | 监控面 TLS | 无证书时降级为「本地 JSON-lines + 结构正确」，材料中如实说明 |

---

## 6. 下一步

见 `AIC_TARGET_ARCHITECTURE.md`（目标架构）与 `AIC_IMPLEMENTATION_PLAN.md`（实施计划）。
