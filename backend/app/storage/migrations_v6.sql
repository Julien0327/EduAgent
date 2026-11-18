-- Phase 4 / S2（2026-11-18）：评测运行结果表结构修正
--
-- V1 的 eval_runs 以 `run_id PRIMARY KEY`（单列）建模，意味着"每个 run 只有
-- 一行"。但评测语义（§9.3「runner 幂等：同一 case 重复执行覆盖同 run_id 结果集」）
-- 是：一次评测（一个 run_id）包含 111 个 case，每个 case 一行；
-- 同一 run+case 重跑 = UPSERT 覆盖（幂等、无重复行）。
--
-- 因此改为复合主键 (run_id, case_id)。eval_runs 当前 0 行（全新特性），
-- 用标准 SQLite 表重建（CREATE→COPY→DROP→RENAME）无损迁移。

CREATE TABLE IF NOT EXISTS eval_runs_new (
  run_id     TEXT NOT NULL,
  case_id    TEXT NOT NULL,
  mode       TEXT NOT NULL,            -- fake / online
  passed     INTEGER NOT NULL,
  metrics    TEXT,                     -- JSON：该用例各指标结果
  detail     TEXT,
  created_at TEXT,
  PRIMARY KEY (run_id, case_id)
);

INSERT INTO eval_runs_new (run_id, case_id, mode, passed, metrics, detail, created_at)
  SELECT run_id, case_id, mode, passed, metrics, detail, created_at FROM eval_runs;

DROP TABLE eval_runs;
ALTER TABLE eval_runs_new RENAME TO eval_runs;

-- 复合主键已隐式提供 (run_id, case_id) 唯一性，无需额外索引。