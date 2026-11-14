"""知识点检索工具（确定性，不调 LLM）。

Phase 2/S2（§4.4）：凡图谱取数必带 ref（此处 ref=kp_id）；S3 将把实现
切换到 retrieval 层（DB 385 KP），ref 语义不变。
"""
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.knowledge.loader import load_knowledge_points
from backend.app.tools.registry import tool


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
                "ref": p["kp_id"],  # §4.4：图谱取数必带引用
                "name": p["name"],
                "description": p.get("description", ""),
                "typical_types": p.get("typical_types", []),
            }
            for p in points
        ]
    }
