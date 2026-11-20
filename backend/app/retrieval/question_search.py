"""阶段 2+3：题目候选扩展与重排（技术方案 §4.2，Phase 2 / S3）。

**阶段 2 题目候选扩展**（对象=题目，结构化为主）：
- 以阶段 1 知识点集合经 ``q_kp`` JOIN 扩展题目候选；
- 结构化过滤：学段 / 难度 / 题型 / 状态（processed，决策⑲）——**全部参数化 SQL**（§4.1 安全红线：禁止字符串拼接，参数白名单校验）；
- 题面 FTS5 补充候选（按题面关键词找题），补充候选同样落回知识点关联校验。

**阶段 3 题目重排**（对象=题目，独立评分；各特征**各自归一化后加权**，
不与阶段 1 知识点分数直接相加——§4.2 纪律）：
- 知识点匹配度：候选题挂接 KP 与阶段 1 集合的重合度（Jaccard）；
- 结构化贴合度：难度偏移惩罚（无目标难度取中性 0.5）；
- 题面相关性：FTS bm25 rank 归一化（**仅在有题面检索意图时计入**）。

权重起步：W_KP=0.6 / W_DIFF=0.25 / W_FTS=0.15（整档调整纪律同 α）。
不引入交叉编码器（2C2G 算力不允许，取舍见 §4.2——设计案例）。
"""
from __future__ import annotations

from backend.app.retrieval.citations import require_ref
from backend.app.retrieval.tokenize import fts_query

# 阶段 3 权重起步值（§4.2；评测期整档调整并记录）
W_KP = 0.6
W_DIFF = 0.25
W_FTS = 0.15


class FilterValidationError(ValueError):
    """结构化过滤参数非法（白名单校验失败，§4.1 参数白名单）。"""


VALID_FILTER_KEYS = {"stage", "difficulty", "difficulty_min", "difficulty_max",
                     "question_type", "limit"}
VALID_STAGES = {"primary", "junior"}
VALID_QUESTION_TYPES = {"application", "choice", "fill", "short", "proof"}


def validate_filters(filters: dict | None) -> dict:
    """结构化过滤白名单校验（§4.1：参数白名单 + 全部参数化绑定）。

    键与值双白名单；未知键直接拒绝（而不是忽略——防注入面扩张）。
    """
    filters = dict(filters or {})
    unknown = set(filters) - VALID_FILTER_KEYS
    if unknown:
        raise FilterValidationError(f"非法过滤键：{sorted(unknown)}（白名单：{sorted(VALID_FILTER_KEYS)}）")
    if "stage" in filters and filters["stage"] not in VALID_STAGES:
        raise FilterValidationError(f"非法 stage：{filters['stage']}")
    if "question_type" in filters and filters["question_type"] not in VALID_QUESTION_TYPES:
        raise FilterValidationError(f"非法 question_type：{filters['question_type']}")
    for key in ("difficulty", "difficulty_min", "difficulty_max", "limit"):
        if key in filters and not isinstance(filters[key], int):
            raise FilterValidationError(f"{key} 必须为整数")
    dmin = filters.get("difficulty_min", filters.get("difficulty"))
    dmax = filters.get("difficulty_max", filters.get("difficulty"))
    if dmin is not None and not 1 <= dmin <= 5:
        raise FilterValidationError("难度必须在 1–5")
    if dmax is not None and not 1 <= dmax <= 5:
        raise FilterValidationError("难度必须在 1–5")
    return filters


def _filter_sql(filters: dict) -> tuple[str, list]:
    """过滤子句组装（占位符拼接，值全部走参数绑定——§4.1 安全）。
    注意：白名单键 → 固定子句模板的选择是代码分支，不是字符串拼接用户输入。"""
    clauses, params = ["q.quality_state = 'processed'"], []  # 决策⑲：检索只取 processed
    if "stage" in filters:
        clauses.append("q.stage = ?")
        params.append(filters["stage"])
    dmin = filters.get("difficulty_min", filters.get("difficulty"))
    dmax = filters.get("difficulty_max", filters.get("difficulty"))
    if dmin is not None:
        clauses.append("q.difficulty >= ?")
        params.append(dmin)
    if dmax is not None:
        clauses.append("q.difficulty <= ?")
        params.append(dmax)
    if "question_type" in filters:
        clauses.append("q.type = ?")
        params.append(filters["question_type"])
    return " AND ".join(clauses), params


def expand_by_kp(conn, kp_ids: list[str], filters: dict | None = None,
                 limit: int = 50) -> list[dict]:
    """阶段 2 主路：阶段 1 知识点集合 → q_kp JOIN 扩展题目候选（参数化 SQL）。

    实现为**两步法**（性能实测驱动，2026-11-14）：
    ① ``idx_q_kp_kp`` 覆盖索引取候选 q_id（LIMIT 预取，无回表，ms 级）；
    ② 按候选 q_id 走 q_kp 主键前导（q_id, kp_id）点查题目字段与挂接集合。
    单条大 JOIN + ``GROUP_CONCAT(DISTINCT)`` 聚合对万级匹配行需临时 B-tree
    （实测 ~1.5s），两步法把聚合移到应用侧、扫描面压到索引链上（~10ms）。
    """
    filters = validate_filters(filters)
    if not kp_ids:
        return []
    limit = int(filters.get("limit", limit))
    where, params = _filter_sql(filters)
    ph = ",".join("?" * len(kp_ids))

    # ① 覆盖索引扫候选 q_id（预取量放大，过滤后仍够 limit）
    cand_rows = conn.execute(
        f"SELECT q_id FROM q_kp WHERE kp_id IN ({ph}) LIMIT ?",
        [*kp_ids, limit * 8],
    ).fetchall()
    seen_ids: list[str] = []
    seen_set: set[str] = set()
    for r in cand_rows:
        if r["q_id"] not in seen_set:
            seen_set.add(r["q_id"])
            seen_ids.append(r["q_id"])
    if not seen_ids:
        return []

    # ② 题目字段点查（q_id 主键）+ 过滤
    ph_q = ",".join("?" * len(seen_ids))
    rows = conn.execute(
        f"""SELECT q.q_id, q.stage, q.type, q.stem, q.answer, q.difficulty, q.score
            FROM questions q
            WHERE q.q_id IN ({ph_q}) AND {where}""",
        [*seen_ids, *params],
    ).fetchall()
    if not rows:
        return []

    # ③ 挂接集合（q_kp 主键前导 q_id，点查）
    kept_ids = [r["q_id"] for r in rows]
    ph_k = ",".join("?" * len(kept_ids))
    link_map: dict[str, list[str]] = {}
    for r in conn.execute(
        f"SELECT q_id, kp_id FROM q_kp WHERE q_id IN ({ph_k})", kept_ids
    ):
        link_map.setdefault(r["q_id"], []).append(r["kp_id"])

    recall_set = set(kp_ids)
    candidates = []
    for r in rows:
        kp_list = link_map.get(r["q_id"], [])
        if not kp_list:
            continue  # 知识点关联校验（§4.2：候选必须挂接知识点）
        c = {
            "q_id": r["q_id"],
            "ref": r["q_id"],  # §4.4：题库取数必带 ref
            "stage": r["stage"],
            "type": r["type"],
            "stem": r["stem"],
            "answer": r["answer"],
            "difficulty": r["difficulty"],
            "score": r["score"],
            "kp_ids": kp_list,
        }
        c["_in_recall"] = len(set(kp_list) & recall_set)
        candidates.append(require_ref(c, kind="question"))

    # 粗排：重合多优先、难度低次之（重排阶段再做精细评分）
    candidates.sort(key=lambda c: (-c["_in_recall"], c["difficulty"]))
    for c in candidates:
        c.pop("_in_recall", None)
    return candidates[:limit]


def supplement_by_fts(conn, query: str, filters: dict | None = None,
                      limit: int = 30) -> list[dict]:
    """阶段 2 补充路：题面 FTS 找题（必须带查询意图才调用）。

    补充候选同样落回知识点关联校验：只返回挂接了知识点的题（q_kp JOIN，
    §4.2"补充候选同样须落回知识点关联校验"）。

    SQL 形态说明：FTS5 辅助函数 bm25 的参数必须是 FTS5 **表名**（别名不可），
    且 MATCH+bm25 须在无 JOIN 的子查询内执行（JOIN 上下文会报 "unable to use
    function bm25"）——故先 FTS 层过滤排序（LIMIT 预取），再关联业务表过滤。
    """
    filters = validate_filters(filters)
    q = fts_query(query)
    if q == '""':
        return []
    limit = int(filters.get("limit", limit))
    where, params = _filter_sql(filters)
    sql = f"""
        SELECT q.q_id, q.stage, q.type, q.stem, q.answer, q.difficulty, q.score,
               GROUP_CONCAT(DISTINCT k.kp_id) AS kp_ids,
               sub.fts_rank
        FROM (
            SELECT q_id AS sub_qid, bm25(questions_fts) AS fts_rank
            FROM questions_fts
            WHERE questions_fts MATCH ?
            ORDER BY fts_rank LIMIT ?
        ) sub
        JOIN questions q ON q.q_id = sub.sub_qid
        JOIN q_kp k ON k.q_id = q.q_id
        WHERE {where}
        GROUP BY q.q_id
        ORDER BY sub.fts_rank
        LIMIT ?
    """
    rows = conn.execute(sql, [q, limit * 2, *params, limit]).fetchall()
    return [_row_to_candidate(r, fts_rank=r["fts_rank"]) for r in rows]


def _row_to_candidate(r, fts_rank: float | None = None) -> dict:
    kp_ids = [k for k in (r["kp_ids"] or "").split(",") if k]
    row = {
        "q_id": r["q_id"],
        "ref": r["q_id"],  # §4.4：题库取数必带 ref
        "stage": r["stage"],
        "type": r["type"],
        "stem": r["stem"],
        "answer": r["answer"],
        "difficulty": r["difficulty"],
        "score": r["score"],
        "kp_ids": kp_ids,
    }
    if fts_rank is not None:
        row["fts_rank"] = float(fts_rank)
    return require_ref(row, kind="question")


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [1.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def rerank(candidates: list[dict], recall_kp_ids: list[str],
           target_difficulty: int | None = None,
           with_fts_intent: bool = False) -> list[dict]:
    """阶段 3 题目重排：三特征各自归一化后加权（独立于阶段 1 分数）。

    with_fts_intent=False 时题面相关性不计入（§4.2：仅在有题面检索意图时计入），
    剩余权重按比例重分配（KP 0.6/难度 0.25 → 0.706/0.294）。
    """
    if not candidates:
        return []
    recall_set = set(recall_kp_ids)

    kp_feat = []
    diff_feat = []
    for c in candidates:
        c_kps = set(c["kp_ids"]) & recall_set
        union = set(c["kp_ids"]) | recall_set
        kp_feat.append(len(c_kps) / len(union) if union else 0.0)
        if target_difficulty is None:
            diff_feat.append(0.5)  # 无目标难度 → 中性
        else:
            diff_feat.append(1.0 - abs(c["difficulty"] - target_difficulty) / 4.0)

    kp_norm = _minmax(kp_feat)
    diff_norm = _minmax(diff_feat)
    fts_norm = _minmax([-c["fts_rank"] for c in candidates if "fts_rank" in c]) \
        if with_fts_intent else None

    if with_fts_intent and fts_norm is not None:
        w_sum = W_KP + W_DIFF + W_FTS
        w_kp, w_diff, w_fts = W_KP / w_sum, W_DIFF / w_sum, W_FTS / w_sum
    else:
        w_sum = W_KP + W_DIFF
        w_kp, w_diff, w_fts = W_KP / w_sum, W_DIFF / w_sum, 0.0

    fts_iter = iter(fts_norm) if fts_norm is not None else None
    scored = []
    for c, kp_s, diff_s in zip(candidates, kp_norm, diff_norm):
        fts_s = next(fts_iter) if (fts_iter and "fts_rank" in c) else 0.0
        total = w_kp * kp_s + w_diff * diff_s + w_fts * fts_s
        row = dict(c)
        row["rerank_score"] = round(total, 4)
        scored.append(row)
    scored.sort(key=lambda x: -x["rerank_score"])
    return scored
