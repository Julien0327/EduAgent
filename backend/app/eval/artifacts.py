"""产物三层校验（S2-A，要求 3）。

避免"文件生成了所以任务成功"的伪通过：
1. 文件存在：预期文件已生成；
2. 文件有效：DOCX/XLSX/PDF/JSON 能正常打开解析且非空；
3. 内容断言：对代表 case 检查必要字段/章节/题量/结构。

分层策略（用户裁决）：全部 case 三层中的第 1/2 层；第 3 层只对每工作流的代表 case
做（多 case 复用时相同断言）。

注意：本模块只负责"校验与断言"，不决定任务是否成功——判定在 runner 汇总。
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.app.eval.models import ArtifactCheck


def check_exists(path: Path, a: ArtifactCheck) -> bool:
    a.exists = path.exists()
    if not a.exists:
        a.error = f"文件不存在：{path.name}"
    return a.exists


def _check_valid_docx(path: Path) -> bool:
    from docx import Document
    doc = Document(str(path))
    n_paras = sum(1 for p in doc.paragraphs if p.text.strip())
    return n_paras > 0


def _check_valid_xlsx(path: Path) -> bool:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=True)
    total = sum(ws.max_row or 0 for ws in wb.worksheets if ws.max_row is not None)
    return total > 0


def _check_valid_pdf(path: Path) -> bool:
    import pypdf
    reader = pypdf.PdfReader(str(path))
    return len(reader.pages) > 0


def _check_valid_json(path: Path) -> bool:
    data = json.loads(path.read_text(encoding="utf-8"))
    return isinstance(data, (dict, list)) and len(json.dumps(data, ensure_ascii=False)) > 0


VALIDITY = {
    ".docx": _check_valid_docx,
    ".xlsx": _check_valid_xlsx,
    ".pdf": _check_valid_pdf,
    ".json": _check_valid_json,
}


def check_valid(path: Path, a: ArtifactCheck) -> bool:
    ext = path.suffix.lower()
    fn = VALIDITY.get(ext)
    if fn is None:
        a.error = f"未支持的产物类型：{ext}"
        return False
    try:
        ok = fn(path)
    except Exception as exc:
        a.error = f"解析失败（{ext}）：{type(exc).__name__}: {exc}"
        return False
    a.valid = ok
    if not ok:
        a.error = f"文件可解析但为空/无效：{path.name}"
    return ok


# ---- 内容断言（代表 case；键=产物文件名，值=断言名→是否通过）----
CONTENT_SELECTED_CASES = {
    # exam_loop 代表：试卷标题/题量 + 报告类型（种子题库固定标题，按实际可断）
    "gc_e03": {
        "01_试卷.docx": {"含'课堂小测'标题": "课堂小测",
                          "至少1个题目": True},
        "03_学情报告.pdf": {"非空PDF": True},
    },
    "gc_e13": {
        "03_学情报告.pdf": {"非空PDF": True},
        "02_答题卡.xlsx": {"非空表格": True},
    },
    # question_gen 代表
    "gc_q01": {
        "01_试卷.docx": {"生成10道题?": None},   # 由 runner 用实际题量断言
    },
    # lesson_plan 代表
    "gc_l01": {"01_教案.docx": {"含教学目标": "教学目标"}},
    # class_diagnosis 代表
    "gc_c01": {
        "04_诊断结果.json": {"含chains键": {"has_key": "chains"}},
        "05_学情诊断报告.pdf": {"非空PDF": True},
    },
}


def _assert_contains(text: str, needle, a: ArtifactCheck, name: str):
    if isinstance(needle, bool) and needle:
        a.content_assertions[name] = len(text.strip()) > 0
    elif isinstance(needle, str):
        a.content_assertions[name] = needle in text
    elif isinstance(needle, dict) and "has_key" in needle:
        try:
            data = json.loads(text)
            a.content_assertions[name] = needle["has_key"] in data
        except Exception:
            a.content_assertions[name] = False


def run_content_assertions(case_id: str, artifacts: dict[str, Path],
                           extra: dict | None = None) -> dict[str, dict]:
    """对代表 case 运行内容断言；返回 {文件名: {断言名: 通过bool}}。

    extra：runner 可注入按运行态计算的断言（如实际题量）。
    """
    spec = CONTENT_SELECTED_CASES.get(case_id, {})
    results: dict[str, dict] = {}
    extra = extra or {}
    for fname, asserts in spec.items():
        path = artifacts.get(fname)
        results.setdefault(fname, {})
        if not path or not path.exists():
            for k in asserts:
                results[fname][k] = False
            continue
        try:
            if fname.endswith(".json"):
                text = path.read_text(encoding="utf-8")
            else:
                text = path.read_bytes().decode("utf-8", errors="ignore")
        except Exception:
            text = ""
        for name, needle in asserts.items():
            if name in (extra.get(fname) or {}):
                results[fname][name] = extra[fname][name]
            else:
                a = ArtifactCheck(name=fname)
                _assert_contains(text, needle, a, name)
                results[fname][name] = a.content_assertions.get(name, False)
    return results


def check_artifacts(case_id: str, artifacts: dict[str, Path]) -> tuple[list[ArtifactCheck], dict[str, dict]]:
    """完整三层校验；返回 ArtifactCheck 列表 + 内容断言结果。"""
    checks: list[ArtifactCheck] = []
    for name, path in sorted(artifacts.items()):
        a = ArtifactCheck(name=name)
        if check_exists(path, a):
            check_valid(path, a)
        checks.append(a)
    content = run_content_assertions(case_id, artifacts)
    return checks, content