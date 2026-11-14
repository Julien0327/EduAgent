"""题库问数工具（Phase 2 / S4，§4.1 受限域 NL 问数）。

注册进工具注册表：LLM 抽参（失败确定性回退）→ 参数化 SQL 检索（不生成
自由 SQL）→ 可选总分配平。返回题带 ref（§4.4，消费方装配进任务账本）。
"""
from pydantic import BaseModel, Field

from backend.app.retrieval.nl_query import query_question_bank
from backend.app.tools.registry import tool


class QueryQuestionBankParams(BaseModel):
    text: str = Field(..., min_length=1, description="自然语言查询，如：找20道小学分数加减应用题，总分100分")


@tool(
    name="query_question_bank",
    description=(
        "在真实题库中检索题目（不生成自由 SQL）：抽取学段/难度/题型/数量/总分"
        "参数后参数化查询，返回题干、答案、难度、知识点与引用 ref。"
    ),
    params_model=QueryQuestionBankParams,
)
def query_question_bank_tool(params: QueryQuestionBankParams) -> dict:
    return query_question_bank(params.text)
