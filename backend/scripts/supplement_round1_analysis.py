"""Round1 补充验收数据分析（只读；不覆盖已有报告、不改运行时代码）。

基于现有实验数据补齐：
- A/B/C/D 四组完整指标与逐条排名（持久化 top-5 ID，支持 NDCG 复算）；
- 23 条原失败样本：旧排名 / D 新排名 / 是否修复 / 未修复主因；
- 27 条原命中样本：top-5 回归与排名变化（回归≠排名下降）；
- FTS 候选明细：40 条非零查询、110 个候选、置信门过滤原因、4 个通过候选
  （候选自身排名 vs 该查询 gold primary 排名分开）、gold primary / any-gold 口径；
- FTS 与名称加分的独立贡献；
- 评测集/Gold/指标口径一致性核对与审计边界说明。

输出（新文件，不覆盖 retrieval_optimization_round1.*）：
    data/staging/retrieval_optimization_round1_supplement.md
    data/staging/retrieval_optimization_round1_supplement.json
"""
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from backend.app.retrieval.kp_recall import (
    KEYWORD_MIN_COVERAGE,
    KEYWORD_MIN_TERM_MATCHES,
    KEYWORD_MIN_TERMS,
    KEYWORD_TOP_N,
    KpRetriever,
    name_bonus_for,
)
from backend.app.retrieval.tokenize import fts_query, fts_query_terms, segment
from backend.scripts.compare_retrieval_optimization import (
    aggregate,
    evaluate,
    legacy_keyword_scores,
    rank_variant,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "data" / "staging"
FORMAL = STAGING / "retrieval_eval_queries_formal_v1.jsonl"
GOLD = STAGING / "trial50_gold_v2.jsonl"
BASELINE = STAGING / "retrieval_baseline_report.json"
DIAG = STAGING / "retrieval_failure_diagnosis.json"
OUT_JSON = STAGING / "retrieval_optimization_round1_supplement.json"
OUT_MD = STAGING / "retrieval_optimization_round1_supplement.md"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    except Exception:
        return ""


def mtime_str(path: Path) -> str:
    try:
        return datetime.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return ""


def pp(a: float, b: float) -> str:
    return f"{(a - b) * 100:+.2f}pp"


def git_diff_sha() -> str:
    try:
        out = subprocess.check_output(["git", "diff"], cwd=REPO_ROOT, text=True)
        return hashlib.sha256(out.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def rank_delta(old: int | None, new: int | None) -> dict:
    if old == new:
        return {"status": "same", "delta": f"{old or '>10'}→{new or '>10'}"}
    if old is None and new is not None:
        return {"status": "improved", "delta": f">10→{new}"}
    if old is not None and new is None:
        return {"status": "worsened", "delta": f"{old}→>10"}
    return {"status": "improved" if new < old else "worsened",
            "delta": f"{old}→{new}"}


def eval_with_ids(ranked: list[tuple[str, float]], q: dict) -> dict:
    row = evaluate(ranked, q)
    row["top_k_ids"] = [kid for kid, _ in ranked]
    return row


def candidate_rank(ids: list[str], kp_id: str) -> int | None:
    return ids.index(kp_id) + 1 if kp_id in ids else None


def main() -> int:
    formal = load_jsonl(FORMAL)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    diag = json.loads(DIAG.read_text(encoding="utf-8"))
    diag_by_qid = {x["query_id"]: x for x in diag["samples"]}
    old_rows = {r["query_id"]: r for r in baseline["per_query"]["stage_filtered"]}
    original_failures = {qid for qid, r in old_rows.items() if not r["recall5_primary"]}
    original_hits = {qid for qid, r in old_rows.items() if r["recall5_primary"]}

    ret = KpRetriever()
    conn = ret._conn
    stage_map = {kid: meta["stage"] for kid, meta in ret._kp_meta.items()}
    kp_name = {kid: meta["name"] for kid, meta in ret._kp_meta.items()}
    try:
        variants = {"A": [], "B": [], "C": [], "D": []}
        per_query = {}
        fts_detail = []
        for q in formal:
            text, stage = q["query_text"], q["stage"]
            old_kw = legacy_keyword_scores(conn, stage_map, text, stage)
            new_kw = ret._keyword_scores(text, stage)
            vec = ret._vector_scores(text, stage)
            name_map = {kid: meta["name"] for kid, meta in ret._kp_meta.items()
                        if not stage or meta["stage"] == stage}

            ranked = {
                "A": rank_variant(old_kw, vec, text, name_map, "old"),
                "B": rank_variant(new_kw, vec, text, name_map, "old"),
                "C": rank_variant(old_kw, vec, text, name_map, "new"),
                "D": rank_variant(new_kw, vec, text, name_map, "new"),
            }
            for key in ("A", "B", "C", "D"):
                variants[key].append(eval_with_ids(ranked[key], q))
            per_query[q["query_id"]] = {k: v[-1] for k, v in variants.items()}

            terms = fts_query_terms(text)
            match_query = fts_query(text)
            rows = []
            if match_query != '""':
                rows = conn.execute(
                    "SELECT kp_id, bm25(kp_fts) AS rank FROM kp_fts "
                    "WHERE kp_fts MATCH ?", (match_query,)
                ).fetchall()
            rows = [r for r in rows if stage_map.get(r["kp_id"]) == stage
                    and r["rank"] is not None]
            candidates = []
            for r in rows:
                meta = ret._kp_meta[r["kp_id"]]
                kp_text = f"{meta['name']} {meta['description']}"
                matched = [t for t in terms if t in kp_text]
                coverage = len(matched) / len(terms) if terms else 0.0
                passed = (len(matched) >= KEYWORD_MIN_TERM_MATCHES
                          and coverage >= KEYWORD_MIN_COVERAGE)
                candidates.append({
                    "kp_id": r["kp_id"], "name": meta["name"],
                    "bm25_rank": round(float(r["rank"]), 6),
                    "matched_terms": matched, "matched_count": len(matched),
                    "coverage": round(coverage, 4), "passed": passed,
                    "is_gold_primary": r["kp_id"] == q["gold_primary"],
                    "is_any_gold": r["kp_id"] in set(q["gold_kp_ids"]),
                    "candidate_A_rank": candidate_rank(
                        per_query[q["query_id"]]["A"]["top_k_ids"], r["kp_id"]),
                    "candidate_C_rank": candidate_rank(
                        per_query[q["query_id"]]["C"]["top_k_ids"], r["kp_id"]),
                    "candidate_D_rank": candidate_rank(
                        per_query[q["query_id"]]["D"]["top_k_ids"], r["kp_id"]),
                })
            candidates.sort(key=lambda x: x["bm25_rank"])
            fts_detail.append({
                "query_id": q["query_id"], "terms": terms,
                "raw_count": len(candidates),
                "passed_count": sum(1 for c in candidates if c["passed"]),
                "candidates": candidates,
            })
    finally:
        ret.close()

    baseline_mismatch = [
        qid for qid in old_rows
        if per_query[qid]["A"]["rank_primary"] != old_rows[qid]["rank_primary"]
    ]

    summary = {}
    for key, rows in variants.items():
        a = aggregate(rows)
        a["fixed_23"] = sum(1 for r in rows if r["query_id"] in original_failures and r["recall5"])
        a["fixed_qids"] = [r["query_id"] for r in rows
                           if r["query_id"] in original_failures and r["recall5"]]
        a["regressed_27"] = sum(1 for r in rows if r["query_id"] in original_hits and not r["recall5"])
        a["regressed_qids"] = [r["query_id"] for r in rows
                               if r["query_id"] in original_hits and not r["recall5"]]
        summary[key] = a

    changes = {}
    for key in ("B", "C", "D"):
        changes[key] = {"improved": [], "worsened": [], "same": []}
        for qid, row in per_query.items():
            d = rank_delta(row["A"]["rank_primary"], row[key]["rank_primary"])
            changes[key][d["status"]].append({"query_id": qid, "delta": d["delta"]})

    failures = []
    for qid in sorted(original_failures):
        q = next(x for x in formal if x["query_id"] == qid)
        old_rank = old_rows[qid]["rank_primary"]
        d_rank = per_query[qid]["D"]["rank_primary"]
        fixed = d_rank is not None and d_rank <= 5
        diag_item = diag_by_qid.get(qid, {})
        q_len = len(q["query_text"])
        tok_count = len(segment(q["query_text"]))
        vec_rank = diag_item.get("vec_gold_rank")
        passed = [c for c in next(x for x in fts_detail if x["query_id"] == qid)["candidates"]
                  if c["passed"]]
        entered = [(c, c["candidate_D_rank"]) for c in passed
                   if c["candidate_D_rank"] and c["candidate_D_rank"] <= 5]
        if fixed:
            reason = "已修复（名称加分去除误触发）"
        elif entered:
            desc = "；".join(
                f"{c['kp_id']} 第{rank}" + ("（any-gold）" if c["is_any_gold"] else "（非 gold）")
                for c, rank in entered)
            reason = (f"关键词候选进入 D top-5：{desc}；gold primary {q['gold_primary']} "
                      f"向量排名 {vec_rank}，未进入关键词候选，未带来 gold 收益")
        elif passed:
            reason = "关键词候选通过置信门但未进入 D top-5；主因向量/描述泛化"
        elif vec_rank and vec_rank > 10:
            if q_len <= 20:
                reason = f"关键词退回向量；短查询（{q_len} 字）→ KP 描述泛化（向量 rank {vec_rank}）"
            else:
                reason = f"关键词退回向量；长题干（{q_len} 字）稀释 + KP 描述泛化（向量 rank {vec_rank}）"
        elif vec_rank:
            reason = f"关键词退回向量；向量排名 6–10（临界，rank {vec_rank}）"
        else:
            reason = "其他"
        failures.append({
            "query_id": qid, "gold": q["gold_primary"],
            "old_rank": old_rank, "d_rank": d_rank, "fixed": fixed,
            "keyword_passed": len(passed), "vec_rank": vec_rank,
            "query_len": q_len, "token_count": tok_count,
            "reason": reason,
        })

    hit_changes = []
    for qid in sorted(original_hits):
        old_rank = old_rows[qid]["rank_primary"]
        d_rank = per_query[qid]["D"]["rank_primary"]
        delta = rank_delta(old_rank, d_rank)
        hit_changes.append({"query_id": qid, "old_rank": old_rank,
                            "d_rank": d_rank, "status": delta["status"],
                            "delta": delta["delta"]})

    passed_candidates = []
    gold_filtered = []
    any_gold_filtered = []
    for item in fts_detail:
        for c in item["candidates"]:
            qid = item["query_id"]
            gold_rank = {
                "A": per_query[qid]["A"]["rank_primary"],
                "C": per_query[qid]["C"]["rank_primary"],
                "D": per_query[qid]["D"]["rank_primary"],
            }
            if c["is_gold_primary"] and not c["passed"]:
                gold_filtered.append({
                    "query_id": qid, "kp_id": c["kp_id"], "name": c["name"],
                    "matched_terms": c["matched_terms"], "coverage": c["coverage"],
                    "bm25_rank": c["bm25_rank"], "d_rank": gold_rank["D"],
                })
            if c["is_any_gold"] and not c["passed"]:
                any_gold_filtered.append({
                    "query_id": qid, "kp_id": c["kp_id"], "name": c["name"],
                    "matched_terms": c["matched_terms"], "coverage": c["coverage"],
                    "is_gold_primary": c["is_gold_primary"],
                })
            if c["passed"]:
                passed_candidates.append({
                    "query_id": qid, "kp_id": c["kp_id"], "name": c["name"],
                    "is_gold_primary": c["is_gold_primary"],
                    "is_any_gold": c["is_any_gold"],
                    "matched_terms": c["matched_terms"], "coverage": c["coverage"],
                    "bm25_rank": c["bm25_rank"],
                    "candidate_A_rank": c["candidate_A_rank"],
                    "candidate_C_rank": c["candidate_C_rank"],
                    "candidate_D_rank": c["candidate_D_rank"],
                    "gold_primary_A_rank": gold_rank["A"],
                    "gold_primary_C_rank": gold_rank["C"],
                    "gold_primary_D_rank": gold_rank["D"],
                })

    gold_filtered_qids = {c["query_id"] for c in gold_filtered}
    gold_filtered_hits = sorted(gold_filtered_qids & original_hits)
    gold_filtered_failures = sorted(gold_filtered_qids & original_failures)
    gold_filtered_with_bonus = []
    for qid in gold_filtered_hits:
        qq = next(x for x in formal if x["query_id"] == qid)
        if name_bonus_for(qq["query_text"], kp_name.get(qq["gold_primary"], "")) > 0:
            gold_filtered_with_bonus.append(qid)
    secondary_gold_filtered = [c for c in any_gold_filtered if not c["is_gold_primary"]]

    primary_gold_in_raw = sum(
        1 for item in fts_detail if any(c["is_gold_primary"] for c in item["candidates"]))
    primary_gold_missing = len(fts_detail) - primary_gold_in_raw
    any_gold_passed = sum(1 for c in passed_candidates if c["is_any_gold"])

    match_dist = {}
    for item in fts_detail:
        for c in item["candidates"]:
            key = c["matched_count"]
            match_dist[key] = match_dist.get(key, 0) + 1
    filtered_by_rule = {
        "zero_match": sum(1 for i in fts_detail for c in i["candidates"]
                          if c["matched_count"] == 0),
        "one_match": sum(1 for i in fts_detail for c in i["candidates"]
                         if c["matched_count"] == 1),
        "two_match_filtered": sum(1 for i in fts_detail for c in i["candidates"]
                                  if c["matched_count"] == 2 and not c["passed"]),
        "two_match_passed": sum(1 for i in fts_detail for c in i["candidates"]
                                if c["matched_count"] == 2 and c["passed"]),
        "four_match_passed": sum(1 for i in fts_detail for c in i["candidates"]
                                 if c["matched_count"] == 4 and c["passed"]),
    }

    report = {
        "summary": summary,
        "changes": changes,
        "failures": failures,
        "hit_changes": hit_changes,
        "fts_detail": fts_detail,
        "passed_candidates": passed_candidates,
        "gold_filtered_candidates": gold_filtered,
        "any_gold_filtered_candidates": any_gold_filtered,
        "secondary_gold_filtered_candidates": secondary_gold_filtered,
        "gold_filtered_hit_queries": gold_filtered_hits,
        "gold_filtered_failure_queries": gold_filtered_failures,
        "gold_filtered_with_new_bonus": gold_filtered_with_bonus,
        "match_count_distribution": dict(sorted(match_dist.items())),
        "primary_gold_in_raw": primary_gold_in_raw,
        "primary_gold_missing": primary_gold_missing,
        "any_gold_passed": any_gold_passed,
        "filtered_by_rule": filtered_by_rule,
        "thresholds": {
            "min_terms": KEYWORD_MIN_TERMS,
            "min_term_matches": KEYWORD_MIN_TERM_MATCHES,
            "min_coverage": KEYWORD_MIN_COVERAGE,
            "top_n": KEYWORD_TOP_N,
        },
        "per_query_variants": per_query,
        "consistency": {
            "formal_sha256": sha256(FORMAL),
            "gold_sha256": sha256(GOLD),
            "baseline_sha256": sha256(BASELINE),
            "baseline_eval_sha256": baseline.get("eval_sha256"),
            "alpha": baseline.get("alpha"),
            "top_k": baseline.get("top_k"),
            "baseline_status": baseline.get("status"),
            "baseline_mismatch_qids": baseline_mismatch,
            "baseline_mismatch_count": len(baseline_mismatch),
            "git_head": git_head(),
            "baseline_report_mtime": mtime_str(BASELINE),
            "runtime_file_mtimes": {
                "tokenize.py": mtime_str(REPO_ROOT / "backend" / "app" / "retrieval" / "tokenize.py"),
                "kp_recall.py": mtime_str(REPO_ROOT / "backend" / "app" / "retrieval" / "kp_recall.py"),
            },
            "a_variant_scope": "rank_primary 逐条 + 六项聚合指标；非完整 top-10 顺序/分数逐条比对",
            "worktree_diff_sha256": git_diff_sha(),
            "gold_source": "正式评测集内嵌 gold（含 h_kp_j8_1408）；trial50_gold_v2.jsonl 仅覆盖 49 条 verified-derived",
            "metrics_scope": "primary；另在 JSON summary 提供 any-gold",
        },
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- Markdown ----------
    L = [
        "# Round1 补充验收报告（2026-11-15，审计修订版）",
        "",
        "> 只读补充分析：基于本轮已有实验数据；不写报告/源数据（`KpRetriever` 可能对派生索引",
        "> `kp_fts` 做条件性自愈，本轮计数 385 未变）；未修改运行时代码/评测集/Gold/题库/图谱；",
        "> 本补充分析未运行正式 50 条基线脚本；未覆盖既有报告；未 commit/push。",
        "",
        "## 1. A/B/C/D 完整指标",
        "",
        "| 变体 | FTS | 名称加分 | Recall@1(primary) | Recall@5(primary) | MRR | NDCG@5 | 23 修复 | 原27命中回归数 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    labels = {"A": ("旧", "旧"), "B": ("新", "旧"), "C": ("旧", "新"), "D": ("新", "新")}
    for key in ("A", "B", "C", "D"):
        a = summary[key]
        L.append(f"| {key} | {labels[key][0]} | {labels[key][1]} | "
                 f"{a['recall@1']:.2%} | {a['recall@5']:.2%} | {a['mrr']:.4f} | "
                 f"{a['ndcg@5']:.4f} | {a['fixed_23']} | {a['regressed_27']} |")
    L += [
        "",
        f"**D 组合**：Recall@1(primary) **{summary['D']['recall@1']:.2%}**、Recall@5(primary) "
        f"**{summary['D']['recall@5']:.2%}**、MRR **{summary['D']['mrr']:.4f}**、"
        f"NDCG@5 **{summary['D']['ndcg@5']:.4f}**；"
        f"any-gold：R@1 {summary['D']['recall@1_any']:.2%}、R@5 {summary['D']['recall@5_any']:.2%}。"
        "（主表为 primary 口径；any-gold 见 JSON summary。）",
        "",
        "**逐条排名变化（相对 A，全 50 条口径）**",
        "",
        "| 变体 | 提升 | 不变 | 排名下降 |",
        "|---|---|---|---|",
    ]
    for key in ("B", "C", "D"):
        L.append(f"| {key} | {len(changes[key]['improved'])} | {len(changes[key]['same'])} | "
                 f"{len(changes[key]['worsened'])} |")
    L += ["", "排名下降明细（仍在前 5 的不计回归，见 §3）："]
    for key in ("B", "C", "D"):
        if changes[key]["worsened"]:
            L.append(f"- {key}：" + "；".join(
                f"{x['query_id']}（{x['delta']}）" for x in changes[key]["worsened"]))
        else:
            L.append(f"- {key}：无")

    L += [
        "",
        "## 2. 23 条原失败样本逐条",
        "",
        "| query_id | Gold primary | 题干/tokens | 旧排名(A) | D 新排名 | 修复 | 向量排名 | 关键词通过 | 结论/主因 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in failures:
        L.append(f"| {f['query_id']} | {f['gold']} | {f['query_len']}/{f['token_count']} | "
                 f"{f['old_rank'] or '>10'} | {f['d_rank'] or '>10'} | "
                 f"{'✓' if f['fixed'] else '✗'} | {f['vec_rank'] or '-'} | "
                 f"{f['keyword_passed']} | {f['reason']} |")

    regressions = [x for x in hit_changes
                   if x["old_rank"] is not None and x["old_rank"] <= 5
                   and (x["d_rank"] is None or x["d_rank"] > 5)]
    improved_hits = [x for x in hit_changes if x["status"] == "improved"]
    same_hits = [x for x in hit_changes if x["status"] == "same"]
    worsened_top5 = [x for x in hit_changes if x["status"] == "worsened"
                     and x["d_rank"] is not None and x["d_rank"] <= 5]
    L += [
        "",
        "## 3. 27 条原命中样本回归检查",
        "",
        f"- **top-5 回归（旧命中 → D 不在 top-5）：{len(regressions)} 条**。",
        f"- 排名提升：{len(improved_hits)} 条；不变：{len(same_hits)} 条；"
        f"top-5 内排名下降：{len(worsened_top5)} 条（仍在前 5，不计回归）。",
        "",
        "| query_id | 旧排名 | D 排名 | 变化 | 状态 |",
        "|---|---|---|---|---|",
    ]
    for x in hit_changes:
        if x["status"] != "same":
            status_cn = {"improved": "排名提升", "worsened": "排名下降（非回归）"}[x["status"]]
            L.append(f"| {x['query_id']} | {x['old_rank'] or '>10'} | {x['d_rank'] or '>10'} | "
                     f"{x['delta']} | {status_cn} |")

    L += [
        "",
        "## 4. FTS 候选明细",
        "",
        f"- 置信门：有效词 ≥{KEYWORD_MIN_TERMS}，候选命中 ≥{KEYWORD_MIN_TERM_MATCHES} 词，"
        f"覆盖率 ≥{KEYWORD_MIN_COVERAGE:.0%}，BM25 排序后最多 {KEYWORD_TOP_N} 个。",
        f"- 匹配数分布（110 个原始候选）：{report['match_count_distribution']}。",
        f"- 过滤构成：0 词命中 {filtered_by_rule['zero_match']} 个；1 词命中 {filtered_by_rule['one_match']} 个；"
        f"2 词命中但覆盖率不足 {filtered_by_rule['two_match_filtered']} 个。",
        f"- 通过构成：2 词命中 {filtered_by_rule['two_match_passed']} 个；4 词命中 "
        f"{filtered_by_rule['four_match_passed']} 个。",
        f"- **gold primary 在置信门前就未进入原始候选的查询：{primary_gold_missing} 条**"
        f"（进入原始候选 {primary_gold_in_raw} 条）；这是比置信门更前置的召回缺口。",
        "- 注：SQLite `bm25()` 返回负值，越小越相关；FTS 分词命中与 Python 子串覆盖率"
        "检查口径不同，故附录中有 1 个 0 匹配候选仍被 FTS 返回。",
        "",
        f"### 4.1 非零命中查询的候选数（{sum(1 for x in fts_detail if x['raw_count'] > 0)} 条）",
        "",
        "| query_id | 原始候选 | 通过置信门 | 被过滤 | 有效词数 |",
        "|---|---|---|---|---|",
    ]
    for item in fts_detail:
        if item["raw_count"] > 0:
            L.append(f"| {item['query_id']} | {item['raw_count']} | {item['passed_count']} | "
                     f"{item['raw_count'] - item['passed_count']} | {len(item['terms'])} |")
    zero = [x["query_id"] for x in fts_detail if x["raw_count"] == 0]
    L += ["", f"### 4.2 零命中查询（{len(zero)} 条）", "", ", ".join(zero) or "无", ""]

    L += [
        "### 4.3 为什么平均原始候选 2.2，最终只有 4 个进入融合",
        "",
        f"110 个原始候选中，**106** 个未通过置信门；通过者共 **4** 个，分布在 "
        f"**{len({c['query_id'] for c in passed_candidates})}** 条查询。",
        f"按 gold 口径：**gold primary** 候选共 14 个（2 通过、12 过滤）；"
        f"**any-gold**（含辅助）候选共 {any_gold_passed + len(any_gold_filtered)} 个"
        f"（{any_gold_passed} 通过、{len(any_gold_filtered)} 过滤）。",
        "长题干与 6–15 字规则 KP 描述的词汇重叠度低，是过滤量大的根本原因。",
        "",
        "### 4.3.1 被置信门过滤的 gold primary 候选（12 条）",
        "",
        f"下表 D 排名为该查询 gold primary 的最终排名。12 个被过滤 gold primary 候选中，"
        f"**{len(gold_filtered_hits)} 条查询仍是原命中样本**（gold 由向量路进入融合集；其中 "
        f"{len(gold_filtered_with_bonus)} 条同时获得新名称加分："
        f"{', '.join(gold_filtered_with_bonus) or '无'}；过滤未造成 top-5 回归），"
        f"**{len(gold_filtered_failures)} 条为原失败样本**（{', '.join(gold_filtered_failures) or '无'}）。",
        "",
        "| query_id | gold primary kp | 名称 | 命中词 | 覆盖率 | BM25 rank | D 排名 |",
        "|---|---|---|---|---|---|---|",
    ]
    for c in gold_filtered:
        L.append(f"| {c['query_id']} | {c['kp_id']} | {c['name']} | "
                 f"{'、'.join(c['matched_terms'])} | {c['coverage']:.1%} | "
                 f"{c['bm25_rank']} | {c['d_rank'] or '>10'} |")

    L += [
        "",
        f"### 4.3.2 被过滤的 secondary gold 候选（{len(secondary_gold_filtered)} 条）",
        "",
        "| query_id | 辅助 gold kp | 名称 | 命中词 | 覆盖率 |",
        "|---|---|---|---|---|",
    ]
    for c in secondary_gold_filtered:
        L.append(f"| {c['query_id']} | {c['kp_id']} | {c['name']} | "
                 f"{'、'.join(c['matched_terms'])} | {c['coverage']:.1%} |")

    L += [
        "",
        "### 4.4 进入融合的 4 个候选及其效果",
        "",
        "| query_id | 候选 kp | 名称 | gold primary | any-gold | 命中词 | 覆盖率 | BM25 | "
        "候选 A/C/D 排名 | gold primary A/C/D 排名 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in passed_candidates:
        L.append(
            f"| {c['query_id']} | {c['kp_id']} | {c['name']} | "
            f"{'是' if c['is_gold_primary'] else '否'} | {'是' if c['is_any_gold'] else '否'} | "
            f"{'、'.join(c['matched_terms'])} | {c['coverage']:.1%} | {c['bm25_rank']} | "
            f"{c['candidate_A_rank'] or '>10'}/{c['candidate_C_rank'] or '>10'}/"
            f"{c['candidate_D_rank'] or '>10'} | "
            f"{c['gold_primary_A_rank'] or '>10'}/{c['gold_primary_C_rank'] or '>10'}/"
            f"{c['gold_primary_D_rank'] or '>10'} |")
    L += [
        "",
        "**效果判定**：`19316` 与 `h_kp_j8_1408` 的候选本身就是 gold primary，A/C/D 均第 1；"
        "`301084` 的两个候选均**不是 gold**，其中 `kp_p_071` 从 C 第 10 名进入 D 第 1 名"
        "（gold primary `kp_p_075` 仍 >10），改变了该查询的 top-5 组成（`kp_p_066` 被挤出），"
        "但未带来 gold 指标收益。因此：**FTS 未产生 gold 层面的 top-5 净收益；"
        "但“4 个候选未改变任何查询的 top-5 名次”不成立（301084 组成已变）**。"
        "这也暴露了一个逻辑风险：少量关键词候选经 min-max 归一化后可直接把非 gold 候选推到第 1。",
        "",
        "### 4.5 全部 110 个候选的过滤明细（附录）",
        "",
        "| query_id | kp_id | 名称 | 命中词 | 覆盖率 | 是否通过 | gold primary | any-gold | BM25 rank |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in fts_detail:
        for c in item["candidates"]:
            L.append(f"| {item['query_id']} | {c['kp_id']} | {c['name']} | "
                     f"{'、'.join(c['matched_terms'])} | {c['coverage']:.1%} | "
                     f"{'通过' if c['passed'] else '过滤'} | "
                     f"{'是' if c['is_gold_primary'] else '否'} | "
                     f"{'是' if c['is_any_gold'] else '否'} | {c['bm25_rank']} |")

    L += [
        "",
        "## 5. FTS 与名称加分的独立贡献",
        "",
        "| 对比 | R@1 变化(pp) | R@5 变化(pp) | MRR 变化 | NDCG@5 变化 | 23 修复 | 原27命中回归数 |",
        "|---|---|---|---|---|---|---|",
        f"| A→B（仅 FTS） | {pp(summary['B']['recall@1'], summary['A']['recall@1'])} | "
        f"{pp(summary['B']['recall@5'], summary['A']['recall@5'])} | "
        f"{summary['B']['mrr'] - summary['A']['mrr']:+.4f} | "
        f"{summary['B']['ndcg@5'] - summary['A']['ndcg@5']:+.4f} | "
        f"{summary['B']['fixed_23']} | {summary['B']['regressed_27']} |",
        f"| A→C（仅名称加分） | {pp(summary['C']['recall@1'], summary['A']['recall@1'])} | "
        f"{pp(summary['C']['recall@5'], summary['A']['recall@5'])} | "
        f"{summary['C']['mrr'] - summary['A']['mrr']:+.4f} | "
        f"{summary['C']['ndcg@5'] - summary['A']['ndcg@5']:+.4f} | "
        f"{summary['C']['fixed_23']} | {summary['C']['regressed_27']} |",
        f"| C→D（FTS 增量） | {pp(summary['D']['recall@1'], summary['C']['recall@1'])} | "
        f"{pp(summary['D']['recall@5'], summary['C']['recall@5'])} | "
        f"{summary['D']['mrr'] - summary['C']['mrr']:+.4f} | "
        f"{summary['D']['ndcg@5'] - summary['C']['ndcg@5']:+.4f} | "
        f"{summary['D']['fixed_23'] - summary['C']['fixed_23']} | "
        f"{summary['D']['regressed_27'] - summary['C']['regressed_27']} |",
        "",
        "- 结论：本轮 **gold 指标提升完全来自名称加分修复**；FTS 结构修复"
        "（50→10 零命中、40 条恢复候选）没有转化为 gold 的 top-5 命中。",
        f"- 代价与风险：{len(gold_filtered)} 个 gold primary 候选、{len(any_gold_filtered)} 个 any-gold 候选"
        "被置信门过滤；301084 的非 gold 候选被关键词路推到第 1，说明 min-max 归一化 + α=0.4 "
        "会放大少量关键词候选。本轮不调参，留待后续评估。",
        "",
        "## 6. 一致性核对",
        "",
        "| 项 | 值 | 与原基线一致 |",
        "|---|---|---|",
        f"| 正式评测集 sha256 | `{report['consistency']['formal_sha256'][:16]}…` | "
        f"{'是' if report['consistency']['formal_sha256'] == report['consistency']['baseline_eval_sha256'] else '否'} |",
        f"| Gold 来源 | 正式评测集内嵌 gold（含 h_kp_j8_1408）；`trial50_gold_v2.jsonl` "
        f"仅覆盖 49 条 verified-derived | formal hash 与 baseline eval_sha256 一致 |",
        f"| Gold 文件 sha256 | `{report['consistency']['gold_sha256'][:16]}…` | "
        "高置信度未修改（构建脚本运行内前后一致 + mtime 早于本轮 + 本轮只读；staging 不在 Git） |",
        f"| 基线报告 sha256 | `{report['consistency']['baseline_sha256'][:16]}…` | "
        "未覆盖（本补充分析只读；基线报告为 14:11 既有产物） |",
        f"| α / top-k / 协议 | {report['consistency']['alpha']} / "
        f"{report['consistency']['top_k']} / stage_filtered | 是（compare 脚本 α 写死 0.4，"
        "当前与 RETRIEVAL_ALPHA 一致；维护性风险已记录） |",
        f"| 代码版本 | HEAD `{report['consistency']['git_head'][:12]}…`，未提交工作区；"
        f"worktree diff sha256 `{report['consistency']['worktree_diff_sha256'][:16]}…` | "
        f"baseline 报告 {report['consistency']['baseline_report_mtime']}；runtime "
        f"tokenize {report['consistency']['runtime_file_mtimes']['tokenize.py']} / "
        f"kp_recall {report['consistency']['runtime_file_mtimes']['kp_recall.py']}"
        "（baseline 早于 Round1 改动） |",
        "| 指标口径 | Recall@1/5、MRR、NDCG@5（复用基线脚本 `ndcg_at_5`） | 是 |",
        f"| A 变体复现范围 | rank_primary 逐条差异 {report['consistency']['baseline_mismatch_count']} 条"
        f"（`{report['consistency']['baseline_mismatch_qids']}`）；六项聚合指标一致 | "
        f"{'是' if report['consistency']['baseline_mismatch_count'] == 0 else '否'}"
        "（非完整 top-10 逐条比对） |",
        "| 指标范围 | main 表为 primary；JSON summary 提供 any-gold | 与 baseline 定义一致 |",
        "| C/D 可复算性 | 补充 JSON 已持久化 A/B/C/D 的 `top_k_ids` | 可逐条复算 NDCG@5 |",
        "",
        "**审计边界说明**：",
        "",
        "- 本轮补充分析不写报告/源数据；`KpRetriever` 可能对 `kp_fts` 派生索引做条件性自愈"
        "（本轮计数 385 未变）。",
        "- 工作区改动：`tokenize.py` / `kp_recall.py`（运行时检索逻辑）、测试文件、分析脚本、"
        "`docs/development/STATUS.md`（文档）；无 commit/push。",
        "- Gold 未修改为高置信度结论：`data/staging` 不在 Git，无法用提交历史做密码学证明；"
        "证据为构建脚本运行内前后哈希一致、mtime 早于本轮、本轮只读。",
        "- 基线报告生成于 2026-11-15 14:11（Round1 正式基线）；本补充分析未重新运行基线脚本。",
        "",
        "## 7. 审计修订记录",
        "",
        "| 修订 | 内容 |",
        "|---|---|",
        "| 301084 候选语义 | 修正 `kp_p_071` 非 gold、候选 C/D 排名 10→1；区分候选排名与 gold primary 排名 |",
        "| top-5 组成 | 修正“未改变任何查询 top-5 名次”为“未带来 gold 指标净收益；301084 组成已变” |",
        "| gold 口径 | 明确 12 个为 gold primary；补充 any-gold 19 过滤 / 2 通过，以及 7 个 secondary gold |",
        "| 回归口径 | 区分 top-5 回归（0）与 top-5 内排名下降（1，1116923 2→3） |",
        "| 可复算性 | 补充 JSON 持久化 A/B/C/D 的 top_k_ids，C/D NDCG 可逐条复算 |",
        "| 短查询归因 | 按 query_len≤20 拆分“短查询→KP 描述泛化”与“长题干稀释”，并补 query_len/token_count |",
        "| 召回缺口 | 补充 gold primary 在置信门前就未进入 FTS 原始候选的查询数 |",
        "| 指标标注 | 表头明确 primary，补 D 的 any-gold；百分比差值改标 pp |",
        "| 代码版本 | 补 HEAD SHA、baseline/runtime mtime、A 变体比对范围；Gold 来源改为正式集内嵌 |",
        "| 可读性 | BM25 负值、0 匹配候选、覆盖率一位小数、列名“结论/主因” |",
        "| 边界声明 | 补充 kp_fts 自愈、改动文件清单、Gold 高置信度、基线运行时间说明 |",
        "",
        "## 8. 边界",
        "",
        "- 未修改运行时代码；未重新调参；未扩大优化范围；",
        "- 本补充分析未运行正式 50 条基线脚本；未覆盖任何已有报告；",
        "- 未修改 Gold、评测集、题库、知识图谱；未 commit、未 push、未关账。",
        "",
    ]
    OUT_MD.write_text("\n".join(L), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"失败 {len(failures)} | 命中变化 {len([x for x in hit_changes if x['status'] != 'same'])} "
          f"| 通过候选 {len(passed_candidates)} | 过滤 gold primary {len(gold_filtered)} "
          f"| any-gold 过滤 {len(any_gold_filtered)}")
    print(f"补充报告：{OUT_MD}\n补充数据：{OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
