"""小学单学段运行时切换：初中数据清理（Phase 3 / S0-2，决策㉝）。

**默认 dry-run，不写库**；只有显式 ``--execute`` 才执行删除。执行前必做：
1. 预检查（初中 KP 被标注/题库引用必须为 0；初中题必须为 0）；
2. SQLite Online Backup 到 ``data/staging/backups/``，校验 integrity + 计数 + sha256；
3. 事务内按外键顺序删除初中演示数据与初中 KP/边；
4. 重建 ``kp_fts`` 为小学行；
5. 校验结果并输出执行报告。

**不删除**：小学 KP/边、小学题库、q_kp/q_kp_llm/q_annotation_results、任务/步骤/
会话、import_batches（保留作审计）。小学前置边由 ``build_primary_graph.py`` 单独写入。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.switch_to_primary_scope            # dry-run
    .venv/Scripts/python.exe -m backend.scripts.switch_to_primary_scope --execute  # 执行删除
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from backend.app.retrieval.tokenize import fts_document
from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "db" / "edumate.sqlite"
BACKUP_DIR = REPO_ROOT / "data" / "staging" / "backups"
REPORT_DIR = REPO_ROOT / "data" / "staging"


class ScopeSwitchError(Exception):
    """清理前置条件不满足（拒绝执行）。"""


def _count(conn: sqlite3.Connection, sql: str, params=()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _ids(conn: sqlite3.Connection, sql: str, params=()) -> list[str]:
    return [r[0] for r in conn.execute(sql, params).fetchall()]


def inspect(conn: sqlite3.Connection) -> dict:
    """统计将删除/保留的数据与预检查项（只读）。"""
    junior_classes = _ids(conn, "SELECT class_id FROM classes WHERE stage='junior'")
    junior_exams = _ids(conn, "SELECT exam_id FROM exams WHERE stage='junior'")
    if junior_classes:
        q = ",".join("?" * len(junior_classes))
        junior_exams += _ids(
            conn, f"SELECT exam_id FROM exams WHERE class_id IN ({q})", junior_classes
        )
    junior_exams = sorted(set(junior_exams))
    junior_students = []
    if junior_classes:
        q = ",".join("?" * len(junior_classes))
        junior_students = _ids(
            conn, f"SELECT student_id FROM students WHERE class_id IN ({q})", junior_classes
        )
    junior_exam_qs = []
    if junior_exams:
        q = ",".join("?" * len(junior_exams))
        junior_exam_qs = [
            r[0] for r in conn.execute(
                f"SELECT id FROM exam_questions WHERE exam_id IN ({q})", junior_exams
            ).fetchall()
        ]

    def in_clause(ids):
        return "(" + ",".join("?" * len(ids)) + ")" if ids else "(NULL)"

    answers = _count(
        conn,
        f"SELECT COUNT(*) FROM student_answers WHERE exam_id IN {in_clause(junior_exams)}"
        f" OR student_id IN {in_clause(junior_students)}"
        f" OR exam_q_id IN {in_clause(junior_exam_qs)}",
        tuple(junior_exams) + tuple(junior_students) + tuple(junior_exam_qs),
    )

    junior_kps = _ids(conn, "SELECT kp_id FROM knowledge_points WHERE stage='junior'")
    junior_edges = _count(
        conn,
        "SELECT COUNT(*) FROM kp_edges WHERE from_kp IN"
        " (SELECT kp_id FROM knowledge_points WHERE stage='junior')"
        " OR to_kp IN (SELECT kp_id FROM knowledge_points WHERE stage='junior')",
    )
    kp_fts_rows = _count(conn, "SELECT COUNT(*) FROM kp_fts")

    prechecks = {
        "questions_junior": _count(conn, "SELECT COUNT(*) FROM questions WHERE stage='junior'"),
        "q_kp_junior_refs": _count(
            conn, "SELECT COUNT(*) FROM q_kp WHERE kp_id IN"
                  " (SELECT kp_id FROM knowledge_points WHERE stage='junior')"),
        "q_kp_llm_junior_refs": _count(
            conn, "SELECT COUNT(*) FROM q_kp_llm WHERE kp_id IN"
                  " (SELECT kp_id FROM knowledge_points WHERE stage='junior')"),
        "annotation_results_junior_refs": _count(
            conn, "SELECT COUNT(*) FROM q_annotation_results WHERE primary_kp_id IN"
                  " (SELECT kp_id FROM knowledge_points WHERE stage='junior')"),
        "exam_questions_junior_kp_json_outside_junior_exams": _count(
            conn,
            "SELECT COUNT(*) FROM exam_questions WHERE kp_ids LIKE '%kp_j%'"
            + (f" AND exam_id NOT IN {in_clause(junior_exams)}" if junior_exams else ""),
            tuple(junior_exams),
        ),
    }

    return {
        "db_path": None,  # main() 填充
        "junior_classes": junior_classes,
        "junior_exams": junior_exams,
        "junior_students": junior_students,
        "junior_exam_questions": junior_exam_qs,
        "junior_kps": junior_kps,
        "counts": {
            "classes_junior": len(junior_classes),
            "students_junior": len(junior_students),
            "exams_junior": len(junior_exams),
            "exam_questions_junior": len(junior_exam_qs),
            "student_answers_junior": answers,
            "knowledge_points_junior": len(junior_kps),
            "kp_edges_junior": junior_edges,
            "kp_fts_rows_before": kp_fts_rows,
        },
        "preserve": {
            "knowledge_points_primary": _count(
                conn, "SELECT COUNT(*) FROM knowledge_points WHERE stage='primary'"),
            "questions_total": _count(conn, "SELECT COUNT(*) FROM questions"),
            "q_kp_total": _count(conn, "SELECT COUNT(*) FROM q_kp"),
            "q_kp_llm_total": _count(conn, "SELECT COUNT(*) FROM q_kp_llm"),
            "annotation_results_total": _count(
                conn, "SELECT COUNT(*) FROM q_annotation_results"),
            "import_batches_total": _count(conn, "SELECT COUNT(*) FROM import_batches"),
            "tasks_total": _count(conn, "SELECT COUNT(*) FROM tasks"),
            "steps_total": _count(conn, "SELECT COUNT(*) FROM steps"),
        },
        "prechecks": prechecks,
    }


def backup(db_path: Path, backup_dir: Path = BACKUP_DIR) -> dict:
    """SQLite Online Backup + integrity/计数/sha256。"""
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"edumate_pre_primary_scope_{ts}.sqlite"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    chk = sqlite3.connect(str(path))
    chk.row_factory = sqlite3.Row
    try:
        integrity = chk.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {}
        for t in ("knowledge_points", "kp_edges", "questions", "classes", "students",
                  "exams", "exam_questions", "student_answers", "q_kp",
                  "q_kp_llm", "q_annotation_results", "import_batches", "tasks", "steps"):
            counts[t] = chk.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    finally:
        chk.close()
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "size_mb": round(path.stat().st_size / 1024 / 1024, 1),
        "sha256": sha,
        "integrity_check": integrity,
        "counts": counts,
    }


def _delete(conn: sqlite3.Connection, state: dict) -> dict:
    """按外键顺序删除；返回实际删除计数。调用方负责事务。"""
    jc, je, js, jq = (state["junior_classes"], state["junior_exams"],
                      state["junior_students"], state["junior_exam_questions"])

    def in_clause(ids):
        return "(" + ",".join("?" * len(ids)) + ")" if ids else "(NULL)"

    deleted = {}
    cur = conn.execute(
        f"DELETE FROM student_answers WHERE exam_id IN {in_clause(je)}"
        f" OR student_id IN {in_clause(js)} OR exam_q_id IN {in_clause(jq)}",
        tuple(je) + tuple(js) + tuple(jq),
    )
    deleted["student_answers"] = cur.rowcount
    cur = conn.execute(
        f"DELETE FROM exam_questions WHERE exam_id IN {in_clause(je)}", tuple(je))
    deleted["exam_questions"] = cur.rowcount
    cur = conn.execute(f"DELETE FROM exams WHERE exam_id IN {in_clause(je)}", tuple(je))
    deleted["exams"] = cur.rowcount
    cur = conn.execute(
        f"DELETE FROM students WHERE class_id IN {in_clause(jc)}", tuple(jc))
    deleted["students"] = cur.rowcount
    cur = conn.execute(f"DELETE FROM classes WHERE class_id IN {in_clause(jc)}", tuple(jc))
    deleted["classes"] = cur.rowcount
    cur = conn.execute(
        "DELETE FROM kp_edges WHERE from_kp IN"
        " (SELECT kp_id FROM knowledge_points WHERE stage='junior')"
        " OR to_kp IN (SELECT kp_id FROM knowledge_points WHERE stage='junior')"
    )
    deleted["kp_edges"] = cur.rowcount
    cur = conn.execute("DELETE FROM knowledge_points WHERE stage='junior'")
    deleted["knowledge_points"] = cur.rowcount
    return deleted


def _rebuild_kp_fts(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT kp_id, name, description FROM knowledge_points WHERE stage='primary'"
    ).fetchall()
    conn.execute("DELETE FROM kp_fts")
    conn.executemany(
        "INSERT INTO kp_fts (kp_id, name, description) VALUES (?, ?, ?)",
        [(r["kp_id"], fts_document(r["name"] or ""),
          fts_document(r["description"] or "")) for r in rows],
    )
    return len(rows)


def verify(conn: sqlite3.Connection) -> dict:
    conn.row_factory = sqlite3.Row
    return {
        "knowledge_points_total": _count(conn, "SELECT COUNT(*) FROM knowledge_points"),
        "knowledge_points_primary": _count(
            conn, "SELECT COUNT(*) FROM knowledge_points WHERE stage='primary'"),
        "knowledge_points_junior": _count(
            conn, "SELECT COUNT(*) FROM knowledge_points WHERE stage='junior'"),
        "kp_edges_total": _count(conn, "SELECT COUNT(*) FROM kp_edges"),
        "kp_edges_junior": _count(
            conn, "SELECT COUNT(*) FROM kp_edges WHERE from_kp LIKE 'kp_j%'"
                  " OR to_kp LIKE 'kp_j%'"),
        "kp_fts_rows": _count(conn, "SELECT COUNT(*) FROM kp_fts"),
        "questions_total": _count(conn, "SELECT COUNT(*) FROM questions"),
        "questions_junior": _count(conn, "SELECT COUNT(*) FROM questions WHERE stage='junior'"),
        "q_kp_total": _count(conn, "SELECT COUNT(*) FROM q_kp"),
        "q_kp_llm_total": _count(conn, "SELECT COUNT(*) FROM q_kp_llm"),
        "annotation_results_total": _count(conn, "SELECT COUNT(*) FROM q_annotation_results"),
        "classes_total": _count(conn, "SELECT COUNT(*) FROM classes"),
        "students_total": _count(conn, "SELECT COUNT(*) FROM students"),
        "exams_total": _count(conn, "SELECT COUNT(*) FROM exams"),
        "exam_questions_total": _count(conn, "SELECT COUNT(*) FROM exam_questions"),
        "student_answers_total": _count(conn, "SELECT COUNT(*) FROM student_answers"),
        "import_batches_total": _count(conn, "SELECT COUNT(*) FROM import_batches"),
        "tasks_total": _count(conn, "SELECT COUNT(*) FROM tasks"),
        "steps_total": _count(conn, "SELECT COUNT(*) FROM steps"),
        "integrity_check": conn.execute("PRAGMA integrity_check").fetchone()[0],
    }


def render_report(state: dict, phase: str, backup_info: dict | None = None,
                  deleted: dict | None = None, verified: dict | None = None) -> str:
    c, p = state["counts"], state["prechecks"]
    lines = [
        f"# 初中运行时清理{' dry-run ' if phase == 'dry-run' else '执行'}报告",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- 数据库：`{state['db_path']}`（{state.get('db_size_mb', '?')} MB）",
        f"- 阶段：**{phase}**（{'未写库' if phase == 'dry-run' else '已执行删除'}）",
        "",
        "## 1. 将删除 / 已删除的表与记录",
        "",
        "| 表 | 条件 | 数量 |",
        "|---|---|---|",
        f"| `classes` | stage='junior' | {c['classes_junior']} |",
        f"| `students` | 属于初中班级 | {c['students_junior']} |",
        f"| `exams` | stage='junior' 或属于初中班级 | {c['exams_junior']} |",
        f"| `exam_questions` | 属于初中考试 | {c['exam_questions_junior']} |",
        f"| `student_answers` | 属于初中考试/学生/题目 | {c['student_answers_junior']} |",
        f"| `kp_edges` | 任一端为初中 KP | {c['kp_edges_junior']} |",
        f"| `knowledge_points` | stage='junior' | {c['knowledge_points_junior']} |",
        f"| `kp_fts` | 清理前总行数（将重建为小学） | {c['kp_fts_rows_before']} |",
        "",
        "## 2. 保留的数据（不删）",
        "",
        "| 表 | 数量 |",
        "|---|---|",
        *[f"| `{k}` | {v} |" for k, v in state["preserve"].items()],
        "| `import_batches` | 保留作审计（含已提交/失败批次） |",
        "",
        "## 3. 外键影响与删除顺序",
        "",
        "1. `student_answers`（FK → exams/students/exam_questions）",
        "2. `exam_questions`（FK → exams/questions）",
        "3. `exams`（FK → classes）",
        "4. `students`（FK → classes）",
        "5. `classes`",
        "6. `kp_edges`（无 FK）",
        "7. `knowledge_points`（被 q_kp_llm/q_annotation_results 引用，但初中引用预检查必须为 0）",
        "8. `kp_fts` 全量重建为小学行",
        "",
        "## 4. 预检查（必须全部为 0 才允许执行）",
        "",
        "| 检查项 | 结果 |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in p.items()],
        "",
        "## 5. 备份方案",
        "",
        "- 使用 SQLite Online Backup API 备份到 `data/staging/backups/`（不入 Git）",
        "- 备份后执行 `PRAGMA integrity_check`、核心表计数、sha256、文件大小核验",
    ]
    if backup_info:
        lines += [
            "",
            f"- 备份文件：`{backup_info['path']}`",
            f"- 大小：{backup_info['size_mb']} MB（{backup_info['size_bytes']} 字节）",
            f"- integrity_check：**{backup_info['integrity_check']}**",
            f"- sha256：`{backup_info['sha256']}`",
            "- 备份核心表计数：" + "，".join(
                f"{k}={v}" for k, v in backup_info["counts"].items()),
        ]
    lines += [
        "",
        "## 6. 删除后动作",
        "",
        "1. 写入小学前置图：`python -m backend.scripts.build_primary_graph`（87 条）",
        "2. `kp_fts` 已重建为小学行；检索层启动时自愈核对",
        "3. 验证：小学 KP 82 / 小学边 87 / 初中 KP 0 / 初中边 0 / 初中演示数据 0",
        "",
        "## 7. 回滚",
        "",
        "1. 停止服务；2. 用备份文件覆盖 `data/db/edumate.sqlite`（含 -wal/-shm 清理）；",
        "3. `PRAGMA integrity_check` + 核心表计数核验；4. 重启服务。",
        "",
    ]
    if deleted:
        lines += ["## 8. 实际删除计数", "", "| 表 | 删除行数 |", "|---|---|",
                  *[f"| `{k}` | {v} |" for k, v in deleted.items()], ""]
    if verified:
        lines += ["## 9. 清理后验证", "", "| 项 | 结果 |", "|---|---|",
                  *[f"| {k} | {v} |" for k, v in verified.items()], ""]
    return "\n".join(lines)


def run(db_path: Path, execute: bool, backup_dir: Path = BACKUP_DIR,
        report_dir: Path = REPORT_DIR) -> int:
    report_dir.mkdir(parents=True, exist_ok=True)
    conn = db.connect(db_path)
    failed = False
    try:
        state = inspect(conn)
        state["db_path"] = str(db_path)
        state["db_size_mb"] = round(db_path.stat().st_size / 1024 / 1024, 1)
        bad = {k: v for k, v in state["prechecks"].items() if v != 0}
        if bad:
            raise ScopeSwitchError(f"预检查未通过（禁止删除）：{bad}")

        if not execute:
            md = render_report(state, "dry-run")
            (report_dir / "primary_scope_dryrun_report.md").write_text(md, encoding="utf-8")
            (report_dir / "primary_scope_dryrun_report.json").write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[DRY-RUN] 将删除：{state['counts']}")
            print(f"预检查：全部为 0；报告：{report_dir / 'primary_scope_dryrun_report.md'}")
            return 0

        t0 = time.perf_counter()
        backup_info = backup(db_path, backup_dir)
        if backup_info["integrity_check"] != "ok":
            raise ScopeSwitchError(f"备份 integrity_check 失败：{backup_info['integrity_check']}")

        with conn:
            deleted = _delete(conn, state)
            fts_rows = _rebuild_kp_fts(conn)
            verified = verify(conn)
            residual = {
                "knowledge_points_junior": verified["knowledge_points_junior"],
                "kp_edges_junior": verified["kp_edges_junior"],
                "classes_junior": _count(
                    conn, "SELECT COUNT(*) FROM classes WHERE stage='junior'"),
                "exams_junior": _count(
                    conn, "SELECT COUNT(*) FROM exams WHERE stage='junior'"),
                "students_in_junior_classes": _count(
                    conn, "SELECT COUNT(*) FROM students s JOIN classes c"
                          " ON c.class_id=s.class_id WHERE c.stage='junior'"),
                "exam_questions_for_junior_exams": _count(
                    conn, "SELECT COUNT(*) FROM exam_questions eq JOIN exams e"
                          " ON e.exam_id=eq.exam_id WHERE e.stage='junior'"),
                "student_answers_for_junior_exams": _count(
                    conn, "SELECT COUNT(*) FROM student_answers sa JOIN exams e"
                          " ON e.exam_id=sa.exam_id WHERE e.stage='junior'"),
                "knowledge_points_total_minus_primary": (
                    verified["knowledge_points_total"] - verified["knowledge_points_primary"]),
                "kp_fts_minus_primary": (
                    verified["kp_fts_rows"] - verified["knowledge_points_primary"]),
            }
            if any(residual.values()):
                raise ScopeSwitchError(f"清理后残留检查失败（事务回滚）：{residual}")
            verified["residual_junior"] = residual
        elapsed = round(time.perf_counter() - t0, 2)
        verified["kp_fts_rebuilt_rows"] = fts_rows
        verified["elapsed_seconds"] = elapsed
        md = render_report(state, "execute", backup_info, deleted, verified)
        (report_dir / "primary_scope_execution_report.md").write_text(md, encoding="utf-8")
        (report_dir / "primary_scope_execution_report.json").write_text(
            json.dumps({"state": state, "backup": backup_info, "deleted": deleted,
                        "verified": verified}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"[执行] 删除：{deleted}")
        print(f"备份：{backup_info['path']}（{backup_info['size_mb']} MB，sha256 "
              f"{backup_info['sha256'][:16]}…，integrity={backup_info['integrity_check']}）")
        print(f"验证：{verified}")
        print(f"报告：{report_dir / 'primary_scope_execution_report.md'}")
        return 0
    except ScopeSwitchError as exc:
        failed = True
        print(f"[清理失败] {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()
        if failed:
            print("未执行删除。", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="初中运行时数据清理（默认 dry-run）")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--backup-dir", default=str(BACKUP_DIR))
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--execute", action="store_true",
                        help="实际执行删除（默认仅 dry-run）；执行前自动备份")
    args = parser.parse_args()
    return run(Path(args.db), args.execute, Path(args.backup_dir), Path(args.report_dir))


if __name__ == "__main__":
    sys.exit(main())
