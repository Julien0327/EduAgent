"""知识点检索工具（确定性，不调 LLM）。

Phase 2/S3（§4.2）：实现走 retrieval 层（DB 82 小学 KP 双路召回，初中已按决策㉝归档，替换 Phase 0
的种子 7 KP）；ref=kp_id（§4.4）；结构化过滤白名单由 question_search 提供。
"""
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.retrieval.service import get_retrieval_service
from backend.app.tools.registry import tool


class SearchKnowledgePointsParams(BaseModel):
    subject: Literal["数学", "语文", "英语"] = Field(
        default="数学", description="学科名称（当前仅数学有内容，§14 决策②）"
    )
    query: str = Field(..., min_length=1, description="检索词，如：一元二次方程 / 分数应用题")
    stage: Literal["primary", "junior"] | None = Field(
        default=None, description="学段过滤：primary=小学 / junior=初中 / None=全学段"
    )
    limit: int = Field(default=10, ge=1, le=50, description="返回知识点数量上限")


@tool(
    name="search_knowledge_points",
    description="按检索词召回知识点（关键词+向量双路），返回 kp_id、名称、描述与学段。",
    params_model=SearchKnowledgePointsParams,
)
def search_knowledge_points(params: SearchKnowledgePointsParams) -> dict:
    if params.subject != "数学":
        # schema 通用、内容只做数学（决策②）：如实返回空而非报错
        return {"knowledge_points": []}
    kps = get_retrieval_service().search_knowledge_points(
        params.query, stage=params.stage, top_k=params.limit
    )
    return {"knowledge_points": kps}
