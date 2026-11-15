"""小学演示数据 F16 工作簿构建与导入前检查（Phase 3 / S0-3，不导入）。

输入：已审核的 ``data/staging/primary_demo_design.json``
输出：
- ``data/staging/primary_demo_synthetic_workbook.xlsx``（合成演示数据，不入 Git）
- ``data/staging/primary_demo_workbook_report.md/.json``（导入前检查报告）
- ``data/annotations/primary_demo_mapping.jsonl``（Git；49 条 q_id→primary/aux KP
  映射清单，保留映射来源与角色，供 Phase 3 证据加权使用）

纪律：
- 不修改已审核的题目映射与答题设计；只把设计 JSON 转成 F16 工作簿。
- 主/辅知识点都写入“知识点”列（顿号分隔），导入后核对 kp_ids 集合完整；
  主/辅角色另存 mapping manifest（DB 的 kp_ids 是并集，不区分角色）。
- 合成数据明确标记：班级 school 字段、考试标题、工作簿文件名、报告均标注“演示/合成”。

用法（仓库根执行）：
    .venv/Scripts/python.exe -X utf8 -m backend.scripts.build_primary_demo_workbook
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font

from backend.app.storage.templates import DATA_SHEET_ORDER, HEADER_DISPLAY, INSTRUCTIONS_SHEET

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_FILE = REPO_ROOT / "data" / "staging" / "primary_demo_design.json"
DB_PATH = REPO_ROOT / "data" / "db" / "edumate.sqlite"
OUT_XLSX = REPO_ROOT / "data" / "staging" / "primary_demo_synthetic_workbook.xlsx"
OUT_MD = REPO_ROOT / "data" / "staging" / "primary_demo_workbook_report.md"
OUT_JSON = REPO_ROOT / "data" / "staging" / "primary_demo_workbook_report.json"
MAPPING_FILE = REPO_ROOT / "data" / "annotations" / "primary_demo_mapping.jsonl"

CLASS_SCHOOL = "演示数据（非真实学校）"
FIXED_DOC_TIME = datetime(2026, 1, 1)
EXAMS = ("A", "B")


def load_kps_and_questions():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        kps = {r["kp_id"]: {"kp_id": r["kp_id"], "name": r["name"]}
               for r in conn.execute(
                   "SELECT kp_id, name FROM knowledge_points WHERE stage='primary'")}
        qs = {r["q_id"]: dict(r) for r in conn.execute(
            "SELECT q_id, stem, answer, difficulty, stage, verified FROM questions"
            " WHERE verified=1")}
    finally:
        conn.close()
    return kps, qs


def resolve_names(kp_ids, kps, primary=None):
    """返回 (names_in_order, errors)。primary 放在第一位。"""
    errs = []
    unique = list(dict.fromkeys(kp_ids))
    if primary and primary in unique:
        unique = [primary] + [k for k in unique if k != primary]
    names = []
    for kp in unique:
        if kp not in kps:
            errs.append(f"KP 不存在：{kp}")
            continue
        names.append(kps[kp]["name"])
    if len(set(names)) != len(names):
        errs.append(f"KP 名称重复，无法唯一映射：{names}")
    return names, errs


def validate_design(design: dict, kps: dict, qmeta: dict) -> list[str]:
    """导入前设计/映射/答题一致性检查（纯函数，便于单测）。"""
    errors: list[str] = []
    if len(design.get("students", [])) != 20:
        errors.append(f"学生数应为 20，实际 {len(design.get('students', []))}")
    if set(design.get("exams", {})) != {"A", "B"}:
        errors.append("考试应恰为 A/B")
    total_q = sum(len(design["exams"].get(e, [])) for e in EXAMS)
    if total_q != 49:
        errors.append(f"题目数应为 49，实际 {total_q}")
    if len(design.get("answers", [])) != 980:
        errors.append(f"答题记录应为 980，实际 {len(design.get('answers', []))}")

    student_nos = [s["student_no"] for s in design["students"]]
    if len(set(student_nos)) != len(student_nos):
        errors.append("学号重复")
    design_qids = set()
    expected_answers = set()
    for e in EXAMS:
        seen_qno = set()
        for i, q in enumerate(design["exams"][e], 1):
            if q["q_id"] not in qmeta:
                errors.append(f"{e} 第 {i} 题 q_id 不在 verified 题库：{q['q_id']}")
            design_qids.add(q["q_id"])
            if i in seen_qno:
                errors.append(f"{e} 题号重复：{i}")
            seen_qno.add(i)
            if q.get("score", 0) <= 0:
                errors.append(f"{e} 第 {i} 题分值必须 >0")
            if not q.get("kp_ids"):
                errors.append(f"{e} 第 {i} 题缺 KP 映射")
            if q.get("primary_kp") not in q.get("kp_ids", []):
                errors.append(f"{e} 第 {i} 题 primary_kp 不在 kp_ids 中")
            if len(set(q.get("kp_ids", []))) != len(q.get("kp_ids", [])):
                errors.append(f"{e} 第 {i} 题 kp_ids 有重复")
            names, errs = resolve_names(q["kp_ids"], kps, q.get("primary_kp"))
            errors.extend(f"{e} 第 {i} 题：{x}" for x in errs)
            if not names:
                errors.append(f"{e} 第 {i} 题没有可写入的 KP 名称")
            for s in design["students"]:
                expected_answers.add((e, s["student_id"], q["q_id"]))

    actual_answers = {}
    for a in design["answers"]:
        key = (a["exam"], a["student_id"], a["q_id"])
        if key in actual_answers:
            errors.append(f"答题重复：{key}")
        actual_answers[key] = a
        if a["score"] < 0 or a["score"] > a["full_score"]:
            errors.append(f"答题分值越界：{key} {a['score']}/{a['full_score']}")
        ic = a.get("is_correct")
        if ic == 1 and a["score"] != a["full_score"]:
            errors.append(f"对错/分值不一致（对但非满分）：{key}")
        if ic == 0 and a["score"] != 0:
            errors.append(f"对错/分值不一致（错但非 0）：{key}")
        if ic is None and not (0 < a["score"] < a["full_score"]):
            errors.append(f"未判分但分值不是部分分：{key}")
    missing = expected_answers - set(actual_answers)
    extra = set(actual_answers) - expected_answers
    if missing:
        errors.append(f"答题矩阵缺 {len(missing)} 条（示例 {sorted(missing)[:3]}）")
    if extra:
        errors.append(f"答题矩阵多 {len(extra)} 条（示例 {sorted(extra)[:3]}）")

    # 每场考试总分与题目分值合计
    for e in EXAMS:
        total = sum(q["score"] for q in design["exams"][e])
        if total <= 0:
            errors.append(f"{e} 卷总分必须 >0")
    return errors


def build_workbook(design: dict, kps: dict, qmeta: dict) -> tuple[bytes, dict]:
    """生成 F16 工作簿字节，返回 (bytes, 汇总)。"""
    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.created = FIXED_DOC_TIME
    wb.properties.modified = FIXED_DOC_TIME
    summary = {"exam_totals": {}, "exam_questions": {}, "answer_counts": {}}

    cls = design["class"]
    ws = wb.create_sheet("班级")
    ws.append(HEADER_DISPLAY["班级"])
    ws.append([cls["name"], cls["grade"], cls["stage"], CLASS_SCHOOL])

    ws = wb.create_sheet("学生")
    ws.append(HEADER_DISPLAY["学生"])
    for s in design["students"]:
        ws.append([cls["name"], s["student_no"], s["name"]])

    ws = wb.create_sheet("考试")
    ws.append(HEADER_DISPLAY["考试"])
    for e in EXAMS:
        meta = design["exam_meta"][e]
        total = sum(q["score"] for q in design["exams"][e])
        summary["exam_totals"][e] = total
        ws.append([meta["title"], cls["name"], meta["date"], "math", "primary", total])

    qno_of = {}
    ws = wb.create_sheet("考试题目")
    ws.append(HEADER_DISPLAY["考试题目"])
    for e in EXAMS:
        title = design["exam_meta"][e]["title"]
        for i, q in enumerate(design["exams"][e], 1):
            qno_of[(e, q["q_id"])] = i
            names, _ = resolve_names(q["kp_ids"], kps, q.get("primary_kp"))
            qmeta_row = qmeta[q["q_id"]]
            ws.append([title, i, q["q_id"], "、".join(names), q["score"],
                       qmeta_row["stem"], qmeta_row["answer"]])
    for e in EXAMS:
        summary["exam_questions"][e] = len(design["exams"][e])

    ws = wb.create_sheet("答题记录")
    ws.append(HEADER_DISPLAY["答题记录"])
    student_no = {s["student_id"]: s["student_no"] for s in design["students"]}
    counts = {e: {"full": 0, "partial": 0, "zero": 0} for e in EXAMS}
    for a in design["answers"]:
        title = design["exam_meta"][a["exam"]]["title"]
        ic = a.get("is_correct")
        label = "对" if ic == 1 else ("错" if ic == 0 else "")
        counts[a["exam"]]["full" if ic == 1 else ("zero" if ic == 0 else "partial")] += 1
        ws.append([title, cls["name"], student_no[a["student_id"]],
                   qno_of[(a["exam"], a["q_id"])], a["score"], label])
    summary["answer_counts"] = counts

    ws = wb.create_sheet(INSTRUCTIONS_SHEET)
    ws.append(["EduMate 小学演示数据（合成，非真实学生）"])
    for line in [
        "1. 本工作簿为 Phase 3 演示与业务验证用合成数据；班级、学生、答题记录均为虚拟生成。",
        "2. 题目为 49 道 verified 小学题，q_id 与知识点映射来自 Gold v2 confirmed。",
        "3. 主知识点与辅助知识点均写入“知识点”列；主/辅角色见 data/annotations/primary_demo_mapping.jsonl。",
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
    return buf.getvalue(), summary


def readback(design: dict, data: bytes) -> list[str]:
    """重新读取 xlsx，核对表名/表头/行数/关键字段。"""
    errors = []
    wb = load_workbook(io.BytesIO(data), data_only=True)
    expected_rows = {
        "班级": 2, "学生": 1 + len(design["students"]),
        "考试": 1 + 2,
        "考试题目": 1 + sum(len(design["exams"][e]) for e in EXAMS),
        "答题记录": 1 + len(design["answers"]),
    }
    for sheet, n in expected_rows.items():
        if sheet not in wb.sheetnames:
            errors.append(f"缺少工作表：{sheet}")
            continue
        ws = wb[sheet]
        if ws.max_row != n:
            errors.append(f"{sheet} 行数 {ws.max_row} != {n}")
        headers = [c.value for c in ws[1]]
        if headers != HEADER_DISPLAY[sheet]:
            errors.append(f"{sheet} 表头不符：{headers}")
    if INSTRUCTIONS_SHEET not in wb.sheetnames:
        errors.append("缺少填写说明表")
    # 抽查：第一道题的 q_id / KP 名称 / 分值
    ws = wb["考试题目"]
    first = [c.value for c in ws[2]]
    q0 = design["exams"]["A"][0]
    if first[1] != 1 or first[2] != q0["q_id"] or first[4] != q0["score"]:
        errors.append(f"考试题目首行不符：{first[:5]}")
    return errors


def write_mapping(design: dict, kps: dict) -> int:
    rows = []
    for e in EXAMS:
        for i, q in enumerate(design["exams"][e], 1):
            primary = q["primary_kp"]
            aux = [k for k in dict.fromkeys(q["kp_ids"]) if k != primary]
            rows.append({
                "exam": e,
                "q_no": i,
                "q_id": q["q_id"],
                "primary_kp_id": primary,
                "primary_kp_name": kps[primary]["name"],
                "aux_kp_ids": aux,
                "aux_kp_names": [kps[k]["name"] for k in aux],
                "all_kp_ids": list(dict.fromkeys(q["kp_ids"])),
                "source": "gold_v2_confirmed",
                "synthetic_demo": True,
                "role_note": "primary/aux role preserved; auxiliary KPs are not equal-strength evidence in diagnosis explanations",
            })
    MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
    MAPPING_FILE.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")
    return len(rows)


def render_report(design: dict, summary: dict, checks: list[str], rb: list[str],
                  sha: str, mapping_count: int) -> str:
    cls = design["class"]
    lines = [
        "# 小学演示数据 F16 工作簿构建与导入前检查报告",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- 状态：**工作簿已生成，未导入**；合成演示数据（非真实学生）",
        f"- 班级：{cls['name']}（{cls['grade']} 年级 / {cls['stage']}）；学校字段：{CLASS_SCHOOL}",
        f"- 工作簿：`{OUT_XLSX}`；sha256 `{sha}`",
        f"- 映射清单：`{MAPPING_FILE}`（{mapping_count} 条，Git）",
        "",
        "## 1. 检查结果",
        "",
        f"- 设计/映射/答题一致性错误：**{len(checks)}**",
        f"- 工作簿回读错误：**{len(rb)}**",
    ]
    for x in checks + rb:
        lines.append(f"  - ❌ {x}")
    if not checks and not rb:
        lines.append("  - ✅ 全部通过")
    lines += [
        "",
        "## 2. 卷面与答题汇总",
        "",
        "| 考试 | 题数 | 卷总分 | 满分/部分/零分 |",
        "|---|---|---|---|",
    ]
    for e in EXAMS:
        c = summary["answer_counts"][e]
        lines.append(f"| {e} | {summary['exam_questions'][e]} | "
                     f"{summary['exam_totals'][e]} | {c['full']}/{c['partial']}/{c['zero']} |")
    lines += [
        "",
        "## 3. 标记与边界",
        "",
        "- 合成数据标记：班级 school 字段、考试标题、工作簿文件名、填写说明表、本报告均标注“演示/合成”。",
        "- 题目与 KP：49 道 verified 小学题；主/辅知识点均写入工作簿；主/辅角色保存在 mapping manifest。",
        "- 不含真实学生姓名/学校；学生为 学生01–学生20。",
        "- 工作簿与完整答题在 `data/staging/`（不入 Git）；mapping manifest 不含题干/答案，可入 Git。",
        "",
        "## 4. 下一步",
        "",
        "- `python -m backend.scripts.import_primary_demo`：通过现有 F16 导入服务导入。",
        "- `python -m backend.scripts.verify_primary_demo_import`：导入后核验与掌握度一致性检查。",
    ]
    return "\n".join(lines)


def main() -> int:
    design = json.loads(DESIGN_FILE.read_text(encoding="utf-8"))
    kps, qmeta = load_kps_and_questions()
    checks = validate_design(design, kps, qmeta)
    data, summary = build_workbook(design, kps, qmeta)
    rb = readback(design, data)
    OUT_XLSX.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    mapping_count = write_mapping(design, kps)

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "built_not_imported",
        "synthetic_demo": True,
        "class": design["class"],
        "exam_totals": summary["exam_totals"],
        "exam_questions": summary["exam_questions"],
        "answer_counts": summary["answer_counts"],
        "workbook": str(OUT_XLSX),
        "sha256": sha,
        "mapping_manifest": str(MAPPING_FILE),
        "mapping_count": mapping_count,
        "checks": checks,
        "readback_errors": rb,
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_MD.write_text(render_report(design, summary, checks, rb, sha, mapping_count),
                      encoding="utf-8")
    print(f"工作簿：{OUT_XLSX}（sha256 {sha[:16]}…）")
    print(f"检查：设计/映射 {len(checks)} 错；回读 {len(rb)} 错；映射清单 {mapping_count} 条")
    print(f"报告：{OUT_MD}")
    return 0 if not checks and not rb else 1


if __name__ == "__main__":
    raise SystemExit(main())
