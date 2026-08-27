"""智能组卷工具（LLM 实时出题）。

generate_questions 的 handler 调 LLM 命制题目并返回结构化 JSON；题目编号、
分值分配、知识点 ID 映射等确定性逻辑由 handler 完成，不依赖 LLM（对应技术方案 §9/§12：
LLM 只负责内容，确定性任务负责结构）。
"""
from typing import Literal

from pydantic import BaseModel, Field

from app.agent.llm_utils import structured_completion
from app.knowledge.loader import load_knowledge_points
from app.tools.registry import tool

# 题型权重：解答题分值高于选择/填空
TYPE_WEIGHT = {"选择题": 1, "填空题": 1, "解答题": 2}


class GenerateQuestionsParams(BaseModel):
    knowledge_points: list[str] = Field(..., description="要覆盖的知识点名称列表")
    count: int = Field(..., ge=1, le=20, description="生成题目数量")
    difficulty: Literal["简单", "中等", "较难", "混合"] = Field(
        default="混合", description="题目难度"
    )
    question_types: list[Literal["选择题", "填空题", "解答题"]] = Field(
        ..., description="题型列表"
    )
    total_score: int = Field(default=100, ge=1, description="试卷总分")


class RawQuestion(BaseModel):
    """LLM 命制的单道题（未分配 id / 分值 / 知识点 ID）。"""

    type: Literal["选择题", "填空题", "解答题"]
    stem: str = Field(..., min_length=1)
    options: list[str] | None = None
    answer: str = Field(..., min_length=1)
    difficulty: Literal["简单", "中等", "较难"]
    analysis: str = ""


class QuestionSet(BaseModel):
    questions: list[RawQuestion]


def _allocate_scores(questions: list[dict], total_score: int) -> list[int]:
    """按题型权重确定性分配分值，保证总分精确等于 total_score。"""
    weights = [TYPE_WEIGHT.get(q["type"], 1) for q in questions]
    total_weight = sum(weights)
    base = total_score // total_weight
    remainder = total_score - base * total_weight
    scores = []
    for i, w in enumerate(weights):
        s = base * w
        if i < remainder:
            s += 1
        scores.append(s)
    return scores


@tool(
    name="generate_questions",
    description="根据知识点命制一套题：返回题干、选项、答案、难度与解析。",
    params_model=GenerateQuestionsParams,
)
def generate_questions(params: GenerateQuestionsParams) -> dict:
    kp_data = load_knowledge_points()["knowledge_points"]
    name_to_id = {kp["name"]: kp["kp_id"] for kp in kp_data}
    name_to_desc = {kp["name"]: kp["description"] for kp in kp_data}

    missing = [n for n in params.knowledge_points if n not in name_to_id]
    if missing:
        return {"error": f"未找到知识点：{missing}"}

    kp_lines = "\n".join(
        f"- {n}：{name_to_desc.get(n, '')}" for n in params.knowledge_points
    )
    system = (
        "你是初中数学教师，负责命制规范的随堂测验题。题目必须覆盖给定知识点，"
        "题干清晰、答案正确、解析准确。务必调用 submit_questions 工具，以参数形式提交。"
    )
    user = (
        f"请命制恰好 {params.count} 道题，覆盖以下知识点：\n{kp_lines}\n"
        f"难度：{params.difficulty}；题型：{'、'.join(params.question_types)}；总分 {params.total_score}。\n"
        "要求：选择题 options 为 4 个形如 'A. xxx' 的选项；填空题、解答题 options 为 null。"
    )

    result = structured_completion(system, user, "submit_questions", QuestionSet)
    raw_questions = [q.model_dump() for q in result.questions]

    if len(raw_questions) < params.count:
        return {"error": f"生成的题目数量不足（期望 {params.count}，实际 {len(raw_questions)}）"}
    raw_questions = raw_questions[: params.count]

    scores = _allocate_scores(raw_questions, params.total_score)
    kp_ids = [name_to_id[n] for n in params.knowledge_points]

    questions = []
    for i, q in enumerate(raw_questions):
        questions.append(
            {
                "question_id": f"q{i + 1}",
                "type": q["type"],
                "stem": q["stem"],
                "options": q.get("options"),
                "answer": q["answer"],
                "score": scores[i],
                "difficulty": q["difficulty"],
                "knowledge_points": [kp_ids[i % len(kp_ids)]],
                "analysis": q.get("analysis", ""),
            }
        )

    return {"questions": questions, "total_score": params.total_score}
