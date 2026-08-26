"""LLM 服务抽象层。

业务层统一通过 LLMService 调用模型，不直接依赖具体厂商：
    业务层 → LLMService → DeepSeekProvider

当前仅实现 DeepSeekProvider（OpenAI 兼容 API）；
后续可扩展 QwenProvider（多模态）。使用官方 OpenAI Python SDK 调用，
不引入 LangChain / LangGraph 等重型框架。
"""
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


class LLMProvider:
    """LLM 厂商抽象接口。"""

    def chat(self, message: str) -> dict:
        """发送一条用户消息，返回 {"message": str, "model": str}。"""
        raise NotImplementedError


class DeepSeekProvider(LLMProvider):
    """DeepSeek 实现（OpenAI 兼容 API）。"""

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float):
        self._model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def chat(self, message: str) -> dict:
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": message}],
            )
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

        content = resp.choices[0].message.content or ""
        return {"message": content, "model": self._model}


class LLMService:
    """业务层统一入口：封装 provider，隔离具体厂商。"""

    def __init__(self, provider: LLMProvider):
        self._provider = provider

    def chat(self, message: str) -> dict:
        return self._provider.chat(message)


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
