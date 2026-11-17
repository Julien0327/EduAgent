"""Trace 与成本测试（Phase 3 / S4）。

三层：
- 单元：collector 逐次记录/NULL 语义/partial 聚合/成本手工复算/价格版本固化；
- 迁移：V5 幂等、历史 steps 数据保留；
- 集成：走真实 DeepSeekProvider._request 代码路径（Fake OpenAI 传输层，无网络），
  验证 workflow → steps 落库 → SSE 字段 → 重启回放 → /api/tasks、/trace、/stats/cost。
"""
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app.annotation import pricing
from backend.app.tracing import collector as tracing
from backend.app.tracing.collector import aggregate

_REAL_DB = Path("data/db/edumate.sqlite")


# ---------- 单元：collector ----------

def _usage(pin=1000, pout=100, hit=0):
    return {"prompt_tokens": pin, "completion_tokens": pout, "cache_hit_tokens": hit}


def test_multiple_calls_each_recorded(monkeypatch):
    monkeypatch.setattr(pricing, "period_at", lambda dt: "peak")
    tracing.begin_node("t1", "n1")
    for i in range(3):
        tracing.record_llm_call("deepseek-flash", "submit_x", _usage(), 10)
    col = tracing.end_node()
    agg = aggregate(col)
    llm = [c for c in agg["llm_calls"] if c["kind"] == "llm"]
    assert len(llm) == 3                      # 逐次记录，不只最后一次
    assert [c["attempt"] for c in llm] == [1, 2, 3]
    assert agg["tokens_in"] == 3000 and agg["tokens_out"] == 300
    assert agg["usage_source"] == "provider"


def test_unavailable_usage_keeps_null(monkeypatch):
    monkeypatch.setattr(pricing, "period_at", lambda dt: "peak")
    tracing.begin_node("t1", "n1")
    tracing.record_llm_call("deepseek-flash", "submit_x", None, 10)
    agg = aggregate(tracing.end_node())
    assert agg["usage_source"] == "unavailable"
    assert agg["tokens_in"] is None and agg["tokens_out"] is None
    assert agg["cost"] is None                # NULL，不以 0 冒充
    call = agg["llm_calls"][0]
    assert call["tokens_in"] is None and call["cost_cny"] is None


def test_failed_call_recorded(monkeypatch):
    monkeypatch.setattr(pricing, "period_at", lambda dt: "peak")
    tracing.begin_node("t1", "n1")
    tracing.record_llm_call("deepseek-flash", "submit_x", None, 5, error="timeout")
    agg = aggregate(tracing.end_node())
    call = agg["llm_calls"][0]
    assert call["error"] == "timeout"
    assert call["tokens_in"] is None and call["cost_cny"] is None
    assert agg["usage_source"] == "unavailable"


def test_partial_aggregation_known_only(monkeypatch):
    """混合：1 次 provider + 1 次 unavailable → partial，只汇总已知部分。"""
    monkeypatch.setattr(pricing, "period_at", lambda dt: "peak")
    tracing.begin_node("t1", "n1")
    tracing.record_llm_call("deepseek-flash", "s", _usage(100, 10), 10)
    tracing.record_llm_call("deepseek-flash", "s", None, 10)
    agg = aggregate(tracing.end_node())
    assert agg["usage_source"] == "partial"
    assert agg["tokens_in"] == 100 and agg["tokens_out"] == 10
    detail_cost = sum(c["cost_cny"] for c in agg["llm_calls"]
                      if c["kind"] == "llm" and c["cost_cny"] is not None)
    assert abs(agg["cost"] - round(detail_cost, 6)) < 1e-9


def test_no_calls_source_none():
    tracing.begin_node("t1", "n1")
    agg = aggregate(tracing.end_node())
    assert agg["usage_source"] == "none"
    assert agg["tokens_in"] == 0 and agg["cost"] == 0.0  # 事实值：确实无调用


def test_no_collector_noop():
    """无活动采集器（CLI/chat/测试直调）→ 安全空转。"""
    tracing.record_llm_call("m", "t", _usage(), 1)
    tracing.record_tool_call("x", 1)
    assert tracing.current() is None


def test_tool_call_recorded_separately(monkeypatch):
    monkeypatch.setattr(pricing, "period_at", lambda dt: "peak")
    tracing.begin_node("t1", "n1")
    tracing.record_tool_call("search_knowledge_points", 12)
    tracing.record_llm_call("deepseek-flash", "s", _usage(50, 5), 8)
    agg = aggregate(tracing.end_node())
    kinds = [c["kind"] for c in agg["llm_calls"]]
    assert kinds == ["tool", "llm"]
    assert agg["tokens_in"] == 50             # tool 记录不进 token 汇总
    assert agg["llm_model"] == "deepseek-flash"


def test_cost_hand_recompute(monkeypatch):
    """手工复算：in=1000(含缓存命中500)、out=100、peak 牌价。
    cost = (500*0.04 + 500*2.0 + 100*8.0)/1e6 = 0.00182 元。"""
    monkeypatch.setattr(pricing, "period_at", lambda dt: "peak")
    tracing.begin_node("t1", "n1")
    tracing.record_llm_call("deepseek-flash", "s", _usage(1000, 100, 500), 10)
    agg = aggregate(tracing.end_node())
    call = agg["llm_calls"][0]
    expected = (500 * 0.04 + 500 * 2.0 + 100 * 8.0) / 1e6
    assert call["cost_cny"] == pytest.approx(expected, abs=1e-9)
    assert call["price_period"] == "peak"
    assert call["unit_price"] == {"in_hit": 0.04, "in_miss": 2.0, "out": 8.0}
    assert agg["cost"] == pytest.approx(expected, abs=1e-9)


def test_price_version_frozen_in_record(monkeypatch):
    """价格表版本固化在明细里；后续改版本不改写历史记录。"""
    monkeypatch.setattr(pricing, "period_at", lambda dt: "peak")
    tracing.begin_node("t1", "n1")
    tracing.record_llm_call("deepseek-flash", "s", _usage(10, 1), 5)
    agg = aggregate(tracing.end_node())
    call = agg["llm_calls"][0]
    assert call["price_version"] == pricing.PRICE_TABLE_VERSION
    serialized = json.dumps(agg["llm_calls"])          # 落库形态
    monkeypatch.setattr(pricing, "PRICE_TABLE_VERSION", "future-v9")
    assert "future-v9" not in serialized               # 历史不受后续调价影响
    assert call["price_version"] != "future-v9"


# ---------- V5 迁移 ----------

def test_v5_migration_idempotent_and_preserves_history(tmp_path):
    from backend.app.storage import db

    path = tmp_path / "mig.sqlite"
    db.apply_migrations(path)
    conn = db.connect(path)
    try:
        conn.execute(
            "INSERT INTO tasks (task_id, prompt, status, created_at)"
            " VALUES ('t_old', 'p', 'completed', '2026-01-01T00:00:00Z')")
        conn.execute(
            "INSERT INTO steps (task_id, step_no, node, status, output_summary)"
            " VALUES ('t_old', 1, 'n', 'done', '历史行')")
        conn.commit()
    finally:
        conn.close()

    db.apply_migrations(path)   # 重复执行必须安全
    db.apply_migrations(path)
    conn = db.connect(path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(steps)")}
        assert {"llm_model", "usage_source", "llm_calls"} <= cols
        row = conn.execute(
            "SELECT node, output_summary, llm_model, usage_source, llm_calls, "
            "tokens_in, cost FROM steps WHERE task_id='t_old'").fetchone()
        assert row["node"] == "n" and row["output_summary"] == "历史行"
        assert row["llm_model"] is None and row["usage_source"] is None
        assert row["llm_calls"] is None
        versions = [r["version"] for r in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version")]
        assert versions == [1, 2, 3, 4, 5]
    finally:
        conn.close()


# ---------- 集成：真实 provider 代码路径（Fake 传输层，无网络） ----------

class FakeCompletions:
    """脚本化 OpenAI completions：走 DeepSeekProvider._request 真实解析路径。"""

    def __init__(self, responses, usage_mode="provider"):
        self.responses = list(responses)
        self.usage_mode = usage_mode   # provider / none（供应商未返回 usage）
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        tool_call, args = self.responses.pop(0) if self.responses else (None, {})
        message = SimpleNamespace(
            content="",
            tool_calls=[] if tool_call is None else [SimpleNamespace(
                id=f"c{self.calls}",
                function=SimpleNamespace(name=tool_call,
                                         arguments=json.dumps(args, ensure_ascii=False)))],
        )
        usage = None
        if self.usage_mode == "provider":
            usage = SimpleNamespace(prompt_tokens=1000 + self.calls,
                                    completion_tokens=50 + self.calls,
                                    prompt_cache_hit_tokens=800)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                               usage=usage, model="deepseek-flash")


def _fake_llm_service(monkeypatch, responses, usage_mode="provider"):
    from backend.app.agent import llm_utils
    from backend.app.agent.llm_client import DeepSeekProvider, LLMService

    fake = FakeCompletions(responses, usage_mode)
    provider = DeepSeekProvider(api_key="test-key", base_url="http://fake",
                                model="deepseek-flash", timeout=5)
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=fake))
    monkeypatch.setattr(llm_utils, "get_llm_service",
                        lambda: LLMService(provider))
    return fake


@pytest.fixture()
def fake_env(monkeypatch):
    """离线环境：检索替身 + 固定牌价时段（peak）+ 真实库路径隔离由 conftest 保证。"""
    import backend.app.agent.workflow as wf
    from backend.tests.conftest import FakeKpService
    import backend.app.tools.knowledge_tools as kt

    monkeypatch.setattr(kt, "get_retrieval_service", lambda: FakeKpService())
    monkeypatch.setattr(pricing, "period_at", lambda dt: "peak")
    return wf


def _plan_resp():
    return ("submit_plan", {"workflow": "exam_loop",
                            "params": {"subject": "数学", "chapter": "分数与百分数"}})


def _diag_resp():
    return ("submit_diagnosis", {"summary": "整体中等", "diagnosis": "分数薄弱",
                                 "suggestions": ["加强练习"]})


def test_workflow_persists_real_trace(fake_env, monkeypatch):
    """exam_loop 走 provider 真实记录路径：steps 落库 tokens/cost/明细。"""
    fake = _fake_llm_service(monkeypatch, [_plan_resp(), _diag_resp()])
    ctx = fake_env.run_task("请对《分数与百分数》做章节小测并生成学情报告")
    assert ctx.status == "completed", ctx.error
    assert fake.calls == 2                      # planner + diagnosis 两次真实记录

    by_node = {s.node: s for s in ctx.steps}
    plan = by_node["understand_task"]
    assert plan.usage_source == "provider"
    assert plan.tokens_in == 1001 and plan.tokens_out == 51
    assert plan.llm_model == "deepseek-flash"
    assert plan.cost is not None and plan.cost > 0
    assert len(plan.llm_calls) == 1
    call = plan.llm_calls[0]
    assert call["usage_source"] == "provider"
    assert call["price_version"] == pricing.PRICE_TABLE_VERSION
    assert call["unit_price"] == {"in_hit": 0.04, "in_miss": 2.0, "out": 8.0}
    # 手工复算：hit=800, miss=1001-800=201, out=51, peak
    expected = (800 * 0.04 + 201 * 2.0 + 51 * 8.0) / 1e6
    assert call["cost_cny"] == pytest.approx(expected, abs=1e-9)
    assert plan.cost == pytest.approx(expected, abs=1e-9)

    # 无 LLM 的节点：usage_source='none'，tokens/cost 为事实 0
    docx = by_node["build_exam_docx"]
    assert docx.usage_source == "none"
    assert docx.tokens_in == 0 and docx.cost == 0.0

    # SSE step 事件扩展字段（§10.3），旧字段保留
    events = []
    while not ctx.events.empty():
        events.append(ctx.events.get_nowait())
    done_events = [e for e in events
                   if e.get("type") == "step" and e.get("status") == "done"]
    assert done_events
    ev = next(e for e in done_events if e["node"] == "understand_task")
    assert ev["tokens_in"] == 1001 and ev["cost"] == pytest.approx(expected, abs=1e-9)
    assert ev["usage_source"] == "provider" and "message" in ev

    # 工具调用记录（fetch_knowledge → search_knowledge_points）
    fetch = by_node["fetch_knowledge"]
    tool_calls = [c for c in (fetch.llm_calls or []) if c["kind"] == "tool"]
    assert any(c["tool"] == "search_knowledge_points" for c in tool_calls)

    # DB 落库核验（不只内存）：Σsteps.cost == Σ明细
    from backend.app.agent.context import task_store
    row_db = task_store.get_from_db(ctx.task_id)
    db_steps = row_db["steps"]
    step_cost = sum(s["cost"] for s in db_steps if s["cost"] is not None)
    detail_cost = sum(c["cost_cny"] for s in db_steps
                      for c in (s.get("llm_calls") or [])
                      if c.get("kind") == "llm" and c.get("cost_cny") is not None)
    assert step_cost == pytest.approx(detail_cost, abs=1e-9)
    # 第二次调用 tokens 递增（1002/52）：hit=800, miss=202, out=52
    expected2 = (800 * 0.04 + 202 * 2.0 + 52 * 8.0) / 1e6
    assert step_cost == pytest.approx(expected + expected2, abs=1e-9)


def test_unavailable_usage_persists_null(fake_env, monkeypatch):
    """供应商未返回 usage → 落库 NULL（不是 0），usage_source='unavailable'。"""
    _fake_llm_service(monkeypatch, [_plan_resp(), _diag_resp()], usage_mode="none")
    ctx = fake_env.run_task("请对《分数与百分数》做章节小测并生成学情报告")
    assert ctx.status == "completed", ctx.error
    plan = next(s for s in ctx.steps if s.node == "understand_task")
    assert plan.usage_source == "unavailable"
    assert plan.tokens_in is None and plan.cost is None

    from backend.app.agent.context import task_store
    conn = task_store._get_conn()
    row = conn.execute(
        "SELECT tokens_in, cost, usage_source FROM steps"
        " WHERE task_id=? AND node='understand_task'", (ctx.task_id,)).fetchone()
    assert row["tokens_in"] is None and row["cost"] is None   # 真 NULL
    assert row["usage_source"] == "unavailable"


def test_trace_api_and_restart_replay(fake_env, monkeypatch):
    """/api/tasks 列表 + /trace 从库直读；reset 模拟重启后仍可回放。"""
    from backend.app.agent.context import task_store
    from backend.main import app
    from backend.tests.conftest import AUTH_HEADERS

    _fake_llm_service(monkeypatch, [_plan_resp(), _diag_resp()])
    ctx = fake_env.run_task("请对《分数与百分数》做章节小测并生成学情报告")

    task_store.reset()          # 模拟服务重启：内存缓存清空

    with TestClient(app, headers=AUTH_HEADERS) as client:
        r = client.get("/api/tasks?limit=10")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 1
        assert any(t["task_id"] == ctx.task_id for t in body["tasks"])

        r = client.get(f"/api/tasks/{ctx.task_id}/trace")
        assert r.status_code == 200
        body = r.json()
        assert body["task_id"] == ctx.task_id
        trace = body["trace"]
        assert trace["totals"]["llm_call_count"] == 2
        assert trace["totals"]["tokens_in"] == 1001 + 1002
        assert trace["cost_recompute_ok"] is True
        nodes = {s["node"] for s in trace["steps"]}
        assert "understand_task" in nodes and "build_report_pdf" in nodes
        # 重启后仍可读到完整明细
        plan = next(s for s in trace["steps"] if s["node"] == "understand_task")
        assert plan["llm_calls"][0]["usage_source"] == "provider"

        r = client.get("/api/tasks/nonexistent/trace")
        assert r.status_code == 404


def test_stats_cost_endpoint(fake_env, monkeypatch):
    """/api/stats/cost：SQL 聚合与明细一致；仅统计 steps（标注批次不混入）。"""
    from backend.app.agent.context import task_store
    from backend.main import app
    from backend.tests.conftest import AUTH_HEADERS

    _fake_llm_service(monkeypatch, [_plan_resp(), _diag_resp()])
    ctx = fake_env.run_task("请对《分数与百分数》做章节小测并生成学情报告")

    with TestClient(app, headers=AUTH_HEADERS) as client:
        r = client.get("/api/stats/cost?days=1")
        assert r.status_code == 200
        body = r.json()
        assert body["totals"]["steps"] >= 1
        assert body["totals"]["tokens_in"] == 1001 + 1002
        task_row = next(t for t in body["by_task"] if t["task_id"] == ctx.task_id)
        # 手工复算：2 次调用，hit=800/miss=201,202/out=51,52 peak
        e1 = (800 * 0.04 + 201 * 2.0 + 51 * 8.0) / 1e6
        e2 = (800 * 0.04 + 202 * 2.0 + 52 * 8.0) / 1e6
        assert task_row["cost_cny"] == pytest.approx(e1 + e2, abs=1e-6)
        assert body["totals"]["steps_usage_unavailable"] == 0


def test_existing_workflows_not_broken(fake_env, monkeypatch):
    """兼容性：question_gen / lesson_plan 工作流照常完成（无回归）。"""
    from backend.app.agent.llm_client import LLMResponse

    _fake_llm_service(monkeypatch, [
        ("submit_plan", {"workflow": "lesson_plan",
                         "params": {"subject": "数学", "chapter": "圆的面积",
                                    "topic": "圆的面积"}}),
        ("submit_lesson_plan", {"objectives": ["o1", "o2", "o3"], "key_points": "k",
                                "difficult_points": "d",
                                "process": [{"step": "导入", "detail": "x"}],
                                "homework": "h"}),
    ])
    ctx = fake_env.run_task("请为《圆的面积》生成一份教案")
    assert ctx.status == "completed", ctx.error
    assert [a.file_name for a in ctx.artifacts] == ["01_教案.docx"]
