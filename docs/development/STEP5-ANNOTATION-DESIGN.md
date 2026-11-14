# Step 5-A 知识点/难度 LLM 打标技术设计方案（第二版）

> **2026-11-14 第二版**：按用户修订指令落实四项已定决策（manifest 分层管理/预算闸门/A·B 双臂试验/显式模型名 deepseek-flash）+ 九项技术修正。第一版（2026-11-13）经用户审查后以书面修订意见驱动本版，修订记录见 §0。
> **本轮仍属设计文档修订（Step 5-A），不是正式开发**：未改业务代码、未改数据库与 Schema、未建 Prompt 文件、未调用任何 LLM API、未读取/验证 API Key、未执行任何批量标注。
> 依据：真实代码与数据库实况（2026-11-13/14 只读查询 `data/db/edumate.sqlite` 与原始文件）+ DeepSeek 官方价格页（2026-11-13 抓取 api-docs.deepseek.com 中文页）。估算数字均标注【估算】；实测数字标来源。
> 约束承接：决策⑲（质量四状态）/⑳（数据文件 Git 边界）/㉑（两道关口，manifest 口径见 §9）/㉒（分发边界）/㉔（KP 规则标签定位与五条独立打标约束）。

---

## 0. 第二版修订记录（2026-11-14）

| # | 修改点 | 内容 | 原因 | 是否改变原有决策 |
|---|---|---|---|---|
| 1 | 新增题级结果表 `q_annotation_results` | 记录每题标注终态（ok/unmapped/failed/invalid）、主知识点、reason、批次；不可映射作为有效标注结果持久化 | 修复结构性缺陷：kp_ids=[] 时无行可写，视图会误回退规则标签（用户指令 2-1） | 是——Schema 提案变更（按用户指令） |
| 2 | 批次表 `field` → `task_type='kp_difficulty'`，新增 `strategy` 列 | 一次 API 调用同时产出 KP+难度+reason；A/B 两臂以 strategy 区分独立批次 | 修复矛盾：prompt 单次输出双字段但批次只允许 kp/difficulty（用户指令 2-2） | 是——Schema 提案变更 |
| 3 | 消费视图重写（v_effective_batch + v_q_kp_effective） | 有效批次按 (scope, task_type, strategy) 选最新 completed；回退以题级结果驱动；unmapped 永不回退规则；review_state 消费规则明确 | 修复多批次无条件合并、不可映射误回退（用户指令 2-3） | 是——视图设计变更 |
| 4 | 并发改 AIMD | 初始 4 → 稳定后缓升至上限 8；**429 = 降并发 + 指数退避**，连续 429 继续降（下限 1），恢复稳定后再缓升 | 修正原错误设计（原写成 429 后升并发）（用户指令 2-4） | 是——修正错误 |
| 5 | 成本全表重算 | 全部规模 × {高峰缓存/空闲缓存/高峰无缓存/空闲无缓存} 四场景，统一含 8% 重试余量；预算保护一律按无缓存最坏口径校验 | 落实决策②；修正原表未含重试、20,000 cap 与最坏值矛盾（用户指令 2-5） | 是——预算表述变更 |
| 6 | manifest 分层管理定为决策㉑落实方式 | 运行级 JSONL 入 Git（batch_start/checkpoint/batch_end/失败汇总/Token 费用汇总）；逐题明细入 `data/staging/` 不入 Git | 用户决策①（原 v1 待确认项 R5 关闭） | 确认原解释口径为正式决策 |
| 7 | A/B 双臂试验正式纳入 | 同 500 题、同模型、同 JSON 结构、同 gold 与指标；两臂费用合并计入 ¥10 总 cap；A 仍为主线 | 用户决策③（原 v1 双臂为可选项，现定为必做） | 是——试跑协议变更 |
| 8 | B 方案成本细项补齐 | 本地 embedding 零 API 费用；候选 = top-10；候选块 ≈200 tok【估算】；缓存仅前缀 ≈600 tok 命中；重试余量同 A；recall@10 等**未知项标注待 5-B2 实测** | 用户决策③ + 指令 2-9 | 补充细化 |
| 9 | 模型名显式 `deepseek-flash` | 配置默认值、批次记录请求模型名、记录响应 model_version 快照、A/B 同模型、不切 v4-pro | 用户决策④（v1 R4 关闭） | 确认 |
| 10 | 抽样池全部改 processed 口径 | 实测重算：难度分布 1:39,056/2:54,220/3:41,776/4:26,457/5:46,349；星号 11,049；百分数答案 6,926；分数答案 20,766；退化入库 574（585 中 11 行重复剔除未入库）；S6 两清单**交集=0**（585∩26），processed 池 = 574+26 = 600；rejected 91 题实测全部无规则挂接 | 用户指令 2-6；抽样必须只含 processed | 修正数据口径 |
| 11 | 50 题 ≥90% 定位修正 | 明确为**工程放行指标**（决策㉑关口），不宣称全库无偏准确率；无偏估计单独由 S12 纯随机层报告 | 用户指令 2-6 | 表述修正 |
| 12 | gold 流程定位修正 | 助手初标仅为人工核验辅助；**最终 gold 由用户确认** | 用户指令 2-6 | 表述修正 |
| 13 | Schema 约束强化 | confidence CHECK 0–1；kp_id/batch_id 增外键；q_id 不设外键理由保留；annotation_reviews 增 `target_kp_id` 精确对应标注行 | 用户指令 2-7 | Schema 提案变更 |
| 14 | reason 持久化 | 存 `q_annotation_results.reason`；Prompt 输出规范不变；Prompt/执行器/Schema 三者一致 | 用户指令 2-8 | 补充明确 |
| 15 | 实施计划授权状态修正 | 5-B3（20,000）**未授权**，须 5-B2 后按实测成本单独审批；5-B4（全量）**默认禁止**，须用户单独批准预算与范围；¥1,000 仅为技术参考熔断上限 | 用户决策② | 授权表述修正 |
| 16 | 消费视图 SQL 实现期修正（5-B1） | v_q_kp_effective 三处 JOIN 增加 `b.strategy = 'A_full_list'` 过滤（§5 已同步） | A/B 两臂各有有效批次时，原 SQL 会使同一题出现两份行、违反"B 臂不混入消费视图"设计意图 | 是——实现与文档同步修正 |
| 17 | 预算三线修订（2026-11-14，5-B2 试跑授权，决策㉖） | 硬停线 cap ¥10→**¥20**；新增 **¥10 提醒线（通知+记录，不自动停止）**与 **¥15 暂停线（停止新请求，输出进度/费用/剩余题量/预计剩余费用，等待人工确认）**；组级累计含历史批次费用——重试/续跑/新建批次均无法绕过；费用预留仍按无缓存高峰价最坏口径 | 用户试跑授权指令（预算与授权边界更新） | 是——预算机制修订 |
| 18 | B 臂 Embedding 实现方案入档（§13）+ config 默认模型切换 | §13 新增：推荐依赖 fastembed+onnxruntime、模型 bge-small-zh-v1.5、大小/内存/推理方式、top-10 实现与测试、recall@10 评估——**依赖未获批前不安装**；`config.py` 默认 DEEPSEEK_MODEL 改 `deepseek-flash`（决策④落地；实测 `.env` 不存在，无环境覆盖） | 用户 5-B2 指令第三、四节 | 补充落地 |

**四项用户决策落地对照**：

| 决策 | 内容 | 落点 |
|---|---|---|
| ① manifest 分层 | 运行级入 Git / 逐题明细入 staging；明确为决策㉑的落实方式 | §9 manifest 行 |
| ② 预算闸门 | 试跑 A+B 共享 cap ¥10（80% 告警暂停、100% 硬停）；全量未授权；¥1,000 技术参考；逐级单独审批 | §8 预算条款 + §10 |
| ③ A/B 双臂 | 同题同模型同 JSON 同 gold；费用合并；A 主线不变 | §3/§6/§8/§10 |
| ④ deepseek-flash | 配置默认/批次记录/响应快照/两臂同模型/不切 v4-pro | §4/§5/§8 |

---

## 1. 当前实现现状与问题分析（全部为实测/实读）

### 1.1 数据库与代码实况（processed 口径，2026-11-13/14 只读核实）

| 对象 | 实况 |
|---|---|
| `questions` | 全库仅 `stage='primary'`（**无初中题数据**，Step 3 为导入服务与测试，实际导入另行规划）：processed 207,858 + rejected 91。`type` 全部单值 `'application'`；`difficulty` 为规则粗标（按 equation 运算符个数）；`score` 统一占位 1.0；`verified` 全 0 |
| 题干长度 | 均值 46 字，p50=43 / p90=70 / p99=114 / max=324（processed 口径） |
| 特殊形态（processed） | 主填空形态 `((())/(()))` 1,360 行；其他空括号 306 行（含空括号共 1,666）；含 `*` 11,049 行（全域 11,054，其中 5 行 rejected） |
| 答案形态（processed） | 百分数 6,926 行；分数形态 20,766 行 |
| `q_kp` | **无 source 列**（库内 571,364 条挂接全部来自规则 `build_primary_kp.py`；实测 rejected 91 题**全部无挂接**，即全部挂接属于 processed）；无置信度/批次/时间字段；仅 PK(q_id,kp_id) 无二级索引 |
| 规则挂接分布（processed） | 覆盖 204,237 题（98.25%），均 2.8 KP/题；**零挂接 3,621 题**；挂接数分布 1:48,004 / 2:58,943 / 3:39,359 / 4:25,934 / 5:16,860 / ≥6:15,137 |
| 头部/尾部 KP | 头部：四则混合运算 112,472（54%，`ops='mixed'` 兜底所致）、乘除 61,367、人民币计算 60,180；尾部：统计图表 54 / 和差问题 43 / 组合图形面积 11；零命中：和倍/差倍 2 个。挂接 <1,000 的 KP 共 24 个（<500 的 14 个） |
| 过度挂接（Step 4 已记录） | kp_p_065 人民币计算 60,180 挂接中 ≥55% 无货币语境（单字"分"误触发）；kp_p_013 抽屉原理 663 挂接 100% 由"至少/保证"触发；kp_p_048 梯形面积含周长类误挂 80 |
| `knowledge_points` | 小学 82 个（kp_p_001..082，source='ape210k_rule'）；**name+description 合计仅 1,477 字符（平均描述 13.5 字）**；`grade` 全 NULL；`embedding` 列已预留（bge-small-zh-v1.5, 512d，Phase 2 导出用，当前无数据） |
| 异常候选 | 退化算式 585（processed 入库 **574**，11 行重复剔除未入库）/ 非终结符结尾 26（全部 processed）；**两清单交集 = 0**（2026-11-14 原始文件重扫实测） |
| 迁移机制 | `backend/app/storage/db.py`：`MIGRATIONS=[(1,"migrations.sql")]`，按序号追加执行并记录 `schema_migrations`；当前仅 V1 |
| LLM 配置 | `backend/config.py`：`DEEPSEEK_API_KEY`（env）、`DEEPSEEK_BASE_URL=https://api.deepseek.com`、现默认 `deepseek-v4-flash`（遗留别名，见 §8 决策④——将显式改用 `deepseek-flash`）、`LLM_TIMEOUT=60` |
| `llm_client.py` | DeepSeekProvider（官方 OpenAI SDK、OpenAI 兼容 API），含 usage 日志与错误映射；**无重试/退避/限流/成本控制/结构化输出**——批量打标执行器自建（§9），不复用单次调用封装 |

### 1.2 问题定性

1. **规则标签不可信（决策㉔已定性）**：关键词级匹配无语境判断，头部 KP 因兜底规则严重过度挂接；标签只能作抽样辅助与对比基线，不能作 LLM 输入或评估 gold。
2. **无溯源承载**：q_kp 无 source/confidence/批次字段——LLM 标注必须写入独立新表（§5），否则与规则标签混淆、违反决策㉔。
3. **难度无语义**：现 difficulty 按运算符个数粗标（d5 占 22.3% / d4 仅 12.7%，分布与教学难度明显错位），需 LLM 按明确锚点重标（独立字段，不改规则值）。
4. **题型不可标**：`type` 全库单值 `'application'`，无分类信号——**首期不标题型**。
5. **分值不标**：数据集无分值字段，禁止按内容推断。
6. **KP 定义精炼但语义弱**：平均 13.5 字描述，人工语义清晰，但对 embedding 召回（B 臂）与模型边界判定是双刃剑——prompt 逐字使用现有定义（不擅自修改），定义修订权在用户。

## 2. 首期目标和范围

| 项 | 裁定 |
|---|---|
| 打标对象 | **processed 207,858 道小学题**（q_ape_%）；rejected 91 不打标（决策⑲） |
| 打标字段 | ①知识点（1–3 个 + 主知识点，限 82 体系；允许不可映射）②难度 1–5——**一次 API 调用同时产出**（task_type='kp_difficulty'） |
| 不标字段 | 题型（无数据基础）、分值（不得推断） |
| 输入字段 | **仅 stem**。answer/analysis 不进 prompt（知识点由题意决定；省 token；试验暴露不足可在 prompt v2 增补并记录版本演进） |
| 初中题 | 当前库 0 行，**Step 5-C 后续单独规划**（303 KP 体系，协议复用） |
| 明确不做 | 修改 82 KP 定义/规则表编号；修改 q_kp 现有规则挂接；修改 questions 现有字段值；不引入 LangGraph、MCP 或与本阶段无关的技术 |

## 3. 三种标注策略对比

| 维度 | A：LLM 直判（全 82 清单入 prompt） | B：Embedding 召回 + LLM 复判 | C：规则先分箱，不确定交 LLM |
|---|---|---|---|
| 准确性/召回 | **最高**：模型看到全部 82 定义，无召回截断 | 取决于召回质量：**recall@10 未知，待 5-B2 实测**（KP 描述仅 13.5 字，短文本召回风险高）；漏召回即必错且不可恢复 | 最差：规则已被证明系统性过度挂接，"明确/不明确"分界本身需要语境理解 |
| 复杂/多知识点题 | 好（全清单 + 1–3 个输出） | 好（多标签不受候选数限制，但候选可能漏） | 差（多知识点题恰是规则失效区） |
| 低频知识点 | 天然覆盖（清单含尾部与零命中 KP） | 尾部 KP 描述短、样本少，召回风险最高 | 尾部 KP 规则关键词少，大概率漏 |
| Token 消耗 | 每题输入 ≈1,970【估算】；固定前缀命中 DeepSeek 前缀缓存（0.04 元/M）；成本敏感项为输出 | 每题输入 ≈870【估算】但候选块逐题不同不命中缓存；四场景全表见 §8 | 理论最省，但省的钱买不回准确率 |
| 本地资源 | 无 | 需新增 onnxruntime/fastembed 依赖（现 requirements 无）；207,858 题 CPU ≈1.7h、内存 <500MB【估算，§11.2 目标 <50ms/条】；**本地 embedding 零 API 费用** | 无 |
| 工程复杂度 | 最低（单脚本执行器） | 中（本地向量化 + 召回 + 阈值回退） | 中（分箱规则维护 = 又一套规则债） |
| 误判/漏判风险 | 误判可经置信度+抽检治理 | 召回漏判**静默且系统性**（最危险） | 双重规则风险叠加 |
| 维护成本 | prompt 单点演进 | prompt + embedding 模型/索引双演进 | 规则表 + prompt 双演进 |

**结论（决策③确认后维持不变）**：**A 为主线方案**；B 为对照实验与未来备选（触发启用条件：实测缓存命中率 <60% 或 A 臂全库成本失控）；C 不采纳为主线，其合理内核（低置信度难例二级复核）并入 A 的可靠性设计——复核触发只用模型自身 confidence，绝不用规则标签。

## 4. 推荐架构及数据流

```
questions(processed, q_ape_%)
   │  只读抽取 q_id+stem（查询不含 q_kp —— 决策㉔红线，单测断言）
   ▼
试验集构建器 build_trial500.py ──► trial500_qids.jsonl（Git，仅 q_id+层标签）
   ▼
打标执行器 tag_questions.py（5-B 实现；A/B 两臂共用执行器，仅 prompt/strategy 不同）
   ├─ 分片（1,000 题/片）+ ThreadPoolExecutor（AIMD 并发：初始 4，上限 8，429 降并发退避）
   ├─ Prompt 组装（两臂同构差异仅知识点候选来源）：
   │    A 臂：固定前缀（任务说明+82 清单+特殊形态说明+难度锚点）→ 前缀缓存生效
   │    B 臂：固定说明（≈600 tok）+ 每题 embedding 召回 top-10 候选块（不命中缓存）
   ├─ 调用 deepseek-flash（temperature=0.2, response_format=json_object）
   ├─ 输出 JSON（两臂同构）：{"primary","kp_ids[1–3]","difficulty","confidence{kp,difficulty}","reason≤30字"}
   ├─ 校验：kp_id ∈ 82 / primary ∈ kp_ids / 难度 ∈1–5 / confidence ∈[0,1]
   │      └ 失败→1 次带错误反馈的重试→仍失败计 invalid（写 results 行 status='invalid'，不入标签表）
   ├─ 逐条事务写入：q_annotation_results（题级终态）+ q_kp_llm + q_difficulty_llm
   ├─ 逐条 usage 累计 → 预算组闸门（§8/§9）→ manifest 追加
   ▼
annotation_batches（A 臂、B 臂各一条批次记录：strategy/model/prompt 版本/费用聚合）
   ▼
v_effective_batch（有效批次选择）→ v_q_kp_effective（消费视图）──► Phase 2 检索/组卷/问数
```

**Prompt 输入红线（决策㉔）**：构造 prompt 的查询绝不 JOIN q_kp；规则挂接仅在事后评估中作对比（且不作 gold）。

**Prompt 骨架（v1 草案，5-B1 时落为 `data/annotations/prompts/kp_primary_v1.md`（A 臂）/ `kp_recall_v1.md`（B 臂））**：
1. 角色与任务说明（小学数学题知识点归类 + 难度评级）
2. 知识点清单：A 臂 = 82 个逐字快照（`kp_id 名称：描述`，取自 `knowledge_points`，**不增删改**）；B 臂 = 该题召回 top-10 候选的同格式子集（清单为真子集，kp_id 白名单校验同 A）
3. 特殊形态说明（合法结构，非数据损坏）：`((())/(()))` 等空括号串＝填空空位；`*`＝乘号、`**`＝指数（python 记法，非 Markdown）；`(3/4)`＝分数；`n(a/b)`＝带分数；`%`＝百分数；`{_{..}}`＝循环小数记法
4. 输出 JSON 规范（见上）；kp_ids 必须取自清单、primary 必含于 kp_ids；**题不属于任何清单知识点时 kp_ids=[]（primary 留空）并在 reason 说明**——不可映射是有效标注结果，持久化（§5），不硬挂（决策㉒精神）
5. 难度锚点（可复现定义）：1=一步运算/直接作答；2=两步内常规应用；3=两至三步含简单组合或单位换算；4=多步复合/隐含条件/多知识点组合；5=需巧妙构造或竞赛级多重嵌套
6. few-shot 2–3 例（含 1 例填空形态、1 例多知识点、1 例不可映射）

## 5. 数据库 Schema 设计（提案，**本阶段不执行迁移**）

迁移方式：`MIGRATIONS` 追加 `(2, "migrations_v2.sql")`（沿用 db.py 既有机制）。V2 全部为**新增表/索引/视图**，不改 questions/q_kp/knowledge_points 现有列（决策㉔：现有规则挂接不动）。

```sql
-- ===== V2 提案（第二版） =====
CREATE TABLE annotation_batches (
  batch_id     TEXT PRIMARY KEY,      -- ann_<yyyymmdd>_<两位序号>_<slug>
  task_type    TEXT NOT NULL DEFAULT 'kp_difficulty' CHECK (task_type IN ('kp_difficulty')),
               -- 一次调用同时产出知识点+难度（第二版：原 field kp/difficulty 二值废弃）
  scope        TEXT NOT NULL,         -- 'ape_primary'
  strategy     TEXT NOT NULL,         -- 'A_full_list' / 'B_recall'（A/B 两臂独立批次）
  provider     TEXT NOT NULL DEFAULT 'deepseek',
  model        TEXT NOT NULL,         -- 请求模型名；决策④：统一 'deepseek-flash'
  model_version TEXT,                 -- API 响应返回的模型快照（如 DeepSeek-V4.1-Flash-1017）
  prompt_id    TEXT NOT NULL,         -- A 臂 'kp_primary' / B 臂 'kp_recall'
  prompt_version TEXT NOT NULL,       -- 如 v1.0
  prompt_sha256  TEXT NOT NULL,       -- prompt 文件指纹（防漂移）
  kp_snapshot_sha256 TEXT,            -- 知识点清单快照指纹（A 臂=82 全量；B 臂=定义快照同源）
  budget_group TEXT,                  -- 预算组：同组批次共享闸门（试跑两臂同组共享 ¥10，§8）
  status       TEXT NOT NULL DEFAULT 'running',
               -- running / completed / aborted(预算或手动中止) / failed
  config_json  TEXT,                  -- {concurrency,max_retries,temperature,price_table,...}
  total_questions INTEGER DEFAULT 0,
  succeeded    INTEGER DEFAULT 0,     -- status=ok
  unmapped     INTEGER DEFAULT 0,     -- status=unmapped（有效标注结果）
  failed       INTEGER DEFAULT 0,     -- 重试耗尽
  invalid      INTEGER DEFAULT 0,     -- 输出校验不过
  input_tokens_sum    INTEGER DEFAULT 0,
  output_tokens_sum   INTEGER DEFAULT 0,
  cache_hit_tokens_sum INTEGER DEFAULT 0,
  cost_cny_est REAL DEFAULT 0,
  created_at   TEXT NOT NULL,
  finished_at  TEXT,
  notes        TEXT,
  UNIQUE (scope, task_type, strategy, model, prompt_id, prompt_version)
               -- 不同模型/Prompt 版本/快照可建独立实验批次；同键重跑=复用同批（幂等）
);

CREATE TABLE q_annotation_results (   -- 题级标注终态（第二版新增：不可映射的持久化载体）
  q_id      TEXT NOT NULL,
  batch_id  TEXT NOT NULL REFERENCES annotation_batches(batch_id),
  status    TEXT NOT NULL CHECK (status IN ('ok','unmapped','failed','invalid')),
            -- ok=已产出标签；unmapped=模型判定无可挂知识点（有效标注结果）；
            -- failed/invalid=中间态，重试成功后被覆盖（INSERT OR REPLACE）
  primary_kp_id TEXT REFERENCES knowledge_points(kp_id),  -- ok 时非空；unmapped/中间态为空
  reason    TEXT,                      -- 模型 reason（≤30 字），持久化（第二版明确）
  created_at TEXT NOT NULL,
  PRIMARY KEY (q_id, batch_id)
);
-- 断点续跑判定：无 (q_id,batch_id) 行或 status ∈ ('failed','invalid') → 待（重）跑
-- 一致性：status='ok' ⇒ primary_kp_id 非空且在 q_kp_llm 中有 is_primary=1 行（validate 脚本核对）

CREATE TABLE q_kp_llm (               -- LLM 知识点标注（规则挂接仍在 q_kp，两表并存=来源天然可区分）
  q_id    TEXT NOT NULL,
  kp_id   TEXT NOT NULL REFERENCES knowledge_points(kp_id),
  batch_id TEXT NOT NULL REFERENCES annotation_batches(batch_id),
  is_primary INTEGER NOT NULL DEFAULT 0,
  confidence REAL CHECK (confidence IS NULL OR (confidence BETWEEN 0 AND 1)),
  review_state TEXT NOT NULL DEFAULT 'pending'
               CHECK (review_state IN ('pending','approved','rejected','corrected')),
  created_at TEXT NOT NULL,
  PRIMARY KEY (q_id, kp_id, batch_id)  -- 决策㉑幂等键 (q_id, prompt_version) 经 batch_id 落地
);
-- q_id 不设外键（与现有 q_kp 同风格）：ETL 全量重建语义会删除重插 q_ape_% 行，
-- 无外键可避免连锁破坏；孤儿行由 validate 脚本核对（重建后需重挂或清理）

CREATE TABLE q_difficulty_llm (
  q_id    TEXT NOT NULL,
  batch_id TEXT NOT NULL REFERENCES annotation_batches(batch_id),
  difficulty INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
  confidence REAL CHECK (confidence IS NULL OR (confidence BETWEEN 0 AND 1)),
  review_state TEXT NOT NULL DEFAULT 'pending'
               CHECK (review_state IN ('pending','approved','rejected','corrected')),
  created_at TEXT NOT NULL,
  PRIMARY KEY (q_id, batch_id)
);

CREATE TABLE annotation_reviews (     -- 人工审核/修正记录（全历史流水，可追溯）
  review_id INTEGER PRIMARY KEY AUTOINCREMENT,
  q_id TEXT NOT NULL,
  batch_id TEXT NOT NULL REFERENCES annotation_batches(batch_id),
  field  TEXT NOT NULL CHECK (field IN ('kp','difficulty','unmapped')),
  target_kp_id TEXT,                  -- field='kp' 时的被修正标注行 kp_id（精确定位 q_kp_llm 行）
  old_value TEXT, new_value TEXT,     -- kp_id 或难度值；approve 时 new_value 为空
  action TEXT NOT NULL CHECK (action IN ('approve','reject','correct')),
  reviewer TEXT NOT NULL DEFAULT 'user',
  reason  TEXT,
  created_at TEXT NOT NULL
);
-- 对应关系：kp 修正 → (q_id, batch_id, target_kp_id) 唯一定位 q_kp_llm 行；
-- difficulty 修正 → (q_id, batch_id, field='difficulty') 唯一定位行；
-- unmapped 修正 → (q_id, batch_id, field='unmapped') 定位 q_annotation_results 行

CREATE INDEX idx_results_q      ON q_annotation_results(q_id);
CREATE INDEX idx_results_status ON q_annotation_results(batch_id, status);
CREATE INDEX idx_qkp_llm_q  ON q_kp_llm(q_id);
CREATE INDEX idx_qkp_llm_kp ON q_kp_llm(kp_id);   -- 顺带补齐 q_kp 缺失的按 KP 查题路径
CREATE INDEX idx_diff_llm_q ON q_difficulty_llm(q_id);
CREATE INDEX idx_reviews_q  ON annotation_reviews(q_id);
CREATE INDEX idx_batches_status ON annotation_batches(status);

-- 有效批次选择：每 (scope, task_type, strategy) 取最新完成的批次（历史批次全部保留，不覆盖）
CREATE VIEW v_effective_batch AS
SELECT b.* FROM annotation_batches b
 WHERE b.status = 'completed'
   AND NOT EXISTS (
     SELECT 1 FROM annotation_batches b2
      WHERE b2.scope = b.scope AND b2.task_type = b.task_type AND b2.strategy = b.strategy
        AND b2.status = 'completed'
        AND (b2.finished_at > b.finished_at
             OR (b2.finished_at = b.finished_at AND b2.batch_id > b.batch_id)));

-- 消费视图（Phase 2 用）：默认消费 A 臂（主线）；B 臂仅实验对比，按 batch_id 手工查询。
-- 回退规则（第二版核心修正）：仅当题目**没有任何有效 LLM 终态**（ok/unmapped 均无）才回退规则候选；
-- 不可映射（unmapped）永不回退规则。
-- （5-B1 实现期修正：三处 JOIN 增加 b.strategy = 'A_full_list' 过滤——否则 A/B 两臂
--   各有有效批次时同一题会出现两份行，违反"B 臂不混入消费视图"；已同步本节 SQL。）
CREATE VIEW v_q_kp_effective AS
SELECT r.q_id, l.kp_id, 'llm' AS tag_source, l.is_primary, l.review_state, r.batch_id
  FROM q_annotation_results r
  JOIN v_effective_batch b ON b.batch_id = r.batch_id AND b.strategy = 'A_full_list'
  JOIN q_kp_llm l ON l.q_id = r.q_id AND l.batch_id = r.batch_id
 WHERE r.status = 'ok' AND l.review_state <> 'rejected'
UNION ALL
SELECT r.q_id, NULL, 'llm_unmapped', 0, 'pending', r.batch_id
  FROM q_annotation_results r
  JOIN v_effective_batch b ON b.batch_id = r.batch_id AND b.strategy = 'A_full_list'
 WHERE r.status = 'unmapped'
UNION ALL
SELECT k.q_id, k.kp_id, 'rule', 0, 'none', NULL
  FROM q_kp k
 WHERE NOT EXISTS (
   SELECT 1 FROM q_annotation_results r
     JOIN v_effective_batch b ON b.batch_id = r.batch_id AND b.strategy = 'A_full_list'
    WHERE r.q_id = k.q_id AND r.status IN ('ok','unmapped'));
```

**review_state 消费规则**：`pending`＝可作 Phase 2 检索/组卷候选（tag_source 标明来源），不进评测 golden；`approved`＝全用途；`rejected`＝消费侧排除（视图已过滤）；`corrected`＝读修正后值（修正动作直接更新标注行值并置 corrected，历史流水在 annotation_reviews）。评测 golden 仍按决策⑲以 `questions.verified=1`（人工核验标记）为门，review_state 是标注行级状态，两者并行不冲突。

**人工修正动作语义**：approve＝标注行 review_state 置 approved；reject＝置 rejected（该行退出消费）；correct＝按 target 定位旧行→置 rejected 或改值，新值写入对应行（review_state='corrected'），annotation_reviews 留 old/new 流水。对 unmapped 判定的人工修正（field='unmapped', action='correct'）＝人工确认某题实际属于某 KP：将 results.status 改 'ok' 并写入 q_kp_llm 行（review_state='corrected'）——这是**人工标注覆盖模型判定**，与"规则标签回退"是两回事，且不违反"不可映射永不自动回退规则"。

**能力覆盖对照（用户要求的 9 项能力 → 落点）**：

| 能力 | 落点 |
|---|---|
| 规则/LLM 标签区分 | 两表并存 + v_q_kp_effective.tag_source（'llm'/'llm_unmapped'/'rule'） |
| 模型名称及版本 | annotation_batches.model / model_version（响应快照）；决策④统一 deepseek-flash |
| Prompt 版本 | prompt_id/prompt_version/prompt_sha256 + prompts/registry.jsonl（Git） |
| 标注任务批次 | annotation_batches（task_type/strategy/status/计数/时间）；A/B 两臂各一条 |
| 置信度 | confidence（逐条，CHECK 0–1） |
| 审核状态 | review_state 四态 + 消费规则（§5） |
| 人工修正 | annotation_reviews 全历史 + target_kp_id 精确定位 |
| 重复标注/版本对比 | PK 含 batch_id → 同题多版本并存；A/B 对比 = 同题跨批次 JOIN；不同 Prompt/模型批次互不合并（视图按有效批次取数） |
| 失败恢复 | 批次 UNIQUE 幂等 + q_annotation_results 终态驱动断点 + manifest（§9） |
| 标注结果追溯 | q_id → batch_id → model/prompt 版本/sha/配置/费用 全链可查；不可映射与人工修正全留痕 |

## 6. 500 题分层试验方案（决策③：A/B 双臂）

**双臂协议（决策③，已定）**：A 臂（82 全清单直判）与 B 臂（embedding 召回 top-10 + LLM 复判）跑**同一批 500 题**、同一模型 `deepseek-flash`、同一输出 JSON 结构、同一 gold 与评估指标（§7）；两臂费用合并计入 ¥10 总 cap（§8）；两臂结果分别落独立批次（strategy 区分），对比 = 同题跨批次查询。A 仍为主线，B 不因双臂试验改变定位。

**分层原则**：12 层、优先级互斥（S1→S11 依序首中即入层）+ 1 个全域随机参考层（S12 与其余层去重）；规则挂接仅用于**定义抽样层与分层分析**（用户允许的辅助用途），绝不进 prompt、绝不作 gold。**全部抽样 SQL 严格限定 `quality_state='processed'`**（下表池容量均为 processed 口径，2026-11-14 实测）。层内抽样：`random.Random(42)` 对按 q_id 排序的池做确定性抽样，产出冻结清单（Git 存仅 q_id+层标签，不含题目内容——决策㉒）。

| 层 | 定义（processed 池容量，实测） | 配额 | 考察点 |
|---|---|---|---|
| S1 | 零规则挂接（3,621） | 40 | 规则失效区 LLM 表现——LLM 核心价值区 |
| S2 | 主填空形态 `((())/(()))`（1,360） | 30 | 填空标记不被误判为损坏 |
| S3 | 其他空括号形态（306） | 10 | 变体形态 |
| S4 | 含 `*`（11,049） | 20 | 乘号/指数不被当 Markdown |
| S5 | 非标准记法（162 全域，processed 略少，实现时按 processed 重算） | 10 | 极端形态鲁棒性 |
| S6 | 退化+不完整候选（**processed 池 = 574 + 26 = 600**；原始 585∩26 交集实测为 0，585 中 11 行重复剔除未入库；清单见 raw_anomalies_report） | 10 | 缺条件题的处置（应低置信/不可映射而非硬挂） |
| S7 | 挂接 <1,000 的 24 个低频 KP 的题目池（processed） | 40 | 低频知识点覆盖 |
| S8a | 恰 1 个规则挂接（48,004） | 15 | 单知识点 |
| S8b | ≥6 个规则挂接（15,137） | 15 | 多知识点/规则噪声重区 |
| S9 | 难度 1/2/3/4/5 各 30（processed 池 39,056/54,220/41,776/26,457/46,349） | 150 | 难度全谱 |
| S10 | 题干 >70 字符（p90，processed ≈20,800） | 20 | 长题干 |
| S11 | 答案形态：百分数（6,926）/分数（20,766） | 10+10 | 答案形态干扰 |
| S12 | **全域纯随机参考层**（processed 全域，排除上述已选后，seed=42） | 120 | 无偏估计生产准确率（**单独报告，见 §7/§11**） |
| 合计 | | **500** | |

试验执行（5-B，需批准）：双臂各 500 次调用，费用合并 cap ¥10（§8）；S5/S7/S11 池容量在实现时按 processed 口径精确重算并冻结进清单。

## 7. 人工评估标准（≥50 题）

**gold 建立流程**：从 500 题按层配比抽 50（S12 必占 ≥15、S1/S2/S7 各 ≥5）；导出核验表（q_id+题干+空白 gold 栏，staging 不入 Git）→ **助手按与 prompt 同源的规则独立初标（盲于 LLM 输出，仅作人工核验辅助）→ 最终 gold 由用户复核确认**（含分歧仲裁）；仲裁分歧率如实记录（分歧率高=KP 定义歧义信号，反馈治理循环）。

**指标定义**（题级，n=50）：
- **主知识点命中**：LLM.primary ∈ gold.kp_set
- **硬误标**：LLM.kp_ids 中存在 ∉ gold.kp_set 的标签（记个数；语义相邻的同大类误标记"软误标"，只统计不计不合格）
- **难度一致**：|LLM.difficulty − gold.difficulty| ≤ 1
- **题合格** = 主命中 ∧ 无硬误标 ∧ 难度一致；**合格率 = 合格题数/50 ≥ 90% ⇒ 决策㉑工程放行关口**
- 辅助诊断（报告全量输出）：完全集合匹配率、micro-Precision、micro-Recall、难度精确一致率、不可映射处置正确率、invalid 率（目标 <2%）、重跑一致率（同 50 题双跑输出一致率 ≥95%）
- **S12 纯随机层指标单独报告**，作为生产准确率的无偏参考；50 题总体 ≥90% 仅是工程放行指标，**不直接宣称全库无偏准确率**（全库无偏估计以 S12 与全量后抽检为准）

**未达标处理循环**（不通过绝不进入任何扩大规模）：错误归因三桶 → ①KP 定义歧义（含 gold 仲裁分歧）→ 修 KP 描述**需用户逐条批准**（不擅改 82 定义）；②模型混淆对（如 相遇/追及、长方形/正方形周长）→ prompt v2 增反例与边界说明，prompt_version 升版重跑 500（成本 ≈¥0.5）；③难度锚点争议 → 修难度定义 → 重跑。每轮循环重做 50 题人工核验。

## 8. API 成本测算与预算闸门（价格 2026-11-13 官方核实）

**价格来源**：api-docs.deepseek.com 中文定价页（2026-11-13 抓取）。现行两个模型（元/百万 tokens，输入缓存命中/未命中/输出）：

| 模型 | 高峰（工作日 9–12、14–18，北京时间） | 空闲（其余时段＝半价） |
|---|---|---|
| **deepseek-flash**（DeepSeek-V4.1-Flash-1017） | 0.04 / 2 / 8 | 0.02 / 1 / 4 |
| deepseek-v4-pro（DeepSeek-V4-Pro-0813） | 0.30 / 9.0 / 27.0 | 0.15 / 4.5 / 13.5 |

**决策④落实**：配置默认值显式改用 `deepseek-flash`（弃用遗留别名 `deepseek-v4-flash`）；批次记录请求模型名并尽量记录响应 `model_version` 快照；A/B 两臂同模型；**不切换 deepseek-v4-pro**（v4-pro 仅作为未来可选复核手段记录于此，未授权启用）。

**A 臂 Token 假设【估算，试跑用实测 usage 校准】**：固定前缀 ≈1,900 tok（82 定义合计 1,477 字符）；每题输入 ≈70 tok（题干均值 46 字）；输出 ≈90 tok（JSON）。重试/失败余量统一 **+8%** 已计入下表。

**B 臂 Token 假设【估算，待 5-B2 实测校准】**：固定前缀 ≈600 tok（缓存命中）；候选块 = embedding 召回 **top-10** KP（候选数量为设计参数，recall@10 未知待实测）≈200 tok【估算】逐题不命中缓存；题干+包装 ≈70 tok；输出 ≈90 tok（与 A 同构）。**本地 embedding 零 API 费用**（CPU，≈30ms/题→207,858 题 ≈1.7h、内存 <500MB【估算】）。重试余量同 A。

**分规模费用【估算，元；已含 8% 重试余量】**：

| 规模 | A 高峰缓存 | A 空闲缓存 | A 高峰无缓存 | A 空闲无缓存 | B 高峰缓存 | B 高峰无缓存 |
|---|---|---|---|---|---|---|
| 500×2 臂（试跑合计） | **≈1.2** | ≈0.6 | **≈3.9** | ≈1.9 | （已并入左列合计） | （已并入） |
| 2,000（A） | 2.0 | 1.0 | 10.1 | 5.0 | 2.8 | 5.3 |
| 5,000（A） | 5.1 | 2.5 | 25.2 | 12.6 | 6.9 | 13.3 |
| 20,000（A） | 20.2 | 10.1 | 100.7 | 50.3 | 27.7 | 53.1 |
| **207,858（A 全库）** | **≈210** | **≈105** | **≈1,046** | **≈523** | ≈288 | ≈552 |

（单独拆分：A 臂 500 题 ≈0.51 高峰缓存 / ≈2.52 高峰无缓存；B 臂 500 题 ≈0.69 / ≈1.33。）

**预算闸门（决策㉖三线，2026-11-14 修订；决策②基础条款延续）**：

1. **500 题双臂试跑硬停线 cap = ¥20**：A/B 两臂共用同一 budget_group，任何一次 API 调用（含重试）都计入组级累计。
2. **三线规则**：**¥10 提醒线**——通知并记录实际支出，不自动停止；**¥15 暂停线**——停止提交新请求（在途调用排水完成），输出进度、实际费用、剩余题量和预计剩余费用，等待人工确认；**¥20 硬停止线**——绝对上限，禁止任何新请求，不得通过重试、续跑或新建批次绕过（组级累计：台账初始值 = 同 budget_group 全部历史批次的 cost_cny_est 之和）。
3. **缓存命中不作为预算保护前提**：费用预留一律按无缓存高峰价最坏口径（¥20 cap 相对双臂无缓存最坏 ≈¥3.9 有充分余量；缓存命中只会让实际支出更低）。
4. **不能因账户余额充足而扩大本次实验范围**：本次授权仅 500 题 A/B 双臂；20,000 题扩展与全量标注仍未授权，每一级扩大规模须用户单独批准预算与执行范围。

吞吐【估算】：并发 ≤8 × ≈2.5s/题 → 全库 ≈18–24h，建议空闲时段分夜执行（成本低一半）。

## 9. 工程可靠性设计

| 机制 | 设计 |
|---|---|
| 幂等执行 | 决策㉑幂等键 (q_id, prompt_version)：批次 UNIQUE(scope,task_type,strategy,model,prompt_id,prompt_version) → 同键重跑复用批次；逐条 PK 含 batch_id → 已成功项跳过 |
| 断点续跑 | 以 q_annotation_results 终态驱动：无行或 status ∈ (failed, invalid) → 待重跑（INSERT OR REPLACE 覆盖中间态）；逐条独立事务，进程被杀不损已写数据 |
| 失败重试/退避 | 可重试错误（429/超时/连接/5xx）指数退避 1→2→4→8→16s（含抖动）max 5 次；输出校验失败→1 次带错误反馈的纠正重试→计 invalid（写 results 行，不入标签表） |
| 熔断 | 连续 20 次失败或失败率 >20%（≥100 样本）→ 自动暂停批次（status=aborted），报告后再人工续跑 |
| 结果缓存 | 响应缓存键 (q_id, prompt_sha256, model)：staging JSONL；同 prompt 重跑直接复用不调 API，prompt 变更不命中 |
| **并发与限流（AIMD）** | 初始并发 4，**上限 8**；稳定运行后逐步+1 缓升；**收到 429 → 并发降半（下限 1）+ 指数退避**；连续 429 继续降并发；稳定恢复后再缓慢升回。429 永远是降并发的信号。官方未公开硬性 RPM【未知】，以 429 退避为限流信号 |
| 费用控制 | 每次调用取 usage（prompt_tokens/completion_tokens/prompt_cache_hit_tokens）→ **budget_group 级累计（含历史批次，防绕过）** + 批次级累计；单价表配置化（技术方案 §6.2）；三线闸门见 §8；每 500 条打印进度+累计费用 |
| 停止机制 | 三线（决策㉖）：提醒线=通知记录不停止／暂停线=停止新请求待人工确认／硬停线=绝对禁止；`--budget-warn-cny`/`--budget-pause-cny`/`--budget-cny` + `--max-items` / Ctrl+C（安全点停，status=aborted 已写数据无损） |
| 日志与统计 | backend.logging_config 常规日志 + 逐条摘要行（q_id/状态/重试/tokens/费用/延迟）；批次汇总落 annotation_batches |
| Prompt 版本管理 | `data/annotations/prompts/kp_primary_v1.md`（A 臂）/`kp_recall_v1.md`（B 臂）（Git）+ `registry.jsonl`（prompt_id/version/sha256/日期/变更说明）；prompt 改动必须升版本号，禁止原地改 |
| **manifest 分层（决策①，决策㉑的落实方式）** | **运行级 manifest JSONL 入 Git**：`data/annotations/manifests/<batch_id>.jsonl`，记录 batch_start / 周期 checkpoint（每 1,000 题）/ batch_end / 失败题目汇总（failed+invalid q_id）/ Token 与费用汇总。**逐题明细 JSONL 不入 Git**：`data/staging/tag_detail_<batch_id>.jsonl`，保留每题实际标注结果、调用状态与调试信息（重试次数、错误、原始响应摘要）。207,858 条逐题明细（≈25MB）**绝不提交 Git**（符合决策⑳防大文件精神） |
| 吞吐 | AIMD 并发 × ≈2.5s/题【估算】→ 全库 ≈18–24h，分夜间空闲时段执行 |

## 10. 分阶段实施计划（每步均需用户放行）

| 阶段 | 内容 | 授权状态 |
|---|---|---|
| **5-A（本轮）** | 现状调查 + 本设计文档（第二版） | 待用户审查批准 |
| **5-B1** | V2 迁移文件 + prompt v1（A/B 两份）+ `build_trial500.py`/`tag_questions.py`/核验表导出脚本 + mock LLM 单测；**不调 API** | 待用户批准 Schema+prompt 后放行 |
| **5-B2** | **500 题双臂试跑**（API Key 核验存在可用，不打印；A/B 合并 cap ¥10，80% 告警/100% 硬停）→ 50 题人工核验（gold 由用户确认）→ 试跑报告（指标含 S12 单独层 + **实测 token/缓存命中率/两臂成本**） | **已授权范围：500 题双臂 + ¥10**（决策②③） |
| **5-B3** | 20,000 题扩展试跑 | **未授权**——须 5-B2 报告后按实测成本单独审批 |
| **5-B4** | 全量 207,858 | **默认禁止**——须用户单独批准预算与范围（届时刻按无缓存最坏口径设 cap） |
| **5-C** | 初中题打标（待 F16 实际导入数据后单独规划，303 KP 体系协议复用） | 届时另立项 |

## 11. 验收标准（Step 5 整体）

1. 试跑关口：50 题合格率 ≥90%（**工程放行指标**），invalid <2%，重跑一致率 ≥95%；**S12 纯随机层指标单独报告**（无偏参考），不与放行指标混用
2. 全量关口（若届时获批）：抽检 200 题合格率 ≥88%；不可映射率如实报告（数量与示例清单）；全库无偏结论以抽检+S12 为准
3. 溯源完备：任一 LLM 标签可查 批次→模型（含 model_version 快照）→prompt 版本/sha→置信度→审核状态；规则标签与 LLM 标签零混淆（两表分离 + 视图来源列）
4. 不可映射正确性：unmapped 题在消费视图中**不回退规则**（单测 + validate 断言）；人工修正可覆盖且留痕
5. 预算可复算：manifest + 单价表 → 逐批（含两臂合并组）费用可独立重算，与批次记录一致；闸门触发记录留痕
6. 决策㉔合规自检：prompt 构造查询无 q_kp JOIN（代码审查+单测断言）；82 KP 定义 sha 与库内一致；规则挂接数据零改动
7. Schema 一致性：V2 迁移后 confidence 0–1 约束、外键（kp_id/batch_id）、UNIQUE 幂等键全部生效（迁移测试）
8. 测试基线：全量 pytest 通过（新增 mock 单测不含真实 API 调用）

## 12. 风险与待裁决事项

| # | 风险/事项 | 处置 | 状态 |
|---|---|---|---|
| R1 | 82 KP 体系源自规则视角，部分题可能真无可挂知识点 | prompt 允许空输出并持久化；不可映射率如实报告；>15% 则提案扩充 KP（编号冻结只许追加） | 届时报用户裁决 |
| R2 | 缓存命中率不保证（DeepSeek 未承诺） | **预算保护一律按无缓存最坏口径校验（决策②）**；实测命中率 <60% 触发 B 臂评估 | 已定机制 |
| R3 | 模型输出非确定性（temperature=0.2） | 重跑一致率指标 ≥95%；JSON 强制输出 | — |
| R4 | ~~模型名为遗留别名~~ | **已定（决策④）**：显式 deepseek-flash + 响应快照；`.env` 实际覆盖值未读取（不读取 Key/环境），5-B 开工核验时确认 | 已关闭（5-B 核验确认执行） |
| R5 | ~~manifest 口径待确认~~ | **已定（决策①）**：运行级入 Git / 逐题明细入 staging | 已关闭 |
| R6 | KP 定义平均 13.5 字，边界判定依赖模型常识 | few-shot + 反例；gold 分歧率监控；定义修订权在用户 | 定义修订逐条批准 |
| R7 | ETL 全量重建（幂等语义）会与标注表孤儿化 | 标注表不设 q_id 外键（同 q_kp 风格）；重建后 validate 提示重挂 | — |
| R8 | 空括号/星号/循环小数形态被模型误读为损坏 | prompt 特殊形态说明（§4.3）+ 500 题 S2–S6 层专门考察 | — |
| R9 | 费用/时长超预期 | **三线闸门（决策㉖：¥10 提醒/¥15 暂停/¥20 硬停）**+熔断+空闲时段执行；任何扩大规模须单独批准 | 已定机制 |
| R10 | 人工核验耗用户时间 | 助手盲标初稿（仅辅助）+ **用户确认最终 gold**（§7） | 流程已定 |
| R11 | 初中 303 KP prompt 前缀更长（≈12k tok）缓存价差扩大 | 5-C 届时重估 B 臂必要性 | 届时裁决 |
| R12 | 网络环境（TUN）影响 API 连通性 | 重试/退避覆盖；试跑先 50 题连通验证 | — |
| R13 | **B 臂 recall@10 未知**（KP 描述短、尾部 KP 样本少） | 5-B2 以 gold 实测 recall@10 与漏召回错误清单；不足则 B 臂降级为纯对照存档 | 待 5-B2 实测 |
| R14 | B 臂候选块 Token 实际值与 ≈200 估算偏差 | 试跑用实测 usage 校准全部 Token 假设后重算成本表 | 待 5-B2 实测 |

**仍存的不确定项（明确标记，不由助手代决）**：①B 臂 recall@10 与候选块实际 Token（R13/R14，待实测）；②官方 RPM 硬限制未知（自适应退避覆盖）；③`.env` 是否覆盖 DEEPSEEK_MODEL 默认值（5-B 核验时确认，本轮不读取）；④DeepSeek tokenizer 精确分词率（所有 Token 假设均为估算区间，试跑校准）。

## 13. B 臂 Embedding 召回实现方案（2026-11-14，**依赖未获批前不安装**）

**现状**：`recall.py` 为占位 fail-fast（RecallNotAvailableError）；执行器 B 臂路径已由 Mock 测试覆盖（候选限制/空召回→invalid/越界纠错/无召回器拒绝）。本节为 5-B2 开工技术方案，**待用户确认依赖与资源清单后实施**。

| 项 | 方案 | 状态 |
|---|---|---|
| 推荐依赖 | **fastembed**（内置 bge-small-zh-v1.5 支持，ONNX Runtime 后端，无 torch 重依赖）+ 其传递依赖 onnxruntime；备选 sentence-transformers+torch（体积大，不推荐） | 待批 |
| 版本 | 获批安装时以 pip 实际解析为准（写入 requirements.txt 并记录） | 待批 |
| 模型 | **BAAI/bge-small-zh-v1.5**（~24M 参数，512 维——与 knowledge_points.embedding 列注释既定模型一致） | — |
| 来源/下载大小 | fastembed 首次运行自动下载**量化 ONNX** 模型至本地缓存（约 30–35MB【待下载实测，不编造精确值】；无需 ~100MB 原始 torch 权重）；国内网络需 TUN/代理（与 clone APE210K 同环境） | 待实测 |
| 磁盘占用 | 模型缓存 ~35MB【估算】+ pip 依赖安装 ~150MB（Windows wheel 含 onnxruntime【估算】） | 待实测 |
| 内存 | 推理进程 <300MB（int8 ONNX CPU）【估算】 | 待实测 |
| 本地推理方式 | CPU 进程内；单条目标 <50ms（技术方案 §11.2 既有阈值）；82 条 KP 定义仅 embedding 一次驻内存；每题 embedding 题干一次 → 余弦相似度 top-10（RECALL_TOP_K=10，候选数待 gold 实测校准）；**零 API 费用** | 试跑实测 |
| top-10 实现与测试 | 候选文本 = KP name+description（与 A 臂 prompt 同源快照，不触 q_kp——决策㉔）；召回结果过滤越界 id；候选空/不足如实传 LLM（B 臂 prompt 已声明候选可能不全）。单测：依赖获批后补——①82 KP 构造样本 sanity（每个 KP 一条典型题干 → top-1 命中自证）；②越界 id 过滤；③空召回路径（已有 Mock 覆盖） | 依赖获批后补 |
| recall@10 评估 | 5-B2 以 50 题 gold：**recall@10 = gold 主知识点 ∈ B 臂候选集的比例**（B 臂专属指标，与两臂通用指标分开报告；另记 recall@5 参考）；漏召回题逐条列出（R13 治理输入） | 待试跑 |
| 新增内容清单 | pip 包：fastembed、onnxruntime（版本获批时定）；网络：HF 下载通道；磁盘：~185MB；无 GPU、无服务进程 | **待用户确认** |

---

## 附一：决策㉔五条约束 → 本设计落点

| 约束 | 落点 |
|---|---|
| 不将 q_kp 规则挂接作为 prompt 输入 | §4 红线 + 单测断言（prompt 组装查询无 q_kp JOIN） |
| 不直接继承规则标签为输出 | 输出仅来自模型 JSON；规则标签不出现在任何输入/流程决策 |
| 限 82 体系内语义匹配 | A 臂清单 = knowledge_points 82 行逐字快照 + kp_id 白名单校验；B 臂候选为其子集 |
| 溯源必留（来源/模型/Prompt 版本/置信度/审核状态） | annotation_batches + q_annotation_results + q_kp_llm/q_difficulty_llm 全字段（§5） |
| 规则标签与 LLM 标签明确区分 | 独立新表，q_kp 零改动；v_q_kp_effective.tag_source 显式 |

## 附二：既有技术决策保持清单（本轮未改变）

1. A 主线 / B 对照备选 ✓（§3）　2. 82 KP 定义逐字入 prompt ✓（§4）　3. q_kp 规则标签绝不进 prompt ✓　4. 规则/LLM 标签独立存储 ✓（§5）　5. 只标知识点+难度 ✓（§2）　6. 对象 207,858 processed ✓（§2）　7. 500 试跑/50 核验/90% 关口保留 ✓（§6/§7）　8. 全量须单独批准 ✓（§8/§10）　9. 初中题另行规划 ✓　10. 不引入无关技术 ✓（§2）　11. 不改现有规则标签 ✓（§5）　12. 不扩大范围 ✓（本轮仅文档修订）

*本轮（5-A 第二版）交付物即本文档及 §0 修订记录；未改动任何代码、数据库、Schema、KP 定义、Prompt 文件；未调用任何 API。等待用户审查批准后进入 5-B1。*
