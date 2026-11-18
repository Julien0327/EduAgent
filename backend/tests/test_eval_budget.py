"""Phase 4 / S2-B 预算保护与成本统计测试（要求 3/4/6）。

全部使用 Mock/模拟费用，不触发真实 LLM/API 请求。
覆盖：
- 预算边界：提醒/暂停/硬停三线触发；
- 暂停后恢复（用户确认后继续）；
- 硬停止：不得绕过（重试/并发/未知成本）；
- 未知成本：不得视 0（按最坏预留入账）；
- 并发：多个 worker 同时 try_reserve/commit 不超支、不泄漏；
- 异常路径：预留后异常 → 按最坏额确认，不泄漏。
"""
from __future__ import annotations

import threading

import pytest

from backend.app.eval.budget import EvalBudget
from backend.app.annotation.pricing import worst_case_cost_cny


def _small_worst(prompt=200, comp=100):
    return worst_case_cost_cny("deepseek-flash", prompt, comp)


# ===================== 三线边界 =====================

def test_budget_lines_prerequisite():
    with pytest.raises(ValueError):
        EvalBudget(cap_cny=20, warn_at_cny=25, pause_at_cny=15)  # 提醒 > cap


def test_warn_line_not_stop():
    """提醒线触发但不停止（还可继续调用）。"""
    b = EvalBudget(warn_at_cny=10, pause_at_cny=15, cap_cny=20)
    assert b.try_reserve(200, 100) is not None
    b.commit(cost_cny=11.0, tokens_in=200, tokens_out=100)  # 越过提醒线
    st = b.status()
    assert st["reminded"] is True
    assert st["pause_requested"] is False
    assert st["hard_stopped"] is False
    assert b.try_reserve(200, 100) is not None  # 提醒不阻止


def test_pause_line_stops_new_calls():
    """暂停线触发 → 停止提交新调用（决策㉖：等待人工确认）。"""
    b = EvalBudget(warn_at_cny=10, pause_at_cny=15, cap_cny=20)
    b.reserved = 0
    b.commit(cost_cny=16.0, tokens_in=400, tokens_out=200)  # > 15
    st = b.status()
    assert st["pause_requested"] is True
    # 暂停触发后新调用被拒（未人工确认恢复）
    assert b.try_reserve(1, 1) is None


def test_pause_resume_after_confirm():
    """暂停后人工确认恢复 → 可继续（新调用通过），但仍然受 cap 约束。"""
    b = EvalBudget(warn_at_cny=10, pause_at_cny=15, cap_cny=20)
    b.reserved = 0
    b.commit(cost_cny=16.0, tokens_in=400, tokens_out=200)  # > 15 → paused
    assert b.try_reserve(1, 1) is None                    # 暂停中拒绝
    b.allow_resume()                                       # 人工确认
    assert b.try_reserve(1, 1) is not None                # 恢复后可继续


def test_hard_stop_cannot_reserve():
    """硬停线：cost+reserved ≥ cap 即拒绝一切新预留。

    pause_at 设为 cap 等值，确保仅 hard（cap）触发（不先触 pause 混淆断言）。
    """
    b = EvalBudget(cap_cny=20, warn_at_cny=10, pause_at_cny=20)  # pause==cap
    # cost 推到 19.5；剩余 0.5。预留一个接近 0.5 的大调用 → 压线 → 拒绝
    b.commit(cost_cny=19.5, tokens_in=1, tokens_out=1)
    # 构造一个 worst ≈ 0.6 的预留（prompt 300k tokens 高峰价 2/百万=0.6）
    assert b.try_reserve(300000, 0) is None       # 0.6 + 19.5 ≥ 20 → 硬停
    st = b.status()
    assert st["hard_stopped"] is True
    # 连最小预留也被拒（硬停后一切拒绝）
    assert b.try_reserve(1, 1) is None


def test_hard_stop_no_bypass_via_retry_or_concurrency():
    """硬停不得通过重试/并发绕过。"""
    b = EvalBudget(cap_cny=20, warn_at_cny=10, pause_at_cny=20)
    b.commit(cost_cny=19.5, tokens_in=1, tokens_out=1)
    # 并发窗口：多个 worker 同时尝试预留（每个 worst≈0.6）——硬停后全部拒绝
    results = []
    stop = threading.Event()
    def worker():
        stop.wait()                  # 对齐开始窗口模拟并发
        results.append(b.try_reserve(300000, 0))
    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads: t.start()
    stop.set()
    for t in threads: t.join()
    assert b.status()["hard_stopped"] is True
    assert all(r is None for r in results)          # 无一个成功（不绕过）
    assert b.cost + b.reserved <= b.cap + 1e-9


# ===================== 未知成本 =====================

def test_unknown_cost_not_treated_as_zero():
    """未知成本：按最坏预留额入账，cost_known=False，不可视 0。"""
    b = EvalBudget()
    assert b.try_reserve(300, 150) is not None
    worst = _small_worst(300, 150)
    entry = b.commit(cost_cny=None, tokens_in=300, tokens_out=150,
                     worst_cny=worst)   # 供应商未返回 usage，显式预留额
    st = b.status()
    assert entry.cost_known is False
    assert entry.cost_cny is None
    assert st["calls_unknown"] == 1
    assert st["spent_unknown_cny"] == pytest.approx(worst, abs=1e-9)  # 按最坏预留入账，非 0
    assert st["spent_known_cny"] == 0.0


def test_known_and_unknown_cost_stats():
    """已知/未知任务数、总额、均值、剩余（要求 6 全部字段）。"""
    b = EvalBudget()
    b.commit(0.05, tokens_in=100, tokens_out=300)              # known
    b.commit(0.12, tokens_in=300, tokens_out=700)              # known
    w = _small_worst(200, 100)
    b.commit(None, tokens_in=200, tokens_out=100, worst_cny=w)  # unknown
    st = b.status()
    assert st["calls_known"] == 2 and st["calls_unknown"] == 1
    assert st["spent_known_cny"] == pytest.approx(0.17, abs=1e-6)
    assert st["known_cost_mean_cny"] == pytest.approx(0.085, abs=1e-6)
    assert st["spent_unknown_cny"] == pytest.approx(w, abs=1e-9)
    assert st["spent_total_cny"] == pytest.approx(0.17 + w, abs=1e-6)
    assert st["budget_remaining_cny"] == pytest.approx(max(0.0, 20 - 0.17 - w), abs=1e-6)


# ===================== 异常路径 / 预留泄漏 =====================

def test_reservation_released_after_exception():
    """预留后调用异常 → 按最坏额确认（reserved 冲抵），不泄漏、不超支。"""
    b = EvalBudget()
    assert b.try_reserve(200, 100) is not None
    worst = _small_worst(200, 100)
    before = b.reserved
    entry = b.commit(None, worst_cny=worst)   # 模拟异常：无 usage → 按最坏确认
    st = b.status()
    assert b.reserved < before                # 预留已冲抵
    assert st["calls_unknown"] == 1
    assert st["spent_unknown_cny"] == pytest.approx(worst, abs=1e-9)


def test_reservation_no_leak_on_commit_worst_none():
    """commit 不带 worst/tokens → 未知成本仍按默认最坏额入账（不泄漏、不视 0）。"""
    b = EvalBudget()
    assert b.try_reserve(200, 100) is not None
    # 不传 worst 也不传 tokens：实施按默认最坏额（1000+500 tokens）入账
    entry = b.commit(None, worst_cny=None, tokens_in=None, tokens_out=None)
    st = b.status()
    exp_unknown = worst_case_cost_cny("deepseek-flash", 1000, 500)
    assert entry.cost_known is False
    assert st["calls_unknown"] == 1
    assert st["spent_unknown_cny"] == pytest.approx(exp_unknown, abs=1e-9)
    # reserved 已被冲抵（不泄漏为已消费）
    assert b.reserved < 0.001


# ===================== 并发正确性 =====================

def test_concurrent_commits_budget_locked():
    """并发 commit 不超 cap、统计准确（线程安全）。"""
    b = EvalBudget(cap_cny=20)
    N = 40
    def worker(i):
        b.try_reserve(200, 100)
        b.commit(0.05, tokens_in=200, tokens_out=100)   # 共 40*0.05=2
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()
    st = b.status()
    assert st["calls_known"] == N
    assert st["spent_known_cny"] == pytest.approx(N * 0.05, abs=1e-6)
    assert st["spent_total_cny"] == pytest.approx(N * 0.05, abs=1e-6)
    assert st["hard_stopped"] is False          # 2 << 20
    assert b.cost + b.reserved <= b.cap + 1e-9

# ===================== S2-C：多重预算（BudgetStack） =====================
from backend.app.eval.budget import BudgetStack


def test_budget_stack_single_warn_cap():
    """单次运行预算：¥3 预警、¥5 硬停（冒烟更严）。"""
    single = EvalBudget(cap_cny=5, warn_at_cny=3, pause_at_cny=5)
    stack = BudgetStack([single])
    # 到 ¥3.5 → 触发预警但不停止
    assert stack.try_reserve(200, 100) is not None
    stack.commit(cost_cny=3.5, tokens_in=200, tokens_out=100)
    st = stack.status()
    assert st["budgets"][0]["reminded"] is True          # 预警触发
    assert st["budgets"][0]["pause_requested"] is False
    assert stack.try_reserve(1, 1) is not None          # 预警不阻止
    # 累计到 4.99，尝试 ¥0.6 预留 → 4.99+0.6 ≥ 5 → 硬停拒绝
    stack.commit(cost_cny=1.49, tokens_in=1, tokens_out=1)  # 3.5 + 1.49 = 4.99
    reserved = stack.try_reserve(300000, 0)             # worst≈0.6 → 压满 5
    assert reserved is None                             # 硬停后拒绝
    assert single.hard_stopped is True

def test_budget_stack_global_and_single_most_restrictive():
    """全局（¥10/¥15/¥20）与单次（¥3/¥5）同时生效，取最严格（单次先触发）。"""
    single = EvalBudget(cap_cny=5, warn_at_cny=3, pause_at_cny=5)
    global_b = EvalBudget(cap_cny=20, warn_at_cny=10, pause_at_cny=15)
    stack = BudgetStack([single, global_b])
    best = stack.status()["most_restrictive"]
    assert best["cap_cny"] == 5                          # 最严格 cap = 单次
    assert best["warn_at"] == 3
    # 累计到 ¥4（>单次cap5 未到，但接近；全局无影响）→ 单次硬停线 5
    stack.try_reserve(200, 100)
    stack.commit(cost_cny=4.0, tokens_in=200, tokens_out=100)
    assert stack.try_reserve(800000, 0) is None         # 单次 cap5 拒绝，全局 cap20 不受影响
    # 验证全局预算单独没被触发（语义正确）
    assert global_b.status()["hard_stopped"] is False
    assert global_b.status()["reminded"] is False

def test_budget_stack_unknown_cost_double_counted():
    """未知成本入账到每个子预算（不视 0），双重预算各自按最坏预留。"""
    single = EvalBudget(cap_cny=5, warn_at_cny=3, pause_at_cny=5)
    global_b = EvalBudget(cap_cny=20, warn_at_cny=10, pause_at_cny=15)
    stack = BudgetStack([single, global_b])
    r = stack.try_reserve(200, 100)                     # 成功返回 max 预留
    assert r is not None
    stack.commit(cost_cny=None, tokens_in=200, tokens_out=100, worst_cny=r)
    unknown_worst = worst_case_cost_cny("deepseek-flash", 200, 100)
    assert single.status()["calls_unknown"] == 1          # 两个预算都记 unknown
    assert global_b.status()["calls_unknown"] == 1
    assert single.status()["spent_unknown_cny"] == pytest.approx(unknown_worst, abs=1e-9)
    assert global_b.status()["spent_unknown_cny"] == pytest.approx(unknown_worst, abs=1e-9)

def test_budget_stack_retry_reservation():
    """重试预留：单 case 按多次最坏预留（max 覆盖），覆盖重试不额外超支。"""
    # 模拟一次 case 有 3 次调用（传输重试 2 + 主调用），每次预留最坏额
    single = EvalBudget(cap_cny=5, warn_at_cny=3, pause_at_cny=5)
    stack = BudgetStack([single])
    worst_each = []
    for _ in range(3):
        r = stack.try_reserve(200, 100)
        assert r is not None
        worst_each.append(r)
        stack.commit(cost_cny=0.002, tokens_in=100, tokens_out=50, worst_cny=r)
    st = stack.status()
    # 3 次调用都成功计入；预留全部冲抵（无泄漏）
    assert single.status()["calls_known"] == 3
    assert single.reserved < 1e-6                        # 预留被冲抵
    assert st["budgets"][0]["spent_known_cny"] == pytest.approx(0.006, abs=1e-6)

def test_budget_stack_concurrent_reserve_no_overrun():
    """并发预留多预算不超各自 cap、不泄漏。"""
    single = EvalBudget(cap_cny=5, warn_at_cny=3, pause_at_cny=5)
    global_b = EvalBudget(cap_cny=20, warn_at_cny=10, pause_at_cny=15)
    stack = BudgetStack([single, global_b])
    N = 20
    def worker():
        r = stack.try_reserve(200, 100)
        if r is not None:
            stack.commit(cost_cny=0.01, tokens_in=100, tokens_out=50, worst_cny=r)
    threads = [threading.Thread(target=worker) for _ in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()
    # 单次 cap=5：每个成功预留 0.01→ 20 个 = 0.2（不超）；预留冲抵无泄漏
    assert single.cost + single.reserved <= 5 + 1e-9
    assert global_b.cost + global_b.reserved <= 20 + 1e-9
    assert stack.status()["most_restrictive"]["spent_total_cny"] <= 5 + 1e-9


def test_budget_stack_release_prevents_leak():
    """异常兜底：case 异常时 release_reservation 释放预留，不泄漏。"""
    single = EvalBudget(cap_cny=5, warn_at_cny=3, pause_at_cny=5)
    stack = BudgetStack([single])
    r = stack.try_reserve(5000, 2000)                 # 预留一次
    assert r is not None
    before = stack.status()["budgets"][0]["reserved_cny"]
    assert before > 0
    # 模拟 case 异常：未 commit，直接 release
    stack.release_reservation(r)
    after = stack.status()["budgets"][0]["reserved_cny"]
    assert after == pytest.approx(0.0, abs=1e-9)      # 预留释放干净
    assert stack.status()["budgets"][0]["spent_total_cny"] == 0.0  # 未记成本


# ===================== S2-Cbis：超支保护针对性核查 =====================

def test_overreserve_actual_settlement_counts_full():
    """预留 ¥0.10 实际结算 ¥0.30：超出部分全额计入所有子预算（非只计预留额）。"""
    single = EvalBudget(cap_cny=10, warn_at_cny=3, pause_at_cny=5)
    global_b = EvalBudget(cap_cny=20, warn_at_cny=10, pause_at_cny=15)
    stack = BudgetStack([single, global_b])
    r = stack.try_reserve(200, 100)          # 预留 worst≈0.0012（近似 ¥0.10 场景用显式 worst）
    worst_amt = 0.10                          # 显式把预留额当作 ¥0.10
    # 用显式 worst 入账：模拟预留 0.10、结算 0.30
    stack.commit(cost_cny=0.30, tokens_in=500, tokens_out=500, worst_cny=worst_amt)
    assert single.status()["spent_known_cny"] == pytest.approx(0.30, abs=1e-6)
    assert global_b.status()["spent_known_cny"] == pytest.approx(0.30, abs=1e-6)
    assert single.status()["reserved_cny"] == pytest.approx(0.0, abs=1e-9)  # 预留已释放
    # 超出预留的 0.20 已计入（非丢弃）——核心断言


def test_overrun_triggers_hard_stop_and_blocks_next():
    """超支结算立即触发硬停（cost>=cap），并阻止后续 case 预留。"""
    single = EvalBudget(cap_cny=0.25, warn_at_cny=0.1, pause_at_cny=0.2)
    stack = BudgetStack([single])
    r = stack.try_reserve(200, 100)
    assert r is not None
    stack.commit(cost_cny=0.30, tokens_in=500, tokens_out=500, worst_cny=0.10)
    assert single.hard_stopped is True         # 0.30 ≥ cap 0.25 → 立刻硬停
    assert stack.status()["most_restrictive"]["trip"] == "cap"
    # 后续 case 预留被拒（阻止继续）
    assert stack.try_reserve(200, 100) is None


def test_overrun_concurrent_no_bypass_or_double():
    """并发超支：多个 worker 各自结算，累计=实际总额，无绕过/无重复结算。"""
    single = EvalBudget(cap_cny=10, warn_at_cny=3, pause_at_cny=5)
    global_b = EvalBudget(cap_cny=20, warn_at_cny=10, pause_at_cny=15)
    stack = BudgetStack([single, global_b])
    N = 5
    def worker():
        r = stack.try_reserve(200, 100)
        if r is not None:
            stack.commit(cost_cny=0.30, tokens_in=500, tokens_out=500, worst_cny=0.10)
    threads = [threading.Thread(target=worker) for _ in range(N)]
    for t in threads: t.start()
    for t in threads: t.join()
    # 每个 worker 结算 0.30；若全部成功 → 单次 cost = 1.5
    assert single.status()["spent_known_cny"] == pytest.approx(N * 0.30, abs=1e-6)
    assert global_b.status()["spent_known_cny"] == pytest.approx(N * 0.30, abs=1e-6)
    assert single.cost + single.reserved <= 10 + 1e-9     # 不超 cap（此处 cap 大）
    assert single.status()["calls_known"] == N            # 无重复结算


def test_unknown_cost_after_overrun_no_bypass():
    """未知成本 + 超支：cost_known=False 仍按最坏入账（不视 0），预留释放。"""
    single = EvalBudget(cap_cny=1.0, warn_at_cny=0.3, pause_at_cny=0.5)
    stack = BudgetStack([single])
    r = stack.try_reserve(200, 100)
    assert r is not None
    worst_known = 0.10
    stack.commit(cost_cny=None, tokens_in=None, tokens_out=None, worst_cny=worst_known)
    st = stack.status()["budgets"][0]
    assert st["calls_unknown"] == 1
    assert st["spent_unknown_cny"] == pytest.approx(worst_known, abs=1e-9)  # 按最坏入账非 0
    assert st["reserved_cny"] == pytest.approx(0.0, abs=1e-9)                # 预留释放


def test_exception_release_then_overrun_double_settle_guard():
    """异常 release 后：预留释放干净，后续结算不重复计入（幂等）。"""
    single = EvalBudget(cap_cny=5, warn_at_cny=3, pause_at_cny=5)
    stack = BudgetStack([single])
    r = stack.try_reserve(3000, 1000)         # 预留一次
    assert r is not None
    stack.release_reservation(r)              # 模拟异常未 commit
    assert single.status()["reserved_cny"] == pytest.approx(0.0, abs=1e-9)
    assert single.status()["spent_total_cny"] == 0.0       # 无成本（异常未结算）
    # 异常后再正常常规调用：只结算一次，不重复
    r2 = stack.try_reserve(200, 100)
    assert r2 is not None
    stack.commit(cost_cny=0.05, tokens_in=200, tokens_out=300, worst_cny=r2)
    assert single.status()["calls_known"] == 1
    assert single.status()["spent_known_cny"] == pytest.approx(0.05, abs=1e-6)
