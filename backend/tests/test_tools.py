"""工具单元测试（FakeProvider 离线，不依赖真实 API Key / 网络）。"""
import pytest

import app.agent.llm_utils as llm_utils
from app.agent.llm_client import LLMResponse
from app.tools import registry  # 导入即注册全部工具
from app.tools.registry import registry as reg


class FakeLLM:
    """按顺序返回预设响应；响应耗尽后返回空（无工具调用）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_with_tools(self, messages, tools=None, tool_choice="auto"):
        self.calls.append(
            {"messages": messages, "tools": tools, "tool_choice": tool_choice}
        )
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(content="", tool_calls=[], model="fake")


@pytest.fixture
def use_fake_llm(monkeypatch):
    def _install(responses):
        fake = FakeLLM(responses)
        monkeypatch.setattr(llm_utils, "get_llm_service", lambda: fake)
        return fake

    return _install


# ---------- 注册表 ----------

def test_registered_tool_names():
    names = {s["function"]["name"] for s in reg.get_schemas()}
    assert names == {"search_knowledge_points", "generate_questions", "generate_diagnosis"}


def test_schemas_openai_format():
    for s in reg.get_schemas():
        assert s["type"] == "function"
        assert isinstance(s["function"]["parameters"], dict)


def test_unknown_tool():
    r = reg.execute("no_such_tool", {})
    assert "error" in r


# ---------- search_knowledge_points ----------

def test_search_knowledge_points_valid():
    r = reg.execute("search_knowledge_points", {"subject": "数学", "chapter": "一元二次方程"})
    assert "error" not in r
    assert len(r["knowledge_points"]) == 7
    assert set(r["knowledge_points"][0].keys()) == {
        "kp_id", "name", "description", "typical_types",
    }


def test_search_knowledge_points_no_match():
    r = reg.execute("search_knowledge_points", {"subject": "数学", "chapter": "二次函数"})
    assert r == {"knowledge_points": []}


def test_search_knowledge_points_invalid_param():
    r = reg.execute("search_knowledge_points", {"chapter": 123})
    assert "error" in r


# ---------- generate_questions ----------

def test_generate_questions_invalid_param():
    r = reg.execute("generate_questions", {"count": 0})
    assert "error" in r


def test_generate_questions_unknown_kp():
    r = reg.execute("generate_questions", {
        "knowledge_points": ["不存在的知识点"],
        "count": 1, "question_types": ["选择题"],
    })
    assert "error" in r


def test_generate_questions_score_sum_and_ids(use_fake_llm):
    use_fake_llm([LLMResponse(content="", model="fake", tool_calls=[{
        "id": "c1", "name": "submit_questions", "arguments": {"questions": [
            {"type": "选择题", "stem": "s1", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
             "answer": "A", "difficulty": "简单", "analysis": ""},
            {"type": "填空题", "stem": "s2", "options": None,
             "answer": "2", "difficulty": "简单", "analysis": ""},
            {"type": "解答题", "stem": "s3", "options": None,
             "answer": "3", "difficulty": "中等", "analysis": ""},
        ]},
    }])])
    r = reg.execute("generate_questions", {
        "knowledge_points": ["一元二次方程的定义与一般形式", "直接开平方法", "因式分解法"],
        "count": 3, "difficulty": "简单",
        "question_types": ["选择题", "填空题", "解答题"], "total_score": 30,
    })
    assert "error" not in r
    qs = r["questions"]
    assert [q["question_id"] for q in qs] == ["q1", "q2", "q3"]
    assert sum(q["score"] for q in qs) == 30
    assert qs[0]["options"] is not None and qs[1]["options"] is None


def test_generate_questions_retries_then_succeeds(use_fake_llm):
    bad = LLMResponse(content="", model="fake", tool_calls=[{
        "id": "c1", "name": "submit_questions", "arguments": {"questions": [
            {"type": "选择题", "stem": "", "options": None,
             "answer": "A", "difficulty": "简单", "analysis": ""},
        ]},
    }])  # stem 为空 -> schema 校验失败，触发重试
    good = LLMResponse(content="", model="fake", tool_calls=[{
        "id": "c2", "name": "submit_questions", "arguments": {"questions": [
            {"type": "选择题", "stem": "s1", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
             "answer": "A", "difficulty": "简单", "analysis": ""},
        ]},
    }])
    fake = use_fake_llm([bad, good])
    r = reg.execute("generate_questions", {
        "knowledge_points": ["一元二次方程的定义与一般形式"],
        "count": 1, "question_types": ["选择题"],
    })
    assert "error" not in r
    assert len(fake.calls) == 2  # 第一次校验失败，第二次成功


# ---------- generate_diagnosis ----------

def test_generate_diagnosis_invalid_stats():
    r = reg.execute("generate_diagnosis", {"stats": "not-a-dict"})
    assert "error" in r


def test_generate_diagnosis_valid(use_fake_llm):
    use_fake_llm([LLMResponse(content="", model="fake", tool_calls=[{
        "id": "c2", "name": "submit_diagnosis", "arguments": {
            "summary": "整体中等", "diagnosis": "判别式薄弱", "suggestions": ["加强练习"],
        },
    }])])
    r = reg.execute("generate_diagnosis", {
        "stats": {"class_name": "九三班", "avg": 70},
        "weak_knowledge_points": ["判别式与根的个数"],
    })
    assert "error" not in r
    assert set(r.keys()) == {"summary", "diagnosis", "suggestions"}
