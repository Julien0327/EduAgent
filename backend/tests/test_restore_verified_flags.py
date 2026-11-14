"""verified 标记重放脚本测试（Phase 2 / S1）。

背景：ETL 全量重建会冲掉 verified 标记（2026-11-14 S1 实测），重放脚本
restore_verified_flags.py 是幂等命令链固定第三步。本测试覆盖：confirmed 置 1 /
pending 不标记 / 库外 q_id fail-fast / 重复执行幂等。
"""
import json

import pytest

from backend.app.storage import db
from backend.scripts import restore_verified_flags


@pytest.fixture()
def mini_db(tmp_path):
    """最小业务库：迁移 + 两道 ape 题（模拟 49 confirmed + 1 pending 的形态）。"""
    db_path = tmp_path / "mini.sqlite"
    db.apply_migrations(db_path)
    conn = db.connect(db_path)
    with conn:
        conn.executemany(
            """INSERT INTO questions
                 (q_id, stage, subject, type, stem, answer, score, difficulty,
                  source, quality_state, verified, created_at)
               VALUES (?, 'primary', 'math', 'application', ?, '8', 1, 3,
                       'ape210k', 'processed', 0, '2026-11-14T00:00:00')""",
            [("q_ape_100", "小明有5个苹果，又买了3个，一共多少个？"),
             ("q_ape_200", "绳子对折后长几米？")],
        )
    conn.close()
    return db_path


def _write_gold(tmp_path, rows) -> object:
    p = tmp_path / "gold_v2.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return p


def test_confirmed_marked_pending_not_marked(mini_db, tmp_path):
    gold = _write_gold(tmp_path, [
        {"q_id": "q_ape_100", "status": "confirmed"},
        {"q_id": "q_ape_200", "status": "pending_verification"},
    ])
    report = restore_verified_flags.run(mini_db, gold)
    assert report["restored"] == 1 and report["verified_total"] == 1
    assert report["pending_not_marked"] == ["q_ape_200"]

    conn = db.connect(mini_db)
    try:
        flags = dict(conn.execute(
            "SELECT q_id, verified FROM questions").fetchall())
        assert flags == {"q_ape_100": 1, "q_ape_200": 0}
        # verified_at 已落
        at = conn.execute(
            "SELECT verified_at FROM questions WHERE q_id='q_ape_100'").fetchone()["verified_at"]
        assert at
    finally:
        conn.close()


def test_missing_qid_fails_fast(mini_db, tmp_path):
    """gold confirmed 题不在库中：报错中断，不静默漏标。"""
    gold = _write_gold(tmp_path, [{"q_id": "q_ape_999", "status": "confirmed"}])
    with pytest.raises(ValueError, match="不存在"):
        restore_verified_flags.run(mini_db, gold)


def test_rerun_idempotent(mini_db, tmp_path):
    """重复执行结果一致（幂等链第三步可安全重跑）。"""
    gold = _write_gold(tmp_path, [{"q_id": "q_ape_100", "status": "confirmed"}])
    r1 = restore_verified_flags.run(mini_db, gold)
    r2 = restore_verified_flags.run(mini_db, gold)
    assert r1["verified_total"] == r2["verified_total"] == 1


def test_empty_gold_fails_fast(mini_db, tmp_path):
    gold = _write_gold(tmp_path, [])
    with pytest.raises(ValueError, match="confirmed"):
        restore_verified_flags.run(mini_db, gold)
