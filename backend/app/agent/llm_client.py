"""LLM 服务抽象层。

业务层统一通过 LLMService 调用模型，不直接依赖具体厂商：
    业务层 → LLMService → DeepSeekProvider

当前仅实现 DeepSeekProvider（OpenAI 兼容 API）；
后续可扩展 QwenProvider（多模态）。使用官方 OpenAI Python SDK 调用，
不引入 LangChain / LangGraph 等重型框架。

D3 扩展：新增 chat_with_tools 支持 Function Calling（多轮消息 + 工具调用），
供 Agent 的 Planner / Executor 使用；原有 chat(message) 保持不变，供 /api/chat 使用。
"""
import json
from dataclasses import dataclass, field
from functools import lru_cache

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from config import settings


class LLMError(Exception):
    """LLM 调用过程中的可预期错误（消息对用户友好，不含敏感信息）。"""


@dataclass
class LLMResponse:
    """一次 LLM 对话的结构化结果。

    content：模型文本回复（纯工具调用时可能为空字符串）。
    tool_calls：模型请求的工具调用列表，每项为 {"id", "name", "arguments"}，
                arguments 已解析为 dict。
    """

    content: str = ""
    tool_calls: list = field(default_factory=list)
    model: str = ""


class LLMProvider:
    """LLM 厂商抽象接口。"""

    def chat(self, message: str) -> dict:
        """发送一条用户消息，返回 {"message": str, "model": str}。"""
        raise NotImplementedError

    def chat_with_tools(
        self, messages: list, tools: list | None = None, tool_choice="auto"
    ) -> LLMResponse:
        """多轮对话（支持工具调用），返回 LLMResponse。

        tool_choice：默认 "auto"；传 {"type": "function", "function": {"name": ...}}
        可强制模型调用指定工具（用于结构化输出）。
        """
        raise NotImplementedError


class DeepSeekProvider(LLMProvider):
    """DeepSeek 实现（OpenAI 兼容 API）。"""

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float):
        self._model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def _request(self, messages: list, tools: list | None = None, tool_choice="auto"):
        """统一发起请求，并把可预期异常转成 LLMError。"""
        kwargs: dict = {"model": self._model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        try:
            return self._client.chat.completions.create(**kwargs)
        except AuthenticationError as exc:
            raise LLMError("API Key 无效或鉴权失败，请检查 DEEPSEEK_API_KEY") from exc
        except APITimeoutError as exc:
            raise LLMError("模型请求超时，请稍后重试") from exc
        except APIConnectionError as exc:
            raise LLMError("无法连接模型服务，请检查网络") from exc
        except RateLimitError as exc:
            raise LLMError("请求过于频繁，请稍后重试") from exc
        except APIError as exc:
            status = getattr(exc, "status_code", None)
            raise LLMError(f"模型服务返回错误（{status}）") from exc

    def chat(self, message: str) -> dict:
        resp = self._request([{"role": "user", "content": message}])
        content = resp.choices[0].message.content or ""
        return {"message": content, "model": self._model}

    def chat_with_tools(
        self, messages: list, tools: list | None = None, tool_choice="auto"
    ) -> LLMResponse:
        resp = self._request(messages, tools, tool_choice)
        message = resp.choices[0].message
        content = message.content or ""
        tool_calls = []
        for tc in message.tool_calls or []:
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                {"id": tc.id, "name": tc.function.name, "arguments": arguments}
            )
        return LLMResponse(content=content, tool_calls=tool_calls, model=self._model)


class LLMService:
    """业务层统一入口：封装 provider，隔离具体厂商。"""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def chat(self, message: str) -> dict:
        return self._provider.chat(message)

    def chat_with_tools(
        self, messages: list, tools: list | None = None, tool_choice="auto"
    ) -> LLMResponse:
        return self._provider.chat_with_tools(messages, tools, tool_choice)


@lru_cache(maxsize=1)
def get_llm_service() -> LLMService:
    """构建并缓存 LLMService 单例（当前使用 DeepSeekProvider）。"""
    provider = DeepSeekProvider(
        api_key=settings.DEEPSEEK_API_KEY or "",
        base_url=settings.DEEPSEEK_BASE_URL,
        model=settings.DEEPSEEK_MODEL,
        timeout=settings.LLM_TIMEOUT,
    )
    return LLMService(provider)
