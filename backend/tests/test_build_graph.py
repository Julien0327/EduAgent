"""图谱构建校验器测试（Phase 1 / Step 2）：引用完整 / DAG 无环 / 孤岛 / 主干链 / 根节点元数据。"""
import pytest

from backend.scripts.build_graph import (
    GraphValidationError,
    TRUNK_ANCHOR_EDGES,
    load_source_files,
    validate_graph,
)


def _node(kp_id: str, prerequisites=(), chapter="人教版七上·第1章", description="测试节点"):
    return {
        "kp_id": kp_id,
        "name": kp_id,
        "stage": "junior",
        "grade": 7,
        "chapter": chapter,
        "difficulty": 2,
        "description": description,
        "source": "curriculum",
        "prerequisites": list(prerequisites),
    }


def _make_nodes(*specs):
    """specs: (kp_id, prerequisites) 列表 → {kp_id: node}。"""
    return {kp: _node(kp, pre) for kp, pre in specs}


def test_validate_pass_on_wellformed_graph(monkeypatch):
    """良构图（锚点边由 monkeypatch 注入）校验通过、零错误。"""
    monkeypatch.setattr(
        "backend.scripts.build_graph.TRUNK_ANCHOR_EDGES", (("a", "b"),)
    )
    nodes = _make_nodes(("a", ()), ("b", ("a",)), ("c", ("b",)))
    assert validate_graph(nodes, [("a", "b"), ("b", "c")]) == []


def test_reference_to_missing_kp_rejected():
    nodes = _make_nodes(("a", ()), ("b", ("ghost",)))
    errors = validate_graph(nodes, [("ghost", "b")])
    assert any("E_REF" in e for e in errors)


def test_cycle_rejected():
    nodes = _make_nodes(("a", ("c",)), ("b", ("a",)), ("c", ("b",)))
    edges = [("c", "a"), ("a", "b"), ("b", "c")]
    errors = validate_graph(nodes, edges)
    assert any("E_CYCLE" in e for e in errors)


def test_root_node_requires_chapter_and_description():
    nodes = _make_nodes(("a", ()), ("b", ("a",)))
    nodes["a"]["chapter"] = ""
    nodes["a"]["description"] = ""
    errors = validate_graph(nodes, [("a", "b")])
    assert any("E_META" in e and "chapter" in e for e in errors)
    assert any("E_META" in e and "description" in e for e in errors)


def test_isolated_non_root_node_rejected():
    nodes = _make_nodes(("a", ()), ("lonely", ()), ("b", ("a",)))
    edges = [("a", "b")]
    errors = validate_graph(nodes, edges)
    assert any("E_ISLAND" in e and "lonely" in e for e in errors)


def test_missing_trunk_anchor_rejected():
    nodes = _make_nodes(("x", ()), ("y", ("x",)))
    errors = validate_graph(nodes, [("x", "y")])
    assert any("E_TRUNK" in e for e in errors)


def test_trunk_anchors_form_chains():
    """锚点对首尾相接成链，覆盖代数主线与几何推理链。"""
    tails = {src for src, _ in TRUNK_ANCHOR_EDGES}
    heads = {dst for _, dst in TRUNK_ANCHOR_EDGES}
    # 除链首外的锚点都应被前一条边指向（除两条主线的共同起点与分叉外全部衔接）
    chain_starts = {"kp_j7_0101", "kp_j7_0401"}
    assert tails - heads == chain_starts


def test_build_rejects_invalid_graph(tmp_path):
    """build() 在校验失败时整体拒绝，不写入任何数据。"""
    from backend.app.storage import db

    db_path = tmp_path / "t.sqlite"
    db.apply_migrations(db_path)
    bad_dir = tmp_path / "src"
    bad_dir.mkdir()
    (bad_dir / "m.json").write_text(
        '{"knowledge_points": [{"kp_id": "a", "name": "a", "stage": "junior",'
        ' "grade": 7, "chapter": "c", "difficulty": 2, "description": "d",'
        ' "source": "curriculum", "prerequisites": ["ghost"]}]}',
        encoding="utf-8",
    )
    with pytest.raises(GraphValidationError):
        import backend.scripts.build_graph as bg

        bg.build(db_path, bad_dir)
    conn = db.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS c FROM knowledge_points").fetchone()["c"] == 0
    finally:
        conn.close()
