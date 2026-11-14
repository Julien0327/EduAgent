"""阶段 1：知识点双路召回（技术方案 §4.2，Phase 2 / S3）。

对象=知识点。双路召回：
- 关键词路：kp_fts（jieba 预分词 MATCH，S1）；
- 向量路：查询 embedding（bge，加查询指令前缀）× KP 向量矩阵内积
  （knowledge_points.embedding BLOB 启动载入为 numpy 矩阵，纯内存内积，
  §4.3 不引入向量库）。

融合（§4.2）：两路分数**各自归一化**（FTS bm25 rank min-max、余弦 min-max）
后加权 ``score = α·kw_norm + (1-α)·vec_norm``，α=``RETRIEVAL_ALPHA``=0.4 起步
（评测期只允许整档调整 0.3/0.4/0.5 并记录效果——不同检索对象分数不可直接
相加，归一化是前提，面试考点）。融合 → 去重 → 规则重排（名称精确包含查询
加小分）→ top-k。

自愈同步：kp_fts 行集合与 knowledge_points 不一致时全量重建（385 行 <1s，
不入幂等命令链；KP 描述/向量更新后由 build_kp_embeddings.py 清空 kp_fts，
服务下次启动自愈重建）。
"""
from __future__ import annotations

import threading

import numpy as np

from backend.app.retrieval.citations import require_ref
from backend.app.retrieval.embedding import EmbeddingService, get_embedding_service
from backend.app.retrieval.tokenize import fts_document, fts_query, segment
from backend.app.storage import db

RETRIEVAL_ALPHA = 0.4  # §4.2 起步值；评测期整档调整（0.3/0.4/0.5）并记录
KP_RECALL_TOP_K = 10   # §4.2 默认 k=10


def _minmax(values: dict[str, float]) -> dict[str, float]:
    """min-max 归一化（单值/空输入安全）。"""
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi - lo < 1e-12:
        return {k: 1.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


class KpRetriever:
    """知识点召回器（启动载入 KP 元数据 + 向量矩阵；进程内单例复用）。"""

    def __init__(self, db_path=None, embedding_service: EmbeddingService | None = None):
        # check_same_thread=False：单例连接跨线程复用（FastAPI sync 端点在线程池
        # 执行）；所有访问经 self._lock 串行化（sqlite3 对象本身非线程安全）
        self._conn = db.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._embedder = embedding_service or get_embedding_service()
        with self._lock:
            self._load_kp()
            self._sync_kp_fts()

    # ---------- 启动载入 ----------

    def _load_kp(self) -> None:
        rows = self._conn.execute(
            "SELECT kp_id, name, stage, grade, chapter, description, embedding "
            "FROM knowledge_points ORDER BY kp_id"
        ).fetchall()
        if not rows:
            raise ValueError("knowledge_points 为空——先建图谱（build_graph.py/build_primary_kp.py）")
        missing = [r["kp_id"] for r in rows if r["embedding"] is None]
        if missing:
            raise ValueError(
                f"{len(missing)} 个 KP 缺向量（如 {missing[:3]}）——"
                "先跑 build_kp_embeddings.py（Phase 2/S3）")
        self._kp_meta = {
            r["kp_id"]: {
                "kp_id": r["kp_id"], "ref": r["kp_id"],  # §4.4：图谱取数必带 ref
                "name": r["name"], "stage": r["stage"], "grade": r["grade"],
                "chapter": r["chapter"], "description": r["description"] or "",
            }
            for r in rows
        }
        vecs = np.stack([
            np.frombuffer(r["embedding"], dtype="float32") for r in rows
        ])
        self._kp_ids = [r["kp_id"] for r in rows]
        self._kp_vecs = vecs  # 已 L2 归一化（写入侧保证）

    def _sync_kp_fts(self) -> None:
        """kp_fts 自愈同步：行集合与 KP 表不一致时全量重建（见模块 docstring）。"""
        db_ids = set(self._kp_meta)
        fts_ids = {r["kp_id"] for r in self._conn.execute("SELECT kp_id FROM kp_fts")}
        if db_ids == fts_ids:
            return
        with self._conn:
            self._conn.execute("DELETE FROM kp_fts")
            self._conn.executemany(
                "INSERT INTO kp_fts (kp_id, name, description) VALUES (?, ?, ?)",
                [
                    (kp_id, fts_document(m["name"]), fts_document(m["description"]))
                    for kp_id, m in self._kp_meta.items()
                ],
            )

    # ---------- 双路召回 ----------

    def _keyword_scores(self, query: str, stage: str | None) -> dict[str, float]:
        """关键词路：kp_fts bm25 rank → 分数（rank 为负值，越小越相关）。"""
        q = fts_query(query)
        if q == '""':
            return {}
        sql = "SELECT kp_id, bm25(kp_fts) AS rank FROM kp_fts WHERE kp_fts MATCH ?"
        params: list = [q]
        rows = self._conn.execute(sql, params).fetchall()
        if stage:
            rows = [r for r in rows if self._kp_meta[r["kp_id"]]["stage"] == stage]
        # bm25 rank 越小越相关 → 取负转分数（越大越相关）再归一化
        return {r["kp_id"]: -float(r["rank"]) for r in rows if r["rank"] is not None}

    def _vector_scores(self, query: str, stage: str | None) -> dict[str, float]:
        """向量路：查询向量 × KP 矩阵内积（余弦，已归一化向量）。"""
        qv = self._embedder.embed_query(query)
        sims = self._kp_vecs @ qv
        out: dict[str, float] = {}
        for i, kp_id in enumerate(self._kp_ids):
            if stage and self._kp_meta[kp_id]["stage"] != stage:
                continue
            out[kp_id] = float(sims[i])
        return out

    def recall(self, query: str, stage: str | None = None,
               top_k: int = KP_RECALL_TOP_K) -> list[dict]:
        """双路召回 + 归一化融合 + 规则重排，返回 top-k KP（带 ref，§4.4）。"""
        query = str(query).strip()
        if not query:
            return []
        with self._lock:
            kw_raw = self._keyword_scores(query, stage)
            vec_raw = self._vector_scores(query, stage)
        kw_norm = _minmax(kw_raw)
        vec_norm = _minmax(vec_raw)
        alpha = RETRIEVAL_ALPHA

        fused: dict[str, float] = {}
        for kp_id in set(kw_norm) | set(vec_norm):
            fused[kp_id] = alpha * kw_norm.get(kp_id, 0.0) + (1 - alpha) * vec_norm.get(kp_id, 0.0)

        # 规则重排（§4.2）：名称精确包含查询的加小分（平手时优先名称直配）
        q_terms = segment(query)
        for kp_id, meta in self._kp_meta.items():
            if kp_id in fused and any(t and t in meta["name"] for t in q_terms):
                fused[kp_id] += 0.05

        ranked = sorted(fused.items(), key=lambda x: -x[1])[: max(1, int(top_k))]
        results = []
        for kp_id, score in ranked:
            row = dict(self._kp_meta[kp_id])
            row["score"] = round(score, 4)
            row["via"] = ("both" if kp_id in kw_norm and kp_id in vec_norm
                          else "keyword" if kp_id in kw_norm else "vector")
            results.append(require_ref(row, kind="kp"))  # §4.4 强制
        return results

    def close(self) -> None:
        self._conn.close()
