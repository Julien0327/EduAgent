"""Tool 注册表与工具实现。

导入本包即完成 3 个 LLM 工具的注册（search_knowledge_points /
generate_questions / generate_diagnosis）。确定性文档服务不在此注册，
由 Workflow 直接调用。
"""
from app.tools import diagnosis_tools, knowledge_tools, question_tools  # noqa: F401
from app.tools.registry import registry  # noqa: F401
