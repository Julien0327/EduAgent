"""S3-A：judge 校准 CLI（生成评分表 / 规则评分 / 一致率计算）。

用法：
    # 1) 从一次 run 报告生成人工评分表（零 LLM）
    python -m backend.scripts.run_judge_calibration --sheet \
        --report data/staging/eval_runs/<run_id>/report.json

    # 2) 规则评分（零 LLM，judge 兜底路径）
    python -m backend.scripts.run_judge_calibration --judge rule \
        --report data/staging/eval_runs/<run_id>/report.json \
        --judge-out data/staging/judge_rule_<run_id>.json

    # 3) 一致率计算（人工 CSV 填好后）
    python -m backend.scripts.run_judge_calibration --agreement \
        --judge-out data/staging/judge_rule_<run_id>.json \
        --human-csv data/staging/calibration_scoring_<run_id>.csv \
        --out data/staging/calibration_agreement_<run_id>.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CALIBRATION = REPO_ROOT / "data" / "gt" / "calibration_set_v1.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="judge 校准 CLI")
    ap.add_argument("--report", help="评测 run 的 report.json")
    ap.add_argument("--calibration", default=str(CALIBRATION))
    ap.add_argument("--sheet", action="store_true", help="生成人工评分表")
    ap.add_argument("--judge", choices=["rule", "llm"], default=None,
                    help="rule=规则评分（零 LLM）；llm=真实 judge（需 --allow-live-api）")
    ap.add_argument("--allow-live-api", action="store_true",
                    help="允许真实 judge LLM 调用（S3-C 需用户批准）")
    ap.add_argument("--judge-out", default=None)
    ap.add_argument("--agreement", action="store_true")
    ap.add_argument("--human-csv", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.sheet:
        from backend.app.eval.scoring_sheet import generate_sheet
        if not args.report:
            print("--sheet 需要 --report")
            return 2
        run_name = Path(args.report).parent.name
        out_md = REPO_ROOT / "data" / "staging" / f"calibration_scoring_{run_name}.md"
        out_csv = REPO_ROOT / "data" / "staging" / f"calibration_scoring_{run_name}.csv"
        info = generate_sheet(Path(args.report), Path(args.calibration), out_md, out_csv)
        print(f"评分表已生成：{info['md']} / {info['csv']}（{info['cases']} 条）")
        return 0

    if args.judge in ("rule", "llm"):
        from backend.app.eval.judge import judge_report
        if not args.report:
            print("--judge 需要 --report")
            return 2
        use_llm = args.judge == "llm"
        llm_provider = None
        if use_llm:
            if not args.allow_live_api:
                print("[安全闸门] 真实 judge 调用未获批准（需 --allow-live-api）")
                return 2
            from backend.app.agent.llm_client import DeepSeekProvider
            from backend.config import settings
            if not settings.DEEPSEEK_API_KEY:
                print("[安全闸门] 未配置 DEEPSEEK_API_KEY")
                return 2
            llm_provider = DeepSeekProvider(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
                model=settings.DEEPSEEK_MODEL,
                timeout=settings.LLM_TIMEOUT)
        run_name = Path(args.report).parent.name
        tag = "llm" if use_llm else "rule"
        out = Path(args.judge_out) if args.judge_out else \
            REPO_ROOT / "data" / "staging" / f"judge_{tag}_{run_name}.json"
        result = judge_report(Path(args.report), Path(args.calibration),
                              use_llm=use_llm, llm_provider=llm_provider)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{tag} 评分完成：{result['summary']} → {out}")
        return 0

    if args.agreement:
        from backend.app.eval.judge import calibration_agreement
        from backend.app.eval.scoring_sheet import read_human_csv
        if not args.judge_out or not args.human_csv:
            print("--agreement 需要 --judge-out 与 --human-csv")
            return 2
        judged = json.loads(Path(args.judge_out).read_text(encoding="utf-8"))["rows"]
        judge_scores = {cid: r["scores"] for cid, r in judged.items()}
        human_scores, warnings = read_human_csv(Path(args.human_csv))
        for w in warnings:
            print(f"[CSV 警告] {w}")
        result = calibration_agreement(judge_scores, human_scores)
        out = Path(args.out) if args.out else \
            REPO_ROOT / "data" / "staging" / "calibration_agreement_v1.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"一致率 overall={result['overall_rate']} "
              f"adopted={result['adopted']}（阈值 {result['threshold']}）→ {out}")
        for d, v in result["per_dimension"].items():
            print(f"  {d}: {v['hit']}/{v['total']} = {v['rate']}")
        return 0

    print("请指定 --sheet / --judge rule / --agreement")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())