"""小学前置图冻结与入库测试（Phase 3 / S0-1）。

覆盖：
- 冻结清单形状：v1.0 / 87 条 / 全部为 kp_p_* / 无重复、无自环 / DAG 通过
- 入库：87 条写入、82 个小学 KP、5 个孤立节点、根节点数
- 幂等：重复运行边数不变
- 边界：只删/写 primary 边，不碰初中边；dry-run 不写库
- 反例：未知 ID / 环被拒绝
"""
import json
from pathlib import Path

import pytest

from backend.app.storage import db
from backend.scripts.build_primary_graph import (
    EDGES_FILE,
    PrimaryGraphError,
    build,
    load_edges,
    validate_edges,
)

EXPECTED_ISOLATED = {"kp_p_026", "kp_p_027", "kp_p_030", "kp_p_031", "kp_p_033"}


@pytest.fixture()
def graph_db(tmp_path):
    path = tmp_path / "graph.sqlite"
    db.apply_migrations(path)
    return path


def _seed_kps(path, ids):
    conn = db.connect(path)
    try:
        conn.executemany(
            "INSERT INTO knowledge_points (kp_id, name, stage, source)"
            " VALUES (?, ?, 'primary', 'test')",
            [(kp, kp) for kp in ids],
        )
        conn.commit()
    finally:
        conn.close()


def _write_edges(tmp_path, pairs, status="frozen"):
    payload = {
        "version": "test",
        "status": status,
        "edges": [
            {"source_id": s, "target_id": t, "category": "测试",
             "note": "测试关系", "basis": "测试依据"}
            for s, t in pairs
        ],
    }
    path = tmp_path / "edges.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _frozen_ids(payload):
    ids = {e["source_id"] for e in payload["edges"]}
    ids |= {e["target_id"] for e in payload["edges"]}
    return ids | EXPECTED_ISOLATED


def test_frozen_file_shape_and_dag():
    """冻结文件：87 条、ID 均为小学、无重复/自环、DAG 通过。"""
    payload = load_edges(EDGES_FILE)
    assert payload["status"] == "frozen"
    assert payload["version"] == "v1.0"
    edges = payload["edges"]
    assert len(edges) == 87
    assert all(e["source_id"].startswith("kp_p_") for e in edges)
    assert all(e["target_id"].startswith("kp_p_") for e in edges)
    assert all(e["category"] and e["note"] and e["basis"] for e in edges)
    pairs = [(e["source_id"], e["target_id"]) for e in edges]
    assert len(set(pairs)) == len(pairs)
    assert all(s != t for s, t in pairs)

    ids = _frozen_ids(payload)
    assert len(ids) == 82, f"冻结边应覆盖 82 个小学 KP，实际 {len(ids)}"


def test_frozen_validation_on_seeded_db(graph_db):
    """用 82 个小学 KP 的临时库跑完整校验：0 错误。"""
    payload = load_edges(EDGES_FILE)
    _seed_kps(graph_db, _frozen_ids(payload))
    conn = db.connect(graph_db)
    try:
        errors = validate_edges(conn, payload["edges"])
    finally:
        conn.close()
    assert errors == []


def test_build_writes_87_and_reports_stats(graph_db):
    """入库 87 条：82 KP / 5 孤立 / 8 根节点。"""
    payload = load_edges(EDGES_FILE)
    _seed_kps(graph_db, _frozen_ids(payload))
    summary = build(graph_db)
    assert summary["edges"] == 87
    assert summary["primary_kps"] == 82
    assert set(summary["isolated"]) == EXPECTED_ISOLATED
    assert summary["roots"] == 8
    assert summary["written"] == 87

    conn = db.connect(graph_db)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM kp_edges").fetchone()["n"]
    finally:
        conn.close()
    assert n == 87


def test_build_is_idempotent(graph_db):
    """重复运行结果一致（先删 primary 边再全量插入）。"""
    payload = load_edges(EDGES_FILE)
    _seed_kps(graph_db, _frozen_ids(payload))
    build(graph_db)
    build(graph_db)
    conn = db.connect(graph_db)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM kp_edges").fetchone()["n"]
    finally:
        conn.close()
    assert n == 87


def test_build_dry_run_writes_nothing(graph_db):
    payload = load_edges(EDGES_FILE)
    _seed_kps(graph_db, _frozen_ids(payload))
    summary = build(graph_db, dry_run=True)
    assert summary["written"] == 0
    conn = db.connect(graph_db)
    try:
        n = conn.execute("SELECT COUNT(*) AS n FROM kp_edges").fetchone()["n"]
    finally:
        conn.close()
    assert n == 0


def test_build_does_not_touch_junior_edges(graph_db):
    """只管理 primary 边：预置的初中边在构建后仍存在。"""
    payload = load_edges(EDGES_FILE)
    _seed_kps(graph_db, _frozen_ids(payload))
    conn = db.connect(graph_db)
    try:
        conn.executemany(
            "INSERT INTO knowledge_points (kp_id, name, stage, source)"
            " VALUES (?, ?, 'junior', 'test')",
            [("kp_j1", "初中1"), ("kp_j2", "初中2")],
        )
        conn.execute(
            "INSERT INTO kp_edges (from_kp, to_kp, relation)"
            " VALUES ('kp_j1', 'kp_j2', 'prerequisite')"
        )
        conn.commit()
    finally:
        conn.close()

    build(graph_db)
    conn = db.connect(graph_db)
    try:
        junior = conn.execute(
            "SELECT COUNT(*) AS n FROM kp_edges WHERE from_kp LIKE 'kp_j%'"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert junior == 1


def test_build_rejects_unknown_id(tmp_path, graph_db):
    _seed_kps(graph_db, ["kp_p_001"])
    edges_file = _write_edges(tmp_path, [("kp_p_001", "kp_p_999")])
    with pytest.raises(PrimaryGraphError, match="E_REF"):
        build(graph_db, edges_file)


def test_build_rejects_cycle(tmp_path, graph_db):
    _seed_kps(graph_db, ["kp_p_001", "kp_p_002"])
    edges_file = _write_edges(
        tmp_path, [("kp_p_001", "kp_p_002"), ("kp_p_002", "kp_p_001")]
    )
    with pytest.raises(PrimaryGraphError, match="E_CYCLE"):
        build(graph_db, edges_file)
