"""合成演示班级的业务数据设计（Phase 6 / S2-B）。

目标：用已冻结的 410 题合成题库，构造 20 名学生 × 2 场考试 × 20 题的
真实答题记录，使诊断/推荐由答题结果自然产生，而不是写死结论。

设计要点：
- 两场考试覆盖不同 KP（A：几何 042–052 + 运算 074–082；B：应用题
  001–010 + 量率 037/038 + 立体几何 053–058 + 039/040）；
- 每个学生有整体能力与逐 KP 波动；班级存在真实薄弱 KP（074/075/078/
  079/037），并在近期考试中可观察到；
- 答案由固定 seed 的伯努利抽样生成；解答题允许部分分；
- 主/辅 KP 角色单独写映射清单，供诊断证据加权使用。
"""
from __future__ import annotations

import io
import json
import math
import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from backend.app.storage.templates import DATA_SHEET_ORDER, HEADER_DISPLAY, INSTRUCTIONS_SHEET

CLASS_NAME = "六年级演示班（合成题库 v1）"
CLASS_SCHOOL = "演示数据（非真实学校）"
EXAM_TITLES = {
    "A": "六年级数学演示测评（合成 A 卷）",
    "B": "六年级数学演示测评（合成 B 卷）",
}
EXAM_DATES = {"A": "2026-09-15", "B": "2026-11-10"}
STUDENT_COUNT = 20
QUESTIONS_PER_EXAM = 20
FULL_SCORE = 5.0

A_KPS = [f"kp_p_{i:03d}" for i in range(42, 53)] + [f"kp_p_{i:03d}" for i in range(74, 83)]
B_KPS = (
    [f"kp_p_{i:03d}" for i in range(1, 11)]
    + ["kp_p_037", "kp_p_038"]
    + [f"kp_p_{i:03d}" for i in range(53, 59)]
    + ["kp_p_039", "kp_p_040"]
)
A_DIFFICULTIES = [1, 2, 3] * 7
B_DIFFICULTIES = [3, 4, 5] * 7
WEAK_KPS = {"kp_p_074", "kp_p_075", "kp_p_078", "kp_p_079", "kp_p_037"}
STRONG_KPS = {"kp_p_042", "kp_p_049", "kp_p_050", "kp_p_053", "kp_p_076", "kp_p_036"}


def load_bank(path: Path) -> dict[str, dict]:
    return {
        data["q_id"]: data
        for data in (
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


def load_context(conn) -> tuple[dict[str, str], dict[str, list[str]]]:
    kps = {
        row[0]: row[1]
        for row in conn.execute(
            "SELECT kp_id, name FROM knowledge_points WHERE kp_id LIKE 'kp_p_%'"
        )
    }
    edges: dict[str, list[str]] = defaultdict(list)
    for from_kp, to_kp in conn.execute("SELECT from_kp, to_kp FROM kp_edges"):
        if from_kp in kps and to_kp in kps:
            edges[to_kp].append(from_kp)
    for value in edges.values():
        value.sort()
    return kps, edges


def _pick_question(bank: dict[str, dict], kp_id: str, difficulty: int) -> dict:
    exact = [q for q in bank.values() if q["kp_id"] == kp_id and q["difficulty"] == difficulty]
    if exact:
        return sorted(exact, key=lambda q: q["q_id"])[0]
    candidates = [q for q in bank.values() if q["kp_id"] == kp_id]
    if not candidates:
        raise ValueError(f"题库缺少 KP {kp_id}")
    return sorted(candidates, key=lambda q: (abs(q["difficulty"] - difficulty), q["q_id"]))[0]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def build_design(bank: dict[str, dict], kps: dict[str, str],
                 edges: dict[str, list[str]], seed: int = 20261120) -> dict:
    rng = random.Random(seed)
    exams: dict[str, list[dict]] = {"A": [], "B": []}
    role_rows: list[dict] = []
    for exam, kp_list, difficulties in (
        ("A", A_KPS, A_DIFFICULTIES), ("B", B_KPS, B_DIFFICULTIES)
    ):
        for idx, (kp_id, difficulty) in enumerate(zip(kp_list, difficulties), start=1):
            if kp_id not in kps:
                raise ValueError(f"KP 不存在：{kp_id}")
            q = _pick_question(bank, kp_id, difficulty)
            aux = [k for k in edges.get(kp_id, []) if k != kp_id][:2]
            all_ids = [kp_id] + [k for k in aux if k != kp_id]
            item = {
                "q_no": idx,
                "q_id": q["q_id"],
                "kp_ids": all_ids,
                "primary_kp": kp_id,
                "score": FULL_SCORE,
                "stem": q["stem"],
                "answer": q["answer"],
                "difficulty": q["difficulty"],
                "qtype": q.get("qtype", ""),
            }
            exams[exam].append(item)
            role_rows.append({
                "exam": exam,
                "q_no": idx,
                "q_id": q["q_id"],
                "primary_kp_id": kp_id,
                "primary_kp_name": kps[kp_id],
                "aux_kp_ids": aux,
                "aux_kp_names": [kps[k] for k in aux],
                "all_kp_ids": all_ids,
                "source": "synthetic_demo_v1",
                "synthetic_demo": True,
                "role_note": "primary/aux role preserved; auxiliary KPs are not equal-strength evidence",
            })

    students = []
    abilities: dict[str, float] = {}
    for i in range(STUDENT_COUNT):
        student_no = f"P{i + 1:02d}"
        # 能力均匀铺开 + 小噪声，保证班级分数有真实差异
        ability = 0.8 + (i - (STUDENT_COUNT - 1) / 2) * 0.09 + rng.gauss(0, 0.12)
        ability = max(-0.6, min(2.4, ability))
        abilities[student_no] = ability
        students.append({
            "student_no": student_no,
            "name": f"学生{i + 1:02d}",
            "ability": round(ability, 4),
        })

    student_kp_noise = {
        (s["student_no"], kp): rng.gauss(0, 0.18)
        for s in students for kp in set(A_KPS + B_KPS)
    }

    answers = []
    for exam, items in exams.items():
        for item in items:
            kp_id = item["primary_kp"]
            base_bias = -1.2 if kp_id in WEAK_KPS else (0.6 if kp_id in STRONG_KPS else 0.0)
            difficulty = item["difficulty"]
            for s in students:
                p = _sigmoid(
                    abilities[s["student_no"]] + base_bias
                    + student_kp_noise[(s["student_no"], kp_id)]
                    - 0.22 * (difficulty - 3)
                )
                correct = rng.random() < p
                if correct:
                    score, is_correct = FULL_SCORE, 1
                elif item.get("qtype") == "解答题" and rng.random() < 0.25:
                    score, is_correct = FULL_SCORE / 2, None
                else:
                    score, is_correct = 0.0, 0
                answers.append({
                    "exam": exam,
                    "student_no": s["student_no"],
                    "q_no": item["q_no"],
                    "score": score,
                    "full_score": FULL_SCORE,
                    "is_correct": is_correct,
                })

    return {
        "class": {"name": CLASS_NAME, "grade": 6, "stage": "primary",
                  "school": CLASS_SCHOOL},
        "exam_meta": {
            exam: {"title": EXAM_TITLES[exam], "date": EXAM_DATES[exam]}
            for exam in ("A", "B")
        },
        "exams": exams,
        "students": students,
        "answers": answers,
        "role_map": role_rows,
        "seed": seed,
    }


def validate_design(design: dict, bank: dict[str, dict], kps: dict[str, str]) -> list[str]:
    errors: list[str] = []
    students = design["students"]
    exams = design["exams"]
    if len(students) != STUDENT_COUNT:
        errors.append(f"学生数应为 {STUDENT_COUNT}，实际 {len(students)}")
    if set(exams) != {"A", "B"}:
        errors.append("考试应恰为 A/B")
    for exam, items in exams.items():
        if len(items) != QUESTIONS_PER_EXAM:
            errors.append(f"{exam} 题数应为 {QUESTIONS_PER_EXAM}，实际 {len(items)}")
        if sum(item["score"] for item in items) != 100:
            errors.append(f"{exam} 总分应为 100")
        qnos = [item["q_no"] for item in items]
        if len(set(qnos)) != len(qnos):
            errors.append(f"{exam} 题号重复")
        for item in items:
            if item["q_id"] not in bank:
                errors.append(f"{exam} 第 {item['q_no']} 题 q_id 不在题库")
            if item["primary_kp"] not in item["kp_ids"]:
                errors.append(f"{exam} 第 {item['q_no']} 题主 KP 不在 kp_ids")
            if any(k not in kps for k in item["kp_ids"]):
                errors.append(f"{exam} 第 {item['q_no']} 题含未知 KP")
    if set(A_KPS) & set(B_KPS):
        errors.append("A/B 两场考试存在重叠 KP，不满足“覆盖不同 KP”")
    expected = len(students) * sum(len(v) for v in exams.values())
    if len(design["answers"]) != expected:
        errors.append(f"答题记录应为 {expected}，实际 {len(design['answers'])}")
    seen = set()
    for a in design["answers"]:
        key = (a["exam"], a["student_no"], a["q_no"])
        if key in seen:
            errors.append(f"答题重复：{key}")
        seen.add(key)
        if not (0 <= a["score"] <= a["full_score"]):
            errors.append(f"答题分值越界：{key}")
        if a["is_correct"] == 1 and a["score"] != a["full_score"]:
            errors.append(f"对但非满分：{key}")
        if a["is_correct"] == 0 and a["score"] != 0:
            errors.append(f"错但非 0 分：{key}")
    totals = defaultdict(float)
    for a in design["answers"]:
        totals[(a["exam"], a["student_no"])] += a["score"]
    values = list(totals.values())
    if values:
        avg = sum(values) / len(values)
        std = (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5
        if std < 5:
            errors.append(f"学生成绩差异过小：std={std:.2f}")
    if len(set(values)) < 5:
        errors.append("学生总分档位过少")
    return errors


def build_workbook(design: dict, kps: dict[str, str]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    fixed = datetime(2026, 1, 1)
    wb.properties.created = fixed
    wb.properties.modified = fixed
    cls = design["class"]

    ws = wb.create_sheet("班级")
    ws.append(HEADER_DISPLAY["班级"])
    ws.append([cls["name"], cls["grade"], cls["stage"], cls["school"]])

    ws = wb.create_sheet("学生")
    ws.append(HEADER_DISPLAY["学生"])
    for s in design["students"]:
        ws.append([cls["name"], s["student_no"], s["name"]])

    ws = wb.create_sheet("考试")
    ws.append(HEADER_DISPLAY["考试"])
    for exam in ("A", "B"):
        meta = design["exam_meta"][exam]
        total = sum(item["score"] for item in design["exams"][exam])
        ws.append([meta["title"], cls["name"], meta["date"], "math", "primary", total])

    ws = wb.create_sheet("考试题目")
    ws.append(HEADER_DISPLAY["考试题目"])
    for exam in ("A", "B"):
        title = design["exam_meta"][exam]["title"]
        for item in design["exams"][exam]:
            names = "、".join(kps[k] for k in item["kp_ids"])
            ws.append([title, item["q_no"], item["q_id"], names,
                       item["score"], item["stem"], item["answer"]])

    ws = wb.create_sheet("答题记录")
    ws.append(HEADER_DISPLAY["答题记录"])
    for a in design["answers"]:
        title = design["exam_meta"][a["exam"]]["title"]
        ic = a["is_correct"]
        label = "对" if ic == 1 else ("错" if ic == 0 else "")
        ws.append([title, cls["name"], a["student_no"], a["q_no"], a["score"], label])

    ws = wb.create_sheet(INSTRUCTIONS_SHEET)
    ws.append(["EduMate 小学演示数据（合成，非真实学生）"])
    for line in [
        "1. 本工作簿由合成演示题库生成；班级、学生、答题记录均为虚拟数据。",
        "2. 题目来自 data/demo/demo_bank_v1.jsonl（82 KP × 5 难度），不含 APE210K 内容。",
        "3. 主/辅知识点均写入“知识点”列；主/辅角色见班级专属映射清单。",
        "4. 不得将本数据描述为真实学生数据。",
    ]:
        ws.append([line])
    ws.column_dimensions["A"].width = 100

    for sheet in DATA_SHEET_ORDER:
        ws = wb[sheet]
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for col in range(1, len(HEADER_DISPLAY[sheet]) + 1):
            ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = 22

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def write_role_map(design: dict, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in design["role_map"]) + "\n",
        encoding="utf-8",
    )
    return len(design["role_map"])


def score_summary(design: dict) -> dict:
    totals = defaultdict(float)
    for a in design["answers"]:
        totals[(a["exam"], a["student_no"])] += a["score"]
    by_exam = {}
    for exam in ("A", "B"):
        values = [v for (e, _), v in totals.items() if e == exam]
        avg = sum(values) / len(values) if values else 0
        std = (sum((v - avg) ** 2 for v in values) / len(values)) ** 0.5 if values else 0
        by_exam[exam] = {
            "min": round(min(values), 1) if values else 0,
            "max": round(max(values), 1) if values else 0,
            "avg": round(avg, 1),
            "std": round(std, 1),
        }
    return {"by_exam": by_exam, "students": len(design["students"])}
