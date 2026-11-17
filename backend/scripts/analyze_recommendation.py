"""真实推荐结果报告（Phase 3 / S3，只读，λ=0.5）。

对演示班跑确定性诊断，产出推荐明细（真实 q_id/难度/来源/理由/放宽策略），
写入 staging 报告。不改库、不调 LLM。

用法（仓库根执行）：
    .venv/Scripts/python.exe -X utf8 -m backend.scripts.analyze_recommendation
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from backend.app.storage import db
from backend.app.diagnosis import service as diag_service

REPO_ROOT = Path(__file__).resolve().parents[2]
DB = REPO_ROOT / "data" / "db" / "edumate.sqlite"
OUT_MD = REPO_ROOT / "data" / "staging" / "recommendation_report.md"
OUT_JSON = REPO_ROOT / "data" / "staging" / "recommendation_report.json"


def _db_retriever():
    from backend.app.retrieval.question_search import expand_by_kp, rerank

    class DBRetriever:
        def search_by_kp(self, kp_ids, filters=None, question_limit=20,
                         target_difficulty=None):
            conn = db.connect(DB)
            try:
                cands = expand_by_kp(conn, kp_ids, filters, limit=question_limit * 3)
            finally:
                conn.close()
            return rerank(cands, kp_ids, target_difficulty=target_difficulty)[:question_limit]

    return DBRetriever()


def main() -> int:
    conn = db.connect(DB)
    try:
        cls = conn.execute(
            "SELECT class_id FROM classes WHERE name='六年级演示班' LIMIT 1").fetchone()
        if cls is None:
            print("未找到演示班")
            return 1
        diag = diag_service.build_diagnosis(conn, cls["class_id"], retriever=_db_retriever())
    finally:
        conn.close()

    recs = diag["recommendations"]
    lines = [
        "# 真实推荐结果报告（Phase 3 / S3）",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- 班级：{diag['data_scope']['class_name']}；λ=0.5",
        f"- 归因链 {len(diag['chains'])} 条；推荐分组 {len(recs)} 组",
        "",
        "## 1. 分组概览",
        "",
        "| 角色 | 知识点 | 掌握度 | 难度带 | 候选数 | 放宽 | 置信 |",
        "|---|---|---|---|---|---|---|",
    ]
    for g in recs:
        relax = g["relaxation"]["final_strategy"] if g["relaxation"] else "strict"
        lines.append(
            f"| {g['role']} | {g['kp_name']} | {g['mastery']:.4f} | "
            f"{g['difficulty_range'][0]}-{g['difficulty_range'][1]} | "
            f"{g['candidate_count']} | {relax} | {g['confidence']} |")

    lines += ["", "## 2. 逐组推荐明细（真实题库）", ""]
    for g in recs:
        lines.append(f"### {g['role']}｜{g['kp_name']}（{g['kp_id']}）")
        if g["relaxation"]:
            lines.append(f"- 放宽：{g['relaxation']['reason']}；策略={g['relaxation']['final_strategy']}")
        for q in g["questions"]:
            stem = q["stem"].replace("|", "｜")[:40]
            lines.append(
                f"- `{q['q_id']}` [{q['source']}] 难度{q['difficulty']}"
                + ("【已完成】" if q["completed"] else "")
                + f" ｜ {stem}")
        lines.append("")

    lines += [
        "## 3. 纪律",
        "",
        "- 推荐题目全部来自现有小学题库（source=ape210k），逐条 q_id 可回查；未混入任何 LLM 编题。",
        "- 默认排除本班已完成题；候选不足按难度带→已完成题放宽，并记录策略。",
        "- 合成演示班，推荐结果仅用于演示与业务验证，不代表真实教学效果。",
        f"- 原始数据：{OUT_JSON}",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "class_id": diag["data_scope"]["class_id"],
        "data_scope": diag["data_scope"],
        "recommendations": recs,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    total_q = sum(g["candidate_count"] for g in recs)
    print(f"归因链 {len(diag['chains'])}；推荐分组 {len(recs)}；推荐题合计 {total_q}")
    for g in recs:
        relax = g["relaxation"]["final_strategy"] if g["relaxation"] else "strict"
        print(f"  {g['role']:6s} {g['kp_name']:12s} mastery={g['mastery']:.4f} "
              f"band={g['difficulty_range']} n={g['candidate_count']} {relax}")
    print(f"报告：{OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
