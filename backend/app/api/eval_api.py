"""评测报告 API（Phase 4 / S4，§9.4）。

GET /api/eval/report —— 只读聚合：
- recent_runs：最近 N 次评测 run（eval_runs 按 run_id 聚合：用例数/通过数/行为正确率/成本）；
- baselines：既有冻结基线常量（检索 §4.5、归因 §5.4、Fake Runner S2-A）；
- judge_calibration：S3 结果（best 64.0% 未采信 → 规则评分回退，如实展示）；
- quality_inspection：Phase 5 指标位（pending，不编数字）。

数据来源：eval_runs（库直读，重启可查）。限流沿用 /api/eval 60/min（security.classify）。
"""
from __future__ import annotations

import json

from fastapi import APIRouter

from backend.app.storage import db

router = APIRouter(prefix="/api", tags=["eval"])

# 冻结基线常量（来源见各条 source；均为已验收数字，不重算）
BASELINES = {
    "retrieval": {
        "R@1": 0.3265, "R@5": 0.5510, "MRR": 0.4335, "NDCG@5": 0.4201,
        "cases": 49, "source": "Phase 2.2 小学单学段重基线（决策㉜）",
    },
    "diagnosis": {
        "consistency": 1.0, "cases": 10,
        "source": "Phase 3 S6 10 例 GT 一致率（仅冻结案例算法一致性）",
    },
    "fake_runner": {
        "task_success_rate": 1.0, "behavior_accuracy": 0.9009,
        "planner_field_accuracy": 0.9587, "cases": 111, "source": "Phase 4 S2-A Fake Runner",
    },
}

# S3 judge 校准结果（决策㊹㊺；best v3 64.0% 未达 80% → 规则回退）
JUDGE_CALIBRATION = {
    "best_agreement": 0.6400, "threshold": 0.8000, "adopted": False,
    "fallback": "rule_score", "versions": [
        {"version": "v1", "agreement": 0.4640},
        {"version": "v2", "agreement": 0.6160},
        {"version": "v3", "agreement": 0.6400},
        {"version": "v4", "agreement": 0.5520, "note": "0.5 分粒度实验回归，已回退"},
    ],
    "source": "Phase 4 S3-C（决策㊹㊺）",
}

QUALITY_INSPECTION = {"status": "pending_phase5", "value": None,
                      "note": "质检拦截率/回流收敛轮次为 Phase 5 critique 指标，待实现"}


def _run_summary(rows: list) -> dict:
    """按 run_id 聚合 eval_runs 行 → run 摘要。"""
    cases = len(rows)
    passed = sum(1 for r in rows if r["passed"])
    behavior_hit = 0
    known_cost = 0.0
    unknown = 0
    latencies = []
    for r in rows:
        try:
            m = json.loads(r["metrics"] or "{}")
        except (TypeError, ValueError):
            m = {}
        if m.get("behavior_matched"):
            behavior_hit += 1
        if m.get("cost_known") and m.get("cost") is not None:
            known_cost += float(m["cost"])
        elif not m.get("cost_known"):
            unknown += 1
        if m.get("latency_ms") is not None:
            latencies.append(m["latency_ms"])
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1] if len(latencies) >= 2 else (
        latencies[0] if latencies else None)
    return {
        "cases": cases,
        "passed": passed,
        "pass_rate": round(passed / cases, 4) if cases else None,
        "behavior_accuracy": round(behavior_hit / cases, 4) if cases else None,
        "known_cost_cny": round(known_cost, 6),
        "unknown_cost_cases": unknown,
        "p95_latency_ms": p95,
    }


@router.get("/eval/report")
def eval_report(limit: int = 5) -> dict:
    limit = max(1, min(limit, 20))
    conn = db.connect(None)
    try:
        run_ids = [r["run_id"] for r in conn.execute(
            "SELECT run_id, MAX(created_at) AS mx FROM eval_runs "
            "GROUP BY run_id ORDER BY mx DESC LIMIT ?", (limit,))]
        recent = []
        for rid in run_ids:
            rows = conn.execute(
                "SELECT case_id, mode, passed, metrics FROM eval_runs WHERE run_id=?",
                (rid,)).fetchall()
            mode = rows[0]["mode"] if rows else None
            summary = _run_summary(rows)
            summary.update({"run_id": rid, "mode": mode})
            recent.append(summary)
    finally:
        conn.close()
    latest = recent[0] if recent else None
    return {
        "recent_runs": recent,
        "latest_run": latest,
        "baselines": BASELINES,
        "judge_calibration": JUDGE_CALIBRATION,
        "quality_inspection": QUALITY_INSPECTION,
        "generated_note": "只读聚合；judge 未采信（64.0%<80%）→ 规则评分回退；"
                          "质检指标待 Phase 5",
    }