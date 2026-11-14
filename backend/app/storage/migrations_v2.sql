-- V2（2026-11-14，Phase 1 / Step 5-B1）：LLM 打标注解表
-- 设计依据：docs/development/STEP5-ANNOTATION-DESIGN.md 第二版 §5（用户已批准的 Schema 提案）。
-- 原则：全部为新增表/索引/视图；不改 questions/q_kp/knowledge_points 现有列
-- （决策㉔：现有规则挂接零改动）。
-- q_annotation_results/q_kp_llm/q_difficulty_llm 的 q_id 不设外键（与 q_kp 同风格）：
-- ETL 全量重建语义会删除重插 q_ape_% 行，无外键避免连锁破坏；孤儿行由 validate 脚本核对。
-- 决策㉑幂等键 (q_id, prompt_version) 经 batch_id 落地：批次 UNIQUE 约束见 annotation_batches。

CREATE TABLE annotation_batches (
  batch_id     TEXT PRIMARY KEY,      -- ann_<yyyymmdd>_<两位序号>_<slug>
  task_type    TEXT NOT NULL DEFAULT 'kp_difficulty' CHECK (task_type IN ('kp_difficulty')),
               -- 一次调用同时产出知识点+难度（第二版设计）
  scope        TEXT NOT NULL,         -- 'ape_primary'
  strategy     TEXT NOT NULL,         -- 'A_full_list' / 'B_recall'（A/B 两臂独立批次）
  provider     TEXT NOT NULL DEFAULT 'deepseek',
  model        TEXT NOT NULL,         -- 请求模型名；决策④：统一 'deepseek-flash'
  model_version TEXT,                 -- API 响应返回的模型快照（完成后回填）
  prompt_id    TEXT NOT NULL,         -- A 臂 'kp_primary' / B 臂 'kp_recall'
  prompt_version TEXT NOT NULL,
  prompt_sha256  TEXT NOT NULL,       -- prompt 文件指纹（防漂移）
  kp_snapshot_sha256 TEXT,            -- 知识点清单快照指纹
  budget_group TEXT,                  -- 预算组：同组批次共享闸门（决策②）
  status       TEXT NOT NULL DEFAULT 'running'
               CHECK (status IN ('running','completed','aborted','failed')),
  config_json  TEXT,                  -- {temperature,concurrency,retries,...}
  total_questions INTEGER DEFAULT 0,
  succeeded    INTEGER DEFAULT 0,     -- results.status=ok
  unmapped     INTEGER DEFAULT 0,     -- results.status=unmapped（有效标注结果）
  failed       INTEGER DEFAULT 0,
  invalid      INTEGER DEFAULT 0,
  input_tokens_sum    INTEGER DEFAULT 0,
  output_tokens_sum   INTEGER DEFAULT 0,
  cache_hit_tokens_sum INTEGER DEFAULT 0,
  cost_cny_est REAL DEFAULT 0,
  created_at   TEXT NOT NULL,
  finished_at  TEXT,
  notes        TEXT,
  UNIQUE (scope, task_type, strategy, model, prompt_id, prompt_version)
);

CREATE TABLE q_annotation_results (   -- 题级标注终态（不可映射的持久化载体）
  q_id      TEXT NOT NULL,
  batch_id  TEXT NOT NULL REFERENCES annotation_batches(batch_id),
  status    TEXT NOT NULL CHECK (status IN ('ok','unmapped','failed','invalid')),
            -- ok=已产出标签；unmapped=模型判定无可挂知识点（有效标注结果，永不回退规则）；
            -- failed/invalid=中间态，重试成功后 INSERT OR REPLACE 覆盖
  primary_kp_id TEXT REFERENCES knowledge_points(kp_id),
            -- ok 时非空且与 q_kp_llm 的 is_primary=1 行一致（validate 核对）；unmapped 为空
  reason    TEXT,                      -- 模型 reason（≤60 字截断持久化）
  created_at TEXT NOT NULL,
  PRIMARY KEY (q_id, batch_id)
);

CREATE TABLE q_kp_llm (               -- LLM 知识点标注（规则挂接仍在 q_kp，两表并存=来源可区分）
  q_id    TEXT NOT NULL,
  kp_id   TEXT NOT NULL REFERENCES knowledge_points(kp_id),
  batch_id TEXT NOT NULL REFERENCES annotation_batches(batch_id),
  is_primary INTEGER NOT NULL DEFAULT 0,
  confidence REAL CHECK (confidence IS NULL OR (confidence BETWEEN 0 AND 1)),
  review_state TEXT NOT NULL DEFAULT 'pending'
               CHECK (review_state IN ('pending','approved','rejected','corrected')),
  created_at TEXT NOT NULL,
  PRIMARY KEY (q_id, kp_id, batch_id)
);

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
  target_kp_id TEXT,                  -- field='kp' 时被修正标注行的 kp_id（精确定位）
  old_value TEXT, new_value TEXT,     -- kp_id 或难度值；approve 时 new_value 为空
  action TEXT NOT NULL CHECK (action IN ('approve','reject','correct')),
  reviewer TEXT NOT NULL DEFAULT 'user',
  reason  TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_results_q       ON q_annotation_results(q_id);
CREATE INDEX idx_results_status  ON q_annotation_results(batch_id, status);
CREATE INDEX idx_qkp_llm_q       ON q_kp_llm(q_id);
CREATE INDEX idx_qkp_llm_kp      ON q_kp_llm(kp_id);
CREATE INDEX idx_diff_llm_q      ON q_difficulty_llm(q_id);
CREATE INDEX idx_reviews_q       ON annotation_reviews(q_id);
CREATE INDEX idx_ann_batches_status ON annotation_batches(status);

-- 有效批次：每 (scope, task_type, strategy) 取最新完成的批次（历史批次全保留，不覆盖）
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
-- 回退规则：仅当题目无任何有效 LLM 终态（ok/unmapped 均无）才回退规则候选；
-- 不可映射（unmapped）永不回退规则（第二版核心修正）。
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
