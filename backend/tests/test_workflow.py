"""工作流与任务接口测试（FakeProvider 离线，不依赖真实 API Key / 网络）。"""
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.agent.llm_utils as llm_utils
import app.agent.workflow as wf
from app.agent import planner
from app.agent.llm_client import LLMError, LLMResponse


class FakeLLM:
    """按顺序返回预设响应；耗尽后返回空（无工具调用）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_with_tools(self, messages, tools=None, tool_choice="auto"):
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
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


def _plan_resp(workflow: str, **params) -> LLMResponse:
    return LLMResponse(content="", model="fake", tool_calls=[{
        "id": "p1", "name": "submit_plan",
        "arguments": {"workflow": workflow,
                      "params": {"subject": "数学", "chapter": "一元二次方程", **params}},
    }])


def _diag_resp() -> LLMResponse:
    return LLMResponse(content="", model="fake", tool_calls=[{
        "id": "d1", "name": "submit_diagnosis",
        "arguments": {"summary": "整体中等", "diagnosis": "判别式薄弱",
                      "suggestions": ["加强训练"]},
    }])


def _questions_resp(n: int = 2) -> LLMResponse:
    return LLMResponse(content="", model="fake", tool_calls=[{
        "id": "q1", "name": "submit_questions", "arguments": {"questions": [
            {"type": "选择题", "stem": f"第{i}题", "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
             "answer": "A", "difficulty": "简单", "analysis": ""}
            for i in range(1, n + 1)
        ]},
    }])


def _lesson_resp() -> LLMResponse:
    return LLMResponse(content="", model="fake", tool_calls=[{
        "id": "l1", "name": "submit_lesson_plan", "arguments": {
            "objectives": ["目标1", "目标2", "目标3"],
            "key_points": "重点", "difficult_points": "难点",
            "process": [{"step": "导入", "detail": "复习"}, {"step": "讲授", "detail": "推导"}],
            "homework": "课后题",
        },
    }])


# ---------- 工作流成功路径 ----------

def test_exam_loop_completes(use_fake_llm):
    use_fake_llm([_plan_resp("exam_loop"), _diag_resp()])
    ctx = wf.run_task("请对《一元二次方程》做章节小测并生成学情报告")
    assert ctx.status == "completed", ctx.error
    assert ctx.workflow == "exam_loop"
    assert [a.file_name for a in ctx.artifacts] == ["01_试卷.docx", "02_答题卡.xlsx", "03_学情报告.pdf"]
    for a in ctx.artifacts:
        assert Path(a.path).exists()


def test_question_gen_completes(use_fake_llm):
    use_fake_llm([_plan_resp("question_gen", count=2), _questions_resp(2)])
    ctx = wf.run_task("帮我智能组一套《一元二次方程》的卷子")
    assert ctx.status == "completed", ctx.error
    assert ctx.workflow == "question_gen"
    assert [a.file_name for a in ctx.artifacts] == ["01_试卷.docx", "02_答题卡.xlsx"]


def test_lesson_plan_completes(use_fake_llm):
    use_fake_llm([_plan_resp("lesson_plan"), _lesson_resp()])
    ctx = wf.run_task("帮我写一份《一元二次方程》的教案")
    assert ctx.status == "completed", ctx.error
    assert ctx.workflow == "lesson_plan"
    assert [a.file_name for a in ctx.artifacts] == ["01_教案.docx"]


# ---------- 异常 / 超时 ----------

def test_workflow_fails_gracefully_on_tool_error(use_fake_llm):
    """diagnose 工具失败 → 节点失败、任务 failed，错误信息透出且不抛异常。"""
    use_fake_llm([_plan_resp("exam_loop"), LLMResponse()])  # 第二次为空 → 校验重试耗尽
    ctx = wf.run_task("请对《一元二次方程》做章节小测并生成学情报告")
    assert ctx.status == "failed"
    assert ctx.error
    failed = [s for s in ctx.steps if s.status == "failed"]
    assert failed and failed[0].node == "diagnose"


def test_workflow_timeout(use_fake_llm, monkeypatch):
    """超时上限触发后任务中止，status=failed 且 error 含超时说明。"""
    use_fake_llm([_plan_resp("exam_loop"), _diag_resp()])
    monkeypatch.setattr(wf, "WORKFLOW_TIMEOUT", 0)
    ctx = wf.run_task("请对《一元二次方程》做章节小测并生成学情报告")
    assert ctx.status == "failed"
    assert "秒上限" in ctx.error


def test_planner_fallback_on_llm_failure(monkeypatch):
    """LLM 返回空（未调用工具）→ 规划器确定性回退，不抛异常。"""
    monkeypatch.setattr(llm_utils, "get_llm_service", lambda: FakeLLM([]))
    p = planner.plan("请对《一元二次方程》做一次章节小测并生成学情报告")
    assert p.workflow == "exam_loop"
    assert p.params.chapter == "一元二次方程"


# ---------- 任务接口 ----------

@pytest.fixture
def fake_settings(monkeypatch):
    monkeypatch.setattr("config.settings.DEEPSEEK_API_KEY", "test-key", raising=False)


def test_create_task_ok(use_fake_llm, fake_settings):
    from app.api import tasks

    use_fake_llm([_plan_resp("exam_loop"), _diag_resp()])
    res = tasks.create_task(tasks.TaskRequest(prompt="请对《一元二次方程》做章节小测"))
    assert res["status"] == "completed"
    assert len(res["artifacts"]) == 3
    assert tasks.get_task(res["task_id"])["status"] == "completed"


def test_create_task_empty_prompt(fake_settings):
    from app.api import tasks

    with pytest.raises(HTTPException) as exc:
        tasks.create_task(tasks.TaskRequest(prompt="   "))
    assert exc.value.status_code == 400


def test_get_task_not_found(fake_settings):
    from app.api import tasks

    with pytest.raises(HTTPException) as exc:
        tasks.get_task("no-such-task")
    assert exc.value.status_code == 404
