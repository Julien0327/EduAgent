"""合成演示题库构建 CLI（Phase 6 / S2）。

用法（仓库根执行）：
    # 1. 计划：不调用 API，查看 KP 拆分与成本估算
    python -m backend.scripts.build_demo_bank --plan

    # 2. 生成：模板 + LLM（需显式允许真实调用）
    python -m backend.scripts.build_demo_bank --generate --allow-live-api --budget-cap 5

    # 3. 校验：规则 + LLM 复核（LLM 复核同样需允许真实调用）
    python -m backend.scripts.build_demo_bank --validate --allow-live-api --budget-cap 3

    # 4. 报告与人工抽检表
    python -m backend.scripts.build_demo_bank --report --review-sheet

    # 5. 人工填写抽检 CSV 后冻结（要求 10% 样本全部通过）
    python -m backend.scripts.build_demo_bank --freeze
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

from backend.app.demo.generation import (
    DEFAULT_CANDIDATE,
    DEFAULT_DB,
    DEFAULT_RAW,
    apply_manual_fixes,
    build_bank,
    load_kps,
    normalize_text,
    salvage_from_raw,
    split_kps,
)
from backend.app.demo.models import DemoQuestion
from backend.app.demo.reporting import (
    coverage,
    write_coverage_report,
    write_manifest,
    write_quality_report,
    write_review_sheet,
    write_validation_report,
)
from backend.app.demo.validation import reclassify_review_issues, validate_all
from backend.app.tracing import collector as tracing
from backend.config import settings

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "data" / "staging" / "demo_bank"
COVERAGE_MD = OUT / "coverage_report.md"
COVERAGE_JSON = OUT / "coverage_report.json"
VALIDATION_MD = OUT / "validation_report.md"
VALIDATION_JSON = OUT / "validation_report.json"
QUALITY_MD = OUT / "quality_report.md"
QUALITY_JSON = OUT / "quality_report.json"
REVIEW_CSV = OUT / "human_review_v1.csv"
REVIEW_MD = OUT / "human_review_v1.md"
RECHECK_CSV = OUT / "human_recheck_v1.csv"
RECHECK_MD = OUT / "human_recheck_v1.md"
FINAL = REPO_ROOT / "data" / "demo" / "demo_bank_v1.jsonl"
MANIFEST = REPO_ROOT / "data" / "demo" / "demo_bank_v1_manifest.json"
MANUAL_FIXES = REPO_ROOT / "data" / "demo" / "manual_fixes_v1.json"


def _load(path: Path) -> list[DemoQuestion]:
    if not path.exists():
        raise SystemExit(f"候选文件不存在：{path}")
    return [
        DemoQuestion.from_dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_review_rows(path: Path) -> list[dict]:
    for encoding in ("utf-8-sig", "gbk"):
        try:
            with path.open(encoding=encoding) as fh:
                return list(csv.DictReader(fh))
        except UnicodeDecodeError:
            continue
    raise SystemExit(f"无法识别抽检表编码：{path}")


def _pass_of(row: dict) -> str:
    for key in ("human_pass(Y/N)", "human_pass", "recheck_pass(Y/N)"):
        value = (row.get(key) or "").strip()
        if value:
            return value.upper()
    return ""


def _write_specific_review(questions: list[DemoQuestion], out_csv: Path,
                           out_md: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "sample_no", "q_id", "kp_id", "kp_name", "difficulty", "generation_method",
            "qtype", "stem", "options", "answer", "solution", "equation",
            "validation_status", "validation_issues",
            "human_pass(Y/N)", "human_score(1-5)", "human_issues", "notes",
        ])
        for idx, q in enumerate(questions, start=1):
            writer.writerow([
                idx, q.q_id, q.kp_id, q.kp_name, q.difficulty, q.generation_method,
                q.qtype, q.stem, " | ".join(q.options), q.answer, q.solution,
                q.equation or "", q.validation_status, "；".join(q.validation_issues),
                "", "", "", "",
            ])
    lines = [
        "# 演示题库复核小表（仅原抽检中标记 N 的题）",
        "",
        f"- 题数：{len(questions)}",
        f"- CSV：`{out_csv.name}`",
        "- 已按你的批注修正；请复核后填写 `human_pass(Y/N)`。全部为 Y 后即可冻结。",
        "",
        "| # | q_id | KP | 难度 | 题干 | 答案 | 解析 |",
        "|---:|---|---|---:|---|---|---|",
    ]
    for idx, q in enumerate(questions, start=1):
        stem = q.stem.replace("|", "\\|")
        solution = q.solution.replace("|", "\\|")
        lines.append(
            f"| {idx} | `{q.q_id}` | {q.kp_name} | {q.difficulty} | {stem} | "
            f"{q.answer} | {solution} |"
        )
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _save(path: Path, questions: list[DemoQuestion]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for q in questions:
            fh.write(json.dumps(q.to_dict(), ensure_ascii=False) + "\n")


def _plan(db_path: Path, seed: int) -> dict:
    kps = load_kps(db_path)
    template, llm = split_kps(kps)
    # 保守估算：每 KP 1 次调用 + 50% 重试；单次按 ¥0.02 计
    estimated_calls = int(len(llm) * 1.5)
    data = {
        "kps_total": len(kps),
        "template_kps": len(template),
        "llm_kps": len(llm),
        "template_questions": len(template) * 5,
        "llm_questions": len(llm) * 5,
        "total_questions": len(kps) * 5,
        "llm_kp_names": [kp["name"] for kp in llm],
        "estimated_llm_calls": estimated_calls,
        "estimated_cost_cny_conservative": round(estimated_calls * 0.02, 2),
        "seed": seed,
        "model": settings.DEEPSEEK_MODEL,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "plan.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return data


def _validate(questions: list[DemoQuestion], *, llm_review: bool,
              budget_cap: float) -> dict:
    kps = load_kps(DEFAULT_DB)
    review_qids: set[str] = set()
    extra: dict = {}
    if llm_review:
        review_qids = {q.q_id for q in questions if q.generation_method.startswith("llm")}
        # 模板题抽 10% 做 LLM 复核
        templates = [q for q in questions if q.generation_method == "template"]
        rng = random.Random(20261120)
        rng.shuffle(templates)
        review_qids.update(q.q_id for q in templates[: max(1, len(templates) // 10)])
        col = tracing.begin_node("demo_bank_validate", "demo_bank")
        try:
            stats = validate_all(
                questions, llm_review_qids=review_qids, budget_cap=budget_cap
            )
            agg = tracing.aggregate(col)
        finally:
            tracing.end_node()
        extra = {
            "llm_review_requested": len(review_qids),
            "cost": agg.get("cost"),
            "usage_source": agg.get("usage_source"),
            "llm_calls": len([c for c in agg.get("llm_calls", []) if c.get("kind") == "llm"]),
            "budget_cap": budget_cap,
        }
        if (agg.get("cost") or 0) > budget_cap:
            extra["budget_exceeded"] = True
    else:
        stats = validate_all(questions, llm_review_qids=None)
    _save(DEFAULT_CANDIDATE, questions)
    write_coverage_report(kps, questions, COVERAGE_MD, COVERAGE_JSON)
    write_validation_report(questions, VALIDATION_MD, VALIDATION_JSON, extra=extra)
    write_quality_report(questions, QUALITY_MD, QUALITY_JSON)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if extra:
        print(json.dumps(extra, ensure_ascii=False, indent=2))
    return stats


def _freeze(questions: list[DemoQuestion]) -> int:
    kps = load_kps(DEFAULT_DB)
    cov = coverage(kps, questions)
    problems: list[str] = []
    if cov["coverage_rate"] < 1.0:
        problems.append(f"KP 覆盖不足：{cov['kps_covered']}/{cov['kps_total']}")
    if cov["all_passed_rate"] < 1.0:
        problems.append(f"存在未通过校验的 KP：{cov['all_passed_rate'] * 100:.2f}%")
    if any(q.duplicate_of for q in questions):
        problems.append("存在查重命中，需先处理")
    if not REVIEW_CSV.exists():
        problems.append(f"人工抽检 CSV 不存在：{REVIEW_CSV}")
    else:
        rows = _read_review_rows(REVIEW_CSV)
        filled = [r for r in rows if _pass_of(r)]
        failed = [r for r in filled if _pass_of(r) != "Y"]
        if len(filled) < max(1, len(questions) // 10):
            problems.append(f"人工抽检未完成：已填 {len(filled)} / 要求 ≥{len(questions) // 10}")
        if failed:
            recheck: dict[str, str] = {}
            if RECHECK_CSV.exists():
                recheck = {r["q_id"]: _pass_of(r) for r in _read_review_rows(RECHECK_CSV)}
            unresolved = [r["q_id"] for r in failed if recheck.get(r["q_id"]) != "Y"]
            if unresolved:
                problems.append(
                    "人工抽检存在未通过且未复核的样本：" + ", ".join(unresolved)
                )
    if problems:
        print("冻结未通过：")
        for item in problems:
            print(f"- {item}")
        return 1
    _save(FINAL, questions)
    stats = {
        "prompt_version": "demo_bank_prompt_v1",
        "model": settings.DEEPSEEK_MODEL,
    }
    manifest = write_manifest(FINAL, questions=questions, seed=20261120,
                              stats=stats, out_json=MANIFEST)
    print(f"已冻结：{FINAL}（sha256 {manifest['sha256']}）")
    print(f"清单：{MANIFEST}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="合成演示题库构建")
    parser.add_argument("--plan", action="store_true", help="只输出计划与成本估算")
    parser.add_argument("--generate", action="store_true", help="生成模板 + LLM 题目")
    parser.add_argument("--salvage", action="store_true",
                        help="从失败原始记录救回仅选项格式不合格的题（零调用）")
    parser.add_argument("--refresh-templates", action="store_true",
                        help="仅重生成模板题并替换候选中的模板部分（零调用）")
    parser.add_argument("--refresh-qids",
                        help="逗号分隔的模板题 q_id，只替换这些题（零调用）")
    parser.add_argument("--reclassify-review", action="store_true",
                        help="把旧版复核结果中的正面备注与硬性问题分开")
    parser.add_argument("--apply-fixes", action="store_true",
                        help="应用 data/demo/manual_fixes_v1.json 定点修正")
    parser.add_argument("--normalize-text", action="store_true",
                        help="将 U+2212 等 Excel 不友好符号归一为 ASCII")
    parser.add_argument("--recheck-sheet", action="store_true",
                        help="为人工抽检中 N 的题生成复核小表")
    parser.add_argument("--validate", action="store_true", help="校验候选题库")
    parser.add_argument("--llm-review", action="store_true", help="启用 LLM 复核")
    parser.add_argument("--report", action="store_true", help="生成报告（不重新校验）")
    parser.add_argument("--review-sheet", action="store_true", help="生成 41 题人工抽检表")
    parser.add_argument("--freeze", action="store_true", help="人工抽检通过后冻结")
    parser.add_argument("--allow-live-api", action="store_true", help="允许真实 LLM 调用")
    parser.add_argument("--budget-cap", type=float, default=5.0, help="LLM 预算上限（元）")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="业务库路径")
    parser.add_argument("--seed", type=int, default=20261120)
    args = parser.parse_args()

    if args.plan:
        _plan(Path(args.db), args.seed)
        return 0
    if args.generate:
        if not args.allow_live_api:
            print("拒绝：LLM 生成需要 --allow-live-api", file=sys.stderr)
            return 2
        if not settings.DEEPSEEK_API_KEY:
            print("拒绝：未配置 DEEPSEEK_API_KEY", file=sys.stderr)
            return 2
        questions, stats = build_bank(
            db_path=Path(args.db), seed=args.seed, allow_live=True,
            budget_cap=args.budget_cap,
        )
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0
    if args.reclassify_review:
        questions = _load(DEFAULT_CANDIDATE)
        stats = reclassify_review_issues(questions)
        _save(DEFAULT_CANDIDATE, questions)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0
    if args.apply_fixes:
        questions = _load(DEFAULT_CANDIDATE)
        questions, stats = apply_manual_fixes(questions, MANUAL_FIXES)
        _save(DEFAULT_CANDIDATE, questions)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0
    if args.normalize_text:
        questions = _load(DEFAULT_CANDIDATE)
        changed = normalize_text(questions)
        _save(DEFAULT_CANDIDATE, questions)
        print(f"已归一 {changed} 道题的文本")
        return 0
    if args.recheck_sheet:
        rows = _read_review_rows(REVIEW_CSV)
        bad_ids = [
            r["q_id"] for r in rows
            if (r.get("human_pass(Y/N)") or r.get("human_pass") or "").strip().upper() != "Y"
        ]
        questions = [q for q in _load(DEFAULT_CANDIDATE) if q.q_id in bad_ids]
        _write_specific_review(questions, RECHECK_CSV, RECHECK_MD)
        print(f"复核小表：{RECHECK_CSV}（{len(questions)} 题）")
        return 0
    if args.refresh_qids:
        from backend.app.demo.templates import generate_template_questions
        questions = _load(DEFAULT_CANDIDATE)
        by_id = {q.q_id: q for q in questions}
        kps = {kp["kp_id"]: kp for kp in load_kps(DEFAULT_DB)}
        replaced = []
        for qid in [x.strip() for x in args.refresh_qids.split(",") if x.strip()]:
            old = by_id.get(qid)
            if old is None:
                print(f"跳过不存在的 q_id：{qid}")
                continue
            kp = kps[old.kp_id]
            fresh = generate_template_questions(kp["name"], kp["kp_id"], args.seed)
            new = next((q for q in fresh if q.difficulty == old.difficulty), None)
            if new is None:
                print(f"跳过无法重生成的 q_id：{qid}")
                continue
            by_id[qid] = new
            replaced.append(qid)
        questions = sorted(by_id.values(), key=lambda q: (q.kp_id, q.difficulty))
        _save(DEFAULT_CANDIDATE, questions)
        print(f"已替换：{', '.join(replaced)}")
        return 0
    if args.refresh_templates:
        from backend.app.demo.generation import generate_templates
        questions = [q for q in _load(DEFAULT_CANDIDATE)
                     if q.generation_method != "template"]
        kps = load_kps(DEFAULT_DB)
        template_kps, _ = split_kps(kps)
        questions += generate_templates(template_kps, args.seed)
        questions = sorted(questions, key=lambda q: (q.kp_id, q.difficulty))
        _save(DEFAULT_CANDIDATE, questions)
        print(f"模板题已刷新；当前总题数 {len(questions)}")
        return 0
    if args.salvage:
        questions = _load(DEFAULT_CANDIDATE)
        added, stats = salvage_from_raw(DEFAULT_RAW, questions)
        questions = sorted(questions + added, key=lambda q: (q.kp_id, q.difficulty))
        _save(DEFAULT_CANDIDATE, questions)
        print(json.dumps({
            "before": len(questions) - len(added),
            "salvaged": len(added),
            "total": len(questions),
            **stats,
        }, ensure_ascii=False, indent=2))
        return 0
    if args.validate:
        if args.llm_review and not args.allow_live_api:
            print("拒绝：--llm-review 需要 --allow-live-api", file=sys.stderr)
            return 2
        questions = _load(DEFAULT_CANDIDATE)
        _validate(questions, llm_review=args.llm_review, budget_cap=args.budget_cap)
        return 0
    if args.report:
        questions = _load(DEFAULT_CANDIDATE)
        kps = load_kps(DEFAULT_DB)
        write_coverage_report(kps, questions, COVERAGE_MD, COVERAGE_JSON)
        write_validation_report(questions, VALIDATION_MD, VALIDATION_JSON)
        write_quality_report(questions, QUALITY_MD, QUALITY_JSON)
        print(f"报告：{COVERAGE_MD}")
        return 0
    if args.review_sheet:
        questions = _load(DEFAULT_CANDIDATE)
        sample = write_review_sheet(questions, REVIEW_CSV, REVIEW_MD)
        print(f"抽检表：{REVIEW_CSV}（{len(sample)} 题）")
        return 0
    if args.freeze:
        questions = _load(DEFAULT_CANDIDATE)
        return _freeze(questions)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
