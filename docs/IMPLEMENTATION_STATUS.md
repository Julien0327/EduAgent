# 实施状态（IMPLEMENTATION_STATUS）

> 更新时间：2026-10-11（官方 SDK 接入 + 产物绝对 URI + 部署准备）
> 项目：EduMate-AIP（基于现有 EduAgent，升级为 AIC「智能体互联」参赛作品）
> 本文件如实记录：真实状态、已完成的代码改动、实际运行的测试与结果、未完成项与阻塞、需用户执行的操作。

---

## 1. 结论速览（TL;DR）

| 项 | 状态 |
|---|---|
| P0 interop 适配层（代码 + 数据契约 + 离线单测） | ✅ 已完成 |
| P1 把 AIP `/rpc` + `/health` 挂到主应用（env 开关） | ✅ 代码已完成，**未与平台联调** |
| 全量测试 | ✅ 60 passed（34 现有 + 26 新增） |
| P2 梧桐平台真实闭环（注册/发现/互联/跨端访问） | ❌ **尚未开始**（需用户在平台操作） |
| 真实平台验证证据（截图/日志/交互记录） | ❌ **尚无** |
| 时延 / 访问量 / 并发 / 成效数据 | ❌ **未测，未编造** |

---

## 2. 本轮已完成的代码改动

### 2.1 新增 interop 适配层（`backend/interop/`，全部为本轮新增）

| 文件 | 说明 |
|---|---|
| `__init__.py` | 导出 `AcpSettings` / `load_acp_settings` |
| `config.py` | ACP 配置读取（env：`ACP_ENABLED/AIC/NAME/证书路径/平台端点/MQ/AMP 日志`），`rpc_url` / `has_mtls` 属性 |
| `models.py` | 对已核实 `acps_sdk` schema 的忠实复刻：AIC/ACS/AIP（`TaskState`/`TaskCommand`/`TaskResult`/`RpcRequest`/`RpcResponse`）+ AMP（`LogRecord`/`MessageBody`/`AccessBody`），线格式 camelCase，`to_wire()` |
| `aic_util.py` | AIC 10 级点分结构解析/校验、Base36、CRC-16/CCITT-FALSE（需 SALT 才做 CRC）、本体/实体判定 |
| `acs_builder.py` | 构建 ACS dict（protocolVersion `02.02`、3 条 skill、mTLS endpoint）+ `validate_acs` + 可选 SDK 二次校验 + `write_acs` |
| `amp.py` | AMP `message`/`access` 日志构造（`correlation_id=session_id`、`messageId` 复用）+ JSON-lines sink + `AmpEmitter` |
| `discovery.py` | ADP 发现客户端（`build_discovery_request` / `discover` / `response_agents`，走 httpx 真实请求，不提供本地假实现） |
| `aip_server.py` | AIP Partner 端点：`POST /rpc`（JSON-RPC 2.0，`start`→workflow→`TaskResult`、`get`→快照）+ `GET /health` |
| `acs.template.json` | 能力描述模板（注册提交用；provider 不含学校名称/LOGO/指导教师） |
| `acp.toml.example` | 部署配置样例（mTLS/rabbitmq/concurrency） |

### 2.2 主应用接线（`backend/main.py`）

- 新增 `interop` 导入，`ACP_ENABLED=true` 时挂载 `create_aip_router()`（`/rpc` + `/health`）；默认关闭，**不影响现有 `/api/*` 与前端**。

### 2.3 配置样例（`.env.example`）

- 追加 `ACP_*` 环境变量清单（全部注释、不含任何密钥）。

### 2.4 新增测试（`backend/tests/`）

| 文件 | 覆盖 |
|---|---|
| `test_interop_models.py`（16 项） | AIC 格式/解析/本体实体；ACS 字段齐全/三 skill/无学校信息；AIP 线格式 camelCase |
| `test_interop_amp.py`（4 项） | `correlationId`/`messageId`/`eventType` 不变量；send/receive 归并；JSON-lines 落盘 |
| `test_interop_aip.py`（6 项） | `TaskCommand`→workflow→`TaskResult` 映射（FakeLLM）、`extract_text`、start/get/未知任务/未实现命令错误路径 |

---

## 3. 实际运行的测试与结果

命令（Windows，venv）：

```
./.venv/Scripts/python.exe -m pytest backend/tests/ -q
```

结果：**60 passed in ~2.3s**（此前基线 34 项，本轮新增 26 项全绿，无回归）。

说明：
- 测试使用既有 `FakeLLM` 模式（monkeypatch `app.agent.llm_utils.get_llm_service`），**不调用真实 DeepSeek API、不联网**。
- `main.py` 在 `ACP_ENABLED` 默认关闭 / 开启两种情况下均已验证可正常导入（未做真实 RPC 联调）。

---

## 4. 未完成项与阻塞

| 项 | 状态 | 阻塞原因 |
|---|---|---|
| 梧桐平台注册（取得 AIC） | ❌ 未做 | 需用户在平台注册/提交审核 |
| mTLS 证书签发 | ❌ 未做 | 需用户用 `acps-cli` 签发 |
| 平台可达部署（HTTPS/mTLS） | ❌ 未做 | 需用户部署 + 网络可达 |
| 自动发现 / 互联 / 跨端访问四步验证 | ❌ 未做 | 依赖上述平台操作 |
| 官方 `acps_sdk` 真实运行时联调 | ❌ 未做 | SDK 尚未安装（可选依赖，惰性导入）；真实运行须用官方 SDK 而非本地复刻模型 |
| AMP Kafka/TLS 上报 | ❌ 未做 | P1，需平台 Kafka 证书 |
| RabbitMQ 群组互联 | ❌ 未做 | P1，需平台 MQ 凭证 |
| 时延/并发/成效数据 | ❌ 未测 | 未做平台实测，**不编造** |

---

## 5. 需要用户执行的操作（阻塞项，我无法代做）

按 `docs/AIC_IMPLEMENTATION_PLAN.md` §3 的精确步骤：

1. [ ] 梧桐平台 `https://wt.ioa.pub/registry/` 注册账号 → 新建 Agent → 上传 `backend/interop/acs.template.json` → 提交审核 → 取得 **AIC**（截图存 `docs/evidence/01_register.png`）。
2. [ ] 本机安装官方 `acps-cli`，`auth login` + `cert eab fetch` + `cert issue` 签发 mTLS 证书（`agent-key/cert/trust-bundle`）。
3. [ ] 配置 `.env`（`ACP_ENABLED=true`、`ACP_AIC`、证书路径、平台可达 `ACP_SERVICE_HOST`），以 HTTPS 部署。
4. [ ] 完成注册 → 自动发现 → 互联 → 跨端访问四步验证，截图/日志交回我归档（`docs/evidence/`）。
5. [ ] 授权我安装官方 `acps-sdk`（AtomGit 源），用于 P1 真实运行时联调替换本地模型。

> 这些步骤需要真实平台账号、证书与网络，**本地测试通过不能代表平台验证完成**。

---

## 6. 下一优先级任务

1. 按用户平台操作结果，进入 **P1**：用官方 `acps_sdk`（`add_aip_rpc_router` + `CommandHandlers` + `identity_binding`）替换 `aip_server.py` 的本地解析，联调真实 `TaskCommand` 闭环。
2. 补齐 P0 遗漏：`ACS` 内 `endPoints.url` 的 `ACP_SERVICE_HOST` 需换成平台可达公网地址（当前默认 `localhost`，仅供本地模板）。
3. 归档 P2 证据后，进入 **P3** 材料与演示。

---

## 7. 关于「平台验证」与「数据」的明确声明

- **梧桐平台真实注册 / 发现 / 互联 / 跨端访问：尚未完成**，当前无平台后台截图、无交互记录、无验证日志。
- 本地 `models.py` 是对已核实 `acps_sdk` schema 的**数据契约复刻**，仅用于离线生成 `acs.json` 与契约单测；**它不被用于冒充真实协议通信**，真实互联必须走梧桐平台 + 官方 `acps_sdk`。
- 通信时延、访问量、并发能力、应用成效等数据**未测量、未编造**，材料中一律标注「未测」。

---

## 8. 本轮补充（2026-10-11）

> 本节覆盖「官方 SDK 接入 + 产物绝对 URI + 部署准备」三件事的最新事实，第 1 轮历史记录见 §2–§7。

### 8.1 官方 SDK 与产物 URI 现状

- 官方 `acps-sdk==2.1.0` 已接入：`POST /rpc` 委托官方 `handle_rpc_request`（JSON-RPC 2.0），全量测试 **73 passed**。
- 产物 `FileDataItem.uri` 已由相对路径改为**绝对 URL**，由可配置公开基址生成，文件名做 percent-encoding。

### 8.2 `ACP_PUBLIC_BASE_URL`（新增配置）

- **含义**：远程调用方访问本服务的公开基址 `scheme://host[:port]`，同时用于 `ACS.endPoints.url` 与产物下载 URI。
- **部署配置示例（`.env`）**：

  ```ini
  ACP_ENABLED=true
  ACP_PUBLIC_BASE_URL=https://<公网域名或IP>:9443
  # 未设置时回退 ACP_SERVICE_HOST:ACP_SERVICE_PORT（scheme 由是否配置证书决定）
  ```

- **限制**：`localhost` / `127.0.0.1` 仅供本机开发，**不可用于远程调用**——远程 leader 无法访问 `localhost`。部署时必须把 `ACP_PUBLIC_BASE_URL` 设为平台可达地址。

### 8.3 产物 URI 的任务 ID 语义

- 产物 URI 形如 `{base}/api/tasks/{业务任务ID}/files/{文件名}`，路径段是**业务工作流内部任务 ID**（`TaskContext.task_id`，如 `acac113f809e`），**不是** AIP 协议的 `TaskResult.taskId`（如 `t-abc`）。
- 原因：下载端点 `GET /api/tasks/{task_id}/files/{file_name}` 以业务 `TaskStore` 内部 ID 为键。两个 ID 分属不同空间，**不可互相映射**。

### 8.4 尚未完成（如实标注，未验证即未验证）

| 项 | 状态 |
|---|---|
| 真实远程产物下载（跨端访问） | ❌ 未做（无部署、无 mTLS） |
| mTLS 双向认证 | ❌ 未做（未签发证书） |
| 梧桐平台注册 / 提交 ACS | ❌ 未做 |
| 真实跨智能体调用（Leader→Partner） | ❌ 未做 |

本地 73 项测试通过**不代表**平台验证或真实互联完成。
