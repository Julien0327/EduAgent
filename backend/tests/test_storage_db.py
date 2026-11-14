"""存储层建库测试（Phase 1 / Step 1）：V1 迁移、外键、UNIQUE 兜底、质量状态枚举、FTS5。"""
import sqlite3

import pytest

from backend.app.storage import db


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "edumate_test.sqlite"
    db.apply_migrations(path)
    return path


@pytest.fixture()
def conn(db_path):
    conn = db.connect(db_path)
    yield conn
    conn.close()


def test_all_expected_tables_created(conn):
    present = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = [name for name in db.EXPECTED_TABLES if name not in present]
    assert missing == []


def test_connect_enforces_foreign_keys(conn):
    """每个由 db.connect 创建的连接都必须开启外键约束。"""
    (fk,) = conn.execute("PRAGMA foreign_keys").fetchone()
    assert fk == 1


def test_apply_migrations_idempotent(db_path):
    """重复应用迁移不报错、不重复记录版本。"""
    version_again = db.apply_migrations(db_path)
    assert version_again == 2
    conn = db.connect(db_path)
    try:
        versions = [
            row["version"] for row in conn.execute("SELECT version FROM schema_migrations")
        ]
        assert versions == [1, 2]
    finally:
        conn.close()


def test_foreign_keys_block_orphan_student(conn):
    """students.class_id 悬空引用必须被外键约束拒绝。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO students (student_id, class_id, student_no, name, created_at)"
            " VALUES ('stu_x', 'cls_missing', 'S99', '李四', '2026-01-01')"
        )


def test_unique_student_answers_blocks_duplicate(conn):
    """UNIQUE(exam_id, student_id, exam_q_id) 兜底：同一学生同一题不可重复累计。"""
    exam_q_id = _seed_exam_chain(conn)
    conn.execute(
        "INSERT INTO student_answers (exam_id, student_id, exam_q_id, score)"
        " VALUES ('exam_1', 'stu_1', ?, 5.0)",
        (exam_q_id,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO student_answers (exam_id, student_id, exam_q_id, score)"
            " VALUES ('exam_1', 'stu_1', ?, 3.0)",
            (exam_q_id,),
        )


def test_quality_state_rejects_unknown_value(conn):
    """quality_state 枚举校验：raw 等非法状态不得入库（决策⑲）。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO questions (q_id, stage, type, stem, answer, score, difficulty,"
            " source, quality_state)"
            " VALUES ('q_x', 'primary', 'fill', '1+1=?', '2', 5.0, 3, 'ape210k', 'raw')"
        )


def test_kp_edges_primary_key(conn):
    """kp_edges 复合主键：同一前置边不可重复。"""
    conn.execute("INSERT INTO kp_edges VALUES ('kp_a', 'kp_b', 'prerequisite')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO kp_edges VALUES ('kp_a', 'kp_b', 'prerequisite')")


def test_questions_fts_match(conn):
    """FTS5 建表可用：插入后按词命中（中文分词召回属 Phase 2 验收，此处不测）。"""
    conn.execute(
        "INSERT INTO questions (q_id, stage, type, stem, answer, score, difficulty, source)"
        " VALUES ('q_fts', 'primary', 'fill', 'Solve the linear equation.', 'x = 3',"
        " 5.0, 2, 'ape210k')"
    )
    conn.execute(
        "INSERT INTO questions_fts (q_id, stem, answer)"
        " VALUES ('q_fts', 'Solve the linear equation.', 'x = 3')"
    )
    rows = conn.execute(
        "SELECT q_id FROM questions_fts WHERE questions_fts MATCH 'equation'"
    ).fetchall()
    assert [row["q_id"] for row in rows] == ["q_fts"]


def _seed_exam_chain(conn) -> int:
    """构造 班级→学生→考试→卷面题 最小关联链，返回 exam_questions.id。"""
    conn.execute(
        "INSERT INTO classes (class_id, name, grade, stage, created_at)"
        " VALUES ('cls_1', '初一（1）班', 7, 'junior', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO students (student_id, class_id, student_no, name, created_at)"
        " VALUES ('stu_1', 'cls_1', 'S01', '张三', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO exams (exam_id, title, created_at)"
        " VALUES ('exam_1', '期中考试', '2026-01-01')"
    )
    cur = conn.execute(
        "INSERT INTO exam_questions (exam_id, q_no, score) VALUES ('exam_1', 1, 5.0)"
    )
    return int(cur.lastrowid)
