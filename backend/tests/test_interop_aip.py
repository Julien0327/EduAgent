"""AIP 交互映射单测：TaskCommand → workflow → TaskResult（FakeLLM 离线）。

复刻 test_workflow.py 的 FakeLLM 模式：monkeypatch `app.agent.llm_utils.get_llm_service`，
使 planner / structured_completion 返回预设工具调用，全程不碰真实 API Key / 网络。
"""
import pytest

import app.agent.llm_utils as llm_utils
from app.agent.llm_client import LLMResponse
from interop.aip_server import extract_text, handle_rpc, reset_tasks
from interop.config import AcpSettings
from interop.models import (
    RpcRequest,
    RpcRequestParams,
    TaskCommand,
    TaskCommandType,
    TaskState,
    TextDataItem,
)


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


def _lesson_resp() -> LLMResponse:
    return LLMResponse(content="", model="fake", tool_calls=[{
        "id": "l1", "name": "submit_lesson_plan", "arguments": {
            "objectives": ["目标1", "目标2", "目标3"],
            "key_points": "重点", "difficult_points": "难点",
            "process": [{"step": "导入", "detail": "复习"}, {"step": "讲授", "detail": "推导"}],
            "homework": "课后题",
        },
    }])


def _start_command(prompt: str, task_id: str = "t-abc", session_id: str = "s-1") -> RpcRequest:
    cmd = TaskCommand(
        type="task-command",
        id="cmd-1",
        sentAt="2026-10-10T00:00:00Z",
        senderRole="leader",
        senderId="leader-aic",
        command=TaskCommandType.Start,
        taskId=task_id,
        sessionId=session_id,
        dataItems=[TextDataItem(text=prompt)],
    )
    return RpcRequest(id=1, params=RpcRequestParams(command=cmd))


# ---------- extract_text ----------

def test_extract_text_joins_dataitems():
    cmd = TaskCommand(
        type="task-command", id="c", sentAt="t", senderRole="leader", senderId="s",
        command=TaskCommandType.Start,
        dataItems=[TextDataItem(text="第一段"), TextDataItem(text="第二段")],
    )
    assert extract_text(cmd) == "第一段\n第二段"


# ---------- start → workflow → TaskResult ----------

def test_start_returns_completed_result(use_fake_llm):
    reset_tasks()
    use_fake_llm([_plan_resp("exam_loop"), _diag_resp()])
    resp = handle_rpc(_start_command("请对《一元二次方程》做章节小测并生成学情报告"),
                      AcpSettings())
    assert resp.error is None
    result = resp.result
    assert result.taskId == "t-abc"
    assert result.status.state == TaskState.Completed
    assert result.senderRole == "partner"
    assert len(result.products) == 3
    assert [p.name for p in result.products] == ["试卷", "答题卡", "学情报告"]
    # 产物 URI 指向既有下载端点
    assert result.products[0].dataItems[0].uri.startswith("/api/tasks/")
    assert result.sessionId == "s-1"


def test_start_missing_text_returns_invalid_params(use_fake_llm):
    reset_tasks()
    cmd = TaskCommand(
        type="task-command", id="c", sentAt="t", senderRole="leader", senderId="s",
        command=TaskCommandType.Start,
    )
    resp = handle_rpc(RpcRequest(id=1, params=RpcRequestParams(command=cmd)), AcpSettings())
    assert resp.result is None
    assert resp.error is not None and resp.error.code == -32602


def test_get_returns_stored_result(use_fake_llm):
    reset_tasks()
    use_fake_llm([_plan_resp("lesson_plan"), _lesson_resp()])
    started = handle_rpc(_start_command("帮我写一份《一元二次方程》的教案", task_id="t-xyz"),
                         AcpSettings())
    assert started.result.status.state == TaskState.Completed

    get_cmd = TaskCommand(
        type="task-command", id="c", sentAt="t", senderRole="leader", senderId="s",
        command=TaskCommandType.Get, taskId="t-xyz",
    )
    got = handle_rpc(RpcRequest(id=2, params=RpcRequestParams(command=get_cmd)), AcpSettings())
    assert got.result.taskId == "t-xyz"
    assert got.result.products[0].name == "教案"


def test_get_unknown_task_returns_not_found(use_fake_llm):
    reset_tasks()
    get_cmd = TaskCommand(
        type="task-command", id="c", sentAt="t", senderRole="leader", senderId="s",
        command=TaskCommandType.Get, taskId="no-such",
    )
    resp = handle_rpc(RpcRequest(id=3, params=RpcRequestParams(command=get_cmd)), AcpSettings())
    assert resp.error is not None and resp.error.code == -32001


def test_unimplemented_command_returns_method_not_found(use_fake_llm):
    reset_tasks()
    cmd = TaskCommand(
        type="task-command", id="c", sentAt="t", senderRole="leader", senderId="s",
        command=TaskCommandType.Cancel, taskId="t-1",
    )
    resp = handle_rpc(RpcRequest(id=4, params=RpcRequestParams(command=cmd)), AcpSettings())
    assert resp.error is not None and resp.error.code == -32601
