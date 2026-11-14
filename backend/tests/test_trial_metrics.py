"""trial_metrics primary 消歧准确率指标测试（§14 V2-5）。"""
import json

from backend.scripts.trial_metrics import disambiguation_accuracy, ADJACENT_GROUPS

GOLD_ROW = {"gold_kp_ids": ["kp_p_037", "kp_p_075"], "gold_primary": "kp_p_037"}


def test_same_group_counts_as_disambiguation_hit():
    """相邻族内命中（038 vs 037 同族）计入消歧准确但不计主知识点命中。"""
    row = {**GOLD_ROW, "status": "ok", "llm_primary": "kp_p_038", "llm_kps": ["kp_p_038"]}
    assert disambiguation_accuracy([row]) == 1.0


def test_outside_group_is_miss():
    row = {**GOLD_ROW, "status": "ok", "llm_primary": "kp_p_079", "llm_kps": ["kp_p_079"]}
    assert disambiguation_accuracy([row]) == 0.0


def test_exact_hit_counts():
    row = {**GOLD_ROW, "status": "ok", "llm_primary": "kp_p_037", "llm_kps": ["kp_p_037"]}
    assert disambiguation_accuracy([row]) == 1.0


def test_unmapped_rows_excluded():
    rows = [GOLD_ROW | {"status": "unmapped", "llm_primary": None, "llm_kps": []},
            GOLD_ROW | {"status": "ok", "llm_primary": "kp_p_037", "llm_kps": ["kp_p_037"]}]
    assert disambiguation_accuracy(rows) == 1.0  # unmapped 不计入分母


def test_adjacent_groups_cover_known_pairs():
    pairs = [("kp_p_037", "kp_p_038"), ("kp_p_071", "kp_p_066"), ("kp_p_036", "kp_p_015"),
             ("kp_p_031", "kp_p_082"), ("kp_p_077", "kp_p_079")]
    for a, b in pairs:
        assert any({a, b} <= g for g in ADJACENT_GROUPS)
