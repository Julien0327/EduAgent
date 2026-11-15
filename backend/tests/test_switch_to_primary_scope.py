"""初中运行时清理脚本测试（Phase 3 / S0-2）。

覆盖：
- dry-run 不写库；
- execute 前自动备份（integrity/counts/sha256）；
- 初中班级/学生/考试/题目/答题/KP/边被删除；
- 小学 KP/边/题库/q_kp、import_batches、任务保留；
- kp_fts 重建为小学行；
- 残留检查（事务内）通过。
"""
import json
import sqlite3

import pytest

from backend.app.storage import db
from backend.scripts.switch_to_primary_scope import run


@pytest.fixture()
def scope_db(tmp_path):
    path = tmp_path / "scope.sqlite"
    db.apply_migrations(path)
    conn = db.connect(path)
    try:
        conn.executemany(
            "INSERT INTO knowledge_points (kp_id, name, stage, source)"
            " VALUES (?, ?, ?, 'test')",
            [("kp_p_001", "小学A", "primary"), ("kp_p_002", "小学B", "primary"),
             ("kp_j1", "初中A", "junior"), ("kp_j2", "初中B", "junior")],
        )
        conn.executemany(
            "INSERT INTO kp_edges (from_kp, to_kp, relation) VALUES (?, ?, 'prerequisite')",
            [("kp_p_001", "kp_p_002"), ("kp_j1", "kp_j2")],
        )
        conn.execute(
            "INSERT INTO questions (q_id, stage, type, stem, answer, score, difficulty, source)"
            " VALUES ('q_p', 'primary', 'fill', '1+1', '2', 5, 1, 'test')"
        )
        conn.execute("INSERT INTO q_kp (q_id, kp_id) VALUES ('q_p', 'kp_p_001')")
        conn.executemany(
            "INSERT INTO classes (class_id, name, grade, stage, created_at)"
            " VALUES (?, ?, ?, ?, '2026-11-15T00:00:00Z')",
            [("cls_p", "小学班", 3, "primary"), ("cls_j", "初中班", 9, "junior")],
        )
        conn.executemany(
            "INSERT INTO students (student_id, class_id, student_no, name, created_at)"
            " VALUES (?, ?, ?, ?, '2026-11-15T00:00:00Z')",
            [("stu_p", "cls_p", "001", "学生甲"), ("stu_j", "cls_j", "001", "学生乙")],
        )
        conn.executemany(
            "INSERT INTO exams (exam_id, title, class_id, stage, created_at)"
            " VALUES (?, ?, ?, ?, '2026-11-15T00:00:00Z')",
            [("exam_p", "小学考", "cls_p", "primary"),
             ("exam_j", "初中考", "cls_j", "junior")],
        )
        conn.executemany(
            "INSERT INTO exam_questions (exam_id, q_no, score) VALUES (?, ?, 10)",
            [("exam_p", 1), ("exam_j", 1)],
        )
        conn.executemany(
            "INSERT INTO student_answers (exam_id, student_id, exam_q_id, score)"
            " VALUES (?, ?, ?, 10)",
            [("exam_p", "stu_p", 1), ("exam_j", "stu_j", 2)],
        )
        conn.execute(
            "INSERT INTO import_batches (batch_id, kind, file_sha256, status, created_at)"
            " VALUES ('imp_test', 'full', 'abc', 'committed', '2026-11-15T00:00:00Z')"
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _counts(path):
    conn = sqlite3.connect(str(path))
    try:
        return {
            "kp": conn.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()[0],
            "kp_junior": conn.execute(
                "SELECT COUNT(*) FROM knowledge_points WHERE stage='junior'").fetchone()[0],
            "edges": conn.execute("SELECT COUNT(*) FROM kp_edges").fetchone()[0],
            "edges_junior": conn.execute(
                "SELECT COUNT(*) FROM kp_edges WHERE from_kp LIKE 'kp_j%'").fetchone()[0],
            "classes": conn.execute("SELECT COUNT(*) FROM classes").fetchone()[0],
            "students": conn.execute("SELECT COUNT(*) FROM students").fetchone()[0],
            "exams": conn.execute("SELECT COUNT(*) FROM exams").fetchone()[0],
            "exam_questions": conn.execute("SELECT COUNT(*) FROM exam_questions").fetchone()[0],
            "answers": conn.execute("SELECT COUNT(*) FROM student_answers").fetchone()[0],
            "q_kp": conn.execute("SELECT COUNT(*) FROM q_kp").fetchone()[0],
            "batches": conn.execute("SELECT COUNT(*) FROM import_batches").fetchone()[0],
            "kp_fts": conn.execute("SELECT COUNT(*) FROM kp_fts").fetchone()[0],
        }
    finally:
        conn.close()


def test_dry_run_writes_nothing(scope_db, tmp_path):
    before = _counts(scope_db)
    code = run(scope_db, execute=False,
               backup_dir=tmp_path / "backups", report_dir=tmp_path / "reports")
    assert code == 0
    assert _counts(scope_db) == before
    report = (tmp_path / "reports" / "primary_scope_dryrun_report.md").read_text(encoding="utf-8")
    assert "dry-run" in report
    assert "knowledge_points" in report
    data = json.loads(
        (tmp_path / "reports" / "primary_scope_dryrun_report.json").read_text(encoding="utf-8"))
    assert data["counts"]["knowledge_points_junior"] == 2
    assert data["counts"]["student_answers_junior"] == 1
    assert not (tmp_path / "backups").exists()


def test_execute_backs_up_and_removes_junior(scope_db, tmp_path):
    code = run(scope_db, execute=True,
               backup_dir=tmp_path / "backups", report_dir=tmp_path / "reports")
    assert code == 0

    after = _counts(scope_db)
    assert after["kp"] == 2
    assert after["kp_junior"] == 0
    assert after["edges"] == 1          # 只保留小学边
    assert after["edges_junior"] == 0
    assert after["classes"] == 1        # 小学班保留
    assert after["students"] == 1
    assert after["exams"] == 1
    assert after["exam_questions"] == 1
    assert after["answers"] == 1
    assert after["q_kp"] == 1           # 题库挂接保留
    assert after["batches"] == 1        # 导入批次保留作审计
    assert after["kp_fts"] == 2         # 重建为小学行

    backups = list((tmp_path / "backups").glob("*.sqlite"))
    assert len(backups) == 1
    conn = sqlite3.connect(str(backups[0]))
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()[0] == 4
    finally:
        conn.close()

    data = json.loads(
        (tmp_path / "reports" / "primary_scope_execution_report.json").read_text(encoding="utf-8"))
    assert data["deleted"]["knowledge_points"] == 2
    assert data["deleted"]["classes"] == 1
    assert data["verified"]["residual_junior"] == {
        "knowledge_points_junior": 0, "kp_edges_junior": 0, "classes_junior": 0,
        "exams_junior": 0, "students_in_junior_classes": 0,
        "exam_questions_for_junior_exams": 0, "student_answers_for_junior_exams": 0,
        "knowledge_points_total_minus_primary": 0, "kp_fts_minus_primary": 0,
    }
