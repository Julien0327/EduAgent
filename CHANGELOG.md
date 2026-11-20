# CHANGELOG

## 2026-11-20 — Phase 6（工程完善与上线）

- 公开仓库合规：全历史扫描 high=0；移除个人材料；补 MIT LICENSE 与数据许可说明。
- 合成演示题库：82 KP × 5 难度 = 410 题；模板 205 + LLM 205；人工抽检 41/41 通过；
  冻结 `data/demo/demo_bank_v1.jsonl`（sha256 `f938c95a…`）。
- 合成演示班级：20 学生 × 2 考试 × 20 题 = 800 答题；诊断 3 薄弱 KP / 3 归因链 /
  5 推荐组；线上演示库仅含合成内容，APE210K 残留 0。
- 工程：README 重写、依赖锁定、Python 3.13 非 root 镜像、compose 仅回环端口、
  nginx/HTTPS 模板、部署手册、SQLite 备份脚本、CI 镜像构建。

## 2026-11-19 — Phase 5（质检环 + MCP）

- critique 质检环（question_gen）：三维规则 + LLM 一题一验 + ≤2 轮回流 + 三态降级 + 6 指标。
- MCP 开放 3 工具：stdio + Streamable HTTP、双 Token、敏感开关、DNS Rebinding 防护。
- 真实 10 条复跑（修复后）：10/10 completed、78/78 passed、0 unverified、¥0.567471。
- 全量 542 tests。

## 2026-11-18 — Phase 4（评测体系）

- golden 111 条；Fake/Online runner；预算三线；judge 校准 4 轮，64.0% 未达阈值，
  按规格回退规则评分并记录。
- eval API + 前端评测中心；GitHub Actions CI（pytest + fake smoke）。

## 2026-11-17 — Phase 3（归因 + 可观测）

- 小学前置图 82 KP / 87 边；确定性掌握度、归因、真实题库推荐。
- Trace/成本逐次记录、价格版本快照、重启后库直读回放。
- 10 例 GT 归因一致率 10/10（仅代表冻结案例算法一致性）。

## 2026-11-15 — Phase 2（可信检索）

- 三阶段检索、中文 FTS5、ONNX BGE、引用注入、多轮会话、认证与限流。
- 正式基线（49 条小学查询）：R@1 32.65% / R@5 55.10% / MRR 0.4335 / NDCG@5 0.4201；
  目标 R@5 80% 未达，按「基线 + 已知限制」记录。

## 2026-11-14 — Phase 1（数据地基）

- APE210K ETL：210,488 → processed 207,858；独立重放核验 0 差异。
- 小学 82 KP 规则标签；F16 Excel 导入七步管线；LLM 打标管线与 500 题 A/B 试验。

## 2026-11-10 — Phase 0（工程地基）

- 统一 `backend.*` 导入；Docker/compose；CORS 白名单；CI 前身；README 重写。
