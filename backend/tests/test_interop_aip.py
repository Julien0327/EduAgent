"""AIP RPC 入口测试：走官方 acps_sdk 的 handle_rpc_request（离线，FakeLLM）。

用 httpx.ASGITransport 直接调用 FastAPI 应用内的 /rpc（进程内 ASGI，不联网），
验证「TaskCommand → 官方 SDK 分发 → on_start → 现有 workflow → 官方 TaskResult」整条链路。
FakeLLM 模式：monkeypatch `app.agent.llm_utils.get_llm_service`，不碰真实 API Key / 网络。
"""
import asyncio

import httpx
import pytest
from fastapi import FastAPI

import app.agent.llm_utils as llm_utils
from app.agent.llm_client import LLMResponse
from interop.aip_server import create_aip_router, extract_text, reset_tasks
from interop.config import AcpSettings


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


@pytest.fixture(autouse=True)
def _clean_tasks():
    reset_tasks()
    yield
    reset_tasks()


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


# ---------- 测试辅助 ----------

def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_aip_router(AcpSettings()))
    return app


def _post(app: FastAPI, body: dict) -> httpx.Response:
    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/rpc", json=body)

    return asyncio.run(_run())


def _start_body(prompt: str, task_id: str = "t-abc", session_id: str = "s-1") -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "rpc",
        "id": 1,
        "params": {"command": {
            "type": "task-command",
            "id": "cmd-1",
            "sentAt": "2026-10-10T00:00:00Z",
            "senderRole": "leader",
            "senderId": "leader-aic",
            "command": "start",
            "taskId": task_id,
            "sessionId": session_id,
            "dataItems": [{"type": "text", "text": prompt}],
        }},
    }


def _cmd_body(command: str, task_id: str, rpc_id: int = 2) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "rpc",
        "id": rpc_id,
        "params": {"command": {
            "type": "task-command",
            "id": "cmd-2",
            "sentAt": "2026-10-10T00:00:00Z",
            "senderRole": "leader",
            "senderId": "leader-aic",
            "command": command,
            "taskId": task_id,
        }},
    }


# ---------- extract_text ----------

def test_extract_text_joins_dataitems():
    from acps_sdk.aip.aip_base_model import TaskCommand, TaskCommandType, TextDataItem

    cmd = TaskCommand(
        type="task-command", id="c", sentAt="t", senderRole="leader", senderId="s",
        command=TaskCommandType.Start,
        dataItems=[TextDataItem(text="第一段"), TextDataItem(text="第二段")],
    )
    assert extract_text(cmd) == "第一段\n第二段"


# ---------- start → workflow → TaskResult（三项教育技能） ----------

def test_start_exam_loop_completed(use_fake_llm):
    use_fake_llm([_plan_resp("exam_loop"), _diag_resp()])
    resp = _post(_make_app(), _start_body("请对《一元二次方程》做章节小测并生成学情报告"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["error"] is None
    result = data["result"]
    assert result["taskId"] == "t-abc"
    assert result["status"]["state"] == "completed"
    assert result["senderRole"] == "partner"
    assert result["senderId"] == "EduMate-AIP"
    assert result["sessionId"] == "s-1"
    assert [p["name"] for p in result["products"]] == ["试卷", "答题卡", "学情报告"]
    uri = result["products"][0]["dataItems"][0]["uri"]
    assert uri.startswith("http://") or uri.startswith("https://")
    assert "/api/tasks/" in uri
    assert uri.endswith("/files/01_%E8%AF%95%E5%8D%B7.docx")


def test_start_question_gen_completed(use_fake_llm):
    use_fake_llm([_plan_resp("question_gen", count=2), _questions_resp(2)])
    resp = _post(_make_app(), _start_body("帮我智能组一套《一元二次方程》的卷子", task_id="t-qg"))
    data = resp.json()
    assert data["result"]["status"]["state"] == "completed"
    assert [p["name"] for p in data["result"]["products"]] == ["试卷", "答题卡"]


def test_start_lesson_plan_completed(use_fake_llm):
    use_fake_llm([_plan_resp("lesson_plan"), _lesson_resp()])
    resp = _post(_make_app(), _start_body("帮我写一份《一元二次方程》的教案", task_id="t-lp"))
    data = resp.json()
    assert data["result"]["status"]["state"] == "completed"
    assert [p["name"] for p in data["result"]["products"]] == ["教案"]


def test_start_missing_text_returns_failed():
    body = _start_body("请出卷")
    del body["params"]["command"]["dataItems"]
    resp = _post(_make_app(), body)
    data = resp.json()
    assert data["error"] is None
    assert data["result"]["status"]["state"] == "failed"
    assert "缺少文本" in data["result"]["status"]["dataItems"][0]["text"]


# ---------- get / 未知任务 / 未支持命令 ----------

def test_get_returns_stored_result(use_fake_llm):
    use_fake_llm([_plan_resp("lesson_plan"), _lesson_resp()])
    app = _make_app()
    started = _post(app, _start_body("帮我写一份《一元二次方程》的教案", task_id="t-xyz")).json()
    assert started["result"]["status"]["state"] == "completed"

    got = _post(app, _cmd_body("get", "t-xyz")).json()
    assert got["result"]["taskId"] == "t-xyz"
    assert got["result"]["products"][0]["name"] == "教案"


def test_get_unknown_task_returns_not_found():
    resp = _post(_make_app(), _cmd_body("get", "no-such"))
    data = resp.json()
    assert data["error"] is not None and data["error"]["code"] == -32001


def test_unknown_command_returns_invalid_params(use_fake_llm):
    use_fake_llm([_plan_resp("exam_loop"), _diag_resp()])
    app = _make_app()
    _post(app, _start_body("请对《一元二次方程》做章节小测"))  # 建任务 t-abc

    resp = _post(app, _cmd_body("re-stream", "t-abc", rpc_id=3))
    data = resp.json()
    assert data["error"] is not None and data["error"]["code"] == -32602
