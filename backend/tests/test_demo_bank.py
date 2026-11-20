"""合成演示题库单测（Phase 6 / S2）。

覆盖：模板 41 KP × 5 难度、算式求值、规则校验、查重、抽检样本、可复现性。
不依赖业务库、不调用真实 LLM。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.demo.business import (
    build_design,
    load_bank,
    score_summary,
    validate_design,
)
from backend.app.demo.generation import normalize_question_text, split_kps
from backend.app.demo.models import DemoQuestion
from backend.app.demo.reporting import coverage
from backend.app.demo.templates import (
    TEMPLATE_GENERATORS,
    generate_template_questions,
    template_kp_names,
)
from backend.app.demo.validation import (
    mark_duplicates,
    normalize_choice_options,
    normalize_stem,
    reclassify_review_issues,
    rule_validate,
    safe_eval,
    sample_for_human_review,
    validate_all,
)


def _kp(name: str, idx: int) -> dict:
    return {"kp_id": f"kp_p_{idx:03d}", "name": name, "description": "测试"}


def test_template_registry_covers_41_kps():
    assert len(TEMPLATE_GENERATORS) == 41
    assert len(template_kp_names()) == 41


def test_every_template_kp_generates_five_valid_questions():
    all_questions = []
    for idx, name in enumerate(template_kp_names(), start=42):
        kp = _kp(name, idx)
        questions = generate_template_questions(name, kp["kp_id"], 20261120)
        assert len(questions) == 5
        assert sorted(q.difficulty for q in questions) == [1, 2, 3, 4, 5]
        all_questions.extend(questions)
    stats = validate_all(all_questions)
    assert stats["total"] == 205
    assert stats["passed"] == 205, [
        (q.q_id, q.validation_issues) for q in all_questions if q.validation_status != "passed"
    ]
    assert stats["duplicates"] == 0


def test_split_kps_is_41_template_and_41_llm():
    kps = [_kp(name, i) for i, name in enumerate(template_kp_names(), start=1)]
    kps += [_kp(f"应用题{i}", 100 + i) for i in range(41)]
    template, llm = split_kps(kps)
    assert len(template) == 41
    assert len(llm) == 41


def test_safe_eval_accepts_math_and_rejects_code():
    assert safe_eval("2*(3+4)") == 14
    assert safe_eval("3.14*2*2") == pytest.approx(12.56)
    assert safe_eval("3 × 4 ÷ 2") == pytest.approx(6)
    with pytest.raises(ValueError):
        safe_eval("__import__('os').system('ls')")


def test_rule_validate_catches_obvious_defects():
    bad = DemoQuestion(
        q_id="q_bad_1", kp_id="kp_p_001", kp_name="和差问题", difficulty=2,
        qtype="解答题", stem="略。", answer="", solution="",
        generation_method="llm", answer_value=10, equation="2+3",
    )
    issues = rule_validate(bad)
    assert any("占位" in x for x in issues)
    assert any("答案缺失" in x for x in issues)
    assert any("算式结果" in x for x in issues)

    bad_options = DemoQuestion(
        q_id="q_bad_2", kp_id="kp_p_001", kp_name="和差问题", difficulty=2,
        qtype="选择题", stem="下面哪个数是偶数？", answer="D",
        solution="2 是偶数。", options=["A. 1", "B. 3"], generation_method="template",
    )
    assert any("不在选项中" in x for x in rule_validate(bad_options))


def test_duplicate_detection_exact_and_near():
    a = DemoQuestion(
        q_id="q1", kp_id="kp_p_001", kp_name="和差问题", difficulty=1,
        qtype="解答题", stem="甲、乙两数的和是 10，差是 2，求两数。",
        answer="6 和 4", solution="略", generation_method="template",
    )
    b = DemoQuestion(
        q_id="q2", kp_id="kp_p_001", kp_name="和差问题", difficulty=2,
        qtype="解答题", stem="甲、乙两数的和是 10，差是 2，求两数。",
        answer="6 和 4", solution="略", generation_method="template",
    )
    mark_duplicates([a, b])
    assert b.duplicate_of == "q1"
    assert normalize_stem("（3+4）×5") != normalize_stem("3+4×5")


def test_reproducible_with_same_seed():
    q1 = generate_template_questions("圆的面积", "kp_p_050", 20261120)
    q2 = generate_template_questions("圆的面积", "kp_p_050", 20261120)
    assert [q.to_dict() for q in q1] == [q.to_dict() for q in q2]
    assert json.dumps([q.to_dict() for q in q1], ensure_ascii=False) == \
        json.dumps([q.to_dict() for q in q2], ensure_ascii=False)


def test_human_review_sample_size_and_priority():
    questions = []
    for idx, name in enumerate(template_kp_names(), start=42):
        questions.extend(generate_template_questions(name, f"kp_p_{idx:03d}", 20261120))
    validate_all(questions)
    sample = sample_for_human_review(questions, count=41)
    assert len(sample) == 41
    assert len({q.q_id for q in sample}) == 41


def test_choice_options_are_normalized_with_letters():
    q = DemoQuestion(
        q_id="q_choice", kp_id="kp_p_001", kp_name="和差问题", difficulty=2,
        qtype="选择题", stem="甲、乙两数的和是 30，甲比乙多 6。甲数是多少？",
        answer="B", solution="甲=(30+6)÷2=18。",
        options=["12", "18", "24", "30"], generation_method="llm",
    )
    changed = normalize_choice_options(q)
    assert changed is True
    assert q.options[0].startswith("A.")
    assert q.answer == "B"
    assert not rule_validate(q)


def test_normalize_text_replaces_gbk_unfriendly_chars():
    q = DemoQuestion(
        q_id="q_text", kp_id="kp_p_050", kp_name="圆的面积", difficulty=2,
        qtype="解答题", stem="圆的半径是 3 厘米，面积是多少？",
        answer="28.26 平方厘米", solution="3.14×3²=28.26；3.14−0=3.14。",
        generation_method="template",
    )
    assert normalize_question_text(q) is True
    assert "²" not in q.solution
    assert "−" not in q.solution
    assert q.solution == "3.14×3^2=28.26；3.14-0=3.14。"


def test_reclassify_splits_positive_notes_from_hard_flags():
    soft = DemoQuestion(
        q_id="q_soft", kp_id="kp_p_001", kp_name="和差问题", difficulty=1,
        qtype="解答题", stem="甲、乙两数的和是 30，甲比乙多 6。甲数是多少？",
        answer="18", solution="(30+6)÷2=18。", generation_method="llm",
        validation_status="needs_review",
        validation_issues=["LLM：条件完整，答案唯一。"],
    )
    hard = DemoQuestion(
        q_id="q_hard", kp_id="kp_p_001", kp_name="和差问题", difficulty=2,
        qtype="解答题", stem="甲、乙两数的和是 30，甲比乙多 6。甲数是多少？",
        answer="17", solution="错误。", generation_method="llm",
        validation_status="needs_review",
        validation_issues=["LLM：答案不唯一或有歧义", "LLM：答案错误。"],
    )
    stats = reclassify_review_issues([soft, hard])
    assert stats == {"hard_kept": 1, "soft_cleared": 1}
    assert soft.validation_status == "passed"
    assert soft.review_notes == ["LLM：条件完整，答案唯一。"]
    assert hard.validation_status == "needs_review"
    assert hard.validation_issues == ["LLM：答案不唯一或有歧义"]


def test_business_design_is_complete_and_varied():
    bank = load_bank(Path("data/demo/demo_bank_v1.jsonl"))
    kps = {q["kp_id"]: q["kp_name"] for q in bank.values()}
    design = build_design(bank, kps, {}, seed=20261120)
    assert validate_design(design, bank, kps) == []
    assert len(design["students"]) == 20
    assert len(design["answers"]) == 800
    assert len(design["role_map"]) == 40
    summary = score_summary(design)
    assert summary["by_exam"]["A"]["std"] > 5
    assert summary["by_exam"]["B"]["std"] > 5


def test_coverage_detects_missing_kp():
    kps = [_kp("圆的面积", 50), _kp("圆的周长", 49)]
    questions = generate_template_questions("圆的面积", "kp_p_050", 20261120)
    validate_all(questions)
    data = coverage(kps, questions)
    assert data["kps_total"] == 2
    assert data["kps_covered"] == 1
    assert data["coverage_rate"] == 0.5
    assert data["missing"] == ["kp_p_049"]
