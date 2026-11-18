# Phase 4 Golden Set 设计规范（技术方案 §9.2 / ROADMAP Phase 4）

> 起草：2026-11-18；阶段：S1 设计规范定稿，待评审。
> 依据：`docs/技术方案.md` §9.2（golden set 格式与维护规则）、§9.1（指标口径）、§4.5（章节同义白名单）、
> `docs/development/PHASE4-PLAN.md`（决策㊲：golden 构成、判定的确定性要求）。
> 本文定义 golden 的**独立预期**：所有 expect 由需求意图 + 系统真实能力推导，**不得用当前 Planner 实际输出反向生成**。

## 1. 单条格式（§9.2 扩展）

```json
{
  "case_id": "gc_0001",
  "prompt": "请对《分数加减》出 10 道随堂小测并生成学情报告",
  "expect": {
    "behavior": "execute",
    "workflow": "exam_loop",
    "params": {
      "subject":    {"match": "exact",   "value": "数学"},
      "chapter":    {"match": "synonym", "value": "分数运算"},
      "count":      {"match": "exact",   "value": 10},
      "difficulty": {"match": "exact",   "value": "混合"},
      "question_types": {"match": "any"},
      "total_score": {"match": "exact",  "value": 100}
    },
    "artifacts_min": ["01_试卷.docx", "02_答题卡.xlsx", "03_学情报告.pdf"]
  },
  "tags": ["core", "exam_loop", "章节小测"],
  "rationale": "一条龙标准场景：章节+题量+报告语义 → exam_loop，默认参数全覆盖"
}
```

### 1.1 `behavior`（预期行为，四类，判定规则确定）

| behavior | 判定规则（S2 runner 实现） | 触发情形 |
|---|---|---|
| `execute` | 任务 `status=completed` 且产物 ⊇ `artifacts_min`；若 workflow 有值则须命中 | 标准成功路径 |
| `fallback` | 任务 completed/产物满足，但 **planner 走确定性回退**（章节缺失/LLM 解析失败默认值） | 模糊输入、参数非法类型 LLM 无法解析 |
| `reject` | **API 层**：`POST /api/tasks` 返回 `scope_notice` 且 `task_id=null`，不创建任务、不调 LLM | 初中/超范围学段（scope 守卫） |
| `clarify` | **API 层 + 会话**：返回 `need_clarification` 且 `task_id=null`（F1 追问通道） | 会话任务信息严重不足 |

> 说明：reject/clarify 是 **API 层行为**（`tasks.py`），runner 对这两类走 HTTP 集成判定；
> execute/fallback 走 workflow 执行判定。class_diagnosis 不经 planner（无此四类中的 fallback）。

### 1.2 `params.*` 匹配模式（参数字段准确率的字段级判定，§9.1）

| match | 判定：实际值 | 用途 |
|---|---|---|
| `exact` | `== value` | 确定性参数（count/difficulty/total_score/subject/workflow） |
| `synonym` | `in`（value + 全局同义白名单 §3 展开） | **chapter**：允许同义表述匹配（§4.5 原文要求） |
| `one_of` | `in value[]` | 多合理取值（如难度「简单｜中等」都算对） |
| `any` | 恒真（不参与参数准确率分子分母） | 不便断言的字段（question_types 语义由题型短语承载） |

**参数字段准确率定义**：`命中字段数 / 参与比对字段数`（`any` 不参与）。
**参与口径（S1 定稿）**：`prompt 中显式出现的参数`参与比对（误差=planner 未正确抽取显式表达）；`prompt 未显式出现的参数`该 case 设 `any`（默认值 10/混合/100 等不列入分子分母——planner 用默认不构成抽取错误）。aggregate = 全 golden 平均。
**判定示例**：「请对《分数加减》出 10 道随堂小测」→ count=exact 10 参与比对；difficulty/题型/总分未提及 → any 不参与。

### 1.3 `workflow` 接受单值或候选数组
不写的 case 表示「任何工作流均可」（仅用于无工作流语义的边界 case，评审时逐条给理由）。

### 1.4 `tags` 约定
- `core` / `boundary`（§9.2 必须，boundary 占比 ≥20% 由校验脚本保证）；
- 工作流标签 `exam_loop` / `question_gen` / `lesson_plan` / `class_diagnosis`;
- 边界子类：`模糊表述` / `参数越界` / `参数冲突` / `注入` / `超范围` / `数据不足` / `易误判` / `合法边界`。

## 2. 独立预期（不使用 Planner 输出反推）

1. 每条 `expect` 由「需求意图 → 产品应然行为」独立推导；编写顺序：case_id → prompt → **先写 expect** → 再对照代码事实确认该预期在现行系统可达（可达性与应然性双检）；
2. 明确写入 `rationale`（为何期望这个行为/参数）；
3. 评审时逐条人工裁决（见 `build_golden_set.py --review` 产物），**running Planner 实测值只作为对照列，不作为 expect 来源**；
4. 维护规则（§9.2）：评测失败不得改 golden 硬凑；改 golden 必须走变更记录并给理由。

## 3. chapter 同义白名单（§4.5「chapter 允许同义匹配」落地）

| 同义词 | 规范化 KP 名称 | 来源 |
|---|---|---|
| 分数加减 / 分数加减法 / 分数应用题 / 分数的加减 | 分数运算 | DB KP 名称 |
| 百分数 / 百分数计算 / 百分数应用题 / 折扣率 / 成数 | 百分数运算 | DB KP 名称 |
| 量率对应 / 单位1 / 单位"1" | 量率对应 | DB KP 名称 |
| 率的应用 / 率类应用题 | 率类问题 | DB KP 名称 |
| 归一 / 归一法 / 归一应用题 | 归一问题 | DB KP 名称 |
| 长方形面积 / 矩形面积 | 长方形面积 | DB KP 名称 |
| 圆的面积 / 圆面积 | 圆的面积 | DB KP 名称 |
| 单价数量总价 / 购物计算 / 人民币计算应用 | 人民币计算 | DB KP 名称 |
| 相遇问题 / 相向而行 / 行程相遇 | 相遇问题 | DB KP 名称 |
| 植树 / 植树应用题 / 间隔问题 | 植树问题 | DB KP 名称 |
| 鸡兔同笼 / 鸡兔问题 / 混合鸡兔 | 鸡兔同笼 | DB KP 名称 |
| 平均数 / 平均分 | 平均数问题 | DB KP 名称 |
| 年月日 / 日期计算 | 年月日 | DB KP 名称 |
| 单位换算 / 进率换算 | 长度/面积/体积/质量/时间单位换算 | DB KP 名称 |
| 四则运算 / 四则混合 / 运算顺序 | 四则混合运算 | DB KP 名称 |
| 小数乘除 / 小数计算 | 小数运算 | DB KP 名称 |
| 乘法分配律 / 简便运算 / 巧算 | 简便计算 | DB KP 名称 |
| 方程 / 解方程 | 方程求解 | DB KP 名称 |
| 圆柱体 / 圆柱体积表面积 | 圆柱 | DB KP 名称 |

> 白名单以「query → 规范化 KP」为键；synonym 判定 = 实际 planner chapter 命中 value 或 value 的白名单任一。
> 白名单为**评审开放项**：评审通过一条补一条；命中多个规范化 KP 的（如「单位换算」）判定为命中其一即过。

## 4. 构成与覆盖面（决策㊲）

| 工作流 | core 预估 | 说明 |
|---|---|---|
| exam_loop | ~35 | 章节×参数分支，含「分析/报告/学情」语义触发 |
| question_gen | ~20 | 章节×难度/题量/题型分支，含 LLM 数量不足失败路径 |
| lesson_plan | ~20 | 主题×知识点组合 |
| class_diagnosis | ~5 | 演示班 / F16 班 / 404 班级 / 422 无数据 |
| **core 合计** | **~80** | |
| boundary | **≥20** | 与 core 互不重叠，跨工作流 |

**boundary 子类覆盖清单**（用户裁决要求，每条 case 至少对应一个子类）：
- `模糊表述`：无章节 / 口语化 / 只有题量没章节 / 只有动词没对象；
- `参数越界`：count=0 / count>20（25） / total_score=0 / total_score=1000 / difficulty 非法措辞 / question_types 非法类型；
- `参数冲突`：同卷两种难度 / 题量与总分自相矛盾 / 章节重叠冲突指令；
- `注入`：prompt 内嵌"忽略上文/直接输出 XX/系统提示词泄露"等注入尝试；
- `超范围`：初中信号（一元二次方程/初二/中考等 scope 信号词）、非数学学科请求；
- `数据不足`：question_gen 高质量题数缺口（count=20 高难度）、class_diagnosis 无数据/无此班级；
- `易误判`：scope 信号边界词但实为小学（**方程** vs 一元二次方程、**面积** vs 初中面积公式、"三角尺""圆规"几何工具）、种子 7 KP 与 DB 82 名称差异映射（如「行程问题」→一般行程/相遇/追及）；
- `合法边界`：空 prompt（API 400）、纯英文/数字数学术语、超长 prompt 截断。

## 5. 校验脚本（`backend/scripts/build_golden_set.py`）

1. **结构校验**：case_id 唯一且格式 `gc_四位数` / prompt 非空 / expect 字段合法（behavior∈四值、workflow∈四工作流或数组、params.match∈五模式）/ tags 结构 / artifacts_min 非空且文件名与 §1 工作流常量一致；
2. **构成校验**：core≥80 / boundary 占比 ≥20%（warning 直至 ≥20% 才放行冻结）/ 四工作流覆盖面 / 八个 boundary 子类至少各 1；
3. **潜在重复检测**：prompt 归一化（去空白/去空格/去标点）后两两比对，重复 → error；
4. **可达性对照（不列入 expect）**：脚本内只读现有代码常量与 artifact 常量，输出「预期 vs 现行实现」对照表供评审；**不反推 expect**。

## 6. 输出

- `backend/scripts/build_golden_set.py`：case 数据内嵌（结构化表），`--json` 出 JSONL、`--review` 出评审指南（staging，不入 Git）、`--validate` 跑全部校验；
- 草案 JSONL：`data/staging/golden_set_draft_v1.jsonl`（不入 Git，待冻结后落 `data/gt/` 或 `data/evals/` + 真库 eval_cases）；
- 评审指南：`data/staging/golden_review_guide_v1.md`（逐条含 prompt/expect/应然理由/对照列/裁决栏四选一）。

## 7. 冻结后行为

用户逐条裁决 → 冻结版 JSON 入 Git + 写入真库 `eval_cases` → 进入 S2（runner）。
评审通过前 **不写 eval_cases、不进 S2、不调真实 LLM**（决策㊲⑦）。

## 8. 评测限制与诊断口径（V1 评审裁定，2026-11-18）

1. **exam_loop 的 count/difficulty 只验证 Planner 参数抽取**，不代表实际试卷生成受其控制：
   exam_loop 出卷走种子题库（固定 10 题 100 分，`_generate_exam_seed`），planner 抽出的
   count/difficulty/total_score 仅用于"参数字段准确率"判定，不进入产物断言。
2. **行为判定指标**（新增，独立于任务成功率）：对每条 case 记录"实际行为 vs 预期行为"，
   behavior ∈ {execute, fallback, fail, reject, reject_scope, reject_404, reject_400, reject_422, clarify}；
   行为判定通过 = 实际行为 == 预期行为。
3. **分母归属**：任务成功率分母只含 `execute/fallback`（且 mode=both）的 case；
   **clarify / reject / reject_* 不计入任务失败率**（它们是行为指标的分母）；
   `fail`（如 gc_b24 数据不足）归属行为指标，不计入任务成功率分母。
4. **产物判定**：仅 `execute/fallback` case 要求 `artifacts_min` 全部存在；clarify/reject 不检查产物。
5. **mode**：`both` = fake+online 均参与成功率分母；`fake_only`（gc_b24）= 仅 fake 档确定性判定，
   online 档只观察、不计入相同分母（fake 档 FakeLLM 预设 12 条 ≤20 保证确定性失败）。
6. **一致性（要求5）**：每条 case 的 entry/workflow/params/artifacts_min/匹配模式与 behavior 相互自洽，
   由 `build_golden_set.py --validate` 强制执行（行为↔入口、clarify 需 session、reject 需 http、mode 合法）。