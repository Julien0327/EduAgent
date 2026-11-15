# EduMate 当前状态（STATUS）

> 最后更新：2026-11-15（**Phase 3 S0 范围收窄：小学前置图 87 条已冻结、初中运行时已清理、Phase 2.2 小学基线完成，决策㉝**）。**新会话开工前必读本文件 + docs/技术方案.md §5/§6（Phase 3）**，以项目文档、实际代码和本目录状态文档为准，不依赖旧会话聊天记录。

## 当前位置

**Phase 0 ✅｜Phase 1 ✅（2026-11-14 关账，决策㉚）｜Phase 2 可信检索 ✅（2026-11-15 关账，决策㉜）｜Phase 3 归因+观测 = 进行中（S0 范围收窄，决策㉝）**

### Phase 3 S0-1 小学前置图冻结（2026-11-15，决策㉝）

用户裁决运行时收窄为小学单学段；初中图谱归档、不参与运行时构建。本轮只做前置图草案/冻结，**未改数据库、未调 LLM、未产生费用**。

| 项 | 结果 | 证据 |
|---|---|---|
| 小学前置图 v1.0 | ✅ 82 个小学 KP / **87 条**教学依赖边（80 草案 + 7 推荐补充；用户 2026-11-15 审核通过） | `data/knowledge/primary_edges.json` |
| 校验 | ✅ 引用完整 / 无自环 / 无重复 / DAG 无环 / 0 错误；5 个独立节点（026/027/030/031/033）；8 个根节点；55 个叶节点；最长链 6 条边 | `data/staging/primary_graph_frozen_review.md` |
| 3 条有意未纳入 | 077→078、077→028、046→057 按用户裁决不恢复；后续如真实诊断数据证明需要再版本化调整 | 同上 §6 |
| 入库构建器 | ✅ `build_primary_graph.py`：只管理 primary 边（先删 primary 边再全量插入），不碰初中 KP/边；支持 `--dry-run`；幂等 | 8 项测试（`test_build_primary_graph.py`） |
| 测试基线 | ✅ **321 passed / 0 failed**（S0-1 时 319 + S0-2 清理脚本 2 项） | 全量 pytest（2026-11-15） |
| Phase 2 Round1 证据补充 | ✅ `PHASE2_ACCEPTANCE_INDEX.md` 新增 §0：A 基线 / D Round1 / 决策㉜ / 报告链接；20 条教师问句仍待用户评分 | staging 验收索引 |
| **Phase 2.2 重基线** | ✅ 49 条小学查询（移除唯一初中查询）：**R@1 32.65% / R@5 55.10% / MRR 0.4335 / NDCG@5 0.4201**；与 D 50 条结果去 junior 后逐项吻合（**非检索回归**） | `retrieval_baseline_report_primary_v2.md/.json` |
| 未做/待批准 | 小学演示数据重建；产品/技术/README/API 文档的运行时切换部分（核心学段说明已补） | 本文件「待办」 |

- 提交：`7988779`（小学前置图冻结 + 文档同步）、`851ecd2`（Phase 2.2 工具与证据）；Phase 2 代码冻结 tag `phase2-frozen-2026-11-15` 指向 `b0ab744`。**未 push**（按部署策略，阶段验收后统一 push）。

### Phase 3 S0-2 初中运行时清理（2026-11-15，已执行，决策㉝）

用户明确批准删除（无需二次确认）；执行前先做 dry-run 并自动备份。

| 项 | 结果 | 证据 |
|---|---|---|
| dry-run | ✅ 将删除：班级 1 / 学生 20 / 考试 1 / 题目 10 / 答题 200 / 初中 KP 303 / 初中边 374 / kp_fts 385；预检查（初中题 0、q_kp 0、q_kp_llm 0、annotation_results 0、外部考试初中 KP 0）全部通过 | `data/staging/primary_scope_dryrun_report.md/.json` |
| 备份 | ✅ SQLite Online Backup：`data/staging/backups/edumate_pre_primary_scope_20261115_205913.sqlite`，175.0 MB，sha256 `cbcfc42483577ecf…`，`integrity_check=ok` | `primary_scope_execution_report.md/.json` |
| 实际删除 | ✅ 答题 200 / 题目 10 / 考试 1 / 学生 20 / 班级 1 / 初中边 374 / 初中 KP 303；**保留** 小学题库 207,949、q_kp 571,364、q_kp_llm 1,319、annotation_results 1,000、import_batches 4（审计）、tasks 27、steps 160 | 同上 |
| 清理后验证 | ✅ 小学 KP **82** / 小学边 **87** / 初中 KP 0 / 初中边 0 / 初中演示数据 0 / kp_fts 82 / `integrity_check=ok` / 残留检查全 0 | SQL 实测 |
| 小学边写入 | ✅ `build_primary_graph.py` 写入 87 条；根 8 / 叶 55 / 孤立 5 / DAG 通过 | `primary_graph_build_report.json` |
| 检索冒烟 | ✅ 鸡兔同笼→kp_p_005、分数加减→kp_p_077/079/075、圆的面积→kp_p_050；小学题库可检 | 实测输出 |
| 清理后最终基线 | ✅ 49 条：stage_filtered 与 open **完全一致**（32.65%/55.10%/0.4335/0.4201），0 异常；与清理前主口径逐项一致 | `retrieval_baseline_report_primary_v2_final.md/.json` |
| 测试 | ✅ **321 passed / 0 failed** | 全量 pytest（2026-11-15） |

- **运行时状态**：数据库已切换为小学单学段（82 KP / 87 边 / 207,949 题）；初中图谱源 JSON/构建脚本/报告保留为 Phase 1 历史资产；`build_graph.py` CLI 默认拒绝执行（需显式 `--allow-legacy-junior`）；`kp_fts` 已重建 82 行。

### Phase 3 S0-3 小学演示数据设计（2026-11-15，草案待用户审核，**未导入**）

| 项 | 结果 | 证据 |
|---|---|---|
| 设计脚本 | ✅ `design_primary_demo.py`：从 49 道 verified 题确定性分卷、生成答题、计算分值加权 + 近期 2:1 掌握度与预期归因链 | 可复现 |
| 数据设计 | ✅ 六年级演示班 / 20 名虚拟学生 / 2 场考试：A 远期 2026-09-15（25 题 95 分）、B 近期 2026-11-10（24 题 84 分）；980 条答题 | `data/staging/primary_demo_design.md/.json` |
| 映射 | ✅ 49 道题全部显式 q_id → Gold primary + 辅助 kp_id；覆盖 40 个小学 KP；无名称模糊匹配、无按题序硬塞 | 设计报告 §1/§2 |
| 检查 | ✅ 49 verified / 20 学生 / 2 卷 / 无重复 q_id / 答题矩阵完整 / 目标 KP 样本 ≥3 且两卷均有 / 3 节点归因链 / insufficient 覆盖 / 近期与分值权重均生效 | 设计报告 §5.3 |
| 预期诊断 | 薄弱且样本足够：乘除 078、分数 075、量率 037、四则混合 079、小数 074；根因链 078→075→037；近期 2:1 使 077 在 0.7 阈值翻转（0.6917→0.7333） | 设计报告 §5.2/§5.4 |
| 数据边界 | 32 个 KP 样本 <3（insufficient，不输出推测结论）；42 个 KP 无论证数据；答案为合成演示数据，非真实学生 | 设计报告 §6 |
| 状态 | **未导入、未写业务库、未调 LLM、无 API 费用**；等待用户确认样本/粒度/分卷/场景表四项口径后走 F16 | — |



- **Phase 1 交付**：初中 303 KP 图谱 + 374 边 ✅；21 万行题库 ETL（processed 207,858，方案 A 修复+全库核验 0 差异）✅；FTS5 ✅；小学 82 KP 规则标签（98.2% 覆盖）✅；F16 导入服务（17 项端到端）✅；LLM 打标管线 + 500 题双臂试跑 + Gold v2 人工核验终版 ✅（管线冻结，见"Phase 1 关账与 Step 5 冻结"节）；**Phase 1 关账时 227 passed（当前 321）**；简历素材清单 `RESUME-AMMO.md`（缺口检查：RAG 未做=Phase 1 时最大缺口；Phase 2 已完成检索层）。
- **Step 5 冻结（决策㉚）**：prompt v2 重跑/全量标注/1070129 治理/079 定义修改/B 臂投入——均不执行；LLM 标签=可加性数据（新批次随时补跑、消费视图自动生效），Phase 2 零依赖零返工；解冻需用户批准。
- **Phase 2 开工纪律（决策㉚⑥）**：中文 FTS5 分词 day-1 → 引用注入 day-1 → 认证限流中段；时间盒 1–1.5 周；BGE 模型已就绪（data/models，Phase 1 遗产直接复用）。


### Phase 2 关账（2026-11-15，决策㉜）

Phase 2 功能步骤 S1–S7 全部完成，测试 **311 passed**；用户确认按「基线 + 已知限制」口径关账，不追求 §4.5 指标达标后再进入 Phase 3。

| 项 | 结果 |
|---|---|
| 正式基线（A） | R@1 32.00% / R@5 54.00% / MRR 0.4317 / NDCG@5 0.4232 |
| Round1 优化后（D，已接受） | R@1 **34.00%** / R@5 **56.00%** / MRR **0.4449** / NDCG@5 **0.4317**；any-gold R@1 36.00% / R@5 60.00% |
| 23 条原失败 | 修复 **1**（`v_q_ape_822661`，名称加分）；剩余 22 条为向量排名/描述泛化问题 |
| 27 条原命中 | top-5 回归 **0**；1 条 top-5 内排名下降（1116923 2→3，非回归） |
| FTS 结构修复 | 50/50→10/50 零命中，40 条恢复候选；本评测**无 metric 增益** |
| 名称加分修复 | 累计误触发 500→7；贡献 +2pp R@1/R@5、+0.0132 MRR、+0.0085 NDCG |
| 已知限制/backlog | 36 条 gold primary 未进 FTS 原始候选；12 primary/19 any-gold 被置信门过滤；301084 非 gold 候选被推到第 1（min-max 风险）；长查询改写、KP 描述扩充、单位词泛化未做 |
| 内存偏差 | 193.6MB > 150MB 目标，接受记录；FP32 保留、INT8 不启用、0.99 阈值不改 |
| 20 条教师问句评分 | 顺延，不阻塞 Phase 3 |

- 正式评测集/Gold 冻结不改；后续检索优化作为 **Round2 / Phase 2.1**，版本化报告、不覆盖基线。
- 验收材料（staging，不入 Git）：`retrieval_baseline_report.*`、`retrieval_failure_diagnosis.*`、`retrieval_optimization_round1.*`、`retrieval_optimization_round1_supplement.*`、`PHASE2_ACCEPTANCE_INDEX.md`。
- **下一步**：Phase 3 归因+观测（§5/§6）；建议新开会话，以 STATUS + ROADMAP + 技术方案 §5/§6 为基准。
- 收尾 commit：`f65fa42`（2026-11-15）。

### Phase 1 各步完成快照

- **Step 1 存储层建库 ✅**：V1 全量 DDL + db 连接层（每连接 foreign_keys=ON）+ 8 项单测；后增 V2 标注表迁移（Step 5-B1）。
- **Step 2 知识图谱构建 ✅**：初中 303 知识点 + 374 条前置边入库；用户抽检 4 处修正已处理；seed=42 复现验证不变。
- **Step 3 F16 导入服务 ✅**：模板生成 + 七步管线 + 三端点 + 17 项真实 Excel 端到端测试 + demo 冒烟。
- **Step 4 APE210K ETL ✅（2026-11-13 用户最终验收，决策㉔）**：数据源核验（许可裁决=决策㉒）+ 流式清洗入库 + FTS5 + 小学 KP 规则聚合 + 方案 A 修复（决策㉓）+ 补充复核 + 最终裁决；processed 207,858；全库逐行核验 0 不一致。
- **Step 5 LLM 打标 ✅ 管线完成并冻结（2026-11-14，决策㉚）**：设计方案两轮修订（㉕）→ B1 工程实施（V2 迁移/执行器/A·B prompt/试验集，1446f38）→ B2 三线预算+双臂真实试跑（a3b1c8e/0cbe689）→ B2 验收与核验（9bdf06f）→ prompt v2 阶段 A（b4c1f41）→ 核验指南（09855af）→ **Gold v2 终版落地 + KP 定义修订 v2（b60a203，决策㉙）**。试跑数据/gold/manifest 全部冻结保留。

## Phase 2 进行中（2026-11-14 开工，决策㉛）

**决策㉛（用户批复四项）**：①中文分词=jieba 预分词（0.42.1 已装，requirements 锁定）；②Embedding=复用 fastembed 官方量化 ONNX，不做 torch 自导出，§4.5「导出一致性」指标改口径（模型来源+sha256+召回实测）；③题库检索→组卷接工作流延后 Phase 3；④检索评测查询集（verified 49 题派生+手写初中补足）最终由用户核验冻结，20 条典型问句由用户人工评判。

### S1 中文 FTS5 分词完成证据（2026-11-14，全部实测）

| 项 | 结果 | 证据 |
|---|---|---|
| tokenize 模块 | ✅ `backend/app/retrieval/tokenize.py`：统一分词入口（`segment`/`fts_document` 索引形态/`fts_query` 查询形态）；查询侧逐词引号包裹+内部 `"` 转义=FTS5 语法注入收口（AND/OR/NOT/NEAR 等只作短语匹配）；索引与查询两端共用，其他模块不得自行切词 | 8 项单测（真实内存 FTS5 表验证，含注入隔离/子词命中/引号转义/空输入） |
| V3 迁移 | ✅ `kp_fts`（385 行 KP 关键词检索表，unicode61）；真实库 schema_migrations=[1,2,3]；重复应用幂等 | `migrations_v3.sql`；EXPECTED_TABLES 扩充 |
| ETL 集成 | ✅ `etl_ape210k.py` FTS 写入改 `fts_document()` 分词形态（FTS 内容=索引文本非原文，原文以 questions 表为准、命中回表）；幂等链重跑成功：漏斗与冻结基线**逐数一致**（raw 210,488 / processed 207,858 / rejected 91 / duplicate 2,539 / 疑似变式 244）；ETL 51.7s（jieba 分词 +33s）；库 183.5MB | ETL stdout 实录；`etl_ape210k_report.json` |
| 中文子词召回 | ✅ **unicode61 缺陷消除（Step 4 遗留项关闭）**：子词查询实测——「苹果」3,282 行、「三角形」3,173、「绳子」1,976、「人民币」345、「应用题」43（均 0.09–0.4ms，首查 398ms 为页缓存冷启动）；完整串查询回归正常；命中回表原文正常 | 实测实录（上方 stdout） |
| ⚠️ 意外发现与修复 | **ETL 全量重建冲掉 verified=1**（决策㉚⑤ 的 49 题标记被删重插清零）→ 新增 `restore_verified_flags.py`（幂等链固定第三步：Gold v2 终版 confirmed 49 题重放置 1，pending 1070129 不标记，库外 q_id fail-fast）；**已恢复 verified=49**；etl docstring 记录三步链：etl → build_primary_kp → restore_verified_flags | 脚本 + 4 项单测；重放输出（49/49 置 1） |
| ⚠️ V2 冻结数据与幂等链相撞（首次暴露） | `build_primary_kp.py` 旧 DELETE 重建语义被 `q_kp_llm.kp_id`/`q_annotation_results.primary_kp_id` 外键拦截（V2 建成后首次重跑链）→ KP 写入改 **UPSERT**（kp_id 确定性稳定、非键字段刷新——DB 小学 KP 描述随本次对齐到 KP 定义 v2 源头，kp_id/挂接不变）；防御性收缩清理仅删无引用行、被引用保留并报告 | 脚本改动 + 全量测试；重跑结果与基线一致（82 KP / 571,364 挂接 / 98.3% 覆盖 / 零命中 2 个）；V2 数据零变化（q_kp_llm 1,319 / results 1,000 / batches 2） |
| 测试基线 | ✅ **239 passed / 0 failed**（227 + 8 tokenize + 4 verified 重放） | 全量 pytest（2026-11-14） |

### S2 引用注入骨架完成证据（2026-11-14，§4.4，全部实测离线）

| 项 | 结果 | 证据 |
|---|---|---|
| 账本机制 | ✅ `backend/app/retrieval/citations.py`：CitationLedger（record 幂等合并 / annotate 装配 / save-load roundtrip / §4.4 格式）；`require_ref` 取数侧强制点（漏 ref=代码缺陷 fail-fast）；ref 形态约定：DB 题=q_id、种子题=`seed:<id>`、KP=kp_id、**LLM 生成题不进账本**（页脚如实统计"AI 生成"） | 6 项机制单测 |
| 取数侧挂钩 | ✅ `loader.load_question_bank()` 每题注入 `ref=seed:<question_id>`；`search_knowledge_points` 返回带 `ref=kp_id` | 单测断言（契约级） |
| 工作流装配 | ✅ exam_loop：题引用 used_in=试卷/答题卡逐题位置 + KP「整卷出题依据」；lesson_plan **新增 fetch_knowledge 节点**（教案基于检索知识点生成，KP 描述进 prompt——§4.4「教案知识点标注」注入点的必要组成），KP used_in=教案教学过程；run_workflow finally 落盘 citations.json（失败任务也保存已发生引用） | 端到端 FakeLLM 测试 |
| **覆盖率 100% 断言** | ✅ 核心验收：卷面每个带 ref 的题都在 citations.json 且 used_in 齐全（双向断言：账本无卷面之外引用）；下游丢弃引用=测试失败 | `test_exam_loop_citations_coverage_100` |
| 文档注入 | ✅ 试卷 section footer=来源构成统计（题库/种子/AI 生成三类，python-docx 实读 footer 断言）；教案文末知识点标注（kp_refs 参数向后兼容）；报告 PDF 数据来源页脚 | 3 项单测 |
| API | ✅ `GET /api/tasks/{id}/citations`（citations.json 为事实源；无文件返回空；任务不存在 404） | TestClient 用例（tmp_path 隔离） |
| 测试基线 | ✅ **253 passed / 0 failed**（239 + 14）；test_tools 契约更新（KP 返回+ref）、test_workflow 全过（lesson_plan 多一节点不影响） | 全量 pytest（2026-11-14） |



| 步骤 | 状态 |
|---|---|
### S3 三阶段检索模块完成证据（2026-11-14，§4.2/§4.3，全部实测）

| 项 | 结果 | 证据 |
|---|---|---|
| 三阶段引擎 | ✅ `retrieval/`：`kp_recall.py`（阶段1 双路召回：kp_fts 关键词 + 向量内积，**各自 min-max 归一化**后 α=0.4 融合→去重→名称直配加分→top-k；kftS 自愈同步）｜`question_search.py`（阶段2 两步法扩展 + 阶段3 重排：KP 重合 Jaccard 0.6/难度贴合 0.25/题面 FTS 0.15 **独立归一化加权**，fts 特征仅在题面检索意图时计入）｜`service.py`（编排门面+进程单例，lifespan 预热） | 引擎测试 15 项（临时库+FakeEmbedder 离线；白名单/注入拦截/processed-only/覆盖索引） |
| embedding 直连重构 | ✅ **弃 fastembed API 封装，直连 onnxruntime+tokenizers**（§4.3 服务器形态；arena 关闭+单线程）：与 fastembed 输出**余弦=1.0**（实现等价验证）；`build_kp_embeddings.py` 385 KP→embedding BLOB（载入 0.34s+推理 7s）；查询侧 bge 指令前缀/索引侧不加 | `embedding.py`；一致性实测（同批 3 文本余弦 1.0/1.0/1.0） |
| **性能优化实录** | ⚠️→✅ 首测三阶段 p95 **1521ms 超标**→profile 定位：q_kp 主键前导是 q_id，按 kp_id 找题**全表扫 57 万行**（~1.5s）→ **V4 迁移补 idx_q_kp_kp 反向索引** + expand 重写**两步法**（覆盖索引取候选 q_id→主键点查字段与挂接，聚合移应用侧）→ 终测 p95 **28ms**（54 倍改善） | V4 迁移 + 实测（1521→18-62→20-28ms 三轮记录） |
| **§4.5 指标实测回填** | ✅ 冷启动 **0.36s**（<5s✓）｜单条 embedding p95 **8.7ms**（<50ms✓）｜三阶段检索 p95 **28ms**（<500ms✓）｜模型 **94.8MB**（≤100MB✓）｜**进程内存增量 193.6MB ✗（目标 ≤150MB，偏差如实记录）** | 见下「内存偏差归因」行 |
| **内存偏差归因链** | fastembed 封装实测增量 **224MB** → 直连 ORT（arena off）**193.6MB** → §4.3 int8 动态量化实做（94.8→**23.8MB**，per-channel 同）但**最低余弦 0.9577 < §4.5 阈值 0.99 → 按阈值守门不交付**，保持 FP32 直连；服务器 2C2G 总账仍可运行（1194MB 预估 <2G）；若未来内存吃紧：int8+重评 Recall 后阈值修订走决策流程 | `quantize_bge_onnx.py` + 10 样本一致性报告（`data/models/bge-int8/quantize_report.json`，不入 Git） |
| 工具/工作流接线 | ✅ `search_knowledge_points` 工具切 retrieval 层（**DB 385 KP 替换种子 7 KP**，签名 chapter→query+stage，§4.4 ref 保持）；workflow fetch_knowledge 传 query；`generate_questions` 的种子 KP 映射改**尽力而为**（检索层 KP 名与种子体系不同——映射不到不再阻塞出题，如实返回 unmapped_kp_names）；conftest 增加 fake_retrieval/FakeEmbedder 替身（workflow/citations/interop 测试离线化） | 测试全量 268 passed；test_tools/test_workflow/test_citations/test_interop_aip 相应更新 |
| 召回质量冒烟 | ✅ 「鸡兔同笼」top1 直配（via=both）｜「分数加减应用题」top3 全切题｜「一元二次方程」top3 全中（双路齐中）；初中 KP 无题目数据→题=0（预期，DB 仅小学题） | 实测输出 |
| 测试基线 | ✅ **268 passed / 0 failed**（253 + 15 引擎） | 全量 pytest（2026-11-14） |



### S4 NL 问数完成证据（2026-11-14，§4.1，全部实测）

| 项 | 结果 | 证据 |
|---|---|---|
| NL 问数模块 | ✅ `retrieval/nl_query.py`：LLM 抽参（严格 Pydantic schema `BankQueryParams`：query/stage/difficulty/type/count/total_score）→ **失败确定性回退**（正则抽难度/题量/学段，无 Key 环境可演示）；**不生成自由 SQL**——参数复用 question_search 白名单校验（二次拦截）+ 全参数化绑定 | 12 项测试（LLM 路径 Mock/回退/边界） |
| 组卷配平 | ✅ `retrieval/balance.py`：总分约束不进 SQL（§4.1），候选集选出后按题型权重贪心配平（精确等分+余数补齐）；从 question_tools 上移共用（后者再导出向后兼容） | 总分精确性/空输入测试 |
| 工具注册 | ✅ `query_question_bank` 第 4 个 LLM 工具（`tools/bank_query_tool.py`）；返回题带 ref（§4.4 供账本装配） | test_tools 契约更新 |
| ⚠️ 生产级 bug 修复 | **检索单例连接跨线程**：TestClient 顺序化测试暴露——单例连接建于 lifespan 线程，FastAPI sync 端点跑线程池必炸（sqlite3 check_same_thread）；修复=`db.connect(check_same_thread=False)` 参数 + 检索层 `threading.Lock` 串行化（p95 28ms 串行无影响）；该修复对生产真实必要 | 测试顺序依赖暴露→全量 280 passed；db.py/kp_recall/service 三处 |
| 测试基线 | ✅ **280 passed / 0 failed**（268 + 12） | 全量 pytest（2026-11-14） |

### S5 任务持久化与多轮会话完成证据（2026-11-14，§7，全部实测离线）

| 项 | 结果 | 证据 |
|---|---|---|
| TaskStore SQLite | ✅ **接口不变**（create/get/update，§7.3）：进程内 dict 读缓存 + `update()` 全量写穿透（tasks UPSERT + steps 重建）；惰性连接 + apply_migrations 保障（CLI/测试不经 lifespan 也可落库）；WAL | workflow 在状态变化点显式 update（每节点 finally + 终态），工作流层逻辑零改动 |
| 重启恢复 | ✅ 内存缓存清空（模拟重启）后 `get_from_db` 仍可查任务+步骤 | `test_task_store_survives_restart` |
| 并发 10 任务 | ✅ 10 线程并发 create+update，WAL 下写不冲突、任务 ID 唯一、状态无错乱 | `test_concurrent_10_tasks_wal` |
| 多轮会话 | ✅ `session_store.py`（sessions/messages 表，V1 已建）：chat 增 `session_id`（缺省新建，消息全量落库+上下文每轮截 2000 字符，§7.2）；`GET /api/sessions` 列表 + `GET /api/sessions/{id}` 消息+任务卡回放；`POST /api/tasks` 增 session_id（任务归属会话）；planner 注入最近 6 轮历史（structured_completion 支持 history） | API 测试（chat 两轮同会话/回放/404） |
| F1 追问通道 | ✅ TaskPlan.need_clarification 字段；会话任务创建时预 plan：信息不足→200 返回追问（【追问】前缀落库，≤2 轮）；**超限降级默认值执行（202 + clarification_dropped 注明）**；无会话任务不追问（D4 语义保持）；预判失败不阻塞创建 | `test_f1_clarification_then_task` / `test_f1_drops_after_two_rounds` / `test_no_session_task_skips_clarification` |
| ⚠️ 测试架构经验 | 后台任务线程与 fixture teardown 竞态（Fake/连接销毁）→ F1 用例**轮询等待任务终态**再 teardown；conftest 全局 autouse 隔离任务库到 tmp（测试不再写真实库） | `_wait_task_done` helper；`_isolated_task_store` fixture |
| 测试基线 | ✅ **290 passed / 0 failed**（280 + 10） | 全量 pytest（2026-11-14） |

### S6 认证与限流完成证据（2026-11-14，§10.5 / 决策⑪，全部实测）

| 项 | 结果 | 证据 |
|---|---|---|
| 安全中间件 | ✅ `api/security.py` 纯 ASGI 中间件：OPTIONS/静态/AIP 放行 → 认证（`EDUMATE_API_TOKEN`，Bearer/X-API-Token 双头，`secrets.compare_digest` 常量时间比较，**401 不区分缺/错**防探测）→ 限流 | middleware 挂载于 main.py（先于 CORS 语义层） |
| fail-closed | ✅ 未配置 token → 仅 `/api/health` 可用，其余 /api/* 一律 **503**（带正确 token 也拒）；启动 warning 提示；`.env.example` 补配置说明 | `test_fail_closed_when_token_unset` |
| 滑动窗口限流 | ✅ 进程内实现（~60 行，单实例够用；分布式随多实例需求走变更记录）；规则=代码分支 classify（§10.5 表逐项：health 免/chat+tasks 创建 30/events 30/files+preview 60/任务与 sessions 查询 120/import 10/stats+eval 60）；超限 **429 + Retry-After** | `test_classify_limit_rules` / `test_rate_limit_429_with_retry_after` |
| 日志脱敏 | ✅ 全局 logging.Filter：日志中 `Bearer <值>`→`Bearer ***`（token 绝不入日志）；学生姓名（张三→张*/复姓保留两字）与学号（仅留后 4 位）脱敏函数供导入链路 | `test_redact_filter_bearer` / `test_student_masking` |
| 测试适配 | ✅ conftest：autouse 注入 test-token + AUTH_HEADERS 共享常量（7 处 TestClient 构造点）+ **限流桶按测试隔离**（模块级单例跨用例累积曾致 429——用例内多次请求仍在窗口内合法） | 3 个测试文件构造点更新 |
| 测试基线 | ✅ **299 passed / 0 failed**（290 + 9） | 全量 pytest（2026-11-14） |

### S7 前端与验收收尾证据（2026-11-14，助手侧完成）

| 项 | 结果 | 证据 |
|---|---|---|
| 前端认证接线 | ✅ 统一 `apiHeaders()`（X-API-Token，localStorage 保存 token 输入框）；401/503 友好提示；**SSE 经 `?token=` query 参数**（EventSource 无法带 header，浏览器限制；token 不入日志） | `frontend/app.js`；security.py extract_token 支持 query token |
| 引用面板 | ✅ 任务完成自动拉 `GET /api/tasks/{id}/citations` 渲染（kind/ref/used_in）；手动按钮兜底 | app.js showCitations |
| 会话回放 | ✅ 会话列表按钮 → `GET /api/sessions` → 点击回放消息+任务卡；chat 延续 session_id | app.js showSessions/openSession |
| 起服冒烟 | ✅ 真实起服：无 token 401 / 带 token 200 / health 免费 / 静态资源免认证 / 错 token 401；⚠️ 修复 lifespan 预热调错函数（get_embedding_service 引用自错误模块）——修复后预热日志正常 | 冒烟输出（8021/8022 端口） |
| 检索评测查询集 v1 | ✅ 50 条（**verified 派生 30** + 手写初中草稿 20，按章节轮转抽样）；`data/staging/retrieval_eval_queries_v1.jsonl` + 核验指南（不入 Git，决策⑳；**Gold 归用户惯例**） | `build_retrieval_eval_queries.py` 可复现 |
| 测试基线 | ✅ **299 passed / 0 failed**（S6 后无净增；前端为 JS 无 pytest 覆盖） | 全量 pytest（2026-11-14） |

## §4.5 指标实测汇总（Phase 2 收口状态）

| 指标 | 目标 | 实测 | 状态 |
|---|---|---|---|
| ONNX 导出一致性（口径改：直连 vs fastembed） | ≥0.999 | **1.0**（同批 3 文本余弦全 1.0） | ✅ 2026-11-14 |
| INT8 量化一致性 | ≥0.99 | **0.9577**（最低，10 样本） | ❌ **不达标→int8 不交付**（阈值守门，FP32 直连） |
| Recall@5 / Recall@1 / MRR | ≥80% / ≥50% / ≥0.6 | **D: 56% / 34% / 0.4449**（A 基线 54% / 32% / 0.4317；open 42% / 24% / 0.3158） | ❌ **未达标**（接受记录，决策㉜） |
| NDCG@5 | 记录不设阈 | **D: 0.4317**（A 基线 0.4232；open 0.3111） | 记录 |
| 检索耗时 p95 | <500ms | **28ms**（20 条典型查询×2 轮） | ✅ |
| 单条 embedding 耗时 | <50ms | **p95 8.7ms**（直连 FP32） | ✅ |
| 冷启动时间 | <5s | **0.36–0.88s**（模型+矩阵+自愈同步） | ✅ |
| 模型文件大小 | ≤100MB | **94.8MB**（FP32；int8 23.8MB 因精度未交付） | ✅ |
| 进程内存增量 | ≤150MB | **193.6MB** ✗（偏差已记录：int8 精度不达标被拦，2C2G 总账仍可运行；未来如需：int8+重评 Recall 走决策流程修订阈值） | ⚠️ 偏差 |
| 引用覆盖率 | 100% | **100%**（双向断言单测：卷面 ref ⊆ 账本且 used_in 齐全） | ✅ |
| 典型查询相关性（20 条人工 3 分制） | ≥85% | 【待用户评判】 | ⏳ |

**关账结论（决策㉜）**：按「基线 + 已知限制」接受；Round1 后 D 指标见上表。剩余为 Round2 backlog（长查询改写/KP 描述扩充/单位词泛化/置信门与 min-max 风险）与 20 条教师问句评分（顺延），不阻塞 Phase 3。

### 正式检索基线结果（2026-11-15 执行；Round1 后 D 为最终接受口径，决策㉜）

| 协议 | Recall@1(primary) | Recall@5(primary) | MRR | NDCG@5 | Top-10 命中率 | 异常 |
|---|---|---|---|---|---|---|
| **stage_filtered（主）** | **32.00%** | **54.00%** | **0.4317** | **0.4232** | 68.00% | 0 |
| open（诊断） | 24.00% | 42.00% | 0.3158 | 0.3111 | 48.00% | 0 |

- 正式集 50 条全部成功；探索集 20 条硬校验未混入；两种协议差异 +8pp R@1 / +12pp R@5 / +0.116 MRR。
- 分来源（主协议）：verified 49 条 R@5=53.06%，新确认 junior 查询 1 条 R@1=100%。
- 耗时：服务初始化 444.4ms；评测总 2603.4ms；主协议 p50 16.15ms / p95 32.47ms；open p50 16.90ms / p95 30.11ms；0 API 调用、¥0。
- 未命中 23 条（16 条未入 top10、7 条 6–10）；主要归因：单位/通用词关键词噪声、长题干稀释向量、小学规则 KP 描述过短；详见 `data/staging/retrieval_baseline_analysis.md`。
- **对照 §4.5 目标三项均未达标**；本轮未修复检索逻辑、未改 Gold、未改指标口径。

### Phase 2 人工验收材料（2026-11-15，历史过程记录；结论已由决策㉜关账取代）

> 以下为验收过程材料；最终结论以上方「Phase 2 关账」节为准。

| 材料 | 位置（staging，不入 Git） | 状态 |
|---|---|---|
| 验收材料总索引（正式集 / 探索集分列） | `data/staging/PHASE2_ACCEPTANCE_INDEX.md` | ✅ |
| **正式评测集 50 条** = 49 条 Gold v2 confirmed verified 派生（全量，不排除）+ 1 条新确认查询（`h_kp_j8_1408`，user_ruling_2026-11-15） | `data/staging/retrieval_eval_queries_formal_v1.jsonl` | **已确认冻结**（基线测试待授权执行） |
| **额外探索集 20 条**（原手写草稿全部保留，`usage=exploratory`、`formal_metric=false`） | `data/staging/retrieval_eval_queries_extra_drafts.jsonl` | 不转正、不入正式指标 |
| 正式集完整核验指南（A 区 49 条逐条含题干全文/Gold ID+名称/来源/溯源；B 区 1 条已确认查询含裁决记录；附录 A 补充此前被排除的 19 条完整信息并标对应正式编号；附录 B 探索集清单；附录 C 修改建议未执行） | `data/staging/retrieval_eval_queries_review.md` | **待您裁决**（B 区草稿未转正） |
| 数量核对与哈希证据（正式 50 全部 confirmed / 原有 49 条 Gold 未变 / 探索 20 / ID 唯一 / Gold primary 全在图谱 / sha256 前后一致） | `data/staging/retrieval_eval_set_counts.json` | ✅ |
| 20 条教师问句实跑结果（5 类 × 4；真实系统 Top-5 知识点融合分/题目重排分；已知限制 4 条如实记录） | `data/staging/teacher_queries_20_results.md` | **待您评分**（1–3 分 + 备注留空） |
| 内存偏差待裁决报告（FP32 保留 / INT8 未启用 / 0.99 阈值未改；2C2G 余量 + 30 次并发探针证据） | `data/staging/memory_deviation_report.md` | **待您裁决** |

> 说明：本次修正了评测集构成偏差（原 30 verified + 20 draft → 正式集 49 verified + 1 新确认查询）；**2026-11-15 用户裁决 `h_kp_j8_1408` 通过转正**（primary=kp_j8_1408、辅助无、查询原样），正式集 50 条全部 confirmed；原有 49 条 Gold 未改动；旧混合文件只读保留、已被取代；未修改检索运行时代码；未计算正式 Recall/MRR；未 push、未关账。题目重排弱 KP 占位问题作为已知缺陷记录，待正式基线后另行裁决修复范围。

**正式基线测试准备**：`backend/scripts/eval_retrieval_baseline.py` 已就绪，`--dry-run` 校验通过（正式集 50 条全部 confirmed / 49+1 / 探索集 20 条未混入）；**尚未执行指标计算**，执行需用户明确授权。指标口径：Recall@1/5（primary + any gold）、MRR（primary, top10）、NDCG@5（分级）；协议 stage_filtered（主）/ open（诊断）。

## Phase 2 步骤看板（时间盒 1–1.5 周，决策㉚⑥纪律）

### Phase 2 步骤看板（时间盒 1–1.5 周，决策㉚⑥纪律）

| S1 中文 FTS5 分词（day-1） | ✅ 完成（见上节） |
| S2 引用注入骨架（day-1） | ✅ 完成（见上节） |
| S3 三阶段检索 + embedding | ✅ 完成（见上节） |
| S4 NL 问数 | ✅ 完成（见上节） |
| S5 任务持久化 + 多轮会话 | ✅ 完成（见 S5 证据表） |
| S6 认证+限流（中段） | ✅ 完成（见 S6 证据表） |
| S7 前端 + 验收收尾 | ✅ 助手侧完成（见 S7 证据表）；**待用户**：50 查询集核验 / 20 问句评判（§4.5 收口） |


## Phase 1 关账与 Step 5 冻结（2026-11-14，决策㉚）

用户确认收尾分法：**按"Phase 2 依赖"而非"完美"定义收尾**——C 类零返工风险项全部冻结，B 类防被动项当日完成。

| 动作 | 状态 |
|---|---|
| 统一 push | ✅ 22 个 commit 上云（`f5c1bbe..b60a203`，main 与远程同步；SSH 经 TUN 拦截→用户关闭 TUN 后成功） |
| Step 5 冻结 | ✅ prompt v2 重跑/全量标注/1070129 治理/079 定义修改/B 臂投入——不执行；解冻需用户批准（论据与依据见决策㉚） |
| 验收口径修订 | ✅ 「打标抽检合格率 ≥90%」→「打标管线与试跑协议完成即达标，全量标注按需执行」（81.6%+治理循环=叙事资产） |
| 未决项落定 | ✅ 1070129 保留 pending 转噪声候选（面试素材：发现数据集 ans↔题干不一致）；079 定义建议保留不执行；87882 维持裁决；B 臂冻结 |
| verified=1 种子 | ✅ 49 题人工核验题置 verified=1（决策⑲；Phase 4 评测 golden 就绪，pending 1070129 未标记） |
| 简历素材清单 | ✅ `docs/development/RESUME-AMMO.md` 初稿（3 个 Phase 1 故事 + 数字 + 追问点 + 缺口检查：RAG 未做=Phase 1 时最大缺口；Phase 2 已完成） |
| Phase 2 排雷 | ✅ ROADMAP Phase 2 行载入开工纪律：中文分词 day-1 / 引用注入 day-1 / 认证限流中段 / 时间盒 1–1.5 周 / BGE 复用 |

## Step 4 完成证据（2026-11-11，全部实测）

| 项 | 结果 | 证据 |
|---|---|---|
| 数据源核验 | ✅ 官方仓库 `Chenny0808/ape210k` clone（commit 52c52eb），三文件 SHA-256 校验并硬编码进 ETL 脚本（不匹配拒绝处理）；字段结构/规模/许可状态如实记录 | `docs/development/DATA-SOURCES.md`；许可模糊（无 LICENSE、README 仅覆盖代码、原论文撤稿称数据不公开）经用户裁决=决策㉒：本地使用不设限制、GitHub 不提交数据集本身 |
| ETL 漏斗 | ✅ 原始 210,488 → 完全重复剔除 2,539 / 验算不一致 rejected 91 / 疑似变式保留 244 → **processed 207,858（98.7%）+ rejected 91（入库可追溯）**；验算一致率 99.96%（可验算部分），无法验算仅 1,941（0.92%，不惩罚题目）。⚠️ 方案 A 修复后"题干无效 6→0"：原 6 行（`16*25．` 等巧算题）是被旧规则删星号变短才误判无效——修复后正确放行（+6），非灌水 | `data/staging/etl_ape210k_report.json` + stdout 实录（修复前后各 2 轮数字一致） |
| 清洗能力 | ✅ 百分数（7,202 行）、带分数 `n(a/b)` 记法解析；AST 白名单安全求值（`__import__` 注入拒绝、大指数防护）；验算 mismatch 才判 rejected；**星号规则已按方案 A 修复（决策㉓）：乘号 `*` 与指数 `**`（含 `cm**3`/`米**2`/`(-1)**2`）原样保留，仅清行首/句末残留 3 行，不做记法转换** | `backend/app/data/ape210k.py` 纯函数 + 28 项单测（含 B 类真实样本回归 11 例） |
| 小学 KP 聚合 | ✅ **82 个规则推导 KP**（kp_p_001..082，source='ape210k_rule'，独立初中图谱）；覆盖 204,231 题（**98.3%**），均 2.8 KP/题；映射不确定不硬挂；零命中 2 个（和倍/差倍，奥数术语不入课标题干）如实报告 | `backend/app/data/primary_kp.py` 规则表入 Git；`data/staging/primary_kp_report.json`；初中 303 KP 重跑前后不变 |
| FTS5 | ✅ processed 一一对应 207,858 行；rejected 不进索引（测试断言）；全量重建语义；MATCH 查询 0.2–0.4ms。⚠️ unicode61 分词：连续中文串整体成 token，中文子词不可查（完整串/数字可查），语义级中文检索属 Phase 2 分词优化 | `test_etl_ape210k.py` 同步测试 + 实测基准 |
| 幂等性 | ✅ **全量重建语义**（非 INSERT OR IGNORE）：每轮先清 q_ape_% 的 questions/FTS/q_kp 再按当前规则写入，清洗规则改进重跑即生效；修复后 2 轮重跑终态逐数一致 | 修复后第二轮断言 PASS：processed 207,858 / rejected 91 / fts 207,858 / 挂接 571,364 / KP 82 / 初中 303 |
| 完整率抽样 | ✅ **seed=42、同一样本集（v3 起固定沿用 v1/v2 的 100 q_id——总体 +6 会使重抽偏移，按验收要求保持原样本集）；完整率 100/100**（判定项 A–F，D 项 v3 起按"无残留形态星号"判定——乘号/指数为合法记法）；99/100 题至少挂 1 个小学知识点；星号保留 10/10 | 清单 `data/staging/ape210k_sample100.md`（v3 方案 A 修复后版，含题目内容与 B 类恢复前后对照，**不入 Git**）；`backend/scripts/sample_questions_audit.py` 可复现 |
| 性能 | ✅ ETL 全程 17.4s（21 万行流式）；库 182.7MB；FTS MATCH 0.2–0.4ms、KP JOIN 1.7ms、全库 KP 聚合 GROUP BY 234ms、processed 计数 43ms | 实测基准（各 3 次取均值） |
| 测试基线 | ✅ **159 passed / 0 failed**（98 基线 + 8 存储 + 8 图谱 + 17 导入 + 28 ETL，含方案 A 回归 13 例） | 全量 pytest（2026-11-12 修复后） |

**实现要点（需知悉）**：①rejected 题**入库**（quality_state='rejected'，决策⑲可追溯语义），FTS5 只索引 processed——检索/组卷天然只见 processed；②幂等是全量重建语义：重跑 ETL 后需重跑 `build_primary_kp.py`（KP 挂接随重建被清），两脚本顺序固定即复现链；③难度 1–5 为规则粗标（按 equation 运算符个数）、score 统一占位 1.0（数据集无分值字段）——**Step 5 LLM 打标将覆盖难度**，分值占位不代表卷面语义；④技术方案 §3.1"APE210K 自带知识点标签"预设落空（实测无标签字段），小学 KP 口径改为规则推导（决策㉒）；⑤两个零命中 KP（和倍/差倍问题）保留在规则表中（合法知识点，供未来 LLM 打标复用），未硬凑映射。

## Step 4 复核发现与修正（2026-11-12，用户抽样反馈）

用户复核 v1 抽样报告发现"题干截断"，对**同一样本集**（seed=42，与 v1 q_id 集合校验一致）做原始数据 vs 库内数据逐字段全量核查，结论：

| 类别 | 样本 100 题 | 全库 210,488 | 说明 |
|---|---|---|---|
| C 报告展示截断 | 53 题 | — | v1 审计代码 `stem[:40]` 展示截断（**仅报告缺陷，数据无损**）；v2 已改为完整展示并重生成报告（`sample_questions_audit.py` 重写：逐题块状、原始/库内全字段对照、三类自动分类） |
| B ETL 清洗损坏 | 10 题 | **11,032 行（5.24%）** | 清洗规则 `\*+` 全量删星号，误删合法**乘号**（`27*324`，7,773 行）与**指数记法**（`cm**3`/`1**2`，3,399 行）；仅损 stem 文本，answer/equation/验算/KP 挂接均无损；FTS 中这些题的 stem 索引同受影响 |
| A 原始数据质量 | 1 题（q_ape_72366：题干不完整 + x=1 退化占位） | 真残留 3 行 + 非标准记法 150 行 | 源数据自身缺陷，与 ETL 无关 |
| X 意外不一致 | 0 | — | 库内 = normalize(原始) 100/100 成立——损坏源于清洗**规则本身**，非管线实现 bug |

- B 类样本 q_id：q_ape_678403、q_ape_1060185、q_ape_58143、q_ape_209048、q_ape_230513、q_ape_290121、q_ape_942935、q_ape_1200515、q_ape_634755、q_ape_87500
- **v1 "3,368 行 Markdown 残留"定性错误已修正**：题干 `**` 绝大多数是指数记法（与 equation 幂语法一致），真残留仅 3 行——DATA-SOURCES.md 已更新
- 完整率结论不受影响：100/100 判定基于完整题干（A–F 字段口径），与展示截断无关；KP 挂接 99/100 复核不变
- ~~修复方案待用户裁决~~ → **用户已裁决采用方案 A（2026-11-12，决策㉓）并已实施完成**，见下节

## Step 4 方案 A 修复完成证据（2026-11-12，全部实测）

| 项 | 结果 | 证据 |
|---|---|---|
| 清洗规则 | ✅ `RESIDUE_STAR_RE = (^|[。．？！？])\*{2,}`：仅清行首/句末 ** 残留（全库实测 3 行：95177/128301 校名打码、36130 尾部），乘号与指数（含 `(-1)**2` 括号底数、`米**2` 中文底数）原样保留；不做记法转换 | `backend/app/data/ape210k.py`；docstring 记录规则与全库实测依据 |
| 回归测试 | ✅ 用户指定 7 记法用例（27*324/123*456/cm**3/2**3/3.14/75%/1/2）+ 残留清除用例 + **B 类真实样本 11 例逐字回归**（抽样 10 题 + 库内同形态 325488）全过 | `backend/tests/test_etl_ape210k.py`（16→28 项） |
| 全量重建 | ✅ 从原始文件重跑（SHA-256 门禁通过；全量重建语义；Schema 未动；原始数据文件未动）；ETL 18.8s + KP 9.7s；**漏斗唯一变化：题干无效 6→0、processed 207,852→207,858（+6）**——原 6 行是被旧规则删星号变短才误判"无效"的巧算题（`16*25．`/`36*5*4．` 等，验算全 match），修复后正确放行 | ETL/KP stdout 两轮一致 |
| 全量数据审计 | ✅ **21 万行独立重放 + 逐行比对 0 不一致**（stem/answer/analysis/difficulty/quality_state 全字段）；题干差异分类：无变化 207,944 / 仅残留清除 3 / 仅空白归一 2（**再无其他任何差异**）；**恢复 11,054 行**（旧规则删星号、新规则保留）；含星号行 11,057 = 乘号 7,701 + 指数 3,359 + 非标准记法 162（循环小数 `**(•)`、车牌打码 `川A*****` 等，保守保留，q_id 清单见 JSON）+ 残留清除 3（去重有重叠） | `backend/scripts/verify_etl_integrity.py`（新增，可复现）+ `data/staging/etl_integrity_report.json` |
| 汇总核对 | ✅ 库内总量 207,949 = 漏斗重算；FTS 207,858 = processed；B 类 10 题 FTS 同步且含星号；小学 KP 82 / 挂接 571,364；初中 303 不变 | verifier 6 项 checks 全 ✓ |
| 数据层恢复证据 | ✅ 10 道 B 类题库内实测全部恢复（如 q_ape_230513 库内 = `因为27*324=8,748，所以0.27*3.24=多少`；q_ape_678403 = `…cm**3？`）；FTS 短语查询 `"8 748"` 命中 q_ape_230513 | v3 报告"B 类恢复核验"节（修复前/后逐字对照）；上文 stdout 实录 |
| 抽样复核（v3） | ✅ 同一样本集（固定沿用 100 q_id，校验一致）：完整率 100/100、KP 99/100、星号保留 10/10、**B 类 0**（修复前 10）、A 类 1（q_ape_72366 源数据缺陷）、C 类 53（v1 展示截断史，数据无损）、X 类 0 | `data/staging/ape210k_sample100.md` v3 |
| 测试 | ✅ **159 passed / 0 failed**；幂等 2 轮逐数一致 | 全量 pytest + ETL/KP 重跑 |

**注意事项**：①修复前 STATUS 曾预判"漏斗计数不变（dedup 键不含星号）"——dedup 确实不变，但题干长度校验与星号保留相互作用使 +6 行放行，属修复的正确效果（旧"题干无效 6"本身是损坏假象）；②审计 v3 起样本集固定沿用上一版报告 q_id（总体变化会使 seed 重抽偏移），`--fresh-sample` 可重抽；③完整率判定项 D 语义更新为"无残留形态星号"（乘号/指数不再判不完整），口径意图不变。

## Step 4 补充复核（2026-11-13，用户验收反馈，只读不改数据）

### 1. 全库原始异常内容扫描（新工具 `backend/scripts/scan_raw_anomalies.py`，SHA-256 门禁）

| 发现 | 题数 | 定性与依据 | 建议（待用户裁决） |
|---|---|---|---|
| 空括号填空标记 | **1,684**（18 种形态，主形态 `((())/(()))` 1,471） | **数据集填空题空位标记，非损坏**：题目带真实 equation/ans 可解可验算（验算 match 1,682 / unverifiable 2），空位即"待填空格"；主形态为"答案为分数"的填空模板，其余为补全分子/分母等变体（如 `(6/(()))=((())/28)`）；库内镜像 1,666 行 processed + 18 行重复剔除未入库 | 保留 processed；显示层渲染填空线；Step 5 打标 prompt 说明该形态；是否做文本替换由用户裁决 |
| 退化算式 x=0/x=1 | **585**（ans 同值 582 / 异值 3） | 疑似退化占位记录（DATA-SOURCES 已知噪声第 5 项的全量量化，q_ape_72366 同型）；**部分可能合法**——如 q_ape_7646「2.78×0.36 保留两位小数」eq=x=1.00/ans=1.00 为正确答案——须逐条人工判定 | 明确无效者可转 rejected（决策⑲可追溯，不物理删除） |
| 题干以非终结符结尾 | **26** | 疑似不完整（如 q_ape_120593 页码题疑似截断、q_ape_241638 以"、"结尾） | 逐条人工判定，处理同上 |
| 锚定参考 | 循环小数记法 128／题干无数字 0／空算式 0 | 与已知噪声记录交叉核对一致 | 无 |

- 用户指定样本结论：q_ape_1012689（eq=x=1-(3/4)、ans=(1/4)）与 q_ape_432326（eq=x=3/5、ans=(3/5)）的 `((())/(()))` 为填空模板空位，验算均 match，**非内容损坏**
- 完整 q_id 清单 + 原始题干 + 库内状态逐行标注：`data/staging/raw_anomalies_report.md` / `.json`（不入 Git，`python -m backend.scripts.scan_raw_anomalies` 可复现）；**未删除、未修改、未变更任何题目状态**

### 2. 漏斗统计口径澄清（etl_integrity_report.json；etl_ape210k_report.json 同语义）

- **verify_match 包含重复数据**：验算发生在去重之前——verify_match 208,456 = 入库 match 205,937 + 重复剔除行 match 2,519
- **三值互斥且完备**：每条 mapped 行恰一个结论；match 208,456 + unverifiable 1,941 + mismatch 91 = mapped 210,488
- **从原始到入库的推导链**：raw 210,488 − 解析失败/题干无效/缺 id 0 = mapped 210,488 → −完全重复 2,539 = **入库 207,949 = processed 207,858（match 205,937 + unverifiable 1,921）+ rejected 91**（重复行 mismatch 0，故 rejected 全部入库）
- **verify_match ≠ processed 的原因**：①含重复行；②unverifiable 也按 processed 入库——208,456 − 207,858 = 598 = 2,519（重复 match）− 1,921（入库 unverifiable），精确闭合
- **报告修订（口径歧义已消除，数据未动）**：`verify_etl_integrity.py` 键名 `verify_mismatch`→`verify_mismatch_rejected`（与 ETL 报告对齐）；新增 `dup_*`/`written_match`/`written_unverifiable` 拆分、`derivation` 推导块与 `semantics_notes` 说明；新增两项守恒 checks（"processed=match+unverifiable 入库拆分"、"mismatch 三分守恒"均 ✓）。ETL 脚本本身未改未重跑

### 3. 小学 KP 规则过度挂接记录（本项未改动任何 KP 数据）

用户抽检 5 例全部复现自当前规则表（`match_kps` 重跑结果与库内 q_kp 挂接一致）：

| q_id | 题干要点 | 误挂 KP | 根因规则 |
|---|---|---|---|
| q_ape_1139203 | 橙汁平均分给学生 | 人民币计算 kp_p_065 | `any_=["元","角","分"]` 单字"分"命中"分给/分到" |
| q_ape_8983 | 25 人选拔 3 人表演节目 | 人民币计算 | "分别表演"的"分" |
| q_ape_173618 | 梯形**周长** | 梯形面积 kp_p_048 | `any_=["梯形"]` 无"面积"约束 |
| q_ape_181624 | 两池注水 | 一般行程 kp_p_010 + 人民币计算 | "以每分钟2吨的速度流入"——"速度"语境歧义（非行程）+ "分钟"的"分" |
| q_ape_93401 | 植树+车速 | 人民币计算 | "每分钟"的"分"（其余挂接合理） |

全量影响面（下界）：**人民币计算 60,180 挂接中 33,131（55%）题干完全无 元/角/钱/币**——纯"分"误触发（含"角"者还有"三角形/直角"等非货币误中，真实误挂率更高）；**抽屉原理 663 挂接 100% 由"至少/保证"触发**（无一题真含抽屉）；梯形面积 1,760 中 80 行无 面积/上底/下底/高（周长类）。

**结论与 Step 5 要求**：规则挂接是"关键词出现"级证据、无语境判断能力，系统性过度挂接成立。**Step 5 LLM 打标必须独立判断知识点，不得以现有 q_kp 挂接为标签来源或 prompt 输入**；规则挂接仅可用于人工复核对比与规则改进候选（规则表编号冻结，任何修改需用户另行裁决）。

## Step 4 最终裁决与收尾（2026-11-13，决策㉔）

用户对补充复核报告逐项裁决，**Step 4 正式关闭**：

| 裁决项 | 内容 |
|---|---|
| 空括号填空标记 1,684 题 | 数据集原生填空标记（非损坏），全部保留 processed；原始标记不动，展示层负责渲染填空线 |
| 退化算式 585 + 非终结符结尾 26 | 全部保留（不自动删除、不转 rejected）；记录为**待复核候选**（q_id + 原始题干见 `data/staging/raw_anomalies_report.md/.json`，`scan_raw_anomalies.py` 可复现），供后续质量治理 |
| q_ape_72366 核实 | **不在** 26 道非终结符清单（题干以"．"结尾，不在判定集），**在** 585 道退化算式清单（equation=x=1/ans=1，库内 processed、验算 match）；题干「大鸡只数的(4/5)相当于小鸡只数．多少．」缺关键条件（小鸡只数），为 DATA-SOURCES 已知噪声第 5 项代表例 |
| 漏斗基线（冻结） | Raw 210,488 / processed 207,858 / rejected 91 / duplicate 2,539 / DB total 207,949；verify_match/unverifiable/mismatch_rejected 三值互斥、去重前计数语义与拆分见"补充复核"第 2 节 |
| KP 规则标签定位 | 82 个规则推导挂接=**候选标签，非可信标注**；本轮不改不删；Step 5 独立打标五条约束（不作为 prompt 输入/不直接继承/限 82 体系内语义匹配/溯源必留/来源可区分）见决策㉔ |

- 收尾动作仅文档与状态：ETL 核心逻辑/数据库内容/Schema 未动；测试 **159 passed**；本地 commit（不 push）
- 审计报告保留（均不入 Git，脚本可复现）：`raw_anomalies_report.md/.json`、`etl_integrity_report.json`、`ape210k_sample100.md`、`etl_ape210k_report.json`、`primary_kp_report.json`

## Step 5 开工前需确认事项（2026-11-13 整理，待用户批准后开工）

1. **打标对象与范围**：初中题（Step 3 F16 导入）与/或小学题（processed 207,858）？打标字段范围：知识点（小学限 82 体系 / 初中 303 体系）+ 难度（覆盖规则粗标 1–5）？题型/分值是否处理？
2. **溯源落库设计**：LLM 标签需保留 标签来源/模型版本/Prompt 版本/置信度/审核状态（决策㉔）——q_kp.source 可区分规则（'ape210k_rule'）与 LLM 来源，但模型/Prompt 版本与置信度字段涉及 Schema 扩展，**需另行报批后实施**
3. **DeepSeek API Key**：开工前核验存在且可用（不打印内容）；成本两道关口协议=决策㉑
4. **试跑协议细节**：500 题分层抽样的分层维度（学段/来源/难度/题型）；人工抽检 ≥50 题合格率 ≥90% 的判定标准细化
5. **Prompt 版本管理**：prompt_version 命名规则与 manifest JSONL 存放位置（决策⑳/㉑）
6. **填空标记说明**：LLM Prompt 中对 `((())/(()))` 等空位形态的说明文字（题干含待填空格，非缺失内容）

## Step 5-B1 工程实施完成证据（2026-11-14，全部实测，未调任何真实 API）

| 项 | 结果 | 证据 |
|---|---|---|
| V2 迁移 | ✅ 新增 5 表（annotation_batches / q_annotation_results / q_kp_llm / q_difficulty_llm / annotation_reviews）+ 2 视图（v_effective_batch / v_q_kp_effective）+ 8 索引；真实库 `data/db/edumate.sqlite` 已应用（schema_migrations=[1,2]）；**数据零改动核验：questions 207,949（processed 207,858）/ q_kp 571,364 / KP 385 不变**；重复应用幂等（版本恒 2）。索引名冲突一次（V1 已占 idx_batches_status）→ 重命名 idx_ann_batches_status | `backend/app/storage/migrations_v2.sql`；`db.py` MIGRATIONS/EXPECTED_TABLES |
| 题级结果表 | ✅ 不可映射（unmapped）作为有效终态持久化（q_annotation_results，PK(q_id,batch_id)）；消费视图实测：unmapped 题输出 tag_source='llm_unmapped' 且**零规则回退**（真实库当前 571,364 行全为 rule 回退态，符合"尚无 LLM 结果"预期）；failed/invalid 中间态亦落行，重跑 INSERT OR REPLACE 覆盖 | `test_annotation_schema.py::test_view_unmapped_never_falls_back_to_rule` 等 13 项 |
| 执行器 | ✅ `backend/app/annotation/`（executor/client/pricing/prompts/recall 五模块）：批次 UNIQUE 幂等键 + 断点续跑（ok/unmapped 跳过）+ 指数退避重试 + 纠错重试 + AIMD 并发（初始 4/上限 8、连续成功缓升 +1、**429 降半至下限 1**）+ budget_group 级预算台账（无缓存最坏口径预留、80% 告警暂停、100% 硬停、实测费用不越 cap）+ manifest（Git）/逐题明细（staging）双层落盘 | `test_annotation_executor.py` 21 项（全部 Mock 客户端） |
| Prompt v1 | ✅ A 臂 `kp_primary_v1.md`（82 清单占位符+特殊形态说明+难度锚点+3 例 few-shot）/ B 臂 `kp_recall_v1.md`（候选可能不全、禁止硬凑）；`registry.jsonl` 记录 sha256（篡改即拒绝运行）；**规则标签零泄漏**（全文不含 q_kp/ape210k_rule/规则挂接，测试断言） | `data/annotations/prompts/`；`test_annotation_prompts.py` 7 项 |
| 500 题试验集 | ✅ 18 层全部足额（40/30/10/20/10/10/40/15/15/30×5/20/10+10/120），processed 口径池实测（S3 剩余 304/S6 剩余 594 系 S1 优先互斥消耗所致）；**确定性复跑 sha256 一致**；冻结清单仅含 q_id+层标签（决策㉒） | `data/annotations/trial500_qids.jsonl`（Git）；`build_trial500.py` 输出实录 |
| 核验表导出器 | ✅ 50 题盲评选样（S12≥15、S1/S2/S7≥5、余量配额轮转），稳定种子（zlib.crc32，不依赖进程 hash） | `backend/scripts/export_review_sheet.py` |
| 测试基线 | ✅ **204 passed / 0 failed** = 159 基线 + 45 新增（schema 13 + prompts 7 + executor 21 + builder 4）；存储基线 8 项随 V2 演进（版本断言 [1,2]） | 全量 pytest（2026-11-14） |
| API 隔离 | ✅ 执行器/测试**不读取任何环境变量与 API Key**（依赖注入 ChatClient）；真实 Key 仅在 CLI `from_settings()` 存在性检查（不打印）；B 臂无召回器 fail-fast（RecallNotAvailableError，不静默降级）；`tag_questions.py` 不自动执行、无预算 cap 拒绝运行 | `client.py` 设计 + `test_processed_only_guard_before_batch_creation` / `test_real_recaller_placeholder_fails_fast` |

**已知问题与遗留风险**：①B 臂 embedding 召回未实现（占位 fail-fast，待 onnxruntime/fastembed 依赖批准后 5-B2 落地）；②Mock 通过 ≠ 真实 API 验收——真实 usage/tokenizer/缓存命中率/429 行为均待 5-B2 实测；③`.env` 是否覆盖 DEEPSEEK_MODEL 默认值未核验（5-B2 开工时确认）；④manifest 的 batch_end 为 run 级口径（续跑合并累计值在批次行）。

## Step 5-C 阶段 B：Gold v2 裁决落地（2026-11-14，决策㉙；零 API）

| 交付 | 状态 |
|---|---|
| Gold v2 终版 | ✅ `trial50_gold_v2.jsonl`：**confirmed 49 + pending_verification 1**（q_ape_1070129）；逐行含 verdict_source（user_ruling/carry_over_verified）与 v1 对照字段；15 争议题按用户裁决落定（**3 处与提案不同如实记录**：87882 改判 081 主、77461 不吸纳 078、577597 暂不吸纳 079）；35 沿用题轻量独立核验 35/35 通过（2 题带源数据备注） |
| 1070129 核查 | ⚠️ **未通过**：原始 equation/ans 自洽（验算 match）但 ≠ 题干数学真值（裂项 Σ=18.9 vs ans=3(17/90)）——源数据缺陷（题干↔算式不一致噪声的真实案例）；该题不进指标分母，处置待裁决 |
| KP 定义修订 v2 | ✅ 4 条描述补边界句（037/036/082/071，077 不改）；**kp_registry.jsonl 登记 kpdefs_v1→v2**（sha256 f7dcb257…→ff1728e1…）；primary_kp.py 源头同步（幂等重建不回退）；DB/题目/挂接零改动；prompt 文件零改动（KP_LIST 运行时注入） |
| 079 定义边界 | ⚠️ 核查结论：现定义未明确覆盖多步纯加减（1216385 落 079 主与定义字面存在张力）——**建议补「含两步及以上同级运算」待您裁决，未自行修改** |
| 复算冒烟 | ✅ Gold v2 终版 × v1 试跑数据：A 臂 pass **81.6%**（44/49 主命中、micro-P 0.897、消歧 86%）/ B 臂 63.3%——比提案冒烟回落 6.4pp（三处裁决差异如实反映）；距 90% 线差 4 题=prompt v2 重跑目标 |
| 边界遵守 | ✅ 未调 API、未重跑、未启动 Step 6；未决事项 4 项明确标记不伪装确认 |

**等待用户**：①裁决未决事项（1070129 处置 / 079 定义建议 / 87882 辅助取舍）→ Gold v2 完全冻结；②批准 A 臂 v2 重跑（届时单独授权 API）。

## Step 5-C 核验准备材料完成（2026-11-14，零 API / 零修改）

| 交付 | 状态 |
|---|---|
| 人工核验指南 | ✅ `data/staging/trial50_gold_v2_review_guide.md`：**50 题全部展示**（15 争议题四问详解：为何改/不改的影响/修改的新风险/其他合理判断 + 35 道沿用题明确标注「沿用 V1，待人工确认」不默认视为已确认）；每题含 q_id/题干/参考答案（questions.answer）/Gold v1 与 v2 提案（**含 KP 名称+定义**）/A/B 臂预测/修改理由/裁决栏（四选一模板+主辅填写位）；字段缺失原样标注未补造 |
| 一致性检查 | ✅ 自动核验全过：50 qid 与冻结抽样集 trial500 一致、无重复遗漏、全部 KP ID 真实存在于知识图谱（385 个）、Gold v1/v2/A/B 逐题同源对应、v2 全程标注「提案」未标正式 Gold；检查逻辑有 5 项单测守护 |
| 边界遵守 | ✅ 未调 API、未重跑、未改 Prompt v1/v2、未改知识图谱与 KP 定义、未改 Gold v1、未扩抽样、未开始 Step 6 |

**等待用户**：按指南逐题裁决 50 题（重点 15 争议题）→ 助手按裁决落定 Gold v2 终版冻结 → 之后方可申请 A 臂 v2 重跑授权。

## Prompt v2 阶段 A 实施完成（2026-11-14，决策㉘；500 题重跑未授权）

| 交付 | 状态 |
|---|---|
| Gold v2 修订提案 | ✅ `trial50_gold_v2_draft.jsonl`（15 题修订提案：P1 吸纳辅助 8 / P2 primary 裁决建议 6 / P3 按定义修订 1 + 35 题沿用）+ `trial50_gold_v2_revision_record.md`（逐项裁决表 + **KP 描述修订建议 5 条单独提交、未执行**）；**v1 盲标初稿原样保留未覆盖**；待用户逐项确认后冻结终版 |
| Prompt v2 | ✅ `data/annotations/prompts/kp_primary_v2.md`：主知识点消歧规则（应用题具体考点优先/表层关键词不决定 primary/4 组相邻边界/077-079 临时消歧）+ 辅助允许为空须有题目依据 + 反例 few-shot 2 例；**难度锚点与 JSON Schema 逐字不变**（不变式测试断言）；registry 追加 v2.0（sha256 `7ead4329…`），v1 条目保留可复现；B 臂 prompt/KP 定义/知识图谱零改动 |
| 指标升级 | ✅ micro-P/micro-R 已有；**新增 primary 消歧准确率**（相邻族口径，ADJACENT_GROUPS 5 组源自归因邻近对） |
| 新增测试 | ✅ 12 项：v2 指纹/内容/难度锚点不变式/Schema 结构兼容/无泄漏、**v2 独立批次隔离（v1 结果零覆盖）**、消歧指标 5 项；全量 **222 passed** |
| Gold v2 复算冒烟 | ✅ 用 v2 提案 gold 复算 v1 试跑数据：**A 臂 pass 68%→88%、micro-P 0.80→0.93、micro-R 0.824、消歧准确率 88%**；B 臂 58%→64%——归因结论（初稿 Gold 偏窄）获数据验证 |
| 重跑成本预估（未授权） | 最坏口径（无缓存高峰、实测 token：509 次 × ≈¥0.00875）≈**¥4.45**；组累计最坏 2.85+4.45=**¥7.30 < 暂停线 ¥15 < 硬停线 ¥20** ✓；缓存态预期 ≈¥1.1 |
| 批次隔离验证 | ✅ UNIQUE(scope,task_type,strategy,model,prompt_id,prompt_version) → v2.0 独立批次；专项测试证明 v1 批次与结果零覆盖；组级台账含 v1 支出（防绕过测试既有） |

**等待用户**：①Gold v2 逐项裁决（修订记录表 15 项）→ 冻结终版；②KP 描述建议 5 条裁决（可选，单独执行）；③批准 500 题 A 臂 v2 重跑（届时单独授权 API 调用，命令与预算检查已就绪）。

## Step 5-B2 裁决与质量验收材料（2026-11-14，决策㉗）

**用户裁决**：①B2 验收确认，commit `0cbe689` 保留；A/B 试验数据、Manifest、费用记录、测试结果=**实验基线，不覆盖不删除**；②A 臂保留主线，B 臂 recall@10=62% 暂停投入、不作主线；③批准 Gold 核验阶段——助手盲标初稿保留不覆盖、最终 Gold 由用户确认；④不调 API、不改题库标注；⑤批准"收紧多标签输出"方向但暂不实施，先出 prompt v2 方案待审；⑥状态=Step 5 工程实施完成、质量验收待完成。

**交付材料（均 staging 不入 Git）**：

| 材料 | 路径 | 内容 |
|---|---|---|
| 核验材料包 | `trial50_review_pack.md` / `.json` | **15 道硬误标逐题详解**（题干/初稿 Gold/A 输出/B 输出/差异/助手归因/建议核验点）+ 35 题简表 + 操作指引 |
| 归因结论 | 材料包 §0 | **P2 primary 分歧 6 题**（相邻 KP 无消歧规则/表层关键词偏置，如 q_ape_1086627 行程表层 vs 归总本质）+ **P1 辅助可辩护 8 题**（初稿 Gold 偏窄）+ **P3 KP 定义张力 1 题**（077"一步"定义 vs 两步题） |
| 核验操作面 | `trial50_review_sheet.csv` | gold 列已填助手初稿（逐行标注"请复核/修订"）；您修订后即终版 |
| 复算工具 | `build_gold_review_pack.py` / `trial_metrics.py` / `eval_recall.py` | gold 终版后不调 API 一键复算全部指标 |

**关键归因洞察**：硬误标主因不是模型"过度打标"——8 题辅助标签有题目依据（初稿 Gold 偏窄）、6 题是相邻 KP primary 选择缺消歧规则。**单纯收紧标签数量会误伤有依据的辅助**；正确药方=primary 消歧规则+辅助依据说明（Prompt v2 方案 §14：V2-1 消歧/V2-2 依据说明/V2-3 反例/V2-4 难度不动/V2-5 micro-P 一级指标，含预期改进/副作用/验证办法与前置顺序）。

**下一步（等待用户）**：①复核 50 题 Gold（重点 15 题）→ 终版；②裁决 P3 KP 描述修订（077/079 边界等，可选）；③审查 §14 方案 → 批准后实施 v2 → A 臂同题重跑（预计 +¥1.1，需届时批准）→ 复算 ≥90% 关口。

## Step 5-B2 试跑完成证据（2026-11-14，真实 API，预算组 trial500）

| 项 | A 臂（主线·82 全清单） | B 臂（对照·召回 top-10） |
|---|---|---|
| 批次 | ann_20261114_01_a | ann_20261114_02_b |
| 运行结果 | ok 492 / unmapped 8（1.6%）/ failed 0 / invalid 0 | ok 361 / unmapped 139（27.8%）/ failed 0 / invalid 0 |
| 耗时/调用 | 217s / 509 次 / 并发满 8 / 0 限流 | 267s / 509 次 / 并发满 8 / 0 限流 |
| Token | 入 1,271,911 / 出 238,857 / **缓存命中率 91.8%** | 入 606,538 / 出 294,836 / **缓存命中率 3.5%**（候选块逐题变化，预判证实） |
| 费用 | ¥1.083（≈¥0.00217/题） | ¥1.765（≈¥0.00353/题）；**组累计 ¥2.848**（¥10/¥15/¥20 三线均未触发） |
| 50 题主知识点命中 | **88.0%**（44/50） | 66.0%（33/50） |
| 难度一致（±1） | **98.0%**（49/50） | 76.0%（38/50） |
| 合格率（工程放行口径） | **68.0%**（S12 纯随机层 58.8%） | 58.0%（S12 层 58.8%） |
| micro-P / micro-R | 0.800 / 0.783 | 0.782 / 0.589 |

- **关口结论：90% 放行线未达（A 68%/B 58%）→ 按决策㉑进入治理循环，不进入任何扩大规模**；A 臂主要失分=硬误标（15 题多标签宽松，micro-P 0.80），难度指标优秀（98%）
- **B 臂召回天花板实证**：recall@10 = 62.0% / recall@5 = 52.0%（漏召回 19 题，清单 `staging/recall10_report.md`）——KP 描述平均 13.5 字符短文本召回弱获数据支撑；B 臂 unmapped 27.8% 为模型按 prompt"不硬凑"指令如实弃标
- **成本实测校准**：输出 token 实测 ~470/题（§8 估算 90——模型输出含 3 KP+置信度+理由比预估长）→ 单题 ¥0.00217（A 缓存态，§8 估算 ¥0.00094）→ 全库外推 ≈¥450（缓存态高峰价），仍在 ¥1,000 技术参考内；全库预算批复需按实测重估
- B 臂依赖落地：fastembed 0.9.1 + onnxruntime 1.32.0（venv 锁定入 requirements）；模型缓存实测 90.8MB/内存 273.3MB/单条 2ms（§13 已回填实测值）
- 交付物（staging 不入 Git）：`trial_ab_report.md/.json`（A/B 对照）、`recall10_report.md/.json`（recall@10+漏召回清单）、`trial50_review_sheet.csv`（50 题核验表，**助手盲标 Gold 初稿已填入，待用户确认/修订**）、`trial50_gold_draft.jsonl`
- manifest（入 Git）：`data/annotations/manifests/ann_20261114_01_a.jsonl`、`ann_20261114_02_b.jsonl`
- 实施中修复：CLI 漏传 recaller（`run_annotation` 第 791 行 B 臂参数）一处；EmbeddingRecaller 增推理线程锁
- 测试：**210 passed / 0 failed**（206 + 4 B 臂召回真实推理测试）；API Key 全程未读取/未打印/未落盘

## Step 5-B2 开工检查报告（2026-11-14，等待 B 臂依赖与 Key 注入方式确认）

| 检查项 | 结果 |
|---|---|
| 预算三线实现（决策㉖） | ✅ BudgetLedger 三线改造：¥10 提醒线（budget_remind 事件，通知+记录**不停止**）/ ¥15 暂停线（停止提交新请求、在途排水、批次 aborted 待确认；budget_pause 事件含进度/费用/剩余题量/预计剩余费用）/ ¥20 硬停线（cost+预留 ≥ cap 拒绝一切新调用预留，实支不越 cap） |
| 组级累计防绕过 | ✅ 台账初始值 = 同 budget_group 全部历史批次 cost_cny_est 之和——**重试/续跑/新建批次均无法绕过**；专项测试：A 臂花至暂停线后新建 B 臂批次 → 零调用直接拦截 |
| 断点续跑/Manifest/批次隔离 | ✅ 5-B1 测试全保留通过（续跑跳过已完成、批次 UNIQUE、A/B strategy 隔立、消费视图不混臂） |
| .env 核验 | ✅ **.env 不存在**（仅 compose 注释引用 .env.example）——DEEPSEEK_MODEL 无任何环境覆盖；config.py 默认已切 `deepseek-flash`（仓库内 v4-flash 代码残留清零）。⚠️ 真实试跑需注入 DEEPSEEK_API_KEY：.env 创建或 shell export（config 走 os.getenv，**无 dotenv 加载**；若要 .env 自动加载需新增 python-dotenv 依赖——列入待批清单） |
| 请求模型名确认 | ✅ 打标路径不读 settings：ExecutorConfig 默认 model='deepseek-flash'，CLI 无 model 参数 → 请求模型名恒为 deepseek-flash；批次记录请求名 + 响应 model_version 快照 |
| 两臂同冻结题集 | ✅ 唯一清单 `data/annotations/trial500_qids.jsonl`（sha256 9a0880ac49e5…），CLI 均以 --qids 显式传入；A/B 各建独立批次（strategy 区分） |
| B 臂 Embedding 方案 | ✅ 设计文档 §13 入档：fastembed + onnxruntime（无 torch）；模型 BAAI/bge-small-zh-v1.5（~24M 参数/512 维，量化 ONNX ~30–35MB【待下载实测】）；内存 <300MB【估算】；CPU <50ms/条【§11.2 目标阈值】；**零 API 费用**；top-10 实现与单测方案、recall@10 评估口径已定——**依赖未获批不安装** |
| 测试基线 | ✅ **206 passed / 0 failed**（= 204 + 2 净增，预算三线专项测试 3 项），未降低基线；全部 Mock，无真实 API 调用 |

**执行前尚差两项（等待用户确认）**：①B 臂依赖清单获批（pip：fastembed、onnxruntime；HF 模型下载 ~35MB；磁盘合计 ~185MB【估算】）；②API Key 注入方式（.env 创建 or shell export；python-dotenv 是否引入）。

## Step 5-A 方案设计（2026-11-13，第二版已批准）

- **交付物**：`docs/development/STEP5-ANNOTATION-DESIGN.md`（12 节完整设计：现状问题/范围/三方案对比/架构数据流/Schema V2 提案/500 题分层试验/50 题人工评估/成本测算/可靠性/实施计划/验收标准/风险待裁决）
- **调查基础（实测）**：数据库只读查询（q_kp 分布 571,364 挂接/204,237 题/零挂接 3,621；题干 p50=43/p90=70；type 全库单值 'application'；82 KP 定义合计 1,477 字符；questions 无初中行）；config.py 模型默认 `deepseek-v4-flash`（遗留别名→flash 线）；迁移机制 MIGRATIONS 列表可追加 V2
- **核心结论**：推荐方案 A（LLM 直判全清单 + DeepSeek 前缀缓存）；全库预估费用 ≈¥195（高峰缓存）/≈¥97（空闲）/最坏 ≈¥970【估算，试跑校准】——建议全量空闲时段分夜跑；题型不可标（type 单值）、分值不标；初中题库 0 行自然延后至 5-C
- **本轮边界（遵守）**：未改业务代码、未动数据库与 Schema、未调用/验证任何 API Key、未执行批量任务、未改 82 KP 定义
- **文档内待用户确认事项**：manifest 口径解释（运行级入 Git/逐题明细入 staging）、预算闸门（试跑 ¥10/全量 ¥1,000）、是否双臂对照试跑（A+B，+≈¥1）、模型名显式切 deepseek-flash

## Step 3 完成证据（2026-11-11，用户已确认）

| 项 | 结果 | 证据 |
|---|---|---|
| 模板 | ✅ GET /api/import/template 生成中文表头五工作簿+填写说明 | 端点测试 + openpyxl 回读校验 |
| 五类用例 | ✅ 合法导入 committed 11/11；含错文件 partial 12/8/4 错误定位 sheet+行+字段；同文件重传 409 指纹拒绝返回原批次；force 覆盖重导 answers-only 分数更新总数不变；写入中断整批回滚（业务表全 0） | `backend/tests/test_import_api.py` 17 项，真实 xlsx 字节流经 TestClient→importer→SQLite，无 Mock 文件层 |
| 业务归属校验 | ✅ 班级同名跨年级歧义 / 学生不属于所填班级 / 考试绑班不符 / q_no 不在卷面 均拒 | 3 项专属用例（含联考不绑班放行） |
| 事务原子性 | ✅ BEGIN IMMEDIATE 单事务；错误行不进写入集合；写入期定位失败跳行报错；意外异常整批回滚；批次状态独立事务 | crash 注入用例：写入全量后崩溃→业务表计数 0→批次 failed→同文件可重导 |
| 重复导入语义 | ✅ 指纹=sha256 拒 committed/partial 重复；force 仅 answers 单表（其余 400）；库内重导被 E_CONFLICT（考试）/E_DUP（答题）双防，零重复写入 | 用例 3/4 + 重导防御用例 |
| 中断恢复 | ✅ 启动清扫滞留 validating→failed（lifespan 实测）；存在 validating 时新导入 409 import_busy 留痕 | 2 项专属用例 |
| demo 冒烟 | ✅ `generate_demo_import.py`：真实演示数据（20 学生×10 题）生成 232 行工作簿→全 committed→库内 1/20/1/10/200；重放被防重且数据不变 | 实际运行输出（batch imp_20261111115806_0e2a4e） |

## Phase 0 完成证据（2026-11-10，全部实测）

| 项 | 结果 | 证据 |
|---|---|---|
| I1 导入统一 | ✅ 34 文件统一 `backend.*`；仓库根=唯一 sys.path 根 | 全仓 grep 无残留 flat 导入；测试全绿；conftest/scripts 均以仓库根引导 |
| I2 acps-sdk 注释 | ✅ 统一为 2.2.0（AtomGit 源/本地 wheel，非 PyPI） | interop 各文件 docstring/报错文案核对 |
| I3 CORS 白名单 | ✅ `ALLOWED_ORIGINS` env 注入，默认仅本机源 | 白名单内响应带 ACAO 头；白名单外无 CORS 头（浏览器拦截）——本地起服实测 |
| I4 HTTPS 核实 | ✅ 证据已回填技术方案 §11.1-4 | edumateaip.xyz A→47.116.76.175（本地 nslookup + 服务器 getent + ECS metadata 三方一致）；nginx 仅 80；无 certbot/acme.sh。HTTPS 顺延至集中部署 |
| I5 Docker | ✅ 构建通过 | 镜像 `eduagent:phase0`（576MB，构建 1264s）；容器无 acps-sdk 环境 `import backend.main` 冒烟通过；`.env` 不在镜像；`docker compose config` 有效 |
| I6 gitignore | ✅ `*.bak.*` + `models/` | commit f7af0e7 |
| I7 杂物清理 | ✅ 仓库 6 + 服务器 7 个 `.bak` 全部删除 | 六步流程（检查→备份→审核→执行→验证→收尾），commit f7af0e7 |
| I9 README | ✅ 重写（去赛化叙事 + 双启动方式 + Docker 用法） | commit 8dbf573 |

**测试基线**：98 passed / 0 failed —— 改动前与改动后各跑一次全量，数量与结果一致（本地 Windows `.venv`，约 4–9s）。根目录 `tests/` 为服务器侧 e2e（硬编码 /opt/EduAgent），经 pytest.ini `testpaths` 排除本地收集，文件保留。

**AIP 降级修复**（本轮新增）：agent_caller/rpc_client/result_parser 的顶层 `acps_sdk` 导入改为可选导入 + 运行时 RuntimeError 守卫——容器最小镜像不装非 PyPI 的 SDK，主业务正常启动；调用 AIP 时报明确安装指引而非 ImportError。

**本轮排障记录**（Docker 构建 3 次失败→修复）：Docker Hub DNS 污染→经 docker.m.daocloud.io 镜像源拉基础镜像后 retag；pip 下载哈希校验失败→Dockerfile 增 `PIP_INDEX_URL` build-arg（默认官方源，国内传清华源）；容器冒烟暴露急切导入链→上述降级修复。

## 代码状态

- **本轮提交**：`8dbf573`（代码+基础设施，43 文件 +300/−196）已 push GitHub main；文档（技术方案回填 + 本目录三件）随下一个 docs commit 入库。
- 工作区：干净（文档提交后）。

## ECS 服务器状态（按新策略本轮保持不动）

- 版本：`f7af0e7`，落后 GitHub——**有意为之**，集中部署窗口统一同步。
- 运行：旧启动命令 `uvicorn main:app --app-dir backend`（旧代码，服务正常，/api/health 200）。
- ⚠️ 下次重启必须用新命令 `python -m uvicorn backend.main:app --app-dir .`（仓库根执行，PYTHONPATH 不再必需）。
- nginx：仅监听 80；域名已解析到位；HTTPS 未配置。

## 待办 / 阻塞

1. **Phase 3 进行中（S0 范围收窄）**：小学前置图 v1.0 已冻结（87 条）；Phase 2 Round1 证据补充、Phase 2.2 49 条重基线、初中运行时清理（含备份）均已完成；小学演示数据设计草案已生成（未导入），**等待用户审核 4 项设计口径**。确认后走 F16 导入 → 数据核验/业务冒烟/测试 → Phase 3 本体。
2. 集中部署窗口（未排期）：ECS 代码同步 + 新启动命令切换 + HTTPS 证书 + compose 上线（见 ROADMAP.md 末节）。
3. Step 5 冻结项：按需解冻（prompt v2 重跑/全量标注等）需用户批准；解冻即用现成管线。
4. 检索 Round2 backlog（可选，不阻塞 Phase 3）：长查询改写、KP 描述扩充、单位词泛化、置信门/min-max 风险；沿用冻结正式集，版本化报告。
5. 无其他阻塞。

## 环境备忘

- 本地：Windows 11，Python 3.13.7（`.venv`），pytest **321 用例**（Phase 3 S0：311 + 前置图 8 + 清理脚本 2），Docker Desktop。⚠️ **pytest 依赖 openssl 在 PATH**（`D:\Git\mingw64\bin`，2026-11-11 踩坑记录）。⚠️ **git push 需关闭 TUN**（SSH 22/443 被 fake-IP 拦截，2026-11-14 实测；HTTPS 通道可达可作备选，gh 已认证）。
- BGE 模型缓存：`data/models/fastembed`（90.8MB，gitignore 覆盖）；fastembed 0.9.1 + onnxruntime 1.32.0 已入 venv 并锁进 requirements.txt。
- 服务器：阿里云 ECS 2核2G（47.116.76.175），/opt/EduAgent，`.venv` Python 3.10，nginx 站点 "edumate"（client_max_body_size 100M，SSE 代理参数就绪）。
