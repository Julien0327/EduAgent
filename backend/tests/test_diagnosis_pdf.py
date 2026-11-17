"""学情诊断报告 PDF 测试（Phase 3 / S5）。

验收口径：**用 pypdf 实读 PDF 文本**（“实际打开检查”，不以文件存在为验收）。
单元：数据映射/四状态/空数据/叙述来源徽标/合成数据声明；
集成：真实演示班 build_diagnosis → PDF 与诊断 JSON 一致性（q_id/KP 名/数值）。
"""
import json
from pathlib import Path

import pytest
from pypdf import PdfReader

from backend.app.services.diagnosis_pdf import create_diagnosis_pdf

_REAL_DB = Path("data/db/edumate.sqlite")


def _mastery_entry(kp, state, mastery, sample=4, p_sample=None, a_sample=0,
                   aux_share=0.2):
    return {
        "kp_id": kp, "state": state, "mastery": mastery,
        "primary_only_mastery": mastery, "aux_share": aux_share,
        "sample_count": sample,
        "primary_sample_count": sample - a_sample if p_sample is None else p_sample,
        "aux_sample_count": a_sample,
        "weighted_evidence_count": float(sample),
        "weighted_primary_evidence_count": float(sample),
        "weighted_aux_evidence_count": 0.0,
        "q_ids": [f"q{i}" for i in range(sample)],
    }


def _diag_fixture(**over):
    d = {
        "class": {"class_id": "c1", "name": "六年级演示班", "grade": 6,
                  "stage": "primary", "school": "演示数据（非真实学校）"},
        "data_scope": {"class_id": "c1", "class_name": "六年级演示班",
                       "exam_count": 2, "question_count": 49, "student_count": 20,
                       "answer_count": 980, "kp_covered_count": 40},
        "kp_names": {"kp_a": "乘除运算", "kp_b": "分数运算", "kp_c": "圆的面积"},
        "mastery": {
            "kp_a": _mastery_entry("kp_a", "weak", 0.5088),
            "kp_b": _mastery_entry("kp_b", "weak", 0.515, sample=19, a_sample=14,
                                   aux_share=0.594),
            "kp_c": _mastery_entry("kp_c", "no_coverage", None, sample=0),
        },
        "chains": [{
            "target_kp": "kp_b", "target_name": "分数运算",
            "root_kp": "kp_a", "root_name": "乘除运算",
            "path": ["kp_a", "kp_b"], "path_names": ["乘除运算", "分数运算"],
            "root_candidates": [{"kp_id": "kp_a", "mastery": 0.5088, "depth": 1}],
            "conflict": False, "blocked_prereqs": [], "blocked_prereq_names": [],
            "prereq_status": {}, "confidence": "low", "min_samples": 4,
            "target_mastery": 0.515, "target_aux_share": 0.594,
            "evidence": [{"q_id": "q_ape_16067", "kp_id": "kp_b", "role": "primary",
                          "exam_label": "A", "q_no": 7}],
        }],
        "recommendations": [
            {"role": "root", "kp_id": "kp_a", "kp_name": "乘除运算",
             "chain_target_kp": "kp_b", "chain_target_name": "分数运算",
             "confidence": "low", "conflict": False, "blocked_prereqs": [],
             "mastery": 0.5088, "band": 3, "difficulty_range": [2, 3],
             "relaxation": None, "candidate_count": 3,
             "questions": [
                 {"q_id": "q_ape_1073439", "stem": "测量国旗杆的高度…",
                  "difficulty": 2, "kp_ids": ["kp_a"], "source": "ape210k",
                  "recommendation_reason": "根因巩固练习：乘除运算", "completed": False},
             ]},
            {"role": "target", "kp_id": "kp_b", "kp_name": "分数运算",
             "chain_target_kp": "kp_b", "chain_target_name": "分数运算",
             "confidence": "low", "conflict": False, "blocked_prereqs": [],
             "mastery": 0.515, "band": 3, "difficulty_range": [2, 3],
             "relaxation": {"levels": ["difficulty_widened"],
                            "final_strategy": "difficulty_widened",
                            "reason": "候选不足", "candidate_count": 3},
             "questions": []},
        ],
        "graph_edges_count": 87,
        "lam": 0.5,
    }
    d.update(over)
    return d


def _pdf_text(tmp_path, diagnosis, narration, name="report.pdf") -> str:
    path = tmp_path / name
    create_diagnosis_pdf(diagnosis, narration, path)
    assert path.exists() and path.stat().st_size > 1000
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def test_pdf_contains_core_facts(tmp_path):
    text = _pdf_text(tmp_path, _diag_fixture(),
                     {"summary": "小结", "diagnosis": "诊断", "suggestions": ["建议1"],
                      "narration_source": "template"})
    for kw in ("学情诊断报告", "六年级演示班", "合成演示数据",
               "薄弱", "样本不足", "无覆盖",           # 四状态标签
               "乘除运算", "分数运算", "0.5088",       # 掌握度数值
               "置信度", "低",                          # 置信度
               "辅助证据占比",                          # aux 提示
               "乘除运算 → 分数运算",                   # 归因路径
               "q_ape_16067",                           # 证据追溯 q_id
               "根因巩固", "目标针对性",                 # 推荐分组
               "q_ape_1073439", "ape210k",              # 真实题目与来源
               "放宽策略",                              # 放宽如实标注
               "模板兜底",                              # 叙述来源
               "49", "980",                             # 数据范围
               ):
        assert kw in text, f"PDF 缺少关键内容：{kw}"


def test_pdf_llm_source_badge(tmp_path):
    text = _pdf_text(tmp_path, _diag_fixture(),
                     {"summary": "s", "diagnosis": "d", "suggestions": ["x"],
                      "narration_source": "llm"})
    assert "AI 叙述（LLM" in text


def test_pdf_narration_none(tmp_path):
    text = _pdf_text(tmp_path, _diag_fixture(), None)
    assert "未生成" in text


def test_pdf_empty_data_no_crash(tmp_path):
    """空链/空推荐/全 no_coverage：输出「无」文案，不崩溃。"""
    d = _diag_fixture(chains=[], recommendations=[])
    d["mastery"] = {"kp_c": _mastery_entry("kp_c", "no_coverage", None, sample=0)}
    text = _pdf_text(tmp_path, d, {"summary": "", "diagnosis": "",
                                   "suggestions": [], "narration_source": "template"})
    assert "无（本班未发现样本充足的薄弱知识点）" in text
    assert "无推荐" in text
    assert "本班级无覆盖知识点" in text


def test_pdf_long_content_paginates(tmp_path):
    """40+ 行明细长表：多页输出且每页可提取文本（分页不丢内容）。"""
    d = _diag_fixture()
    for i in range(45):
        d["mastery"][f"kp_x{i:02d}"] = _mastery_entry(
            f"kp_x{i:02d}", "normal", 0.7 + i / 1000)
        d["kp_names"][f"kp_x{i:02d}"] = f"测试知识点{i:02d}"
    text = _pdf_text(tmp_path, d, {"summary": "s", "diagnosis": "d",
                                   "suggestions": ["x"], "narration_source": "template"})
    assert "测试知识点44" in text and "测试知识点00" in text
    reader_pages = len(PdfReader(str(tmp_path / "report.pdf")).pages)
    assert reader_pages >= 2


# ---------- 集成：真实演示班 PDF 与诊断 JSON 一致性 ----------

def _db_retriever():
    from backend.app.retrieval.question_search import expand_by_kp, rerank
    from backend.app.storage import db

    class DBRetriever:
        def search_by_kp(self, kp_ids, filters=None, question_limit=20,
                         target_difficulty=None):
            conn = db.connect(_REAL_DB)
            try:
                cands = expand_by_kp(conn, kp_ids, filters, limit=question_limit * 3)
            finally:
                conn.close()
            return rerank(cands, kp_ids,
                          target_difficulty=target_difficulty)[:question_limit]

    return DBRetriever()


@pytest.mark.skipif(not _REAL_DB.exists(), reason="真实业务库不存在")
def test_real_demo_pdf_matches_diagnosis_json(tmp_path):
    """真实演示班：PDF 文本与统一诊断 JSON 一致（q_id/KP 名/掌握度/状态计数）。"""
    from backend.app.diagnosis import service as diag_service
    from backend.app.diagnosis.narrate import narrate_diagnosis
    from backend.app.storage import db
    from backend.config import settings

    old_key = settings.DEEPSEEK_API_KEY
    settings.DEEPSEEK_API_KEY = None      # 离线：模板兜底
    try:
        conn = db.connect(_REAL_DB)
        try:
            cls = conn.execute(
                "SELECT class_id FROM classes WHERE name='六年级演示班' LIMIT 1").fetchone()
            assert cls is not None
            diag = diag_service.build_diagnosis(conn, cls["class_id"],
                                                retriever=_db_retriever())
        finally:
            conn.close()
        narration = narrate_diagnosis(diag)
        assert narration["narration_source"] == "template"

        path = tmp_path / "real_demo.pdf"
        create_diagnosis_pdf(diag, narration, path)
        text = "\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)

        # 一致性：JSON 中每条推荐的每个 q_id 都出现在 PDF
        for g in diag["recommendations"]:
            for q in g["questions"]:
                assert q["q_id"] in text, f"推荐题 {q['q_id']} 未出现在 PDF"
        # 一致性：每条链的目标/根因/路径名出现在 PDF
        for c in diag["chains"]:
            assert c["target_name"] in text
            assert c["root_name"] in text
            for name in c["path_names"]:
                assert name in text
        # 一致性：四状态计数（对 82 全集统计）与 PDF 统计行一致
        counts = {}
        for m in diag["mastery"].values():
            counts[m["state"]] = counts.get(m["state"], 0) + 1
        assert counts["weak"] >= 5
        # 掌握度数值抽查（4 位小数字符串形式）
        for c in diag["chains"]:
            v = f"{c['target_mastery']:.4f}"
            assert v in text, f"掌握度 {v} 未出现在 PDF"
        # 叙述来源徽标
        assert "模板兜底" in text
        # 合成演示声明
        assert "合成演示数据" in text
    finally:
        settings.DEEPSEEK_API_KEY = old_key
