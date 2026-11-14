"""B 臂 recall@10 评估（Step 5-B2，设计文档 §13）。

以 50 题 Gold（当前=助手盲标初稿 trial50_gold_draft.jsonl，最终以用户确认为准）
评估 B 臂召回层：recall@10 / recall@5 = gold 主知识点 ∈ embedding 候选集比例；
漏召回题逐条列出（R13 治理输入）。本地推理零 API 费用。

用法（仓库根）：
    .venv/Scripts/python.exe -m backend.scripts.eval_recall [--gold <jsonl>] [--top-k 10]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from backend.app.annotation.executor import _load_kp_rows
from backend.app.annotation.recall import EmbeddingRecaller, RECALL_TOP_K, REPO_MODELS_DIR
from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLD = REPO_ROOT / "data" / "staging" / "trial50_gold_v2.jsonl"
OUT_JSON = REPO_ROOT / "data" / "staging" / "recall10_report.json"
OUT_MD = REPO_ROOT / "data" / "staging" / "recall10_report.md"


def main() -> int:
    parser = argparse.ArgumentParser(description="B 臂 recall@10 评估（50 题 gold）")
    parser.add_argument("--gold", default=str(DEFAULT_GOLD))
    parser.add_argument("--top-k", type=int, default=RECALL_TOP_K)
    args = parser.parse_args()

    gold = [json.loads(l) for l in Path(args.gold).read_text(encoding="utf-8").splitlines() if l.strip()]
    pending = [g for g in gold if g.get("status") == "pending_verification"]
    gold = [g for g in gold if g.get("status") != "pending_verification"]
    conn = db.connect()
    stems = {r["q_id"]: r["stem"] for r in conn.execute(
        "SELECT q_id, stem FROM questions WHERE quality_state='processed'")}
    conn.close()
    kp_rows = _load_kp_rows(None)

    recaller = EmbeddingRecaller(kp_rows, cache_dir=REPO_MODELS_DIR, top_k=args.top_k)
    t0 = time.perf_counter()
    hits10, hits5, misses = 0, 0, []
    details = []
    for g in gold:
        q_id = g["q_id"]
        cands = recaller.recall(q_id, stems[q_id])
        h10 = g["gold_primary"] in cands
        h5 = g["gold_primary"] in cands[:5]
        hits10 += h10
        hits5 += h5
        if not h10:
            misses.append({"q_id": q_id, "gold_primary": g["gold_primary"],
                           "stem": stems[q_id], "candidates": cands})
        details.append({"q_id": q_id, "gold_primary": g["gold_primary"],
                        "candidates": cands, "hit10": h10, "hit5": h5})
    elapsed = time.perf_counter() - t0
    n = len(gold)
    report = {
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "gold_source": str(args.gold),
        "gold_nature": "assistant_blind_draft（最终以用户确认为准）",
        "top_k": args.top_k, "n": n,
        "recall_at_k": round(hits10 / n, 4), "recall_at_5": round(hits5 / n, 4),
        "hits10": hits10, "hits5": hits5,
        "elapsed_seconds": round(elapsed, 1), "misses": misses, "details": details,
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# B 臂 recall@10 评估报告", "",
        f"- Gold：{args.gold}（**助手盲标初稿**，最终以用户确认为准）",
        f"- 召回器：bge-small-zh-v1.5 top-{args.top_k}（本地推理，{elapsed:.1f}s / {n} 题）",
        f"- **recall@{args.top_k} = {hits10}/{n} = {hits10/n:.1%}**；recall@5 = {hits5}/{n} = {hits5/n:.1%}",
        f"- pending_verification 题排除分母：{len(pending)} 题（{[g['q_id'] for g in pending]}）",
        f"- 漏召回 {len(misses)} 题（gold 主知识点不在 top-{args.top_k}）：", "",
    ]
    for m in misses:
        lines.append(f"- {m['q_id']}（gold={m['gold_primary']}）：{m['stem'][:60]}…")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:8]))
    print(f"报告：{OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
