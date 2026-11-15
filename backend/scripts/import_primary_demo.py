"""小学演示数据 F16 导入（Phase 3 / S0-3）。

通过现有 F16 导入服务（``importer.import_file``）导入已审核的合成工作簿；
不绕过导入服务直接写业务表。

前置：
- 工作簿由 ``build_primary_demo_workbook.py`` 生成且导入前检查 0 错误；
- 业务库中不存在同名班级/考试（防重复）。

输出：``data/staging/primary_demo_import_execution_report.md/.json``（含导入前后快照）。

用法（仓库根执行）：
    .venv/Scripts/python.exe -X utf8 -m backend.scripts.import_primary_demo
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.app.storage import importer

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "db" / "edumate.sqlite"
WORKBOOK = REPO_ROOT / "data" / "staging" / "primary_demo_synthetic_workbook.xlsx"
DESIGN_FILE = REPO_ROOT / "data" / "staging" / "primary_demo_design.json"
OUT_MD = REPO_ROOT / "data" / "staging" / "primary_demo_import_execution_report.md"
OUT_JSON = REPO_ROOT / "data" / "staging" / "primary_demo_import_execution_report.json"

CLASS_NAME = "六年级演示班"
EXAM_TITLES = ("六年级数学演示测评（一）", "六年级数学演示测评（二）")
SNAPSHOT_TABLES = (
    "knowledge_points", "kp_edges", "kp_fts", "questions", "q_kp",
    "q_kp_llm", "q_annotation_results", "import_batches", "tasks", "steps",
    "classes", "students", "exams", "exam_questions", "student_answers",
)


def snapshot(conn) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in SNAPSHOT_TABLES}


def main() -> int:
    if not WORKBOOK.exists():
        print(f"工作簿不存在：{WORKBOOK}")
        return 1
    data = WORKBOOK.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    design = json.loads(DESIGN_FILE.read_text(encoding="utf-8"))
    if design.get("status") != "design_draft_not_imported":
        print(f"设计状态异常：{design.get('status')}")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        pre = snapshot(conn)
        existing_class = conn.execute(
            "SELECT class_id FROM classes WHERE name=?", (CLASS_NAME,)).fetchone()
        existing_exams = [r["title"] for r in conn.execute(
            f"SELECT title FROM exams WHERE title IN ({','.join('?' * len(EXAM_TITLES))})",
            EXAM_TITLES)]
    finally:
        conn.close()
    if existing_class or existing_exams:
        print(f"已存在同名班级/考试，拒绝重复导入：class={existing_class} exams={existing_exams}")
        return 1

    try:
        report = importer.import_file(
            data, WORKBOOK.name, kind="full", db_path=str(DB_PATH))
    except Exception as exc:
        print(f"F16 导入异常：{type(exc).__name__}: {exc}")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        post = snapshot(conn)
    finally:
        conn.close()

    warnings = report.get("warnings", [])
    kp_warnings = [w for w in warnings if str(w.get("code", "")).startswith("W_KP")]
    ok = (report.get("status") == "committed"
          and report.get("failed_rows", 1) == 0
          and not kp_warnings)

    lines = [
        "# 小学演示数据 F16 导入执行报告",
        "",
        f"- 执行时间：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- 工作簿：`{WORKBOOK.name}`；sha256 `{sha}`",
        f"- 状态：**{'成功' if ok else '未通过'}**；合成演示数据（非真实学生）",
        f"- 批次：`{report.get('batch_id')}`；kind={report.get('kind')}；"
        f"status={report.get('status')}；total={report.get('total_rows')} / "
        f"ok={report.get('ok_rows')} / failed={report.get('failed_rows')}",
        "",
        "## 1. 导入前后快照",
        "",
        "| 表 | 导入前 | 导入后 | 变化 |",
        "|---|---|---|---|",
    ]
    for t in SNAPSHOT_TABLES:
        lines.append(f"| `{t}` | {pre[t]} | {post[t]} | {post[t] - pre[t]:+d} |")
    lines += ["", "## 2. 警告/错误", ""]
    if warnings:
        for w in warnings:
            lines.append(f"- ⚠️ {w}")
    else:
        lines.append("- 无警告。")
    if report.get("errors"):
        lines += ["", "错误："]
        for e in report["errors"]:
            lines.append(f"- ❌ {e}")
    lines += [
        "",
        "## 3. 边界",
        "",
        "- 通过现有 F16 `importer.import_file` 导入，未直接写业务表。",
        "- 合成演示数据：班级 school 字段、考试标题、工作簿文件名、报告均标注“演示/合成”，不得描述为真实学生数据。",
        "- 主/辅知识点映射清单见 `data/annotations/primary_demo_mapping.jsonl`；辅助 KP 不作为同等强度证据（Phase 3 实现时处理）。",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps({
        "executed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "synthetic_demo": True,
        "workbook": str(WORKBOOK),
        "sha256": sha,
        "ok": ok,
        "report": report,
        "pre_snapshot": pre,
        "post_snapshot": post,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    if ok:
        design["status"] = "imported"
        design["import_batch_id"] = report.get("batch_id")
        design["imported_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        DESIGN_FILE.write_text(json.dumps(design, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    print(f"导入结果：{report.get('status')} batch={report.get('batch_id')} "
          f"total={report.get('total_rows')} ok={report.get('ok_rows')} "
          f"failed={report.get('failed_rows')} warnings={len(warnings)}")
    print(f"报告：{OUT_MD}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
