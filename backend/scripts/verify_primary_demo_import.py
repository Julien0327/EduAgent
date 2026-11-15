"""小学演示数据导入后核验（Phase 3 / S0-3，只读）。

核对：
1. 班级/学生/考试/题目/答题记录数量与外键关联；
2. q_id → 主/辅 KP 集合完整（对照 mapping manifest）；
3. 答题分值/正确性逐条一致；
4. 班级级掌握度、近期 2:1、分值加权、insufficient 与设计口径一致；
5. 预期归因链一致；
6. 小学原有题库/图谱/检索/标注数据未被影响；
7. 真实检索冒烟。

输出：``data/staging/primary_demo_import_verification.md/.json``。

用法（仓库根执行）：
    .venv/Scripts/python.exe -X utf8 -m backend.scripts.verify_primary_demo_import
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from backend.scripts.design_primary_demo import attribute, load_graph

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "db" / "edumate.sqlite"
DESIGN_FILE = REPO_ROOT / "data" / "staging" / "primary_demo_design.json"
MAPPING_FILE = REPO_ROOT / "data" / "annotations" / "primary_demo_mapping.jsonl"
IMPORT_REPORT = REPO_ROOT / "data" / "staging" / "primary_demo_import_execution_report.json"
OUT_MD = REPO_ROOT / "data" / "staging" / "primary_demo_import_verification.md"
OUT_JSON = REPO_ROOT / "data" / "staging" / "primary_demo_import_verification.json"

CLASS_NAME = "六年级演示班"
EXAM_TITLES = ("六年级数学演示测评（一）", "六年级数学演示测评（二）")
PRIMARY_ASSETS = ("knowledge_points", "kp_edges", "kp_fts", "questions", "q_kp",
                  "q_kp_llm", "q_annotation_results", "tasks", "steps")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def exam_key(title: str) -> str:
    return "A" if title == EXAM_TITLES[0] else "B"


def main() -> int:
    design = json.loads(DESIGN_FILE.read_text(encoding="utf-8"))
    mapping = load_jsonl(MAPPING_FILE)
    expected_map = {(m["exam"], m["q_no"]): m for m in mapping}
    expected_answers = {
        (a["exam"], a["student_id"], a["q_id"]): a for a in design["answers"]
    }
    errors: list[str] = []
    warnings: list[str] = []

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        cls = conn.execute("SELECT * FROM classes WHERE name=?", (CLASS_NAME,)).fetchone()
        if not cls:
            print(f"未找到班级：{CLASS_NAME}")
            return 1
        class_id = cls["class_id"]
        students = {r["student_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM students WHERE class_id=?", (class_id,))}
        exams = {r["title"]: dict(r) for r in conn.execute(
            f"SELECT * FROM exams WHERE class_id=? AND title IN ({','.join('?' * len(EXAM_TITLES))})",
            (class_id, *EXAM_TITLES))}
        eq_rows = [dict(r) for r in conn.execute(
            "SELECT eq.id, eq.exam_id, eq.q_no, eq.q_id, eq.kp_ids, eq.score,"
            " e.title, q.verified, q.stage AS q_stage"
            " FROM exam_questions eq JOIN exams e ON e.exam_id=eq.exam_id"
            " JOIN questions q ON q.q_id=eq.q_id"
            " WHERE e.class_id=? ORDER BY e.title, eq.q_no", (class_id,))]
        answers = [dict(r) for r in conn.execute(
            "SELECT sa.exam_id, sa.student_id, sa.exam_q_id, sa.score, sa.is_correct,"
            " eq.q_no, eq.score AS full_score, eq.q_id, e.title"
            " FROM student_answers sa"
            " JOIN exam_questions eq ON eq.id=sa.exam_q_id"
            " JOIN exams e ON e.exam_id=sa.exam_id"
            " WHERE e.class_id=?", (class_id,))]

        # 1. 数量与关联
        counts = {
            "class": 1, "students": len(students), "exams": len(exams),
            "exam_questions": len(eq_rows), "answers": len(answers),
        }
        if len(students) != 20:
            errors.append(f"学生数 {len(students)} != 20")
        if len(exams) != 2:
            errors.append(f"考试数 {len(exams)} != 2")
        if len(eq_rows) != 49:
            errors.append(f"考试题目数 {len(eq_rows)} != 49")
        if len(answers) != 980:
            errors.append(f"答题数 {len(answers)} != 980")
        for s in students.values():
            if s["class_id"] != class_id:
                errors.append(f"学生班级关联错误：{s['student_id']}")
        for e in exams.values():
            if e["class_id"] != class_id:
                errors.append(f"考试班级关联错误：{e['title']}")

        # 2. q_id → 主/辅 KP 完整
        imported_map = {}
        primary_count = aux_count = 0
        for row in eq_rows:
            e = exam_key(row["title"])
            key = (e, row["q_no"])
            exp = expected_map.get(key)
            if not exp:
                errors.append(f"导入题不在映射清单：{key}")
                continue
            imported_map[key] = row
            if row["q_id"] != exp["q_id"]:
                errors.append(f"{key} q_id 不一致：{row['q_id']} != {exp['q_id']}")
            if row["verified"] != 1 or row["q_stage"] != "primary":
                errors.append(f"{key} q_id 非 verified primary：{row['q_id']}")
            got = set(json.loads(row["kp_ids"] or "[]"))
            want = set(exp["all_kp_ids"])
            if got != want:
                errors.append(f"{key} KP 集合不一致：缺 {sorted(want - got)} 多 {sorted(got - want)}")
            if exp["primary_kp_id"] not in got:
                errors.append(f"{key} 丢失主知识点：{exp['primary_kp_id']}")
            if len(got) != len(want):
                errors.append(f"{key} KP 数量不一致")
            primary_count += 1
            aux_count += len(exp["aux_kp_ids"])
        if len(imported_map) != 49:
            errors.append(f"导入映射覆盖 {len(imported_map)}/49")

        # 3. 答题逐条核对
        student_no = {s["student_id"]: s["student_no"] for s in students.values()}
        got_answers = {}
        for a in answers:
            e = exam_key(a["title"])
            key = (e, student_no[a["student_id"]], a["q_id"])
            if key in got_answers:
                errors.append(f"答题重复：{key}")
            got_answers[key] = a
            if a["score"] < 0 or a["score"] > a["full_score"]:
                errors.append(f"答题分值越界：{key}")
        exp_answers = {
            (a["exam"], next(s["student_no"] for s in design["students"]
                             if s["student_id"] == a["student_id"]), a["q_id"]): a
            for a in design["answers"]
        }
        if set(got_answers) != set(exp_answers):
            errors.append(
                f"答题键集合不一致：缺 {len(set(exp_answers) - set(got_answers))} "
                f"多 {len(set(got_answers) - set(exp_answers))}")
        for key, exp in exp_answers.items():
            got = got_answers.get(key)
            if not got:
                continue
            if abs(float(got["score"]) - float(exp["score"])) > 1e-9:
                errors.append(f"答题分值不一致：{key} {got['score']} != {exp['score']}")
            if got["is_correct"] != exp.get("is_correct"):
                errors.append(f"对错不一致：{key} {got['is_correct']} != {exp.get('is_correct')}")

        # 4. 掌握度重算（与设计同口径）
        # 先按 exam/q_no 取班级得分率
        by_eq = defaultdict(list)
        for a in answers:
            by_eq[a["exam_q_id"]].append(a)
        kp_items = defaultdict(list)
        for row in eq_rows:
            e = exam_key(row["title"])
            rows = by_eq[row["id"]]
            mean_ratio = sum(a["score"] / row["score"] for a in rows) / len(rows)
            for kp in json.loads(row["kp_ids"] or "[]"):
                kp_items[kp].append((e, row["q_id"], row["score"], mean_ratio))
        mastery = {}
        for kp, items in kp_items.items():
            num = den = eq_num = so_num = so_den = 0.0
            qids = set()
            exams_seen = set()
            for e, qid, score, mean in items:
                w = 2.0 if e == "B" else 1.0
                num += mean * score * w
                den += score * w
                eq_num += mean
                so_num += mean * score
                so_den += score
                qids.add(qid)
                exams_seen.add(e)
            mastery[kp] = {
                "mastery": round(num / den, 4),
                "equal_mastery": round(eq_num / len(items), 4),
                "score_only_mastery": round(so_num / so_den, 4),
                "samples": len(qids),
                "insufficient": len(qids) < 3,
                "weak": (len(qids) >= 3 and num / den < 0.6),
            }
        for kp, exp in design["mastery"].items():
            got = mastery.get(kp)
            if not got:
                errors.append(f"掌握度缺失：{kp}")
                continue
            for field in ("mastery", "equal_mastery", "score_only_mastery"):
                if abs(got[field] - exp[field]) > 1e-3:
                    errors.append(f"{kp} {field} 不一致：{got[field]} != {exp[field]}")
            if got["samples"] != exp["samples"]:
                errors.append(f"{kp} samples 不一致：{got['samples']} != {exp['samples']}")
            if got["insufficient"] != exp["insufficient"]:
                errors.append(f"{kp} insufficient 不一致")
            if got["weak"] != exp["weak"]:
                errors.append(f"{kp} weak 不一致")

        # 5. 归因链一致
        graph = load_graph()
        kps_names = {k: v for k, v in design["kps"].items()}
        recalc_chains = attribute(mastery, graph, kps_names)
        exp_chains = {
            (c["target_kp"], c["root_kp"], tuple(c["path"])) for c in design["chains"]
        }
        got_chains = {
            (c["target_kp"], c["root_kp"], tuple(c["path"])) for c in recalc_chains
        }
        if exp_chains != got_chains:
            errors.append(
                f"归因链不一致：缺 {sorted(exp_chains - got_chains)} "
                f"多 {sorted(got_chains - exp_chains)}")

        # 6. 小学原有资产未受影响（对照导入前快照）
        pre = {}
        if IMPORT_REPORT.exists():
            pre = json.loads(IMPORT_REPORT.read_text(encoding="utf-8")).get("pre_snapshot", {})
        asset_now = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in PRIMARY_ASSETS}
        for t in PRIMARY_ASSETS:
            if pre and pre.get(t) != asset_now[t]:
                errors.append(f"小学原有资产变化：{t} {pre.get(t)} -> {asset_now[t]}")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            errors.append(f"integrity_check={integrity}")
    finally:
        conn.close()

    # 7. 检索冒烟
    try:
        from backend.app.retrieval.service import RetrievalService
        svc = RetrievalService()
        try:
            kp_hits = svc.search_knowledge_points("鸡兔同笼", stage="primary", top_k=3)
            q_hits = svc.search_questions("分数加减应用题", stage="primary", question_limit=3)
        finally:
            svc.close()
        if not kp_hits:
            errors.append("检索冒烟：KP 无结果")
        if not q_hits["questions"]:
            errors.append("检索冒烟：题目无结果")
    except Exception as exc:
        errors.append(f"检索冒烟失败：{type(exc).__name__}: {exc}")
        kp_hits, q_hits = [], {"questions": []}

    ok = not errors
    lines = [
        "# 小学演示数据导入后核验报告",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- 状态：**{'通过' if ok else '未通过'}**；合成演示数据（非真实学生）",
        f"- 错误：**{len(errors)}**；警告：{len(warnings)}",
        "",
        "## 1. 记录数与关联",
        "",
        "| 项 | 结果 |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in counts.items()],
        f"| 主知识点映射 | {primary_count}（每题 1 个） |",
        f"| 辅助知识点映射 | {aux_count} |",
        "",
        "## 2. 掌握度与设计口径一致性",
        "",
        "| KP | 名称 | 掌握度（重算） | 设计值 | samples | insufficient | weak |",
        "|---|---|---|---|---|---|---|",
    ]
    for kp, m in sorted(mastery.items(), key=lambda x: (x[1]["mastery"], x[0])):
        exp = design["mastery"].get(kp, {})
        lines.append(
            f"| {kp} | {design['kps'][kp]['name']} | {m['mastery']:.4f} | "
            f"{exp.get('mastery', '-')} | {m['samples']} | "
            f"{'是' if m['insufficient'] else '否'} | {'是' if m['weak'] else '否'} |")
    lines += ["", "## 3. 归因链", "",
              "| 目标 | 根因 | 路径 |", "|---|---|---|",
              *[f"| {c['target_name']} | {c['root_name']} | {' → '.join(c['path_names'])} |"
                for c in recalc_chains],
              "", "## 4. 小学原有资产（导入前后）", "",
              "| 表 | 导入前 | 现在 |", "|---|---|---|",
              *[f"| `{t}` | {pre.get(t, '-')} | {asset_now[t]} |" for t in PRIMARY_ASSETS],
              "", "## 5. 检索冒烟", "",
              f"- KP：{[(x['kp_id'], x['name']) for x in kp_hits]}",
              f"- 题目：{[q['q_id'] for q in q_hits['questions']]}",
              "", "## 6. 错误/警告", ""]
    if errors:
        lines += [f"- ❌ {e}" for e in errors]
    else:
        lines.append("- 无错误。")
    if warnings:
        lines += [f"- ⚠️ {w}" for w in warnings]
    lines += ["", "> 主/辅 KP 角色来自 `data/annotations/primary_demo_mapping.jsonl`；"
              "辅助 KP 不作为同等强度证据，Phase 3 诊断解释实现时处理。"]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps({
        "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": ok,
        "synthetic_demo": True,
        "counts": counts,
        "primary_kp_mappings": primary_count,
        "aux_kp_mappings": aux_count,
        "mastery": mastery,
        "design_mastery": design["mastery"],
        "chains": recalc_chains,
        "design_chains": design["chains"],
        "errors": errors,
        "warnings": warnings,
        "primary_assets": {"before": pre, "after": asset_now},
        "retrieval_smoke": {"kps": kp_hits, "questions": q_hits["questions"]},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"核验：{'通过' if ok else '未通过'}；错误 {len(errors)}；警告 {len(warnings)}")
    for e in errors[:10]:
        print(f"  ❌ {e}")
    print(f"报告：{OUT_MD}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
