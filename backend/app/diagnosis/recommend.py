"""真实题库推荐（Phase 3 / S3，§5.3）。

只从现有小学题库召回候选，返回真实题目（q_id/stem/difficulty/kp_ids/source）。
**禁止 LLM 编题混入推荐结果。**

策略（用户补充要求）：
- 区分两类练习：根因巩固（root，每个根因候选各一份）+ 目标针对性（target）；
- 低置信度 / 证据阻断如实反映在结果字段与推荐理由中；
- 默认排除当前班级已完成的题；候选不足时按序放宽（难度带 → 已完成题），并记录
  放宽原因与实际采用策略；
- 难度带（D4）：band = clamp(round_half_up(5×掌握度), 1, 5)，取 [band-1, band]。
"""
from __future__ import annotations

from backend.app.diagnosis.rounding import round_half_up

MIN_PER_KP = 3
SOURCE_FALLBACK = "unknown"


def difficulty_band(mastery: float | None) -> int:
    """D4：难度带中心。mastery None → 1。"""
    if mastery is None:
        return 1
    m = max(0.0, min(1.0, float(mastery)))
    return max(1, min(5, int(round_half_up(5 * m, 0))))


def _source_lookup_db(conn, q_ids: set[str]) -> dict[str, str]:
    if not q_ids:
        return {}
    ph = ",".join("?" * len(q_ids))
    return {r["q_id"]: r["source"] for r in conn.execute(
        f"SELECT q_id, source FROM questions WHERE q_id IN ({ph})", list(q_ids))}


def _filter_questions(ranked: list[dict], completed: set[str]) -> list[dict]:
    out = []
    for q in ranked:
        q = dict(q)
        q["completed"] = q["q_id"] in completed
        out.append(q)
    return out


def _recommend_one(kp_id: str, role: str, mastery: float | None, chain: dict,
                   completed_q_ids: set[str], retriever, source_lookup,
                   kp_names: dict, seen: set[str]) -> dict:
    kp_name = kp_names.get(kp_id, kp_id)
    band = difficulty_band(mastery)
    lo, hi = max(1, band - 1), band

    relaxation = None
    levels: list[str] = []

    # 严格：难度带 + 排除已完成
    strict = retriever.search_by_kp(
        [kp_id], filters={"difficulty_min": lo, "difficulty_max": hi},
        question_limit=MIN_PER_KP * 3)
    strict = [q for q in strict if q["q_id"] not in completed_q_ids]

    chosen = strict
    if len(chosen) < MIN_PER_KP:
        # 放宽 1：难度全范围（仍排除已完成）
        wide = retriever.search_by_kp(
            [kp_id], filters={"difficulty_min": 1, "difficulty_max": 5},
            question_limit=MIN_PER_KP * 3)
        wide = [q for q in wide if q["q_id"] not in completed_q_ids]
        levels.append("difficulty_widened")
        chosen = wide
        if len(chosen) < MIN_PER_KP:
            # 放宽 2：纳入已完成（保留 completed 标记）
            incl_completed = retriever.search_by_kp(
                [kp_id], filters={"difficulty_min": 1, "difficulty_max": 5},
                question_limit=MIN_PER_KP * 3)
            levels.append("completed_included")
            chosen = incl_completed

    # 去重（全局 seen）+ 取 MIN_PER_KP
    final = []
    for q in chosen:
        if q["q_id"] in seen:
            continue
        q = dict(q)
        q["completed"] = q["q_id"] in completed_q_ids
        final.append(q)
        seen.add(q["q_id"])
        if len(final) >= MIN_PER_KP:
            break

    src = source_lookup({q["q_id"] for q in final})
    reason = (
        f"{'根因巩固练习' if role == 'root' else '目标薄弱点针对性练习'}：{kp_name}"
        f"（掌握度 {mastery:.2f}，难度带 {lo}-{hi}）"
    )
    if chain.get("confidence") == "low":
        reason += "（低置信度，建议结合课堂观察）"
    if chain.get("blocked_prereqs"):
        reason += "（存在前置证据不足，建议补充测评）"
    if levels:
        relaxation = {
            "levels": levels,
            "final_strategy": "+".join(levels) or "strict",
            "reason": f"该知识点候选不足（严格口径 {len(strict)} 条 < {MIN_PER_KP}），已放宽",
            "candidate_count": len(final),
        }

    questions = []
    for q in final:
        questions.append({
            "q_id": q["q_id"],
            "stem": q["stem"],
            "difficulty": q["difficulty"],
            "kp_ids": q.get("kp_ids", []),
            "source": src.get(q["q_id"], SOURCE_FALLBACK),
            "recommendation_reason": reason,
            "completed": q["completed"],
        })

    return {
        "role": role,
        "kp_id": kp_id,
        "kp_name": kp_name,
        "chain_target_kp": chain["target_kp"],
        "chain_target_name": chain.get("target_name", chain["target_kp"]),
        "confidence": chain.get("confidence"),
        "conflict": chain.get("conflict", False),
        "blocked_prereqs": chain.get("blocked_prereqs", []),
        "mastery": mastery,
        "band": band,
        "difficulty_range": [lo, hi],
        "relaxation": relaxation,
        "candidate_count": len(final),
        "questions": questions,
    }


def _representative_chain(chains: list[dict], kp_id: str, as_root: bool) -> dict | None:
    """返回包含该 KP（作为根因或目标）的一条代表链，用于置信度/阻断/冲突上下文。"""
    cands = []
    for c in chains:
        roots = {rc["kp_id"] for rc in c.get("root_candidates", [])}
        if as_root and kp_id in roots:
            cands.append(c)
        if not as_root and c["target_kp"] == kp_id:
            cands.append(c)
    if not cands:
        return None
    return min(cands, key=lambda c: (c.get("target_mastery") or 1.0, c["target_kp"]))


def recommend(chains: list[dict], mastery: dict, completed_q_ids: set[str],
              retriever, source_lookup, kp_names: dict | None = None) -> list[dict]:
    """先根因（去重、巩固），再目标（去重、针对性）；每个知识点一组，避免重复空组。"""
    kp_names = kp_names or {}
    root_kps: list[str] = []
    for c in chains:
        for rc in c.get("root_candidates", []):
            if rc["kp_id"] not in root_kps:
                root_kps.append(rc["kp_id"])
    target_kps: list[str] = []
    for c in chains:
        if c["target_kp"] not in target_kps:
            target_kps.append(c["target_kp"])

    out: list[dict] = []
    seen_kp: set[str] = set()
    seen_q: set[str] = set()

    for kp in root_kps:
        rep = _representative_chain(chains, kp, as_root=True)
        if rep is None:
            continue
        out.append(_recommend_one(
            kp, "root", mastery[kp]["mastery"], rep, completed_q_ids,
            retriever, source_lookup, kp_names, seen_q))
        seen_kp.add(kp)

    for kp in target_kps:
        if kp in seen_kp:
            continue
        rep = _representative_chain(chains, kp, as_root=False)
        if rep is None:
            continue
        out.append(_recommend_one(
            kp, "target", mastery[kp]["mastery"], rep, completed_q_ids,
            retriever, source_lookup, kp_names, seen_q))
        seen_kp.add(kp)

    return out
