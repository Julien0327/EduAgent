"""Phase 4 / S3-A judge 测试（零真实调用）。

覆盖：规则评分兜底（含 degraded 检测）、LLM judge（MockLLM）、
LLM 失败回退、制品文本提取、校准一致率、评分表生成与回读。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.eval.judge import (
    DIMENSIONS, band, calibration_agreement, extract_artifact_text,
    is_degraded, judge_case, llm_judge, rule_score,
)


def _case(prompt="请对《分数加减》做章节小测并生成学情报告", behavior="execute",
          params=None):
    return {"case_id": "gc_test", "prompt": prompt,
            "expect": {"behavior": behavior, "workflow": "exam_loop",
                       "params": params or {}}}


def _rec(status="completed", artifacts_ok=True, params_matched=True,
         artifacts=None):
    return {"case_id": "gc_test", "workflow": "exam_loop", "task_status": status,
            "artifacts_ok": artifacts_ok, "params_matched": params_matched,
            "artifacts": artifacts or []}


# ===================== 规则评分 =====================

def test_rule_score_healthy_artifacts(tmp_path):
    art = tmp_path / "01_试卷.docx.json"
    art.write_text(json.dumps({"x": 1}), encoding="utf-8")
    rec = _rec(artifacts=[{"name": "a", "path": str(art)}])
    out = rule_score(_case(), rec, "正常内容")
    assert out["scoring_source"] == "rule_fallback"
    for d in DIMENSIONS:
        assert 1 <= out["scores"][d] <= 5
    assert out["scores"]["content_correctness"] >= 4    # 完成+参数命中
    assert out["scores"]["coverage_completeness"] >= 4  # 产物有效


def test_rule_score_degraded_caps_content(tmp_path):
    """degraded 标记 → 内容正确性/覆盖完整 ≤2（gc_e03 类场景，决策㊶）。"""
    art = tmp_path / "03_学情报告.json"
    art.write_text(json.dumps({"msg": "学情诊断暂不可用"}), encoding="utf-8")
    rec = _rec(artifacts=[{"name": "a", "path": str(art)}])
    out = rule_score(_case(), rec, "学情诊断暂不可用（外部Agent与本地LLM均失败）")
    assert out["scores"]["content_correctness"] <= 2
    assert out["scores"]["coverage_completeness"] <= 2
    assert "降级" in out["reason"]


def test_rule_score_missing_artifacts():
    out = rule_score(_case(), _rec(artifacts_ok=False, artifacts=[]), "")
    assert out["scores"]["coverage_completeness"] <= 2
    assert out["scores"]["expression"] <= 2


def test_rule_score_failed_task():
    out = rule_score(_case(), _rec(status="failed", artifacts_ok=False), "")
    assert out["scores"]["content_correctness"] == 1


def test_is_degraded_markers():
    assert is_degraded("学情诊断暂不可用（外部Agent与本地LLM均失败）")
    assert not is_degraded("正常诊断内容")


# ===================== LLM judge（MockLLM，零真实调用）=====================

def test_llm_judge_with_mock_llm(monkeypatch):
    from backend.app.eval.provider import MockLLM
    import backend.app.agent.llm_utils as llmu
    monkeypatch.setattr(llmu, "get_llm_service", lambda: MockLLM())
    out = llm_judge(_case(), _rec(), "正常内容")
    assert out["scoring_source"] == "llm_judge"
    assert out["scores"]["content_correctness"] == 4
    assert out["reason"]


def test_llm_judge_failure_falls_back_to_rule(monkeypatch):
    """LLM 返回非法结构 → 规则评分兜底（记录 judge_error）。"""
    from backend.app.eval.fake_llm import FakeLLM
    import backend.app.agent.llm_utils as llmu
    monkeypatch.setattr(llmu, "get_llm_service", lambda: FakeLLM())
    out = llm_judge(_case(), _rec(), "内容")
    assert out["scoring_source"] == "rule_fallback"
    assert out.get("judge_error")


def test_judge_case_no_artifacts_uses_rule():
    out = judge_case(_case(), _rec(artifacts=[]), use_llm=True)
    assert out["scoring_source"] == "rule_fallback"
    assert "无产物" in out["reason"]


# ===================== 制品文本提取 =====================

def test_extract_artifact_text_json(tmp_path):
    p = tmp_path / "04_诊断结果.json"
    p.write_text(json.dumps({"chains": [1, 2]}), encoding="utf-8")
    text = extract_artifact_text([str(p)])
    assert "chains" in text
    assert "04_诊断结果.json" in text


def test_extract_artifact_text_missing_file():
    assert extract_artifact_text(["/nonexistent/x.json"]) == ""


# ===================== 校准一致率 =====================

def test_band_rounding():
    assert band(3.4) == 3
    assert band(3.5) == 4
    assert band(4.6) == 5
    assert band(0) == 1


def test_calibration_agreement_adopt_and_reject():
    judge = {"a": {d: 4 for d in DIMENSIONS},
             "b": {d: 2 for d in DIMENSIONS}}
    # 全部一致 → 100% 采信
    human = {"a": {d: 4 for d in DIMENSIONS},
             "b": {d: 2 for d in DIMENSIONS}}
    r = calibration_agreement(judge, human)
    assert r["overall_rate"] == 1.0 and r["adopted"] is True
    # 全部不一致 → 0% 回退
    human_bad = {"a": {d: 1 for d in DIMENSIONS},
                 "b": {d: 5 for d in DIMENSIONS}}
    r2 = calibration_agreement(judge, human_bad)
    assert r2["overall_rate"] == 0.0 and r2["adopted"] is False
    # 部分样本缺失 → 只统计交集
    r3 = calibration_agreement({"a": {d: 4 for d in DIMENSIONS}},
                               {"b": {d: 4 for d in DIMENSIONS}})
    assert r3["compared_pairs"] == 0


def test_calibration_agreement_threshold():
    judge = {f"c{i}": {d: 4 for d in DIMENSIONS} for i in range(10)}
    human = {f"c{i}": {d: (4 if i < 8 else 1) for d in DIMENSIONS} for i in range(10)}
    r = calibration_agreement(judge, human)  # 80% → 采信（>=）
    assert r["overall_rate"] == pytest.approx(0.8)
    assert r["adopted"] is True


# ===================== 评分表生成与回读 =====================

def test_generate_sheet_and_read_back(tmp_path, monkeypatch):
    from backend.app.eval import scoring_sheet as ss
    art = tmp_path / "01_试卷.docx.json"
    art.write_text(json.dumps({"stem": "测试题"}), encoding="utf-8")
    report = {"records": [{"case_id": "gc_e03", "workflow": "exam_loop",
                           "task_status": "completed",
                           "artifacts": [{"name": "01_试卷.docx",
                                          "path": str(art), "valid": True}]}]}
    rep_path = tmp_path / "report.json"
    rep_path.write_text(json.dumps(report), encoding="utf-8")
    calib = {"case_ids": ["gc_e03"]}
    calib_path = tmp_path / "calib.json"
    calib_path.write_text(json.dumps(calib), encoding="utf-8")
    out_md = tmp_path / "sheet.md"
    out_csv = tmp_path / "sheet.csv"
    info = ss.generate_sheet(rep_path, calib_path, out_md, out_csv)
    assert info["cases"] == 1
    assert out_md.exists() and out_csv.exists()
    md = out_md.read_text(encoding="utf-8")
    assert "gc_e03" in md and "内容正确性" in md
    # 回填 CSV 后回读
    import csv
    rows = list(csv.reader(out_csv.open(encoding="utf-8-sig")))
    header = rows[0]
    body = rows[1]
    body[header.index("content_correctness")] = "4"
    body[header.index("difficulty_fit")] = "3"
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f); w.writerow(header); w.writerow(body)
    scores, warnings = ss.read_human_csv(out_csv)
    assert scores["gc_e03"]["content_correctness"] == 4
    assert scores["gc_e03"]["difficulty_fit"] == 3
    assert warnings == []