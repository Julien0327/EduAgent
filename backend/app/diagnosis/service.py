"""统一诊断服务层（Phase 3 / S3）。

API、class_diagnosis 工作流、前端复用同一套计算逻辑，避免多套实现：
    build_diagnosis()  → 确定性诊断（数据 + 掌握度 + 归因 + 推荐，无 LLM）
    narrate（S3）      → LLM 只解释，模板兜底
"""
from __future__ import annotations

from backend.app.diagnosis import data_access, recommend
from backend.app.diagnosis.attribution import attribute, build_prereq_graph
from backend.app.diagnosis.mastery import compute_mastery

LAM = 0.5


def _load_graph(conn) -> tuple[dict, dict, set[str], list[dict]]:
    names = {r["kp_id"]: r["name"] for r in conn.execute(
        "SELECT kp_id, name FROM knowledge_points WHERE stage='primary'")}
    all_kp_ids = set(names)
    edges = [{"from_kp": r["from_kp"], "to_kp": r["to_kp"]}
             for r in conn.execute("SELECT from_kp, to_kp FROM kp_edges")]
    prereqs = build_prereq_graph(edges)
    return names, prereqs, all_kp_ids, edges


def build_diagnosis(conn, class_id: str, retriever=None, lam: float = LAM) -> dict:
    """确定性诊断（不含叙述）。retriever=None 用检索单例；测试注入 Fake。"""
    loaded = data_access.load_class_evidence(conn, class_id)
    names, prereqs, all_kp_ids, edges = _load_graph(conn)
    mastery = compute_mastery(loaded["evidence"], loaded["exams"], lam,
                              all_kp_ids=all_kp_ids)
    chains = attribute(mastery, prereqs, names=names, evidence=loaded["evidence"])

    completed_q_ids = {q["q_id"] for q in loaded["exam_questions"] if q["q_id"]}

    if retriever is None:
        from backend.app.retrieval.service import get_retrieval_service
        retriever = get_retrieval_service()

    def source_lookup(q_ids):
        if not q_ids:
            return {}
        ph = ",".join("?" * len(q_ids))
        return {r["q_id"]: r["source"] for r in conn.execute(
            f"SELECT q_id, source FROM questions WHERE q_id IN ({ph})", list(q_ids))}

    recommendations = recommend.recommend(
        chains, mastery, completed_q_ids, retriever, source_lookup, names)

    return {
        "class": loaded["class"],
        "data_scope": loaded["data_scope"],
        "kp_names": names,
        "mastery": mastery,
        "chains": chains,
        "recommendations": recommendations,
        "graph_edges_count": len(edges),
        "lam": lam,
    }
