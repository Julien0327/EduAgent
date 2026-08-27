"""工作流与任务接口测试（FakeProvider 离线，不依赖真实 API Key / 网络）。"""
import json
import time
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


def test_workflow_emits_events(use_fake_llm):
    """工作流执行过程中向事件队列推送 step/status/artifact 事件（D4 前置）。"""
    use_fake_llm([_plan_resp("exam_loop"), _diag_resp()])
    ctx = wf.run_task("请对《一元二次方程》做章节小测并生成学情报告")
    assert ctx.status == "completed"
    events = []
    while not ctx.events.empty():
        events.append(ctx.events.get_nowait())
    types = [e["type"] for e in events]
    assert types[0] == "status" and events[0]["status"] == "running"
    assert "step" in types and "artifact" in types
    assert types[-1] == "status" and events[-1]["status"] == "completed"
    nodes = [e["node"] for e in events if e["type"] == "step"]
    assert nodes.count("understand_task") == 2  # running + done
    names = [e["file_name"] for e in events if e["type"] == "artifact"]
    assert names == ["01_试卷.docx", "02_答题卡.xlsx", "03_学情报告.pdf"]


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
    assert res["status"] == "running"
    # 异步执行：轮询至终态（后台线程瞬时完成）
    cur = res
    for _ in range(200):
        cur = tasks.get_task(res["task_id"])
        if cur["status"] in ("completed", "failed"):
            break
        time.sleep(0.01)
    assert cur["status"] == "completed"
    assert len(cur["artifacts"]) == 3


def test_task_events_stream(use_fake_llm, fake_settings):
    """SSE 事件流：hello 快照 → step/artifact/status → done。"""
    from app.api import tasks
    from app.agent.context import task_store

    use_fake_llm([_plan_resp("exam_loop"), _diag_resp()])
    res = tasks.create_task(tasks.TaskRequest(prompt="请对《一元二次方程》做章节小测"))
    for _ in range(200):
        cur = tasks.get_task(res["task_id"])
        if cur["status"] in ("completed", "failed"):
            break
        time.sleep(0.01)
    assert cur["status"] == "completed"

    ctx = task_store.get(res["task_id"])
    events = [
        json.loads(frame[len("data: "):])
        for frame in tasks._event_stream(ctx)
        if frame.startswith("data: ")
    ]
    types = [e["type"] for e in events]
    assert types[0] == "hello"
    assert types[-1] == "done"
    assert "step" in types and "artifact" in types and "status" in types
    assert any(e["type"] == "status" and e["status"] == "completed" for e in events)


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


def _wait_completed(tasks, task_id):
    """轮询任务直至终态（异步后台线程 + FakeLLM 瞬时完成）。"""
    cur = None
    for _ in range(200):
        cur = tasks.get_task(task_id)
        if cur["status"] in ("completed", "failed"):
            return cur
        time.sleep(0.01)
    return cur


def test_download_file(use_fake_llm, fake_settings):
    from app.api import tasks
    from fastapi.responses import FileResponse

    use_fake_llm([_plan_resp("exam_loop"), _diag_resp()])
    res = tasks.create_task(tasks.TaskRequest(prompt="请对《一元二次方程》做章节小测"))
    cur = _wait_completed(tasks, res["task_id"])
    assert cur["status"] == "completed"

    resp = tasks.download_file(res["task_id"], "01_试卷.docx")
    assert isinstance(resp, FileResponse)
    assert resp.filename == "01_试卷.docx"
    assert Path(resp.path).exists()


def test_download_file_not_found(use_fake_llm, fake_settings):
    from app.api import tasks

    use_fake_llm([_plan_resp("exam_loop"), _diag_resp()])
    res = tasks.create_task(tasks.TaskRequest(prompt="请对《一元二次方程》做章节小测"))
    _wait_completed(tasks, res["task_id"])
    with pytest.raises(HTTPException) as exc:
        tasks.download_file(res["task_id"], "不存在.docx")
    assert exc.value.status_code == 404


def test_preview_exam_and_answer_sheet(use_fake_llm, fake_settings):
    from app.api import tasks
    from fastapi.responses import HTMLResponse

    use_fake_llm([_plan_resp("exam_loop"), _diag_resp()])
    res = tasks.create_task(tasks.TaskRequest(prompt="请对《一元二次方程》做章节小测"))
    _wait_completed(tasks, res["task_id"])

    exam = tasks.preview_task(res["task_id"], "exam")
    assert isinstance(exam, HTMLResponse)
    assert "班级" in exam.body.decode("utf-8")

    sheet = tasks.preview_task(res["task_id"], "answer_sheet")
    assert isinstance(sheet, HTMLResponse)
    assert "标准答案" in sheet.body.decode("utf-8")


def test_preview_lesson(use_fake_llm, fake_settings):
    from app.api import tasks
    from fastapi.responses import HTMLResponse

    use_fake_llm([_plan_resp("lesson_plan"), _lesson_resp()])
    res = tasks.create_task(tasks.TaskRequest(prompt="帮我写一份《一元二次方程》的教案"))
    cur = _wait_completed(tasks, res["task_id"])
    assert cur["status"] == "completed"

    lesson = tasks.preview_task(res["task_id"], "lesson")
    assert isinstance(lesson, HTMLResponse)
    body = lesson.body.decode("utf-8")
    assert "教学目标" in body and "教学过程" in body


def test_preview_unknown_kind(use_fake_llm, fake_settings):
    from app.api import tasks

    use_fake_llm([_plan_resp("exam_loop"), _diag_resp()])
    res = tasks.create_task(tasks.TaskRequest(prompt="请对《一元二次方程》做章节小测"))
    _wait_completed(tasks, res["task_id"])
    with pytest.raises(HTTPException) as exc:
        tasks.preview_task(res["task_id"], "nope")
    assert exc.value.status_code == 404
