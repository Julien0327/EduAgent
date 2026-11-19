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
    # 测试模型允许 string/list 两种形态：真实修复样本中 reasons 是被修复成字符串的
    reasons: list[str] | str = []


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
    """连续 3 次空参数 → LLMError（不伪造通过）；错误文本可区分"非法 JSON"。"""
    fake = _install(monkeypatch, [_resp({}), _resp({}), _resp({})])
    with pytest.raises(LLMError) as exc:
        llmu.structured_completion("s", "u", "submit_verify", Verdict)
    assert "合法 JSON" in str(exc.value)
    assert len(fake.calls) == 3


def test_validation_error_retry_mentions_required_fields(monkeypatch):
    """schema 不匹配：重试提示包含必填字段（verdict）与「不得为空」。"""
    fake = _install(monkeypatch, [_resp({"verdict": "maybe"}),
                                  _resp({"verdict": "maybe"}),
                                  _resp({"verdict": "maybe"})])
    with pytest.raises(LLMError):
        llmu.structured_completion("s", "u", "submit_verify", Verdict)
    retry_prompt = fake.calls[1][-1]["content"]
    assert "verdict" in retry_prompt and "不得为空" in retry_prompt
    assert len(fake.calls) == 3


def test_no_tool_call_content_json_still_supported(monkeypatch):
    """原有兜底：无工具调用、content 为 JSON → 正常解析。"""
    _install(monkeypatch, [_resp(None, '{"verdict":"fail","reasons":["答案错误"]}')])
    out = llmu.structured_completion("s", "u", "submit_verify", Verdict)
    assert out.verdict == "fail"
    assert out.reasons == ["答案错误"]


# ===================== Phase 5 / S4：非法 JSON 修复（真实捕获样本） =====================
_REAL_BAD_ARGS = (
    '{"verdict": "pass", "reasons": 计算正确：366÷7=52……2（52×7=364，余2），'
    '故366天=52个星期零2天，答案A与解析一致；2024年为闰年、366天表述无误；'
    '干扰项设置合理。, "difficulty_fit": true, "suggested_difficulty": "简单"}'
)


def test_repair_unquoted_reasons_from_real_capture():
    """真实捕获的非法 JSON（reasons 漏引号）→ 保守修复后解析成功。"""
    parsed = llmu._parse_args_text(_REAL_BAD_ARGS)
    assert parsed is not None
    assert parsed["verdict"] == "pass"
    assert "计算正确" in parsed["reasons"]
    assert parsed["difficulty_fit"] is True


def test_repair_python_literal_arguments():
    """Python 字面量（单引号 / True）→ ast.literal_eval 解析。"""
    parsed = llmu._parse_args_text("{'verdict': 'fail', 'difficulty_fit': True}")
    assert parsed == {"verdict": "fail", "difficulty_fit": True}


def test_malformed_raw_arguments_recovered_via_provider_field(monkeypatch):
    """工具参数解析失败但 arguments_raw 保留 → structured_completion 修复并成功。"""
    resp = LLMResponse(content="", model="fake", tool_calls=[{
        "id": "x", "name": "submit_verify", "arguments": {},
        "arguments_raw": _REAL_BAD_ARGS,
    }])
    _install(monkeypatch, [resp])
    out = llmu.structured_completion("s", "u", "submit_verify", Verdict)
    assert out.verdict == "pass"


def test_structured_completion_with_content_returns_both(monkeypatch):
    """新入口同时返回结构化结果与正文 content（理由走 content 的结构性修复基础）。"""
    _install(monkeypatch, [_resp({"verdict": "fail"}, content="答案计算错误")])
    model, content = llmu.structured_completion_with_content(
        "s", "u", "submit_verify", Verdict)
    assert model.verdict == "fail"
    assert content == "答案计算错误"


def test_provider_preserves_arguments_raw(monkeypatch):
    """provider 解析失败时不丢弃原文：arguments_raw 必须保留。"""
    from backend.app.agent.llm_client import DeepSeekProvider

    class _Fn:
        name = "submit_verify"
        arguments = _REAL_BAD_ARGS

    class _TC:
        id = "x"
        function = _Fn()

    class _Msg:
        content = ""
        tool_calls = [_TC()]

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    p = DeepSeekProvider(api_key="x", base_url="http://x", model="m", timeout=1)
    monkeypatch.setattr(p, "_request", lambda *a, **k: _Resp())
    out = p.chat_with_tools([], [])
    assert out.tool_calls[0]["arguments"] == {}
    assert out.tool_calls[0]["arguments_raw"] == _REAL_BAD_ARGS
