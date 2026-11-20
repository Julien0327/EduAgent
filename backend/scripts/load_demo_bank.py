"""把冻结的合成演示题库写入业务库（Phase 6 / S2-B）。

- 只写 `q_demo_%` 前缀的题目，不触碰 APE210K 题库；
- 同步写 questions / q_kp / questions_fts；
- 幂等：先删同前缀记录再全量写入；
- 输出导入前后计数与 KP 覆盖报告（staging，不入 Git）。

用法（仓库根执行）：
    python -m backend.scripts.load_demo_bank
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "db" / "edumate.sqlite"
BANK = REPO_ROOT / "data" / "demo" / "demo_bank_v1.jsonl"
OUT_JSON = REPO_ROOT / "data" / "staging" / "demo_bank_load_report.json"
OUT_MD = REPO_ROOT / "data" / "staging" / "demo_bank_load_report.md"
CREATED_AT = "2026-11-20T00:00:00"


def main() -> int:
    if not BANK.exists():
        print(f"题库不存在：{BANK}")
        return 1
    rows = [
        json.loads(line)
        for line in BANK.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(rows) != 410:
        print(f"题库题数异常：{len(rows)}")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    try:
        pre = {
            "questions_demo": conn.execute(
                "SELECT COUNT(*) FROM questions WHERE q_id LIKE 'q_demo_%'").fetchone()[0],
            "q_kp_demo": conn.execute(
                "SELECT COUNT(*) FROM q_kp WHERE q_id LIKE 'q_demo_%'").fetchone()[0],
            "fts_demo": conn.execute(
                "SELECT COUNT(*) FROM questions_fts WHERE q_id LIKE 'q_demo_%'").fetchone()[0],
            "questions_total": conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
        }
        with conn:
            conn.execute("DELETE FROM q_kp WHERE q_id LIKE 'q_demo_%'")
            conn.execute("DELETE FROM questions_fts WHERE q_id LIKE 'q_demo_%'")
            conn.execute("DELETE FROM questions WHERE q_id LIKE 'q_demo_%'")
            for q in rows:
                options = q.get("options") or []
                conn.execute(
                    "INSERT INTO questions (q_id, stage, subject, type, stem, options,"
                    " answer, analysis, score, difficulty, source, quality_state,"
                    " verified, verified_at, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        q["q_id"], "primary", "math", q.get("qtype") or "application",
                        q["stem"], json.dumps(options, ensure_ascii=False) if options else None,
                        q["answer"], q["solution"], float(q.get("score", 10.0)),
                        int(q["difficulty"]), "synthetic_demo_v1", "processed",
                        1, CREATED_AT, CREATED_AT,
                    ),
                )
                conn.execute(
                    "INSERT INTO q_kp (q_id, kp_id) VALUES (?, ?)",
                    (q["q_id"], q["kp_id"]),
                )
                conn.execute(
                    "INSERT INTO questions_fts (q_id, stem, answer) VALUES (?,?,?)",
                    (q["q_id"], q["stem"], q["answer"]),
                )
        post = {
            "questions_demo": conn.execute(
                "SELECT COUNT(*) FROM questions WHERE q_id LIKE 'q_demo_%'").fetchone()[0],
            "q_kp_demo": conn.execute(
                "SELECT COUNT(*) FROM q_kp WHERE q_id LIKE 'q_demo_%'").fetchone()[0],
            "fts_demo": conn.execute(
                "SELECT COUNT(*) FROM questions_fts WHERE q_id LIKE 'q_demo_%'").fetchone()[0],
            "questions_total": conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
            "kp_covered": conn.execute(
                "SELECT COUNT(DISTINCT kp_id) FROM q_kp WHERE q_id LIKE 'q_demo_%'").fetchone()[0],
        }
    finally:
        conn.close()

    ok = (
        post["questions_demo"] == 410
        and post["q_kp_demo"] == 410
        and post["fts_demo"] == 410
        and post["kp_covered"] == 82
    )
    report = {"ok": ok, "pre": pre, "post": post, "bank": str(BANK)}
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 合成演示题库写库报告",
        "",
        f"- 状态：**{'通过' if ok else '未通过'}**",
        f"- 题库：`{BANK}`",
        f"- 写入前：{pre}",
        f"- 写入后：{post}",
        "- 只写 `q_demo_%` 前缀；不触碰 APE210K 题库。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
