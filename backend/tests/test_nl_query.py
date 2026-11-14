"""NL 问数测试（Phase 2 / S4，§4.1）。

覆盖：确定性参数抽取 / LLM 抽参与回退 / 白名单二次拦截 / 分值配平 /
端到端（真实库冒烟，skipif 守卫）。注入面：LLM 参数过白名单 + 全参数化绑定。
"""
import pytest
from pydantic import ValidationError

from backend.app.retrieval.balance import allocate_scores
from backend.app.retrieval.nl_query import (
    BankQueryParams,
    _deterministic_extract,
    query_question_bank,
)
from backend.app.storage import db

DB_EXISTS = db.DEFAULT_DB_PATH.exists()


# ---------- 确定性抽取 ----------

def test_deterministic_extract_basic():
    p = _deterministic_extract("找20道小学分数加减应用题，难度3，总分100分")
    assert p.count == 20 and p.stage == "primary" and p.difficulty == 3
    assert p.total_score == 100 and p.query


def test_deterministic_extract_conservative():
    p = _deterministic_extract("找一些题")
    assert p.count == 10 and p.stage is None and p.difficulty is None
    assert p.total_score is None


def test_deterministic_extract_count_bounds():
    assert _deterministic_extract("要999道题").count == 10  # 越界不采纳
    assert _deterministic_extract("要30道题").count == 30


def test_deterministic_extract_question_type():
    assert _deterministic_extract("找5道填空题").question_type == "填空题"
    assert _deterministic_extract("找5道选择题").question_type == "选择题"


# ---------- LLM 抽参与回退 ----------

def test_extract_params_llm_path(monkeypatch):
    import backend.app.agent.llm_utils as llm_utils
    from backend.app.agent.llm_client import LLMResponse

    class FakeLLM:
        def chat_with_tools(self, messages, tools=None, tool_choice="auto"):
            return LLMResponse(content="", model="fake", tool_calls=[{
                "id": "b1", "name": "submit_bank_query",
                "arguments": {"query": "分数加减", "stage": "primary", "count": 15,
                              "total_score": 100},
            }])

    monkeypatch.setattr(llm_utils, "get_llm_service", lambda: FakeLLM())
    params, source = _extract()
    assert source == "llm" and params.count == 15 and params.stage == "primary"


def _extract():
    from backend.app.retrieval.nl_query import extract_params
    return extract_params("找15道小学分数加减题，总分100分")


def test_extract_params_fallback_on_llm_failure(monkeypatch):
    import backend.app.agent.llm_utils as llm_utils

    class BoomLLM:
        def chat_with_tools(self, *a, **k):
            raise RuntimeError("network down")

    monkeypatch.setattr(llm_utils, "get_llm_service", lambda: BoomLLM())
    params, source = _extract()
    assert source == "fallback" and params.count == 15


# ---------- 白名单二次拦截（§4.1 注入面） ----------

def test_query_bank_rejects_invalid_llm_params():
    """LLM 被诱导输出非法参数 → 白名单拦截，不产生 SQL、不触任何服务构造。"""
    bad = BankQueryParams(query="x", stage="ALL", count=5)
    # service 传占位对象：拦截发生在服务构造之前（不应被使用）
    r = query_question_bank("any", params=bad, service=object())
    assert "error" in r and "stage" in r["error"]


def test_query_bank_rejects_bad_difficulty():
    """难度越界在两层被拦：Pydantic ge/le（schema 层）+ validate_filters（查询层）。"""
    from backend.app.retrieval.question_search import FilterValidationError, validate_filters
    with pytest.raises(ValidationError):
        BankQueryParams(query="x", difficulty=9)  # schema 层拦截
    with pytest.raises(FilterValidationError):
        validate_filters({"difficulty": 9})  # 查询层拦截


# ---------- 配平 ----------

def test_allocate_scores_exact_sum():
    qs = [{"type": "选择题"} for _ in range(5)] + [{"type": "解答题"} for _ in range(3)]
    scores = allocate_scores(qs, 100)
    assert sum(scores) == 100
    assert [q["score"] for q in qs] == scores  # 就地写入
    # 解答题分值为选择/填空的 2 倍（基数层面）
    assert scores[-1] > scores[0]


def test_allocate_scores_empty():
    assert allocate_scores([], 100) == []


# ---------- 端到端（真实库） ----------

@pytest.mark.skipif(not DB_EXISTS, reason="真实业务库不存在")
def test_query_bank_end_to_end_real_bank():
    """真实库端到端：抽参→检索→ref/processed/数量语义。"""
    from backend.app.retrieval.kp_recall import KpRetriever
    from backend.app.retrieval.service import RetrievalService
    from backend.tests.conftest import FakeEmbedder
    svc = RetrievalService(db.DEFAULT_DB_PATH, kp_retriever=KpRetriever(
        db.DEFAULT_DB_PATH, embedding_service=FakeEmbedder()))
    try:
        r = query_question_bank("找5道小学分数加减应用题", service=svc)
        assert "error" not in r
        assert len(r["questions"]) <= 5
        for q in r["questions"]:
            assert q["ref"] == q["q_id"]
            assert q["stage"] == "primary"
        assert r["params_source"] in ("llm", "fallback")
    finally:
        svc.close()


@pytest.mark.skipif(not DB_EXISTS, reason="真实业务库不存在")
def test_query_bank_with_score_allocation():
    """带总分约束：结果逐题配平且总分精确。"""
    from backend.app.retrieval.kp_recall import KpRetriever
    from backend.app.retrieval.service import RetrievalService
    from backend.tests.conftest import FakeEmbedder
    svc = RetrievalService(db.DEFAULT_DB_PATH, kp_retriever=KpRetriever(
        db.DEFAULT_DB_PATH, embedding_service=FakeEmbedder()))
    try:
        r = query_question_bank("找10道小学数学题，总分100分", service=svc)
        assert "error" not in r
        if r.get("allocation") and r["questions"]:
            assert sum(r["allocation"]) == 100
    finally:
        svc.close()
