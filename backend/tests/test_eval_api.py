"""Phase 4 / S4：评测报告 API 测试（TestClient，隔离库，不调 LLM）。"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend.main import app
from backend.tests.conftest import AUTH_HEADERS
from backend.app.storage import db as _db


def _seed_run(run_id="run_x", mode="fake"):
    _db.apply_migrations(None)     # 隔离库先建表（等价 lifespan）
    conn = _db.connect(None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for i, (case_id, passed, behavior, cost) in enumerate([
            ("gc_e01", 1, True, 0.01),
            ("gc_e02", 1, True, 0.02),
            ("gc_b16", 0, False, None),   # 成本未知用例
        ], 1):
            m = {"behavior_matched": behavior, "cost_known": cost is not None,
                 "cost": cost, "latency_ms": 100 * i}
            conn.execute(
                "INSERT OR REPLACE INTO eval_runs "
                "(run_id, case_id, mode, passed, metrics, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, case_id, mode, passed, json.dumps(m), "{}",
                 f"2026-11-18T00:0{i}:00"))
        conn.commit()
    finally:
        conn.close()


def test_eval_report_empty():
    with TestClient(app, headers=AUTH_HEADERS) as client:
        r = client.get("/api/eval/report")
        assert r.status_code == 200
        d = r.json()
        assert d["recent_runs"] == []
        assert d["latest_run"] is None
        assert d["judge_calibration"]["adopted"] is False
        assert d["quality_inspection"]["status"] == "pending_phase5"


def test_eval_report_aggregates_run():
    _seed_run()
    with TestClient(app, headers=AUTH_HEADERS) as client:
        r = client.get("/api/eval/report?limit=5")
        assert r.status_code == 200
        d = r.json()
        run = d["recent_runs"][0]
        assert run["run_id"] == "run_x"
        assert run["cases"] == 3
        assert run["passed"] == 2
        assert run["pass_rate"] == round(2 / 3, 4)
        assert run["behavior_accuracy"] == round(2 / 3, 4)
        assert run["known_cost_cny"] == round(0.03, 6)
        assert run["unknown_cost_cases"] == 1      # 未知成本如实计数，不视 0
        assert d["latest_run"]["run_id"] == "run_x"


def test_eval_report_baselines_and_judge():
    _seed_run()
    with TestClient(app, headers=AUTH_HEADERS) as client:
        d = client.get("/api/eval/report").json()
    b = d["baselines"]
    assert b["retrieval"]["R@1"] == 0.3265
    assert b["retrieval"]["R@5"] == 0.5510
    assert b["diagnosis"]["consistency"] == 1.0
    assert b["fake_runner"]["task_success_rate"] == 1.0
    jc = d["judge_calibration"]
    assert jc["best_agreement"] == 0.64
    assert jc["adopted"] is False
    assert jc["fallback"] == "rule_score"
    assert len(jc["versions"]) == 4


def test_eval_report_requires_token():
    with TestClient(app) as client:          # 无 AUTH_HEADERS
        r = client.get("/api/eval/report")
        assert r.status_code == 401