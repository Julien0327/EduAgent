"""查重规则（S1，§8.1）：批内查重 + 历史题库查重。

- **批内查重**：本批生成题两两比对（防止同卷重复出题）。
- **历史查重**：``questions_fts``（jieba 分词）召回 top-N 候选 + ``difflib.SequenceMatcher``
  编辑距离相似度阈值告警；**不整库载入内存**（FTS 预取候选，仅对候选算相似度）。

相似度口径：先做文本归一化（去格式标点/空白/小写，保留 CJK/数字/数学运算符），
再算 ``SequenceMatcher.ratio``。阈值两级：>=HARD 判 fail（近乎雷同），
[WARN, HARD) 判 warn（告警）。归一化后过短（<4 字符）不计相似（防平凡串误判）。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from backend.app.critique.models import (
    RuleIssue, DIM_DUPLICATE, SEV_FAIL, SEV_WARN,
)
from backend.app.retrieval.tokenize import fts_query

# 阈值（可调，测试锁定）
DUP_BATCH_HARD = 0.95
DUP_BATCH_WARN = 0.85
DUP_HISTORY_HARD = 0.95
DUP_HISTORY_WARN = 0.85
DUP_HISTORY_TOPK = 10

_MIN_NORMALIZED_LEN = 4

# 格式标点（相似度比对前去除；保留 CJK/数字/字母/数学运算符 + - * / = × ÷ % ^）
_FMT_PUNCT = re.compile(
    r'[，。、；：！？"' "'" r'“”‘’（）()【】《》〈〉…—·,.;:!?\[\]\{\}<>]'
)


def normalize_stem(text: str) -> str:
    """文本归一化：去格式标点 + 去空白 + 小写（供相似度比对）。"""
    t = _FMT_PUNCT.sub("", str(text or ""))
    t = re.sub(r"\s+", "", t)
    return t.lower()


def similarity(a: str, b: str) -> float:
    """两段文本的相似度（0~1）；任一方归一化后过短返回 0。"""
    na, nb = normalize_stem(a), normalize_stem(b)
    if len(na) < _MIN_NORMALIZED_LEN or len(nb) < _MIN_NORMALIZED_LEN:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def batch_duplicates(questions: list[dict]) -> tuple[list[dict], dict[int, list[RuleIssue]]]:
    """批内两两查重。返回 (duplicate_pairs, {index: [RuleIssue]})。"""
    pairs: list[dict] = []
    issues: dict[int, list[RuleIssue]] = {}
    n = len(questions)
    for i in range(n):
        for j in range(i + 1, n):
            r = similarity(questions[i].get("stem", ""), questions[j].get("stem", ""))
            if r < DUP_BATCH_WARN:
                continue
            sev = SEV_FAIL if r >= DUP_BATCH_HARD else SEV_WARN
            pair = {
                "index_a": i, "index_b": j, "ratio": round(r, 4),
                "ids": [questions[i].get("question_id"), questions[j].get("question_id")],
            }
            pairs.append(pair)
            for idx, other in ((i, j), (j, i)):
                issues.setdefault(idx, []).append(RuleIssue(
                    DIM_DUPLICATE, sev, "duplicate_batch",
                    f"与第 {other + 1} 题高度相似（相似度 {r:.2f}）", {"pair": pair}))
    return pairs, issues


def find_similar_questions(conn, stem: str, top_k: int = DUP_HISTORY_TOPK) -> list[dict]:
    """历史题库查重：FTS 召回候选 + 相似度过滤，返回 >=WARN 的命中（按相似度降序）。

    沿用检索层 FTS 查询形态（``fts_query``，jieba OR 关键词）；FTS5 bm25 须在
    无 JOIN 的子查询内（同 ``question_search.supplement_by_fts`` 的约束）。
    """
    q = fts_query(stem)
    if q == '""':
        return []
    rows = conn.execute(
        """
        SELECT q.q_id, q.stem, q.answer, q.difficulty, sub.fts_rank
        FROM (
            SELECT q_id AS sub_qid, bm25(questions_fts) AS fts_rank
            FROM questions_fts WHERE questions_fts MATCH ?
            ORDER BY fts_rank LIMIT ?
        ) sub
        JOIN questions q ON q.q_id = sub.sub_qid
        WHERE q.quality_state = 'processed'
        ORDER BY sub.fts_rank
        """,
        (q, top_k),
    ).fetchall()

    out = []
    for r in rows:
        ratio = similarity(stem, r["stem"])
        if ratio >= DUP_HISTORY_WARN:
            out.append({
                "q_id": r["q_id"], "stem": r["stem"], "ratio": round(ratio, 4),
                "difficulty": r["difficulty"],
            })
    out.sort(key=lambda x: x["ratio"], reverse=True)
    return out


def history_duplicates(conn, questions: list[dict]) -> tuple[list[dict], dict[int, list[RuleIssue]]]:
    """逐题历史查重（conn=None 跳过）。返回 (history_matches, {index: [RuleIssue]})。"""
    if conn is None:
        return [], {}
    matches: list[dict] = []
    issues: dict[int, list[RuleIssue]] = {}
    for i, q in enumerate(questions):
        sims = find_similar_questions(conn, q.get("stem", ""))
        if not sims:
            continue
        best = sims[0]
        sev = SEV_FAIL if best["ratio"] >= DUP_HISTORY_HARD else SEV_WARN
        for s in sims:
            matches.append({"index": i, **s})
        issues[i] = [RuleIssue(
            DIM_DUPLICATE, sev, "duplicate_history",
            f"与题库已有题 {best['q_id']} 相似（{best['ratio']:.2f}）",
            {"top_match": best, "match_count": len(sims)})]
    return matches, issues
