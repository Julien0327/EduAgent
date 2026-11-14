"""核验指南一致性检查测试（Step 5-C 准备）：捕获重复/越界/悬空 KP。"""
import pytest

from backend.scripts.build_gold_v2_review_guide import REVIEW_SIZE, consistency_check

KP_DEFS = {"kp_p_001": {"kp_id": "kp_p_001", "name": "甲", "description": ""},
           "kp_p_002": {"kp_id": "kp_p_002", "name": "乙", "description": ""}}
FROZEN = {f"q_{i}": "S12" for i in range(100)}


def make_row(qid, kps=("kp_p_001",), primary="kp_p_001"):
    gold = {"gold_kp_ids": list(kps), "gold_primary": primary, "gold_difficulty": 2}
    return {"q_id": qid, "gold_v1": gold, "gold_v2": gold,
            "A": {"kp_ids": list(kps), "view": "ok"}, "B": {"kp_ids": list(kps), "view": "ok"}}


def test_valid_pack_passes():
    rows = [make_row(f"q_{i}") for i in range(REVIEW_SIZE)]
    assert consistency_check(rows, FROZEN, KP_DEFS) == []


def test_duplicate_qid_detected():
    rows = [make_row("q_0") for _ in range(REVIEW_SIZE)]
    errors = consistency_check(rows, FROZEN, KP_DEFS)
    assert any("重复" in e for e in errors)


def test_qid_outside_frozen_set_detected():
    rows = [make_row(f"q_{i}") for i in range(REVIEW_SIZE - 1)] + [make_row("q_not_frozen")]
    errors = consistency_check(rows, FROZEN, KP_DEFS)
    assert any("冻结抽样集" in e for e in errors)


def test_dangling_kp_detected():
    rows = [make_row(f"q_{i}", kps=("kp_p_001", "kp_p_999"), primary="kp_p_001")
            for i in range(REVIEW_SIZE)]
    errors = consistency_check(rows, FROZEN, KP_DEFS)
    assert any("kp_p_999" in e for e in errors)


def test_wrong_size_detected():
    rows = [make_row(f"q_{i}") for i in range(REVIEW_SIZE - 1)]
    errors = consistency_check(rows, FROZEN, KP_DEFS)
    assert any(f"题数" in e for e in errors)
