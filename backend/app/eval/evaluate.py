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
            db_path: Path | None = None, mode: str = "fake",
            llm_provider=None, budget=None) -> list:
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
                            out_dir=workdir, mode=mode, llm_provider=llm_provider,
                            budget=budget)
        records.append(rec)
    return records


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 4 S2 Fake/Online Runner")
    ap.add_argument("--mode", default="fake",
                    help="fake=离线复刻（零成本）；online=在线语义（成本统计/预算）")
    ap.add_argument("--llm-mode", default="fake",
                    help="fake=FakeLLM 复刻现状；mock=MockLLM 模拟供应商（零真实调用）；"
                         "real=真实 DeepSeek（需 --allow-live-api）")
    ap.add_argument("--allow-live-api", action="store_true",
                    help="允许真实 LLM 调用（仅用户批准后设置；S2-B 阶段禁用）")
    ap.add_argument("--frozen", default=str(REPO_ROOT / "data" / "gt" / "golden_set_v1.jsonl"))
    ap.add_argument("--db", default=None, help="独立 eval DB（落 eval_runs）；默认共享真库", )
    ap.add_argument("--workdir", default=None, help="运行目录（产物/临时）")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--report-out", default=None)
    ap.add_argument("--budget-warn", type=float, default=3.0,
                    help="单次运行预算预警线（¥，默认 3）")
    ap.add_argument("--budget-cap", type=float, default=5.0,
                    help="单次运行预算硬停线（¥，默认 5；冒烟更严，全局 ¥20 仍恒生效）")
    ap.add_argument("--cases", default=None,
                    help="逗号分隔的 case_id 子集（如 --cases gc_e03,gc_q01）；缺省=全量")
    args = ap.parse_args()

    from backend.app.eval.golden import load_frozen
    from backend.app.eval.aggregate import (
        evaluate_params, evaluate_behavior, build_metrics,
        persist_records, write_report,
    )

    cases, sha = load_frozen(args.frozen)
    if args.cases:
        wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
        cases = [c for c in cases if c["case_id"] in wanted]
        missing = wanted - {c["case_id"] for c in cases}
        if missing:
            print(f"[子集警告] 以下 case_id 不在冻结集（忽略）：{sorted(missing)}")
        if not cases:
            print("[子集错误] 过滤后为空，退出")
            return 2
    print(f"[golden] {len(cases)} 条；sha256={sha[:16]}…")

    workdir = build_workdir(Path(args.workdir) if args.workdir else None)

    # ---- LLM provider 构造 + 安全闸门（S2-B：不发起真实调用）----
    mode = args.mode
    llm_provider = None
    if args.llm_mode == "fake":
        mode = "fake" if mode == "fake" else mode
    elif args.llm_mode == "mock":
        from backend.app.eval.provider import MockLLM
        llm_provider = MockLLM(seed=args.seed)
        mode = "online"
    elif args.llm_mode == "real":
        if not args.allow_live_api:
            print("[安全闸门] 真实 LLM 调用未获批准（需 --allow-live-api；S2-B 阶段禁用）")
            return 2
        from backend.app.agent.llm_client import DeepSeekProvider
        from backend.config import settings
        if not settings.DEEPSEEK_API_KEY:
            print("[安全闸门] 未配置 DEEPSEEK_API_KEY，拒绝真实调用")
            return 2
        llm_provider = DeepSeekProvider(
            api_key=settings.DEEPSEEK_API_KEY, base_url=settings.DEEPSEEK_BASE_URL,
            model=settings.DEEPSEEK_MODEL, timeout=settings.LLM_TIMEOUT)
        mode = "online"
    else:
        print(f"[参数非法] --llm-mode={args.llm_mode}（fake/mock/real）")
        return 2
    if mode not in ("fake", "online"):
        print(f"[参数非法] --mode={mode}（fake/online）")
        return 2

    # 预算保护（要求 3/要求 1）：online 档启用；单次运行预算（¥3 预警/¥5 硬停）
    # 与全局预算（¥10 提醒/¥15 暂停/¥20 硬停）同时生效，取最严格（BudgetStack）。
    budget = None
    if mode == "online":
        from backend.app.eval.budget import EvalBudget, BudgetStack
        single = EvalBudget(cap_cny=args.budget_cap, warn_at_cny=args.budget_warn,
                            pause_at_cny=args.budget_cap)   # pause=cap 等值，硬停即止
        global_b = EvalBudget(cap_cny=20.0, warn_at_cny=10.0, pause_at_cny=15.0)
        budget = BudgetStack([single, global_b])
        # 打印预算矩阵（透明度）
        print(f"[预算] 单次 warn={args.budget_warn} cap={args.budget_cap}；"
              f"全局 warn=10 pause=15 cap=20（BudgetStack 最严格）")

    t0 = time.time()
    records = run_all(cases, seed=args.seed, workdir=workdir,
                      db_path=Path(args.db) if args.db else None,
                      mode=mode, llm_provider=llm_provider, budget=budget)
    elapsed = time.time() - t0

    # 判定（与执行分离）
    for case, rec in zip(cases, records):
        evaluate_params(case, rec)
        evaluate_behavior(case, rec)
        rec.commit = _commit_short()

    metrics = build_metrics(cases, records)
    metrics["elapsed_sec"] = round(elapsed, 2)
    if budget is not None:
        metrics["budget"] = budget.to_dict()   # 预算快照（含 BttBudgetStack 矩阵 + 触发线）

    run_id = args.run_id or f"{mode}_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"

    # 落库（可重复性：同一 run_id 重跑是 UPSERT，不产生重复行）
    _db = args.db or (REPO_ROOT / "data" / "db" / "edumate.sqlite")
    persist_records(cases, records, sha, run_id=run_id, mode=mode, db_path=Path(_db))

    report_path = write_report(cases, records, metrics, sha, run_id,
                               _commit_short(),
                               out_dir=Path(args.report_out) if args.report_out else None)
    _print_summary(metrics)
    print(f"[run_id] {run_id}")
    print(f"[报告] {report_path}")
    return 0


def _print_summary(m: dict) -> None:
    t = m["tasks"]
    c = m.get("cost", {})
    print(f"\n===== Runner 指标 =====")
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
    print(f"成本(已知任务)     : {c.get('known_cost_total_cny','—')} 元 / "
          f"{c.get('known_cost_tasks')} 任务 / 均值 {_fmt_mean(c.get('known_cost_mean_cny'))}")
    print(f"成本(未知任务)     : {c.get('unknown_cost_tasks')} 任务（未知成本不视 0，页面如实展示）")
    print(f"耗时               : {m.get('elapsed_sec', 0)}s")


def _fmt_mean(x) -> str:
    return "N/A" if x is None else f"{x:.6f}元"


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