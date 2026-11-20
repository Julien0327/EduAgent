"""构建线上演示专用 SQLite（Phase 6 / S2-B，许可隔离）。

从开发库复制后裁剪，只保留：
- 82 个小学 KP + 87 条前置边 + kp_fts；
- 410 道合成演示题（q_demo_*）+ q_kp + questions_fts；
- 合成演示班级（20 学生 / 2 考试 / 40 卷面题 / 800 答题）。

删除：所有 APE210K 题目与标签、旧班级/考试/答题、任务/Trace、会话、
评测数据。输出不进入 Git，用于 S3 上传。

用法：
    python -m backend.scripts.build_online_demo_db
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "data" / "db" / "edumate.sqlite"
DST = REPO_ROOT / "data" / "staging" / "edumate_online_demo.sqlite"
OUT_JSON = REPO_ROOT / "data" / "staging" / "online_demo_db_report.json"
OUT_MD = REPO_ROOT / "data" / "staging" / "online_demo_db_report.md"
CLASS_NAME = "六年级演示班（合成题库 v1）"


def _scalar(conn, sql: str, params=()):
    return conn.execute(sql, params).fetchone()[0]


def main() -> int:
    if not SRC.exists():
        print(f"源库不存在：{SRC}")
        return 1
    if DST.exists():
        DST.unlink()

    src = sqlite3.connect(str(SRC))
    dst = sqlite3.connect(str(DST))
    try:
        src.backup(dst)
        dst.execute("PRAGMA foreign_keys=OFF")
        with dst:
            class_rows = dst.execute(
                "SELECT class_id, import_batch_id FROM classes WHERE name=?", (CLASS_NAME,)
            ).fetchall()
            if not class_rows:
                print(f"未找到合成演示班级：{CLASS_NAME}")
                return 1
            class_ids = [r[0] for r in class_rows]
            batch_ids = [r[1] for r in class_rows if r[1]]
            ph = ",".join("?" * len(class_ids))

            exam_ids = [r[0] for r in dst.execute(
                f"SELECT exam_id FROM exams WHERE class_id IN ({ph})", class_ids
            )]
            exam_ph = ",".join("?" * len(exam_ids)) if exam_ids else "''"

            # 业务数据：只保留合成演示班级
            if exam_ids:
                dst.execute(
                    f"DELETE FROM student_answers WHERE exam_id NOT IN ({exam_ph})", exam_ids)
                dst.execute(
                    f"DELETE FROM exam_questions WHERE exam_id NOT IN ({exam_ph})", exam_ids)
                dst.execute(
                    f"DELETE FROM exams WHERE exam_id NOT IN ({exam_ph})", exam_ids)
            dst.execute(f"DELETE FROM students WHERE class_id NOT IN ({ph})", class_ids)
            dst.execute(f"DELETE FROM classes WHERE class_id NOT IN ({ph})", class_ids)

            # 题库：只保留合成题
            dst.execute("DELETE FROM q_kp WHERE q_id NOT LIKE 'q_demo_%'")
            dst.execute("DELETE FROM questions_fts WHERE q_id NOT LIKE 'q_demo_%'")
            dst.execute("DELETE FROM questions WHERE q_id NOT LIKE 'q_demo_%'")

            # 标注 / 评测 / 任务 / 会话：线上演示不需要，全部清空
            for table in (
                "annotation_reviews", "annotation_batches", "q_annotation_results",
                "q_difficulty_llm", "q_kp_llm", "eval_cases", "eval_runs",
                "messages", "sessions", "steps", "tasks",
            ):
                dst.execute(f"DELETE FROM {table}")

            # 导入批次：只保留合成班级的批次
            if batch_ids:
                bph = ",".join("?" * len(batch_ids))
                dst.execute(f"DELETE FROM import_batches WHERE batch_id NOT IN ({bph})", batch_ids)
            else:
                dst.execute("DELETE FROM import_batches")

        dst.execute("VACUUM")
        integrity = _scalar(dst, "PRAGMA integrity_check")
        counts = {
            "knowledge_points": _scalar(dst, "SELECT COUNT(*) FROM knowledge_points"),
            "kp_edges": _scalar(dst, "SELECT COUNT(*) FROM kp_edges"),
            "kp_fts": _scalar(dst, "SELECT COUNT(*) FROM kp_fts"),
            "questions": _scalar(dst, "SELECT COUNT(*) FROM questions"),
            "q_kp": _scalar(dst, "SELECT COUNT(*) FROM q_kp"),
            "questions_fts": _scalar(dst, "SELECT COUNT(*) FROM questions_fts"),
            "classes": _scalar(dst, "SELECT COUNT(*) FROM classes"),
            "students": _scalar(dst, "SELECT COUNT(*) FROM students"),
            "exams": _scalar(dst, "SELECT COUNT(*) FROM exams"),
            "exam_questions": _scalar(dst, "SELECT COUNT(*) FROM exam_questions"),
            "student_answers": _scalar(dst, "SELECT COUNT(*) FROM student_answers"),
        }
        ape_residue = {
            "questions": _scalar(dst, "SELECT COUNT(*) FROM questions WHERE q_id LIKE 'q_ape_%'"),
            "q_kp": _scalar(dst, "SELECT COUNT(*) FROM q_kp WHERE q_id LIKE 'q_ape_%'"),
            "questions_fts": _scalar(
                dst, "SELECT COUNT(*) FROM questions_fts WHERE q_id LIKE 'q_ape_%'"),
        }
        sizes = {"source_bytes": SRC.stat().st_size, "demo_bytes": DST.stat().st_size}
    finally:
        src.close()
        dst.close()

    expected = {
        "knowledge_points": 82, "kp_edges": 87, "kp_fts": 82,
        "questions": 410, "q_kp": 410, "questions_fts": 410,
        "classes": 1, "students": 20, "exams": 2,
        "exam_questions": 40, "student_answers": 800,
    }
    ok = (
        integrity == "ok"
        and all(counts[k] == v for k, v in expected.items())
        and all(v == 0 for v in ape_residue.values())
    )
    report = {
        "ok": ok,
        "integrity": integrity,
        "counts": counts,
        "expected": expected,
        "ape_residue": ape_residue,
        "sizes": sizes,
        "dst": str(DST),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 线上演示专用数据库构建报告",
        "",
        f"- 状态：**{'通过' if ok else '未通过'}**",
        f"- 输出：`{DST}`（{sizes['demo_bytes'] / 1024 / 1024:.2f} MB；源库 {sizes['source_bytes'] / 1024 / 1024:.1f} MB）",
        f"- integrity：`{integrity}`",
        f"- 计数：{json.dumps(counts, ensure_ascii=False)}",
        f"- APE210K 残留：{json.dumps(ape_residue, ensure_ascii=False)}",
        "",
        "## 许可边界",
        "",
        "- 仅保留 82 KP / 87 边与 410 道合成题；不含 APE210K 题目内容。",
        "- 仅保留合成演示班级与答题；不含其他班级/学生/任务/会话/评测数据。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
