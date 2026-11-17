"""学情诊断报告 PDF（Phase 3 / S5，§5.4）。

**只做排版呈现，不重新计算任何业务指标**——输入为统一诊断服务
（``diagnosis.service.build_diagnosis``）与叙述（``diagnosis.narrate``）的结果。

内容结构：
1. 标题 + 班级 + 数据范围（含合成演示数据声明）；
2. 掌握度四状态统计与明细（状态/掌握度/样本数/主辅样本/辅助证据占比）；
3. 根因归因链（目标/根因/路径/置信度/阻断前置/证据题目追溯）；
4. 推荐练习（根因巩固/目标针对分组；真实 q_id/难度/来源/理由；放宽策略如实标注）；
5. 诊断叙述（**标注来源**：AI 叙述(LLM) / 模板兜底）。

排版检查项：中文 STSong-Light（内置 CID 字体）；长表自动分页重复表头
（repeatRows）；长文本 Paragraph 换行；页脚页码 + 数据口径；空数据输出「无」
不崩溃。生成后用 pypdf 实读文本验证（测试与验收脚本），不以文件存在为验收。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
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

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

FONT = "STSong-Light"
PAGE_W, PAGE_H = A4
MARGIN = 18 * mm

C_HEADER_BG = colors.HexColor("#d9e2f3")
C_GRID = colors.HexColor("#9aa5b1")
C_WEAK = colors.HexColor("#dc2626")
C_INSUF = colors.HexColor("#d97706")
C_NOCOV = colors.HexColor("#6b7280")
C_NORMAL = colors.HexColor("#16a34a")
C_MUTED = colors.HexColor("#6b7280")

STATE_LABELS = {
    "normal": "正常", "weak": "薄弱",
    "insufficient": "样本不足", "no_coverage": "无覆盖",
}
STATE_ORDER = ("weak", "insufficient", "normal", "no_coverage")
CONF_LABELS = {"high": "高", "mid": "中", "low": "低"}

ST_TITLE = ParagraphStyle(name="DTitle", fontName=FONT, fontSize=19, leading=26,
                          alignment=TA_CENTER, spaceAfter=6)
ST_SUB = ParagraphStyle(name="DSub", fontName=FONT, fontSize=10, leading=15,
                        alignment=TA_CENTER, spaceAfter=10, textColor=C_MUTED)
ST_WARN = ParagraphStyle(name="DWarn", fontName=FONT, fontSize=10, leading=15,
                         alignment=TA_CENTER, spaceAfter=10, textColor=C_WEAK)
ST_H = ParagraphStyle(name="DH", fontName=FONT, fontSize=13, leading=18,
                      spaceBefore=10, spaceAfter=5)
ST_BODY = ParagraphStyle(name="DBody", fontName=FONT, fontSize=10, leading=15,
                         spaceAfter=4)
ST_NOTE = ParagraphStyle(name="DNote", fontName=FONT, fontSize=8.5, leading=12,
                         spaceAfter=4, textColor=C_MUTED)
ST_CELL = ParagraphStyle(name="DCell", fontName=FONT, fontSize=9, leading=12,
                         alignment=TA_CENTER)
ST_CELL_L = ParagraphStyle(name="DCellL", fontName=FONT, fontSize=9, leading=12,
                           alignment=TA_LEFT)


def _c(text, style=None) -> Paragraph:
    return Paragraph(str(text), style or ST_CELL)


def _cl(text) -> Paragraph:
    return Paragraph(str(text), ST_CELL_L)


def _table(data, col_widths, header=True) -> Table:
    t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0)
    style = [
        ("GRID", (0, 0), (-1, -1), 0.5, C_GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), C_HEADER_BG))
    t.setStyle(TableStyle(style))
    return t


def _fmt(x, nd=4):
    return "—" if x is None else f"{x:.{nd}f}"


def _is_demo(diagnosis: dict) -> bool:
    cls = diagnosis.get("class") or {}
    return "演示" in str(cls.get("school") or "") or "演示" in str(cls.get("name") or "")


def _footer(note: str):
    def _draw(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 8)
        canvas.setFillColor(C_MUTED)
        canvas.drawString(MARGIN, 10 * mm, note)
        canvas.drawRightString(PAGE_W - MARGIN, 10 * mm, f"第 {doc.page} 页")
        canvas.restoreState()
    return _draw


def create_diagnosis_pdf(diagnosis: dict, narration: dict | None,
                         output_path: str | Path) -> str:
    """由统一诊断结果生成 PDF；narration 可为 None（未生成叙述时如实标注）。"""
    narration = narration or {}
    kp_names = diagnosis.get("kp_names", {})
    mastery = diagnosis.get("mastery", {})
    chains = diagnosis.get("chains", [])
    recs = diagnosis.get("recommendations", [])
    scope = diagnosis.get("data_scope", {})
    cls = diagnosis.get("class") or {}
    demo = _is_demo(diagnosis)

    doc = SimpleDocTemplate(
        str(output_path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=18 * mm,
        title="学情诊断报告", author="EduMate",
    )
    story = []

    # ── 1. 标题与数据范围 ──
    story.append(Paragraph("学情诊断报告", ST_TITLE))
    story.append(Paragraph(
        f"班级：{cls.get('name', '')}（{cls.get('grade', '')} 年级 / "
        f"{cls.get('stage', '')}）　生成时间："
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", ST_SUB))
    if demo:
        story.append(Paragraph(
            "⚠ 本报告基于合成演示数据（非真实学生），仅用于功能演示与业务验证。", ST_WARN))
    story.append(Paragraph(
        f"数据范围：{scope.get('exam_count', 0)} 场考试 / "
        f"{scope.get('question_count', 0)} 道题 / {scope.get('student_count', 0)} 名学生 / "
        f"{scope.get('answer_count', 0)} 条答题 / 覆盖 {scope.get('kp_covered_count', 0)} 个知识点"
        f"（小学图谱全集 {len(mastery)} 个）", ST_BODY))
    story.append(Paragraph(
        "口径：掌握度 = 卷面分值加权 × 近期考试权重 2:1 × 角色权重（主 1.0 / 辅 0.5）；"
        "样本数 = 不同题目数，<3 判「样本不足」；掌握度 <0.6 判「薄弱」；"
        "归因回溯前置掌握度 <0.7 的节点，前置证据不足不猜测根因。"
        "全部指标由确定性规则计算，叙述文字不参与事实判定。", ST_NOTE))
    story.append(Spacer(1, 6))

    # ── 2. 四状态统计 + 明细 ──
    story.append(Paragraph("一、知识点掌握度（四状态）", ST_H))
    counts = {s: 0 for s in STATE_ORDER}
    for m in mastery.values():
        counts[m["state"]] = counts.get(m["state"], 0) + 1
    story.append(_table(
        [[_c("状态"), _c("薄弱"), _c("样本不足"), _c("正常"), _c("无覆盖")],
         [_c("知识点数"),
          _c(counts.get("weak", 0)), _c(counts.get("insufficient", 0)),
          _c(counts.get("normal", 0)), _c(counts.get("no_coverage", 0))]],
        [30 * mm] + [32 * mm] * 4))
    story.append(Spacer(1, 6))

    covered = [m for m in mastery.values() if m["state"] != "no_coverage"]
    covered.sort(key=lambda m: (STATE_ORDER.index(m["state"])
                                if m["state"] in STATE_ORDER else 9,
                                m["mastery"] if m["mastery"] is not None else 9))
    rows = [[_c("知识点"), _c("状态"), _c("掌握度"), _c("样本数"),
             _c("主/辅样本"), _c("辅助证据占比")]]
    for m in covered:
        rows.append([
            _cl(f"{kp_names.get(m['kp_id'], m['kp_id'])}"),
            _c(STATE_LABELS.get(m["state"], m["state"])),
            _c(_fmt(m["mastery"])),
            _c(m["sample_count"]),
            _c(f"{m['primary_sample_count']}/{m['aux_sample_count']}"),
            _c(_fmt(m["aux_share"], 3)),
        ])
    if len(rows) == 1:
        story.append(Paragraph("本班级无覆盖知识点。", ST_BODY))
    else:
        story.append(_table(rows, [52 * mm, 22 * mm, 22 * mm, 18 * mm, 24 * mm, 28 * mm]))
    no_cov = sorted(kp for kp, m in mastery.items() if m["state"] == "no_coverage")
    if no_cov:
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"无覆盖知识点（本班考试未涉及，共 {len(no_cov)} 个，不输出任何结论）："
            + "、".join(kp_names.get(k, k) for k in no_cov), ST_NOTE))
    story.append(Spacer(1, 6))

    # ── 3. 归因链 ──
    story.append(Paragraph("二、根因归因链", ST_H))
    if not chains:
        story.append(Paragraph("无（本班未发现样本充足的薄弱知识点）。", ST_BODY))
    for i, ch in enumerate(chains, 1):
        head = (f"{i}. {ch.get('target_name', ch['target_kp'])}"
                f"（掌握度 {_fmt(ch.get('target_mastery'))}）"
                f" ← 根因：{ch.get('root_name', ch['root_kp'])}")
        story.append(Paragraph(head, ST_BODY))
        info = [
            [_c("归因路径"), _cl(" → ".join(ch.get("path_names") or []))],
            [_c("置信度"), _c(f"{CONF_LABELS.get(ch.get('confidence'), ch.get('confidence'))}"
                              f"（{ch.get('confidence')}）")],
            [_c("最小样本"), _c(ch.get("min_samples", "—"))],
            [_c("阻断前置"), _cl("、".join(ch.get("blocked_prereq_names") or []) or "无")],
            [_c("多根因并存"), _c("是" if ch.get("conflict") else "否")],
        ]
        t_aux = ch.get("target_aux_share")
        if t_aux is not None and t_aux > 0.5:
            info.append([_c("证据构成"), _cl(
                f"辅助证据占比 {_fmt(t_aux, 3)} > 0.5——薄弱判定主要由辅助证据驱动，"
                f"建议补充主知识点测评后再下结论")])
        if ch.get("confidence") == "low":
            info.append([_c("提示"), _cl(
                "低置信度结论：存在阻断前置 / 分支冲突 / 辅助证据主导 / 样本临界，"
                "请结合课堂观察使用，不作为确定性结论。")])
        story.append(_table(info, [26 * mm, 140 * mm], header=False))
        ev = ch.get("evidence") or []
        if ev:
            ev_lines = "；".join(
                f"{e['q_id']}[{'主' if e['role'] == 'primary' else '辅'}]"
                f"考试{e['exam_label']}#{e['q_no']}" for e in ev[:20])
            story.append(Paragraph(f"证据追溯（题目↔角色↔考试）：{ev_lines}"
                                   + ("…" if len(ev) > 20 else ""), ST_NOTE))
        story.append(Spacer(1, 6))

    # ── 4. 推荐练习 ──
    story.append(Paragraph("三、推荐练习（真实题库）", ST_H))
    if not recs:
        story.append(Paragraph("无推荐（未发现可归因的薄弱知识点）。", ST_BODY))
    for g in recs:
        role_label = "根因巩固" if g["role"] == "root" else "目标针对性"
        head = (f"【{role_label}】{g.get('kp_name', g['kp_id'])}"
                f"（掌握度 {_fmt(g.get('mastery'), 3)}，"
                f"难度带 {g['difficulty_range'][0]}-{g['difficulty_range'][1]}，"
                f"置信度 {CONF_LABELS.get(g.get('confidence'), g.get('confidence'))}）")
        story.append(Paragraph(head, ST_BODY))
        if g.get("relaxation"):
            story.append(Paragraph(
                f"放宽策略：{g['relaxation'].get('reason', '')}"
                f"（采用：{g['relaxation'].get('final_strategy', '')}）", ST_NOTE))
        qs = g.get("questions") or []
        if not qs:
            story.append(Paragraph("该知识点题库候选不足，未推荐题目（如实报告，不以编题补齐）。",
                                   ST_NOTE))
            continue
        qrows = [[_c("题目 ID"), _c("难度"), _c("来源"), _c("题干")]]
        for q in qs:
            stem = (q.get("stem") or "").replace("<", "＜").replace("&", "＆")
            if len(stem) > 60:
                stem = stem[:60] + "…"
            qrows.append([_c(q["q_id"]), _c(q.get("difficulty", "—")),
                          _c(q.get("source", "unknown")), _cl(stem)])
        story.append(_table(qrows, [30 * mm, 14 * mm, 20 * mm, 102 * mm]))
        reason = qs[0].get("recommendation_reason", "")
        if reason:
            story.append(Paragraph(f"推荐理由：{reason}", ST_NOTE))
        story.append(Spacer(1, 5))

    # ── 5. 诊断叙述 ──
    story.append(Paragraph("四、诊断叙述与教学建议", ST_H))
    src = narration.get("narration_source")
    if src == "llm":
        badge = "叙述来源：AI 叙述（LLM 生成，仅解释性文字；掌握度/根因/推荐等事实以上方确定性计算为准）"
    elif src == "template":
        badge = "叙述来源：模板兜底（未调用 LLM 或调用失败；内容均来自确定性计算结果）"
    else:
        badge = "叙述来源：未生成（本次仅输出确定性诊断结果）"
    story.append(Paragraph(badge, ST_NOTE))
    if narration.get("summary"):
        story.append(Paragraph(str(narration["summary"]), ST_BODY))
    if narration.get("diagnosis"):
        story.append(Paragraph(str(narration["diagnosis"]).replace("\n", "<br/>"), ST_BODY))
    for s in narration.get("suggestions") or []:
        story.append(Paragraph("• " + str(s), ST_BODY))

    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "数据来源：F16 导入的班级考试数据 + 小学题库（推荐题目 source 逐条标注）。"
        "本报告不含任何 LLM 生成的事实性数值。", ST_NOTE))

    doc.build(story, onFirstPage=_footer("EduMate 学情诊断报告"),
              onLaterPages=_footer("EduMate 学情诊断报告"))
    return str(output_path)
