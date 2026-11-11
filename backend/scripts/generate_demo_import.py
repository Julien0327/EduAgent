"""演示数据导入冒烟脚本（Phase 1 / Step 3）。

把仓库自带演示数据（data/questions/question_bank.json + data/demo/student_answers.json）
合成为 F16 统一模板工作簿（真实 xlsx，写入 data/staging/，不入 Git），再走
importer 完整七步管线导入业务库——验证"真实 Excel 端到端"链路（非 Mock）。

班级/学生/考试/题目/答题全部来自演示数据：
- 班级：九年级三班（grade=9）
- 考试：question_bank.title（一元二次方程 课堂小测，总分 100，绑定班级）
- 题目：10 题，q_id 留空（题干+答案快照），kp_names 从图谱真实匹配"一元二次方程"
- 答题：20 学生 × 10 题；is_correct 推断：满分→对，0 分→错，其余留空

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.generate_demo_import [--db <路径>] [--skip-import]
"""
import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from backend.app.knowledge import loader
from backend.app.storage import db, importer

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING_DIR = REPO_ROOT / "data" / "staging"
EXAM_DATE = "2026-10-15"  # 演示数据无日期字段，固定演示值
# 固定 docProps 时间戳：相同演示数据每次生成字节一致（openpyxl 默认写入当前时间，
# 会导致同内容文件 sha 不同、指纹判重失效——由业务规则层兜底，此处从源头固定）
FIXED_DOC_TIME = datetime(2026, 1, 1)


def build_demo_workbook() -> bytes:
    bank = loader.load_question_bank()
    answers = loader.load_student_answers()
    assert bank["quiz_id"] == answers["quiz_id"], "演示题库与答题数据 quiz_id 不一致"

    class_name = answers["class_name"]
    grade = 9  # 九年级
    questions = bank["questions"]
    students = answers["students"]

    conn = db.connect(None)
    try:
        kp_names = [
            row["name"] for row in conn.execute(
                "SELECT name FROM knowledge_points WHERE name LIKE '一元二次方程%'"
                " ORDER BY kp_id LIMIT ?",
                (len(questions),),
            )
        ]
    finally:
        conn.close()

    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.created = FIXED_DOC_TIME
    wb.properties.modified = FIXED_DOC_TIME

    ws = wb.create_sheet("班级")
    ws.append(["班级名称", "年级", "学段", "学校"])
    ws.append([class_name, grade, "junior", None])

    ws = wb.create_sheet("学生")
    ws.append(["班级", "学号", "姓名"])
    for s in students:
        ws.append([class_name, s["student_id"], s["name"]])

    ws = wb.create_sheet("考试")
    ws.append(["考试名称", "班级", "考试日期", "科目", "学段", "总分"])
    ws.append([bank["title"], class_name, EXAM_DATE, "math", "junior", bank["total_score"]])

    ws = wb.create_sheet("考试题目")
    ws.append(["考试名称", "题号", "题库ID", "知识点", "分值", "题干", "答案"])
    for i, q in enumerate(questions):
        ws.append([
            bank["title"], i + 1, None,  # q_id 留空：题库尚未入库（Step 4/5），用快照
            kp_names[i] if i < len(kp_names) else None,
            q["score"], q["stem"], q["answer"],
        ])

    ws = wb.create_sheet("答题记录")
    ws.append(["考试名称", "班级", "学号", "题号", "得分", "对错"])
    for s in students:
        for i, q in enumerate(questions):
            score = s["question_scores"][q["question_id"]]
            is_correct = "对" if score == q["score"] else ("错" if score == 0 else None)
            ws.append([bank["title"], class_name, s["student_id"], i + 1, score, is_correct])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def counts_stable(db_path) -> bool:
    """重放检查：库内 demo 计数应保持 1/20/1/10/200（防重未写入重复数据）。"""
    conn = db.connect(db_path)
    try:
        counts = {
            t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
            for t in ("classes", "students", "exams", "exam_questions", "student_answers")
        }
    finally:
        conn.close()
    print(f"库内计数：{counts}")
    return counts == {"classes": 1, "students": 20, "exams": 1,
                      "exam_questions": 10, "student_answers": 200}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成演示导入工作簿并走完整导入管线")
    parser.add_argument("--db", default=None, help="业务库路径（默认 data/db/edumate.sqlite）")
    parser.add_argument("--skip-import", action="store_true", help="只生成 xlsx 不导入")
    args = parser.parse_args()

    data = build_demo_workbook()
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    out = STAGING_DIR / "demo_import_workbook.xlsx"
    out.write_bytes(data)
    print(f"工作簿已生成：{out}（{len(data)} 字节）")
    if args.skip_import:
        return 0

    try:
        report = importer.import_file(data, out.name, kind="full", db_path=args.db)
    except importer.ImportConflictError as exc:
        original = exc.original
        print(
            f"重复导入被指纹拒绝（符合预期）：文件已存在于批次 "
            f"{original['batch_id']}（status={original['status']}，"
            f"total={original['total_rows']}/ok={original['ok_rows']}）"
        )
        return 0
    print(
        f"导入结果：batch_id={report['batch_id']} status={report['status']} "
        f"kind={report['kind']} total={report['total_rows']} "
        f"ok={report['ok_rows']} failed={report['failed_rows']}"
    )
    if report["fatal"]:
        print(f"fatal: {report['fatal']}")
    if report["status"] == "failed" and any(e["code"] == "E_CONFLICT" for e in report["errors"]):
        # 程序重新生成的等价文件 sha 会变（zip 时间戳），指纹不命中由业务规则兜底：
        # 同名考试 E_CONFLICT + 答题 E_DUP，整批拒绝、库内数据不变——行为符合预期。
        print(
            "演示数据已在库中（同名考试触发业务规则防重，库内数据未变化）。\n"
            "如需从零重放：先清空 classes/students/exams/exam_questions/"
            "student_answers/import_batches 六表后重跑本脚本。"
        )
        return 0 if counts_stable(args.db) else 1
    for e in report["errors"][:10]:
        print(f"  error: {e}")
    for w in report["warnings"][:10]:
        print(f"  warning: {w}")

    return 0 if counts_stable(args.db) else 1


if __name__ == "__main__":
    sys.exit(main())
