# Phase 3 小学演示数据（S0-3/S0-4）

> 状态：**合成演示数据已通过 F16 导入并核验**（2026-11-15）；**范围收尾已完成**（2026-11-16：种子切小学、初中请求守卫、前端/API 文档）；**Phase 3 本体未开始**。
> 本文件不含题干/答案内容；完整工作簿、设计 JSON、逐条答题与核验报告在 `data/staging/`（不入 Git，决策⑳/㉒）。
> 合成数据仅用于 Phase 3 演示与业务验证，**不得描述为真实学生数据**。

## 1. 数据设计

| 项 | 内容 |
|---|---|
| 班级 | 六年级演示班（grade 6 / primary）；school 字段 = “演示数据（非真实学校）” |
| 学生 | 20 名虚拟学生（学生01–学生20） |
| 考试 A（远期） | 2026-09-15，25 题 / 95 分 |
| 考试 B（近期） | 2026-11-10，24 题 / 84 分 |
| 题目 | 49 道 verified 小学题，全部显式 `q_id → Gold primary + 辅助 kp_id` 映射 |
| 答题 | 980 条（2 场 × 20 学生 × 各卷题数） |
| 样本口径 | 不同 `q_id` 数；<3 → `insufficient` |
| 掌握度 | 卷面分值加权；近期考试权重 2、远期 1；<0.6 薄弱；前置 <0.7 失败 |
| 粒度 | 班级级（学生级需要每人 ≥3 题/KP，本数据不满足） |

设计/映射/答题模式/预期诊断检查：`data/staging/primary_demo_design.md`（完整 49 行映射 + 检查）。
工作簿导入前检查：`data/staging/primary_demo_workbook_report.md`（0 错误，0 回读错误）。

## 2. 导入

| 项 | 结果 |
|---|---|
| 工作簿 | `data/staging/primary_demo_synthetic_workbook.xlsx`（sha256 `ff75e33f880eca34…`） |
| 导入方式 | 现有 F16 `importer.import_file`（未绕过导入服务直接写业务表） |
| 批次 | `imp_20261115132652_4c6a91`；status=committed |
| 行数 | **1052 / 1052 / failed 0**；warnings 0 |
| 班级 ID | `cls_3b71d97847` |
| 考试 ID | `exam_3b071422d8`（一，2026-09-15）/ `exam_c9daf187ce`（二，2026-11-10） |
| 合成标记 | 班级 school 字段、考试标题、工作簿文件名、导入报告、mapping manifest 均明确标注 |

执行报告：`data/staging/primary_demo_import_execution_report.md`。

## 3. 导入后核验

报告：`data/staging/primary_demo_import_verification.md`（**0 错误 / 0 警告**）。

| 检查 | 结果 |
|---|---|
| 记录数 | class 1 / students 20 / exams 2 / exam_questions 49 / answers 980 |
| 主知识点映射 | **49**（每题 1 个） |
| 辅助知识点映射 | **47** |
| q_id → KP 集合 | 逐题与 mapping manifest 完全一致，无丢失/多余 |
| 答题分值/正确性 | 980 条逐条一致 |
| 掌握度 | 逐 KP 与设计值一致（分值加权 + 近期 2:1 + insufficient） |
| 归因链 | 5 条，根因均为乘除运算；`乘除 → 分数 → 量率` 等路径一致 |
| 近期 2:1 | 加减运算 0.6917（无近期）→ 0.7333（有近期），在 0.7 前置阈值两侧翻转 |
| 小学原有资产 | `questions` 207,949 / `q_kp` 571,364 / KP 82 / 边 87 / `q_kp_llm` 1,319 / `q_annotation_results` 1,000 / tasks 27 / steps 160 均 **不变** |
| 检索冒烟 | KP：鸡兔同笼→`kp_p_005`；题目：分数加减应用题→3 条小学题 |
| API 冒烟 | `/api/health` 200；`/api/import/{batch}` 带 token 200（committed 1052/1052/0），无 token 401 |
| 全量测试 | **325 passed / 0 failed** |

## 4. 主/辅知识点角色（Phase 3 必须处理）

- DB 的 `exam_questions.kp_ids` 是主+辅的**并集**，不区分角色。
- 角色与映射来源保存在 `data/annotations/primary_demo_mapping.jsonl`（Git，49 行，无题干/答案）：
  `q_id / primary_kp_id / aux_kp_ids / source=gold_v2_confirmed / synthetic_demo=true`。
- **辅助知识点不得在诊断解释中被无条件视为与主知识点同等强度的证据**；Phase 3 实现诊断叙述/证据明细时必须按角色加权并保留来源。本次只保留映射，不改诊断逻辑（Phase 3 本体）。

## 5. 复现命令

```bash
.venv/Scripts/python.exe -X utf8 -m backend.scripts.build_primary_demo_workbook
.venv/Scripts/python.exe -X utf8 -m backend.scripts.import_primary_demo
.venv/Scripts/python.exe -X utf8 -m backend.scripts.verify_primary_demo_import
.venv/Scripts/python.exe -m pytest -q
```

## 6. 范围收窄收尾（2026-11-16 完成）

- **种子演示数据切小学**：`data/knowledge/knowledge_points.json`（7 个小学 KP）、`data/questions/question_bank.json`（10 道小学题，100 分）、`data/demo/student_answers.json`（20 名学生，合成答题）已替换；`exam_loop` 不再使用初中“一元二次方程”种子。启动校验仍为「7 个知识点 / 10 道题 / 20 名学生」。
- **初中请求友好拦截**：`backend/app/scope.py` + `/api/chat`、`/api/tasks` 前置守卫；返回友好提示，不创建任务、不调 LLM、不产生费用。负向测试见 `backend/tests/test_scope_guard.py`。
- **前端示例/API 文档**：示例 chip、placeholder、欢迎文案切换为小学内容；`app.js` 处理 `scope_notice` 与 `model=scope-guard`；技术方案 §10.2 记录 API 返回形态。
- **旧脚本归档**：`generate_demo_import.py` 默认拒绝执行（需显式 `--allow-legacy-generator`），新演示流程为 `build_primary_demo_workbook.py → import_primary_demo.py`。
- **Phase 3 本体未开始**：掌握度/归因/推荐/Trace/成本仍待下一阶段。

> 本文件为设计/导入/核验的事实汇总；详细数据与报告在 `data/staging/`。合成演示数据不得描述为真实学生数据。
