"""学情报告 PDF 生成（reportlab）。

使用 reportlab 内置 CID 字体 STSong-Light 渲染中文；柱状图用
reportlab.graphics 内置图表（不引入 matplotlib）。对应技术方案 §9 / §12 / §18。
"""
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing

# 注册内置中文字体（STSong-Light，无需外部字体文件）
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

FONT = "STSong-Light"
WEAK_THRESHOLD = 0.6  # 正确率低于该值标红（薄弱）
WEAK_RED = colors.HexColor("#dc2626")

ST_TITLE = ParagraphStyle(
    name="CNTitle", fontName=FONT, fontSize=20, leading=28, alignment=TA_CENTER, spaceAfter=8
)
ST_SUB = ParagraphStyle(
    name="CNSub", fontName=FONT, fontSize=11, leading=16, alignment=TA_CENTER, spaceAfter=12
)
ST_HEADING = ParagraphStyle(
    name="CNHeading", fontName=FONT, fontSize=14, leading=18, spaceBefore=10, spaceAfter=6
)
ST_BODY = ParagraphStyle(name="CNBody", fontName=FONT, fontSize=11, leading=16, spaceAfter=4)
ST_CELL = ParagraphStyle(name="CNCell", fontName=FONT, fontSize=10, leading=14, alignment=TA_CENTER)
ST_CELL_RED = ParagraphStyle(
    name="CNCellRed", fontName=FONT, fontSize=10, leading=14, alignment=TA_CENTER, textColor=WEAK_RED
)


def _cell(text: str, red: bool = False) -> Paragraph:
    return Paragraph(str(text), ST_CELL_RED if red else ST_CELL)


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _build_metrics_table(stats: dict) -> Table:
    headers = ["平均分", "中位数", "最高分", "最低分", "标准差", "及格率", "优秀率"]
    values = [
        f"{stats['avg']:.1f}",
        f"{stats['median']:.1f}",
        str(stats["max"]),
        str(stats["min"]),
        f"{stats['std']:.2f}",
        _pct(stats["pass_rate"]),
        _pct(stats["excellent_rate"]),
    ]
    data = [[_cell(h) for h in headers], [_cell(v) for v in values]]
    table = Table(data, colWidths=[23 * mm] * len(headers))
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9e2f3")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa5b1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


def _build_bar_chart(distribution: list) -> Drawing:
    labels = [d["label"] for d in distribution]
    counts = [d["count"] for d in distribution]
    max_count = max(counts) if counts else 1

    drawing = Drawing(420, 200)
    chart = VerticalBarChart()
    chart.x = 50
    chart.y = 42
    chart.width = 340
    chart.height = 130
    chart.data = [counts]
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.fontName = FONT
    chart.categoryAxis.labels.fontSize = 10
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = max_count + 2
    chart.valueAxis.labels.fontName = FONT
    chart.valueAxis.labels.fontSize = 9
    chart.bars[0].fillColor = colors.HexColor("#2563eb")
    chart.barLabels.nudge = 5
    chart.barLabels.fontName = FONT
    chart.barLabels.fontSize = 9
    chart.barLabelFormat = "%d"
    drawing.add(chart)
    return drawing


def _build_accuracy_table(per_question: list) -> Table:
    header = ["题号", "题型", "满分", "正确率"]
    data = [[_cell(h) for h in header]]
    for p in per_question:
        acc = p["accuracy"]
        red = acc < WEAK_THRESHOLD
        data.append(
            [
                _cell(p["question_id"], red),
                _cell(p["type"], red),
                _cell(str(p["full_score"]), red),
                _cell(_pct(acc), red),
            ]
        )
    table = Table(data, colWidths=[30 * mm, 40 * mm, 30 * mm, 40 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9e2f3")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa5b1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


def _build_weak_kp_table(weak_kp: list) -> Table:
    header = ["薄弱知识点", "正确率"]
    data = [[_cell(h) for h in header]]
    for kp in weak_kp:
        red = kp["accuracy"] < WEAK_THRESHOLD
        data.append([_cell(kp["name"], red), _cell(_pct(kp["accuracy"]), red)])
    table = Table(data, colWidths=[90 * mm, 40 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9e2f3")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9aa5b1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return table


def create_report_pdf(
    stats: dict, diagnosis: dict, weak_kp: list, output_path: str | Path
) -> str:
    """根据统计、诊断文字、薄弱知识点生成学情报告 PDF，返回文件路径。"""
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    story = []
    # 1. 封面 / 标题 + 班级信息
    story.append(Paragraph("学情分析报告", ST_TITLE))
    story.append(
        Paragraph(
            f"班级：{stats['class_name']}　　人数：{stats['student_count']} 人　　满分：{stats['full_marks']} 分",
            ST_SUB,
        )
    )

    # 2. 班级整体指标
    story.append(Paragraph("一、班级整体指标", ST_HEADING))
    story.append(_build_metrics_table(stats))
    story.append(Spacer(1, 12))

    # 3. 分数段分布（柱状图）
    story.append(Paragraph("二、分数段分布", ST_HEADING))
    story.append(_build_bar_chart(stats["score_distribution"]))
    story.append(Spacer(1, 12))

    # 4. 逐题正确率（表格，标红薄弱题）
    story.append(Paragraph("三、逐题正确率", ST_HEADING))
    story.append(_build_accuracy_table(stats["per_question_accuracy"]))
    story.append(Spacer(1, 12))

    # 5. 薄弱知识点诊断 + 建议
    story.append(Paragraph("四、薄弱知识点诊断与教学建议", ST_HEADING))
    story.append(Paragraph("薄弱知识点（按正确率升序）", ST_BODY))
    story.append(_build_weak_kp_table(weak_kp))
    story.append(Spacer(1, 10))
    story.append(Paragraph(diagnosis.get("summary", ""), ST_BODY))
    story.append(Paragraph(diagnosis.get("diagnosis", ""), ST_BODY))
    story.append(Spacer(1, 6))
    for s in diagnosis.get("suggestions", []):
        story.append(Paragraph("• " + s, ST_BODY))

    doc.build(story)
    return str(output_path)
