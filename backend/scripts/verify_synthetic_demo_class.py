"""验收已有合成演示班级的诊断闭环（Phase 6 / S2-B）。

用法：
    python -m backend.scripts.verify_synthetic_demo_class --class-id cls_xxx
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "db" / "edumate.sqlite"
OUT_JSON = REPO_ROOT / "data" / "staging" / "synthetic_demo_class_verify_report.json"
OUT_MD = REPO_ROOT / "data" / "staging" / "synthetic_demo_class_verify_report.md"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--class-id", required=True)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    os.environ.pop("DEEPSEEK_API_KEY", None)
    os.environ["EDUMATE_DB_PATH"] = args.db

    from backend.app.agent.workflow import run_class_diagnosis_task

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        cls = conn.execute("SELECT * FROM classes WHERE class_id=?", (args.class_id,)).fetchone()
        if cls is None:
            print(f"班级不存在：{args.class_id}")
            return 1
        counts = {
            "students": conn.execute(
                "SELECT COUNT(*) FROM students WHERE class_id=?", (args.class_id,)).fetchone()[0],
            "exams": conn.execute(
                "SELECT COUNT(*) FROM exams WHERE class_id=?", (args.class_id,)).fetchone()[0],
            "exam_questions": conn.execute(
                "SELECT COUNT(*) FROM exam_questions eq JOIN exams e ON eq.exam_id=e.exam_id"
                " WHERE e.class_id=?", (args.class_id,)).fetchone()[0],
            "answers": conn.execute(
                "SELECT COUNT(*) FROM student_answers sa JOIN exams e ON sa.exam_id=e.exam_id"
                " WHERE e.class_id=?", (args.class_id,)).fetchone()[0],
        }
        answer_states = {
            "correct": conn.execute(
                "SELECT COUNT(*) FROM student_answers sa JOIN exams e ON sa.exam_id=e.exam_id"
                " WHERE e.class_id=? AND sa.is_correct=1", (args.class_id,)).fetchone()[0],
            "wrong": conn.execute(
                "SELECT COUNT(*) FROM student_answers sa JOIN exams e ON sa.exam_id=e.exam_id"
                " WHERE e.class_id=? AND sa.is_correct=0", (args.class_id,)).fetchone()[0],
            "partial": conn.execute(
                "SELECT COUNT(*) FROM student_answers sa JOIN exams e ON sa.exam_id=e.exam_id"
                " WHERE e.class_id=? AND sa.is_correct IS NULL", (args.class_id,)).fetchone()[0],
        }
        kp_coverage = conn.execute(
            "SELECT COUNT(DISTINCT kp_id) FROM ("
            "  SELECT je.value AS kp_id FROM exam_questions eq"
            "  JOIN exams e ON eq.exam_id=e.exam_id, json_each(eq.kp_ids) AS je"
            "  WHERE e.class_id=?"
            ")", (args.class_id,)).fetchone()[0]
        totals = defaultdict(float)
        exam_title = {}
        for row in conn.execute(
            "SELECT sa.exam_id, sa.student_id, sa.score, e.title"
            " FROM student_answers sa JOIN exams e ON sa.exam_id=e.exam_id"
            " WHERE e.class_id=?", (args.class_id,)
        ):
            totals[(row["exam_id"], row["student_id"])] += row["score"]
            exam_title[row["exam_id"]] = row["title"]
        by_exam: dict[str, list[float]] = defaultdict(list)
        for (exam_id, _), value in totals.items():
            by_exam[exam_title[exam_id]].append(value)
        score_summary = {
            title: {
                "min": round(min(values), 1), "max": round(max(values), 1),
                "avg": round(mean(values), 1), "std": round(pstdev(values), 1),
            }
            for title, values in by_exam.items()
        }
    finally:
        conn.close()

    ctx = run_class_diagnosis_task(args.class_id)
    diagnosis_path = pdf_path = None
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
        weak = [kp_names.get(kp_id, kp_id) for kp_id, item in mastery.items()
                if isinstance(item, dict) and item.get("state") == "weak"]
        states: dict[str, int] = defaultdict(int)
        for item in mastery.values():
            if isinstance(item, dict):
                states[item.get("state", "unknown")] += 1
    else:
        weak = [m.get("kp_name", "") for m in mastery
                if isinstance(m, dict) and m.get("state") == "weak"]
        states = defaultdict(int)
        for item in mastery:
            states[item.get("state", "unknown")] += 1
    chains = diagnosis.get("chains", [])
    recommendations = diagnosis.get("recommendations", [])
    recommended_questions = [
        q for r in recommendations for q in r.get("questions", [])
    ]
    recommendation_sources: dict[str, int] = defaultdict(int)
    for q in recommended_questions:
        recommendation_sources[q.get("source") or "unknown"] += 1
    ape_recommendations = [
        q.get("q_id") for q in recommended_questions
        if str(q.get("q_id", "")).startswith("q_ape_")
    ]
    pdf_checks = None
    if pdf_path and pdf_path.exists():
        from pypdf import PdfReader
        text = "\n".join((page.extract_text() or "") for page in PdfReader(str(pdf_path)).pages)
        pdf_checks = {
            "class_name": cls["name"] in text,
            "synthetic": ("合成" in text or "演示" in text),
            "weak_kp": any(name in text for name in weak[:3]),
            "recommendation": "推荐" in text or "练习" in text,
        }
    ok = (
        counts == {"students": 20, "exams": 2, "exam_questions": 40, "answers": 800}
        and len(chains) > 0 and len(recommendations) > 0
        and not ape_recommendations
        and set(recommendation_sources) <= {"synthetic_demo_v1"}
        and pdf_checks is not None and all(pdf_checks.values())
        and min(v["std"] for v in score_summary.values()) > 5
    )
    result = {
        "ok": ok,
        "class_id": args.class_id,
        "class_name": cls["name"],
        "counts": counts,
        "answer_states": answer_states,
        "kp_coverage": kp_coverage,
        "score_summary": score_summary,
        "task_id": ctx.task_id,
        "task_status": ctx.status,
        "mastery_states": dict(states),
        "weak_kps": weak,
        "chain_count": len(chains),
        "recommendation_count": len(recommendations),
        "recommendation_groups": [
            {"role": r.get("role"), "kp": r.get("kp_name"),
             "chain_target": r.get("chain_target_name"),
             "question_count": len(r.get("questions", []))}
            for r in recommendations
        ],
        "recommendation_sources": dict(recommendation_sources),
        "ape_recommendations": ape_recommendations,
        "db": args.db,
        "diagnosis_path": str(diagnosis_path) if diagnosis_path else None,
        "pdf_path": str(pdf_path) if pdf_path else None,
        "pdf_checks": pdf_checks,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 合成演示班级诊断闭环验收",
        "",
        f"- 状态：**{'通过' if ok else '未通过'}**",
        f"- 班级：{cls['name']}（`{args.class_id}`）",
        f"- 计数：{counts}；答案状态：{answer_states}；覆盖 KP：{kp_coverage}",
        f"- 成绩：{json.dumps(score_summary, ensure_ascii=False)}",
        f"- 诊断任务：`{ctx.task_id}` / `{ctx.status}`",
        f"- 掌握度状态：{json.dumps(dict(states), ensure_ascii=False)}",
        f"- 薄弱 KP：{', '.join(weak) if weak else '（无）'}",
        f"- 归因链：{len(chains)} 条；推荐组：{len(recommendations)} 组",
        f"- PDF 核验：{pdf_checks}",
        "",
        "## 边界",
        "",
        "- 数据为合成演示，不得描述为真实学生数据。",
        "- 诊断链/推荐来自上述真实导入的答题记录，不是固定输出。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
