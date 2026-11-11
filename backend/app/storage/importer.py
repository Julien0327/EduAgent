"""F16 Excel 导入服务（Phase 1 / Step 3）— §3.4 七步管线。

管线：解析 → 表头映射 → 格式校验 → 关联校验 → 重复检测 → 事务写入 → 批次报告。

验收语义（用户 2026-11-11 强调的五个重点）：
1. 跨表关联严格校验：①班级 → ②学生（班内学号）→ ③考试 → ④卷面题号，逐级定位
   并检查业务归属——答题行的 (班级, 学号) 必须能唯一定位一名学生；考试绑定班级时
   答题学生必须属于该班（多班联考 class_name 为空不限制）；q_no 必须是该考试卷面
   上的题；q_id 必须是题库中 processed 状态的题；同名班级跨年级时仅凭班名定位报歧义错。
2. 写入原子性：行级校验错误分流到报告且错误行不进入写入集合；合法行在单个
   BEGIN IMMEDIATE 事务写入；写入期不可恢复异常（如约束冲突）整批回滚，不留半批
   数据；批次状态更新走独立事务，业务数据回滚不影响批次记录。
3. 重复导入语义：指纹 = file_sha256（内容相同则 kind 必相同），命中已 committed/
   partial 批次默认拒绝并返回原批次报告；覆盖重导（force=true）仅限只含答题记录
   表的文件，同一事务内先删该考试原 answers 再写入，是唯一覆盖通道；批内重复行、
   库内 UNIQUE 预检双层判重，UNIQUE(exam_id, student_id, exam_q_id) 兜底。
4. 异常中断可恢复：批次状态机 pending → validating → committed/partial/failed；
   服务启动清扫滞留 validating 批次为 failed；存在 validating 批次时拒绝新导入。
5. 错误定位：校验错误带 sheet + 工作簿行号 + 字段，error 该行不写入、warning 写入
   并记录；关联断裂（E_REF 行占比 ≥ 50%）、无可写行、写入异常 → 整批 failed。

批次外致命错误（无法解析 / 无数据表 / 超行数上限 / force 语义不符）不创建批次，
由 API 层转 400/422。

错误码：E_REQUIRED/E_TYPE/E_ENUM/E_RANGE/E_DATE/E_REF/E_DUP/E_CONFLICT/
E_INTEGRITY；W_TOTAL_SCORE/W_KP/W_HEADER。
"""
from __future__ import annotations

import hashlib
import io
import json
import random
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from openpyxl import load_workbook

from backend.app.storage import db
from backend.app.storage.templates import (
    DATA_SHEET_ORDER,
    determine_kind,
    resolve_header,
)
from backend.config import settings

IMPORT_BATCH_MAX_ROWS = settings.IMPORT_BATCH_MAX_ROWS

# 关联断裂阈值：某表 E_REF 错误行占比达到该值视为“关联大面积断裂”，整批 failed
REF_BREAKDOWN_RATIO = 0.5

# kind 声明与文件实际包含数据表的匹配规则（§10.2 kind 参数）
KIND_SHEETS: dict[str, set[str] | None] = {
    "full": None,      # 整簿导入：≥2 个数据表
    "classes": {"班级"},
    "students": {"学生"},
    "exam": {"考试", "考试题目"},   # 可只含其一（先建考试后补题）
    "answers": {"答题记录"},
}

VALID_STAGES = ("primary", "junior", "senior")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TRUE_TOKENS = {"对", "正确", "1", "true", "t", "y", "yes", "√", "✓"}
FALSE_TOKENS = {"错", "错误", "0", "false", "f", "n", "no", "×", "x"}

E_REQUIRED, E_TYPE, E_ENUM, E_RANGE, E_DATE = "E_REQUIRED", "E_TYPE", "E_ENUM", "E_RANGE", "E_DATE"
E_REF, E_DUP, E_CONFLICT, E_INTEGRITY = "E_REF", "E_DUP", "E_CONFLICT", "E_INTEGRITY"
W_TOTAL_SCORE, W_KP, W_HEADER = "W_TOTAL_SCORE", "W_KP", "W_HEADER"


@dataclass
class Issue:
    """校验问题：error = 该行不写入；warning = 写入并记录。"""

    sheet: str
    row: int | None
    field: str | None
    level: str
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ImportFatalError(Exception):
    """批次外致命错误（文件不可导入），不创建批次记录。"""


class ImportConflictError(Exception):
    """指纹重复（默认拒绝），携带原批次报告。"""

    def __init__(self, original: dict):
        super().__init__(f"重复文件：批次 {original.get('batch_id')} 已导入")
        self.original = original


class ImportBusyError(Exception):
    """已有 validating 批次在处理中，拒绝并发导入；携带被拒批次的报告。"""

    def __init__(self, report: dict):
        super().__init__("已有导入批次正在处理，请稍后重试")
        self.report = report


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _cell(v: Any) -> Any:
    if isinstance(v, str):
        return v.strip()
    return v


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


# ---------------------------------------------------------------- 解析与映射

def parse_workbook(data: bytes) -> dict[str, list[dict]]:
    """步骤①②：openpyxl 流式解析 + 表头别名归一。

    返回 {sheet 名: [{"row": 工作簿行号, "data": {字段: 原始值}}, ...]}。
    表头完全无法识别的数据表忽略；未知工作表与“填写说明”忽略；全空行跳过。
    """
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ImportFatalError(f"Excel 解析失败：{exc}") from exc
    parsed: dict[str, list[dict]] = {}
    try:
        for sheet_name in DATA_SHEET_ORDER:
            if sheet_name not in wb.sheetnames:
                continue
            ws = wb[sheet_name]
            header_map: dict[int, str] | None = None
            unmapped_cols: dict[int, str] = {}
            rows: list[dict] = []
            for row_idx, values in enumerate(ws.iter_rows(values_only=True), start=1):
                cells = [_cell(v) for v in values]
                if header_map is None:
                    mapped: dict[int, str] = {}
                    unmapped: dict[int, str] = {}
                    for col_idx, raw in enumerate(cells):
                        field_name = resolve_header(sheet_name, raw)
                        if field_name:
                            mapped[col_idx] = field_name
                        elif not _is_blank(raw):
                            unmapped[col_idx] = str(raw)
                    header_map = mapped
                    unmapped_cols = unmapped
                    continue  # 表头行（第 1 行）不计入数据行
                if all(_is_blank(v) for v in cells):
                    continue
                record = {f: cells[i] for i, f in header_map.items() if i < len(cells)}
                # 未识别列警告只随首个数据行记录一次，避免每行重复
                rows.append({
                    "sheet": sheet_name,
                    "row": row_idx,
                    "data": record,
                    "unmapped": unmapped_cols if not rows else {},
                })
            if header_map is not None:
                parsed[sheet_name] = rows
    finally:
        wb.close()
    return parsed


# ---------------------------------------------------------------- 格式校验

def _err(rec: dict, field: str | None, code: str, msg: str) -> Issue:
    return Issue(rec["sheet"], rec["row"], field, "error", code, msg)


def _warn(rec: dict, field: str | None, code: str, msg: str) -> Issue:
    return Issue(rec["sheet"], rec["row"], field, "warning", code, msg)


def _stage_from_grade(grade: int) -> str:
    if grade <= 6:
        return "primary"
    if grade <= 9:
        return "junior"
    return "senior"


def _fmt_issues(sheet: str, records: list[dict]) -> tuple[list[dict], list[Issue]]:
    """步骤③：格式校验（必填/类型/枚举/范围/日期）。返回（合法行, 问题列表）。"""
    issues: list[Issue] = []
    valid: list[dict] = []

    def clean_class(rec: dict) -> None:
        d = rec["data"]
        if _is_blank(d.get("name")):
            issues.append(_err(rec, "name", E_REQUIRED, "班级名称缺失"))
            return
        if _is_blank(d.get("grade")):
            issues.append(_err(rec, "grade", E_REQUIRED, "年级缺失"))
            return
        try:
            grade_f = float(d["grade"])
        except (TypeError, ValueError):
            issues.append(_err(rec, "grade", E_TYPE, f"年级不是数字：{d['grade']!r}"))
            return
        if not grade_f.is_integer():
            issues.append(_err(rec, "grade", E_TYPE, f"年级必须为整数：{d['grade']!r}"))
            return
        grade = int(grade_f)
        if not 1 <= grade <= 12:
            issues.append(_err(rec, "grade", E_RANGE, f"年级超出 1-12：{grade}"))
            return
        stage = d.get("stage")
        if _is_blank(stage):
            stage = _stage_from_grade(grade)
        elif stage not in VALID_STAGES:
            issues.append(_err(rec, "stage", E_ENUM, f"学段非法：{stage!r}（可选 {'/'.join(VALID_STAGES)}）"))
            return
        valid.append({"sheet": sheet, "row": rec["row"], "name": str(d["name"]),
                      "grade": grade, "stage": stage,
                      "school": None if _is_blank(d.get("school")) else str(d["school"])})

    def clean_student(rec: dict) -> None:
        d = rec["data"]
        missing = [f for f in ("class_name", "student_no", "name") if _is_blank(d.get(f))]
        if missing:
            issues.append(_err(rec, missing[0], E_REQUIRED, f"学生行必填字段缺失：{'、'.join(missing)}"))
            return
        valid.append({"sheet": sheet, "row": rec["row"], "class_name": str(d["class_name"]),
                      "student_no": str(d["student_no"]), "name": str(d["name"])})

    def clean_exam(rec: dict) -> None:
        d = rec["data"]
        if _is_blank(d.get("title")):
            issues.append(_err(rec, "title", E_REQUIRED, "考试名称缺失"))
            return
        exam_date = None
        if not _is_blank(d.get("exam_date")):
            raw = d["exam_date"]
            if isinstance(raw, datetime):
                exam_date = raw.date().isoformat()
            elif hasattr(raw, "isoformat") and not isinstance(raw, str):
                exam_date = raw.isoformat()[:10]
            elif isinstance(raw, str) and DATE_RE.match(raw):
                exam_date = raw
            else:
                issues.append(_err(rec, "exam_date", E_DATE, f"日期格式应为 YYYY-MM-DD：{raw!r}"))
                return
        total_score = None
        if not _is_blank(d.get("total_score")):
            try:
                total_score = float(d["total_score"])
            except (TypeError, ValueError):
                issues.append(_err(rec, "total_score", E_TYPE, f"总分不是数字：{d['total_score']!r}"))
                return
            if total_score <= 0:
                issues.append(_err(rec, "total_score", E_RANGE, f"总分必须 > 0：{total_score}"))
                return
        stage = d.get("stage")
        if not _is_blank(stage) and stage not in VALID_STAGES:
            issues.append(_err(rec, "stage", E_ENUM, f"学段非法：{stage!r}"))
            return
        valid.append({"sheet": sheet, "row": rec["row"], "title": str(d["title"]),
                      "class_name": None if _is_blank(d.get("class_name")) else str(d["class_name"]),
                      "exam_date": exam_date,
                      "subject": "math" if _is_blank(d.get("subject")) else str(d["subject"]),
                      "stage": None if _is_blank(stage) else str(stage),
                      "total_score": total_score})

    def clean_question(rec: dict) -> None:
        d = rec["data"]
        if _is_blank(d.get("exam_title")):
            issues.append(_err(rec, "exam_title", E_REQUIRED, "考试名称缺失"))
            return
        if _is_blank(d.get("q_no")):
            issues.append(_err(rec, "q_no", E_REQUIRED, "卷面题号缺失"))
            return
        try:
            q_no_f = float(d["q_no"])
        except (TypeError, ValueError):
            issues.append(_err(rec, "q_no", E_TYPE, f"题号不是数字：{d['q_no']!r}"))
            return
        if not q_no_f.is_integer() or q_no_f <= 0:
            issues.append(_err(rec, "q_no", E_RANGE, f"题号必须为正整数：{d['q_no']!r}"))
            return
        if _is_blank(d.get("score")):
            issues.append(_err(rec, "score", E_REQUIRED, "分值缺失"))
            return
        try:
            score = float(d["score"])
        except (TypeError, ValueError):
            issues.append(_err(rec, "score", E_TYPE, f"分值不是数字：{d['score']!r}"))
            return
        if score <= 0:
            issues.append(_err(rec, "score", E_RANGE, f"分值必须 > 0：{score}"))
            return
        q_id = None if _is_blank(d.get("q_id")) else str(d["q_id"])
        stem = None if _is_blank(d.get("stem")) else str(d["stem"])
        answer = None if _is_blank(d.get("answer")) else str(d["answer"])
        if q_id is None and (stem is None or answer is None):
            issues.append(_err(rec, "stem", E_REQUIRED, "q_id 为空时题干与答案必填（外部题快照）"))
            return
        valid.append({"sheet": sheet, "row": rec["row"], "exam_title": str(d["exam_title"]),
                      "q_no": int(q_no_f), "q_id": q_id,
                      "kp_names": None if _is_blank(d.get("kp_names")) else str(d["kp_names"]),
                      "score": score, "stem": stem, "answer": answer})

    def clean_answer(rec: dict) -> None:
        d = rec["data"]
        missing = [f for f in ("exam_title", "class_name", "student_no", "q_no", "score")
                   if _is_blank(d.get(f))]
        if missing:
            issues.append(_err(rec, missing[0], E_REQUIRED, f"答题行必填字段缺失：{'、'.join(missing)}"))
            return
        try:
            q_no_f = float(d["q_no"])
        except (TypeError, ValueError):
            issues.append(_err(rec, "q_no", E_TYPE, f"题号不是数字：{d['q_no']!r}"))
            return
        if not q_no_f.is_integer() or q_no_f <= 0:
            issues.append(_err(rec, "q_no", E_RANGE, f"题号必须为正整数：{d['q_no']!r}"))
            return
        try:
            score = float(d["score"])
        except (TypeError, ValueError):
            issues.append(_err(rec, "score", E_TYPE, f"得分不是数字：{d['score']!r}"))
            return
        if score < 0:
            issues.append(_err(rec, "score", E_RANGE, f"得分不能为负：{score}"))
            return
        is_correct = None
        if not _is_blank(d.get("is_correct")):
            token = str(d["is_correct"]).strip().lower()
            if token in TRUE_TOKENS:
                is_correct = 1
            elif token in FALSE_TOKENS:
                is_correct = 0
            else:
                issues.append(_err(rec, "is_correct", E_ENUM, f"对错取值非法：{d['is_correct']!r}（对/错/0/1）"))
                return
        valid.append({"sheet": sheet, "row": rec["row"], "exam_title": str(d["exam_title"]),
                      "class_name": str(d["class_name"]), "student_no": str(d["student_no"]),
                      "q_no": int(q_no_f), "score": score, "is_correct": is_correct})

    cleaners = {"班级": clean_class, "学生": clean_student, "考试": clean_exam,
                "考试题目": clean_question, "答题记录": clean_answer}
    for rec in records:
        cleaners[sheet](rec)
        for col in rec.get("unmapped", {}):
            issues.append(_warn(rec, None, W_HEADER, f"未识别的列被忽略：{rec['unmapped'][col]}"))
    return valid, issues


# ---------------------------------------------------------------- 关联与重复校验

class _Context:
    """校验上下文：批内合法行 + 库内既有数据的合并视图（批内优先）。"""

    def __init__(self, conn: sqlite3.Connection, valid_by_sheet: dict[str, list[dict]]):
        self.conn = conn
        self.valid = valid_by_sheet
        self.batch_classes: dict[str, list[dict]] = {}
        for r in valid_by_sheet.get("班级", []):
            self.batch_classes.setdefault(r["name"], []).append(r)
        self.db_classes: dict[str, list[dict]] = {}
        for row in conn.execute("SELECT class_id, name, grade, stage FROM classes"):
            self.db_classes.setdefault(row["name"], []).append(dict(row))
        self.db_students: dict[tuple[str, str], str] = {}
        for row in conn.execute("SELECT student_id, class_id, student_no FROM students"):
            self.db_students[(row["class_id"], row["student_no"])] = row["student_id"]
        self.db_exams: dict[str, list[dict]] = {}
        for row in conn.execute(
            "SELECT e.exam_id, e.title, e.class_id, c.name AS class_name FROM exams e"
            " LEFT JOIN classes c ON e.class_id = c.class_id"
        ):
            self.db_exams.setdefault(row["title"], []).append(dict(row))
        # (exam_id, q_no) -> {"id": exam_questions.id, "score": 卷面分值}
        self.db_exam_q: dict[tuple[str, int], dict] = {}
        for row in conn.execute("SELECT id, exam_id, q_no, score FROM exam_questions"):
            self.db_exam_q[(row["exam_id"], row["q_no"])] = {"id": row["id"], "score": row["score"]}

    def resolve_class(self, name: str) -> list[dict]:
        """班级定位候选（批内 + 库内）。"""
        out = [{"batch": True, "grade": r["grade"], "stage": r["stage"]}
               for r in self.batch_classes.get(name, [])]
        out.extend(self.db_classes.get(name, []))
        return out

    def resolve_exam(self, title: str) -> list[dict]:
        out = [{"batch": True, "class_name": r["class_name"], "stage": r["stage"]}
               for r in self.valid.get("考试", []) if r["title"] == title]
        out.extend(self.db_exams.get(title, []))
        return out

    def find_student(self, class_name: str, student_no: str) -> dict | None:
        for s in self.valid.get("学生", []):
            if s["class_name"] == class_name and s["student_no"] == student_no:
                return {"batch": True}
        for c in self.db_classes.get(class_name, []):
            student_id = self.db_students.get((c["class_id"], student_no))
            if student_id is not None:
                return {"batch": False, "student_id": student_id, "class_id": c["class_id"]}
        return None


def _class_ambiguous(cands: list[dict]) -> bool:
    """同名班级候选跨越多个 (年级, 学段) → 仅凭班名无法唯一定位。"""
    keys = {(c.get("grade"), c.get("stage")) for c in cands}
    return len(keys) > 1


def _ref_issues(ctx: _Context, issues: list[Issue], force_answers: bool) -> None:
    """步骤④⑤：四级关联校验 + 业务归属校验 + 批内/库内重复检测。"""
    conn = ctx.conn
    valid = ctx.valid

    def add_err(r: dict, field: str | None, code: str, msg: str) -> None:
        issues.append(Issue(r["sheet"], r["row"], field, "error", code, msg))

    # 班级表：批内 (name, grade, stage) 判重
    seen_cls: dict[tuple[str, int, str], int] = {}
    for r in valid.get("班级", []):
        key = (r["name"], r["grade"], r["stage"])
        if key in seen_cls:
            add_err(r, "name", E_DUP, f"班级行重复：{r['name']}（{r['grade']}年级/{r['stage']}，首见第 {seen_cls[key]} 行）")
        else:
            seen_cls[key] = r["row"]

    # 学生表：class_name 必须可唯一定位；批内 (class, no) 判重
    seen_stu: dict[tuple[str, str], int] = {}
    for r in valid.get("学生", []):
        cands = ctx.resolve_class(r["class_name"])
        if not cands:
            add_err(r, "class_name", E_REF, f"班级定位失败：{r['class_name']}（本批班级表与数据库中均不存在）")
        elif _class_ambiguous(cands):
            add_err(r, "class_name", E_REF, f"班级名称 {r['class_name']} 对应多个不同年级/学段的班级，无法唯一定位")
        key = (r["class_name"], r["student_no"])
        if key in seen_stu:
            add_err(r, "student_no", E_DUP, f"学号在班级 {r['class_name']} 内重复：{r['student_no']}（首见第 {seen_stu[key]} 行）")
        else:
            seen_stu[key] = r["row"]

    # 考试表：批内重名；绑定班级可唯一定位；库内同名考试拒绝（exams 不支持覆盖）
    seen_titles: dict[str, int] = {}
    for r in valid.get("考试", []):
        if r["title"] in seen_titles:
            add_err(r, "title", E_DUP, f"考试名称批内重复：《{r['title']}》（首见第 {seen_titles[r['title']]} 行）")
        else:
            seen_titles[r["title"]] = r["row"]
        if r["class_name"] is not None:
            cands = ctx.resolve_class(r["class_name"])
            if not cands:
                add_err(r, "class_name", E_REF, f"考试绑定班级定位失败：{r['class_name']}")
            elif _class_ambiguous(cands):
                add_err(r, "class_name", E_REF, f"考试绑定班级 {r['class_name']} 对应多个不同年级/学段的班级，无法唯一定位")
        hit = conn.execute("SELECT exam_id FROM exams WHERE title = ? LIMIT 1", (r["title"],)).fetchone()
        if hit is not None:
            add_err(r, "title", E_CONFLICT,
                    f"库内已存在同名考试《{r['title']}》（{hit['exam_id']}）；考试与卷面题目不支持覆盖重导，"
                    f"如需重导答题请只上传答题记录表")

    # 考试题目表：q_id 必须存在于题库（processed）；批内 (exam, q_no) 判重
    seen_q: dict[tuple[str, int], int] = {}
    for r in valid.get("考试题目", []):
        if r["q_id"] is not None:
            hit = conn.execute(
                "SELECT 1 FROM questions WHERE q_id = ? AND quality_state = 'processed'",
                (r["q_id"],),
            ).fetchone()
            if hit is None:
                add_err(r, "q_id", E_REF, f"题库中不存在可用题目 q_id={r['q_id']}（须为 processed 状态）")
        key = (r["exam_title"], r["q_no"])
        if key in seen_q:
            add_err(r, "q_no", E_DUP, f"考试《{r['exam_title']}》卷面题号 {r['q_no']} 批内重复（首见第 {seen_q[key]} 行）")
        else:
            seen_q[key] = r["row"]

    # 答题记录：四级链 + 业务归属 + 批内/库内判重
    seen_ans: dict[tuple[str, str, str, int], int] = {}
    err_rows: set[tuple[str, int]] = {(i.sheet, i.row) for i in issues if i.level == "error" and i.row is not None}
    for r in valid.get("答题记录", []):
        # ①班级唯一归属
        cands = ctx.resolve_class(r["class_name"])
        if not cands:
            add_err(r, "class_name", E_REF, f"班级定位失败：{r['class_name']}")
        elif _class_ambiguous(cands):
            add_err(r, "class_name", E_REF, f"班级名称 {r['class_name']} 对应多个不同年级/学段的班级，无法唯一定位")
        # ②学生归属：该班该学号必须存在
        if ctx.find_student(r["class_name"], r["student_no"]) is None:
            add_err(r, "student_no", E_REF,
                    f"学生定位失败：{r['class_name']} 的学号 {r['student_no']} 不存在（或未随本批导入）")
        # ③考试定位 + 业务归属（考试绑定班级时学生班级必须一致，联考不限制）
        exam_cands = ctx.resolve_exam(r["exam_title"])
        q_score: float | None = None
        if not exam_cands:
            add_err(r, "exam_title", E_REF, f"考试定位失败：《{r['exam_title']}》（本批考试表与数据库中均不存在）")
        else:
            exam = exam_cands[0]
            bound = exam.get("class_name")
            if bound is not None and r["class_name"] != bound:
                add_err(r, "class_name", E_REF,
                        f"业务归属不符：考试《{r['exam_title']}》绑定班级 {bound}，答题学生属于 {r['class_name']}")
            # ④卷面题号必须在试卷上（批内题目行或库内 exam_questions）
            q_in_batch = any(q["exam_title"] == r["exam_title"] and q["q_no"] == r["q_no"]
                             for q in valid.get("考试题目", []))
            if q_in_batch:
                for q in valid.get("考试题目", []):
                    if q["exam_title"] == r["exam_title"] and q["q_no"] == r["q_no"]:
                        q_score = q["score"]
                        break
            else:
                db_q = None
                for e in exam_cands:
                    if not e.get("batch") and e.get("exam_id"):
                        db_q = ctx.db_exam_q.get((e["exam_id"], r["q_no"]))
                        if db_q is not None:
                            break
                if db_q is None:
                    add_err(r, "q_no", E_REF,
                            f"卷面题号定位失败：考试《{r['exam_title']}》没有第 {r['q_no']} 题（须先导入该考试的题目行）")
                else:
                    q_score = db_q["score"]
        if q_score is not None and r["score"] > q_score:
            add_err(r, "score", E_RANGE,
                    f"得分 {r['score']} 超过卷面分值 {q_score}（考试《{r['exam_title']}》第 {r['q_no']} 题）")
        # 批内三元组判重
        key = (r["exam_title"], r["class_name"], r["student_no"], r["q_no"])
        if key in seen_ans:
            add_err(r, "q_no", E_DUP,
                    f"答题记录批内重复：{r['class_name']}/{r['student_no']} 第 {r['q_no']} 题（首见第 {seen_ans[key]} 行）")
        else:
            seen_ans[key] = r["row"]
        # 库内 UNIQUE 预检（force 覆盖场景跳过：写入时先删原记录）；已报错的行不再重复报
        if not force_answers and (r["sheet"], r["row"]) not in err_rows:
            hit = conn.execute(
                "SELECT 1 FROM student_answers a"
                " JOIN exams e ON a.exam_id = e.exam_id"
                " JOIN students s ON a.student_id = s.student_id"
                " JOIN classes c ON s.class_id = c.class_id"
                " JOIN exam_questions q ON a.exam_q_id = q.id"
                " WHERE e.title = ? AND c.name = ? AND s.student_no = ? AND q.q_no = ? LIMIT 1",
                (r["exam_title"], r["class_name"], r["student_no"], r["q_no"]),
            ).fetchone()
            if hit is not None:
                add_err(r, None, E_DUP,
                        f"该答题记录已存在（考试《{r['exam_title']}》{r['class_name']}/{r['student_no']}"
                        f" 第 {r['q_no']} 题）；如需覆盖请使用 force 重导")

    # 完整性检查仅在文件包含考试题目表时有意义（kind=exam 单传考试表 = 先建考试后补题）
    q_count: dict[str, int] = {}
    q_sum: dict[str, float] = {}
    for q in valid.get("考试题目", []):
        q_count[q["exam_title"]] = q_count.get(q["exam_title"], 0) + 1
        q_sum[q["exam_title"]] = q_sum.get(q["exam_title"], 0.0) + q["score"]
    if valid.get("考试题目"):
        for r in valid.get("考试", []):
            if q_count.get(r["title"], 0) == 0:
                add_err(r, "title", E_INTEGRITY, f"考试《{r['title']}》没有对应的考试题目行（每场考试至少 1 题）")
            elif r["total_score"] is not None and abs(r["total_score"] - q_sum[r["title"]]) > 0.01:
                issues.append(Issue(r["sheet"], r["row"], "total_score", "warning", W_TOTAL_SCORE,
                                    f"总分 {r['total_score']} 与卷面分值合计 {round(q_sum[r['title']], 2)} 不一致"))


def _kp_issues(conn: sqlite3.Connection, valid_by_sheet: dict[str, list[dict]],
               issues: list[Issue]) -> None:
    """知识点名称匹配（§3.4：匹配不上仅警告，匹配上的写入 kp_ids）。"""
    kp_by_name: dict[str, str] = {}
    for row in conn.execute("SELECT kp_id, name FROM knowledge_points"):
        kp_by_name.setdefault(row["name"], row["kp_id"])
    for q in valid_by_sheet.get("考试题目", []):
        if q["kp_names"] is None:
            q["kp_ids"] = []
            continue
        ids: list[str] = []
        miss: list[str] = []
        for token in re.split(r"[、,，;；]", q["kp_names"]):
            token = token.strip()
            if not token:
                continue
            kp_id = kp_by_name.get(token)
            if kp_id:
                ids.append(kp_id)
            else:
                miss.append(token)
        q["kp_ids"] = ids
        if miss:
            issues.append(Issue(q["sheet"], q["row"], "kp_names", "warning", W_KP,
                                f"知识点未匹配到图谱（已忽略）：{'、'.join(miss)}"))


# ---------------------------------------------------------------- 事务写入

def _write_rows(conn: sqlite3.Connection, valid_by_sheet: dict[str, list[dict]],
                batch_id: str, force_answers: bool) -> tuple[int, list[Issue]]:
    """步骤⑥：在调用方事务内写入全部合法行。

    行级定位失败（校验漏网）→ 跳过该行并返回 error 记录，不拖垮整批；
    意外异常（约束冲突等）向上抛出，由调用方整批回滚。
    返回（成功写入行数, 写入期问题列表）。
    """
    written = 0
    w_issues: list[Issue] = []

    def skip(rec: dict, field: str, msg: str) -> None:
        w_issues.append(Issue(rec["sheet"], rec["row"], field, "error", E_REF, msg))

    def class_id_of(name: str) -> str | None:
        row = conn.execute(
            "SELECT class_id FROM classes WHERE name = ? ORDER BY class_id LIMIT 1", (name,)
        ).fetchone()
        return row["class_id"] if row else None

    # 班级：UNIQUE(name, grade, stage) — 库内已有则复用，否则确定性 ID 插入（幂等重跑）
    for r in valid_by_sheet.get("班级", []):
        key = (r["name"], r["grade"], r["stage"])
        hit = conn.execute(
            "SELECT class_id FROM classes WHERE name = ? AND grade = ? AND stage = ?", key
        ).fetchone()
        if hit:
            continue
        class_id = "cls_" + hashlib.sha1("|".join(map(str, key)).encode("utf-8")).hexdigest()[:10]
        conn.execute(
            "INSERT INTO classes (class_id, name, grade, stage, school, import_batch_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (class_id, r["name"], r["grade"], r["stage"], r["school"], batch_id, _now()),
        )
        written += 1

    # 学生：UNIQUE(class_id, student_no) — 库内已有则复用
    for r in valid_by_sheet.get("学生", []):
        class_id = class_id_of(r["class_name"])
        if class_id is None:
            skip(r, "class_name", f"写入期班级定位失败：{r['class_name']}")
            continue
        hit = conn.execute(
            "SELECT student_id FROM students WHERE class_id = ? AND student_no = ?",
            (class_id, r["student_no"]),
        ).fetchone()
        if hit:
            continue
        student_id = "stu_" + hashlib.sha1(f"{class_id}|{r['student_no']}".encode("utf-8")).hexdigest()[:10]
        conn.execute(
            "INSERT INTO students (student_id, class_id, student_no, name, import_batch_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (student_id, class_id, r["student_no"], r["name"], batch_id, _now()),
        )
        written += 1

    # 考试：批内 title 唯一（校验期已保证且库内同名已拒）；stage 推断 显式 > 绑定班级 > junior
    exam_ids: dict[str, str] = {}
    for r in valid_by_sheet.get("考试", []):
        hit = conn.execute("SELECT exam_id FROM exams WHERE title = ? LIMIT 1", (r["title"],)).fetchone()
        if hit is not None:
            skip(r, "title", f"写入期发现库内同名考试《{r['title']}》，本行跳过")
            continue
        exam_id = "exam_" + hashlib.sha1(
            f"{r['title']}|{r['class_name']}|{r['exam_date']}".encode("utf-8")
        ).hexdigest()[:10]
        class_id = None
        if r["class_name"] is not None:
            class_id = class_id_of(r["class_name"])
            if class_id is None:
                skip(r, "class_name", f"写入期班级定位失败：{r['class_name']}（考试《{r['title']}》）")
                continue
        stage = r["stage"]
        if stage is None and class_id is not None:
            row = conn.execute("SELECT stage FROM classes WHERE class_id = ?", (class_id,)).fetchone()
            stage = row["stage"] if row else None
        if stage is None:
            stage = "junior"
        conn.execute(
            "INSERT INTO exams (exam_id, title, class_id, exam_date, subject, stage, total_score,"
            " import_batch_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (exam_id, r["title"], class_id, r["exam_date"], r["subject"], stage,
             r["total_score"], batch_id, _now()),
        )
        exam_ids[r["title"]] = exam_id
        written += 1

    def exam_id_of(title: str) -> str | None:
        if title in exam_ids:
            return exam_ids[title]
        row = conn.execute(
            "SELECT exam_id FROM exams WHERE title = ? ORDER BY exam_id LIMIT 1", (title,)
        ).fetchone()
        return row["exam_id"] if row else None

    # 考试题目：UNIQUE(exam_id, q_no) — 库内已有则复用（幂等）
    for r in valid_by_sheet.get("考试题目", []):
        exam_id = exam_id_of(r["exam_title"])
        if exam_id is None:
            skip(r, "exam_title", f"写入期考试定位失败：《{r['exam_title']}》")
            continue
        hit = conn.execute(
            "SELECT id FROM exam_questions WHERE exam_id = ? AND q_no = ?", (exam_id, r["q_no"])
        ).fetchone()
        if hit:
            continue
        conn.execute(
            "INSERT INTO exam_questions (exam_id, q_no, q_id, kp_ids, score, stem, answer)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (exam_id, r["q_no"], r["q_id"],
             json.dumps(r.get("kp_ids", []), ensure_ascii=False),
             r["score"], r["stem"], r["answer"]),
        )
        written += 1

    # 答题记录：force 时同事务先删文件涉及考试的原 answers（唯一覆盖通道），再写入
    if force_answers:
        for title in {r["exam_title"] for r in valid_by_sheet.get("答题记录", [])}:
            exam_id = exam_id_of(title)
            if exam_id is not None:
                conn.execute("DELETE FROM student_answers WHERE exam_id = ?", (exam_id,))
    for r in valid_by_sheet.get("答题记录", []):
        exam_id = exam_id_of(r["exam_title"])
        if exam_id is None:
            skip(r, "exam_title", f"写入期考试定位失败：《{r['exam_title']}》")
            continue
        stu = conn.execute(
            "SELECT s.student_id FROM students s JOIN classes c ON s.class_id = c.class_id"
            " WHERE c.name = ? AND s.student_no = ? LIMIT 1",
            (r["class_name"], r["student_no"]),
        ).fetchone()
        if stu is None:
            skip(r, "student_no", f"写入期学生定位失败：{r['class_name']}/{r['student_no']}")
            continue
        eq = conn.execute(
            "SELECT id FROM exam_questions WHERE exam_id = ? AND q_no = ?", (exam_id, r["q_no"])
        ).fetchone()
        if eq is None:
            skip(r, "q_no", f"写入期卷面题定位失败：《{r['exam_title']}》第 {r['q_no']} 题")
            continue
        conn.execute(
            "INSERT INTO student_answers (exam_id, student_id, exam_q_id, score, is_correct)"
            " VALUES (?, ?, ?, ?, ?)",
            (exam_id, stu["student_id"], eq["id"], r["score"], r["is_correct"]),
        )
        written += 1
    return written, w_issues


# ---------------------------------------------------------------- 批次状态管理

def _insert_batch(conn_path, batch_id: str, kind: str, file_name: str, sha: str) -> None:
    conn = db.connect(conn_path)
    try:
        conn.execute(
            "INSERT INTO import_batches (batch_id, kind, file_name, file_sha256, status,"
            " total_rows, ok_rows, failed_rows, error_report, created_at)"
            " VALUES (?, ?, ?, ?, 'pending', 0, 0, 0, NULL, ?)",
            (batch_id, kind, file_name, sha, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def _update_batch(conn_path, batch_id: str, **fields) -> None:
    """批次状态更新走独立事务（业务数据回滚不影响批次记录）。"""
    conn = db.connect(conn_path)
    try:
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE import_batches SET {cols} WHERE batch_id = ?",
                     (*fields.values(), batch_id))
        conn.commit()
    finally:
        conn.close()


def get_batch(conn_path, batch_id: str) -> dict | None:
    conn = db.connect(conn_path)
    try:
        row = conn.execute("SELECT * FROM import_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        if row is None:
            return None
        report = dict(row)
        # 展平 error_report 使 GET 返回与导入返回结构一致（fatal/errors/warnings 顶层）
        embedded = (
            json.loads(report["error_report"]) if report["error_report"]
            else {"fatal": None, "errors": [], "warnings": []}
        )
        report.pop("error_report")
        report.update(embedded)
        return report
    finally:
        conn.close()


def has_active_validating(conn_path) -> bool:
    conn = db.connect(conn_path)
    try:
        return conn.execute(
            "SELECT 1 FROM import_batches WHERE status = 'validating' LIMIT 1"
        ).fetchone() is not None
    finally:
        conn.close()


def sweep_stale_validating(conn_path) -> int:
    """服务启动清扫：滞留 validating 的批次（进程中断残留）→ failed。"""
    conn = db.connect(conn_path)
    try:
        cur = conn.execute(
            "UPDATE import_batches SET status = 'failed', error_report = ? WHERE status = 'validating'",
            (json.dumps({"fatal": "服务中断清扫：批次滞留 validating，按失败处理",
                         "errors": [], "warnings": []}, ensure_ascii=False),),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------- 主入口

def _check_kind(declared: str, present: set[str]) -> None:
    """kind 声明与文件实际包含数据表的一致性校验（§10.2，批次外 fatal）。"""
    if declared not in KIND_SHEETS:
        raise ImportFatalError(
            f"kind 非法：{declared!r}（可选 {'/'.join(KIND_SHEETS)}）"
        )
    required = KIND_SHEETS[declared]
    if required is None:
        if len(present) < 2:
            raise ImportFatalError(
                f"kind=full 要求整簿导入（≥2 个数据表），文件实际包含：{'、'.join(sorted(present)) or '无'}"
            )
    elif not present or not present <= required:
        raise ImportFatalError(
            f"kind={declared} 只允许包含 {'、'.join(sorted(required))}，"
            f"文件实际包含：{'、'.join(sorted(present))}"
        )


def import_file(data: bytes, file_name: str, kind: str = "full", force: bool = False,
                db_path=None) -> dict:
    """F16 导入主入口：七步管线，返回批次报告 dict。

    kind: full/classes/students/exam/answers（须与文件实际包含的数据表一致）。
    ImportConflictError（指纹重复）/ ImportBusyError（并发冲突）由 API 层转 409；
    ImportFatalError（文件不可导入/kind 不符，不建批次）由 API 层转 400/422。
    """
    sha = hashlib.sha256(data).hexdigest()

    # 指纹判重：同 sha 已有 committed/partial 批次 → 默认拒绝（force 豁免，见下）
    conn = db.connect(db_path)
    try:
        dup = conn.execute(
            "SELECT batch_id FROM import_batches"
            " WHERE file_sha256 = ? AND status IN ('committed','partial') LIMIT 1",
            (sha,),
        ).fetchone()
    finally:
        conn.close()
    if dup is not None and not force:
        raise ImportConflictError(get_batch(db_path, dup["batch_id"]))

    # 步骤①②：解析 + 表头映射（批次外 fatal 不建批次）
    parsed = parse_workbook(data)
    present = set(parsed)
    if not present:
        raise ImportFatalError(
            "文件中没有可识别的数据工作表（表头需为：" + "、".join(DATA_SHEET_ORDER) + "）"
        )
    _check_kind(kind, present)
    kind = determine_kind(present)  # 批次记录实际内容 kind（指纹语义基于内容）
    total_rows = sum(len(rows) for rows in parsed.values())
    if total_rows > IMPORT_BATCH_MAX_ROWS:
        raise ImportFatalError(f"单批数据行数 {total_rows} 超过上限 {IMPORT_BATCH_MAX_ROWS}")
    if force and kind != "answers":
        raise ImportFatalError("覆盖重导（force）仅支持只含答题记录表的数据文件")

    batch_id = ("imp_" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
                + "_" + random.randbytes(3).hex())

    # 并发互斥：存在 validating 批次 → 拒绝（本次导入记为 failed 批次留痕）
    if has_active_validating(db_path):
        _insert_batch(db_path, batch_id, kind, file_name, sha)
        report = {
            "batch_id": batch_id, "kind": kind, "file_name": file_name, "file_sha256": sha,
            "status": "failed", "total_rows": 0, "ok_rows": 0, "failed_rows": 0,
            "fatal": "已有导入批次正在处理（validating），本批次拒绝",
            "errors": [], "warnings": [],
        }
        _update_batch(db_path, batch_id, status="failed",
                      error_report=json.dumps({"fatal": report["fatal"], "errors": [],
                                               "warnings": []}, ensure_ascii=False))
        raise ImportBusyError(report)

    _insert_batch(db_path, batch_id, kind, file_name, sha)
    _update_batch(db_path, batch_id, status="validating")

    fatal: str | None = None
    errors: list[Issue] = []
    warnings: list[Issue] = []
    ok_rows = 0

    try:
        # 步骤③：格式校验
        valid_by_sheet: dict[str, list[dict]] = {}
        for sheet, records in parsed.items():
            valid, iss = _fmt_issues(sheet, records)
            valid_by_sheet[sheet] = valid
            for i in iss:
                (errors if i.level == "error" else warnings).append(i)

        # 步骤④⑤：关联校验 + 重复检测 + 知识点匹配
        conn = db.connect(db_path)
        try:
            issues: list[Issue] = []
            _ref_issues(_Context(conn, valid_by_sheet), issues,
                        force_answers=force and kind == "answers")
            _kp_issues(conn, valid_by_sheet, issues)
        finally:
            conn.close()
        for i in issues:
            (errors if i.level == "error" else warnings).append(i)

        # 关联大面积断裂 → 整批 failed（按错误行数占比，E_REF 系）
        err_row_keys = {(i.sheet, i.row) for i in errors if i.row is not None}
        for sheet, rows in parsed.items():
            if not rows:
                continue
            ref_rows = {(i.sheet, i.row) for i in errors if i.sheet == sheet and i.code == E_REF}
            if len(ref_rows) / len(rows) >= REF_BREAKDOWN_RATIO:
                fatal = (f"工作表「{sheet}」关联断裂行占比 {len(ref_rows)}/{len(rows)} "
                         f"达到阈值 {REF_BREAKDOWN_RATIO:.0%}，整批拒绝")
                break

        ok_total = sum(len(v) for v in valid_by_sheet.values()) - len(err_row_keys)
        if fatal is None and ok_total <= 0:
            fatal = "没有可写入的合法数据行"

        # 步骤⑥：单事务写入合法行（错误行不进入写入集合）
        if fatal is None:
            writeable = {
                sheet: [r for r in rows if (sheet, r["row"]) not in err_row_keys]
                for sheet, rows in valid_by_sheet.items()
            }
            conn = db.connect(db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    ok_rows, w_issues = _write_rows(conn, writeable, batch_id,
                                                    force_answers=force and kind == "answers")
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            except sqlite3.IntegrityError as exc:
                fatal = f"数据库约束冲突（整批回滚）：{exc}"
                ok_rows = 0
            except Exception as exc:
                fatal = f"写入异常（整批回滚）：{exc}"
                ok_rows = 0
            finally:
                conn.close()
            if fatal is None:
                errors.extend(w_issues)
                if ok_rows == 0:
                    fatal = "写入完成后无成功行"
    except ImportFatalError as exc:
        fatal = str(exc)
    except Exception as exc:  # 校验阶段意外异常同样整批失败
        fatal = f"导入处理异常：{exc}"

    if fatal is not None:
        ok_rows = 0
    failed_rows = total_rows - ok_rows
    if fatal is not None:
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "committed"
    report = {
        "batch_id": batch_id,
        "kind": kind,
        "file_name": file_name,
        "file_sha256": sha,
        "status": status,
        "total_rows": total_rows,
        "ok_rows": ok_rows,
        "failed_rows": failed_rows,
        "fatal": fatal,
        "errors": [i.to_dict() for i in errors],
        "warnings": [i.to_dict() for i in warnings],
        "created_at": _now(),
    }
    _update_batch(db_path, batch_id,
                  status=status, total_rows=total_rows, ok_rows=ok_rows,
                  failed_rows=failed_rows,
                  error_report=json.dumps(
                      {"fatal": fatal, "errors": report["errors"], "warnings": report["warnings"]},
                      ensure_ascii=False),
                  committed_at=_now() if status in ("committed", "partial") else None)
    return report
