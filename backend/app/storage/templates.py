"""F16 导入模板生成与表头映射（Phase 1 / Step 3）。

- 五个工作表 + 填写说明（产品方案 F16 字段逐一落表）
- 表头别名归一：中文/英文别名 → schema 字段名（§3.4 字段映射）
- 模板由 openpyxl 动态生成，不入库二进制（§10.2）
"""
import io

from openpyxl import Workbook
from openpyxl.styles import Font

# 工作表定义：sheet 名 → [(字段名, 必填, 说明)]
TEMPLATE_SHEETS: dict[str, list[tuple[str, bool, str]]] = {
    "班级": [
        ("name", True, "班级名称，如：初一（1）班；同年级同名视为同一班"),
        ("grade", True, "年级整数 1-12"),
        ("stage", False, "学段 primary/junior/senior；留空按年级推断"),
        ("school", False, "学校名称；演示数据禁用真实校名"),
    ],
    "学生": [
        ("class_name", True, "所属班级名称（关联标识①）"),
        ("student_no", True, "学号，班内唯一（关联标识②）"),
        ("name", True, "学生姓名"),
    ],
    "考试": [
        ("title", True, "考试名称，同一文件内不得重名（关联标识③）"),
        ("class_name", False, "所属班级；留空 = 多班联考"),
        ("exam_date", False, "考试日期，YYYY-MM-DD"),
        ("subject", False, "科目，默认 math"),
        ("stage", False, "学段 primary/junior/senior"),
        ("total_score", False, "卷面总分；与题目分值合计不一致时给出警告"),
    ],
    "考试题目": [
        ("exam_title", True, "所属考试名称（关联标识③）"),
        ("q_no", True, "卷面题号，考试内唯一（关联标识④）"),
        ("q_id", False, "题库题 ID；填写则必须存在于题库"),
        ("kp_names", False, "知识点名称，顿号分隔；匹配不上图谱仅警告"),
        ("score", True, "本题卷面分值，> 0"),
        ("stem", False, "题干快照；q_id 为空时必填"),
        ("answer", False, "答案快照；q_id 为空时必填"),
    ],
    "答题记录": [
        ("exam_title", True, "所属考试名称（关联标识③）"),
        ("class_name", True, "学生所属班级（关联标识①）"),
        ("student_no", True, "学号（关联标识②）"),
        ("q_no", True, "卷面题号（关联标识④）"),
        ("score", True, "得分，0 ≤ 得分 ≤ 该题卷面分值"),
        ("is_correct", False, "对/错/0/1；主观题可留空"),
    ],
}

# 表头别名归一表：任意别名 → schema 字段（§3.4 字段映射）
HEADER_ALIASES: dict[str, str] = {}
_SHEET_FIELD_ALIASES: dict[str, dict[str, str]] = {
    "班级": {
        "班级名称": "name", "班级": "name", "name": "name",
        "年级": "grade", "grade": "grade",
        "学段": "stage", "stage": "stage",
        "学校": "school", "school": "school",
    },
    "学生": {
        "班级": "class_name", "班级名称": "class_name", "class_name": "class_name",
        "学号": "student_no", "student_no": "student_no", "no": "student_no",
        "姓名": "name", "name": "name",
    },
    "考试": {
        "考试名称": "title", "考试": "title", "title": "title",
        "班级": "class_name", "班级名称": "class_name", "class_name": "class_name",
        "考试日期": "exam_date", "日期": "exam_date", "exam_date": "exam_date",
        "科目": "subject", "subject": "subject",
        "学段": "stage", "stage": "stage",
        "总分": "total_score", "total_score": "total_score",
    },
    "考试题目": {
        "考试名称": "exam_title", "考试": "exam_title", "exam_title": "exam_title",
        "题号": "q_no", "q_no": "q_no",
        "题库ID": "q_id", "题库ID（可选）": "q_id", "q_id": "q_id",
        "知识点": "kp_names", "知识点名称": "kp_names", "kp_names": "kp_names",
        "分值": "score", "score": "score",
        "题干": "stem", "stem": "stem",
        "答案": "answer", "answer": "answer",
    },
    "答题记录": {
        "考试名称": "exam_title", "考试": "exam_title", "exam_title": "exam_title",
        "班级": "class_name", "班级名称": "class_name", "class_name": "class_name",
        "学号": "student_no", "student_no": "student_no",
        "题号": "q_no", "q_no": "q_no",
        "得分": "score", "score": "score",
        "对错": "is_correct", "是否正确": "is_correct", "is_correct": "is_correct",
    },
}
for _sheet, _aliases in _SHEET_FIELD_ALIASES.items():
    for _alias, _field in _aliases.items():
        HEADER_ALIASES[(_sheet, _alias.strip())] = _field

# 数据工作表名（用于识别上传文件包含哪些表；"填写说明"不算数据表）
DATA_SHEET_ORDER = ["班级", "学生", "考试", "考试题目", "答题记录"]
INSTRUCTIONS_SHEET = "填写说明"

# kind 判定：文件包含的数据表集合 → 批次 kind（§3.4 枚举）
KIND_FULL = "full"
SINGLE_KIND_BY_SHEET = {
    "班级": "classes", "学生": "students", "考试": "exam",
    "考试题目": "exam", "答题记录": "answers",
}


def resolve_header(sheet: str, raw: object) -> str | None:
    """表头别名归一：返回 schema 字段名；未识别返回 None（英文名不区分大小写）。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    field = HEADER_ALIASES.get((sheet, text))
    if field is None:
        field = HEADER_ALIASES.get((sheet, text.lower()))
    return field


def determine_kind(present_sheets: set[str]) -> str | None:
    """按文件包含的数据表判定批次 kind；无数据表返回 None。"""
    if not present_sheets:
        return None
    if len(present_sheets) > 1:
        return KIND_FULL
    return SINGLE_KIND_BY_SHEET[next(iter(present_sheets))]


# 模板表头显示名（中文首选；导入时经 HEADER_ALIASES 归一，英文别名同样兼容）
HEADER_DISPLAY: dict[str, list[str]] = {
    "班级": ["班级名称", "年级", "学段", "学校"],
    "学生": ["班级", "学号", "姓名"],
    "考试": ["考试名称", "班级", "考试日期", "科目", "学段", "总分"],
    "考试题目": ["考试名称", "题号", "题库ID", "知识点", "分值", "题干", "答案"],
    "答题记录": ["考试名称", "班级", "学号", "题号", "得分", "对错"],
}


def build_import_template() -> bytes:
    """生成统一导入模板工作簿字节（5 数据表 + 填写说明，§10.2）。"""
    wb = Workbook()
    wb.remove(wb.active)

    for sheet_name, fields in TEMPLATE_SHEETS.items():
        ws = wb.create_sheet(title=sheet_name)
        headers = HEADER_DISPLAY[sheet_name]
        ws.append(headers)
        for i in range(1, len(headers) + 1):
            ws.cell(row=1, column=i).font = Font(bold=True)
        # 列宽：按字段说明取一个可读宽度
        for i, (_, _, note) in enumerate(fields, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = max(12, min(30, len(note)))

    # 填写说明（导入时忽略此表）
    ws = wb.create_sheet(title=INSTRUCTIONS_SHEET)
    ws.append(["EduMate 学生数据导入模板 — 填写说明"])
    notes = [
        "1. 五个数据工作表：班级、学生、考试、考试题目、答题记录；可整簿导入，也可只保留需要导入的表。",
        "2. 带 * 的字段为必填（见各表首行表头下方说明，可对照本页）。",
        "3. 关联标识链：①班级 → ②学生（班内学号）→ ③考试 → ④卷面题号，逐级定位失败该行不写入。",
        "4. 考试题目表：q_id 填写则必须存在于题库；q_id 留空时题干与答案必填（外部题快照）。",
        "5. 答题记录表：0 ≤ 得分 ≤ 该题卷面分值；主观题对错可留空。",
        "6. 同一文件重复导入会被指纹拒绝；答题记录重导请在导入时勾选覆盖（仅答题记录允许覆盖）。",
        f"7. 支持的表头别名见系统文档；本模板表头为标准字段名：{'、'.join(TEMPLATE_SHEETS)}。",
    ]
    for note in notes:
        ws.append([note])
    ws.column_dimensions["A"].width = 100

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
