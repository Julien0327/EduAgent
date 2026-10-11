# AIC 实施计划（EduMate-AIP）

> 时间窗：2026-10-10（今）→ 10-13 核心功能 → 10-14 材料/演示 → 10-15 提交。
> 本计划区分「我（Claude）能做的代码改动」与「必须由用户在平台/本机执行的操作」。

---

## 0. 阶段总览

| 阶段 | 内容 | 产出 | 依赖 |
|---|---|---|---|
| P0 | interop 适配层 + ACS/AIC/AIP/AMP 数据契约 + 离线单测 | 代码 + 测试 | 无（可立即做） |
| P1 | AIP RPC 真实互联（Leader→Partner）+ RabbitMQ 群组 + 流式 | 代码 | 平台 AIC + 证书 |
| P2 | 梧桐平台真实闭环：注册/发现/互联/跨端访问 + 截图/日志留痕 | 证据 | 用户平台操作 |
| P3 | 材料与演示（README/演练手册/录屏/成效数据） | 交付物 | P0-P2 实测 |

---

## 1. P0 —— interop 适配层（我负责，代码改动）

### 1.1 文件清单

```
backend/interop/
  __init__.py        # 导出公共符号
  config.py          # ACP 配置读取（env）
  models.py          # AIC/ACS/AIP/AMP 结构模型（复刻已核实 schema）
  aic_util.py        # AIC 解析/校验
  acs_builder.py     # 构建 ACS dict + 写出 acs.json
  amp.py             # AMP message/access 日志构造 + JSON-lines sink
  discovery.py       # ADP 发现客户端（httpx）
  aip_server.py      # FastAPI 路由：/rpc + /health（Partner）
  acs.template.json  # 能力描述模板（提交注册用）
  acp.toml.example   # 部署配置样例
backend/tests/
  test_interop_models.py     # 契约：ACS/AIC/AMP 字段与已核实 schema 一致
  test_interop_aip.py        # TaskCommand→workflow→TaskResult 映射（FakeLLM）
  test_interop_amp.py        # AMP 日志字段（correlationId/messageId/eventType）
```

### 1.2 关键实现点

- `models.py`：pydantic v2 模型，字段名/alias **逐字对齐** `acps_sdk` 源码（见差距分析 §2）。
- `amp.py`：`build_message_record(event_type, message_id, session_id, ...)` 保证
  `correlation_id=session_id`、`message_id` 在 send/receive 一致、`event_type∈{send,receive}`。
- `aip_server.py`：`POST /rpc` 解析 `RpcRequest`，`start` 命令 → 拼 prompt → `workflow.run_task()`
  → 把 `TaskContext` 产物映射为 `TaskResult`（`Product[]` 用 `file` DataItem，uri 指向产物下载端点）。
- `discovery.py`：`discover(query)` POST 到 `{DISCOVERY_BASE_URL}/acps-adp-v2/discover`，解析 `DiscoveryResponse`。

### 1.3 验收（P0）

- [ ] `pytest backend/tests/test_interop_*.py` 全绿
- [ ] `acs_builder` 产出的 acs.json 能通过 `AgentCapabilitySpec.model_validate`（若 SDK 已装）
- [ ] 不装 SDK 时，现有 34 项测试 + 新 interop 测试仍全绿（SDK 为可选依赖，惰性导入）

---

## 2. P1 —— AIP 真实互联（我负责，代码 + 联调指导）

- 把 `aip_server` 挂到主应用（`backend/main.py` 加一行 `include_router`，用 env 开关 `ACP_ENABLED`）。
- 用官方 `acps_sdk.aip.add_aip_rpc_router` + `CommandHandlers` 替换本地解析（若 SDK 已装），
  并开启 `identity_binding`（校验对端证书 AIC）。
- 联调：用官方 demo-leader 或 acps-cli 发 `TaskCommand` 到 EduMate `/rpc`，验证闭环。

---

## 3. P2 —— 梧桐平台真实闭环（**必须由用户在平台操作**，我提供步骤与留痕模板）

> ⚠️ 这些步骤我**无法代做**：注册账号、提交审核、申请证书、部署到可被平台访问的地址。
> 下面给出精确操作清单，完成后把截图/日志交回我归档到 `docs/evidence/`。

### 3.1 注册（用户，梧桐平台）

1. 打开 `https://wt.ioa.pub/registry/`，注册/登录账号。
2. 「新建 Agent」→ 填入 EduMate-AIP 名称/描述 → 上传 `interop/acs.template.json` 内容。
3. 提交审核，等待审核通过 → 平台分配 **AIC**。
4. 截图：平台后台「Agent 列表」页（含 AIC 与状态）→ 存 `docs/evidence/01_register.png`。

### 3.2 签发证书（用户，本机 acps-cli）

```bash
# 安装官方 CLI 并配置 acps-cli.toml（registry/ca/discovery 三处 base_url 指向 wt.ioa.pub）
acps-cli auth login
acps-cli cert eab fetch --aic <你的AIC>
acps-cli cert issue -a <你的AIC> --eab-file eab.json -u clientAuth \
  --key-path agent-key.pem --cert-path agent-cert.pem --trust-bundle-path trust-bundle.pem
```

### 3.3 部署 EduMate-AIP（用户）

- 配置 `.env`：`ACP_AIC=<AIC>`、证书路径、`ACP_ENABLED=true`。
- 用 HTTPS 起服务（`[server.mtls] tls_enabled=true, verify_client=true`），端口需**可从平台侧访问**。

### 3.4 验证四步（用户 + 平台）

| 步骤 | 操作 | 证据 |
|---|---|---|
| 注册 | §3.1 通过，拿到 AIC | `01_register.png` |
| 自动发现 | 平台/CLI 发起 discover 命中 EduMate 的 skills | `02_discovery.png` + 响应 JSON |
| 互联 | Leader/CLI 向 `/rpc` 发 `start` 命令，返回 `completed` | `03_interconnect.png` + 请求/响应 |
| 跨端访问 | 另一智能体/端访问 EduMate 产出物 URI | `04_cross_access.png` + 访问日志 |

---

## 4. P3 —— 材料与演示（我负责草拟，实测数据由 P2 提供）

- 更新 `README.md`、`docs/Demo演练手册.md`：加入 AIP 互联演示段。
- 编写 `docs/IMPLEMENTATION_STATUS.md`（每轮更新）。
- 成效数据**只写实测值**；时延/并发/访问量若未测，标注「未测」。

---

## 5. 需要用户执行的操作清单（汇总）

1. [ ] 梧桐平台注册账号并提交 EduMate-AIP 审核，取得 AIC。
2. [ ] 用 acps-cli 签发 mTLS 证书。
3. [ ] 将 EduMate-AIP 以 mTLS/HTTPS 部署到平台可达地址。
4. [ ] 完成注册/发现/互联/跨端访问四步验证并截图留痕。
5. [ ] 若 P1 需官方 SDK 联调，允许我安装 `acps-sdk`（从 AtomGit 源）。
