# Phase 4 S2-B Online 冒烟测试计划（待审批，不自动执行）

> 起草：2026-11-18；状态：**待用户审批**（决策㊴：真实调用需单独审批，S2-B 阶段不执行任何真实 LLM/API 请求）。
> 依据：`docs/技术方案.md` §9.3（online 档）、`docs/development/PHASE4-PLAN.md` S2-B、决策㊲③（预算三线 ¥10/¥15/¥20）、决策㊴。
> 运行方式：`python -m backend.app.eval.evaluate --mode online --llm-mode real --allow-live-api --db <eval库> --workdir <运行目录> --run-id smoke_<date>`。
> **批准后才可执行**；任何成本上限/批次修改需再次审批。

## 1. 目的与验收

在**真实 DeepSeek 环境**下验证 Online Runner 的执行链路与成本统计：
- 复用 Fake 档判定逻辑，验证在线执行产物/成本聚合与 Fake 结构一致（execution_mode=online）；
- 真实 usage（provider）进入 cost 统计：已知/未知任务数、总额、均值、预算剩余；
- 预算三线（¥10 提醒/¥15 暂停/¥20 硬停）在实际调用下正确触发；
- 20 条 case 全部跑完、报告可读、无 infra 归因。

**验收门槛（冒烟）**：20 条全部完成；已知成本任务数 ≥90%；预算未触发硬停（若触发，如实记录并暂停等待人工）；报告可生成。

## 2. 抽样：20 条冒烟 case（从冻结 111 条中选，覆盖全部行为与工作流）

| # | case_id | 工作流/行为 | 说明 |
|---|---|---|---|
| 1 | gc_e03 | exam_loop | 标准一条龙 |
| 2 | gc_e13 | exam_loop | 多参数 |
| 3 | gc_q01 | question_gen | count=10 |
| 4 | gc_q12 | question_gen | 题型限定 |
| 5 | gc_l01 | lesson_plan | 教案 |
| 6 | gc_l16 | lesson_plan | 教学设计 |
| 7 | gc_c01 | class_diagnosis | 演示班（fixture） |
| 8 | gc_c04 | class_diagnosis·API | 202 双产物 |
| 9 | gc_e27 | exam_loop | 同义章节 |
| 10 | gc_q15 | question_gen | total_score=80 |
| 11 | gc_b13 | question_gen·冲突 | difficulty=混合 |
| 12 | gc_b19 | 注入 | trap 词不影响 |
| 13 | gc_b26 | question_gen·易误判 | 三角形面积 |
| 14 | gc_b27 | question_gen·易误判 | 《方程》放行 |
| 15 | gc_b31 | 合法边界·超长 | 长 prompt |
| 16 | gc_b18 | reject_scope | 初中注入拦截 |
| 17 | gc_b20 | reject_scope | 一元二次方程 |
| 18 | gc_b25 | reject_404 | 不存在班级 |
| 19 | gc_b30 | reject_400 | 空 prompt |
| 20 | gc_b24 | fail·fake_only | 20 题上限（**online 为观察项**，不计入成功率分母） |

> coverage：exam_loop 4 / question_gen 5 / lesson_plan 2 / class_diagnosis 2 / reject·边界 5 / fail 观察 1；行为覆盖 execute/fallback/reject/reject_*/fail。
> gc_b24 按决策㊳为 fake_only（online 观察项），选入用于验证"online 不加计成功率分母"。

## 3. 模型配置

| 项 | 值 | 依据 |
|---|---|---|
| 模型 | `deepseek-flash` | 决策④（主力）；config 默认 |
| 价格表 | `deepseek-cny-2026-11-13-v1` | 与 S4 一致（tracing 固化版本） |
| base_url / key | `settings.DEEPSEEK_BASE_URL` / `DEEPSEEK_API_KEY`（启动前确认存在，不打印） | 决策㉑ |
| 预算线 | 提醒 ¥10 / 暂停 ¥15 / 硬停 ¥20 | 决策㊲③（EvalBudget 默认） |
| 并发 | 串行（冒烟 20 条） | S2-B 不引入并发控制 |

## 4. 成本估算（预测值，冒烟后回填实测）

| 项 | 估算 | 依据 |
|---|---|---|
| 每次执行 LLM 调用数 | 2–3（planner + 主节点 + 可能 narrate） | S1/S3 实测（T5 节点） |
| 单次调用 tokens | in ~1,200 / out ~600（突发少） | Phase1 试跑实测（缓存态 in~500-1200） |
| 单条 case 成本 | ≈ ¥0.01–0.05 | deepseek-flash 缓存态：in_hit ¥0.04/M + out ¥8/M |
| **20 条合计（乐观）** | **≈ ¥0.3–1.0**（缓存命中率高） | — |
| **20 条合计（无缓存最坏）** | **≈ ¥1.5–3.0** | 无缓存高峰口径，预算保护按此预留 |
| 预算触发风险 | 极低（≤ ¥3 ≪ ¥10 提醒线） | 冒烟目的=验证链路与成本，非预算压力测试 |

> 说明：预算三线针对**全量评测**（111 条 online）设计的保护；冒烟 20 条远低于提醒线，主要用于验证「成本统计正确 + 三线机制接线正确」，不期望触发暂停/硬停。

## 5. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 真实调用失败（超时/网络/429） | 中 | 个别 case 归因 infra/失败 | 冒烟**逐条独立**记录；失败 case 单列，不中断 20 条；报告如实 |
| 成本未知（供应商未返回 usage） | 低 | cost_known=False，不视 0 | 预算按最坏预留；报告标注「未知成本任务 N 个」 |
| 触发预算暂停/硬停 | 极低（¥3≪¥10） | 中途停止 | 若触发，报告暂停说明，等待人工确认后决定是否继续 |
| 前端/API 未完成（无关本阶段） | — | — | S2-B 只做执行层，不涉及 S4 前端 |
| Golděn 数据被意外修改 | — | 致命 | **禁止触碰 frozen JSON / eval_cases**；只读加载（校验 sha256 `268cd828a12efe42…`） |
| DEEPSEEK_API_KEY 缺失 | 中 | 无法执行 | 启动前 fail-fast 检查，不打印 key |

## 6. 审批通过后的执行步骤（user 批准后再执行）

1. 确认 DEEPSEEK_API_KEY 存在（不打印）；确认冻结 golden sha256 不变。
2. `python -m backend.app.eval.evaluate --mode online --llm-mode real --allow-live-api --db <eval库> --workdir <运行目录> --run-id smoke_<date>`。
3. 读取报告：20 条 case、已知/未知成本任务数、总成本、预算状态。
4. 对照本计划 §1 验收门槛逐项核验。
5. 将冒烟结论回填 STATUS/PHASE4-PLAN；评估是否进入全量 online 评测（届时另行审批）。

## 7. 边界声明（本文件不构成执行授权）

- 本文档**仅在用户审批后**生效；审批范围 = 上述 20 条冒烟、单次运行、预算 ¥10/¥15/¥20。
- 未经审批：**禁止**发起任何真实 LLM/API 调用、禁止全量 111 条 online、禁止修改冻结 Golden、禁止进入 S3。
- 审批通过前，所有 Online 相关测试一律使用 MockLLM（零真实费用）。