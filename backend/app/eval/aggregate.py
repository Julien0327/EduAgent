"""评测聚合（S2-A：行为判定 + 指标统计 + 落盘 eval_runs + 报告）。

**行为判定（与 runner 分离，保证可重复性）**：给定 frozen golden 与逐条原始执行
结果，统一计算每条 case 的：
- 实际行为 vs 预期行为 → 行为判定；
- passed（该 case 是否达标，用于指标分子/分母）。

**判定规则（要求 2 / 裁决细化）**：

| expected | actual 判定 | passed 条件 |
|---|---|---|
| execute / fallback | 实际 completed 且 workflow 匹配预期 | completed + workflow 命中 + 产物三层通过 |
| fail（预期失败，如 gc_b24） | 实际 failed（或预期 fail 且任务确实失败） | 实际 failed == 预期 fail → passed（行为匹配） |
| clarify | plan().need_clarification 为真 | actual=clarify → passed |
| reject / reject_scope | detect 返回拦截 | actual=reject → passed |
| reject_400 / 404 | 输入校验/班级不存在 | actual 匹配 → passed |
| reject_422 | 预留（S2-B） | — |

**指标（要求 2 建议表）**：
- 任务成功率：分母=expected.execute/fallback 且 mode=both；分子=其中 passed；
- Planner 字段准确率：有参数预期的 case，逐字段匹配命中率；
- Case 完全匹配率：参数整体匹配（全部预期字段 exact/synonym 命中）；
- 正确追问率：分母=expected.clarify；分子=actual.clarify；
- 正确拒绝率：分母=expected.reject/reject_scope；分子=actual 匹配；
- HTTP 边界正确率：分母=reject_400/404/422；分子=实际匹配；
- 错误执行率：分母=expected∈{clarify,reject,reject_*}；分子=实际 execute（本应非执行却执行了）；
- 错误拒绝率：分母=expected=execute/fallback；分子=实际 reject（本应执行却被拒）；
- fail 因子：预期 fail 且实际 failed → 记 passed，不计入任务成功率分母、计入行为正确率分母。

**输出**：
- eval_runs 持久化（每 case 一行，run_id 分组；upsert 幂等）；
- 结构化 JSON 报告 staging（per-run 目录，不入 Git）。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
STAGING = REPO_ROOT / "data" / "staging" / "eval_runs"

from backend.app.eval.models import (
    CaseRecord, ArtifactCheck, ERR_OK, ERR_INFRA,
)


# ===================== 预期参数匹配 =====================
def _field_match(spec: dict, actual_value) -> bool:
    m = spec.get("match", "exact")
    if m == "any":
        return True
    if m == "exact":
        return actual_value == spec["value"]
    if m == "one_of":
        return actual_value in spec.get("value", [])
    if m == "synonym":
        terms = spec.get("eq_terms") or []
        return actual_value in terms
    return False


def evaluate_params(case: dict, rec: CaseRecord) -> None:
    """Planner 参数字段级匹配（要求 2：Planner 字段准确率）。"""
    expected = case["expect"].get("params", {})
    actual = rec.actual_params or {}
    rec.param_field_matches = {}
    rec.params_matched = True
    matched, total = 0, 0
    for field, spec in expected.items():
        if spec.get("match") == "any":
            continue
        total += 1
        ok = _field_match(spec, actual.get(field))
        rec.param_field_matches[field] = ok
        matched += 1 if ok else 0
        if not ok:
            rec.params_matched = False
    # 无显式参数预期 → params_matched 保持 True（不进入字段准确率分母）


# ===================== 行为判定 =====================
def _workflow_hit(case: dict, rec: CaseRecord) -> bool:
    exp_wf = case["expect"].get("workflow")
    if exp_wf is None:
        return True
    exp_wf = exp_wf if isinstance(exp_wf, list) else [exp_wf]
    return rec.workflow in exp_wf if rec.workflow else False


def evaluate_behavior(case: dict, rec: CaseRecord) -> None:
    """计算 behavior_matched / passed（判定规则见模块 docstring）。"""
    exp = case["expect"]
    expected = exp["behavior"]
    if expected in ("execute", "fallback"):
        ok = (rec.task_status == "completed" and _workflow_hit(case, rec)
              and rec.artifacts_ok)
        rec.behavior_matched = ok
        rec.passed = ok
        if not ok and rec.error_type == ERR_OK:
            rec.error_type = "workflow_choice"   # 归类：workflow/产物不符
    elif expected == "fail":
        # 预期失败：任务真失败即行为匹配（gc_b24 数据不足）
        ok = rec.task_status == "failed"
        rec.behavior_matched = ok
        rec.passed = ok
    elif expected == "clarify":
        rec.behavior_matched = rec.actual_behavior == "clarify"
        rec.passed = rec.behavior_matched
        if not rec.passed and rec.error_type == ERR_OK:
            rec.error_type = "api_behavior"
    else:  # reject / reject_scope / reject_400 / reject_404 / reject_422
        # 归一化：reject 为通用拒绝；reject_scope 期望接受 scope 拦截（actual=reject）
        actual = rec.actual_behavior
        if expected == "reject_scope":
            matched = actual in ("reject", "reject_scope")
            rec.actual_behavior = "reject_scope" if actual in ("reject", "reject_scope") else actual
        else:
            matched = actual == expected
        rec.behavior_matched = matched
        rec.passed = matched
        if not rec.passed and rec.error_type == ERR_OK:
            rec.error_type = "api_behavior"


# ===================== 指标聚合 =====================
def build_metrics(cases: list[dict], records: list[CaseRecord]) -> dict:
    """汇总全部指标（要求 2）。records 与 cases 按 case_id 对齐。"""
    rec_by_id = {r.case_id: r for r in records}
    denom_success, numer_success = 0, 0
    param_total, param_hit = 0, 0
    case_complete_denom, case_complete_hit = 0, 0
    clarify_denom, clarify_hit = 0, 0
    reject_denom, reject_hit = 0, 0
    http_denom, http_hit = 0, 0
    wrong_exec_denom, wrong_exec = 0, 0
    wrong_reject_denom, wrong_reject = 0, 0
    behavior_denom, behavior_hit = 0, 0
    fail_denom, fail_hit = 0, 0
    total, executed = 0, 0
    pending_ids = []

    for case in cases:
        cid = case["case_id"]
        rec = rec_by_id.get(cid)
        if rec is None:
            pending_ids.append(cid)   # 未执行 → 不入分母（可重复性要求）
            continue
        total += 1
        executed += 1
        exp = case["expect"]
        expected = exp["behavior"]
        mode = exp.get("mode", "both")
        # 行为正确率分母：所有已执行 case
        behavior_denom += 1
        if rec.behavior_matched:
            behavior_hit += 1
        # 分项
        if expected in ("execute", "fallback"):
            if mode == "both":
                denom_success += 1
                if rec.passed:
                    numer_success += 1
            param_total += sum(1 for s in exp.get("params", {}).values()
                               if s.get("match") != "any")
            field_hits = sum(1 for ok in rec.param_field_matches.values() if ok)
            param_hit += field_hits
            case_complete_denom += 1
            if rec.params_matched:
                case_complete_hit += 1
        elif expected == "fail":
            fail_denom += 1
            if rec.behavior_matched:
                fail_hit += 1
        elif expected == "clarify":
            clarify_denom += 1
            if rec.actual_behavior == "clarify":
                clarify_hit += 1
            if rec.actual_behavior == "execute":
                wrong_exec += 1   # 本应追问却执行
        elif expected in ("reject", "reject_scope"):
            reject_denom += 1
            if rec.behavior_matched:
                reject_hit += 1
            if rec.actual_behavior == "execute":
                wrong_exec += 1
        elif expected in ("reject_400", "reject_404", "reject_422"):
            http_denom += 1
            if rec.behavior_matched:
                http_hit += 1
            if rec.actual_behavior == "execute":
                wrong_exec += 1

    # 错误拒绝率：预期 execute 却 reject（FakeLLM 当前行为下罕见但保留）
    wrong_reject_denom = sum(1 for c in cases
                             if c["expect"]["behavior"] in ("execute", "fallback"))
    wrong_reject = sum(1 for r in records
                       if r.case_id in {c["case_id"] for c in cases if c["expect"]["behavior"] in ("execute", "fallback")}
                       and r.actual_behavior in ("reject", "reject_scope", "reject_400", "reject_404"))

    def pct(n, d):
        return round(n / d, 4) if d else None

    # ---- 成本统计（要求 2/6：已知/未知任务数、总额、均值、成本可信）----
    known_task_n = sum(1 for r in records if r.cost_known and r.cost is not None)
    unknown_task_n = sum(1 for r in records if not r.cost_known)
    known_sum = sum((r.cost or 0.0) for r in records if r.cost_known and r.cost is not None)
    # 未知成本不得视 0（页面要如实展示）——这里单独标注任务数；预算剩余由 budget 负责

    return {
        "cases_total": total,
        "cases_pending_ids": pending_ids,
        "tasks": {
            "denominator": denom_success,
            "pass": numer_success,
            "success_rate": pct(numer_success, denom_success),
        },
        "planner_field_accuracy": pct(param_hit, param_total),
        "planner_params_total_fields": param_total,
        "case_complete_match_rate": pct(case_complete_hit, case_complete_denom),
        "cost": {
            "known_cost_tasks": known_task_n,
            "unknown_cost_tasks": unknown_task_n,      # 成本未知的任务数（不冒充 0）
            "known_cost_total_cny": round(known_sum, 6),
            "known_cost_mean_cny": round(known_sum / known_task_n, 6) if known_task_n else None,
        },
        "clarify": {"denominator": clarify_denom, "hit": clarify_hit,
                    "rate": pct(clarify_hit, clarify_denom)},
        "reject": {"denominator": reject_denom, "hit": reject_hit,
                   "rate": pct(reject_hit, reject_denom)},
        "http_boundary": {"denominator": http_denom, "hit": http_hit,
                          "rate": pct(http_hit, http_denom)},
        "wrong_execution_rate": pct(wrong_exec, sum(
            1 for c in cases if c["expect"]["behavior"] in
            ("clarify", "reject", "reject_scope", "reject_400", "reject_404", "reject_422"))),
        "wrong_rejection_rate": pct(wrong_reject, wrong_reject_denom),
        "behavior_accuracy": pct(behavior_hit, behavior_denom),
        "fail_expected": {"denominator": fail_denom, "hit": fail_hit,
                          "rate": pct(fail_hit, fail_denom)},
    }


# ===================== 落盘 =====================
def persist_records(cases: list[dict], records: list[CaseRecord],
                    golden_sha256: str, run_id: str | None = None,
                    mode: str = "fake", db_path: Path | None = None) -> str:
    """写入 eval_runs；返回 run_id。

    幂等性：eval_runs 的 (run_id, case_id) 在装库中由 V6 唯一索引保证唯一——
    同一 run + case 重跑是 UPSERT 语义（不产生重复行）。
    """
    from backend.app.storage import db as _db
    run_id = run_id or f"{mode}_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
    _db.apply_migrations(db_path)
    conn = _db.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for c in cases:
            rec = next((r for r in records if r.case_id == c["case_id"]), None)
            if rec is None:
                continue
            metrics = {
                "behavior": rec.actual_behavior,
                "behavior_matched": rec.behavior_matched,
                "params_matched": rec.params_matched,
                "fields": rec.param_field_matches,
                "error_type": rec.error_type,
                "cost": rec.cost,
                "cost_known": rec.cost_known,
                "latency_ms": rec.latency_ms,
                "workflow": rec.workflow,
                "rule_scores": rec.rule_scores,
                "quality": rec.quality,
            }
            conn.execute(
                "INSERT INTO eval_runs (run_id, case_id, mode, passed, metrics, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, case_id) DO UPDATE SET "
                " passed=excluded.passed, metrics=excluded.metrics, detail=excluded.detail, "
                " mode=excluded.mode, created_at=excluded.created_at",
                (run_id, rec.case_id, mode, int(rec.passed),
                 json.dumps(metrics, ensure_ascii=False),
                 json.dumps(rec.to_dict(), ensure_ascii=False),
                 time.strftime("%Y-%m-%dT%H:%M:%S")),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return run_id


def write_report(cases, records, metrics, golden_sha256, run_id, commit,
                 mode: str = "fake", out_dir: Path | None = None) -> Path:
    """写结构化 JSON 报告（staging，不入 Git）。

    ``mode``：必须传实际执行档位（fake/online）——报告是"真实 vs Fake 严格区分"
    的事实源之一；此前硬编码 "fake" 会让 online run 的报告标注错误。
    """
    out_dir = out_dir or (STAGING / run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "run_id": run_id,
        "mode": mode,
        "golden_sha256": golden_sha256,
        "commit": commit,
        "metrics": metrics,
        "records": [r.to_dict() for r in records],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def finish_records(cases, records, golden_sha256, commit, run_id, db_path) -> dict:
    """落库 + 写报告；返回 run_id（供可重复性测试复用）。"""
    run_id = persist_records(cases, records, golden_sha256, run_id=run_id, mode="fake",
                             db_path=db_path)
    return {"run_id": run_id}