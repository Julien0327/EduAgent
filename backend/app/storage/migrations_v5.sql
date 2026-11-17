-- V5（2026-11-17，Phase 3 / S4，§6）：Trace 观测列
-- steps 表原已有 tokens_in/tokens_out/cost/latency_ms 列但从未写入；
-- 本次补齐模型调用观测能力：
--   llm_model    节点内调用的模型名（多个时逗号分隔去重；无调用为 NULL）
--   usage_source provider（全部真实 usage）/ partial（部分缺失）/
--                unavailable（有调用但无 usage，tokens/cost 为 NULL）/
--                none（节点无模型调用，tokens/cost=0 为事实值）
--   llm_calls    JSON 数组：逐次调用明细（含重试与纠错轮），每条含
--                model/tokens/单价快照/价格表版本/period/cost/latency/error。
--                成本可复算依据（Σ明细 = steps.cost）。
-- 迁移幂等：由 schema_migrations 版本表保证只应用一次；ALTER 只增列，
-- 不触碰已有 tasks/steps 历史数据（旧行新列为 NULL）。

ALTER TABLE steps ADD COLUMN llm_model TEXT;
ALTER TABLE steps ADD COLUMN usage_source TEXT;
ALTER TABLE steps ADD COLUMN llm_calls TEXT;
