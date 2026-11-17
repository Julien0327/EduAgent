"""诊断推荐 + 叙述 + 服务/API/工作流测试（Phase 3 / S3）。

三层：单元（推荐策略/narrate 兜底）+ 真实演示班端到端（服务层）+ API/工作流集成。
"""
import json
from pathlib import Path

import pytest

from backend.app.diagnosis import recommend
from backend.app.diagnosis.narrate import narrate_diagnosis
from backend.app.diagnosis.recommend import difficulty_band

_REAL_DB = Path("data/db/edumate.sqlite")


# ---------- 单元：难度带（D4） ----------

def test_difficulty_band():
    assert difficulty_band(0.10) == 1   # 5*0.10=0.5 → round 1
    assert difficulty_band(0.30) == 2   # 1.5 → 2（half-up）
    assert difficulty_band(0.50) == 3   # 2.5 → 3
    assert difficulty_band(0.90) == 5   # 4.5 → 5
    assert difficulty_band(None) == 1


# ---------- 单元：推荐 ----------

class FakeRetriever:
    def __init__(self, pool):
        self.pool = pool
        self.calls = []

    def search_by_kp(self, kp_ids, filters=None, question_limit=20,
                     target_difficulty=None):
        filters = filters or {}
        dmin = filters.get("difficulty_min")
        dmax = filters.get("difficulty_max")
        out = [q for q in self.pool
               if (dmin is None or q["difficulty"] >= dmin)
               and (dmax is None or q["difficulty"] <= dmax)]
        self.calls.append({"kp_ids": kp_ids, "filters": filters})
        return out[:question_limit]


def _chain(target, root, confidence="mid", blocked=(), conflict=False):
    return {
        "target_kp": target, "target_name": target,
        "root_kp": root, "root_name": root,
        "path": [root, target], "path_names": [root, target],
        "root_candidates": [{"kp_id": root, "mastery": 0.5, "depth": 1}],
        "conflict": conflict, "blocked_prereqs": list(blocked),
        "confidence": confidence,
    }


def _mastery(kps):
    return {kp: {"mastery": 0.5, "state": "weak"} for kp in kps}


def test_recommend_root_and_target_both():
    pool = [
        {"q_id": "q1", "stem": "s1", "difficulty": 2, "kp_ids": ["R"]},
        {"q_id": "q2", "stem": "s2", "difficulty": 2, "kp_ids": ["R"]},
        {"q_id": "q3", "stem": "s3", "difficulty": 3, "kp_ids": ["R"]},
        {"q_id": "q4", "stem": "s4", "difficulty": 2, "kp_ids": ["T"]},
        {"q_id": "q5", "stem": "s5", "difficulty": 2, "kp_ids": ["T"]},
        {"q_id": "q6", "stem": "s6", "difficulty": 3, "kp_ids": ["T"]},
    ]
    ret = FakeRetriever(pool)
    src = lambda ids: {i: "ape210k" for i in ids}
    chains = [_chain("T", "R")]
    groups = recommend.recommend(chains, _mastery(["R", "T"]), set(), ret, src,
                                 {"R": "根因R", "T": "目标T"})
    roles = [g["role"] for g in groups]
    assert roles == ["root", "target"]
    for g in groups:
        assert g["candidate_count"] == 3
        assert all(q["source"] == "ape210k" for q in g["questions"])
        assert all("q_id" in q and "stem" in q and "recommendation_reason" in q
                   for q in g["questions"])


def test_exclude_completed():
    pool = [
        {"q_id": "q_done", "stem": "s", "difficulty": 2, "kp_ids": ["R"]},
        {"q_id": "q2", "stem": "s", "difficulty": 2, "kp_ids": ["R"]},
        {"q_id": "q3", "stem": "s", "difficulty": 2, "kp_ids": ["R"]},
        {"q_id": "q4", "stem": "s", "difficulty": 2, "kp_ids": ["R"]},
    ]
    ret = FakeRetriever(pool)
    src = lambda ids: {i: "ape210k" for i in ids}
    groups = recommend.recommend([_chain("T", "R")], _mastery(["R", "T"]),
                                 {"q_done"}, ret, src, {"R": "R", "T": "T"})
    root = groups[0]
    assert all(q["q_id"] != "q_done" for q in root["questions"])
    assert root["candidate_count"] == 3


def test_relaxation_widened_and_completed():
    # strict band [2,3] 里 q1(d2) 已完成、q2(d2) 新鲜 → 排除后仅 1
    pool = [
        {"q_id": "q1", "stem": "s", "difficulty": 2, "kp_ids": ["R"]},
        {"q_id": "q2", "stem": "s", "difficulty": 2, "kp_ids": ["R"]},
        {"q_id": "q3", "stem": "s", "difficulty": 5, "kp_ids": ["R"]},
    ]
    ret = FakeRetriever(pool)
    src = lambda ids: {i: "ape210k" for i in ids}
    groups = recommend.recommend([_chain("T", "R")], _mastery(["R", "T"]),
                                 {"q1"}, ret, src, {"R": "R", "T": "T"})
    root = groups[0]
    assert root["relaxation"] is not None
    assert "difficulty_widened" in root["relaxation"]["levels"]
    assert "completed_included" in root["relaxation"]["levels"]
    # 最终纳入 3 条，其中 q1 标记 completed
    assert root["candidate_count"] == 3
    by_qid = {q["q_id"]: q for q in root["questions"]}
    assert by_qid["q1"]["completed"] is True


def test_multi_root_and_low_confidence_reflected():
    chain = {
        "target_kp": "T", "target_name": "目标T",
        "root_candidates": [
            {"kp_id": "R1", "mastery": 0.5, "depth": 1},
            {"kp_id": "R2", "mastery": 0.55, "depth": 1},
        ],
        "conflict": True, "blocked_prereqs": ["P"],
        "confidence": "low",
    }
    pool = [
        {"q_id": "q1", "stem": "s", "difficulty": 2, "kp_ids": ["R1"]},
        {"q_id": "q2", "stem": "s", "difficulty": 2, "kp_ids": ["R1"]},
        {"q_id": "q3", "stem": "s", "difficulty": 2, "kp_ids": ["R1"]},
        {"q_id": "q4", "stem": "s", "difficulty": 2, "kp_ids": ["R2"]},
        {"q_id": "q5", "stem": "s", "difficulty": 2, "kp_ids": ["R2"]},
        {"q_id": "q6", "stem": "s", "difficulty": 2, "kp_ids": ["R2"]},
        {"q_id": "q7", "stem": "s", "difficulty": 2, "kp_ids": ["T"]},
        {"q_id": "q8", "stem": "s", "difficulty": 2, "kp_ids": ["T"]},
        {"q_id": "q9", "stem": "s", "difficulty": 2, "kp_ids": ["T"]},
    ]
    ret = FakeRetriever(pool)
    src = lambda ids: {i: "ape210k" for i in ids}
    groups = recommend.recommend([chain], _mastery(["R1", "R2", "T"]), set(),
                                 ret, src, {"R1": "R1", "R2": "R2", "T": "T"})
    roles = [g["role"] for g in groups]
    assert roles == ["root", "root", "target"]  # 两个根因 + 目标
    for g in groups:
        if g["role"] == "root":
            assert "低置信度" in g["questions"][0]["recommendation_reason"]
            assert "前置证据不足" in g["questions"][0]["recommendation_reason"]
        assert g["conflict"] is True
        assert g["blocked_prereqs"] == ["P"]


# ---------- 单元：叙述兜底 ----------

def _diagnosis_fixture():
    return {
        "kp_names": {"kp_p_078": "乘除运算", "kp_p_075": "分数运算"},
        "mastery": {
            "kp_p_078": {"state": "weak", "mastery": 0.50},
            "kp_p_075": {"state": "weak", "mastery": 0.52},
        },
        "chains": [{
            "target_name": "分数运算", "root_name": "乘除运算",
            "path_names": ["乘除运算", "分数运算"], "confidence": "mid",
            "blocked_prereqs": [], "conflict": False,
        }],
    }


def test_narrate_template_when_no_key(monkeypatch):
    monkeypatch.setattr("backend.config.settings.DEEPSEEK_API_KEY", None)
    out = narrate_diagnosis(_diagnosis_fixture())
    assert out["narration_source"] == "template"
    assert "乘除运算" in out["diagnosis"]


def test_narrate_template_on_llm_failure(monkeypatch):
    from backend.app.agent.llm_client import LLMError

    class FailLLM:
        def chat_with_tools(self, *a, **k):
            raise LLMError("boom")

    monkeypatch.setattr("backend.config.settings.DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr("backend.app.agent.llm_utils.get_llm_service",
                        lambda: FailLLM())
    out = narrate_diagnosis(_diagnosis_fixture())
    assert out["narration_source"] == "template"
    assert out["summary"] and out["diagnosis"] and out["suggestions"]


def test_narrate_llm_success(monkeypatch):
    from backend.app.agent.llm_client import LLMResponse

    class OkLLM:
        def chat_with_tools(self, *a, **k):
            return LLMResponse(model="fake", tool_calls=[{
                "id": "n", "name": "submit_narration",
                "arguments": {"summary": "s", "diagnosis": "d", "suggestions": ["x"]},
            }])

    monkeypatch.setattr("backend.config.settings.DEEPSEEK_API_KEY", "x")
    monkeypatch.setattr("backend.app.agent.llm_utils.get_llm_service",
                        lambda: OkLLM())
    out = narrate_diagnosis(_diagnosis_fixture())
    assert out["narration_source"] == "llm"
    assert out["summary"] == "s"


# ---------- 集成：真实演示班（服务层） ----------

def _db_retriever():
    from backend.app.retrieval.question_search import expand_by_kp, rerank
    from backend.app.storage import db

    class DBRetriever:
        def search_by_kp(self, kp_ids, filters=None, question_limit=20,
                         target_difficulty=None):
            conn = db.connect(_REAL_DB)
            try:
                cands = expand_by_kp(conn, kp_ids, filters,
                                     limit=question_limit * 3)
            finally:
                conn.close()
            return rerank(cands, kp_ids, target_difficulty=target_difficulty)[:question_limit]

    return DBRetriever()


@pytest.mark.skipif(not _REAL_DB.exists(), reason="真实业务库不存在")
def test_real_demo_recommendation_end_to_end():
    from backend.app.diagnosis import service as diag_service
    from backend.app.storage import db

    conn = db.connect(_REAL_DB)
    try:
        cls = conn.execute(
            "SELECT class_id FROM classes WHERE name='六年级演示班' LIMIT 1").fetchone()
        assert cls is not None
        diag = diag_service.build_diagnosis(conn, cls["class_id"], retriever=_db_retriever())
    finally:
        conn.close()

    assert diag["data_scope"]["question_count"] == 49
    assert len(diag["chains"]) >= 1
    recs = diag["recommendations"]
    assert recs
    roles = {g["role"] for g in recs}
    assert roles == {"root", "target"}  # 根因巩固 + 目标针对性都有
    # 逐条核验：真实 q_id 在题库、source=ape210k、有 stem/difficulty/kp_ids/reason
    for g in recs:
        for q in g["questions"]:
            assert q["q_id"].startswith("q_ape_")
            assert q["source"] == "ape210k"
            assert q["stem"]
            assert q["difficulty"] >= 1
            assert q["kp_ids"]
            assert "recommendation_reason" in q
    # 已完成的题默认排除（本班 49 题都在 exam_questions，推荐不应重复）
    completed = {q["q_id"] for g in recs for q in g["questions"]}
    assert not (completed & {
        "q_ape_101780", "q_ape_16067", "q_ape_284252"})  # 演示班已用题样例


@pytest.mark.skipif(not _REAL_DB.exists(), reason="真实业务库不存在")
def test_workflow_class_diagnosis_e2e(monkeypatch):
    """class_diagnosis 工作流端到端：确定性诊断 + 模板叙述 + JSON 产物 + 引用。"""
    from backend.app.agent import workflow as wf
    from backend.app.agent import context
    from backend.app.storage import db

    real_connect = db.connect

    def fake_connect(path=None, **kw):
        if path is None:
            return real_connect(_REAL_DB, **kw)
        return real_connect(path, **kw)

    monkeypatch.setattr("backend.app.storage.db.connect", fake_connect)
    monkeypatch.setattr("backend.app.retrieval.service.get_retrieval_service",
                        lambda: _db_retriever())
    monkeypatch.setattr("backend.config.settings.DEEPSEEK_API_KEY", None)  # 强制模板兜底

    cls = real_connect(_REAL_DB).execute(
        "SELECT class_id FROM classes WHERE name='六年级演示班' LIMIT 1").fetchone()
    conn_id = cls["class_id"]

    ctx = wf.run_class_diagnosis_task(conn_id)
    assert ctx.status == "completed", ctx.error
    assert ctx.workflow == "class_diagnosis"
    assert [a.file_name for a in ctx.artifacts] == [
        "04_诊断结果.json", "05_学情诊断报告.pdf"]
    path = Path(ctx.artifacts[0].path)
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["diagnosis"]["chains"]
    assert payload["diagnosis"]["recommendations"]
    assert payload["narration"]["narration_source"] == "template"  # 无 Key → 兜底
    # S5：PDF 产物存在且非空（内容级验证见 test_diagnosis_pdf.py）
    pdf_path = Path(ctx.artifacts[1].path)
    assert pdf_path.exists() and pdf_path.stat().st_size > 1000
    # 引用账本：KP + 推荐题均已登记，且 used_in 装配到 JSON 与 PDF
    refs = ctx.citations
    assert refs.refs("kp")
    assert refs.refs("question")
    dumped = json.dumps(refs.to_dict(), ensure_ascii=False)
    assert "05_学情诊断报告.pdf#归因链" in dumped
    assert "05_学情诊断报告.pdf#推荐练习" in dumped


# ---------- API 集成（复用统一服务层） ----------

@pytest.mark.skipif(not _REAL_DB.exists(), reason="真实业务库不存在")
def test_api_classes_and_diagnosis(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.tests.conftest import AUTH_HEADERS

    monkeypatch.setattr("backend.config.settings.DB_PATH", str(_REAL_DB))
    monkeypatch.setattr("backend.app.retrieval.service.get_retrieval_service",
                        lambda: _db_retriever())
    with TestClient(app, headers=AUTH_HEADERS) as client:
        r = client.get("/api/classes")
        assert r.status_code == 200
        classes = r.json()
        demo = next((c for c in classes if c["name"] == "六年级演示班"), None)
        assert demo is not None
        d = client.get(f"/api/diagnosis/class/{demo['class_id']}")
        assert d.status_code == 200
        body = d.json()
        assert body["data_scope"]["question_count"] == 49
        assert body["mastery"]
        assert body["chains"]
        assert body["recommendations"]


def test_api_report_404_unknown_class(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.main import app
    from backend.tests.conftest import AUTH_HEADERS

    monkeypatch.setattr("backend.app.retrieval.service.get_retrieval_service",
                        lambda: _db_retriever())
    with TestClient(app, headers=AUTH_HEADERS) as client:
        r = client.post("/api/diagnosis/class/nonexistent/report")
        assert r.status_code == 404
