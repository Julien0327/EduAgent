-- V3（2026-11-14，Phase 2 / S1，决策㉛①）：知识点关键词检索表
-- kp_fts 与 questions_fts 同风格（unicode61 分 token）；文档文本由
-- backend.app.retrieval.tokenize.fts_document() 写入（jieba 预分词空格连接）。
-- 填充采用检索层启动自愈同步（385 行全量重建 <1s，不进 ETL 幂等命令链）：
-- KP 数据更新（图谱重跑/KP 定义修订）无需记得重跑索引脚本。
-- 注意：questions_fts 的分词化改造不改本迁移——其文档文本由 etl_ape210k.py
-- 幂等重建写入（全量重建语义不变）。

CREATE VIRTUAL TABLE kp_fts USING fts5(
  kp_id UNINDEXED, name, description, tokenize='unicode61'
);
