"""诊断叙述（Phase 3 / S3）。

LLM 只负责把确定性诊断结果转成教师可读的自然语言与教学建议；**不得修改
掌握度/根因/归因路径/置信度/推荐题目，不得补充无证据结论**，辅助证据必须标注。

无 Key / 超时 / 异常 / 输出不符合 schema → 确定性模板兜底，保证基本报告可用。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from backend.config import settings


class DiagnosisNarration(BaseModel):
    summary: str = Field(..., min_length=1)
    diagnosis: str = Field(..., min_length=1)
    suggestions: list[str] = Field(..., min_length=1)


def _facts(diagnosis: dict) -> dict:
    """从确定性结果抽取事实（供 prompt 与模板复用），不引入任何推测。"""
    mastery = diagnosis.get("mastery", {})
    chains = diagnosis.get("chains", [])
    weak = [kp for kp, m in mastery.items() if m["state"] == "weak"]
    weak.sort(key=lambda kp: mastery[kp]["mastery"])
    weak_names = [diagnosis.get("kp_names", {}).get(k, k) for k in weak]
    chain_lines = []
    for c in chains:
        chain_lines.append(
            f"{c['target_name']}：根因 {c['root_name']}，"
            f"路径 {' → '.join(c['path_names'])}，置信度 {c['confidence']}"
            + ("，前置证据不足" if c.get("blocked_prereqs") else "")
            + ("，多根因并存" if c.get("conflict") else "")
        )
    return {"weak_names": weak_names, "chain_lines": chain_lines}


def _template(diagnosis: dict) -> DiagnosisNarration:
    facts = _facts(diagnosis)
    weak = "、".join(facts["weak_names"]) or "无"
    chains = facts["chain_lines"]
    summary = f"本班共识别 {len(diagnosis.get('chains', []))} 条薄弱知识点归因链。"
    diagnosis_text = (
        f"薄弱知识点（按掌握度升序）：{weak}。\n"
        + ("\n".join("- " + c for c in chains) if chains else "无归因链。")
    )
    suggestions = [
        "优先对归因链的根因知识点进行巩固练习（见推荐题目）。",
        "对低置信度或前置证据不足的结论，建议补充测评后再下结论。",
        "推荐题目均来自现有题库，请结合班级实际选择。",
    ]
    return DiagnosisNarration(summary=summary, diagnosis=diagnosis_text,
                              suggestions=suggestions)


def narrate_diagnosis(diagnosis: dict) -> dict:
    """返回 {summary, diagnosis, suggestions, narration_source}。"""
    facts = _facts(diagnosis)
    if not settings.DEEPSEEK_API_KEY:
        tpl = _template(diagnosis)
        return {**tpl.model_dump(), "narration_source": "template"}

    from backend.app.agent.llm_utils import structured_completion

    system = (
        "你是教学分析专家。下面是已经由确定性规则计算好的学情诊断结果。"
        "你的任务只是把这些结果改写成教师可读的自然语言和教学建议。"
        "**禁止修改掌握度、根因、归因路径、置信度或推荐题目；禁止补充任何无证据的结论。**"
        "若某结论标注了辅助证据或低置信度，必须如实说明，不得当作强证据表述。"
        "务必调用 submit_narration 工具提交结果。"
    )
    user = (
        f"薄弱知识点（按掌握度升序）：{'、'.join(facts['weak_names']) or '无'}\n"
        f"归因链：\n" + ("\n".join("- " + c for c in facts["chain_lines"]) or "无") +
        "\n请给出：summary（班级总体小结）、diagnosis（薄弱知识点诊断叙述）、"
        "suggestions（3-5 条分层教学建议）。"
    )
    try:
        result = structured_completion(system, user, "submit_narration",
                                       DiagnosisNarration)
        return {**result.model_dump(), "narration_source": "llm"}
    except Exception:
        tpl = _template(diagnosis)
        return {**tpl.model_dump(), "narration_source": "template"}
