"""Phase 4 / S2-A Fake Runner 主入口。

用法：
    .venv/Scripts/python.exe -m backend.app.eval.evaluate --mode fake
    # 指定冻结版/独立 DB（可重复性测试用隔离库）：
    .venv/Scripts/python.exe -m backend.app.eval.evaluate --mode fake \
        --frozen data/gt/golden_set_v1.jsonl --db <独立库路径> --workdir <运行目录>

流程：加载 frozen(111) → dispatch 逐 case → (行为/参数/产物判定) → 指标聚合
     → 落 eval_runs(UPSERT) + 结构化 JSON 报告(staging)。
"""
from __future__ import annotations

import argparse
import json
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def build_workdir(base: Path | None = None) -> Path:
    """独立运行目录（产物写到 workdir/outputs，隔离业务 data/outputs）。"""
    base = base or Path(tempfile.mkdtemp(prefix="eval_run_"))
    (base / "outputs").mkdir(parents=True, exist_ok=True)
    return base


def run_all(cases: list[dict], seed: int, workdir: Path,
            db_path: Path | None = None) -> list:
    from backend.app.eval.runner import dispatch_case
    from backend.app.eval.db_fixture import Fixture

    fixture = None
    records = []
    for case in cases:
        exp = case["expect"]
        needs_fixture = (exp.get("workflow") == "class_diagnosis"
                         or exp.get("behavior") == "reject_404")
        if needs_fixture and fixture is None:
            fixture = Fixture()
        rec = dispatch_case(case, seed, db_path=db_path, fixture=fixture,
                            out_dir=workdir)
        records.append(rec)
    return records


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 4 S2-A Fake Runner")
    ap.add_argument("--mode", default="fake")
    ap.add_argument("--frozen", default=str(REPO_ROOT / "data" / "gt" / "golden_set_v1.jsonl"))
    ap.add_argument("--db", default=None, help="独立 eval DB（落 eval_runs）；默认共享真库", )
    ap.add_argument("--workdir", default=None, help="运行目录（产物/临时）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--report-out", default=None)
    args = ap.parse_args()

    from backend.app.eval.golden import load_frozen
    from backend.app.eval.aggregate import (
        evaluate_params, evaluate_behavior, build_metrics,
        persist_records, write_report,
    )

    cases, sha = load_frozen(args.frozen)
    print(f"[golden] {len(cases)} 条；sha256={sha[:16]}…")

    workdir = build_workdir(Path(args.workdir) if args.workdir else None)

    t0 = time.time()
    records = run_all(cases, seed=args.seed, workdir=workdir,
                      db_path=Path(args.db) if args.db else None)
    elapsed = time.time() - t0

    # 判定（与执行分离）
    for case, rec in zip(cases, records):
        evaluate_params(case, rec)
        evaluate_behavior(case, rec)
        rec.commit = _commit_short()

    metrics = build_metrics(cases, records)
    metrics["elapsed_sec"] = round(elapsed, 2)

    run_id = args.run_id or f"fake_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"

    # 落库（可重复性：同一 run_id 重跑是 UPSERT，不产生重复行）
    _db = args.db or (REPO_ROOT / "data" / "db" / "edumate.sqlite")
    persist_records(cases, records, sha, run_id=run_id, mode="fake", db_path=Path(_db))

    report_path = write_report(cases, records, metrics, sha, run_id,
                               _commit_short(),
                               out_dir=Path(args.report_out) if args.report_out else None)
    _print_summary(metrics)
    print(f"[run_id] {run_id}")
    print(f"[报告] {report_path}")
    return 0


def _print_summary(m: dict) -> None:
    t = m["tasks"]
    print(f"\n===== Fake Runner 指标 =====")
    print(f"cases_total       : {m['cases_total']}")
    if m.get("cases_pending_ids"):
        print(f"  pending(未执行)   : {m['cases_pending_ids']}")
    print(f"任务成功率         : {t['pass']}/{t['denominator']} = {_fmt(t['success_rate'])}")
    print(f"Planner 字段准确率 : {_fmt(m['planner_field_accuracy'])}"
          f"（{m.get('planner_params_total_fields', 0)} 字段）")
    print(f"Case 完全匹配率    : {_fmt(m['case_complete_match_rate'])}")
    print(f"行为正确率         : {_fmt(m['behavior_accuracy'])}")
    print(f"正确追问率         : {_fmt(m['clarify']['rate'])}（{m['clarify']['hit']}/{m['clarify']['denominator']}）")
    print(f"正确拒绝率         : {_fmt(m['reject']['rate'])}（{m['reject']['hit']}/{m['reject']['denominator']}）")
    print(f"HTTP 边界正确率    : {_fmt(m['http_boundary']['rate'])}（{m['http_boundary']['hit']}/{m['http_boundary']['denominator']}）")
    print(f"错误执行率         : {_fmt(m['wrong_execution_rate'])}")
    print(f"错误拒绝率         : {_fmt(m['wrong_rejection_rate'])}")
    print(f"预期 fail 率       : {_fmt(m['fail_expected']['rate'])}（{m['fail_expected']['hit']}/{m['fail_expected']['denominator']}）")
    print(f"耗时               : {m.get('elapsed_sec', 0)}s")


def _fmt(x) -> str:
    return "N/A" if x is None else f"{x:.2%}"


def _commit_short() -> str:
    import subprocess
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=5)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())