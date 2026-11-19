"""structured_completion 空工具参数恢复测试（Phase 5 / S4，零 API）。

真实复现：DeepSeek 偶尔调用 submit_* 但提交 `{}`（缺 verdict），导致 3 次重试后
降级 unverified。修复策略（不放宽 schema）：
- 空参数不直接返回，回退解析 content 中的 JSON；
- 重试提示明确「参数不得为空 + 必填字段」；
- 连续空参数仍失败 → LLMError（保持诚实降级）。
"""
from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel

import backend.app.agent.llm_utils as llmu
from backend.app.agent.llm_client import LLMError, LLMResponse


class Verdict(BaseModel):
    verdict: Literal["pass", "fail"]
    reasons: list[str] = []


class SeqLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[list] = []

    def chat_with_tools(self, messages, tools=None, tool_choice="auto"):
        self.calls.append(messages)
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(content="", tool_calls=[], model="fake")


def _resp(args: dict | None = None, content: str = "") -> LLMResponse:
    tcs = [] if args is None else [{"id": "x", "name": "submit_verify", "arguments": args}]
    return LLMResponse(content=content, tool_calls=tcs, model="fake")


def _install(monkeypatch, responses) -> SeqLLM:
    fake = SeqLLM(responses)
    monkeypatch.setattr(llmu, "get_llm_service", lambda: fake)
    return fake


def test_empty_tool_args_falls_back_to_content_json(monkeypatch):
    """工具参数为空但 content 含合法 JSON → 从文本恢复，不浪费重试。"""
    _install(monkeypatch, [_resp({}, '{"verdict":"pass","reasons":[]}')])
    out = llmu.structured_completion("s", "u", "submit_verify", Verdict)
    assert out.verdict == "pass"


def test_empty_tool_args_retries_then_succeeds(monkeypatch):
    """第一次空参数、第二次合法 → 第二次成功；重试提示含「参数不得为空」。"""
    fake = _install(monkeypatch, [_resp({}), _resp({"verdict": "pass"})])
    out = llmu.structured_completion("s", "u", "submit_verify", Verdict)
    assert out.verdict == "pass"
    assert len(fake.calls) == 2
    retry_prompt = fake.calls[1][-1]["content"]
    assert "参数不得为空" in retry_prompt


def test_empty_tool_args_three_times_raises(monkeypatch):
    """连续 3 次空参数 → LLMError（不伪造通过）。"""
    fake = _install(monkeypatch, [_resp({}), _resp({}), _resp({})])
    with pytest.raises(LLMError) as exc:
        llmu.structured_completion("s", "u", "submit_verify", Verdict)
    assert "空参数" in str(exc.value)
    assert len(fake.calls) == 3


def test_validation_error_retry_mentions_required_fields(monkeypatch):
    """schema 不匹配：重试提示包含必填字段（verdict）与「参数不得为空」。"""
    fake = _install(monkeypatch, [_resp({"verdict": "maybe"}),
                                  _resp({"verdict": "maybe"}),
                                  _resp({"verdict": "maybe"})])
    with pytest.raises(LLMError):
        llmu.structured_completion("s", "u", "submit_verify", Verdict)
    retry_prompt = fake.calls[1][-1]["content"]
    assert "verdict" in retry_prompt and "参数不得为空" in retry_prompt
    assert len(fake.calls) == 3


def test_no_tool_call_content_json_still_supported(monkeypatch):
    """原有兜底：无工具调用、content 为 JSON → 正常解析。"""
    _install(monkeypatch, [_resp(None, '{"verdict":"fail","reasons":["答案错误"]}')])
    out = llmu.structured_completion("s", "u", "submit_verify", Verdict)
    assert out.verdict == "fail"
    assert out.reasons == ["答案错误"]
