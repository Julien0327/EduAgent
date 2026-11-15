"""正式检索基线评测（Phase 2 / §4.5）。

数据边界（硬性）：
- 只读 `retrieval_eval_queries_formal_v1.jsonl`（正式集 50 条，全部 confirmed）；
- **绝不读取** `retrieval_eval_queries_extra_drafts.jsonl` 参与指标（探索集 20 条，
  usage=exploratory / formal_metric=false）；脚本会断言两者无交叉。
- 只读检索，不写业务数据；不修改运行时代码。

指标（知识点阶段，口径固定）：
- Recall@1 / Recall@5（primary）：gold_primary 是否在 top-1 / top-5；
- Recall@1 / Recall@5（any gold）：任一 gold_kp_id 是否在 top-1 / top-5；
- MRR（primary）：1/rank（top-10 内；未命中记 0）；
- NDCG@5：分级相关（primary=2，辅助=1；gain=2^rel−1，log2 折扣）。
两种协议：
- `stage_filtered`（主口径）：传入查询自带学段（49 条 primary + 1 条 junior）；
- `open`（诊断口径）：不传学段（全学段召回）。

记录：模型加载/服务初始化、各协议检索总耗时与单次延迟分布、评测总耗时、
逐条异常（单条异常记录后继续，不修改运行时代码）、运行成本（本地推理零 API 费用）。

用法：
    # 只校验数据构成，不加载模型、不计算指标
    .venv/Scripts/python.exe -m backend.scripts.eval_retrieval_baseline --dry-run
    # 正式基线（需用户明确授权后执行）
    .venv/Scripts/python.exe -m backend.scripts.eval_retrieval_baseline
"""
import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "data" / "staging"
FORMAL_FILE = STAGING / "retrieval_eval_queries_formal_v1.jsonl"
EXTRA_FILE = STAGING / "retrieval_eval_queries_extra_drafts.jsonl"
OUT_JSON = STAGING / "retrieval_baseline_report.json"
OUT_MD = STAGING / "retrieval_baseline_report.md"

TOP_K = 10
PROTOCOLS = ("stage_filtered", "open")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_and_validate() -> tuple[list[dict], dict]:
    formal = load_jsonl(FORMAL_FILE)
    extra = load_jsonl(EXTRA_FILE)

    checks = {
        "formal_count": len(formal),
        "formal_all_confirmed": all(q.get("status") == "confirmed" for q in formal),
        "formal_usage_all_formal": all(q.get("usage") == "formal" for q in formal),
        "formal_verified_derived": sum(1 for q in formal if q["source"] == "verified_derived"),
        "formal_newly_confirmed": sum(1 for q in formal if q["source"] == "handwritten_draft"),
        "formal_ids_unique": len({q["query_id"] for q in formal}) == len(formal),
        "formal_gold_primary_in_gold": all(q["gold_primary"] in q["gold_kp_ids"] for q in formal),
        "extra_count": len(extra),
        "extra_all_not_formal": all(q.get("formal_metric") is False
                                    and q.get("usage") == "exploratory" for q in extra),
        "no_id_overlap": not ({q["query_id"] for q in formal} & {q["query_id"] for q in extra}),
        "formal_sha256": sha256(FORMAL_FILE),
        "extra_sha256": sha256(EXTRA_FILE),
    }
    expected = {
        "formal_count": 50,
        "formal_all_confirmed": True,
        "formal_usage_all_formal": True,
        "formal_verified_derived": 49,
        "formal_newly_confirmed": 1,
        "formal_ids_unique": True,
        "formal_gold_primary_in_gold": True,
        "extra_count": 20,
        "extra_all_not_formal": True,
        "no_id_overlap": True,
    }
    failed = {k: checks[k] for k, v in expected.items() if checks[k] != v}
    if failed:
        raise SystemExit(f"数据构成校验失败（拒绝继续）：{failed}")
    return formal, checks


def _dcg(rels: list[int], k: int = 5) -> float:
    return sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(rels[:k]))


def ndcg_at_5(ranked_ids: list[str], gold_primary: str, gold_all: list[str],
              k: int = 5) -> float:
    rels = [2 if kp == gold_primary else (1 if kp in gold_all else 0)
            for kp in ranked_ids[:k]]
    ideal = [2] + [1] * max(len(set(gold_all)) - 1, 0)
    idcg = _dcg(ideal, k)
    return _dcg(rels, k) / idcg if idcg > 0 else 0.0


def evaluate_query(svc, q: dict, protocol: str, top_k: int) -> dict:
    stage = q["stage"] if protocol == "stage_filtered" else None
    results = svc.search_knowledge_points(q["query_text"], stage=stage, top_k=top_k)
    ranked = [r["kp_id"] for r in results]
    gold_primary = q["gold_primary"]
    gold_all = list(dict.fromkeys(q["gold_kp_ids"]))
    gold_set = set(gold_all)

    rank_primary = ranked.index(gold_primary) + 1 if gold_primary in ranked else None
    rank_any = next((i + 1 for i, kp in enumerate(ranked) if kp in gold_set), None)
    return {
        "query_id": q["query_id"],
        "source": q["source"],
        "stage": q["stage"],
        "gold_primary": gold_primary,
        "rank_primary": rank_primary,
        "rank_any_gold": rank_any,
        "recall1_primary": rank_primary == 1,
        "recall5_primary": rank_primary is not None and rank_primary <= 5,
        "recall1_any": rank_any == 1,
        "recall5_any": rank_any is not None and rank_any <= 5,
        "mrr_primary": (1.0 / rank_primary) if rank_primary else 0.0,
        "ndcg5": round(ndcg_at_5(ranked, gold_primary, gold_all), 4),
        "top_k_ids": ranked,
    }


def aggregate(rows: list[dict]) -> dict:
    n = len(rows)

    def mean(key: str) -> float:
        if not n:
            return 0.0
        total = 0.0
        for r in rows:
            v = r[key]
            total += 1.0 if v is True else 0.0 if v is False else float(v)
        return round(total / n, 4)

    return {
        "count": n,
        "recall@1_primary": mean("recall1_primary"),
        "recall@5_primary": mean("recall5_primary"),
        "recall@1_any_gold": mean("recall1_any"),
        "recall@5_any_gold": mean("recall5_any"),
        "mrr_primary": round(sum(r["mrr_primary"] for r in rows) / n, 4) if n else 0.0,
        "ndcg@5": round(sum(r["ndcg5"] for r in rows) / n, 4) if n else 0.0,
        "primary_in_top10": round(sum(1 for r in rows if r["rank_primary"]) / n, 4) if n else 0.0,
    }


def breakdown(rows: list[dict]) -> dict:
    by_source, by_stage = {}, {}
    for key, bucket in (("source", by_source), ("stage", by_stage)):
        for val in sorted({r[key] for r in rows}):
            bucket[val] = aggregate([r for r in rows if r[key] == val])
    return {"by_source": by_source, "by_stage": by_stage}


def run() -> int:
    formal, checks = load_and_validate()

    from backend.app.retrieval.embedding import MODEL_NAME
    from backend.app.retrieval.kp_recall import RETRIEVAL_ALPHA
    from backend.app.retrieval.service import RetrievalService

    timing = {"service_init_ms": None, "per_protocol": {}, "total_eval_ms": None}
    errors: list[dict] = []
    protocol_rows: dict[str, list[dict]] = {}

    t_total0 = time.perf_counter()
    t_init0 = time.perf_counter()
    svc = RetrievalService()  # 含 KP 元数据+向量矩阵+embedding 模型加载
    timing["service_init_ms"] = round((time.perf_counter() - t_init0) * 1000, 1)

    try:
        for protocol in PROTOCOLS:
            t_p0 = time.perf_counter()
            rows = []
            for q in formal:
                t_q0 = time.perf_counter()
                try:
                    row = evaluate_query(svc, q, protocol, TOP_K)
                except Exception as exc:  # 单条异常记录后继续，不修改运行时代码
                    message = f"{type(exc).__name__}: {exc}"
                    errors.append({"protocol": protocol, "query_id": q["query_id"],
                                   "error": message})
                    row = {
                        "query_id": q["query_id"], "source": q["source"],
                        "stage": q["stage"], "gold_primary": q["gold_primary"],
                        "rank_primary": None, "rank_any_gold": None,
                        "recall1_primary": False, "recall5_primary": False,
                        "recall1_any": False, "recall5_any": False,
                        "mrr_primary": 0.0, "ndcg5": 0.0, "top_k_ids": [],
                        "error": message,
                    }
                row["latency_ms"] = round((time.perf_counter() - t_q0) * 1000, 2)
                rows.append(row)
            total_ms = round((time.perf_counter() - t_p0) * 1000, 1)
            ok = [r for r in rows if "error" not in r]
            lat = sorted(r["latency_ms"] for r in ok)

            def _pct(p: float) -> float:
                return lat[min(len(lat) - 1, int(len(lat) * p))] if lat else 0.0

            timing["per_protocol"][protocol] = {
                "total_ms": total_ms,
                "attempted": len(rows),
                "successful": len(ok),
                "latency_ms": {
                    "p50": round(_pct(0.50), 2),
                    "p95": round(_pct(0.95), 2),
                    "max": round(max(lat), 2) if lat else 0.0,
                    "mean": round(sum(lat) / len(lat), 2) if lat else 0.0,
                },
            }
            protocol_rows[protocol] = rows
    finally:
        svc.close()
    timing["total_eval_ms"] = round((time.perf_counter() - t_total0) * 1000, 1)

    aggregates = {}
    breakdowns = {}
    for protocol, rows in protocol_rows.items():
        ok = [r for r in rows if "error" not in r]
        aggregates[protocol] = aggregate(ok)
        breakdowns[protocol] = breakdown(ok)

    report = {
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "ok" if not errors else "completed_with_errors",
        "model": MODEL_NAME,
        "alpha": RETRIEVAL_ALPHA,
        "top_k": TOP_K,
        "eval_file": str(FORMAL_FILE),
        "eval_sha256": checks["formal_sha256"],
        "extra_file_excluded": str(EXTRA_FILE),
        "data_checks": checks,
        "metric_definitions": {
            "recall@1_primary": "gold_primary 排名=1",
            "recall@5_primary": "gold_primary 排名<=5",
            "recall@1_any_gold / recall@5_any_gold": "任一 gold_kp_id 进入 top-1 / top-5",
            "mrr_primary": "1/rank（top-10 未命中记 0）",
            "ndcg@5": "分级相关 primary=2 / 辅助=1，gain=2^rel-1，log2 折扣",
            "protocols": "stage_filtered=按查询学段过滤（主口径）；open=全学段（诊断口径）",
        },
        "aggregate": aggregates,
        "breakdown": breakdowns,
        "per_query": protocol_rows,
        "timing": timing,
        "errors": errors,
        "runtime_cost": {
            "api_calls": 0,
            "estimated_cost_cny": 0.0,
            "embedding_queries": len(formal) * len(PROTOCOLS),
            "note": "本地 ONNX CPU 推理（bge-small-zh-v1.5），零 API 费用",
        },
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 正式检索基线报告（Phase 2 / §4.5）",
        "",
        f"- 生成时间：{report['run_at']}｜运行状态：**{report['status']}**",
        f"- 正式集：50 条（49 verified + 1 新确认），sha256 `{report['eval_sha256'][:16]}…`",
        f"- 探索集（20 条）**未参与**；模型 `{report['model']}`；α={report['alpha']}；top-k={TOP_K}",
        "",
        "## 汇总结论",
        "",
        "| 协议 | 成功/总数 | Recall@1(primary) | Recall@5(primary) | Recall@1(any) | Recall@5(any) | MRR | NDCG@5 | Top-10 命中率 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for p in PROTOCOLS:
        a = aggregates[p]
        t = timing["per_protocol"][p]
        lines.append(
            f"| {p} | {t['successful']}/{t['attempted']} | {a['recall@1_primary']:.2%} | "
            f"{a['recall@5_primary']:.2%} | {a['recall@1_any_gold']:.2%} | "
            f"{a['recall@5_any_gold']:.2%} | {a['mrr_primary']:.4f} | "
            f"{a['ndcg@5']:.4f} | {a['primary_in_top10']:.2%} |")

    sf, op = aggregates["stage_filtered"], aggregates["open"]
    lines += [
        "",
        "## 两种协议差异",
        "",
        "| 指标 | stage_filtered（主口径） | open（诊断口径） | 差值 |",
        "|---|---|---|---|",
        f"| Recall@1(primary) | {sf['recall@1_primary']:.2%} | {op['recall@1_primary']:.2%} | "
        f"{(sf['recall@1_primary'] - op['recall@1_primary']) * 100:+.2f} pp |",
        f"| Recall@5(primary) | {sf['recall@5_primary']:.2%} | {op['recall@5_primary']:.2%} | "
        f"{(sf['recall@5_primary'] - op['recall@5_primary']) * 100:+.2f} pp |",
        f"| MRR | {sf['mrr_primary']:.4f} | {op['mrr_primary']:.4f} | "
        f"{sf['mrr_primary'] - op['mrr_primary']:+.4f} |",
        f"| NDCG@5 | {sf['ndcg@5']:.4f} | {op['ndcg@5']:.4f} | "
        f"{sf['ndcg@5'] - op['ndcg@5']:+.4f} |",
        "",
    ]

    lines += ["## 分来源（stage_filtered 主口径）", "",
              "| 来源 | 数量 | Recall@5(primary) | MRR | NDCG@5 |", "|---|---|---|---|---|"]
    for src, a in breakdowns["stage_filtered"]["by_source"].items():
        lines.append(f"| {src} | {a['count']} | {a['recall@5_primary']:.2%} | "
                     f"{a['mrr_primary']:.4f} | {a['ndcg@5']:.4f} |")

    lines += ["", "## 耗时与成本", "",
              "| 项 | 数值 |", "|---|---|",
              f"| 模型/服务初始化（含 KP 矩阵载入） | {timing['service_init_ms']} ms |",
              f"| 评测总耗时 | {timing['total_eval_ms']} ms |"]
    for p in PROTOCOLS:
        t = timing["per_protocol"][p]
        lines.append(f"| {p} 检索总耗时（{t['attempted']} 次） | {t['total_ms']} ms |")
        lines.append(f"| {p} 单次延迟 p50 / p95 / max / mean | "
                     f"{t['latency_ms']['p50']} / {t['latency_ms']['p95']} / "
                     f"{t['latency_ms']['max']} / {t['latency_ms']['mean']} ms |")
    lines.append(f"| API 调用 / 费用 | 0 次 / ¥0（本地 ONNX，零 API 费用；"
                 f"embedding 查询 {report['runtime_cost']['embedding_queries']} 次） |")

    lines += ["", "## 异常情况", ""]
    if errors:
        lines += ["| 协议 | query_id | 错误 |", "|---|---|---|"]
        for e in errors:
            lines.append(f"| {e['protocol']} | {e['query_id']} | {e['error']} |")
        lines += ["", f"> 共 {len(errors)} 条异常；汇总指标仅统计成功样本"
                      "（未修改运行时代码与评测集）。"]
    else:
        lines.append("无异常。")

    lines += ["", "## 未命中明细（stage_filtered，primary 不在 top-5）", "",
              "| q_id | 查询（截断） | gold primary | rank | top-5 |", "|---|---|---|---|---|"]
    for r in protocol_rows["stage_filtered"]:
        if "error" in r:
            continue
        if not r["recall5_primary"]:
            q = next(x for x in formal if x["query_id"] == r["query_id"])
            qtext = q["query_text"].replace("|", "｜")[:32]
            lines.append(f"| {r['query_id']} | {qtext} | {r['gold_primary']} | "
                         f"{r['rank_primary'] or '未入 top10'} | {', '.join(r['top_k_ids'][:5])} |")

    lines += ["", "## 逐条结果（完整数据见 JSON）", "",
              "| q_id | 来源 | rank primary | Recall@5 | MRR | NDCG@5 | 延迟(ms) |",
              "|---|---|---|---|---|---|---|"]
    for r in protocol_rows["stage_filtered"]:
        if "error" in r:
            lines.append(f"| {r['query_id']} | {r['source']} | ERROR | ✗ | - | - | {r['latency_ms']} |")
            continue
        lines.append(f"| {r['query_id']} | {r['source']} | {r['rank_primary'] or '-'} | "
                     f"{'✓' if r['recall5_primary'] else '✗'} | {r['mrr_primary']:.3f} | "
                     f"{r['ndcg5']:.3f} | {r['latency_ms']} |")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(aggregates, ensure_ascii=False, indent=2))
    print(json.dumps({"timing": timing, "errors": len(errors)}, ensure_ascii=False, indent=2))
    print(f"\n报告：{OUT_MD}\n原始数据：{OUT_JSON}")
    return 0 if not errors else 1


def dry_run() -> int:
    formal, checks = load_and_validate()
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    print(f"\n[DRY-RUN] 正式集 {len(formal)} 条构成校验通过；未加载模型、未计算任何指标。")
    print("指标口径：Recall@1/5(primary + any gold)、MRR(primary, top10)、NDCG@5(分级)。")
    print(f"协议：{PROTOCOLS}（stage_filtered 为主口径；open 为诊断口径）。")
    print("执行正式基线（需用户授权）：python -m backend.scripts.eval_retrieval_baseline")
    print(f"输出将写入：{OUT_JSON} / {OUT_MD}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="正式检索基线评测（默认需显式执行）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只校验数据构成，不加载模型、不计算指标")
    args = parser.parse_args()
    return dry_run() if args.dry_run else run()


if __name__ == "__main__":
    sys.exit(main())
