"""LLM 调用客户端抽象（Step 5-B1）。

隔离原则：执行器与测试只依赖 ChatClient 协议，不读取任何环境变量/API Key；
真实 Key 仅在 OpenAICompatClient.from_settings()（CLI 装配层）读取——只判存在、
不打印、不入日志（5-B1 交付要求：预算控制与 API 调用隔离）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from backend.logging_config import get_logger

log = get_logger("annotation.client")

RETRY_MAX = 5
BACKOFF_STEPS = (1, 2, 4, 8, 16)  # 指数退避秒数（含调用方抖动）


@dataclass(frozen=True)
class Usage:
    """一次调用的 token 用量（DeepSeek OpenAI 兼容 usage 字段）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0  # 前缀缓存命中部分（缺失按 0 = 全未命中，预算最坏口径）

    @property
    def cache_miss_tokens(self) -> int:
        return max(0, self.prompt_tokens - self.cache_hit_tokens)


@dataclass(frozen=True)
class ChatResult:
    """一次调用的结果：模型文本、响应中的模型版本快照、usage。"""

    content: str
    model_version: str
    usage: Usage


class RetryableError(Exception):
    """可重试错误：429 / 超时 / 连接失败 / 5xx。

    throttled=True 表示限流（HTTP 429）——AIMD 并发控制据此**降并发**（决策②实现，
    第二版修订：429 永远是降并发的信号）。
    """

    def __init__(self, message: str, throttled: bool = False):
        super().__init__(message)
        self.throttled = throttled


class FatalError(Exception):
    """不可重试错误：鉴权失败 / 请求被拒（4xx 非 429）等。"""


class ChatClient(Protocol):
    """执行器依赖的最小调用协议（结构化 JSON 输出）。"""

    def chat_json(self, messages: list[dict], model: str, temperature: float) -> ChatResult:
        ...


class OpenAICompatClient:
    """OpenAI 兼容 API 客户端（DeepSeek）。仅在 5-B2 试跑经 CLI 装配真实使用。"""

    def __init__(self, api_key: str, base_url: str, timeout: float = 60.0):
        from openai import OpenAI  # 延迟导入：测试环境无需真实 SDK 配置

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def chat_json(self, messages: list[dict], model: str, temperature: float) -> ChatResult:
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            BadRequestError,
            RateLimitError,
        )

        start = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"},
            )
        except RateLimitError as exc:
            raise RetryableError("请求过于频繁（429），已触发限流退避", throttled=True) from exc
        except APITimeoutError as exc:
            raise RetryableError("模型请求超时") from exc
        except APIConnectionError as exc:
            raise RetryableError("无法连接模型服务") from exc
        except AuthenticationError as exc:
            raise FatalError("API Key 无效或鉴权失败") from exc
        except BadRequestError as exc:
            raise FatalError(f"请求被拒绝（400）：{exc}") from exc
        except APIStatusError as exc:
            status = getattr(exc, "status_code", 0) or 0
            if status >= 500:
                raise RetryableError(f"服务端错误（{status}）") from exc
            raise FatalError(f"API 错误（{status}）") from exc

        usage_obj = getattr(resp, "usage", None)
        usage = Usage(
            prompt_tokens=getattr(usage_obj, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage_obj, "completion_tokens", 0) or 0,
            cache_hit_tokens=getattr(usage_obj, "prompt_cache_hit_tokens", 0) or 0,
        )
        log.info(
            "LLM 调用成功 model=%s 耗时=%.2fs 输入=%s 输出=%s 缓存命中=%s",
            model, time.perf_counter() - start,
            usage.prompt_tokens, usage.completion_tokens, usage.cache_hit_tokens,
        )
        return ChatResult(
            content=resp.choices[0].message.content or "",
            model_version=getattr(resp, "model", "") or "",
            usage=usage,
        )

    @classmethod
    def from_settings(cls) -> "OpenAICompatClient":
        """CLI 装配入口：读取集中配置（存在性检查，不打印/不记录 Key 内容）。"""
        from backend.config import settings

        if not settings.DEEPSEEK_API_KEY:
            raise FatalError("DEEPSEEK_API_KEY 未配置，拒绝创建真实客户端")
        return cls(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
            timeout=settings.LLM_TIMEOUT,
        )
