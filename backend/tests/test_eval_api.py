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
        assert d["quality_inspection"]["status"] == "no_data"
        assert d["quality_inspection"]["metrics"] is None   # 无质检数据返回 null，不以 0 冒充


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


# ===================== Phase 5 / S3：质检指标聚合 =====================
def _seed_quality_run(run_id="run_q", qualities=None):
    _db.apply_migrations(None)
    conn = _db.connect(None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for i, q in enumerate(qualities or [], 1):
            m = {"quality": q}
            conn.execute(
                "INSERT OR REPLACE INTO eval_runs "
                "(run_id, case_id, mode, passed, metrics, detail, created_at) "
                "VALUES (?, ?, 'fake', 1, ?, '{}', ?)",
                (run_id, f"gc_q{i:02d}", json.dumps(m), f"2026-11-19T00:0{i}:00"))
        conn.commit()
    finally:
        conn.close()


def test_eval_report_quality_metrics():
    """质检指标从原始统计字段聚合：首轮/全轮拦截率、收敛轮次、剔除率、回流成功率。"""
    _seed_quality_run("run_q", [
        {"status": "completed", "total": 2, "passed": 2, "retired": 0, "unverified": 0,
         "verified_questions": 2,
         "first_round_fail": 0, "first_round_fail_eventually_passed": 0,
         "total_fail_verdicts": 0, "total_verification_rounds": 2,
         "regenerated": 0, "duplicate_pairs_batch": 0},
        {"status": "completed", "total": 1, "passed": 0, "retired": 1, "unverified": 0,
         "verified_questions": 1,
         "first_round_fail": 1, "first_round_fail_eventually_passed": 0,
         "total_fail_verdicts": 3, "total_verification_rounds": 3,
         "regenerated": 1, "duplicate_pairs_batch": 0},
    ])
    with TestClient(app, headers=AUTH_HEADERS) as client:
        d = client.get("/api/eval/report").json()
    q = d["quality_inspection"]["metrics"]
    assert d["quality_inspection"]["status"] == "measured"
    assert q["sample_cases"] == 2 and q["sample_questions"] == 3
    assert q["interception_rate_first"] == round(1 / 3, 4)      # 首轮不合格 1 / 送检 3
    assert q["interception_rate_all"] == round(3 / 5, 4)        # 判不合格 3 / 复核轮次 5
    assert q["avg_convergence_rounds"] == round(5 / 3, 3)       # 复核 5 / 已复核题数 3
    assert q["retire_rate"] == round(1 / 3, 4) and q["retired_count"] == 1
    assert q["reflux_success_rate"] == 0.0                      # 回流后通过 0 / 首轮不合格 1
    assert q["degraded_tasks"] == 0


def test_eval_report_quality_null_on_skip():
    """降级（skipped，无任何复核）→ 全轮拦截率/收敛轮次分母为 0 → null，不以 0 冒充。"""
    _seed_quality_run("run_skip", [
        {"status": "skipped", "total": 2, "passed": 0, "retired": 0, "unverified": 2,
         "verified_questions": 0,
         "first_round_fail": 0, "first_round_fail_eventually_passed": 0,
         "total_fail_verdicts": 0, "total_verification_rounds": 0,
         "regenerated": 0, "duplicate_pairs_batch": 0},
    ])
    with TestClient(app, headers=AUTH_HEADERS) as client:
        d = client.get("/api/eval/report").json()
    q = d["quality_inspection"]["metrics"]
    assert q["interception_rate_all"] is None
    assert q["avg_convergence_rounds"] is None
    assert q["reflux_success_rate"] is None      # 首轮不合格分母为 0
    assert q["degraded_tasks"] == 1
    assert q["retire_rate"] == 0.0               # 剔除 0 / 送检 2（有分母，如实 0）


def test_eval_report_quality_partial():
    """部分题目未复核 → 平均收敛轮次分母用 verified_questions（非 total）。"""
    _seed_quality_run("run_partial", [
        {"status": "partial", "total": 3, "passed": 2, "retired": 0, "unverified": 1,
         "verified_questions": 2,
         "first_round_fail": 1, "first_round_fail_eventually_passed": 1,
         "total_fail_verdicts": 1, "total_verification_rounds": 4,
         "regenerated": 1, "duplicate_pairs_batch": 0},
    ])
    with TestClient(app, headers=AUTH_HEADERS) as client:
        d = client.get("/api/eval/report").json()
    q = d["quality_inspection"]["metrics"]
    assert q["interception_rate_first"] == round(1 / 3, 4)   # 首轮不合格 / 送检题数 total=3（不变）
    assert q["retire_rate"] == round(0 / 3, 4)               # 剔除 / total=3（不变）
    assert q["interception_rate_all"] == round(1 / 4, 4)     # 失败 verdict 1 / 复核轮次 4
    assert q["avg_convergence_rounds"] == 2.0                # 复核 4 / 已复核题数 2（非 / total=3）
    assert q["reflux_success_rate"] == 1.0                   # 回流后通过 1 / 首轮不合格 1
    assert q["degraded_tasks"] == 1