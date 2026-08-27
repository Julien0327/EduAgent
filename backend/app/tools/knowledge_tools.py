"""知识点检索工具（确定性，不调 LLM）。"""
from typing import Literal

from pydantic import BaseModel, Field

from app.knowledge.loader import load_knowledge_points
from app.tools.registry import tool


class SearchKnowledgePointsParams(BaseModel):
    subject: Literal["数学", "语文", "英语"] = Field(
        default="数学", description="学科名称（当前种子数据仅含数学）"
    )
    chapter: str = Field(..., description="章节名称，如：一元二次方程")
    limit: int = Field(default=10, ge=1, le=50, description="返回知识点数量上限")


@tool(
    name="search_knowledge_points",
    description="根据学科与章节检索知识点，返回知识点 id、名称、描述与常见题型。",
    params_model=SearchKnowledgePointsParams,
)
def search_knowledge_points(params: SearchKnowledgePointsParams) -> dict:
    data = load_knowledge_points()
    # subject / chapter 是种子文件的顶层字段，逐条 kp 不含这两项
    if data.get("subject") != params.subject or params.chapter not in data.get("chapter", ""):
        return {"knowledge_points": []}
    points = data["knowledge_points"][: params.limit]
    return {
        "knowledge_points": [
            {
                "kp_id": p["kp_id"],
                "name": p["name"],
                "description": p.get("description", ""),
                "typical_types": p.get("typical_types", []),
            }
            for p in points
        ]
    }
