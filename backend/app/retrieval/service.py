"""检索服务编排（Phase 2 / S3）：三阶段检索的对外门面（§4.2）。

    search_knowledge_points  阶段 1：知识点双路召回（kp_recall.KpRetriever）
    search_questions         阶段 1→2→3 全链：KP 召回 → 题目扩展 → 重排

进程内单例（``get_retrieval_service``）：KP 元数据 + 向量矩阵 + FTS 连接
复用；embedding 模型单例（embedding.get_embedding_service）。FastAPI
lifespan 预热，避免首个请求承担模型加载（§4.5 冷启动指标）。

引用注入（§4.4）：本层所有返回行带 ``ref``（kp_recall/question_search 内
require_ref 强制）；调用方（工具/工作流）负责把 ref 装配进任务账本。
"""
from __future__ import annotations

import threading

from backend.app.retrieval.kp_recall import KP_RECALL_TOP_K, KpRetriever
from backend.app.retrieval.question_search import (
    expand_by_kp,
    rerank,
    supplement_by_fts,
    validate_filters,
)
from backend.app.storage import db


class RetrievalService:
    """三阶段检索门面（构造即完成启动载入：KP + 向量矩阵 + kp_fts 自愈同步）。

    线程模型：单例连接跨线程复用（FastAPI sync 端点在线程池执行），三阶段
    在 ``self._lock`` 内串行执行（p95 28ms，串行无吞吐影响）。
    """

    def __init__(self, db_path=None, kp_retriever: KpRetriever | None = None):
        self._conn = db.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._kp = kp_retriever or KpRetriever(db_path)

    # ---------- 阶段 1 ----------

    def search_knowledge_points(self, query: str, stage: str | None = None,
                                top_k: int = KP_RECALL_TOP_K) -> list[dict]:
        """知识点双路召回（带 ref/score/召回路来源）。"""
        return self._kp.recall(query, stage=stage, top_k=top_k)

    # ---------- 阶段 1→2→3 全链 ----------

    def search_questions(self, query: str, stage: str | None = None,
                         filters: dict | None = None,
                         kp_top_k: int = KP_RECALL_TOP_K,
                         question_limit: int = 20,
                         target_difficulty: int | None = None) -> dict:
        """完整三阶段：返回召回知识点、题目候选（已重排）与过程信息。

        题面 FTS 补充路仅在 query 非空时启用（题面检索意图）。
        """
        validate_filters(filters)
        with self._lock:
            kps = self._kp.recall(query or "", stage=stage, top_k=kp_top_k)
            kp_ids = [k["kp_id"] for k in kps]

            candidates = expand_by_kp(self._conn, kp_ids, filters, limit=question_limit * 3)
            fts_used = False
            if query:
                fts_candidates = supplement_by_fts(self._conn, query, filters,
                                                   limit=question_limit * 2)
                fts_used = bool(fts_candidates)
                seen = {c["q_id"] for c in candidates}
                candidates.extend(c for c in fts_candidates if c["q_id"] not in seen)

        ranked = rerank(candidates, kp_ids, target_difficulty=target_difficulty,
                        with_fts_intent=fts_used)[:question_limit]
        return {
            "kps": kps,
            "questions": ranked,
            "meta": {"kp_recall_count": len(kps), "candidate_count": len(candidates),
                     "fts_supplement_used": fts_used},
        }

    def search_by_kp(self, kp_ids: list[str], filters: dict | None = None,
                     question_limit: int = 20,
                     target_difficulty: int | None = None) -> list[dict]:
        """按指定 KP 直接召回+重排题目（阶段 2/3，无查询文本/无 FTS）。

        供诊断推荐复用：绕过阶段 1 的 query 召回，按已知根因/目标 KP 扩展候选
        并按阶段 3 重排。返回题目 dict（q_id/ref/stage/type/stem/answer/
        difficulty/score/kp_ids）。
        """
        validate_filters(filters)
        if not kp_ids:
            return []
        with self._lock:
            candidates = expand_by_kp(self._conn, kp_ids, filters,
                                      limit=question_limit * 3)
        return rerank(candidates, kp_ids, target_difficulty=target_difficulty)[:question_limit]

    def close(self) -> None:
        self._kp.close()
        self._conn.close()


_singleton: RetrievalService | None = None
_lock = threading.Lock()


def get_retrieval_service() -> RetrievalService:
    """进程内单例（lifespan 预热调用以摊平冷启动）。"""
    global _singleton
    with _lock:
        if _singleton is None:
            _singleton = RetrievalService()
        return _singleton


def reset_retrieval_service() -> None:
    """关闭并清空单例（测试隔离用；生产不调用）。"""
    global _singleton
    with _lock:
        if _singleton is not None:
            _singleton.close()
            _singleton = None
