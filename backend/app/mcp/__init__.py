"""MCP 能力开放（Phase 5 / S4，§8.3 / 决策③⑫㊾）。

三个工具复用现有服务层，不在 MCP 层重写检索/题库/诊断逻辑：
- ``search_knowledge_points`` → 检索阶段 1（kp_recall）
- ``query_question_bank``     → 检索阶段 2/3（question_search）
- ``diagnose_learning_gaps``  → 诊断服务层（diagnosis.service）

传输：stdio（本地受信）+ Streamable HTTP（远程，双 Token + 敏感开关）。
"""
from backend.app.mcp.server import build_mcp_server, create_mcp_app, run_stdio

__all__ = ["build_mcp_server", "create_mcp_app", "run_stdio"]
