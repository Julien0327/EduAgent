"""引用注入机制测试（Phase 2 / S2，§4.4，决策㉚⑥ day-1 纪律项）。

覆盖：账本机制（record/annotate/save/load）→ 取数侧 ref（loader/工具）→
工作流端到端装配（覆盖率 100% 断言：卷面每个 ref 都在 citations.json 且
used_in 齐全）→ 文档页脚注入 → API 端点。
工作流用例沿用 test_workflow 的 FakeLLM 离线模式（不依赖真实 API）。
"""
import json
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

import backend.app.agent.workflow as wf
from backend.app.agent.context import TaskContext, task_store
from backend.app.knowledge import loader
from backend.app.retrieval.citations import (
    REF_SEED_PREFIX,
    CitationLedger,
    require_ref,
    summarize_question_sources,
)
from backend.app.services import exam_docx, lesson_plan
from backend.main import app

# S3 起检索层走真实库+模型——本模块全部用例替换为 Fake（离线）
pytestmark = pytest.mark.usefixtures("fake_retrieval")


# ---------- 机制层 ----------

def test_record_merges_same_ref_and_appends_used_in():
    led = CitationLedger("t1")
    led.record("question", "q_1", "01_试卷.docx#第1题")
    led.record("question", "q_1", "02_答题卡.xlsx#第1题")
    led.record("question", "q_1", "01_试卷.docx#第1题")  # 重复 used_in 去重
    d = led.to_dict()
    assert d["task_id"] == "t1"
    assert d["citations"] == [{
        "kind": "question", "ref": "q_1",
        "used_in": ["01_试卷.docx#第1题", "02_答题卡.xlsx#第1题"],
    }]


def test_record_validates_kind_and_ref():
    led = CitationLedger("t1")
    with pytest.raises(ValueError, match="未知引用类型"):
        led.record("bogus", "x")
    with pytest.raises(ValueError, match="ref 不能为空"):
        led.record("question", "")


def test_annotate_registers_missing_ref_too():
    led = CitationLedger("t1")
    led.annotate("kp", "kp_x", "01_教案.docx#教学过程")
    assert led.refs("kp") == ["kp_x"]
    assert led.to_dict()["citations"][0]["used_in"] == ["01_教案.docx#教学过程"]


def test_save_load_roundtrip(tmp_path):
    led = CitationLedger("t9")
    led.record("question", "q_ape_1", "01_试卷.docx#第1题")
    led.record("kp", "kp_p_001")
    path = led.save(tmp_path / "citations.json")
    loaded = CitationLedger.load(path)
    assert loaded.to_dict() == led.to_dict()


def test_require_ref_fails_fast_on_missing(tmp_path):
    assert require_ref({"ref": "q_1", "x": 1}, kind="question")["x"] == 1
    with pytest.raises(ReferenceError, match="必带引用"):
        require_ref({"q_id": "q_1"}, kind="question")  # 无 ref → 代码缺陷


def test_summarize_sources_seed_bank_and_generated():
    qs = [
        {"stem": "a", "ref": f"{REF_SEED_PREFIX}q1"},
        {"stem": "b", "ref": "q_ape_123"},
        {"stem": "c"},  # 无 ref = AI 生成
        {"stem": "d", "ref": f"{REF_SEED_PREFIX}q2"},
    ]
    text = summarize_question_sources(qs)
    assert "题库检索 1 题" in text and "内置种子题库 2 题" in text and "AI 生成 1 题" in text
    assert summarize_question_sources([]).startswith("题目来源：无")


# ---------- 取数侧 ref（§4.4 强制） ----------

def test_seed_bank_questions_carry_ref():
    bank = loader.load_question_bank()
    assert bank["questions"]
    for q in bank["questions"]:
        assert q["ref"].startswith(REF_SEED_PREFIX)
        assert q["ref"] == f"{REF_SEED_PREFIX}{q['question_id']}"


def test_search_knowledge_points_carries_ref():
    """真实库 385 KP 冒烟（FakeEmbedder 离线）：检索返回必带 ref=kp_id（§4.4 契约）。"""
    from backend.app.storage import db as _db
    from backend.app.retrieval.kp_recall import KpRetriever
    from backend.tests.conftest import FakeEmbedder
    if not _db.DEFAULT_DB_PATH.exists():
        pytest.skip("真实业务库不存在")
    ret = KpRetriever(_db.DEFAULT_DB_PATH, embedding_service=FakeEmbedder())
    try:
        r = ret.recall("一元二次方程", top_k=3)
        assert r
        for kp in r:
            assert kp["ref"] == kp["kp_id"]
    finally:
        ret.close()


# ---------- 工作流端到端：覆盖率 100%（核心断言） ----------

@pytest.fixture()
def use_fake_llm(monkeypatch):
    from backend.tests.test_workflow import FakeLLM, LLMResponse  # 复用离线 Fake
    def _install(responses):
        fake = FakeLLM(responses)
        monkeypatch.setattr(llm_utils_module, "get_llm_service", lambda: fake)
        return fake
    import backend.app.agent.llm_utils as llm_utils_module
    return _install


def _plan_resp(workflow: str, **params):
    from backend.app.agent.llm_client import LLMResponse
    return LLMResponse(content="", model="fake", tool_calls=[{
        "id": "p1", "name": "submit_plan",
        "arguments": {"workflow": workflow,
                      "params": {"subject": "数学", "chapter": "一元二次方程", **params}},
    }])


def test_exam_loop_citations_coverage_100(use_fake_llm):
    """§4.4 核心验收：卷面每道题的 ref 都在 citations.json 且 used_in 覆盖试卷+答题卡。"""
    from backend.app.agent.llm_client import LLMResponse
    use_fake_llm([_plan_resp("exam_loop"), LLMResponse(content="", model="fake", tool_calls=[{
        "id": "d1", "name": "submit_diagnosis",
        "arguments": {"summary": "s", "diagnosis": "d", "suggestions": ["x"]},
    }])])
    ctx = wf.run_task("请对《一元二次方程》做章节小测并生成学情报告")
    assert ctx.status == "completed", ctx.error

    # 落盘文件存在且与账本一致
    path = Path("data/outputs") / ctx.task_id / "citations.json"
    assert path.exists()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["task_id"] == ctx.task_id

    by_ref = {c["ref"]: c for c in saved["citations"] if c["kind"] == "question"}
    # 覆盖率 100%：卷面每个带 ref 的题都登记，且 used_in 齐全
    for i, q in enumerate(ctx.params["questions"], 1):
        c = by_ref.get(q["ref"])
        assert c is not None, f"卷面题 {q['ref']} 未登记引用（下游丢弃=代码缺陷）"
        assert f"01_试卷.docx#第{i}题" in c["used_in"]
        assert f"02_答题卡.xlsx#第{i}题" in c["used_in"]
    # 反向：账本中没有卷面之外的题目引用
    paper_refs = {q["ref"] for q in ctx.params["questions"] if q.get("ref")}
    assert paper_refs == set(by_ref)
    # KP 引用已装配 used_in
    kp_cites = [c for c in saved["citations"] if c["kind"] == "kp"]
    assert kp_cites and all("01_试卷.docx#整卷出题依据" in c["used_in"] for c in kp_cites)


def test_question_gen_llm_questions_not_in_ledger(use_fake_llm):
    """LLM 生成题不是取数（无 ref）→ 不进引用账本；页脚统计如实标 AI 生成。"""
    from backend.app.agent.llm_client import LLMResponse
    use_fake_llm([_plan_resp("question_gen", count=2), LLMResponse(content="", model="fake", tool_calls=[{
        "id": "q1", "name": "submit_questions", "arguments": {"questions": [
            {"type": "选择题", "stem": f"第{i}题", "options": ["A.1", "B.2", "C.3", "D.4"],
             "answer": "A", "difficulty": "简单", "analysis": ""}
            for i in range(1, 3)
        ]},
    }])])
    ctx = wf.run_task("帮我智能组一套《一元二次方程》的卷子")
    assert ctx.status == "completed", ctx.error
    question_refs = ctx.citations.refs("question")
    assert question_refs == []  # LLM 生成 ≠ 取数
    # 但页脚来源统计仍如实标注
    assert "AI 生成 2 题" in summarize_question_sources(ctx.params["questions"])


def test_lesson_plan_annotates_kp_refs(use_fake_llm):
    from backend.app.agent.llm_client import LLMResponse
    use_fake_llm([_plan_resp("lesson_plan"), LLMResponse(content="", model="fake", tool_calls=[{
        "id": "l1", "name": "submit_lesson_plan",
        "arguments": {"objectives": ["o1", "o2", "o3"], "key_points": "k",
                      "difficult_points": "d",
                      "process": [{"step": "导入", "detail": "x"}], "homework": "h"},
    }])])
    ctx = wf.run_task("帮我写一份《一元二次方程》的教案")
    assert ctx.status == "completed", ctx.error
    kp_cites = [c for c in ctx.citations.to_dict()["citations"] if c["kind"] == "kp"]
    assert kp_cites
    assert all("01_教案.docx#教学过程" in c["used_in"] for c in kp_cites)


# ---------- 文档注入 ----------

def test_exam_docx_footer_injected(tmp_path):
    questions = [
        {"type": "选择题", "stem": "s1", "options": ["A.1"], "answer": "A", "score": 5,
         "difficulty": "简单", "ref": f"{REF_SEED_PREFIX}q1"},
        {"type": "填空题", "stem": "s2", "answer": "x", "score": 5, "difficulty": "简单"},
    ]
    path = exam_docx.create_exam_docx("测试卷", questions, tmp_path / "exam.docx")
    footer_text = Document(str(path)).sections[0].footer.paragraphs[0].text
    assert "内置种子题库 1 题" in footer_text and "AI 生成 1 题" in footer_text


def test_lesson_plan_kp_note_injected(tmp_path):
    content = {"objectives": ["o"], "key_points": "k", "difficult_points": "d",
               "process": [("s", "d")], "homework": "h"}
    path = lesson_plan.create_lesson_plan_docx(
        "主题", content, tmp_path / "lp.docx", kp_refs=["kp_p_001", "kp_p_002"])
    text = "\n".join(p.text for p in Document(str(path)).paragraphs)
    assert "知识点引用：2 个" in text and "kp_p_001" in text


# ---------- API ----------

def test_citations_api(tmp_path, monkeypatch):
    """API 隔离到 tmp_path（不污染真实 outputs 目录）。"""
    from backend.app.storage import file_store
    monkeypatch.setattr(file_store, "OUTPUTS_ROOT", tmp_path)

    with TestClient(app) as client:
        # 任务不存在 → 404
        r = client.get("/api/tasks/nonexistent/citations")
        assert r.status_code == 404
        # 任务存在、无 citations.json → 空
        ctx = TaskContext(task_id="ci_test_1", prompt="p")
        task_store._tasks[ctx.task_id] = ctx
        r = client.get("/api/tasks/ci_test_1/citations")
        assert r.status_code == 200 and r.json() == {"task_id": "ci_test_1", "citations": []}
        # 有 citations.json → 读取
        led = CitationLedger("ci_test_1")
        led.record("question", "q_1", "01_试卷.docx#第1题")
        led.save(file_store.build_output_path("ci_test_1", "citations.json"))
        r = client.get("/api/tasks/ci_test_1/citations")
        assert r.status_code == 200
        assert r.json()["citations"][0]["ref"] == "q_1"
