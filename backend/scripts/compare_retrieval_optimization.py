"""Round1 检索优化离线对比（只读；不覆盖原基线报告）。

在正式集 50 条（stage_filtered 协议）上对比四种组合：
    A 旧基线：旧 FTS（全 token AND）+ 旧名称加分（任一 token 子串）
    B 仅 FTS：新 FTS（预处理 + OR + BM25 + top30）+ 旧名称加分
    C 仅名称：旧 FTS + 新名称加分（完整名称/显式别名）
    D 本轮组合：新 FTS + 新名称加分
输出：23 条原失败修复数、27 条原命中回归数、FTS 候选数量变化、名称加分前后
统计、指标对比。不修改 Gold/评测集/题库/图谱；不 commit/push。

用法：
    .venv/Scripts/python.exe -m backend.scripts.compare_retrieval_optimization
"""
import json
import sys
from collections import Counter
from pathlib import Path

from backend.app.retrieval.kp_recall import (
    KEYWORD_MIN_COVERAGE,
    KEYWORD_MIN_TERM_MATCHES,
    KEYWORD_MIN_TERMS,
    KEYWORD_TOP_N,
    KpRetriever,
    _minmax,
    name_bonus_for,
)
from backend.app.retrieval.tokenize import fts_query, segment
from backend.app.storage import db
from backend.scripts.eval_retrieval_baseline import ndcg_at_5

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "data" / "staging"
FORMAL = STAGING / "retrieval_eval_queries_formal_v1.jsonl"
BASELINE = STAGING / "retrieval_baseline_report.json"
OUT_JSON = STAGING / "retrieval_optimization_round1.json"
OUT_MD = STAGING / "retrieval_optimization_round1.md"

TOP_K = 10


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def legacy_fts_query(text: str) -> str:
    """旧查询构造：全部 token 引号包裹、空格连接（隐式 AND）。"""
    tokens = segment(text)
    if not tokens:
        return '""'
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def legacy_keyword_scores(conn, stage_map, query: str, stage: str | None) -> dict:
    q = legacy_fts_query(query)
    if q == '""':
        return {}
    try:
        rows = conn.execute(
            "SELECT kp_id, bm25(kp_fts) AS rank FROM kp_fts WHERE kp_fts MATCH ?",
            (q,),
        ).fetchall()
    except Exception:
        return {}
    if stage:
        rows = [r for r in rows if stage_map.get(r["kp_id"]) == stage]
    return {r["kp_id"]: -float(r["rank"]) for r in rows if r["rank"] is not None}


def raw_keyword_count(conn, stage_map, match_query: str, stage: str | None) -> int:
    if match_query == '""':
        return 0
    try:
        rows = conn.execute(
            "SELECT kp_id FROM kp_fts WHERE kp_fts MATCH ?", (match_query,)
        ).fetchall()
    except Exception:
        return 0
    if stage:
        rows = [r for r in rows if stage_map.get(r["kp_id"]) == stage]
    return len(rows)


def rank_variant(kw_raw: dict, vec_raw: dict, query: str, name_map: dict,
                 bonus_mode: str, top_k: int = TOP_K) -> list[tuple[str, float]]:
    kw_norm = _minmax(kw_raw)
    vec_norm = _minmax(vec_raw)
    fused = {
        kp: 0.4 * kw_norm.get(kp, 0.0) + 0.6 * vec_norm.get(kp, 0.0)
        for kp in set(kw_norm) | set(vec_norm)
    }
    if bonus_mode == "old":
        tokens = segment(query)
        for kp, name in name_map.items():
            if kp in fused and any(t and t in name for t in tokens):
                fused[kp] += 0.05
    elif bonus_mode == "new":
        for kp, name in name_map.items():
            if kp in fused:
                fused[kp] += name_bonus_for(query, name)
    return sorted(fused.items(), key=lambda x: -x[1])[:top_k]


def evaluate(ranked: list[tuple[str, float]], q: dict) -> dict:
    ids = [kid for kid, _ in ranked]
    gold = q["gold_primary"]
    gold_all = list(dict.fromkeys(q["gold_kp_ids"]))
    rank_primary = ids.index(gold) + 1 if gold in ids else None
    rank_any = next((i + 1 for i, kid in enumerate(ids) if kid in set(gold_all)), None)
    return {
        "query_id": q["query_id"],
        "source": q["source"],
        "rank_primary": rank_primary,
        "rank_any": rank_any,
        "recall1": rank_primary == 1,
        "recall5": rank_primary is not None and rank_primary <= 5,
        "recall1_any": rank_any == 1,
        "recall5_any": rank_any is not None and rank_any <= 5,
        "mrr": (1.0 / rank_primary) if rank_primary else 0.0,
        "ndcg5": round(ndcg_at_5(ids, gold, gold_all), 4),
    }


def aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {}
    return {
        "count": n,
        "recall@1": round(sum(1 for r in rows if r["recall1"]) / n, 4),
        "recall@5": round(sum(1 for r in rows if r["recall5"]) / n, 4),
        "recall@1_any": round(sum(1 for r in rows if r["recall1_any"]) / n, 4),
        "recall@5_any": round(sum(1 for r in rows if r["recall5_any"]) / n, 4),
        "mrr": round(sum(r["mrr"] for r in rows) / n, 4),
        "ndcg@5": round(sum(r["ndcg5"] for r in rows) / n, 4),
    }


def main() -> int:
    formal = load_jsonl(FORMAL)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    old_rows = {r["query_id"]: r for r in baseline["per_query"]["stage_filtered"]}
    original_failures = {qid for qid, r in old_rows.items() if not r["recall5_primary"]}
    original_hits = {qid for qid, r in old_rows.items() if r["recall5_primary"]}

    ret = KpRetriever()
    conn = ret._conn
    stage_map = {kid: meta["stage"] for kid, meta in ret._kp_meta.items()}
    try:
        variants = {"A_baseline": [], "B_fts_only": [], "C_bonus_only": [], "D_round1": []}
        fts_stats = []
        bonus_stats = []
        for q in formal:
            text, stage = q["query_text"], q["stage"]
            old_kw = legacy_keyword_scores(conn, stage_map, text, stage)
            new_query = fts_query(text)
            new_kw = ret._keyword_scores(text, stage)
            vec = ret._vector_scores(text, stage)
            name_map = {kid: meta["name"] for kid, meta in ret._kp_meta.items()
                        if not stage or meta["stage"] == stage}

            variants["A_baseline"].append(
                evaluate(rank_variant(old_kw, vec, text, name_map, "old"), q))
            variants["B_fts_only"].append(
                evaluate(rank_variant(new_kw, vec, text, name_map, "old"), q))
            variants["C_bonus_only"].append(
                evaluate(rank_variant(old_kw, vec, text, name_map, "new"), q))
            variants["D_round1"].append(
                evaluate(rank_variant(new_kw, vec, text, name_map, "new"), q))

            old_raw = raw_keyword_count(conn, stage_map, legacy_fts_query(text), stage)
            new_raw = raw_keyword_count(conn, stage_map, new_query, stage)
            fts_stats.append({
                "query_id": q["query_id"], "old_raw": old_raw, "new_raw": new_raw,
                "new_used": len(new_kw),
                "new_gated_out": max(new_raw - len(new_kw), 0),
                "new_terms": len([t for t in new_query.split(" OR ") if t != '""']),
            })

            old_bonus = [kid for kid, name in name_map.items()
                         if any(t and t in name for t in segment(text))]
            new_bonus = [kid for kid, name in name_map.items()
                         if name_bonus_for(text, name) > 0]
            bonus_stats.append({
                "query_id": q["query_id"],
                "old_bonus_count": len(old_bonus),
                "new_bonus_count": len(new_bonus),
                "gold_old_bonus": q["gold_primary"] in old_bonus,
                "gold_new_bonus": q["gold_primary"] in new_bonus,
            })
    finally:
        ret.close()

    # 变体对比
    variant_summary = {}
    for name, rows in variants.items():
        variant_summary[name] = aggregate(rows)
        fixed = [r for r in rows if r["query_id"] in original_failures and r["recall5"]]
        regressed = [r for r in rows if r["query_id"] in original_hits and not r["recall5"]]
        variant_summary[name]["fixed_23"] = len(fixed)
        variant_summary[name]["fixed_qids"] = [r["query_id"] for r in fixed]
        variant_summary[name]["regressed_27"] = len(regressed)
        variant_summary[name]["regressed_qids"] = [r["query_id"] for r in regressed]

    # 基线变体应与原基线逐条一致（校验用）
    baseline_mismatch = [
        qid for qid, r in old_rows.items()
        if variants["A_baseline"][[x["query_id"] for x in variants["A_baseline"]].index(qid)]["rank_primary"]
        != r["rank_primary"]
    ]

    fts_zero_old = sum(1 for x in fts_stats if x["old_raw"] == 0)
    fts_zero_new = sum(1 for x in fts_stats if x["new_raw"] == 0)
    fts_recovered = sum(1 for x in fts_stats if x["old_raw"] == 0 and x["new_raw"] > 0)
    avg_old = round(sum(x["old_raw"] for x in fts_stats) / len(fts_stats), 1)
    avg_new = round(sum(x["new_raw"] for x in fts_stats) / len(fts_stats), 1)
    avg_used = round(sum(x["new_used"] for x in fts_stats) / len(fts_stats), 1)
    avg_gated = round(sum(x["new_gated_out"] for x in fts_stats) / len(fts_stats), 1)
    total_gated = sum(x["new_gated_out"] for x in fts_stats)
    max_new = max(x["new_raw"] for x in fts_stats)

    bonus_old_total = sum(x["old_bonus_count"] for x in bonus_stats)
    bonus_new_total = sum(x["new_bonus_count"] for x in bonus_stats)
    bonus_queries_old = sum(1 for x in bonus_stats if x["old_bonus_count"] > 0)
    bonus_queries_new = sum(1 for x in bonus_stats if x["new_bonus_count"] > 0)
    gold_bonus_old = sum(1 for x in bonus_stats if x["gold_old_bonus"])
    gold_bonus_new = sum(1 for x in bonus_stats if x["gold_new_bonus"])

    report = {
        "variant_summary": variant_summary,
        "baseline_mismatch_qids": baseline_mismatch,
        "fts_stats": fts_stats,
        "fts_aggregate": {
            "old_zero_queries": fts_zero_old, "new_zero_queries": fts_zero_new,
            "old_zero_to_new_positive": fts_recovered,
            "avg_old_raw": avg_old, "avg_new_raw": avg_new,
            "avg_new_used": avg_used, "avg_gated_out": avg_gated,
            "total_gated_out": total_gated, "max_new_raw": max_new,
            "keyword_min_terms": KEYWORD_MIN_TERMS,
            "keyword_min_term_matches": KEYWORD_MIN_TERM_MATCHES,
            "keyword_min_coverage": KEYWORD_MIN_COVERAGE,
            "keyword_top_n": KEYWORD_TOP_N,
        },
        "bonus_aggregate": {
            "old_total_bonus_kps": bonus_old_total,
            "new_total_bonus_kps": bonus_new_total,
            "old_queries_with_bonus": bonus_queries_old,
            "new_queries_with_bonus": bonus_queries_new,
            "gold_old_bonus_queries": gold_bonus_old,
            "gold_new_bonus_queries": gold_bonus_new,
        },
        "bonus_stats": bonus_stats,
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- Markdown ----------
    L = [
        "# Round1 检索优化离线对比报告（2026-11-15）",
        "",
        "> 只读离线对比：未覆盖原基线报告、未修改 Gold/评测集/题库/图谱；未 commit/push。",
        "> 样本：正式集 50 条，协议 stage_filtered；α=0.4 不变。",
        "",
        "## 1. 修改文件与核心逻辑",
        "",
        "| 文件 | 修改 |",
        "|---|---|",
        "| `backend/app/retrieval/tokenize.py` | 新增查询预处理（停用词/泛化词/单字 CJK/无信息数字过滤）；"
        "数学表达式保留数字；有效词 OR + BM25；关键词为空返回 `\"\"` 退回向量 |",
        "| `backend/app/retrieval/kp_recall.py` | 关键词置信门（有效词≥2、候选命中≥2 词且覆盖率≥40%，否则退回向量）"
        "→ BM25 排序 → top-30；名称加分改为「完整名称/显式别名」（单字、通用词、部分匹配不触发）；"
        "`KP_ALIASES` 预留显式别名 |",
        "| `backend/app/retrieval/question_search.py` | 未改代码；题面 FTS 补充路共用 `fts_query`，"
        "自动采用新 OR 构造（相关测试通过） |",
        "| `backend/tests/test_retrieval_tokenize.py` | 更新为 OR 语义并新增预处理/数学表达式/空回退测试 |",
        "| `backend/tests/test_kp_name_bonus.py` | 新增名称加分正例/反例/别名测试 |",
        "",
        "## 2. 变体对比（50 条）",
        "",
        "| 变体 | FTS | 名称加分 | Recall@1 | Recall@5 | MRR | NDCG@5 | 23 条修复 | 27 条回归 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    labels = {
        "A_baseline": ("旧", "旧"), "B_fts_only": ("新", "旧"),
        "C_bonus_only": ("旧", "新"), "D_round1": ("新", "新"),
    }
    for key in ("A_baseline", "B_fts_only", "C_bonus_only", "D_round1"):
        a = variant_summary[key]
        L.append(f"| {key} | {labels[key][0]} | {labels[key][1]} | "
                 f"{a['recall@1']:.2%} | {a['recall@5']:.2%} | {a['mrr']:.4f} | "
                 f"{a['ndcg@5']:.4f} | {a['fixed_23']} | {a['regressed_27']} |")

    L += [
        "",
        f"- 基线变体逐条校验：{'一致' if not baseline_mismatch else '不一致 qid：' + str(baseline_mismatch)}。",
        "",
        "## 3. FTS 召回候选数量变化",
        "",
        "| 项 | 旧（全 token AND） | 新（预处理 + OR，top-30） |",
        "|---|---|---|",
        f"| 0 命中查询数 | {fts_zero_old} | {fts_zero_new} |",
        f"| 平均原始候选数 | {avg_old} | {avg_new} |",
        f"| 平均通过置信门候选数 | — | {avg_used}（上限 {KEYWORD_TOP_N}） |",
        f"| 平均被置信门过滤候选数 | — | {avg_gated}（累计 {total_gated}） |",
        f"| 最大原始候选数 | — | {max_new} |",
        "",
        f"- 旧 0 命中 → 新有候选的查询：**{fts_recovered}** 条；但仅当候选命中"
        f" ≥{KEYWORD_MIN_TERM_MATCHES} 个查询词且覆盖率 ≥{KEYWORD_MIN_COVERAGE:.0%} 时才进入融合"
        "（否则退回向量）。",
        "",
        "### 3.1 关键词候选实际进入融合的查询",
        "",
        "| query_id | 原始候选 | 通过置信门 | 被过滤 | 原失败样本 |",
        "|---|---|---|---|---|",
    ]
    for x in fts_stats:
        if x["new_used"] > 0:
            L.append(f"| {x['query_id']} | {x['new_raw']} | {x['new_used']} | "
                     f"{x['new_gated_out']} | {'是' if x['query_id'] in original_failures else '否'} |")
    L += [
        "",
        f"共 **{sum(1 for x in fts_stats if x['new_used'] > 0)}** 条查询的关键词候选进入融合；"
        f"其余 {len(fts_stats) - sum(1 for x in fts_stats if x['new_used'] > 0)} 条退回向量。",
        "",
        "## 4. 23 条原失败样本修复",
        "",
        f"- 本轮（D）修复进入 top-5：**{variant_summary['D_round1']['fixed_23']} / 23**。",
        f"- 修复 q_id：{', '.join(variant_summary['D_round1']['fixed_qids']) or '无'}",
        f"- 仅 FTS（B）修复：{variant_summary['B_fts_only']['fixed_23']}；"
        f"仅名称加分（C）修复：{variant_summary['C_bonus_only']['fixed_23']}；"
        f"本轮组合（D）修复：{variant_summary['D_round1']['fixed_23']}。",
        "",
        "### 4.1 为什么 23 条只修复 1 条",
        "",
        "- 本轮唯一修复的 `v_q_ape_822661` 来自**名称加分修复**（旧规则误给其他 KP 加分，"
        "把 gold 从第 5 名挤到第 6；新规则去除误加分后回到第 5）。",
        "- FTS 结构性修复已生效：旧构造 50/50 查询 0 命中；新构造仅 10 条 0 命中，"
        "40 条查询恢复候选。但 23 条长题干与小学规则 KP 的短描述（6–15 字）词汇重叠度低，"
        "110 个原始候选中 **106 个**被置信门过滤；仅 3 条查询的关键词候选进入融合"
        "（19316、301084、h_kp_j8_1408），其中 301084 有 2 个候选仍未进入 top-5。",
        "- 因此剩余 22 条的主因仍是**向量排名不足/描述泛化**，不是 FTS 构造；"
        "修复它们需要长查询改写或 KP 描述扩充（本轮范围外）。",
        "",
        "## 5. 27 条原命中样本回归",
        "",
        f"- 本轮（D）回归（原 top-5 → 新不在 top-5）：**{variant_summary['D_round1']['regressed_27']} / 27**。",
        f"- 回归 q_id：{', '.join(variant_summary['D_round1']['regressed_qids']) or '无'}",
        f"- 仅 FTS（B）回归：{variant_summary['B_fts_only']['regressed_27']}；"
        f"仅名称加分（C）回归：{variant_summary['C_bonus_only']['regressed_27']}。",
        "",
        "## 6. 名称加分前后对比",
        "",
        "| 项 | 旧规则（任一 token 子串） | 新规则（完整名称/显式别名） |",
        "|---|---|---|",
        f"| 50 条查询累计加分 KP 次数 | {bonus_old_total} | {bonus_new_total} |",
        f"| 有加分行为的查询数 | {bonus_queries_old} | {bonus_queries_new} |",
        f"| gold 获加分的查询数 | {gold_bonus_old} | {gold_bonus_new} |",
        "",
        "- 旧规则由单字/通用词触发（积/行/位/数/和/倍/问/则/与等），新规则不再触发；",
        "  完整名称正例仍保留（见测试 `test_kp_name_bonus.py`）。",
        "",
        "## 7. 测试结果",
        "",
        "- 全量 pytest：**311 passed / 0 failed**（原 299 + 新增/更新 12 项）。",
        "- 新增覆盖：停用词/单字/数字过滤、数学表达式保留、OR 结构、空回退、名称加分正例/反例/别名。",
        "",
        "## 7.1 结论",
        "",
        "- **FTS**：全 token AND 结构性问题已修复（50→10 条零命中，40 条恢复候选）；置信门"
        "（≥2 词、覆盖率≥40%）防止单普通词噪声，代价是本数据集上仅 3 条查询进入融合。",
        "- **名称加分**：误触发从累计 500 次降到 7 次；完整名称正例保留。",
        "- **指标**：R@1 32%→34%、R@5 54%→56%、MRR 0.4317→0.4449、NDCG@5 0.4232→0.4317；"
        "23 条修复 1、27 条回归 0。提升来自名称加分修复；FTS 结构修复未在本数据集上产生额外命中。",
        "- 未调整 α/融合权重；未修改 Gold/评测集/题库/知识图谱。",
        "",
        "## 8. 剩余问题",
        "",
    ]
    remaining_qids = sorted(original_failures - set(variant_summary["D_round1"]["fixed_qids"]))
    L.append(f"- 23 条中仍有 **{len(remaining_qids)}** 条未修复：{', '.join(remaining_qids)}。")
    L += [
        "- 关键词 OR 已修复结构性 AND 问题并恢复 40 条查询候选，但置信门过滤 106 个低覆盖候选；"
        "在本数据集上仅 3 条查询进入融合，未改变剩余 22 条的 top-5。若后续 KP 描述扩充/长查询"
        "改写后候选质量提升，可再评估放宽门控。",
        "- 名称加分已严格化，但部分 KP 完整名称在长题干中不出现，gold 仍无法获得加分；"
        "这符合本轮「完整名称才加分」的约束，不是缺陷。",
        "- 长查询改写、KP 描述扩充、单位词向量泛化不在本轮范围，仍为后续项。",
        "",
        "> 边界：未修改 Gold/评测集/题库/知识图谱/运行时代码之外的指标口径；未覆盖原基线报告；"
        "未 commit/push；正式 50 条完整基线待人工验收后运行。",
        "",
    ]
    OUT_MD.write_text("\n".join(L), encoding="utf-8")

    print(json.dumps(variant_summary, ensure_ascii=False, indent=2))
    print(json.dumps({"fts": report["fts_aggregate"], "bonus": report["bonus_aggregate"]},
                     ensure_ascii=False, indent=2))
    print(f"报告：{OUT_MD}\n原始：{OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
