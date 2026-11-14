"""人工核验标记（verified=1）重放脚本（Phase 2 / S1 新增，决策⑲/㉚）。

背景：questions 的 verified 标记属于人工核验状态，而 ETL 是全量重建语义
（q_ape_% 行删除重插）——2026-11-14 S1 重跑分词化 ETL 时实测发现 49 题
verified=1 被冲掉。本脚本把 verified 标记从 Gold v2 终版（冻结于
data/staging/trial50_gold_v2.jsonl，不入 Git，决策⑳/㉙）重放到题库，
作为幂等命令链的固定第三步：

    etl_ape210k.py -> build_primary_kp.py -> restore_verified_flags.py

规则（决策㉚⑤）：status=confirmed 的题置 verified=1；pending_verification
（q_ape_1070129）**不标记**。gold 中出现库外 q_id 时 fail-fast（防静默漏标）。

用法（仓库根执行，需在 ETL 之后）：
    .venv/Scripts/python.exe -m backend.scripts.restore_verified_flags [--db <路径>]
"""
import argparse
import json
import sys
import time
from pathlib import Path

from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = REPO_ROOT / "data" / "staging" / "trial50_gold_v2.jsonl"


def run(db_path, gold_path: Path = GOLD_PATH) -> dict:
    gold_rows = [json.loads(line) for line in
                 gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    confirmed = [r["q_id"] for r in gold_rows if r.get("status") == "confirmed"]
    pending = [r["q_id"] for r in gold_rows if r.get("status") != "confirmed"]
    if not confirmed:
        raise ValueError(f"gold 文件无 confirmed 行：{gold_path}")

    conn = db.connect(db_path)
    t0 = time.time()
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    restored, missing = 0, []
    with conn:
        for q_id in confirmed:
            cur = conn.execute(
                "UPDATE questions SET verified=1, verified_at=? WHERE q_id=?",
                (now, q_id),
            )
            if cur.rowcount == 0:
                missing.append(q_id)
            else:
                restored += 1
        if missing:
            raise ValueError(f"gold confirmed 题在库中不存在（防静默漏标）：{missing}")

    verified_total = conn.execute(
        "SELECT COUNT(*) n FROM questions WHERE verified=1"
    ).fetchone()["n"]
    elapsed = time.time() - t0
    conn.close()
    return {
        "gold_path": str(gold_path),
        "gold_rows": len(gold_rows),
        "confirmed": len(confirmed),
        "pending_not_marked": pending,
        "restored": restored,
        "verified_total": verified_total,
        "elapsed_seconds": round(elapsed, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="verified=1 人工核验标记重放（幂等链第三步）")
    parser.add_argument("--db", default=None, help="业务库路径（默认 data/db/edumate.sqlite）")
    parser.add_argument("--gold", default=None, help="gold v2 终版路径（默认 staging 冻结文件）")
    args = parser.parse_args()

    report = run(args.db, Path(args.gold) if args.gold else GOLD_PATH)
    print(f"verified 重放完成：{report['restored']}/{report['confirmed']} 置 1，"
          f"库内 verified 总数 {report['verified_total']}（{report['elapsed_seconds']}s）")
    print(f"pending 未标记：{report['pending_not_marked']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
