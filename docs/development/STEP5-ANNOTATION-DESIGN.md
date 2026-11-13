# Step 5-A 知识点/难度 LLM 打标技术设计方案

> 2026-11-13 产出（Step 5-A：方案设计阶段）。**本文档为提案，未经用户审查批准前不实施**：不改业务代码、不改数据库与 Schema、不调用任何 LLM API、不读取/验证 API Key、不执行任何批量任务、不修改 82 个 KP 定义。
> 依据：真实代码与数据库实况（2026-11-13 只读查询 `data/db/edumate.sqlite`）+ 官方价格页核实（2026-11-13 抓取 api-docs.deepseek.com 中文页）。估算数字均标注【估算】；实测数字标来源。
> 约束承接：决策⑲（质量四状态）/⑳（数据文件 Git 边界）/㉑（两道关口）/㉒（分发边界）/㉔（KP 规则标签定位与五条独立打标约束）。

---

## 1. 当前实现现状与问题分析（全部为 2026-11-13 实测/实读）

### 1.1 数据库与代码实况

| 对象 | 实况 |
|---|---|
| `questions` | 全库仅 `stage='primary'`（**无初中题数据**，Step 3 为导入服务与测试，实际导入另行规划）：processed 207,858 + rejected 91。`type` 全部单值 `'application'`；`difficulty` 为规则粗标（按 equation 运算符个数）；`score` 统一占位 1.0；`verified` 全 0 |
| 题干长度 | 均值 46 字，p50=43 / p90=70 / p99=114 / max=324 |
| 特殊形态 | 主填空形态 `((())/(()))` 1,360 行；含其他空括号 1,666 行；含 `*` 11,054 行（乘号 7,701 / 指数 3,359 / 非标准记法 162，方案 A 修复后如实保留） |
| `q_kp` | **无 source 列**（现库 571,364 条挂接全部隐含来自规则 `build_primary_kp.py`）；无置信度/批次/时间字段；仅 PK(q_id,kp_id) 无二级索引 |
| 规则挂接分布 | 覆盖 204,237 题（98.2%），均 2.8 KP/题；**零挂接 3,621 题**；挂接数分布 1:48,004 / 2:58,943 / 3:39,359 / 4:25,934 / 5:16,860 / 6:9,254 / 7:3,995 / 8:1,346 / ≥9:542 |
| 头部/尾部 KP | 头部：四则混合运算 112,472（54%，`ops='mixed'` 兜底所致）、乘除 61,367、人民币计算 60,180；尾部：统计图表 54 / 和差问题 43 / 组合图形面积 11；零命中：和倍/差倍 2 个。挂接 <1,000 的 KP 共 24 个 |
| 过度挂接（Step 4 已记录） | kp_p_065 人民币计算 60,180 挂接中 ≥55% 无货币语境（单字"分"误触发）；kp_p_013 抽屉原理 663 挂接 100% 由"至少/保证"触发；kp_p_048 梯形面积含周长类误挂 80 |
| `knowledge_points` | 小学 82 个（kp_p_001..082，source='ape210k_rule'）；**name+description 合计仅 1,477 字符（平均描述 13.5 字）**；`grade` 全 NULL；`embedding` 列已预留（bge-small-zh-v1.5, 512d，Phase 2 导出用，当前无数据） |
| 迁移机制 | `backend/app/storage/db.py`：`MIGRATIONS=[(1,"migrations.sql")]`，迁移文件位于 `backend/app/storage/`，按序号追加执行并记录 `schema_migrations`；当前仅 V1 |
| LLM 配置 | `backend/config.py`：`DEEPSEEK_API_KEY`（env）、`DEEPSEEK_BASE_URL=https://api.deepseek.com`、**`DEEPSEEK_MODEL` 默认 `deepseek-v4-flash`**（遗留别名，已迁移至 flash 线，见 §8）、`LLM_TIMEOUT=60` |
| `llm_client.py` | DeepSeekProvider（官方 OpenAI SDK、OpenAI 兼容 API），含 usage 日志与错误映射；**无重试/退避/限流/成本控制/结构化输出**——批量打标执行器需自建（§9），不复用单次调用封装 |

### 1.2 问题定性

1. **规则标签不可信（决策㉔已定性）**：关键词级匹配无语境判断，头部 KP 因兜底规则（`ops='mixed'`→四则混合、单字"分"→人民币）严重过度挂接；标签只能作抽样辅助与对比基线，不能作 LLM 输入或评估 gold。
2. **无溯源承载**：q_kp 无 source/confidence/批次字段——LLM 标注不能写入 q_kp（否则与规则标签混淆、违反决策㉔"来源可区分"），需要新表（§5）。
3. **难度无语义**：现 difficulty 按运算符个数粗标（如 d5 占 22.3%，d4 仅 12.7%——分布与教学难度明显错位），需要 LLM 按明确锚点重标（独立字段，不改规则值）。
4. **题型不可标**：`type` 全库单值 `'application'`，无任何分类信号——**首期不标题型**（回答用户确认事项 1/3）。
5. **分值不标**：数据集无分值字段，禁止按内容推断（用户已定）。
6. **KP 定义精炼但语义弱**：82 个 KP 描述平均 13.5 字，人工语义清晰但对 embedding 召回（方案 B）与模型边界判定是双刃剑——prompt 直接用现有定义（不擅自修改），定义修订权在用户。

## 2. 首期目标和范围

| 项 | 裁定 |
|---|---|
| 打标对象 | **processed 207,858 道小学题**（q_ape_%）；rejected 91 不打标（决策⑲：评测/检索只取 processed） |
| 打标字段 | ①知识点（1–3 个 + 主知识点，限 82 体系）②难度 1–5（LLM 独立标，覆盖规则粗标用于对比） |
| 不标字段 | 题型（无数据基础）、分值（不得推断）——首期不做 |
| 输入字段 | **仅 stem**。answer/analysis 不进 prompt（知识点由题意决定；省 token；试验暴露不足可在 prompt v2 增补并记录版本演进） |
| 初中题 | 当前库 0 行，**Step 5-C 后续单独规划**（303 KP 体系，协议复用） |
| 明确不做 | 修改 82 KP 定义/规则表编号；修改 q_kp 现有规则挂接；修改 questions 现有字段值 |

## 3. 三种标注策略对比

| 维度 | A：LLM 直判（全 82 清单入 prompt） | B：Embedding 召回 + LLM 复判 | C：规则先分箱，不确定交 LLM |
|---|---|---|---|
| 准确性/召回 | **最高**：模型看到全部 82 定义，无召回截断 | 取决于召回质量：**KP 描述仅 13.5 字，bge-small 短文本召回 recall@10 未知**，漏召回即必错且不可恢复 | 最差：规则已被证明系统性过度挂接，"明确/不明确"分界本身需要语境理解，规则做不了 |
| 复杂/多知识点题 | 好（全清单 + 可输出 1–3 个） | 好（多标签不受候选数限制，但候选可能漏） | 差（多知识点题恰是规则失效区） |
| 低频知识点 | 天然覆盖（清单含尾部 KP 与零命中 KP） | 尾部 KP 描述短、样本少，召回风险最高 | 尾部 KP 规则关键词少，大概率漏 |
| Token 消耗 | 每题输入 ≈2,000【估算】；**DeepSeek 前缀缓存自动命中固定清单**，命中价 0.04 元/M，成本敏感项只剩输出（§8） | 每题输入 ≈390【估算】但候选块不命中缓存；总成本反高于 A 缓存态（¥288 vs ¥195 全库【估算】） | 理论最省，但省的钱买不回准确率 |
| 本地资源 | 无 | 需新增 onnxruntime/fastembed 依赖（现 requirements 无）；207,858 题 CPU ~1.7h、内存 <500MB【估算，§11.2 目标 <50ms/条】 | 无 |
| 工程复杂度 | 最低（单脚本执行器） | 中（本地向量化 + 召回 + 阈值回退） | 中（分箱规则维护 = 又一套规则债） |
| 误判/漏判风险 | 误判可经置信度+抽检治理 | 召回漏判**静默且系统性**（最危险） | 双重规则风险叠加 |
| 维护成本 | prompt 单点演进 | prompt + embedding 模型/索引双演进 | 规则表 + prompt 双演进 |
| 结论 | **主线推荐** | 备选：仅当实测缓存命中率 <60% 或全库预算失控时启用（触发条件见 §12） | **不采纳为主线**；吸收其思想为"低置信度难例二级复核"（用模型自身 confidence 触发，绝不用规则标签触发） |

**推荐：A 主线（全 82 清单 + DeepSeek 前缀缓存 + JSON 结构化输出）**，B 为降本备选并留实证触发条件，C 的合理内核（难例分级复核）并入 A 的可靠性设计。

## 4. 推荐架构及数据流

```
questions(processed, q_ape_%)
   │  只读抽取 q_id+stem（不含 q_kp 规则挂接 —— 决策㉔）
   ▼
试验集构建器 build_trial500.py ──► trial500_qids.jsonl（Git，仅 q_id+层标签）
   ▼
打标执行器 tag_questions.py（5-B 实现）
   ├─ 分片（1,000 题/片）+ ThreadPoolExecutor 并发（初始 4，自适应 8）
   ├─ Prompt 组装：固定前缀（任务说明+82 清单+特殊形态说明+难度锚点）＝缓存前缀
   ├─ OpenAI 兼容调用（temperature=0.2, response_format=json_object）
   ├─ 校验：kp_id ∈ 82 / primary ∈ kp_ids / 难度 ∈1–5 / confidence ∈[0,1]
   │      └ 失败→1 次带错误反馈的重试→仍失败计 invalid（不写库）
   ├─ 逐条事务写入 q_kp_llm / q_difficulty_llm（幂等键=(q_id, prompt_version)）
   ├─ 逐条 usage 累计 → 预算闸门（§9）→ manifest 追加
   ▼
annotation_batches（批次/模型/Prompt 版本/Token/费用聚合）
q_kp_llm / q_difficulty_llm（逐条溯源）＋ annotation_reviews（人工审核/修正）
   ▼
v_q_kp_effective（消费视图：LLM 优先、规则回退、来源列）──► Phase 2 检索/组卷/问数
```

**Prompt 输入红线（决策㉔）**：构造 prompt 的查询绝不 JOIN q_kp；规则挂接仅在事后评估中作对比（且不作 gold）。

**Prompt 骨架（v1 草案，文本入库于 `data/annotations/prompts/kp_primary_v1.md`）**：
1. 角色与任务说明（小学数学题知识点归类 + 难度评级）
2. 82 个知识点清单（`kp_id 名称：描述`，逐字取自 `knowledge_points`，**不增删改**）
3. 特殊形态说明（合法结构，非数据损坏）：`((())/(()))` 等空括号串＝填空空位；`*`＝乘号、`**`＝指数（python 记法，非 Markdown）；`(3/4)`＝分数；`n(a/b)`＝带分数；`%`＝百分数；`{_{..}}`＝循环小数记法
4. 输出 JSON 规范：`{"primary":"kp_p_xxx","kp_ids":[1–3 个],"difficulty":1–5,"confidence":{"kp":0–1,"difficulty":0–1},"reason":"≤30字"}`；kp_ids 必须取自清单、primary 必含于 kp_ids；**题不属于任何知识点时允许 kp_ids=[] 并在 reason 说明**（不可映射率如实统计，不硬挂——决策㉒精神）
5. 难度锚点（可复现定义）：1=一步运算/直接作答；2=两步内常规应用；3=两至三步含简单组合或单位换算；4=多步复合/隐含条件/多知识点组合；5=需巧妙构造或竞赛级多重嵌套
6. few-shot 2–3 例（含 1 例填空形态、1 例多知识点、1 例不可映射）

## 5. 数据库 Schema 设计（提案，**本阶段不执行迁移**）

迁移方式：`MIGRATIONS` 追加 `(2, "migrations_v2.sql")`（沿用 db.py 既有机制），V2 全部为**新增表/索引/视图**，不改 questions/q_kp/knowledge_points 现有列（决策㉔：现有规则挂接不动）。

```sql
-- ===== V2 提案 =====
CREATE TABLE annotation_batches (
  batch_id     TEXT PRIMARY KEY,      -- ann_<yyyymmdd>_<两位序号>_<slug>
  field        TEXT NOT NULL,         -- 'kp' / 'difficulty'
  scope        TEXT NOT NULL,         -- 'ape_primary'
  provider     TEXT NOT NULL DEFAULT 'deepseek',
  model        TEXT NOT NULL,         -- 请求模型名，如 deepseek-flash
  model_version TEXT,                 -- API 响应返回的模型快照（如 DeepSeek-V4.1-Flash-1017）
  prompt_id    TEXT NOT NULL,         -- 如 kp_primary
  prompt_version TEXT NOT NULL,       -- 如 v1.0
  prompt_sha256  TEXT NOT NULL,       -- prompt 文件指纹（防漂移）
  kp_snapshot_sha256 TEXT,            -- 82 KP 定义快照指纹
  status       TEXT NOT NULL DEFAULT 'running',
               -- running / completed / aborted(预算或手动中止) / failed
  budget_cap_cny REAL,                -- 本批预算上限（元）
  config_json  TEXT,                  -- {concurrency,max_retries,temperature,price_table...}
  total_questions INTEGER DEFAULT 0,
  succeeded    INTEGER DEFAULT 0,
  failed       INTEGER DEFAULT 0,     -- 重试耗尽
  invalid      INTEGER DEFAULT 0,     -- 输出校验不过
  input_tokens_sum    INTEGER DEFAULT 0,
  output_tokens_sum   INTEGER DEFAULT 0,
  cache_hit_tokens_sum INTEGER DEFAULT 0,
  cost_cny_est REAL DEFAULT 0,
  created_at   TEXT NOT NULL,
  finished_at  TEXT,
  notes        TEXT,
  UNIQUE (field, scope, prompt_id, prompt_version)  -- 批次级幂等：同版本重跑=复用同批
);

CREATE TABLE q_kp_llm (               -- LLM 知识点标注（规则挂接仍在 q_kp，两表并存=来源天然可区分）
  q_id    TEXT NOT NULL,
  kp_id   TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  is_primary INTEGER NOT NULL DEFAULT 0,
  confidence REAL,                    -- 模型自报 0–1
  review_state TEXT NOT NULL DEFAULT 'pending'
               CHECK (review_state IN ('pending','approved','rejected','corrected')),
  created_at TEXT NOT NULL,
  PRIMARY KEY (q_id, kp_id, batch_id)  -- 决策㉑幂等键 (q_id, prompt_version) 经 batch_id 落地
);
-- 不设 q_id 外键（与现有 q_kp 同风格）：ETL 全量重建语义删除重插 q_ape_% 时标注表不致连锁破坏，
-- 一致性由 validate 脚本核对（孤儿行=重建后需重挂，脚本提示）

CREATE TABLE q_difficulty_llm (
  q_id    TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  difficulty INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
  confidence REAL,
  review_state TEXT NOT NULL DEFAULT 'pending'
               CHECK (review_state IN ('pending','approved','rejected','corrected')),
  created_at TEXT NOT NULL,
  PRIMARY KEY (q_id, batch_id)
);

CREATE TABLE annotation_reviews (     -- 人工审核/修正记录（含历史，可追溯）
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  q_id TEXT NOT NULL,
  batch_id TEXT NOT NULL,
  field  TEXT NOT NULL,               -- 'kp' / 'difficulty'
  old_value TEXT, new_value TEXT,     -- kp_id 或难度值；approve 时 new_value 为空
  action TEXT NOT NULL CHECK (action IN ('approve','reject','correct')),
  reviewer TEXT NOT NULL DEFAULT 'user',
  reason  TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_qkp_llm_q  ON q_kp_llm(q_id);
CREATE INDEX idx_qkp_llm_kp ON q_kp_llm(kp_id);   -- 顺带补齐 q_kp 缺失的按 KP 查题路径
CREATE INDEX idx_diff_llm_q ON q_difficulty_llm(q_id);
CREATE INDEX idx_batches_status ON annotation_batches(status);

-- 消费视图（Phase 2 用）：某题有 LLM 标注则只见 LLM 标签，否则回退规则候选；来源列显式
CREATE VIEW v_q_kp_effective AS
SELECT q_id, kp_id, 'llm' AS tag_source, is_primary, review_state, batch_id
  FROM q_kp_llm
UNION ALL
SELECT k.q_id, k.kp_id, 'rule', 0, 'none', NULL
  FROM q_kp k
 WHERE NOT EXISTS (SELECT 1 FROM q_kp_llm l WHERE l.q_id = k.q_id);
```

**能力覆盖对照（用户要求的 9 项能力 → 落点）**：

| 能力 | 落点 |
|---|---|
| 规则/LLM 标签区分 | 两表并存 + v_q_kp_effective.tag_source |
| 模型名称及版本 | annotation_batches.model / model_version（响应快照） |
| Prompt 版本 | prompt_id/prompt_version/prompt_sha256 + prompts/registry.jsonl（Git） |
| 标注任务批次 | annotation_batches（status/计数/时间） |
| 置信度 | confidence（逐条） |
| 审核状态 | review_state 四态 |
| 人工修正 | annotation_reviews 全历史流水 |
| 重复标注/版本对比 | PK 含 batch_id → 同题多版本并存；对比=按 batch_id JOIN 对比查询 |
| 失败恢复 | 批次 UNIQUE 幂等 + 逐条事务 + manifest 断点（§9） |
| 标注结果追溯 | q_id → batch_id → model/prompt 版本/sha/配置/费用 全链可查 |

## 6. 500 题分层试验方案

**分层原则**：12 层、优先级互斥（S1→S11 依序首中即入层）+ 1 个全域随机参考层（S12 允许与其他层去重）；规则挂接仅用于**定义抽样层与分层分析**（用户允许的辅助用途），绝不进 prompt、绝不作 gold。层内抽样：`random.Random(42)` 对按 q_id 排序的池做确定性抽样，产出冻结清单（Git 存仅 q_id+层标签，不含题目内容——决策㉒）。

| 层 | 定义（真实池容量） | 配额 | 考察点 |
|---|---|---|---|
| S1 | 零规则挂接（3,621） | 40 | 规则失效区 LLM 表现——LLM 核心价值区 |
| S2 | 主填空形态 `((())/(()))`（1,360） | 30 | 填空标记不被误判为损坏 |
| S3 | 其他空括号形态（306） | 10 | 变体形态 |
| S4 | 含 `*`（11,054） | 20 | 乘号/指数不被当 Markdown |
| S5 | 非标准记法（162：循环小数/车牌打码等） | 10 | 极端形态鲁棒性 |
| S6 | 退化/不完整候选（611=585+26，清单见 raw_anomalies_report） | 10 | 缺条件题的处置（应低置信/不可映射而非硬挂） |
| S7 | 挂接 <1,000 的 24 个低频 KP 的题目池 | 40 | 低频知识点覆盖 |
| S8a | 恰 1 个规则挂接（48,004） | 15 | 单知识点 |
| S8b | ≥6 个规则挂接（15,137） | 15 | 多知识点/规则噪声重区 |
| S9 | 难度 1/2/3/4/5 各 30（池 39,062/54,223/41,785/26,460/46,419） | 150 | 难度全谱 |
| S10 | 题干 >70 字符（≈20,800） | 20 | 长题干 |
| S11 | 答案形态：百分数（7,202）/分数（ans 含 `/`） | 10+10 | 答案形态干扰 |
| S12 | **全域纯随机参考层**（排除上述已选后，seed=42） | 120 | 无偏估计生产准确率 |
| 合计 | | **500** | |

试验执行（5-B，需批准）：A 方案跑全部 500 题（预算 cap ¥10）；可选加跑 B 方案对照 500 题（+≈¥1）——由用户裁决是否双臂。

## 7. 人工评估标准（≥50 题）

**gold 建立流程**：从 500 题按层配比抽 50（S12 必占 ≥15 保证无偏、S1/S2/S7 各 ≥5）；导出核验表（q_id+题干+空白 gold 栏，staging 不入 Git）→ **我按与 prompt 同源的规则独立初标（盲于 LLM 输出）** → 用户复核/仲裁分歧 → gold 定稿；仲裁分歧率如实记录（分歧率高=KP 定义歧义信号，反馈 §7.3 治理）。

**指标定义**（题级，n=50）：
- **主知识点命中**：LLM.primary ∈ gold.kp_set
- **硬误标**：LLM.kp_ids 中存在 ∉ gold.kp_set 的标签（记个数；语义相邻的同大类误标记"软误标"，只统计不计不合格）
- **难度一致**：|LLM.difficulty − gold.difficulty| ≤ 1
- **题合格** = 主命中 ∧ 无硬误标 ∧ 难度一致；**合格率 = 合格题数/50 ≥ 90% ⇒ 决策㉑关口通过**
- 辅助诊断（报告全量输出，不设阈值）：完全集合匹配率、micro-Precision（TP/LLM 总标）、micro-Recall（TP/gold 总标）、难度精确一致率、不可映射题处置正确率、invalid 率（目标 <2%）、重跑一致率（同 50 题双跑输出一致率 ≥95%，temperature=0.2 下验证稳定性）

**未达标处理循环**（不通过绝不进入全量）：错误归因三桶 → ①KP 定义歧义（含 gold 仲裁分歧）→ 修 KP 描述**需用户逐条批准**（不擅改 82 定义）；②模型混淆对（如 相遇/追及、长方形/正方形周长）→ prompt v2 增反例与边界说明，prompt_version 升版重跑 500（成本 ≈¥0.5）；③难度锚点争议 → 修难度定义（prompt 内）→ 重跑。每轮循环重抽 50 题人工核验（可复用前轮 gold 题 + 新抽 10）。

## 8. API 成本测算（官方价格 2026-11-13 核实）

**价格来源**：api-docs.deepseek.com 中文定价页（WebFetch 抓取，2026-11-13）。现行两个模型（元/百万 tokens，输入缓存命中/未命中/输出）：

| 模型 | 高峰（工作日 9–12、14–18，北京时间） | 空闲（其余时段＝半价） |
|---|---|---|
| **deepseek-flash**（DeepSeek-V4.1-Flash-1017） | 0.04 / 2 / 8 | 0.02 / 1 / 4 |
| deepseek-v4-pro（DeepSeek-V4-Pro-0813） | 0.30 / 9.0 / 27.0 | 0.15 / 4.5 / 13.5 |

- `config.py` 现默认 `deepseek-v4-flash` 为**遗留别名**（官方页注明已迁移至 flash 线）——建议 5-B 显式改用 `deepseek-flash` 并在批次记录 model_version 响应快照；`.env` 实际覆盖值未读取（不读取 Key/环境，遵守本轮边界），开工核验时确认。
- deepseek-chat/deepseek-reasoner 名称已不在现行价目表——"不同模型差异"按现行两模型对比。

**单题 Token 假设【估算，试跑用实测 usage 校准】**：固定前缀（任务说明+82 清单）≈1,900 tok（82 定义合计 1,477 字符）；每题输入 ≈70 tok（题干均值 46 字）；输出 ≈90 tok（JSON）。重试/失败预留 +5–8%。

**方案 A（主线）分规模费用【估算，元】**：

| 规模 | 高峰+缓存命中 | 空闲+缓存命中 | 无缓存最坏（缓存全失效） |
|---|---|---|---|
| 500 题（试跑） | ≈0.5 | ≈0.2 | ≈2.3 |
| 2,000 题 | ≈1.9 | ≈0.9 | ≈9.3 |
| 5,000 题 | ≈4.7 | ≈2.3 | ≈23 |
| 20,000 题 | ≈19 | ≈9 | ≈93 |
| **207,858 题（全库）** | **≈195** | **≈97** | **≈970** |

- 成本大头是**输出**（占 ≈77%）：空闲时段跑全量可省一半 → 建议全量安排在晚间/周末分 2–3 夜执行。
- 缓存命中与否造成 ≈5× 差价：DeepSeek 前缀缓存自动生效（顺序批量下前缀稳定），但**预算闸门必须按无缓存口径设置**（§9），防失控。
- 方案 B（embedding 召回 top-10）全库 ≈¥288【估算】（候选块不命中缓存），且高于 A 缓存态——B 的价值只在缓存失效场景对冲。
- 二级复核（低置信度题改用 v4-pro 二判，假设 5% 触发）：全库 +≈¥38【估算，高峰价】——可选，试跑后按 invalid/低置信占比裁决。

**预算闸门提案（待批准）**：试跑 cap **¥10**；全量 cap **¥1,000**（覆盖无缓存最坏情形），80%（¥800）告警暂停复核、100% 优雅停机。实际预期支出 ≈¥100–200。

## 9. 工程可靠性设计

| 机制 | 设计 |
|---|---|
| 幂等执行 | 决策㉑幂等键 (q_id, prompt_version)：批次 UNIQUE(field,scope,prompt_id,prompt_version) → 同版本重跑复用批次；逐条 PK 含 batch_id → 已成功项跳过 |
| 断点续跑 | 逐条独立事务提交；resume 从库内已成功项 + manifest 断点续跑；进程被杀不损已写数据 |
| 失败重试/退避 | 可重试错误（429/超时/连接/5xx）指数退避 1→2→4→8→16s（含抖动）max 5 次；输出校验失败→1 次带错误反馈的纠正重试→计 invalid 不写库 |
| 熔断 | 连续 20 次失败或失败率 >20%（≥100 样本）→ 自动暂停批次（status=aborted），报告后再人工续跑 |
| 结果缓存 | 响应缓存键 (q_id, prompt_sha256, model)：staging JSONL；同 prompt 重跑直接复用不调 API，prompt 变更不命中 |
| 并发与限流 | ThreadPoolExecutor 初始 4 → 429 自适应升 8；官方价目页未列硬性 RPM【未知】，以 429 退避为限流信号 |
| 费用控制 | 每次调用取 usage（prompt_tokens/completion_tokens/prompt_cache_hit_tokens）→ 批次累计；单价表配置化（技术方案 §6.2 要求）；预算闸门见 §8；每 500 条打印进度+累计费用 |
| 停止机制 | --budget-cny / --max-items / Ctrl+C（安全点停）；达 cap 优雅停机不丢已写数据 |
| 日志与统计 | backend.logging_config 常规日志 + 逐条摘要行（q_id/状态/重试/tokens/费用/延迟）；批次汇总落 annotation_batches |
| Prompt 版本管理 | `data/annotations/prompts/kp_primary_v1.md`（Git）+ `registry.jsonl`（prompt_id/version/sha256/日期/变更说明）；prompt 改动必须升版本号，禁止原地改 |
| manifest JSONL | `data/annotations/manifests/<batch_id>.jsonl`（Git）：batch_start / 周期 checkpoint（每 1,000 题）/ batch_end（含 failed_qids、token 与费用汇总）。**逐题明细**（207,858 行 ≈25MB）写 `data/staging/tag_detail_<batch_id>.jsonl` 不入 Git——符合决策⑳防大文件精神。⚠️ 此为对决策㉑"manifest JSONL 入 Git"的解释口径（运行级入 Git、逐题明细入 staging），**请用户确认** |
| 吞吐 | 并发 8 × ≈2.5s/题【估算】→ 全库 ≈18–24h，分夜间空闲时段 2–3 晚完成 |

## 10. 分阶段实施计划（每步均需用户放行）

| 阶段 | 内容 | 关口 |
|---|---|---|
| **5-A（本轮）** | 现状调查 + 本设计文档 | 用户审查批准 |
| **5-B1** | V2 迁移文件 + prompt v1 + `build_trial500.py`/`tag_questions.py`/核验表导出脚本 + mock LLM 单测；**不调 API** | 用户批准 Schema 设计 + prompt 文本 |
| **5-B2** | 500 题试跑（API Key 核验存在可用，不打印；cap ¥10）→ 50 题人工核验 → 试跑报告（指标 + **实测单题 token/成本/缓存命中率**） | 合格率 ≥90%（§7）；用户批准成本 |
| **5-B3（可选缓冲步）** | 20,000 题扩大试跑（空闲时段，cap ≈¥20）→ 复核 100 题 | 用户裁决是否需要 |
| **5-B4** | 全量 207,858（空闲时段分片，cap ¥1,000）→ 完成后抽检 200 题合格率 ≥88% + 不可映射率报告 + manifest 汇总 | 用户验收 |
| **5-C** | 初中题打标（待 F16 实际导入数据后单独规划，303 KP 体系协议复用） | 届时另立项 |

## 11. 验收标准（Step 5 整体）

1. 试跑关口：50 题合格率 ≥90%（§7 定义），invalid <2%，重跑一致率 ≥95%
2. 全量关口：抽检 200 题合格率 ≥88%；不可映射率如实报告（数量与示例清单）
3. 溯源完备：任一 LLM 标签可查 批次→模型版本→prompt 版本/sha→置信度→审核状态；规则标签与 LLM 标签零混淆（两表分离 + 视图来源列）
4. 成本可复算：manifest + 单价表 → 逐批费用可独立重算，与批次记录一致
5. 决策㉔合规自检：prompt 构造查询无 q_kp JOIN（代码审查+单测断言）；82 KP 定义 sha 与库内一致；规则挂接数据零改动
6. 测试基线：全量 pytest 通过（新增 mock 单测不含真实 API 调用）

## 12. 风险与待裁决事项

| # | 风险/事项 | 处置 | 需用户裁决 |
|---|---|---|---|
| R1 | 82 KP 体系源自规则视角，部分题可能真无可挂知识点 | prompt 允许空输出；不可映射率如实报告；>15% 则提案扩充 KP（编号冻结只许追加） | 扩充与否届时裁决 |
| R2 | 缓存命中率不保证（DeepSeek 未承诺） | 预算按无缓存口径 cap ¥1,000；实测 <60% 触发方案 B 评估 | 触发后裁决切换 |
| R3 | 模型输出非确定性（temperature=0.2） | 重跑一致率指标 ≥95%；JSON 强制输出 | — |
| R4 | 模型名：默认 `deepseek-v4-flash` 是遗留别名 | 5-B 显式配置 `deepseek-flash`；批次记录响应 model_version | 5-B1 批准时确认 |
| R5 | 决策㉑ manifest 口径（运行级入 Git / 逐题明细入 staging） | §9 已说明理由 | **待确认本解释** |
| R6 | KP 定义平均 13.5 字，边界判定依赖模型常识 | few-shot + 反例；gold 分歧率监控；定义修订权在用户 | 修订逐条批准 |
| R7 | ETL 全量重建（幂等语义）会与标注表孤儿化 | 标注表不设 q_id 外键（同 q_kp 风格）；重建后 validate 提示重挂 | — |
| R8 | 空括号/星号/循环小数形态被模型误读为损坏 | prompt 特殊形态说明（§4.3）+ 500 题 S2–S6 层专门考察 | — |
| R9 | 打标期间费用/时长超预期 | 闸门+熔断+分夜执行；20,000 缓冲步可选 | 超额即停待裁决 |
| R10 | 人工核验耗用户时间 | 我盲标初稿 + 用户复核仲裁；核验表格式化降低负担 | gold 仲裁流程确认 |
| R11 | 初中 303 KP prompt 前缀更长（≈12k tok）缓存价差扩大 | 5-C 届时重估 B 方案必要性 | 届时裁决 |
| R12 | 网络环境（TUN）影响 API 连通性 | 重试/退避已覆盖；试跑先 50 题连通验证 | — |

---

## 附：决策㉔五条约束 → 本设计落点

| 约束 | 落点 |
|---|---|
| 不将 q_kp 规则挂接作为 prompt 输入 | §4 红线 + 单测断言（prompt 组装查询无 q_kp JOIN） |
| 不直接继承规则标签为输出 | 输出仅来自模型 JSON；规则标签不出现在任何输入/流程决策 |
| 限 82 体系内语义匹配 | prompt 清单 = knowledge_points 82 行逐字快照 + kp_id 白名单校验 |
| 溯源必留（来源/模型/Prompt 版本/置信度/审核状态） | annotation_batches + q_kp_llm/q_difficulty_llm 全字段（§5） |
| 规则标签与 LLM 标签明确区分 | 独立新表，q_kp 零改动；v_q_kp_effective.tag_source 显式 |

*本轮（5-A）交付物即本文档；未改动任何代码、数据库、Schema、KP 定义；未调用任何 API。等待用户审查批准后进入 5-B1。*
