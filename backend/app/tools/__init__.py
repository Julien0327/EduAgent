"""Tool 注册表与工具实现。

导入本包即完成 LLM 工具的注册（search_knowledge_points / generate_questions /
generate_diagnosis / query_question_bank）。确定性文档服务不在此注册，
由 Workflow 直接调用。
"""
from backend.app.tools import (  # noqa: F401
    bank_query_tool,
    diagnosis_tools,
    knowledge_tools,
    question_tools,
)
from backend.app.tools.registry import registry  # noqa: F401
