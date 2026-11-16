# Phase 3 执行计划（归因 + 观测）

> 状态：**计划待用户确认，未动手**。基准：技术方案 §5/§6、决策㉝（小学单学段）、S0 收尾验收（342 passed）。
> 用户五条强调全部纳入：①掌握度确定性计算、LLM 只解释；②主/辅 KP 区别处理且权重有依据；③四种状态；④推荐用真实题库；⑤Trace/成本真实，不可用就标记。

## 0. 现状基础（实测）

| 项 | 状态 |
|---|---|
| 图谱 | 82 小学 KP / 87 前置边（DAG，5 孤立节点） |
| 题库 | 207,858 processed 小学题；`search_questions` 支持 difficulty_min/max、target_difficulty、processed-only |
| 导入演示班 | `cls_3b71d97847`：2 考试 / 49 题 / 20 学生 / 980 答题；主/辅角色在 `data/annotations/primary_demo_mapping.jsonl` |
| 掌握度口径（已定） | 卷面分值加权；近期考试 2、其余 1；样本=不同 q_id 数，<3 insufficient；<0.6 薄弱；前置 <0.7 失败 |
| LLM usage | `annotation/client.py` 已解析真实 usage（prompt/completion/cache_hit）；**agent `llm_client.py` 只记日志未回传**（S4 补） |
| steps 表 | 有 tokens_in/out/cost/latency_ms 列但从未写入；**无 model/usage_source 列**（S4 V5 迁移） |
| 种子 exam_loop | 已切小学（7 KP/10 题/20 学生），属 dev/demo 路径；正式诊断只读导入数据（§5.1） |

**重要口径变化预告**：S0 设计/核验报告的掌握度是“主+辅等权”的历史参考值。Phase 3 引入角色权重后，演示班掌握度数值会变化（含 077 近期翻转是否仍成立）。S1 完成后重算并**如实报告与设计值的差异**，不回调数据凑旧结果；设计报告保留为历史参考。

## 1. 步骤

### S1 诊断数据层 + 确定性掌握度（§5.1，约 1.5 天）

新增 `backend/app/diagnosis/`：

- `data_access.py`：只读 classes/exams/exam_questions/student_answers；产出逐条证据行 `{exam_id, exam_date, q_id, q_no, kp_id, role(primary/aux), student_id, score, full_score, is_correct}`；`data_scope`（考试数/题目数/学生数/答题数/日期范围）。**无导入数据 → 拒绝正式结论**（422 +“请先导入班级数据”），不读种子、不推测。
- `mastery.py`（纯 Python，零 LLM）：
  - 单题对错 `c = is_correct`（非空优先）否则 `score/full_score`；
  - 每 (考试,题) 班级平均得分率 `r`；
  - **KP 掌握度 = Σ(r × 卷面分值 × 近期权重 × 角色权重) / Σ(卷面分值 × 近期权重 × 角色权重)**；
  - 近期权重：按 exam_date（缺失用 created_at）最新一场 =2，其余 =1；
  - **角色权重：primary=1.0，aux=λ（建议 0.5，见 D1）**；
  - 同时输出 `primary_only_mastery`（仅主 KP 证据）与 `aux_evidence_share`（辅助证据权重占比），供透明化与置信度使用；
  - 每个掌握度值可分解回证据行（q_id/role/得分），前端可展开。
- **四种状态**（D2）：
  | 状态 | 判定 |
  |---|---|
  | `no_coverage` | 该班考试题目中没有任何题映射到该 KP |
  | `insufficient` | 有映射题但不同 q_id 数 <3 |
  | `weak` | 样本足够且掌握度 <0.6 |
  | `normal` | 样本足够且掌握度 ≥0.6 |
- λ 敏感性报告：对演示班计算 λ∈{0, 0.5, 1} 三档掌握度与薄弱集合差异，落 staging，作为 D1 的依据（不是拍数字）。
- 测试：手算 fixture 验证加权公式、阈值边界（0.6/3 题）、四状态、is_correct 优先、部分得分、多考试近期权重、主/辅角色权重、证据可分解、无数据拒绝。

### S2 归因算法（§5.2，规则出骨架，约 1 天）

- `attribution.py`：薄弱 KP 按掌握度升序；沿 `kp_edges` 反向回溯；**只有状态为 normal/weak（样本足够）的前置才可作为归因节点**；路径上掌握度 <0.7 的最深节点 = 根因；前置全部良好 → 目标自身为根因。
- **前置证据不足/无覆盖 → 路径阻断**：不穿过 insufficient/no_coverage 节点猜根因；输出 `blocked_prereqs` 并降置信度（D3）。
- 多分支并列确定性排序：深度深 → 掌握度低 → kp_id；环保护（eval 库注入环用例）。
- 输出链：`{target, root, path, evidence[(q_id, exam, role, 得分/满分, 对错)], confidence, blocked_prereqs, data_scope}`。
- 置信度规则（D3，建议）：
  - high：路径全节点样本足够、最小样本 ≥5、目标 aux_share ≤0.5；
  - mid：路径全节点样本足够、最小样本 ≥3；
  - low：存在阻断前置，或目标 aux_share >0.5（薄弱判定主要由辅助证据驱动）。
- LLM 不参与归因判断（S3 只叙述）。
- 测试：直接前置/两跳/多分支并列/前置全好/前置不足阻断/目标不足/无覆盖/环保护/排序确定性。

### S3 推荐 + 叙述 + 服务/API/工作流（§5.3，约 1.5 天）

- `recommend.py`：**真实题库检索**，复用 `retrieval.service.search_questions`：
  - 对每个根因 KP：stage=primary、KP 扩展召回、processed-only（rejected 天然不可见）；
  - 难度规则（D4）：`band = clamp(round(5 × 根因掌握度), 1, 5)`，取 `difficulty_min=max(1, band-1)`、`difficulty_max=band`（薄弱降 1 档起步，§5.3）；
  - 按检索阶段 3 重排分取 3–5 题，跨根因去重；
  - 返回真实 `q_id/stem/difficulty/kp_ids/source='ape210k'/ref=q_id`；**不返回 score 当卷面分**（题库 score 是占位 1.0，文档注明）；
  - 候选 <3 → 如实返回 `insufficient_question_bank` + 实际候选数，不编题、不用 LLM 生成冒充题库题。
- `narrate.py`：LLM 只把规则链转成教师可读叙述；prompt 硬约束“不得更改根因/路径/数值”；输出 Pydantic schema；**无 Key/失败 → 确定性模板兜底**（离线验收可跑）。叙述中辅助 KP 证据必须标注“辅助证据”，不与主证据等权表述（用户强调②）。
- `service.py`：编排 data_access → mastery → attribution → recommend → narrate；引用账本（§4.4）：KP ref=kp_id、推荐题 ref=q_id、导入题证据 ref=q_id（本演示班全部有 q_id）；used_in 装配。
- API（§10.2/§10.5）：
  - `GET /api/classes`（班级列表 + 计数，限流 120）；
  - `GET /api/diagnosis/class/{class_id}`（同步 JSON：四状态掌握度/归因链/推荐/数据范围；确定性部分不依赖 LLM，限流 120）；
  - `POST /api/diagnosis/class/{class_id}/report`（202 + SSE，走新工作流，限流 30）。
- 新工作流 `class_diagnosis`：load_class_data → compute_mastery → attribute → recommend → narrate → build_diagnosis_pdf；直接传 class_id，不经 planner；纳入 WORKFLOW_NODES 与 Trace 覆盖。
- 测试：FakeRetrieval/FakeLLM 离线；推荐只出真实 q_id；难度过滤生效；候选不足如实返回；叙述不改事实（数值断言）；API 认证/限流；引用覆盖；工作流端到端。

### S4 Trace + 成本（§6，真实 usage，约 1.5 天）

- **V5 迁移**：steps 增列 `llm_model TEXT`、`usage_source TEXT`、`llm_calls TEXT`（JSON 数组：每次调用 {model, tool, tokens_in, tokens_out, cache_hit, usage_source, cost, latency_ms}）；tokens/cost 不可用时存 **NULL**（不写假 0）。
- `tracing/collector.py`：contextvars 节点级采集；`_run_node` 前后记 latency；`executor.run_tool` 挂钩记工具名/耗时；采集失败只告警不阻断（try/except 隔离）。
- `llm_client.py`：`LLMResponse` 增 `usage`（prompt/completion/cache_hit）与 `usage_source`：
  - 供应商返回 usage → `provider`（真实值）；
  - 未返回 → `unavailable`，tokens/cost=NULL；
  - 节点内多次调用部分缺失 → `partial`，只汇总已知部分并标记不完整；
  - **不做 token 估算冒充真实**；重试的每次调用都单独记录。
- 成本：复用 `annotation/pricing.py` 单价表（peak/idle 按调用时刻），`cost = tokens × 牌价`，**标记 `cost_basis='price_table_estimate'`（按当时牌价估算）**——token 是真实值、费用是牌价推算，两者来源分开标注（用户强调⑤）。
- `TaskStore.update/get_from_db` 持久化/回读新列；SSE step 事件扩展 `tokens_in/tokens_out/cost/latency_ms/model/usage_source`（旧字段全保留）。
- API：`GET /api/tasks`（列表）、`GET /api/tasks/{id}/trace`（**从库直读**，重启后可回放）、`GET /api/stats/cost?days=30`（SQL 按天/任务聚合；**Σsteps.cost = 任务成本可复算**，单测断言）。
- 成本面板只统计业务任务 LLM 调用；Step 5 标注批次费用在独立台账，不混入（文档写明）。
- 测试：FakeProvider 有/无 usage 两路；unavailable/partial 标记；NULL 不造假；重启回放；成本对账；**四条工作流（exam_loop/question_gen/lesson_plan/class_diagnosis）节点 100% 落 Trace**。

### S5 报告 PDF + 前端（约 1 天）

- `report_pdf.create_report_pdf` 增可选参数（向后兼容）：四状态掌握度表、归因链（根因/路径/置信度/阻断前置）、推荐练习（真实 q_id/难度/来源）、数据范围页脚；演示班报告页脚如实标“合成演示数据”。
- 前端“学情分析”页：班级选择 → 掌握度四状态表 → 归因链可展开证据（q_id/role/得分）→ 推荐题卡 → 生成报告任务。
- 前端新增“运行观测”页：任务列表 → Trace 时间线（model/tokens/cost/latency + usage_source 徽标，unavailable 明确显示“供应商未返回”）→ 成本面板（按天/任务，标注牌价估算）。
- 沿用现有 app.js 风格与认证头，不引框架；真实起服冒烟。

### S6 验收与关账（约 1.5 天）

- **10 例 ground truth**（独立评测库，staging 不入 Git）：直接前置失败/两跳/多分支并列/前置全好→自身/前置不足→阻断+low/目标不足→不出链/无覆盖/近期权重翻转根因/辅助权重改变薄弱判定/环保护。我起草用例与期望根因 → **用户核验冻结 Gold** → 跑 `eval_diagnosis.py` → 根因一致率 ≥80%（数字实测回填，不编造）。
- **真实导入数据端到端**：演示班 `cls_3b71d97847` 跑 class_diagnosis：JSON/PDF/Trace/成本全链路；与设计参考值的差异如实报告（角色权重引入后的重算）。
- Trace 覆盖 100%、重启回放、成本 Σ 对账、引用覆盖、四状态正确性。
- 全量 pytest；文档回填：STATUS（证据+commit）、DECISIONS（㉞+）、ROADMAP、技术方案 §5/§6 实测口径、RESUME-AMMO；commit → 用户验收 → push（TUN 关闭）。

## 2. 需要确认的决策（D1–D6）

| # | 决策 | 建议 | 依据 |
|---|---|---|---|
| D1 | 辅助 KP 证据权重 λ | **0.5**，同时输出 primary_only_mastery 与 aux_share；S1 交付 λ∈{0,0.5,1} 敏感性报告 | Gold v2 标签原则：主=核心考查目标，辅=独立教学意义（非核心）；辅助证据是较弱信号，0.5=“计入但减半”，λ=0 会丢弃辅助信息、λ=1 违背用户强调②。最终值以敏感性报告+用户裁决为准 |
| D2 | 四状态语义 | no_coverage（无映射题）/ insufficient（不同 q_id<3）/ weak（<0.6）/ normal；样本数主辅都计，但角色权重区分证据强度 | 用户强调③；与已定阈值一致 |
| D3 | 置信度规则 | high=全路径足够+最小样本≥5+aux_share≤0.5；mid=全路径足够+≥3；low=有阻断前置或 aux_share>0.5 | 可单测、可解释；阻断不猜根因 |
| D4 | 推荐难度规则 | band=clamp(round(5×根因掌握度),1,5)，取 [band-1, band]；processed-only；3–5 题；不足如实报告 | §5.3“薄弱降 1 档起步”；题库 difficulty 为规则粗标 1–5（如实注明） |
| D5 | Trace 口径 | V5 增列；usage_source=provider/partial/unavailable；不可用存 NULL；cost=牌价估算并标注 cost_basis | 用户强调⑤；DeepSeek 实际返回 usage（annotation 侧已验证），agent 侧补回传 |
| D6 | GT Gold 流程 | 我起草 10 例 → 用户核验冻结 → 再跑指标 | Phase 1/2 惯例（Gold 归用户） |

## 3. 明确不做（防范围膨胀）

- 学生级归因（每人 ≥3 题/KP 不满足，顺延）；
- Phase 4 评测框架 / `/api/eval/report` / judge 校准；
- 检索 Round2（长查询改写/KP 描述扩充等 backlog 不动）；
- 全量标注解冻、MCP/质检环、ECS 部署；
- 不为演示效果调答题数据；角色权重引入后的掌握度变化如实报告。

## 4. 时间盒与验收

- 约 **8 个工作日**（S1 1.5 / S2 1 / S3 1.5 / S4 1.5 / S5 1 / S6 1.5）；超时按 Phase 2 口径“基线+已知限制”关账，但 **Trace 真实性与成本可复算两项核心验收不砍**。
- 验收线：归因一致率 ≥80%（10 例 GT，用户 Gold）；真实导入班端到端；Trace 四工作流 100%；重启回放；Σcost 可复算；四状态/主辅权重/推荐真实题库/usage 真实性各有专项测试。
- 测试基线：342 passed 起步，只增不减。
