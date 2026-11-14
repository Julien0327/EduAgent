"""标注层 Schema V2 测试（Step 5-B1）：迁移、约束、外键、消费视图行为。

核心断言（设计文档 §5 第二版）：
- confidence 0–1 约束 / difficulty 1–5 约束 / 批次 UNIQUE 幂等键
- 消费视图：不可映射（unmapped）永不回退规则；A/B 臂隔离；rejected 排除；
  有效批次 = 每 strategy 最新 completed。
"""
import sqlite3

import pytest

from backend.app.storage import db

NOW = "2026-11-14T00:00:00Z"


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


def _seed_kp(conn, kp_id="kp_p_001", name="和差问题"):
    conn.execute(
        "INSERT INTO knowledge_points (kp_id, name, subject, stage, source, description)"
        " VALUES (?, ?, 'math', 'primary', 'ape210k_rule', '描述')",
        (kp_id, name))


def _seed_question(conn, q_id="q_ape_000001", difficulty=3):
    conn.execute(
        "INSERT INTO questions (q_id, stage, subject, type, stem, answer, score,"
        " difficulty, source) VALUES (?, 'primary', 'math', 'application',"
        " '测试题干', '42', 1.0, ?, 'ape210k')", (q_id, difficulty))


def _seed_rule_link(conn, q_id="q_ape_000001", kp_id="kp_p_001"):
    conn.execute("INSERT INTO q_kp (q_id, kp_id) VALUES (?, ?)", (q_id, kp_id))


def _seed_batch(conn, batch_id="ann_test_a", status="completed",
                strategy="A_full_list", finished="2026-11-14T10:00:00Z",
                prompt_version="v1.0"):
    conn.execute(
        "INSERT INTO annotation_batches (batch_id, task_type, scope, strategy,"
        " provider, model, prompt_id, prompt_version, prompt_sha256, status,"
        " finished_at, created_at)"
        " VALUES (?, 'kp_difficulty', 'ape_primary', ?, 'deepseek', 'deepseek-flash',"
        " ?, ?, ?, ?, ?, ?)",
        (batch_id, strategy,
         "kp_primary" if strategy == "A_full_list" else "kp_recall",
         prompt_version, "a" * 64, status, finished, NOW))
    return batch_id


def _seed_result(conn, batch_id, q_id, status, primary=None):
    conn.execute(
        "INSERT INTO q_annotation_results (q_id, batch_id, status, primary_kp_id,"
        " reason, created_at) VALUES (?, ?, ?, ?, '理由', ?)",
        (q_id, batch_id, status, primary, NOW))


def _seed_kp_llm(conn, batch_id, q_id, kp_id, is_primary=1, review="pending"):
    conn.execute(
        "INSERT INTO q_kp_llm (q_id, kp_id, batch_id, is_primary, confidence,"
        " review_state, created_at) VALUES (?, ?, ?, ?, 0.9, ?, ?)",
        (q_id, kp_id, batch_id, is_primary, review, NOW))


def _view_rows(conn, q_id):
    return conn.execute(
        "SELECT tag_source, kp_id, review_state FROM v_q_kp_effective WHERE q_id=?"
        " ORDER BY tag_source, kp_id", (q_id,)).fetchall()


# ---------------------------------------------------------------- 迁移与约束

def test_v2_tables_and_views_exist(conn):
    objects = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    for name in ("annotation_batches", "q_annotation_results", "q_kp_llm",
                 "q_difficulty_llm", "annotation_reviews",
                 "v_effective_batch", "v_q_kp_effective"):
        assert name in objects


def test_confidence_range_check(conn):
    _seed_kp(conn)
    _seed_batch(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO q_kp_llm (q_id, kp_id, batch_id, is_primary, confidence,"
            " review_state, created_at) VALUES ('q_x', 'kp_p_001', 'ann_test_a', 0, 1.5,"
            " 'pending', ?)", (NOW,))
    conn.execute(
        "INSERT INTO q_kp_llm (q_id, kp_id, batch_id, is_primary, confidence,"
        " review_state, created_at) VALUES ('q_x', 'kp_p_001', 'ann_test_a', 0, NULL,"
        " 'pending', ?)", (NOW,))
    conn.commit()


def test_difficulty_range_check(conn):
    _seed_batch(conn)
    for bad in (0, 6):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO q_difficulty_llm (q_id, batch_id, difficulty, created_at)"
                " VALUES ('q_x', 'ann_test_a', ?, ?)", (bad, NOW))


def test_results_status_check(conn):
    _seed_batch(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO q_annotation_results (q_id, batch_id, status, created_at)"
            " VALUES ('q_x', 'ann_test_a', 'pending', ?)", (NOW,))


def test_fk_batch_and_kp_enforced(conn):
    _seed_kp(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO q_kp_llm (q_id, kp_id, batch_id, created_at)"
            " VALUES ('q_x', 'kp_p_001', 'ann_missing', ?)", (NOW,))
    _seed_batch(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO q_kp_llm (q_id, kp_id, batch_id, created_at)"
            " VALUES ('q_x', 'kp_p_999', 'ann_test_a', ?)", (NOW,))


def test_results_pk_and_batch_unique(conn):
    _seed_batch(conn)
    _seed_result(conn, "ann_test_a", "q_x", "ok")
    with pytest.raises(sqlite3.IntegrityError):
        _seed_result(conn, "ann_test_a", "q_x", "unmapped")
    with pytest.raises(sqlite3.IntegrityError):  # UNIQUE(scope,task_type,strategy,model,prompt_id,version)
        _seed_batch(conn, batch_id="ann_test_b")


def test_reviews_fk_and_field_check(conn):
    _seed_batch(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO annotation_reviews (q_id, batch_id, field, action, created_at)"
            " VALUES ('q_x', 'ann_missing', 'kp', 'approve', ?)", (NOW,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO annotation_reviews (q_id, batch_id, field, action, created_at)"
            " VALUES ('q_x', 'ann_test_a', 'type', 'approve', ?)", (NOW,))
    conn.execute(
        "INSERT INTO annotation_reviews (q_id, batch_id, field, target_kp_id, old_value,"
        " action, created_at) VALUES ('q_x', 'ann_test_a', 'kp', 'kp_p_001', 'kp_p_001',"
        " 'approve', ?)", (NOW,))
    conn.commit()


# ---------------------------------------------------------------- 有效批次选择

def test_effective_batch_latest_completed(conn):
    _seed_batch(conn, "ann_old_a", finished="2026-11-14T08:00:00Z", prompt_version="v1.0")
    _seed_batch(conn, "ann_new_a", finished="2026-11-14T10:00:00Z", prompt_version="v1.1")
    _seed_batch(conn, "ann_aborted_a", status="aborted", finished="2026-11-14T11:00:00Z",
                prompt_version="v1.2")
    rows = conn.execute("SELECT batch_id FROM v_effective_batch").fetchall()
    assert [r["batch_id"] for r in rows] == ["ann_new_a"]


# ---------------------------------------------------------------- 消费视图行为

def test_view_rule_fallback_when_no_llm(conn):
    _seed_kp(conn)
    _seed_question(conn)
    _seed_rule_link(conn)
    _seed_batch(conn)
    rows = _view_rows(conn, "q_ape_000001")
    assert [(r["tag_source"], r["kp_id"]) for r in rows] == [("rule", "kp_p_001")]


def test_view_llm_suppresses_rule(conn):
    _seed_kp(conn, "kp_p_001")
    _seed_kp(conn, "kp_p_002")
    _seed_question(conn)
    _seed_rule_link(conn)
    _seed_batch(conn)
    _seed_result(conn, "ann_test_a", "q_ape_000001", "ok", primary="kp_p_001")
    _seed_kp_llm(conn, "ann_test_a", "q_ape_000001", "kp_p_001", is_primary=1)
    _seed_kp_llm(conn, "ann_test_a", "q_ape_000001", "kp_p_002", is_primary=0)
    rows = _view_rows(conn, "q_ape_000001")
    assert [(r["tag_source"], r["kp_id"], r["review_state"]) for r in rows] == [
        ("llm", "kp_p_001", "pending"), ("llm", "kp_p_002", "pending")]


def test_view_unmapped_never_falls_back_to_rule(conn):
    """核心断言（第二版修订）：不可映射是有效标注结果，绝不回退规则标签。"""
    _seed_kp(conn)
    _seed_question(conn)
    _seed_rule_link(conn)
    _seed_batch(conn)
    _seed_result(conn, "ann_test_a", "q_ape_000001", "unmapped")
    rows = _view_rows(conn, "q_ape_000001")
    assert len(rows) == 1
    assert rows[0]["tag_source"] == "llm_unmapped"
    assert rows[0]["kp_id"] is None


def test_view_rejected_excluded_and_corrected_visible(conn):
    _seed_kp(conn, "kp_p_001")
    _seed_kp(conn, "kp_p_002")
    _seed_question(conn)
    _seed_batch(conn)
    _seed_result(conn, "ann_test_a", "q_ape_000001", "ok", primary="kp_p_001")
    _seed_kp_llm(conn, "ann_test_a", "q_ape_000001", "kp_p_001", is_primary=1,
                 review="corrected")
    _seed_kp_llm(conn, "ann_test_a", "q_ape_000001", "kp_p_002", is_primary=0,
                 review="rejected")
    rows = _view_rows(conn, "q_ape_000001")
    assert [(r["kp_id"], r["review_state"]) for r in rows] == [("kp_p_001", "corrected")]


def test_view_strategy_isolation_b_arm_not_merged(conn):
    """B 臂结果不得混入消费视图（默认只消费 A 主线臂）。"""
    _seed_kp(conn)
    _seed_question(conn)
    _seed_batch(conn, "ann_a", strategy="A_full_list")
    _seed_batch(conn, "ann_b", strategy="B_recall")
    for batch in ("ann_a", "ann_b"):
        _seed_result(conn, batch, "q_ape_000001", "ok", primary="kp_p_001")
        _seed_kp_llm(conn, batch, "q_ape_000001", "kp_p_001", is_primary=1)
    rows = _view_rows(conn, "q_ape_000001")
    assert len(rows) == 1
    assert rows[0]["tag_source"] == "llm"
    assert conn.execute("SELECT batch_id FROM v_effective_batch WHERE strategy='B_recall'"
                        ).fetchone() is not None  # B 臂有效批次存在（供对比查询）
