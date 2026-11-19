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
import ast
import json
import re
import time
from typing import Type

from pydantic import BaseModel, ValidationError

from backend.app.agent.llm_client import LLMError, get_llm_service
from backend.logging_config import get_logger

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


def _call_with_retry(llm, messages, submit_tool, tool_choice, retries=2,
                     deadline: float | None = None):
    """传输层：LLM 调用失败（超时/网络/限流）按指数退避重试 retries 次。

    ``deadline``（Phase 5 / S4 预算）：time.monotonic() 绝对截止点；到点不再发起
    新请求（防单次结构化输出在重试中无限拖长）。默认 None=不限制（向后兼容）。
    """
    for i in range(retries + 1):
        if deadline is not None and time.monotonic() >= deadline:
            raise LLMError("结构化输出超出时间预算")
        try:
            return llm.chat_with_tools(messages, [submit_tool], tool_choice=tool_choice)
        except LLMError as exc:
            _log.warning("LLM 调用失败（第 %d/%d 次）：%s", i + 1, retries + 1, exc)
            if i == retries:
                raise
            time.sleep(2 ** i)  # 1s、2s


def _quote_unquoted_field(raw: str, field: str) -> str | None:
    """保守修复：把 ``"field": <未加引号的值>, "<下一个键>":`` 中的值加引号。

    真实复现（Phase 5 / S4）：模型把整段中文理由写进 ``"reasons"`` 却漏了引号，
    导致工具参数不是合法 JSON。只对**指定字段 + 后面紧跟下一个键**的模式生效，
    不尝试通用 JSON 修复（避免误改）。
    """
    pattern = (rf'("{re.escape(field)}"\s*:\s*)([^"\[\{{\s][^,]*?)'
               r'(\s*,\s*"[A-Za-z_][\w]*"\s*:)')
    m = re.search(pattern, raw, re.DOTALL)
    if not m:
        return None
    value = m.group(2).strip()
    if not value:
        return None
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return raw[: m.start()] + m.group(1) + '"' + escaped + '"' + m.group(3) + raw[m.end():]


def _parse_args_text(text: str) -> dict | None:
    """解析工具参数字符串：JSON → Python 字面量 → 提取 {...} → 指定字段引号修复。"""
    text = (text or "").strip()
    if not text:
        return None
    candidates = [text]
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m and m.group(0) != text:
        candidates.append(m.group(0))
    for cand in candidates:
        for parser in (json.loads, ast.literal_eval):
            try:
                value = parser(cand)
                if isinstance(value, dict):
                    return value
            except Exception:
                pass
    # 安全网：模型偶发漏引号（如 "reasons": 一大段中文, "difficulty_fit": ...）
    for cand in candidates:
        for field in ("reasons", "reason"):
            fixed = _quote_unquoted_field(cand, field)
            if not fixed:
                continue
            try:
                value = json.loads(fixed)
                if isinstance(value, dict):
                    return value
            except Exception:
                pass
    return None


def _extract_arguments(resp, submit_name: str) -> dict | None:
    """优先取工具调用参数；空/非法参数会尝试修复，再回退 content 文本解析。

    Phase 5 / S4 真实复现：模型调用 submit_* 时可能：
    - 提交非法 JSON（长文本漏引号）→ provider 保留 ``arguments_raw``，这里修复；
    - 提交空参数 → 继续尝试 content 中的 JSON。
    全部失败返回 None（调用方按"未提交/非法"重试）。
    """
    for tc in resp.tool_calls:
        if tc.get("name") == submit_name:
            args = tc.get("arguments")
            if isinstance(args, dict) and args:
                return args
            raw = tc.get("arguments_raw")
            if raw:
                repaired = _parse_args_text(str(raw))
                if repaired:
                    return repaired
            break   # 空/非法 → 走 content 回退
    return _parse_args_text(resp.content or "")


def _structured_completion_impl(
    system: str,
    user: str,
    submit_name: str,
    submit_model: Type[BaseModel],
    max_attempts: int = 3,
    history: list[dict] | None = None,
    deadline: float | None = None,
    return_content: bool = False,
):
    llm = get_llm_service()
    submit_tool = model_to_tool_schema(submit_name, "提交结构化结果", submit_model)
    tool_choice = _tool_choice(submit_name)
    messages = [{"role": "system", "content": system}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user})

    last_err = "未知错误"
    required = submit_model.model_json_schema().get("required") or []
    for _ in range(max_attempts):
        if deadline is not None and time.monotonic() >= deadline:
            raise LLMError("结构化输出超出时间预算")
        resp = _call_with_retry(llm, messages, submit_tool, tool_choice,
                                deadline=deadline)
        args = _extract_arguments(resp, submit_name)
        if args is None:
            last_err = ("模型未按要求调用提交工具，或工具参数不是合法 JSON"
                        "（不得为空、不得漏引号）")
            messages.append(
                {"role": "user",
                 "content": f"请调用 {submit_name} 工具并提交**合法 JSON** 参数"
                            f"（参数不得为空；字符串值必须加双引号）。上次问题：{last_err}"}
            )
            continue
        try:
            model = submit_model.model_validate(args)
            return (model, resp.content or "") if return_content else model
        except ValidationError as exc:
            last_err = str(exc.errors())
            messages.append(
                {
                    "role": "user",
                    "content": f"上次输出不符合 schema，请补齐必填字段 {required} 后重新调用 "
                               f"{submit_name}；参数必须是合法 JSON、不得为空。校验错误：{last_err}",
                }
            )

    raise LLMError(f"结构化输出在 {max_attempts} 次尝试后仍不合法：{last_err}")


def structured_completion(
    system: str,
    user: str,
    submit_name: str,
    submit_model: Type[BaseModel],
    max_attempts: int = 3,
    history: list[dict] | None = None,
    deadline: float | None = None,
) -> BaseModel:
    """强制 LLM 以 submit_name 工具调用返回结构化参数，校验通过后返回模型实例。

    history（Phase 2 / S5，§7.1）：多轮上下文（最近 N 轮、已截断），插在
    system 与本轮 user 之间。
    deadline（Phase 5 / S4 预算）：time.monotonic() 绝对截止点；到点即停止重试并
    抛 LLMError（调用方据此降级）。默认 None=不限制（向后兼容）。
    """
    return _structured_completion_impl(
        system, user, submit_name, submit_model, max_attempts, history,
        deadline, return_content=False)


def structured_completion_with_content(
    system: str,
    user: str,
    submit_name: str,
    submit_model: Type[BaseModel],
    max_attempts: int = 3,
    history: list[dict] | None = None,
    deadline: float | None = None,
) -> tuple[BaseModel, str]:
    """同 structured_completion，但额外返回模型的正文 content（用于把长理由移出参数）。

    Phase 5 / S4：质检复核把"理由"放在 content，工具参数只保留短字段，避免模型
    在 JSON 中输出未加引号的长文本。
    """
    return _structured_completion_impl(
        system, user, submit_name, submit_model, max_attempts, history,
        deadline, return_content=True)
