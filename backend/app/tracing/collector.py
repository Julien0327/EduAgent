"""Trace 采集器（Phase 3 / S4，§6.1）。

节点级 LLM 调用采集：workflow 在节点开始时 ``begin_node()``、结束时
``end_node()``；``llm_client`` 在每次请求（含重试、纠错轮）后调用
``record_llm_call``——**逐次记录，不只记最后一次**。

采集基于 contextvars（工作流在后台线程执行，线程内调用链同栈）；
无活动采集器（CLI 直调 / chat API / 测试）时 ``record_*`` 安全空转。

Usage 真实性（用户 S4 验收要求 1）：
- usage_source='provider'：模型供应商返回的真实 usage；
- usage_source='unavailable'：供应商未返回 usage——tokens/cost 记 NULL，
  **不得用 0 或估算值冒充**；
- 调用失败也记录（error 字段），tokens NULL。

成本可复算（要求 2）：每条明细固化 model/tokens/单价快照/价格表版本/
period/cost/latency；Σ明细 == steps 汇总列（测试断言）。
"""
from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class LlmCallRecord:
    """单次 LLM 调用明细（JSON 落库 steps.llm_calls 的元素）。"""

    kind: str = "llm"                 # llm / tool
    model: str | None = None
    tool: str | None = None           # structured 输出的 submit_* 名或 chat
    attempt: int = 1
    tokens_in: int | None = None      # None = 供应商未返回（不写 0 冒充）
    tokens_out: int | None = None
    cache_hit_tokens: int | None = None
    usage_source: str = "unavailable"  # provider / unavailable
    price_version: str | None = None
    price_period: str | None = None   # peak / idle
    unit_price: dict | None = None    # 单价快照（元/百万 tokens）
    cost_cny: float | None = None     # None = 无法计算（usage 缺失/模型无牌价）
    latency_ms: int | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "model": self.model, "tool": self.tool,
            "attempt": self.attempt,
            "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
            "cache_hit_tokens": self.cache_hit_tokens,
            "usage_source": self.usage_source,
            "price_version": self.price_version, "price_period": self.price_period,
            "unit_price": self.unit_price, "cost_cny": self.cost_cny,
            "latency_ms": self.latency_ms, "error": self.error,
        }


@dataclass
class NodeCollector:
    """一个工作流节点内的采集容器。"""

    task_id: str = ""
    node: str = ""
    calls: list = field(default_factory=list)
    started_at: float = field(default_factory=time.perf_counter)
    started_iso: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, record: LlmCallRecord) -> None:
        with self.lock:
            self.calls.append(record)


_current: ContextVar[NodeCollector | None] = ContextVar("trace_node", default=None)


def begin_node(task_id: str, node: str) -> NodeCollector:
    col = NodeCollector(
        task_id=task_id, node=node,
        started_iso=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    _current.set(col)
    return col


def end_node() -> NodeCollector | None:
    col = _current.get()
    _current.set(None)
    return col


def current() -> NodeCollector | None:
    return _current.get()


def record_llm_call(model: str | None, tool: str | None,
                    usage: dict | None, latency_ms: int,
                    error: str | None = None) -> None:
    """llm_client 每次请求后调用（成功/失败都记）。无采集器时空转。

    usage: {"prompt_tokens":..,"completion_tokens":..,"cache_hit_tokens":..}
    或 None（供应商未返回）。attempt 由采集器按节点内顺序自动编号。
    """
    col = _current.get()
    if col is None:
        return
    attempt = sum(1 for c in col.calls if c.kind == "llm") + 1
    rec = LlmCallRecord(model=model, tool=tool, attempt=attempt,
                        latency_ms=latency_ms, error=error)
    if error is None and usage and usage.get("prompt_tokens") is not None:
        rec.tokens_in = int(usage["prompt_tokens"])
        rec.tokens_out = int(usage.get("completion_tokens") or 0)
        rec.cache_hit_tokens = int(usage.get("cache_hit_tokens") or 0)
        rec.usage_source = "provider"
        _price(rec)
    else:
        rec.usage_source = "unavailable"  # tokens/cost 保持 None（不冒充）
    col.add(rec)


def record_tool_call(name: str, latency_ms: int, error: str | None = None) -> None:
    """executor 工具调用记录（kind='tool'，无 token 语义）。"""
    col = _current.get()
    if col is None:
        return
    col.add(LlmCallRecord(kind="tool", tool=name, latency_ms=latency_ms,
                          error=error, usage_source="n/a"))


def _price(rec: LlmCallRecord) -> None:
    """按调用时刻牌价计算成本；失败不阻断（cost 保持 None + error 注明）。"""
    try:
        from backend.app.annotation.client import Usage
        from backend.app.annotation import pricing

        period = pricing.period_at(datetime.now(timezone.utc))
        prices = pricing.prices_for(rec.model, period)
        usage = Usage(prompt_tokens=rec.tokens_in or 0,
                      completion_tokens=rec.tokens_out or 0,
                      cache_hit_tokens=rec.cache_hit_tokens or 0)
        rec.cost_cny = round(pricing.cost_cny(usage, rec.model, period), 6)
        rec.price_version = pricing.PRICE_TABLE_VERSION
        rec.price_period = period
        rec.unit_price = dict(prices)
    except Exception as exc:  # 模型无牌价等：cost 留 NULL，如实标注
        rec.cost_cny = None
        rec.error = (rec.error + "; " if rec.error else "") + f"pricing_failed: {exc}"


def aggregate(col: NodeCollector | None) -> dict:
    """把节点采集结果聚合为 steps 列值。

    - 无调用：usage_source='none'，tokens/cost=0（事实值：确实没有调用）。
    - 有调用：tokens/cost = 已知部分之和；全部缺 usage → NULL（不写假 0）。
    - usage_source：provider（全部有 usage）/ partial（部分缺）/
      unavailable（全部缺）。
    """
    if col is None or not col.calls:
        return {"tokens_in": 0, "tokens_out": 0, "cost": 0.0,
                "usage_source": "none", "llm_model": None, "llm_calls": []}
    llm_calls = [c for c in col.calls if c.kind == "llm"]
    if not llm_calls:
        return {"tokens_in": 0, "tokens_out": 0, "cost": 0.0,
                "usage_source": "none", "llm_model": None,
                "llm_calls": [c.to_dict() for c in col.calls]}

    with_usage = [c for c in llm_calls if c.usage_source == "provider"]
    if not with_usage:
        source = "unavailable"
        tokens_in = tokens_out = None
        cost = None
    elif len(with_usage) == len(llm_calls):
        source = "provider"
        tokens_in = sum(c.tokens_in or 0 for c in with_usage)
        tokens_out = sum(c.tokens_out or 0 for c in with_usage)
        cost = round(sum(c.cost_cny or 0 for c in with_usage), 6)
    else:
        source = "partial"  # 只汇总已知部分；缺失明细在 llm_calls 中如实保留
        tokens_in = sum(c.tokens_in or 0 for c in with_usage)
        tokens_out = sum(c.tokens_out or 0 for c in with_usage)
        cost = round(sum(c.cost_cny or 0 for c in with_usage), 6)

    models = sorted({c.model for c in llm_calls if c.model})
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost": cost,
        "usage_source": source,
        "llm_model": ",".join(models) if models else None,
        "llm_calls": [c.to_dict() for c in col.calls],
    }
