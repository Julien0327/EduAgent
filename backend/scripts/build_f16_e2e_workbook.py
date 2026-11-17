"""前端 F16 导入端到端验收工作簿构建（Phase 3 前置任务，不导入）。

输入：已审核的 ``data/staging/primary_demo_design.json``（49 道 verified 题 + 答题设计）。
输出：
- ``data/staging/f16_e2e_workbook{run_suffix}.xlsx``（合成演示数据，不入 Git）
- ``data/staging/f16_e2e_workbook_report.md/.json``（导入前检查报告，不入 Git）

设计纪律（与 demo 工作簿一致）：
- **题目与题序完全复用 demo 的 49 道 verified 题**（同一 q_id、同一 A/B 题序、
  同一主/辅 KP 名称），从而主/辅角色映射（``primary_demo_mapping.jsonl`` 按
  「考试标签 A/B + 题号」键）对诊断服务天然可解析——**零诊断代码改动**。
- **班级/学生/考试/答题数据全新**：新班级名、20 名虚拟学生、新考试标题与日期、
  答题整体复用 demo 设计的分值分布（合成演示数据，非真实学生）。
- 合成标记：班级 school 字段、考试标题、工作簿文件名、填写说明、报告均标注“演示/合成”。
- F16 模板格式严格沿用 ``templates.HEADER_DISPLAY``；不改后端导入逻辑/数据库结构。

``run_suffix`` 可选：追加到班级名/考试标题/学生名，用于生成“全新一版”工作簿
（同一文件重复导入会被指纹拒绝；验收复验时用新 suffix 即可重新上传）。

用法（仓库根执行）：
    .venv/Scripts/python.exe -X utf8 -m backend.scripts.build_f16_e2e_workbook [--run-suffix s2]
"""
from __future__ import annotations

import argparse
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

BASE_CLASS_NAME = "六（1）班（F16导入演示）"
BASE_EXAM_TITLES = {"A": "六（1）班测评（一）", "B": "六（1）班测评（二）"}
EXAM_DATES = {"A": "2026-10-12", "B": "2026-11-16"}  # A < B：诊断按日期升序设标签
CLASS_SCHOOL = "演示数据（非真实学校）"
FIXED_DOC_TIME = datetime(2026, 1, 1)


def _kps_and_questions():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        kps = {r["kp_id"]: r["name"] for r in conn.execute(
            "SELECT kp_id, name FROM knowledge_points WHERE stage='primary'")}
        qs = {r["q_id"]: {"stem": r["stem"], "answer": r["answer"]}
              for r in conn.execute(
                  "SELECT q_id, stem, answer FROM questions WHERE quality_state='processed'")}
    finally:
        conn.close()
    return kps, qs


def _resolve_names(kp_ids, kps, primary=None):
    """返回 kp 名称列表（primary 放在第一位），与 demo 工作簿生成逻辑一致。"""
    unique = list(dict.fromkeys(kp_ids))
    if primary and primary in unique:
        unique = [primary] + [k for k in unique if k != primary]
    names = [kps[k] for k in unique if k in kps]
    return names


def _validate_design(design: dict) -> list[str]:
    errors = []
    if set(design.get("exams", {})) != {"A", "B"}:
        errors.append("设计应包含 A/B 两场考试")
    total = sum(len(design["exams"].get(e, [])) for e in ("A", "B"))
    if total != 49:
        errors.append(f"题目总数应为 49，实际 {total}")
    if len(design.get("students", [])) != 20:
        errors.append("学生数应为 20")
    return errors


def build_e2e_workbook(run_suffix: str = "") -> bytes:
    """构建全新班级的 F16 工作簿（题目复用 49 道 verified 题；班级/学生/考试/答题全新）。"""
    design = json.loads(DESIGN_FILE.read_text(encoding="utf-8"))
    kps, qs = _kps_and_questions()

    suffix = run_suffix or ""
    class_name = BASE_CLASS_NAME + (f" {suffix}" if suffix else "")
    exam_title = {e: t + (f"（{suffix}）" if suffix else "") for e, t in BASE_EXAM_TITLES.items()}
    # demo 学生 → 新学号（P01..P20），答题记录按新学号写
    new_student_no = {s["student_id"]: f"P{i:02d}" for i, s in enumerate(design["students"], 1)}

    # 考试题目 → (考试, 题号) 映射（沿用设计顺序 q_no=1..N）
    q_no_of: dict[tuple[str, str], int] = {}
    for e in ("A", "B"):
        for i, q in enumerate(design["exams"][e], 1):
            q_no_of[(e, q["q_id"])] = i

    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.created = FIXED_DOC_TIME
    wb.properties.modified = FIXED_DOC_TIME
    exam_totals = {}

    ws = wb.create_sheet("班级")
    ws.append(HEADER_DISPLAY["班级"])
    ws.append([class_name, 6, "primary", CLASS_SCHOOL])

    ws = wb.create_sheet("学生")
    ws.append(HEADER_DISPLAY["学生"])
    for i, s in enumerate(design["students"], 1):
        ws.append([class_name, f"P{i:02d}", f"演示生{i:02d}"])

    ws = wb.create_sheet("考试")
    ws.append(HEADER_DISPLAY["考试"])
    for e in ("A", "B"):
        total = sum(q["score"] for q in design["exams"][e])
        exam_totals[e] = total
        ws.append([exam_title[e], class_name, EXAM_DATES[e], "math", "primary", total])

    ws = wb.create_sheet("考试题目")
    ws.append(HEADER_DISPLAY["考试题目"])
    missing_answer = []
    for e in ("A", "B"):
        for q in design["exams"][e]:
            meta = qs.get(q["q_id"])
            if meta is None:
                missing_answer.append(q["q_id"])
                continue
            names = _resolve_names(q["kp_ids"], kps, q.get("primary_kp"))
            ws.append([exam_title[e], q_no_of[(e, q["q_id"])], q["q_id"],
                       "、".join(names), q["score"], meta["stem"], meta["answer"]])

    ws = wb.create_sheet("答题记录")
    ws.append(HEADER_DISPLAY["答题记录"])
    for a in design["answers"]:
        title = exam_title[a["exam"]]
        ic = a.get("is_correct")
        label = "对" if ic == 1 else ("错" if ic == 0 else "")
        ws.append([title, class_name, new_student_no[a["student_id"]],
                   q_no_of[(a["exam"], a["q_id"])], a["score"], label])

    ws = wb.create_sheet(INSTRUCTIONS_SHEET)
    ws.append([f"EduMate 前端 F16 导入 E2E 验收工作簿（合成：班级/学生/答题为虚拟数据{suffix})"])
    for line in [
        "1. 题目复用 49 道 verified 小学题（q_id 与知识点映射来自 Gold v2 confirmed），同一 A/B 题序。",
        "2. 班级、学生、考试标题为全新合成；不得描述为真实学生数据。",
        "3. 通过前端「数据导入」页上传即可走现有 F16 导入 API；主/辅角色由演示映射清单解析。",
    ]:
        ws.append([line])
    ws.column_dimensions["A"].width = 100

    for sheet in DATA_SHEET_ORDER:
        ws = wb[sheet]
        for cell in ws[1]:
            cell.font = Font(bold=True)

    if missing_answer:
        raise RuntimeError(f"题库缺失题目快照：{missing_answer[:5]}")
    buf = io.BytesIO()
    wb.properties.modified = FIXED_DOC_TIME  # 保存前最后固化 → 内容确定性
    wb.save(buf)
    return _pin_zip_timestamps(buf.getvalue(), FIXED_DOC_TIME)


def _pin_zip_timestamps(data: bytes, dt) -> bytes:
    """重打包 xlsx：把 ZIP 条目标时间固定为 dt（openpyxl 默认写当前时刻，跨进程血统不稳定）。

    内容字节不变（同输入 + 同 DEFLATE level 输出确定），仅时间戳恒定 → 全链路字节确定，
    使同一份导出的工作簿重复上传稳定触发「指纹重复 409」语义。
    """
    import re
    import zipfile

    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    fixed_size = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=(dt.year, dt.month, dt.day,
                                                           dt.hour, dt.minute, dt.second))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = info.external_attr
            content = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                # openpyxl 保存时把 dcterms:modified 覆盖为当前时刻 → 这里改写为固定值
                content = re.sub(
                    r'(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)',
                    lambda m: m.group(1) + fixed_size + m.group(2),
                    content.decode("utf-8")).encode("utf-8")
            z.writestr(zi, content)
    src.close()
    return out.getvalue()


def readback(design: dict, data: bytes, class_name: str) -> list[str]:
    """重新读取 xlsx，核对表名/表头/行数。"""
    errors = []
    wb = load_workbook(io.BytesIO(data), data_only=True)
    expected = {
        "班级": 2, "学生": 1 + len(design["students"]),
        "考试": 1 + 2, "考试题目": 1 + 49, "答题记录": 1 + len(design["answers"]),
    }
    for sheet, n in expected.items():
        ws = wb[sheet]
        if ws.max_row != n:
            errors.append(f"{sheet} 行数 {ws.max_row} != {n}")
        if [c.value for c in ws[1]] != HEADER_DISPLAY[sheet]:
            errors.append(f"{sheet} 表头不符")
    if [c.value for c in wb["班级"][2]] != [class_name, 6, "primary", CLASS_SCHOOL]:
        errors.append("班级行不符")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-suffix", default="", help="班级/考试/学生名后缀（生成全新版本工作簿）")
    args = ap.parse_args()

    design = json.loads(DESIGN_FILE.read_text(encoding="utf-8"))
    checks = _validate_design(design)
    data = build_e2e_workbook(args.run_suffix)
    class_name = BASE_CLASS_NAME + (f" {args.run_suffix}" if args.run_suffix else "")
    rb = readback(design, data, class_name)

    suffix = args.run_suffix or ""
    out_xlsx = REPO_ROOT / "data" / "staging" / f"f16_e2e_workbook{suffix}.xlsx"
    out_xlsx.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_suffix": args.run_suffix or None,
        "synthetic": "演示/合成（非真实学生）",
        "class": {"name": class_name, "grade": 6, "stage": "primary"},
        "exam_dates": EXAM_DATES,
        "exam_totals": {e: sum(q["score"] for q in design["exams"][e]) for e in ("A", "B")},
        "questions": 49,
        "answers": len(design["answers"]),
        "workbook": str(out_xlsx),
        "sha256": sha,
        "design_errors": checks,
        "readback_errors": rb,
    }
    out_json = REPO_ROOT / "data" / "staging" / f"f16_e2e_workbook_report{suffix}.json"
    out_md = REPO_ROOT / "data" / "staging" / f"f16_e2e_workbook_report{suffix}.md"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [
        "# 前端 F16 导入 E2E 工作簿构建报告",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 班级：{class_name}（6 年级 / primary；school 字段：{CLASS_SCHOOL}）",
        f"- 工作簿：`{out_xlsx.name}`；sha256 `{sha}`",
        f"- 题目：49 道 verified 小学题（复用 demo A/B 题序，主/辅角色可经演示映射清单解析）",
        f"- 设计/回读错误：{len(checks) + len(rb)}；检查：{checks}；回读：{rb}",
        "",
        "- 合成数据标记：班级 school 字段、考试标题、工作簿文件名、填写说明、本报告均标注“演示/合成”。",
        "- 未导入；通过前端「数据导入」页或 `importer.import_file` 走现有 F16 导入服务。",
    ]
    out_md.write_text("\n".join(md), encoding="utf-8")
    print(f"工作簿：{out_xlsx}（sha256 {sha[:16]}…）")
    print(f"检查：设计 {len(checks)} 错、回读 {len(rb)} 错；报告：{out_md}")
    return 0 if not checks and not rb else 1


if __name__ == "__main__":
    raise SystemExit(main())