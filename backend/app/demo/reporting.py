"""演示题库报告（Phase 6 / S2）。

产出：
- KP 覆盖率报告（82/82、每 KP 难度 1–5 齐全）；
- 校验报告（逐题状态与问题）；
- 质量报告（方法/题型/难度分布、查重、长度）；
- 人工抽检表（CSV + Markdown，至少 10%）；
- 冻结清单（hash、seed、模型、prompt 版本、许可）。
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from backend.app.demo.models import DemoQuestion
from backend.app.demo.validation import sample_for_human_review


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def coverage(kps: list[dict], questions: list[DemoQuestion]) -> dict:
    by_kp: dict[str, list[DemoQuestion]] = defaultdict(list)
    for q in questions:
        by_kp[q.kp_id].append(q)
    rows = []
    covered = 0
    quality_pass = 0
    missing = []
    for kp in kps:
        qs = by_kp.get(kp["kp_id"], [])
        diffs = sorted({q.difficulty for q in qs})
        has_five = len(qs) == 5 and diffs == [1, 2, 3, 4, 5]
        all_passed = has_five and all(q.validation_status == "passed" for q in qs)
        if has_five:
            covered += 1
        else:
            missing.append(kp["kp_id"])
        if all_passed:
            quality_pass += 1
        rows.append({
            "kp_id": kp["kp_id"],
            "kp_name": kp["name"],
            "count": len(qs),
            "difficulties": diffs,
            "has_five": has_five,
            "all_passed": all_passed,
            "methods": sorted({q.generation_method for q in qs}),
            "statuses": dict(Counter(q.validation_status for q in qs)),
        })
    return {
        "kps_total": len(kps),
        "kps_covered": covered,
        "coverage_rate": round(covered / len(kps), 4) if kps else 0.0,
        "kps_all_passed": quality_pass,
        "all_passed_rate": round(quality_pass / len(kps), 4) if kps else 0.0,
        "missing": missing,
        "per_kp": rows,
        "generated_at": _now(),
    }


def write_coverage_report(kps: list[dict], questions: list[DemoQuestion],
                          out_md: Path, out_json: Path) -> dict:
    data = coverage(kps, questions)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 演示题库 KP 覆盖率报告",
        "",
        f"- 生成时间：{data['generated_at']}",
        f"- KP 总数：{data['kps_total']}；已覆盖（5 题齐全）：{data['kps_covered']}；"
        f"覆盖率：{data['coverage_rate'] * 100:.2f}%",
        f"- 全部题目校验通过：{data['kps_all_passed']}/{data['kps_total']}"
        f"（{data['all_passed_rate'] * 100:.2f}%）",
        "",
        "| KP | 名称 | 题数 | 难度 | 方法 | 校验状态 |",
        "|---|---|---:|---|---|---|",
    ]
    for row in data["per_kp"]:
        status = " / ".join(f"{k}:{v}" for k, v in row["statuses"].items()) or "—"
        lines.append(
            f"| {row['kp_id']} | {row['kp_name']} | {row['count']} | "
            f"{row['difficulties']} | {','.join(row['methods'])} | {status} |"
        )
    if data["missing"]:
        lines += ["", f"未覆盖 KP：{', '.join(data['missing'])}"]
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return data


def write_validation_report(questions: list[DemoQuestion], out_md: Path, out_json: Path,
                            extra: dict | None = None) -> dict:
    statuses = Counter(q.validation_status for q in questions)
    issues = [
        {"q_id": q.q_id, "kp_id": q.kp_id, "difficulty": q.difficulty,
         "method": q.generation_method, "status": q.validation_status,
         "issues": q.validation_issues}
        for q in questions if q.validation_issues
    ]
    data = {
        "generated_at": _now(),
        "total": len(questions),
        "statuses": dict(statuses),
        "issues": issues,
        **(extra or {}),
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 演示题库校验报告",
        "",
        f"- 生成时间：{data['generated_at']}",
        f"- 总题数：{data['total']}；通过：{statuses.get('passed', 0)}；"
        f"待复核：{statuses.get('needs_review', 0)}",
    ]
    if extra:
        lines.append(f"- 附加统计：{json.dumps(extra, ensure_ascii=False)}")
    lines += ["", "## 问题明细", ""]
    if not issues:
        lines.append("- 无问题。")
    for item in issues[:500]:
        lines.append(
            f"- `{item['q_id']}`（{item['kp_id']}，难度 {item['difficulty']}，"
            f"{item['method']}）：{'；'.join(item['issues'])}"
        )
    if len(issues) > 500:
        lines.append(f"- …… 其余 {len(issues) - 500} 条见 JSON")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return data


def write_quality_report(questions: list[DemoQuestion], out_md: Path, out_json: Path) -> dict:
    method = Counter(q.generation_method for q in questions)
    qtype = Counter(q.qtype for q in questions)
    difficulty = Counter(str(q.difficulty) for q in questions)
    lengths = [len(q.stem) for q in questions]
    duplicates = [q.q_id for q in questions if q.duplicate_of]
    data = {
        "generated_at": _now(),
        "total": len(questions),
        "by_method": dict(method),
        "by_qtype": dict(qtype),
        "by_difficulty": dict(sorted(difficulty.items())),
        "stem_length": {
            "min": min(lengths) if lengths else 0,
            "max": max(lengths) if lengths else 0,
            "avg": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        },
        "duplicates": duplicates,
        "review_pending": sum(1 for q in questions if q.review_status == "pending"),
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 演示题库质量报告",
        "",
        f"- 生成时间：{data['generated_at']}",
        f"- 总题数：{data['total']}",
        f"- 生成方法：{json.dumps(data['by_method'], ensure_ascii=False)}",
        f"- 题型：{json.dumps(data['by_qtype'], ensure_ascii=False)}",
        f"- 难度分布：{json.dumps(data['by_difficulty'], ensure_ascii=False)}",
        f"- 题干长度：min={data['stem_length']['min']} / "
        f"avg={data['stem_length']['avg']} / max={data['stem_length']['max']}",
        f"- 查重命中：{len(duplicates)}",
        "",
    ]
    if duplicates:
        lines.append(f"重复题：{', '.join(duplicates)}")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return data


def write_review_sheet(questions: list[DemoQuestion], out_csv: Path, out_md: Path,
                       count: int = 41, seed: int = 20261120) -> list[DemoQuestion]:
    sample = sample_for_human_review(questions, count=count, seed=seed)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "sample_no", "q_id", "kp_id", "kp_name", "difficulty", "generation_method",
            "qtype", "stem", "options", "answer", "solution", "equation",
            "validation_status", "validation_issues",
            "human_pass(Y/N)", "human_score(1-5)", "human_issues", "notes",
        ])
        for idx, q in enumerate(sample, start=1):
            writer.writerow([
                idx, q.q_id, q.kp_id, q.kp_name, q.difficulty, q.generation_method,
                q.qtype, q.stem, " | ".join(q.options), q.answer, q.solution,
                q.equation or "", q.validation_status, "；".join(q.validation_issues),
                "", "", "", "",
            ])
    lines = [
        "# 演示题库人工抽检表",
        "",
        f"- 样本数：{len(sample)}（约 10%；优先 LLM 生成的应用题/几何题/复杂题）",
        f"- CSV：`{out_csv.name}`",
        "- 填写口径：`human_pass` 填 Y/N；`human_score` 1–5；发现问题写入 `human_issues`。",
        "",
        "| # | q_id | KP | 难度 | 方法 | 题干 | 答案 | 规则校验 |",
        "|---:|---|---|---:|---|---|---|---|",
    ]
    for idx, q in enumerate(sample, start=1):
        stem = q.stem.replace("|", "\\|")
        if len(stem) > 80:
            stem = stem[:80] + "…"
        lines.append(
            f"| {idx} | `{q.q_id}` | {q.kp_name} | {q.difficulty} | "
            f"{q.generation_method} | {stem} | {q.answer} | {q.validation_status} |"
        )
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sample


def write_manifest(path: Path, *, questions: list[DemoQuestion], seed: int,
                   stats: dict, out_json: Path) -> dict:
    data = {
        "generated_at": _now(),
        "path": str(path),
        "sha256": _sha256(path),
        "total": len(questions),
        "seed": seed,
        "prompt_version": stats.get("prompt_version"),
        "model": stats.get("model"),
        "source": "synthetic_demo_v1",
        "license": "MIT",
        "counts": {
            "by_method": dict(Counter(q.generation_method for q in questions)),
            "by_difficulty": dict(sorted(Counter(str(q.difficulty) for q in questions).items())),
        },
        "stats": stats,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
