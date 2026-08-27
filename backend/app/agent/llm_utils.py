"""LLM 结构化输出工具函数。

通过让模型调用一个合成的 submit_* 工具来返回结构化参数（对应技术方案 §7：
"不依赖 LLM 直接吐 JSON 文本，而是用工具调用返回结构化参数"）。

当前 DeepSeek 模型为「思考模式」，不支持强制指定工具（tool_choice 传
{"type":"function",...} 会报 400），故用 tool_choice="auto"，靠系统提示词
要求调用 submit_* 工具，并在 _extract_arguments 中把纯 JSON 文本作为兜底解析。

structured_completion 统一处理两类重试（对应技术方案 §22）：
    - 传输层：LLM 超时 / 网络 / 限流，指数退避重试 2 次；
    - 校验层：结构化输出不符合 Pydantic schema，把校验错误回喂模型，最多 3 次尝试。
"""
import json
import re
import time
from typing import Type

from pydantic import BaseModel, ValidationError

from app.agent.llm_client import LLMError, get_llm_service
from logging_config import get_logger

_log = get_logger("app.agent.llm_utils")


def model_to_tool_schema(name: str, description: str, model: Type[BaseModel]) -> dict:
    """把 Pydantic 模型转成 OpenAI Function Calling 的单个 tool 描述。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }


def _tool_choice(name: str) -> str:
    """结构化输出用的 tool_choice。

    DeepSeek 思考模式不支持强制指定工具（传 {"type":"function",...} 会报 400
    「Thinking mode does not support this tool_choice」），因此用 "auto"：
    仅提交一个 submit_* 工具 + 系统提示词要求调用，模型几乎总会调用该工具；
    未调用时由 _extract_arguments 兜底解析纯 JSON 文本。
    """
    return "auto"


def _call_with_retry(llm, messages, submit_tool, tool_choice, retries=2):
    """传输层：LLM 调用失败（超时/网络/限流）按指数退避重试 retries 次。"""
    for i in range(retries + 1):
        try:
            return llm.chat_with_tools(messages, [submit_tool], tool_choice=tool_choice)
        except LLMError as exc:
            _log.warning("LLM 调用失败（第 %d/%d 次）：%s", i + 1, retries + 1, exc)
            if i == retries:
                raise
            time.sleep(2 ** i)  # 1s、2s


def _extract_arguments(resp, submit_name: str) -> dict | None:
    """优先取工具调用参数；模型未调用工具时从 content 文本中兜底解析 JSON。"""
    for tc in resp.tool_calls:
        if tc["name"] == submit_name:
            return tc["arguments"]
    text = (resp.content or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 纯文本可能夹带 JSON（如「结果如下：{...}」），提取首个 {...} 对象再解析
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def structured_completion(
    system: str,
    user: str,
    submit_name: str,
    submit_model: Type[BaseModel],
    max_attempts: int = 3,
) -> BaseModel:
    """强制 LLM 以 submit_name 工具调用返回结构化参数，校验通过后返回模型实例。"""
    llm = get_llm_service()
    submit_tool = model_to_tool_schema(submit_name, "提交结构化结果", submit_model)
    tool_choice = _tool_choice(submit_name)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    last_err = "未知错误"
    for _ in range(max_attempts):
        resp = _call_with_retry(llm, messages, submit_tool, tool_choice)
        args = _extract_arguments(resp, submit_name)
        if args is None:
            last_err = "模型未按要求调用提交工具"
            messages.append(
                {"role": "user", "content": f"请调用 {submit_name} 工具提交结果。上次问题：{last_err}"}
            )
            continue
        try:
            return submit_model.model_validate(args)
        except ValidationError as exc:
            last_err = str(exc.errors())
            messages.append(
                {
                    "role": "user",
                    "content": f"上次输出不符合 schema，请修正后重新调用 {submit_name}。校验错误：{last_err}",
                }
            )

    raise LLMError(f"结构化输出在 {max_attempts} 次尝试后仍不合法：{last_err}")
