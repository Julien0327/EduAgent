# AIC 目标架构（EduMate-AIP）

> 本文档描述 EduMate-AIP 如何在**不破坏现有教学业务闭环**的前提下，接入 AIP 智能体互联国标。
> 架构原则：**业务层不变，协议层外挂**——用一个独立的 `interop/` 适配层把现有能力翻译成 ACPs 协议语义。

---

## 1. 总览

```
                     梧桐智能体互联平台 (wt.ioa.pub)
   ┌────────────────────────────────────────────────────────────┐
   │  registry-server (注册)   discovery (发现)   ca-server (证书) │
   │  mq-auth-server (RabbitMQ 消息面)   monitor (Kafka 监控面)    │
   └────────────────────────────────────────────────────────────┘
        ▲ 注册/审核/取AIC/签证书           ▲ discover/RPC/AMP
        │(人工，平台操作)                    │(运行时，mTLS)
   ┌────┴────────────────────────────────────────────────────┴────┐
   │                     EduMate-AIP 运行时                        │
   │                                                              │
   │  ┌──────────────────────── interop 适配层 ──────────────────┐ │
   │  │  aic_util     AIC 解析/校验                                │ │
   │  │  acs_builder  生成 acs.json（能力描述，提交注册）          │ │
   │  │  aip_server   暴露 POST /rpc + GET /health（AIP Partner）  │ │
   │  │  discovery    ADP 发现客户端                               │ │
   │  │  amp          AMP 日志（message/access）发射器            │ │
   │  └──────────────────────────┬───────────────────────────────┘ │
   │                             │ TaskCommand → TaskResult        │
   │  ┌──────────────────────────▼───────────────────────────────┐ │
   │  │  现有业务层（不变）                                        │ │
   │  │  Planner → Workflow → Executor → ToolRegistry             │ │
   │  │  → 5 个文档服务（组卷/答题卡/学情报告/教案…）              │ │
   │  └──────────────────────────────────────────────────────────┘ │
   └───────────────────────────────────────────────────────────────┘
```

---

## 2. 角色定位：EduMate-AIP 作为 AIP **Partner**

根据 AIP 协议，智能体分 Leader / Partner 两种角色：

- **Leader**：编排者，把任务拆分并分派给多个 Partner，聚合结果。
- **Partner**：能力提供者，接收 `TaskCommand`，返回 `TaskResult`（状态 + 产出物）。

EduMate-AIP 的定位是 **Partner**：它擅长「教学任务自动化」，把自然语言教学需求
（组卷/答题卡/学情报告/教案）作为一项能力对外提供。未来若要做「多智能体互联」演示，
可再实现一个 Leader（复用 demo-leader 的 planner/executor 思路），调用 EduMate 这个 Partner。

**本阶段（P0）交付一个可被 Leader / 平台调用的 Partner 端点。**

---

## 3. interop 适配层设计

新增目录 `backend/interop/`，**不修改** `app/` 下任何业务文件。

| 模块 | 职责 | 依赖官方 SDK? |
|---|---|---|
| `interop/config.py` | 读取 ACP 配置（AIC、证书路径、registry/discovery/mq 地址） | 否（纯 env/文件） |
| `interop/models.py` | AIC/ACS/AIP/AMP 结构模型（忠实复刻已核实的 schema） | 可选（优先 import SDK，缺失则用本地等价模型） |
| `interop/acs_builder.py` | 从配置构建 ACS dict / 写出 `acs.json` | 可选 |
| `interop/aic_util.py` | AIC 解析/校验（薄封装） | 可选 |
| `interop/aip_server.py` | FastAPI 路由：`POST /rpc`、`GET /health`，`TaskCommand→workflow→TaskResult` | 否（本层定义，运行时做真实网络 I/O 时用 SDK） |
| `interop/amp.py` | AMP `message`/`access` 日志记录构造 + 本地 JSON-lines sink | 可选 |
| `interop/discovery.py` | ADP 发现客户端（httpx，POST discover） | 可选 |

**关键诚实性设计**：
- 数据模型是「已核实 schema 的忠实复刻」，用于**离线生成 acs.json 与单测契约校验**，
  不属于「用本地 Mock 冒充官方协议」——因为**真实注册/发现/互联/跨端访问必须走平台 + 官方 SDK**。
- 运行时的真实网络路径（mTLS 握手、RabbitMQ 连接、Kafka AMP 上报、discover RPC）
  **必须 import 官方 `acps_sdk`**；SDK 未安装/未配置时**明确报错**，绝不静默回退到假实现。

---

## 4. AIP 端点语义（Partner 视角）

### 4.1 `GET /health`

返回（对齐 demo-partner 语义）：

```json
{ "agent": "EduMate-AIP", "status": "online", "aic": "<aic 后8位或占位>", "tasks": { "active": 0 } }
```

### 4.2 `POST /rpc`（JSON-RPC 2.0）

请求（Leader → Partner）：

```json
{
  "jsonrpc": "2.0", "id": "1", "method": "rpc",
  "params": { "command": {
    "type": "task-command", "command": "start",
    "taskId": "t-123", "senderRole": "leader", "senderId": "<leader AIC>",
    "sessionId": "s-456",
    "dataItems": [ {"type":"text","text":"请对《一元二次方程》做章节小测并生成学情报告"} ]
  } }
}
```

响应（Partner → Leader）：

```json
{
  "jsonrpc": "2.0", "id": "1",
  "result": {
    "type": "task-result", "taskId": "t-123",
    "senderRole": "partner", "senderId": "<EduMate AIC>", "sessionId": "s-456",
    "status": { "state": "completed", "stateChangedAt": "<ISO8601>", "dataItems": [] },
    "products": [
      { "id": "p-1", "name": "试卷", "dataItems": [ {"type":"file","name":"01_试卷.docx","uri":"..."} ] }
    ]
  }
}
```

### 4.3 `TaskCommand → Workflow` 映射

AIP 的 `start` 命令触发一次完整教学工作流，把 `dataItems` 中的文本拼成 `prompt`，
交给现有 `workflow.run_task(prompt)` 执行，最后把产物映射为 AIP `Product[]`。

| AIP 命令 | EduMate 行为 | TaskState |
|---|---|---|
| `start` | 新建任务并执行工作流 | `accepted → working → completed/failed` |
| `get` | 返回当前任务快照（增量可选） | 不变 |
| `cancel` | 中止任务（终态才不可取消） | `canceled` |
| `complete` | 仅当 `awaiting-completion` 时置 `completed` | `completed` |
| `continue` | 仅 `awaiting-input/completion` 时继续 | 业务实现 |

P0 先实现 **`start`（同步执行工作流）** 与 **`get`（快照）** 两个命令的最小子集；
`cancel/continue/complete` 复用 SDK `DefaultHandlers` 语义，作为 P1 增强。

---

## 5. 能力描述 ACS（提交注册用）

EduMate-AIP 的 `acs.json` 以「教学任务自动化」为核心，技能（skills）对齐现有三条工作流：

1. `edumate.exam-loop` —— 章节小测一条龙（出卷 + 答题卡 + 学情报告）
2. `edumate.question-gen` —— 智能组卷
3. `edumate.lesson-plan` —— AI 教案生成

端点（endPoints）P0 先声明 **JSONRPC**（点对点 `/rpc`，mTLS）；AMQP 群组端点作为 P1。
完整模板见 `interop/acs.template.json`。

---

## 6. 安全与配置

| 项 | 说明 |
|---|---|
| AIC | 注册审核通过后由平台分配，写入 `.env`（`ACP_AIC`），不硬编码 |
| mTLS | 证书由平台 CA 签发（`agent-key.pem`/`agent-cert.pem`/`trust-bundle.pem`），路径写入配置 |
| 密钥 | LLM Key、RabbitMQ accessToken、证书私钥**一律环境变量/文件，不入库** |
| identity_binding | 开启：校验 `senderId` 与对端证书 AIC 一致（官方 `aip_identity` 语义） |

---

## 7. 非目标（明确不做）

- 不把通用 REST（`/api/tasks`）宣称成 AIP——AIP 走独立的 `/rpc` + JSON-RPC 语义。
- 不重写 Planner/Workflow/Executor 业务逻辑。
- 不引入 LangChain/LangGraph 等重型框架（沿用「零重型框架」原则）。
- 不在 P0 实现 RabbitMQ 群组模式与 SSE 流式 AIP（P1）。
