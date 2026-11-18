# Phase 4 执行计划：评测体系（技术方案 §9）

> 制定：2026-11-18；状态：**用户已批准（决策㊲㊳）**，S0+S1 完成（golden 111 条冻结入库），S2 runner 待启动。
> 依据：`docs/技术方案.md` §9（评测体系）、§12（Phase 4 里程碑）、`ROADMAP.md` Phase 4 行、`DECISIONS.md` ㊱（Phase 3 关账）。
> 本文是 Phase 4 的实施基准；范围或顺序调整须回写本文件并在 DECISIONS.md 记决策。

## 0. 范围与现状盘点

### 0.1 §9 要求 → 现状适配

| §9 条目 | 要求 | Phase 4 实施口径（适配决策㉝ 小学单学段） |
|---|---|---|
| 9.1 指标 | 任务成功率 / 参数字段准确率 / recall@k·MRR / judge 五维 / **judge 校准一致率 ≥80% 或回退规则并记录** / p95 延迟 / 单任务成本 / 质检拦截率 / 归因一致率 | 新增任务级指标（成功率/参数准确率/p95/成本/judge/校准）；recall@k·MRR 与归因一致率**复用既有评测资产**（不重构、不改冻结基线）；质检拦截率为 Phase 5 指标，Phase 4 只建框架位并明文标注「待 Phase 5 采集」，**不编数字** |
| 9.2 golden set | ≥100 条、边界 ≥20%、格式 `{case_id, prompt, expect{workflow, params, artifacts_min}, tags}` | golden 全部改用**小学章节**（82 个小学 KP）；§9.2 原文示例「一元二次方程」为旧初中示例，以本文件与 golden 文件为准，评审时同步加注 |
| 9.3 执行方式 | `fake`（FakeLLM，CI 20 条 smoke ＜2min）/ `online`（真实 DeepSeek，100+ 条，落 `eval_runs`）；runner 幂等 | `eval_cases` / `eval_runs` 表已在 V1 schema 建好且真库为空（0 行），**无需建表**；online 档沿用决策㉖ 预算三线，全量前用户逐次批准 |
| 9.4 报告与 CI | `GET /api/eval/report`（baseline→current、近 5 次 run）；GitHub Actions push → pytest 全量 + smoke eval | 仓库当前无 `.github/`，CI 从零建；**CI runner 为 Linux**，需验证全量 pytest 跨平台通过 |

### 0.2 既有评测资产（Phase 2/3 已产出，Phase 4 只聚合展示）

| 指标 | 资产 | 已有数字（冻结） |
|---|---|---|
| 检索 recall@k / MRR | `backend/scripts/eval_retrieval_baseline.py`（小学 49 条正式集 v2） | R@1 32.65% / R@5 55.10% / MRR 0.4335 / NDCG@5 0.4201（Phase 2.2 重基线） |
| 归因一致率 | `backend/scripts/eval_diagnosis.py` + `data/gt/diagnosis_gt_gold.json`（10 例 GT） | 10/10 = 100%（仅冻结案例上的算法一致性，非真实教学准确率） |

→ Phase 4 的**新增面**是任务级评测（planner 参数准确率、任务成功率、p95、成本、judge 五维与校准）+ 统一报告 API/页面 + CI；既有两项数字只做展示，不重跑覆盖。

### 0.3 工程事实（已核实）

- `eval_cases(case_id PK, prompt, expect JSON, tags JSON)` / `eval_runs(run_id, case_id, mode, passed, metrics JSON, detail, created_at)` 已在 `migrations.sql` V1 建表；真库 `schema_migrations=[1..5]`，两表均存在、0 行。
- `security.py::classify_limit` 已含 `/api/eval → 60/min`，认证中间件已覆盖。
- `main.py` 现挂载 health/chat/tasks/import/sessions/diagnosis 六个路由，**无 eval 路由**。
- 前端导航现有：工作台/智能任务/学情分析/数据导入/教学方案/Agent 协作/任务记录/运行观测/设置。
- FakeLLM 注入模式已有成熟范例：`backend/tests/test_workflow.py`（monkeypatch `llm_utils.get_llm_service`，返回预设 `LLMResponse`）。
- 工作流：`exam_loop`（8 节点）/ `question_gen`（4 节点）/ `lesson_plan`（3 节点）/ `class_diagnosis`（4 节点，`skip_plan=True` 直接携带 `class_id`）。
- Planner 参数字段集：`subject/chapter/count/difficulty/question_types/total_score/topic`；`workflow ∈ {exam_loop, question_gen, lesson_plan}`。

## 1. 目标与验收标准

**目标**：建立可复现、可自动化、可展示的评测体系——golden set 冻结 → runner 两档执行 → 全指标 baseline→current 有数字 → eval 报告页 → CI 绿灯。

**验收（对应 ROADMAP Phase 4）**：
1. golden ≥100 条冻结（入 Git + 真库 `eval_cases`），边界用例 ≥20%；
2. `fake` 档 CI 绿：pytest 全量 + smoke eval 20 条 ＜2 分钟；
3. `GET /api/eval/report` + 前端「评测中心」页可看任务级 5 项指标、既有 2 项指标、质检 1 项「待 Phase 5」；
4. judge 校准：用户人工 50 条评分 → 一致率 ≥80% 采信 judge 全量；否则回退规则评分并记录（两条路径均有徽标）；
5. online 档每次运行有成本台账，全量前经用户逐次批准；
6. 全量 pytest 基线不降低（当前 401 passed，Phase 4 结束时应 ≥ 401 + 新增）。

## 2. 分步计划

### S0 开工准备（零业务代码）
1. 确认 Phase 3 统一 push 完成（`origin/main = a47c472`，"update by push" 于 2026-11-17 23:09）；无需重推。
2. 落盘本文件 + `DECISIONS.md` 决策㊲（范围与五项裁决）。
3. 全量 pytest 基线复跑确认（401 passed）。
4. 更新 STATUS.md / ROADMAP.md Phase 4 状态为进行中。

### S1 golden set 起草与冻结（2026-11-18 ✅ 完成，决策㊳）
- **111 条冻结**（core 80 + boundary 31，28%）：`data/gt/golden_set_v1.jsonl`（Git，full sha256 `268cd828a12efe42f226c88d96d264ee2bdc89fc6fa96cc7b30d5feb75e532b4`）。
- 章节核对：真实 82 个小学 KP 逐一核对，76 处参与比对章节全部有效（0 错误）。
- 边界裁决落实：clarify/reject/reject_scope fail+等行为语义见设计规范 §8；b23 为 known_defect 只记录不改代码。
- 入库：`eval_cases`（UPSERT 幂等，111/111 唯一，逐条回读一致）。
- 设计规范：`docs/development/PHASE4-GOLDEN-DESIGN.md`（§8 测评口径已写）。
- **限制写入规范**：exam_loop 的 count/difficulty 只验证 Planner 参数抽取，不代表实际试卷生成受其控制。

### S2-A Fake Runner（2026-11-18 ✅ 完成，决策㊴）
- **交付**：`backend/app/eval/`（fake_llm / models / artifacts / golden / runner / aggregate / evaluate / db_fixture）+ `migrations_v6.sql`（eval_runs 复合主键 (run_id, case_id)）。
- **全量 111 条跑通（11.5s，零 API 费用）**：任务成功率 **100%**（93/93 execute/fallback）、Planner 字段准确率 **95.87%**、Case 完全匹配率 94.62%、**行为正确率 90.09%**。
- **如实暴露系统短板（核心价值）**：正确追问率 **0%**（0/9，系统缺 F1 追问能力）、正确拒绝率 **66.67%**（4/6，b16 reject 未实现 + b23 高中 known_defect 未拦截）、错误执行率 64.71%（本应 clarify/reject 却 execute）、HTTP 边界正确率 100%、预期 fail 率 100%（gc_b24 确定性失败）。
- **独立性与可重复性（要求 1/5）**：FakeLLM 复刻当前实现（不读 golden，`contacted_golden=False`）；同 seed 两跑逐判定一致（排除 latency_ms）；单 case 畸形不中断（dispatch 兜底 infra）；固定 run-id 重跑 UPSERT 幂等（111 行/111 唯一）；gc_c01–c04 走独立 fixture（82 KP/87 边/4 题/2 学生/8 答题，不依赖开发者本机库）。
- **产物三层校验**：存在性 + 有效性（docx/xlsx/pdf/json 可解析非空）+ 代表 case 内容断言。
- **失败归因 7 类**：reports 每 case 含 error_type（ok/workflow_choice/api_behavior/workflow_failed/artifact_*/infra 等）+ 全字段记录。
- 测试：`test_eval_fake_runner.py`（6 项）；全量 **407 passed**（基线 401 + V6 版本断言 + eval 6）。
- detection 生效在 `python -m backend.app.eval.evaluate --mode fake [--db 独立库] [--run-id X]`。

### S2-B Online Runner 适配 + 成本统计 + 预算保护（2026-11-18 ✅ 实现完成，**真实 LLM 未批准不执行**）
- **交付**：
  - `provider.py`：Online 适配（install_online_env）+ MockLLM（模拟供应商带 usage 上报，走 tracing 真实上报路径）+ `aggregate_cost_from_ctx`（从 steps 聚合成ig日成本）；
  - `budget.py`：EvalBudget——¥10 提醒 / ¥15 暂停 / ¥20 硬停三线，执行前 try_reserve 预留（防并发/重试/未知绕过），未知成本按最坏口径入账**不视 0**，暂停后 `allow_resume()` 人工确认恢复；
  - `evaluate.py`：`--llm-mode fake/mock/real` + `--allow-live-api` 安全闸门（real 未批准即拒绝，`exit 2`）；
  - `docs/development/PHASE4-ONLINE-SMOKE-PLAN.md`：待审批的 20 条冒烟计划（模型配置/成本估算/风险/边界）。
- **验证（全部 Mock，零真实 API）**：
  - mock online 全量 111 条：指标与 fake 完全一致（任务成功率 100%、行为 90.09%、预期 fail 100%）；成本统计 111 任务全已知、总额 ¥0.583 模拟、均值 ¥0.0053/任务；预算快照落盘（spent_total/remaining/calls_known/calls_unknown/trip_point）；
  - 预算保护单测 **11 项**（提醒/暂停/恢复/硬停/并发不绕过/未知不视 0/异常不泄漏）+ 端到端：初始 ¥14.97 → 第 6 个 case 累计越过 ¥15 暂停线被拦截（trip=pause）；
  - 未知成本:cost_known=False、tokens NULL 不视 0（单测 + 集成）。
- **约束遵守**：未发起任何真实 LLM/API 请求、未启动冒烟/全量在线、未改冻结 Golden、未修复 Golden 记录的业务缺陷；S3 未启动。
- 测试：`test_eval_budget.py`（11 项）；全量 pytest **417 passed**（407 + budget 11 - 1 合并）。
- **待审批**：`PHASE4-ONLINE-SMOKE-PLAN.md` 的 20 条冒烟需用户单独审批后执行。

### S2-Cbis + 真实冒烟 + S2 关账（2026-11-18 ✅，决策㊶）
- 超支保护核查 5 项测试（预留0.10/结算0.30 全额入账、超支即硬停阻断后续、并发不绕过、未知不视0、release 幂等）；预算定性=**预估软保护+结算后硬停**。
- `--cases` 子集入口（20 条精确选中，mock 预演验证）。
- **真实冒烟 `smoke_real_01`（用户批准执行）**：20 条全完成、273.5s、**¥0.210568**、20/20 cost_known、预算未触发、逐条 err=ok、行为正确率 100%。
- **真实发现**：gc_e03 diagnose 节点真实 LLM 3 次未返回合法 schema → P2-8 降级兜底（PDF 含「暂不可用」）；评测如实记录（非评测 bug）；处置路线=S3 规则评分加 degraded 检测 → 全量 online 量化 → 业务修复待用户决策。
- 全量 pytest **429 passed**。**S2 关账**。

### S3 judge 五维 + 校准（LLM-as-judge，✅ 完成 2026-11-18，决策㊷㊹）
1. **S3-A（零真实调用 ✅）**：judge.py 五维+规则兜底+一致率；校准集 50 条冻结；评分表；测试 14 项。
2. **S3-B（✅）**：真实 50 条执行（¥0.6614，6/20 exam_loop 降级=30%）；用户人工评分 250/250 有效（Excel，硬规则无违规）。
3. **S3-C（✅ 三轮迭代，未达标回退）**：judge v1 46.4% → v2 61.6% → v3 64.0%（覆盖 78%/引用 42%）；**judge 未采信（<80%）→ 回退规则评分（44.8%）并记录**（决策㊲④⑤）；judge 成本合计 ¥1.338；口径边界如实记录。
4. 测试：judge 解析/失败兜底/规则评分/校准统计/一致性计算（已完成）；全量 443 passed。

### S4 eval API + 前端评测页（✅ 2026-11-18 实现完成，待验收，决策㊻）
1. `GET /api/eval/report`（最新 5 次 run 聚合 + 冻结基线 + judge 校准状态 + 质检待 Phase 5）；
2. 前端「评测中心」页（导航 + 指标卡 + run 列表 + baseline 对比 + judge 回退徽标）；
3. V7 迁移修正真库 eval_runs 复合主键；导入真实 runs（smoke_real_01 / s3b_calib_real_01）；
4. 冒烟：uvicorn 200/页面含评测中心/无 token 401；测试 4 项，全量 447 passed。

### S5 CI
1. `.github/workflows/ci.yml`：push → Python 3.13 + Ubuntu → 装依赖 → pytest 全量 + fake smoke eval 20 条；失败即红。
2. Linux 兼容验证（路径/编码/平台差异）；个别 Windows 耦合测试用 skipif 处理而非改逻辑。
3. smoke ＜2 分钟。

### S6 验收与关账
1. 全指标 baseline→current 有数字（检索/归因复用既有 + 任务级新跑 fake/授权 online）。
2. 关账：STATUS / DECISIONS（终裁）/ ROADMAP（Phase 4 ✅）/ 技术方案 §9 实测回填 / RESUME-AMMO 回填「评测 baseline」缺口。
3. 统一 push。

## 3. 用户已裁决事项（2026-11-18，决策㊲）

1. **Phase 3 push**：开工即统一 push —— 核实已完成（`a47c472` 已于 2026-11-17 23:09 push），无需重推。
2. **golden 构成**：core ≈80 + boundary ≈20（四工作流权重 exam_loop 35 / question_gen 20 / lesson_plan 20 / class_diagnosis 5，边界 20 条跨工作流）。
3. **online 预算线**：¥10 提醒 / ¥15 暂停 / ¥20 硬停（沿用决策㉖）；先 20 条冒烟确认单条成本，全量前逐次批准。
4. **judge 人工校准**：50 条人工评分由用户参与（Gold 归用户惯例）。
5. **质检拦截率**：Phase 4 只建框架位，页面明文「待 Phase 5 采集」，**不编数字**。

## 4. 指标矩阵（§9.1 逐项落位）

| 指标 | Phase 4 来源 | 执行档 | 现状 |
|---|---|---|---|
| 任务成功率 | runner（新增） | fake + online | 待建 |
| 参数字段准确率 | runner（新增，chapter 同义白名单） | fake + online | 待建 |
| recall@k / MRR | 既有 `eval_retrieval_baseline.py` | 本地（零 API） | 已有数字，只聚合展示 |
| judge 五维 | `judge.py`（新增） | online（真实 LLM） | 待建 |
| judge 校准一致率 | 用户 50 条人工评分 vs judge | 半自动 | 待建 |
| p95 延迟 | tasks/steps 时间戳（新增聚合） | fake + online | 待建 |
| 单任务成本 | Σ steps.cost（S4 已就绪数据） | online 为主；fake 为 0/NULL | 数据源已就绪 |
| 质检拦截率 / 回流轮次 | §8.2（Phase 5） | — | **待 Phase 5，不编数字** |
| 归因一致率 | 既有 `eval_diagnosis.py`（10 例 GT） | 本地（零 API） | 已有 10/10，只聚合展示 |

## 5. 成本与预算
- fake 档：零 API 成本。
- online 档：100+ 条 × 每任务 2–3 次 LLM 调用，预算三线 ¥10/¥15/¥20（决策㉖）；先 20 条冒烟实测单条成本 → 外推全量 → 用户批准后执行。
- judge：online 档每条 1 次判分调用，计入同一台账。
- 任何真实 API 调用前确认 Key 存在（不打印内容）。

## 6. 风险与边界（如实记录）
1. `planner.fallback_plan` 默认初中章节（一元二次方程）与小学运行时不一致——golden 边界用例可能暴露；按决策流程处理，不擅自改。
2. CI 首次上线可能需要按 Linux 实况修补（个别测试平台耦合）。
3. §9.2 的旧初中示例 prompt 不适用于当前运行时；以本文件与 golden 文件的小学口径为准。
4. 质检拦截率本阶段无数字（Phase 5 才有），报告页与文档均如实标注。
5. judge 校准若 <80% 触发规则回退——回退后指标口径降级的事实必须写入报告，不掩饰。

## 7. 复现方法（逐步回填）
- 全量测试：`.venv/Scripts/python.exe -m pytest`（Windows 开发机；基线 401 passed）
- 检索基线：`python -m backend.scripts.eval_retrieval_baseline --formal-file data/staging/retrieval_eval_queries_formal_primary_v2.jsonl ...`
- 归因一致率：`python -m backend.scripts.eval_diagnosis`
- Phase 4 新增 runner / judge / CI 命令：S2–S5 实施后回填本节。