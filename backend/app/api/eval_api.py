"""评测报告 API（Phase 4 / S4，§9.4）。

GET /api/eval/report —— 只读聚合：
- recent_runs：最近 N 次 run（用例/通过/任务成功率/行为正确率/成本/未知成本/
  总体 p95 + **分工作流 p95**（§9.1）/ **规则评分五维均值**（judge 未采信的
  回退路径，S6 扩展））；
- baselines：冻结基线常量（检索 §4.5、归因 §5.4、Fake Runner S2-A）；
- latest_vs_baseline：任务级指标 baseline→current 差值（S6 扩展）；
- judge_calibration：S3 结果（best 64.0% 未采信 → 规则评分回退，如实展示）；
- quality_inspection：Phase 5 指标位（pending，不编数字）。

数据来源：eval_runs（库直读，重启可查）。限流沿用 /api/eval 60/min。
"""
from __future__ import annotations

import json

from fastapi import APIRouter

from backend.app.storage import db

router = APIRouter(prefix="/api", tags=["eval"])

DIMENSIONS = ("content_correctness", "difficulty_fit", "coverage_completeness",
              "expression", "citation_compliance")

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


def _p95(values: list) -> int | None:
    if not values:
        return None
    values = sorted(values)
    return values[max(0, int(len(values) * 0.95) - 1)]


def _run_summary(rows: list) -> dict:
    """按 run_id 聚合 eval_runs 行 → run 摘要（含分工作流 p95 与规则评分均值）。"""
    cases = len(rows)
    passed = sum(1 for r in rows if r["passed"])
    behavior_hit = 0
    known_cost = 0.0
    unknown = 0
    latencies: list[int] = []
    lat_by_wf: dict[str, list[int]] = {}
    rule_acc: dict[str, list[float]] = {d: [] for d in DIMENSIONS}
    task_denom = 0
    task_pass = 0
    for r in rows:
        try:
            m = json.loads(r["metrics"] or "{}")
        except (TypeError, ValueError):
            m = {}
        try:
            exp_b = json.loads(r["detail"] or "{}").get("expected_behavior")
        except (TypeError, ValueError, KeyError):
            exp_b = None
        if exp_b in ("execute", "fallback"):
            task_denom += 1
            if r["passed"]:
                task_pass += 1
        if m.get("behavior_matched"):
            behavior_hit += 1
        if m.get("cost_known") and m.get("cost") is not None:
            known_cost += float(m["cost"])
        elif not m.get("cost_known"):
            unknown += 1
        lat = m.get("latency_ms")
        if lat is not None:
            latencies.append(lat)
            wf = m.get("workflow") or _workflow_from_detail(r)
            if wf:
                lat_by_wf.setdefault(wf, []).append(lat)
        rs = m.get("rule_scores") or {}
        for d in DIMENSIONS:
            if rs.get(d) is not None:
                rule_acc[d].append(float(rs[d]))
    rule_mean = {d: (round(sum(v) / len(v), 3) if v else None)
                 for d, v in rule_acc.items()}
    return {
        "cases": cases,
        "passed": passed,
        "pass_rate": round(passed / cases, 4) if cases else None,
        "task_success_rate": round(task_pass / task_denom, 4) if task_denom else None,
        "task_cases": task_denom,
        "behavior_accuracy": round(behavior_hit / cases, 4) if cases else None,
        "known_cost_cny": round(known_cost, 6),
        "unknown_cost_cases": unknown,
        "p95_latency_ms": _p95(latencies),
        "p95_by_workflow": {wf: _p95(v) for wf, v in lat_by_wf.items()},
        "rule_score_mean": rule_mean,
        "rule_scored_cases": len(rule_acc[DIMENSIONS[0]]),
    }


def _workflow_from_detail(row) -> str:
    try:
        return json.loads(row["detail"] or "{}").get("workflow") or ""
    except (TypeError, ValueError, KeyError):
        return ""


def _delta(current, base) -> float | None:
    if current is None or base is None:
        return None
    return round(current - base, 4)


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
                "SELECT case_id, mode, passed, metrics, detail FROM eval_runs "
                "WHERE run_id=?", (rid,)).fetchall()
            mode = rows[0]["mode"] if rows else None
            summary = _run_summary(rows)
            summary.update({"run_id": rid, "mode": mode})
            recent.append(summary)
    finally:
        conn.close()
    latest = recent[0] if recent else None
    baseline = BASELINES["fake_runner"]
    latest_vs_baseline = None
    if latest:
        latest_vs_baseline = {
            "task_success_rate": {"baseline": baseline["task_success_rate"],
                                  "current": latest["task_success_rate"],
                                  "delta": _delta(latest["task_success_rate"],
                                                  baseline["task_success_rate"])},
            "behavior_accuracy": {"baseline": baseline["behavior_accuracy"],
                                  "current": latest["behavior_accuracy"],
                                  "delta": _delta(latest["behavior_accuracy"],
                                                  baseline["behavior_accuracy"])},
            "source": "baseline=S2-A Fake Runner（决策㊴）；current=最近一次 run",
        }
    return {
        "recent_runs": recent,
        "latest_run": latest,
        "baselines": BASELINES,
        "latest_vs_baseline": latest_vs_baseline,
        "judge_calibration": JUDGE_CALIBRATION,
        "quality_inspection": QUALITY_INSPECTION,
        "generated_note": "只读聚合；judge 未采信（64.0%<80%）→ 规则评分回退"
                          "（rule_score_mean 为回退评分均值）；质检指标待 Phase 5",
    }