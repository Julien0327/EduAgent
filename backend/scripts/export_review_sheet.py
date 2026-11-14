"""50 题人工核验表导出（Step 5-B1，设计文档 §7）。

从 trial500_qids.jsonl 按层配比抽 50 题（S12 ≥15、S1/S2/S7 各 ≥5，其余按配额
轮转分配），导出**盲评**核验表（q_id + 题干 + 空白 gold 栏，不含 LLM 输出）——
助手初标仅为辅助，最终 gold 由用户确认（第二版修订）。输出入 data/staging/
（含题目内容，不入 Git——决策㉒）。

用法（仓库根）：
    .venv/Scripts/python.exe -m backend.scripts.export_review_sheet \
        [--trial data/annotations/trial500_qids.jsonl] [--total 50]
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import zlib
from collections import Counter
from pathlib import Path

from backend.app.storage import db
from backend.scripts.build_trial500 import REPO_ROOT

DEFAULT_TRIAL = REPO_ROOT / "data" / "annotations" / "trial500_qids.jsonl"
DEFAULT_OUT_MD = REPO_ROOT / "data" / "staging" / "trial50_review_sheet.md"
DEFAULT_OUT_CSV = REPO_ROOT / "data" / "staging" / "trial50_review_sheet.csv"

SEED = 42
TOTAL = 50
# 最低配额（设计文档 §7）：S12 保证无偏、S1/S2/S7 重点层
MIN_PER_STRATUM = {"S12_random": 15, "S1_zero_link": 5, "S2_main_placeholder": 5,
                   "S7_lowfreq_kp": 5}


def _stratum_seed(seed: int, stratum: str) -> int:
    """稳定派生种子（zlib.crc32，不依赖进程内 hash 随机化）。"""
    return seed + zlib.crc32(stratum.encode("utf-8")) % 100000


def select_review_50(trial_rows: list[dict], total: int = TOTAL,
                     seed: int = SEED) -> list[dict]:
    """按层配比选 50 题：先满足 MIN_PER_STRATUM，其余按层配额降序轮转分配。"""
    by_stratum: dict[str, list[dict]] = {}
    for row in trial_rows:
        by_stratum.setdefault(row["stratum"], []).append(row)
    quotas = {s: len(rows) for s, rows in by_stratum.items()}

    picked: dict[str, list[dict]] = {}
    remaining = total
    for s, m in MIN_PER_STRATUM.items():
        k = min(m, quotas.get(s, 0), remaining)
        if k:
            picked[s] = random.Random(_stratum_seed(seed, s)).sample(
                sorted(by_stratum[s], key=lambda r: r["q_id"]), k)
            remaining -= k
    # 其余按层配额降序轮转 +1（不超过该层配额）
    order = sorted(quotas, key=lambda s: (-quotas[s], s))
    while remaining > 0:
        progressed = False
        for s in order:
            if remaining == 0:
                break
            already = len(picked.get(s, []))
            if already < quotas[s]:
                pool = sorted({r["q_id"] for r in by_stratum[s]}
                              - {r["q_id"] for r in picked.get(s, [])})
                take = random.Random(_stratum_seed(seed, s) + already).sample(pool, 1)[0]
                picked.setdefault(s, []).append({"q_id": take, "stratum": s})
                remaining -= 1
                progressed = True
        if not progressed:
            break
    rows = [r for s in order for r in picked.get(s, [])]
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 50 题人工核验表（盲评，staging）")
    parser.add_argument("--trial", default=str(DEFAULT_TRIAL))
    parser.add_argument("--db", default=None)
    parser.add_argument("--total", type=int, default=TOTAL)
    args = parser.parse_args()

    trial_rows = [json.loads(line) for line in
                  Path(args.trial).read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = select_review_50(trial_rows, total=args.total)
    assert len(rows) == args.total, f"核验选题 {len(rows)} != {args.total}"

    conn = db.connect(args.db)
    stems: dict[str, str] = {}
    for r in rows:
        row = conn.execute(
            "SELECT stem FROM questions WHERE q_id=? AND quality_state='processed'",
            (r["q_id"],)).fetchone()
        stems[r["q_id"]] = row["stem"] if row else "（题干缺失！）"
    conn.close()

    DEFAULT_OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(DEFAULT_OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["q_id", "stratum", "题干", "gold_kp_ids(逗号分隔)",
                         "gold_primary", "gold_difficulty(1-5)", "备注"])
        for r in rows:
            writer.writerow([r["q_id"], r["stratum"], stems[r["q_id"]], "", "", "", ""])

    lines = [
        "# 50 题人工核验表（盲评）", "",
        f"- 来源：{args.trial}（seed=42 试验集 500 题的分层子集）",
        f"- 选题：共 {len(rows)} 题；层分布 {dict(Counter(r['stratum'] for r in rows))}",
        "- **盲评**：本表不含 LLM 输出；助手初标仅为人工核验辅助，最终 gold 由用户确认。",
        "- gold 栏填写：gold_kp_ids（逗号分隔 kp_id）、gold_primary、gold_difficulty(1-5)。",
        "- 本文件含题目内容，入 data/staging/ 不入 Git（决策㉒）。",
        "",
    ]
    for r in rows:
        lines.append(f"## {r['q_id']}（{r['stratum']}）")
        lines.append("")
        lines.append(stems[r["q_id"]])
        lines.append("")
    DEFAULT_OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print(f"核验表已导出：{len(rows)} 题")
    print(f"  层分布：{dict(Counter(r['stratum'] for r in rows))}")
    print(f"  {DEFAULT_OUT_CSV}")
    print(f"  {DEFAULT_OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
