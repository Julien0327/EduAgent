"""评测预算保护（S2-B，要求 3/6）。

基于决策㉖/㊲ 的预算三线与 annotation.BudgetLedger 语义，专为评测运行：
- 提醒线 ¥10（warn）：达到 → 记录预算提醒，**不停止**；
- 暂停线 ¥15（pause）：达到 → 停止提交新 case（在途排空后报告暂停），等待人工确认；
- 硬停线 ¥20（cap）：cost+reserved ≥ cap 即拒绝任何新 case——不因重试/并发/未知成本绕过。

关键语义：
- **未知成本不可视为 0**（要求 6）：供应商未返回 usage 的调用，预留按
  `worst_case_cost_cny` 全额预留（无缓存高峰口径，决策②），防止"成本未知=0"钻空子；
  实际 cost 只在 usage 可信时计入；
- **并发/重试/异常**：每次 LLM 调用前 `try_reserve(worst)`，成功后调用，
  实际 cost commit；留空（未知）则在 finally 按最坏额确认（视为已消费）——
  避免异常路径造成预留泄漏；
- **防绕过**：初始可注入历史累计（跨 run 复用）；硬停后任何 try_reserve 均拒绝。
"""
from __future__ import annotations

import threading

from backend.app.annotation.pricing import worst_case_cost_cny

DEFAULT_CAP = 20.0
DEFAULT_PAUSE = 15.0
DEFAULT_WARN = 10.0


class CostEntry:
    """一次已记录调用的成本明细（要求 2/6 的结构化单位）。"""

    def __init__(self, model: str, tokens_in: int | None, tokens_out: int | None,
                 cost_cny: float | None, cost_known: bool,
                 period: str | None = None, price_version: str | None = None):
        self.model = model
        self.tokens_in = tokens_in          # None=未知
        self.tokens_out = tokens_out
        self.cost_cny = cost_cny            # None=未知（不冒认为 0）
        self.cost_known = cost_known
        self.period = period
        self.price_version = price_version

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_cny": self.cost_cny,
            "cost_known": self.cost_known,
            "period": self.period,
            "price_version": self.price_version,
        }


class EvalBudget:
    """评测运行级预算台账（线程安全，三线 + 预留 + unknown 语义）。"""

    def __init__(self, cap_cny: float = DEFAULT_CAP,
                 warn_at_cny: float = DEFAULT_WARN,
                 pause_at_cny: float = DEFAULT_PAUSE,
                 initial_cost_cny: float = 0.0,
                 worst_model: str = "deepseek-flash"):
        if not 0 < warn_at_cny <= pause_at_cny <= cap_cny:
            raise ValueError(
                f"预算线非法：warn={warn_at_cny} pause={pause_at_cny} cap={cap_cny}"
                "（须 0 < 提醒 ≤ 暂停 ≤ cap）")
        self.cap = float(cap_cny)
        self.warn_at = float(warn_at_cny)
        self.pause_at = float(pause_at_cny)
        self.worst_model = worst_model
        self.cost = max(0.0, float(initial_cost_cny))
        self.reserved = 0.0
        self.unknown_cost = 0.0             # 未知成本已按最坏口径预留（不可视 0）
        self.calls_known = 0
        self.calls_unknown = 0
        self.entries: list[CostEntry] = []
        self.reminded = self.cost >= self.warn_at
        self.pause_requested = self.cost >= self.pause_at
        self.hard_stopped = self.cost >= self.cap
        self._allow_resume = False           # 暂停后人工确认恢复（决策㉖：等待人工）
        self._lock = threading.Lock()

    def allow_resume(self) -> None:
        """暂停线触发后人工确认恢复执行（决策㉖：暂停等待人工确认再续跑）。"""
        with self._lock:
            self._allow_resume = True

    # ---- 预留：调用前必须通过（按最坏口径） ----
    def try_reserve(self, prompt_tokens: int, completion_tokens: int) -> float | None:
        worst = worst_case_cost_cny(self.worst_model,
                                    max(0, int(prompt_tokens or 0)),
                                    max(0, int(completion_tokens or 0)))
        with self._lock:
            if self.hard_stopped:
                return None
            if self.pause_requested and not self._allow_resume:
                return None                     # 暂停线触发：需人工确认才恢复
            if self.cost + self.reserved + worst >= self.cap:
                self.hard_stopped = True
                return None
            self.reserved += worst
            return worst

    def reserve_unknown(self) -> float | None:
        """未知 tokens 的调用：按默认最坏预留（防未知=0 绕过）。"""
        worst = worst_case_cost_cny(self.worst_model, 1000, 500)
        with self._lock:
            if self.hard_stopped:
                return None
            if self.pause_requested and not self._allow_resume:
                return None
            if self.cost + self.reserved + worst >= self.cap:
                self.hard_stopped = True
                return None
            self.reserved += worst
            return worst

    # ---- 提交：调用结束后汇入实际成本 ----
    def commit(self, cost_cny: float | None, model: str = "deepseek-flash",
               tokens_in: int | None = None, tokens_out: int | None = None,
               period: str | None = None, price_version: str | None = None,
               worst_cny: float | None = None) -> CostEntry:
        """记录一次调用。cost_cny=None → 未知（cost_known=False，按最坏口径入账）。

        未知成本入账额 priority：
          1. 显式传 worst_cny（该次预留额，最准）；
          2. 否则按本调用 tokens 推算最坏；
          3. tokens 也未知 → reserve_unknown 的默认最坏额。
        保证"未知成本 ≠ 0"，防预算绕过；预留额用同额冲抵（防泄漏）。
        """
        with self._lock:
            if worst_cny is None:
                worst_cny = worst_case_cost_cny(
                    self.worst_model,
                    max(0, int(tokens_in or 0)), max(0, int(tokens_out or 0))) \
                    if (tokens_in or tokens_out) else worst_case_cost_cny(
                        self.worst_model, 1000, 500)
            if worst_cny:
                self.reserved = max(0.0, self.reserved - worst_cny)
            if cost_cny is None:
                # 未知成本：按最坏额入账（cost_known=False），不视 0
                self.unknown_cost += worst_cny
                self.cost += worst_cny
                self.calls_unknown += 1
                entry = CostEntry(model, tokens_in, tokens_out, None, False,
                                  period, price_version)
            else:
                known_cny = float(cost_cny)
                self.cost += known_cny
                self.calls_known += 1
                entry = CostEntry(model, tokens_in, tokens_out, known_cny, True,
                                  period, price_version)
            self.entries.append(entry)
            if self.cost >= self.cap:
                self.hard_stopped = True
            if self.cost >= self.pause_at:
                self.pause_requested = True
            if self.cost >= self.warn_at:
                self.reminded = True
            return entry

    def status(self) -> dict:
        """预算状态快照（要求 2/6 的全部字段）。"""
        with self._lock:
            known_sum = sum(e.cost_cny for e in self.entries if e.cost_known and e.cost_cny is not None)
            known_n = sum(1 for e in self.entries if e.cost_known)
            return {
                "cap_cny": self.cap,
                "spent_known_cny": round(known_sum, 6),
                "spent_unknown_cny": round(self.unknown_cost, 6),
                "spent_total_cny": round(self.cost, 6),
                "reserved_cny": round(self.reserved, 6),
                "calls_known": self.calls_known,
                "calls_unknown": self.calls_unknown,
                "known_cost_mean_cny": (round(known_sum / known_n, 6) if known_n else None),
                "budget_remaining_cny": round(max(0.0, self.cap - self.cost - self.reserved), 6),
                "warn_at": self.warn_at,
                "pause_at": self.pause_at,
                "reminded": self.reminded,
                "pause_requested": self.pause_requested,
                "hard_stopped": self.hard_stopped,
                "trip_point": ("cap" if self.hard_stopped
                               else "pause" if self.pause_requested
                               else "warn" if self.reminded else "none"),
            }

    def to_dict(self) -> dict:
        return self.status() | {"entries": [e.to_dict() for e in self.entries]}


def apply_entry_to_record(rec, entry: CostEntry, ledger: EvalBudget) -> None:
    """把一次预算提交落进 CaseRecord（cost 透传 + entry 汇入 details）。"""
    rec.cost = entry.cost_cny
    rec.cost_known = entry.cost_known
    rec.details = (rec.details or "") + f" | cost_entry={entry.to_dict()}"