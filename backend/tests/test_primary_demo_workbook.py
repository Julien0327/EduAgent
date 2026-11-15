"""小学演示数据工作簿校验器测试（Phase 3 / S0-3）。

依赖已审核设计文件（data/staging/primary_demo_design.json）与真实业务库；
任一缺失则跳过（与真实库集成测试同风格）。不导入、不写库。
"""
import copy
import json
from pathlib import Path

import pytest

from backend.scripts.build_primary_demo_workbook import (
    DESIGN_FILE,
    load_kps_and_questions,
    validate_design,
)

pytestmark = pytest.mark.skipif(
    not DESIGN_FILE.exists() or not Path("data/db/edumate.sqlite").exists(),
    reason="演示设计文件或真实业务库不存在",
)


def _load():
    design = json.loads(DESIGN_FILE.read_text(encoding="utf-8"))
    kps, qmeta = load_kps_and_questions()
    return design, kps, qmeta


def test_design_validates_clean():
    design, kps, qmeta = _load()
    assert validate_design(design, kps, qmeta) == []


def test_missing_qid_detected():
    design, kps, qmeta = _load()
    bad = copy.deepcopy(design)
    bad["exams"]["A"][0]["q_id"] = "q_ape_不存在"
    errors = validate_design(bad, kps, qmeta)
    assert any("q_id 不在 verified 题库" in e for e in errors)


def test_duplicate_qno_detected():
    design, kps, qmeta = _load()
    bad = copy.deepcopy(design)
    # 复制第一条题到第二条位置 → 同一 q_id 重复（答题矩阵也会报缺/多，
    # 这里直接验证题号/映射检查能发现设计异常）
    bad["exams"]["A"][1] = copy.deepcopy(bad["exams"]["A"][0])
    errors = validate_design(bad, kps, qmeta)
    assert errors


def test_answer_out_of_range_detected():
    design, kps, qmeta = _load()
    bad = copy.deepcopy(design)
    bad["answers"][0]["score"] = bad["answers"][0]["full_score"] + 1
    errors = validate_design(bad, kps, qmeta)
    assert any("答题分值越界" in e for e in errors)
