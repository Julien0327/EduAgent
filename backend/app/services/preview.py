"""HTML 预览渲染（Jinja2）。

把试卷 / 答题卡数据渲染为 HTML，供前端 iframe 预览 Word / Excel 内容。
预览接口（GET /api/preview/{id}）属于 D4，本阶段只提供渲染函数。
对应技术方案 §9 / §12。
"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# autoescape=True：默认对变量做 HTML 转义，避免 XSS
_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


def render_exam_preview(title: str, questions: list) -> str:
    """渲染试卷 HTML（题干 / 选项，不含答案）。"""
    template = _env.get_template("exam_preview.html")
    return template.render(title=title, questions=questions)


def render_answer_sheet_preview(title: str, questions: list) -> str:
    """渲染答题卡 HTML（题号 / 题型 / 分值 / 标准答案）。"""
    template = _env.get_template("answer_sheet_preview.html")
    return template.render(title=title, questions=questions)
