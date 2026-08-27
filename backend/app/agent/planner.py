"""任务规划器（Planner）。

用 1 次 LLM 调用把自然语言任务解析为结构化计划（工作流 + 参数），对应技术方案 §4。
LLM 解析失败或章节缺失时，回退到基于关键词的确定性解析，保证现场演示稳定。
"""
import re
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.llm_utils import structured_completion


class PlanParams(BaseModel):
    subject: str = Field(default="数学", description="学科")
    chapter: str = Field(default="", description="章节，如：一元二次方程")
    count: int = Field(default=10, ge=1, le=20, description="题量")
    difficulty: Literal["简单", "中等", "较难", "混合"] = Field(default="混合", description="难度")
    question_types: list[Literal["选择题", "填空题", "解答题"]] = Field(
        default_factory=lambda: ["选择题", "填空题", "解答题"], description="题型"
    )
    total_score: int = Field(default=100, ge=1, description="总分")
    topic: str = Field(default="", description="教案主题")


class TaskPlan(BaseModel):
    workflow: Literal["exam_loop", "question_gen", "lesson_plan"]
    params: PlanParams


def _extract_chapter(prompt: str) -> str:
    m = re.search(r"《(.+?)》", prompt)
    return m.group(1) if m else ""


def fallback_plan(prompt: str) -> TaskPlan:
    """确定性兜底：按关键词判断工作流，并抽取《》中的章节（默认种子章节）。"""
    chapter = _extract_chapter(prompt) or "一元二次方程"
    if "教案" in prompt or "教学设计" in prompt:
        workflow = "lesson_plan"
        topic = _extract_chapter(prompt) or "一元二次方程"
    elif ("分析" in prompt) or ("报告" in prompt) or ("学情" in prompt):
        workflow = "exam_loop"
        topic = ""
    else:
        workflow = "question_gen"
        topic = ""
    return TaskPlan(workflow=workflow, params=PlanParams(chapter=chapter, topic=topic))


def plan(prompt: str) -> TaskPlan:
    """把自然语言任务解析为计划。LLM 失败或章节缺失时回退到确定性解析。"""
    system = (
        "你是教学任务规划器，负责把老师的自然语言需求解析为结构化计划。"
        "从需求中抽取学科、章节、题量、难度、题型，并判断要执行的工作流：\n"
        "- exam_loop：章节小测一条龙（出卷 + 成绩分析 + 学情报告）\n"
        "- question_gen：只智能组卷（出试卷 + 答题卡）\n"
        "- lesson_plan：生成教案\n"
        "务必调用 submit_plan 工具提交结果。"
    )
    try:
        result = structured_completion(system, prompt, "submit_plan", TaskPlan)
    except Exception:
        return fallback_plan(prompt)
    # LLM 未抽到章节时用确定性抽取补齐，仍无则整体回退
    result.params.chapter = result.params.chapter or _extract_chapter(prompt)
    if not result.params.chapter:
        return fallback_plan(prompt)
    return result
