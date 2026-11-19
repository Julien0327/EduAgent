# Phase 5 执行计划（纵深：critique 质检环 + MCP 能力开放）

> 基准：docs/技术方案.md §8（Phase 5·决策③）。本计划由用户 2026-11-19 四项最终决策 + 补充工程约束定稿；范围不扩大。
> 开工前已实测：仓库干净（HEAD=origin/main=d13edce）、测试基线 **447 passed / 59.10s**、mcp SDK 未装（PyPI 最新 2.3.0）。

## 0. 四项决策（用户最终确认，2026-11-19）

| # | 决策 | 要点 |
|---|---|---|
| 1 | critique 作用范围 | **仅 question_gen**；exam_loop 冻结题库确定性基准不改、lesson_plan 不属题目质量控制 |
| 2 | 真实质检指标 | **最多 10 条真实 question_gen**（真 DeepSeek）；先报预算获批再执行；与 FakeLLM 严格分离 |
| 3 | MCP 敏感工具授权 | **普通 Token + 管理员 Token + 环境总开关**；复用 SecurityMiddleware；不引入 OAuth/RBAC |
| 4 | 遗留修复 | ①question_gen 生成 prompt「初中→小学数学教师」；②`_generate_questions_llm` 错误返回/题量不足显式失败 |

## 1. 与 §8.1–§8.3 的差异与调整理由（如实记录）

| 位置 | 基线表述 | 本计划调整 | 理由 |
|---|---|---|---|
| §8.1 回流 | 仍不合格 → 剔除（`status='retired'`） | LLM 生成题**无 DB 落点**（不入 questions 表，决策⑲ 四状态不含「生成中」），改为质检报告 `retired` 状态 + 任务结果缺口报告 | 生成题不持久化；`questions.status` 只承接 processed/rejected 口径，不加新枚举 |
| §8.1 正确性 | 数值题代回验证 | 代回验证适用于题库 ETL 的 equation 字段，**不适用于 LLM 生成的应用题**（无等式）；生成题确定性校验改为：选择题答案在选项内+选项 4 个、答案非空无占位符、数值答案可解析与量纲一致性 | 诚实边界：规则层不宣称能判断应用题答案数学对错，完整正确性由 LLM 一题一验承担 |
| §8.2 指标 | 拦截率、回流收敛（2 项核心） | **扩展为 6 项**：首轮拦截率、全轮拦截率、最终剔除率+数、平均收敛轮次、回流成功率、降级任务数（原 2 项为子集） | 用户决策指定；指标均附分母/分子/样本量 |
| §8.3 认证 | 远程「Access Token 认证 + 限流 + 敏感工具授权」 | 细化为**双 Token（普通/管理员）+ 环境总开关** | 用户决策对「敏感工具授权」的实现细化，不另建安全体系 |

其余 §8 约束不变：三工具复用服务层、不在 MCP 层重写业务逻辑、stdio 本地受信 + Streamable HTTP 远程、参数白名单/SQL 参数化在服务层统一生效。

## 2. critique 质检环设计（§8.1）

- **作用范围**：仅 `question_gen` 工作流；作为独立节点插在 `_generate_questions_llm` 之后、组卷（`_build_exam_docx`）之前。
- **输入**：本批 LLM 生成的题目 + 图谱上下文（知识点名/描述）。
- **三维校验**：
  - **正确性**：①确定性规则（不依赖 LLM，优先独立执行）：选择题答案必须在选项内且选项为 4 个、答案非空且无占位符、数值答案可解析与量纲/单位一致性；②LLM 一题一验（严格 schema，结构化输出）。
  - **难度复核**：规则层题干长度/运算步骤数代理指标 vs 标注档位比对告警；LLM 五档复判；**规则优先**（§8.1）。
  - **查重**：①**批内查重**（本批题目两两相似度，防同卷重复）；②**历史题库查重**：`questions_fts`（jieba 分词）召回 top-N 候选 + `difflib.SequenceMatcher` 相似度阈值告警；**不整库载入内存**。
- **回流**：不合格 → 携带质检原因重生成（复用 `generate_questions`），≤2 轮；仍不合格 → 剔除并记缺口。**全部剔除 → 任务显式失败，禁止输出空试卷**。
- **降级语义**（三态）：
  - `completed`：全部质检完成；
  - `partial`：部分质检完成、后续服务异常（已完成结果保留，不因某道题异常丢弃先前结果）；
  - `skipped`：质检整体不可用（未产生有效质检结果）。
  - 确定性规则不依赖 LLM、必须尽可能独立执行；LLM 不可用时仍跑确定性规则；未完成 LLM 复核的题目**不得标记为完整通过**；降级不阻塞主流程，但产生 warning + 可追踪记录。
- **产物**：`03_质检报告.json`（任务级），登记在 task artifacts 与 citations 账本（生成题 ref 语义按 §4.4 处理）。

## 3. 质检指标（精确口径，§8.2 扩展）

| 指标 | 口径 | 说明 |
|---|---|---|
| 首轮拦截率 | 首轮判不合格题数 / 送检题数 | 主口径，对应 §8.2「拦截率」 |
| 全轮拦截率 | 全部判不合格次数 / 全部送检次数 | 辅助口径 |
| 最终剔除率 | 剔除题数 / 送检题数（附剔除数） | 剔除=2 轮回流后仍不合格 |
| 平均收敛轮次 | Σ每题送检轮次 / 题数；剔除题按 3 轮计并单列 `retired_count` | 对应 §8.2「回流收敛」 |
| 回流成功率 | 回流后最终通过题数 / 首轮不合格题数 | 用户新增 |
| 降级任务数 | critique_status ∈ {partial, skipped} 的任务数 | 不并入拦截率分母 |

- **指标真实性**：FakeLLM 与真实 DeepSeek 结果**分开统计**；无质检数据返回 `null`（不显示 0 冒充）；保留每次真实执行的原始质检报告；真实样本附样本量、模型来源、运行时间。

## 4. MCP 能力开放设计（§8.3）

- **SDK**：`mcp==2.3.0`（已装）。**关键迁移事实（S0 探针实测）**：`FastMCP` 已重命名为 `MCPServer`（`from mcp.server.mcpserver import MCPServer`）；`list_tools`/`call_tool` 为 **async**；工具参数 schema 由函数 type hint 生成（无默认值参数 → required）；`dict` 返回序列化为 JSON 文本 `content`，`structured_output=True` 需 pydantic 返回模型；`streamable_http_app()` 返回 Starlette app（挂 FastAPI）；`run(transport='stdio')` 可用。
- **三工具**（复用服务层，handler 只做参数适配）：

| 工具 | 业务服务 | 权限 |
|---|---|---|
| `search_knowledge_points` | `retrieval.service.search_knowledge_points` | 普通/管理员 |
| `query_question_bank` | 检索阶段 2/3（结构化参数，非 NL 问数） | 普通/管理员 |
| `diagnose_learning_gaps` | `diagnosis.service`（新增 class_name/exam_id/student_no 定位适配） | **仅管理员 + 开关开** |

- **权限矩阵**：

| MCP 工具 | 普通 Token | 管理员 Token |
|---|---|---|
| search_knowledge_points | ✅ | ✅ |
| query_question_bank | ✅ | ✅ |
| diagnose_learning_gaps | ❌ 权限拒绝 | ✅（且环境总开关开） |

- **安全**：普通/管理员两把 Token + 敏感工具环境总开关（默认关）；无/错 token→401；普通调敏感→明确权限拒绝；管理员+开关关→仍拒绝；复用现有 `SecurityMiddleware` 认证+限流（覆盖 `/mcp`）；日志禁记完整 Token/学生姓名/学号；诊断最小必要（只归因结论+q_id 证据，姓名脱敏）；学生诊断访问审计。
- **传输**：stdio 本地受信（**stdout 仅 JSON-RPC，日志走 stderr**）；Streamable HTTP 远程挂载 FastAPI。

## 5. 遗留修复（纳入 Phase 5 正式范围）

- **4.1** `backend/app/tools/question_tools.py`：生成 prompt 学段/角色统一为**小学数学教师**；核查相关生成约束/难度描述/上下文提示的学段遗留；补测试防回归。
- **4.2** `backend/app/agent/workflow.py::_generate_questions_llm`：显式识别 `{"error": ...}` 返回 → 结构化失败语义；题量不足明确抛错，不 KeyError、不静默产出残缺试卷；补错误返回/题量不足/正常返回三类测试。

## 6. 分步计划

| 步 | 内容 | 交付/证据 | 真实成本 |
|---|---|---|---|
| **S0 底座** | 方案落盘 + 装锁 mcp 2.3.0 + SDK 探针 + 基线确认 | PHASE5-PLAN.md、DECISIONS ㊾、requirements 锁定、`probe_mcp_sdk.py` + `mcp_sdk_probe_report.json`、447 passed | ¥0 |
| **S1 质检规则层** | `backend/app/critique/`：三维确定性规则 + 模型/schema；批内+历史查重；纯离线测试 | 单测（规则/查重/边界）；零 LLM | ¥0 |
| **S2 LLM 复核 + 回流 + 工作流** | 一题一验、≤2 轮回流、剔除/缺口、三态降级、质检报告产物；FakeLLM 支持 `submit_critique`；含 4.1/4.2 修复 | 工作流 e2e、降级/全剔除/回流/错误返回测试；fake 全量回归 | ¥0 |
| **S3 指标接入评测** | runner/aggregate/eval_api/前端评测页替换「待 Phase 5」；fake 证据；**真实数字经批准后在线 10 条** | 指标聚合测试、fake run 报告；批准后真实 run 数字（附样本量/模型/时间） | 待批（见 §7） |
| **S4 MCP** | `backend/app/mcp/`：3 工具 + stdio + HTTP 挂载 + 双 Token/开关/限流/敏感授权 | §8.3 验收清单逐项 + 补充协议级测试（见 §8） | ¥0 |
| **S5 关账** | 全量 pytest + CI 绿；STATUS/ROADMAP/DECISIONS/RESUME-AMMO 回填；commit/push | 关账表 + CI run 链接 | ¥0 |

依赖：S1→S2→S3 串行；S4 与 S1–S3 无代码依赖，可按偏好并行/后置。**各阶段独立验收，未通过当前阶段不进入下一阶段。**

## 7. 真实在线验证协议（10 条 question_gen）

1. 在线执行前，先提供：预计 LLM 调用量、Token 预算、成本上限，**未经批准不开始**。
2. 最多 10 条真实 question_gen case；每道题最多重生成 2 次。
3. 设置明确的 API 调用次数与 Token 预算上限，达到立即停止新增在线调用。
4. 已产生结果及时持久化（预算耗尽不丢已有数据）。
5. 记录：实际生成题数、首次送检数、重生成数、总送检次数、最终剔除数。
6. 真实与 FakeLLM 数据严格分离，禁止混合计算真实指标。
7. 10 条为初始验证样本，**不宣传为充分统计代表性的长期质量结论**。

## 8. MCP 补充协议级测试（§8.3 验收 + 用户增补）

无 Token 请求 / 错误 Token 请求 / 普通 Token 调普通工具 / 普通 Token 调敏感工具 / 管理员 Token+开关关 / 管理员 Token+开关开 / 超限流阈值 / 非法参数与越权参数 / 工具内部异常的结构化错误 / stdio stdout 输出污染检查（stderr 分离）。

## 9. 工程约束（不可违反）

- 不破坏 447 passed 测试基线；不修改 exam_loop 冻结题库基准行为。
- 不绕过服务层、参数白名单、安全校验；不为测试通过而削弱真实业务校验。
- 新增功能配离线单测 + 工作流集成测试 + 必要协议级测试。

## 10. 风险

- mcp 2.x 与 1.x 示例不兼容（已探针确认真实 API）；S4 严格按探针事实编码。
- 真实 10 条样本小，指标结论标注为初始验证、不泛化。
- 双 Token 需新增环境变量（`EDUMATE_MCP_ADMIN_TOKEN`、`EDUMATE_MCP_ALLOW_SENSITIVE`），文档与 .env.example 同步。
