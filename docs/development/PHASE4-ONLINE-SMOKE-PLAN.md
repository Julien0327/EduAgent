# Phase 4 S2-B Online 冒烟测试计划（待审批，不自动执行）

> 起草：2026-11-18；**修订：2026-11-18（S2-B 评审意见落地）**；状态：**待用户审批**。
> 依据：`docs/技术方案.md` §9.3（online 档）、`PHASE4-PLAN.md` S2-B、决策㊲③（全局预算 ¥10/¥15/¥20）、决策㊴⑤（**单次冒烟另设更严预算 ¥3 预警 / ¥5 硬停**）、决策㊵。
> 运行方式：`python -m backend.app.eval.evaluate --mode online --llm-mode real --allow-live-api --db <eval库> --workdir <运行目录> --run-id smoke_<date>`。
> **本文档不构成执行授权**：任何真实 LLM/API 调用必须在用户另行批准后执行；文档修订本身不代表批准。

## 0. 范围与边界（评审修订）

1. **真实在线执行仅 20 条**，<u>不含 `gc_b24`</u>：`gc_b24`（数据不足，请求 20 题上限）为 **Fake-only 观察项**（决策㊳），真实 DeepSeek 不参与其确定性失败/成功判定分母；已从 20 条清单移除。
2. **gc_b16（reject）、gc_b23（reject_scope·高中）为已知业务缺陷（决策㊲/㊴记录），本次冒烟不验证其业务修复**——它们不在 20 条里（见 §2 说明），真实输入不会触发修复验证，仅作为已知缺口留待后续治理。
3. **fallback 覆盖**：冻结 Golden 中 `behavior=fallback` 的 case 均因 planner 回退产生（无真实 LLM 路径可离线验证）；在线冒烟**不选取 fallback case**（见 §2 覆盖说明），不虚报 fallback 在线覆盖。
4. 本计划文档修订**不包含任何真实调用**；修订完成即回到待审批状态。

## 1. 目的与验收（评审修订）

**真实冒烟的预算目标修正（评审 #7）**：不计成本预算压力测试——聚焦验证：
- 真实 usage 统计（provider 上报 → gather → cost 聚合闭环与 Mock 验证一致）；
- 预算预留与结算（每 call 前 try_reserve、结算后 cost/remaining 更新）；
- `cost_known` 语义在真实环境（供应商返回 full/partial/none usage）下的表现。

**验收门槛（冒烟）**：
- 20 条全部完成且无一 infra 归因（可有个别真实失败/unknown，属如实记录，非冒烟失败）；
- 已知成本任务数 ≥ **90%**（18/20，容忍个别 usage 缺失）；
- 预算未触发**单次冒烟硬停 ¥5**（若触发，如实记录并停止，等人工确认）；
- 报告（report.json）含：逐 case 成本、预算快照、cost_known/cost_unknown 统计。

## 2. 20 条冒烟 case 清单与覆盖（评审：#1 移除 b24 后补足）

> 说明：已从清单移除 `gc_b24`（Fake-only）；新增 `gc_l04`（lesson_plan·圆的面积，非 fake_only）补足 20 条。
> **不覆盖 fallback**：Golden 中 5 个 fallback case（gc_b01–b06 中 fallback 语义）均由 planner 确定性回退产生，
> 在线冒烟无独立真实调用价值，且其预期行为（追问/拒绝）属缺陷项——**不虚报 fallback 在线覆盖**（评审 #2）。
> 明确不在本次清单：gc_b16（reject 缺陷）、gc_b23（高中 reject_scope 缺陷）——**不验证其业务修复**（评审 #3）。

| # | case_id | 工作流/行为 | fake_only? | 说明 |
|---|---|---|---|---|
| 1 | gc_e03 | exam_loop | 否 | 标准一条龙（count=10） |
| 2 | gc_e13 | exam_loop | 否 | 多参数（count=10/简单/100） |
| 3 | gc_e15 | exam_loop | 否 | count=15/混合 |
| 4 | gc_e27 | exam_loop | 否 | 同义章节《分数加减法》 |
| 5 | gc_e31 | exam_loop | 否 | 同义章节·比例分配 |
| 6 | gc_q01 | question_gen | 否 | count=10 |
| 7 | gc_q07 | question_gen | 否 | count=12 |
| 8 | gc_q12 | question_gen | 否 | 题型限定（选择+填空） |
| 9 | gc_q15 | question_gen | 否 | total_score=80 |
| 10 | gc_l01 | lesson_plan | 否 | 教案（分数运算） |
| 11 | gc_l04 | lesson_plan | 否 | 教学设计（圆的面积）**（替换 b24 新增）** |
| 12 | gc_l16 | lesson_plan | 否 | 教学设计（比与按比例分配） |
| 13 | gc_c01 | class_diagnosis | 否 | fixture 演示班（确定性，无 LLM cost） |
| 14 | gc_c04 | class_diagnosis·API | 否 | 202 双产物（确定性，无 LLM cost） |
| 15 | gc_b13 | question_gen·参数冲突 | 否 | difficulty=混合精确 |
| 16 | gc_b19 | 注入·透传 | 否 | trap 词不影响任务 |
| 17 | gc_b26 | question_gen·易误判 | 否 | 《三角形面积》放行 |
| 18 | gc_b18 | reject_scope | 否 | 初中注入→**应被 scope 拦截（零 LLM 调用）** |
| 19 | gc_b20 | reject_scope | 否 | 一元二次方程→**应被拦截（零 LLM 调用）** |
| 20 | gc_b25 | reject_404 | 否 | 不存在班级→404 语义（零 LLM 调用） |

**覆盖汇总**：exam_loop 5 / question_gen 5 / lesson_plan 3 / class_diagnosis 2 / API 边界(reject_scope/404) 3；行为：execute 17 / reject_scope 2 / reject_404 1。
**不覆盖**：fallback（0，在线无独立价值，见上）、gc_b24（Fake-only）、gc_b16/gc_b23（缺陷项不验证修复）。

## 3. 模型与价格配置（评审：#4 逐一核对源码/同目录，非猜测）

| 项 | 值 | 依据（代码） |
|---|---|---|
| 模型 ID | **`deepseek-flash`** | `config.py:40 DEEPSEEK_MODEL default="deepseek-flash"`（env 无覆盖） |
| Base URL | `https://api.deepseek.com` | `config.py:37` |
| 超时 | 60s | `config.py:41 LLM_TIMEOUT default="60"` |
| API Key | `DEEPSEEK_API_KEY` env | `config.py:36`；启动前 fail-fast（不打印） |
| 价格表版本 | `deepseek-cny-2026-11-13-v1` | `pricing.py PRICE_TABLE_VERSION` |
| 价格表（元/百万 tokens） | peak：in_hit **¥0.04** / in_miss **¥2.0** / out **¥8.0**；idle=半价 | `pricing.py PRICE_TABLE deepseek-flash`（2026-11-13 核实） |
| 时段判定 | 北京时间工作日 9-12/14-18 = peak，其余 idle | `pricing.py period_at` |
| 单次调用 token 上限 | 由供应商决定（**无本地硬上限**，代码未设 max_tokens/上下文限制；DeepSeek flash 上下文上限以供应商为准） | `llm_client._request`、`llm_utils.structured_completion` 均无 max_tokens 限制 → **标记为待核实项**（见 §4） |
| 每 case 调用次数上限 | planner 1 次 + 主节点 ≤2 次（exam_loop 的 diagnose 1 次 / question_gen 的 generate_questions 1 次 / lesson_plan 1 次 / narrative 0–1 次）→ **≤4 次/ case** | workflow 节点结构 |
| 传输层重试 | 2 次（指数退避 1s/2s） | `llm_utils._call_with_retry retries=2` |
| 结构化校验重试 | 3 次（schema 不符回喂） | `llm_utils.structured_completion max_attempts=3` |
| 计费规则 | cost = hit×0.04 + miss×2.0 + out×8.0（元/百万，peak）；idle 减半 | `pricing.cost_cny` |
| 预算保护 | **单次冒烟降低**：预警 ¥3 / 硬停 ¥5；仍受全局 ¥10 提醒/¥15 暂停/¥20 硬停约束 | 本计划 §5 + `budget.EvalBudget` |

## 4. 成本估算（评审：#5 以实测数据重算）

**关键实测参考**（S4 真实 provider 验证，`data/staging/trace_real_check_report.md`）：
- 一次 narrate 调用：in=712 / out=1346 / cache_hit=512 → **peak 成本 ¥0.011188**；
- 单 case 通常 1–4 次 LLM 调用（planner 必然 + 各主节点）。

**单 case 预估**（按实测 tokens 外推 + 峰值价上限）：

| 所用 | 单次调用 tokens（in/out） | 单价（peak, 元/M） | 单次成本 |
|---|---|---|---|
| 乐观（缓存命中高） | 712/1346，hit 512 | 0.04/8 | ≈ (512×0.04 + 200×2 + 1346×8)/1e6 ≈ **¥0.0112** |
| 保守（无缓存） | 712/1346 | 2.0/8 | ≈ (712×2 + 1346×8)/1e6 ≈ **¥0.0122** |
| 最坏（单调峰值 + 全重试） | ×(2 传输重试 + 3 结构化尝试上限) | 同上 | 单 case 上限 ≈ **¥0.06–0.10** |

**20 条估算**：
- 乐观：17 个有 LLM 的 case × ¥0.0112 ≈ **¥0.19**；
- 保守：17 × ¥0.0122 ≈ **¥0.21**；
- 最坏（每 case 全上限重试，17×¥0.10 + 3 零调用）≈ **¥1.7**；
- **仍远低于单次冒烟硬停 ¥5 / 全局提醒 ¥10** → 冒烟预算目标=验证成本链路，不触发预算线。

> **单次调用 token 上限为待核实项**：代码未设 max_tokens/上下文限制，真实 DeepSeek flash 请求 token 上限由供应商 API 决定；冒烟启动前核实 README/API 文档，若超出则以实际报错如实记录（不影响 20 条可见性）。

## 5. 预算保护口径（评审：#6）

**双级预算（S2-C 已实现，BudgetStack 取最严格）**：
- **单次冒烟**：`evaluate.py` 支持 `--budget-warn 3 --budget-cap 5`（默认即 ¥3 预警 / ¥5 硬停）→ `EvalBudget(cap=5, warn=3, pause=5)`（pause=cap 等值，硬停即止）；
- **全局**：evaluate 同时构造 `EvalBudget(cap=20, warn=10, pause=15)`，与单次预算经 `BudgetStack` 成组，**严格时取先触发者**（单次硬停 ¥5 先于全局 ¥20 生效）；
- **预算目标（评审 #7）**：真实冒烟只验证 usage 统计、预留、结算的数字正确性；**三线触发/暂停恢复/并发不绕过/未知成本等阈值行为均以 Mock 测试（`test_eval_budget.py` 17 项）验证**，不在真实冒烟中触发验证。
- 预留：每 case 执行前 `try_reserve(case级最坏)`（覆盖 max LLM 节点 × 重试上限，详见 §4bis）；未知成本按最坏入账（不视 0）；
- 结算：实际 usage 回填 cost；cost_known=False 的记录为 unknown；
- 触发即停：任一预算 pause/硬停 → 该 case `budget_stopped`，后续 case 不再执行，报告明示（含 `most_restrictive`）。

### 4bis 单 case 最大 LLM 调用次数与预留覆盖（S2-C 核实，要求 4）
- 单节点结构化输出最多 LLM 调用：`max_attempts=3`（校验重试）× `(retries=2 + 1 主调用)` = **9 次**（失败路径；成功路径恒 1 次）；
- 各工作流 LLM 节点数：exam_loop/question_gen/lesson_plan 均 **2**（planner = understand_task + 主节点），class_diagnosis **1**（narrate）；
- **单 case 最大调用次数上界 = 4 节点 × 9 = 36**（保守取 4 覆盖三主工作流 + 预留重试递增）；
- 预算预留 = `case 级最坏 = 4 节点 × 9 重试 × 单次最坏 token`，`commit` 后整体冲抵（预留覆盖重试不泄漏），异常路径 `release_reservation` 兜底。

## 6. 风险与缓解（评审修订后）

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 单次调用 token 上限未知 | 中 | 个别超长调用被供应商截断/报错 | 标记待核实（§4）；启动前查 API 文档；按确定性节点（class_diagnosis 零 LLM）控制调用量 |
| 真实调用失败（超时/429/网络） | 中 | 个别 case 归因真实失败/unknown | 逐条独立记录不中断；已知成本 ≥90% 即达标 |
| usage 缺失（供应商未返回） | 低 | cost_known=False | 预算按最坏预留；报告标注「未知成本任务 N 个」 |
| 触发单次冒烟硬停 ¥5 | 极低（最坏估算 ¥1.7） | 中途停止 | 若触发，如实报告并等待人工确认后决定是否继续 |
| DEEPSEEK_API_KEY 缺失 | 中 | 无法执行 | 启动前 fail-fast，不打印 key |
| 冻结 Golden 被意外修改 | 致命 | 数据污染 | 只读加载；启动前校验 sha256 `268cd828a12efe42…` |
| gc_b16/b23 缺陷 | — | 本次不验证 | 已在 §0/§2 明示不纳入清单 |

## 7. 审批通过后的执行步骤

1. 启动前：确认 DEEPSEEK_API_KEY（不打印）；确认冻结 golden sha256 不变；**检查 DeepSeek flash 单次请求 token 上限（若可查）**。
2. **单次冒烟预算（¥3 预警 / ¥5 硬停）已实现**：`evaluate.py` 提供 `--budget-warn 3 --budget-cap 5`（默认值即 3/5），与全局 ¥10/¥15/¥20 经 `BudgetStack` 同时生效、取最严格（S2-C）。冒烟直接使用默认即可。
3. `python -m backend.app.eval.evaluate --mode online --llm-mode real --allow-live-api --db <eval库> --workdir <运行目录> --run-id smoke_<date>`。
4. 读取报告：20 条完成度、已知/未知成本任务数、总成本、预算快照（含 `most_restrictive`）、逐 case cost_known/usage。
5. 对照 §1 验收门槛逐项核验；按需回填实测 token/成本到本计划 §4。
6. 结果反馈后评估：是否进入全量 online / 缺陷修复（gc_b16/b23）/ S3 judge——均需另行决策。

## 8. 边界声明（再次强调）

- 本文档**仅在用户另行批准后**生效；审批范围 = 上述 20 条、单次运行、¥3/¥5 + 全局 ¥10/¥15/¥20。
- 未经审批：**禁止**发起任何真实 LLM/API 调用、禁止全量 111 条 online、禁止修改冻结 Golden、禁止进入 S3。
- 审批通过前，所有 Online 相关验证一律使用 MockLLM（零真实费用）。