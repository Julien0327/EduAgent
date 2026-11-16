"""归因算法测试（Phase 3 / S2）。

覆盖：正常归因、多跳、多分支冲突、前置证据不足阻断、孤立节点、环检测、
置信度分级、状态与置信度分离、证据链字段完整。
"""
import pytest

from backend.app.diagnosis.attribution import (
    HIGH,
    LOW,
    MID,
    _attribute_one,
    attribute,
    build_prereq_graph,
    detect_cycles,
)
from backend.app.diagnosis.mastery import (
    INSUFFICIENT,
    NO_COVERAGE,
    NORMAL,
    WEAK,
)


def _m(state, mastery, sample, aux_share=0.0):
    return {
        "kp_id": "x", "state": state, "mastery": mastery,
        "primary_only_mastery": mastery, "aux_share": aux_share,
        "sample_count": sample, "primary_sample_count": sample,
        "aux_sample_count": 0, "weighted_evidence_count": sample,
        "weighted_primary_evidence_count": sample, "weighted_aux_evidence_count": 0,
        "q_ids": [f"q{i}" for i in range(sample)],
    }


def _make_mastery(entries: dict[str, tuple]):
    return {kp: _m(*v) for kp, v in entries.items()}


def test_direct_prereq_failing_root():
    """A→B，B weak，A 充分且 <0.7 → 根因 A。"""
    prereqs = {"B": ["A"]}
    mastery = _make_mastery({
        "A": (WEAK, 0.55, 4),
        "B": (WEAK, 0.50, 4),
    })
    chain = _attribute_one("B", mastery, prereqs)
    assert chain["root_kp"] == "A"
    assert chain["path"] == ["A", "B"]
    assert chain["conflict"] is False


def test_prereq_all_healthy_self_root():
    """前置 ≥0.7 → 目标自身为根因。"""
    prereqs = {"B": ["A"]}
    mastery = _make_mastery({
        "A": (NORMAL, 0.85, 5),
        "B": (WEAK, 0.50, 5),
    })
    chain = _attribute_one("B", mastery, prereqs)
    assert chain["root_kp"] == "B"
    assert chain["path"] == ["B"]


def test_two_hop_deepest_root():
    """A→C→B 链：A、C 都 <0.7 → 最深 A 为根因。"""
    prereqs = {"B": ["C"], "C": ["A"]}
    mastery = _make_mastery({
        "A": (WEAK, 0.45, 5),
        "C": (WEAK, 0.50, 5),
        "B": (WEAK, 0.40, 5),
    })
    chain = _attribute_one("B", mastery, prereqs)
    assert chain["root_kp"] == "A"
    assert chain["path"] == ["A", "C", "B"]


def test_multi_branch_conflict_kept():
    """B 有两个前置 A1、A2 都失败且同深度 → 冲突保留，root 取掌握度低者。"""
    prereqs = {"B": ["A1", "A2"]}
    mastery = _make_mastery({
        "A1": (WEAK, 0.60, 5),
        "A2": (WEAK, 0.55, 5),
        "B": (WEAK, 0.50, 5),
    })
    chain = _attribute_one("B", mastery, prereqs)
    assert chain["root_kp"] == "A2"  # 掌握度更低
    assert chain["conflict"] is True
    assert {c["kp_id"] for c in chain["root_candidates"]} == {"A1", "A2"}
    assert chain["confidence"] == LOW  # 分支冲突 → low


def test_prereq_insufficient_blocked_low():
    """前置证据不足 → 阻断、不猜测、root 回到目标、low。"""
    prereqs = {"B": ["A"]}
    mastery = _make_mastery({
        "A": (INSUFFICIENT, 0.30, 2),
        "B": (WEAK, 0.50, 5),
    })
    chain = _attribute_one("B", mastery, prereqs)
    assert chain["blocked_prereqs"] == ["A"]
    assert chain["root_kp"] == "B"
    assert chain["confidence"] == LOW


def test_no_coverage_prereq_blocked():
    prereqs = {"B": ["A"]}
    mastery = _make_mastery({
        "A": (NO_COVERAGE, None, 0),
        "B": (WEAK, 0.50, 5),
    })
    chain = _attribute_one("B", mastery, prereqs)
    assert "A" in chain["blocked_prereqs"]
    assert chain["confidence"] == LOW


def test_isolated_node_self_root():
    """无前置的弱节点 → 根因自身，无冲突无阻断。"""
    prereqs = {}
    mastery = _make_mastery({"X": (WEAK, 0.55, 5)})
    chain = _attribute_one("X", mastery, prereqs)
    assert chain["root_kp"] == "X"
    assert chain["path"] == ["X"]
    assert chain["blocked_prereqs"] == []
    assert chain["confidence"] == HIGH  # 样本5、无冲突、aux=0


def test_aux_dominant_lowers_confidence():
    """aux_share>0.5 → low（辅助证据主导），即使样本充足。"""
    prereqs = {}
    mastery = _make_mastery({"X": (WEAK, 0.55, 6)})
    mastery["X"]["aux_share"] = 0.6
    chain = _attribute_one("X", mastery, prereqs)
    assert chain["confidence"] == LOW


def test_confidence_gradation():
    """样本 5→high；4→mid；3→low（临界）。"""
    prereqs = {}
    for sample, want in [(5, HIGH), (4, MID), (3, LOW)]:
        mastery = _make_mastery({"X": (WEAK, 0.55, sample)})
        assert _attribute_one("X", mastery, prereqs)["confidence"] == want


def test_state_and_confidence_separate():
    """置信度不改状态：weak 目标即使 high，状态仍 weak；insufficient 不出链。"""
    prereqs = {}
    mastery = _make_mastery({"X": (WEAK, 0.55, 5), "Y": (INSUFFICIENT, 0.40, 2)})
    chains = attribute(mastery, prereqs)
    assert [c["target_kp"] for c in chains] == ["X"]  # Y 不产生链
    assert mastery["Y"]["state"] == INSUFFICIENT


def test_cycle_detection_and_guard():
    """显式检测环；归因对环有 visited 保护不无限递归。"""
    edges = [{"from_kp": "A", "to_kp": "B"}, {"from_kp": "B", "to_kp": "A"}]
    assert detect_cycles(edges) != []
    prereqs = build_prereq_graph(edges)
    # 环上归因：visited 保护，不抛异常、不无限递归
    mastery = _make_mastery({"A": (WEAK, 0.55, 5), "B": (WEAK, 0.50, 5)})
    chain = _attribute_one("B", mastery, prereqs)
    assert chain["root_kp"] in ("A", "B")


def test_output_fields_complete():
    prereqs = {"B": ["A"]}
    mastery = _make_mastery({"A": (WEAK, 0.55, 5), "B": (WEAK, 0.50, 5)})
    names = {"A": "前置A", "B": "目标B"}
    chain = _attribute_one("B", mastery, prereqs)
    for field in ("target_kp", "root_kp", "path", "root_candidates", "conflict",
                  "blocked_prereqs", "prereq_status", "confidence", "min_samples",
                  "target_mastery", "target_aux_share"):
        assert field in chain
    # prereq_status 覆盖目标与前置
    assert set(chain["prereq_status"]) == {"A", "B"}
