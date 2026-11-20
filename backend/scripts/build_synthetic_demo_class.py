"""构建合成演示班级并跑通诊断闭环（Phase 6 / S2-B）。

流程：
1. 读取冻结题库 `data/demo/demo_bank_v1.jsonl`（需先运行 load_demo_bank）；
2. 设计 20 学生 × 2 场考试 × 20 题，答案由固定 seed 的真实答题模型生成；
3. 生成 F16 工作簿，走现有 importer 导入；
4. 写班级专属主/辅映射 `data/annotations/role_map_{class_id}.jsonl`；
5. 运行 class_diagnosis 工作流，核验诊断链/推荐/PDF。

默认强制模板叙述（清空 DEEPSEEK_API_KEY），S2-B 零 API 成本；
真实 LLM 叙述可在部署验收时另跑。

用法：
    python -m backend.scripts.build_synthetic_demo_class [--run-suffix S1] [--allow-llm-narration]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "db" / "edumate.sqlite"
BANK = REPO_ROOT / "data" / "demo" / "demo_bank_v1.jsonl"
WORKBOOK = REPO_ROOT / "data" / "staging" / "synthetic_demo_class_workbook.xlsx"
MAPPING_DIR = REPO_ROOT / "data" / "annotations"
OUT_JSON = REPO_ROOT / "data" / "staging" / "synthetic_demo_class_report.json"
OUT_MD = REPO_ROOT / "data" / "staging" / "synthetic_demo_class_report.md"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    parser = argparse.ArgumentParser(description="构建合成演示班级并跑通诊断闭环")
    parser.add_argument("--run-suffix", default="",
                        help="班级/考试名称后缀，便于重复导入")
    parser.add_argument("--allow-llm-narration", action="store_true",
                        help="允许真实 LLM 叙述（默认模板兜底，零成本）")
    args = parser.parse_args()

    if not args.allow_llm_narration:
        os.environ.pop("DEEPSEEK_API_KEY", None)

    from backend.app.agent.workflow import run_class_diagnosis_task
    from backend.app.demo.business import (
        build_design, build_workbook, load_bank, load_context,
        score_summary, validate_design, write_role_map,
    )
    from backend.app.storage import importer

    if not BANK.exists():
        print(f"题库不存在：{BANK}")
        return 1
    bank = load_bank(BANK)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        kps, edges = load_context(conn)
    finally:
        conn.close()
    if len(kps) != 82:
        print(f"KP 数量异常：{len(kps)}")
        return 1

    design = build_design(bank, kps, edges, seed=20261120)
    suffix = args.run_suffix.strip()
    if suffix:
        design["class"]["name"] = f"{design['class']['name']} {suffix}"
        for exam in ("A", "B"):
            design["exam_meta"][exam]["title"] = (
                f"{design['exam_meta'][exam]['title']} {suffix}"
            )
    errors = validate_design(design, bank, kps)
    if errors:
        print("设计校验未通过：")
        for item in errors:
            print(f"- {item}")
        return 1

    summary = score_summary(design)
    wb_bytes = build_workbook(design, kps)
    WORKBOOK.parent.mkdir(parents=True, exist_ok=True)
    WORKBOOK.write_bytes(wb_bytes)
    sha = hashlib.sha256(wb_bytes).hexdigest()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        existing_class = conn.execute(
            "SELECT class_id FROM classes WHERE name=?", (design["class"]["name"],)
        ).fetchone()
        titles = [design["exam_meta"][e]["title"] for e in ("A", "B")]
        existing_exams = [r["title"] for r in conn.execute(
            f"SELECT title FROM exams WHERE title IN ({','.join('?' * len(titles))})",
            titles,
        )]
    finally:
        conn.close()
    if existing_class or existing_exams:
        print("已存在同名班级/考试，请使用 --run-suffix 换名后重跑："
              f"class={dict(existing_class) if existing_class else None} exams={existing_exams}")
        return 1

    report = importer.import_file(
        wb_bytes, WORKBOOK.name, kind="full", db_path=str(DB_PATH)
    )
    if report.get("status") != "committed" or report.get("failed_rows", 1) != 0:
        print(f"F16 导入失败：{json.dumps(report, ensure_ascii=False)[:2000]}")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        cls = conn.execute(
            "SELECT * FROM classes WHERE name=?", (design["class"]["name"],)
        ).fetchone()
        class_id = cls["class_id"]
        counts = {
            "students": conn.execute(
                "SELECT COUNT(*) FROM students WHERE class_id=?", (class_id,)).fetchone()[0],
            "exams": conn.execute(
                "SELECT COUNT(*) FROM exams WHERE class_id=?", (class_id,)).fetchone()[0],
            "exam_questions": conn.execute(
                "SELECT COUNT(*) FROM exam_questions eq JOIN exams e ON eq.exam_id=e.exam_id"
                " WHERE e.class_id=?", (class_id,)).fetchone()[0],
            "answers": conn.execute(
                "SELECT COUNT(*) FROM student_answers sa JOIN exams e ON sa.exam_id=e.exam_id"
                " WHERE e.class_id=?", (class_id,)).fetchone()[0],
        }
    finally:
        conn.close()

    mapping_path = MAPPING_DIR / f"role_map_{class_id}.jsonl"
    mapping_count = write_role_map(design, mapping_path)

    ctx = run_class_diagnosis_task(class_id)
    diagnosis_path = None
    pdf_path = None
    for artifact in getattr(ctx, "artifacts", []):
        name = getattr(artifact, "name", "")
        path = Path(getattr(artifact, "path", ""))
        if name == "诊断结果":
            diagnosis_path = path
        elif name == "学情诊断报告":
            pdf_path = path

    payload = (
        json.loads(diagnosis_path.read_text(encoding="utf-8"))
        if diagnosis_path and diagnosis_path.exists() else {}
    )
    diagnosis = payload.get("diagnosis", payload)
    mastery = diagnosis.get("mastery", {})
    kp_names = diagnosis.get("kp_names", {})
    if isinstance(mastery, dict):
        weak = [
            kp_names.get(kp_id, kp_id)
            for kp_id, item in mastery.items()
            if isinstance(item, dict) and item.get("state") == "weak"
        ]
    else:
        weak = [m.get("kp_name", m.get("kp_id", "")) for m in mastery
                if isinstance(m, dict) and m.get("state") == "weak"]
    chains = diagnosis.get("chains", [])
    recommendations = diagnosis.get("recommendations", [])
    pdf_text_ok = None
    if pdf_path and pdf_path.exists():
        from pypdf import PdfReader
        text = "\n".join(
            (page.extract_text() or "") for page in PdfReader(str(pdf_path)).pages
        )
        pdf_text_ok = {
            "class_name": design["class"]["name"] in text,
            "synthetic": "合成" in text or "演示" in text,
            "weak_kp": any(name in text for name in weak[:3]),
        }

    ok = (
        counts == {"students": 20, "exams": 2, "exam_questions": 40, "answers": 800}
        and mapping_count == 40
        and ctx.status == "completed"
        and len(chains) > 0
        and len(recommendations) > 0
        and pdf_text_ok is not None
        and all(pdf_text_ok.values())
    )
    result = {
        "ok": ok,
        "generated_at": _now(),
        "class_id": class_id,
        "class_name": design["class"]["name"],
        "batch_id": report.get("batch_id"),
        "workbook": str(WORKBOOK),
        "workbook_sha256": sha,
        "counts": counts,
        "mapping": str(mapping_path),
        "mapping_count": mapping_count,
        "score_summary": summary,
        "task_id": ctx.task_id,
        "task_status": ctx.status,
        "weak_kps": weak,
        "chain_count": len(chains),
        "recommendation_count": len(recommendations),
        "recommendation_groups": [
            {
                "role": r.get("role"),
                "kp": r.get("kp_name"),
                "chain_target": r.get("chain_target_name"),
                "question_count": len(r.get("questions", [])),
            }
            for r in recommendations
        ],
        "diagnosis_path": str(diagnosis_path) if diagnosis_path else None,
        "pdf_path": str(pdf_path) if pdf_path else None,
        "pdf_text_ok": pdf_text_ok,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 合成演示班级构建与诊断闭环报告",
        "",
        f"- 状态：**{'通过' if ok else '未通过'}**",
        f"- 班级：{result['class_name']}（`{class_id}`）；批次 `{report.get('batch_id')}`",
        f"- 工作簿：`{WORKBOOK.name}`；sha256 `{sha}`",
        f"- 计数：{counts}",
        f"- 映射：`{mapping_path.name}`（{mapping_count} 条）",
        f"- 成绩：{json.dumps(summary, ensure_ascii=False)}",
        f"- 诊断任务：`{ctx.task_id}`；状态 `{ctx.status}`",
        f"- 薄弱 KP：{', '.join(weak) if weak else '（无）'}",
        f"- 归因链：{len(chains)} 条；推荐组：{len(recommendations)} 组",
        f"- PDF 核验：{pdf_text_ok}",
        "",
        "## 边界",
        "",
        "- 班级/学生/答题均为合成数据，不得描述为真实学生数据。",
        "- 题目来自合成演示题库，不含 APE210K 内容。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
