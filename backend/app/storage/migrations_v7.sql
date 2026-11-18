-- Phase 4 / S4（2026-11-18）：eval_runs 复合主键修正（V6 历史版本差异）
--
-- 背景：V6 曾以"唯一索引"版本在部分库应用（版本号已记录），后续改为
-- 复合主键 (run_id, case_id) 重建；已记录 V6 的库不会重跑迁移。
-- V7 无条件重建：无论旧结构如何，统一为 (run_id, case_id) 复合主键。
-- 幂等：重复执行（如新库先跑过 V6 复合版）结果一致。

CREATE TABLE IF NOT EXISTS eval_runs_v7 (
  run_id     TEXT NOT NULL,
  case_id    TEXT NOT NULL,
  mode       TEXT NOT NULL,
  passed     INTEGER NOT NULL,
  metrics    TEXT,
  detail     TEXT,
  created_at TEXT,
  PRIMARY KEY (run_id, case_id)
);

INSERT INTO eval_runs_v7 (run_id, case_id, mode, passed, metrics, detail, created_at)
  SELECT run_id, case_id, mode, passed, metrics, detail, created_at FROM eval_runs;

DROP TABLE eval_runs;
ALTER TABLE eval_runs_v7 RENAME TO eval_runs;

DROP INDEX IF EXISTS idx_eval_runs_run_case;