"""单价表与费用计算（Step 5-B1）。

价格来源：api-docs.deepseek.com 中文定价页（2026-11-13 抓取核实，设计文档 §8）。
单位：元 / 百万 tokens。高峰 = 北京时间工作日（不含法定节假日）9:00-12:00、14:00-18:00；
其余时段 = 空闲 = 高峰半价（法定节假日差异未单列，保守按空闲处理——费用只会低估为
高峰价、不会高估，预算闸门按最坏口径另行保护）。

决策②：预算保护一律按无缓存最坏口径（worst_case_cost_cny），不以缓存命中为前提。
决策④：主力模型显式 deepseek-flash；deepseek-v4-pro 仅存档（未授权启用）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.annotation.client import Usage

PRICE_TABLE: dict[str, dict[str, dict[str, float]]] = {
    # 元 / 百万 tokens：输入缓存命中 / 输入未命中 / 输出
    "deepseek-flash": {
        "peak": {"in_hit": 0.04, "in_miss": 2.0, "out": 8.0},
        "idle": {"in_hit": 0.02, "in_miss": 1.0, "out": 4.0},
    },
    # 存档（决策④：不切换；如未来启用需用户批准并核实当时牌价）
    "deepseek-v4-pro": {
        "peak": {"in_hit": 0.30, "in_miss": 9.0, "out": 27.0},
        "idle": {"in_hit": 0.15, "in_miss": 4.5, "out": 13.5},
    },
}

_BJ = timezone(timedelta(hours=8))
VALID_PERIODS = ("peak", "idle")


def period_at(dt: datetime) -> str:
    """北京时间高峰/空闲判定。传入 naive datetime 时按 UTC 处理。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(_BJ)
    if local.weekday() < 5 and (9 <= local.hour < 12 or 14 <= local.hour < 18):
        return "peak"
    return "idle"


def prices_for(model: str, period: str) -> dict[str, float]:
    if model not in PRICE_TABLE:
        raise KeyError(f"单价表中无模型 {model!r}（需先核实价格并登记）")
    if period not in VALID_PERIODS:
        raise KeyError(f"时段 {period!r} 非法（peak/idle）")
    return PRICE_TABLE[model][period]


def cost_cny(usage: Usage, model: str, period: str) -> float:
    """按 缓存命中 / 未命中 / 输出 三档计费（元）。防御：hit 超出 prompt 时截断。"""
    p = prices_for(model, period)
    hit = min(max(0, usage.cache_hit_tokens), max(0, usage.prompt_tokens))
    miss = max(0, usage.prompt_tokens - hit)
    return (hit * p["in_hit"] + miss * p["in_miss"] + usage.completion_tokens * p["out"]) / 1e6


def worst_case_cost_cny(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """无缓存 + 高峰价的单次调用最坏成本（元）——预算闸门的预留基准（决策②）。"""
    p = PRICE_TABLE[model]["peak"]
    return (max(0, prompt_tokens) * p["in_miss"] + max(0, completion_tokens) * p["out"]) / 1e6
