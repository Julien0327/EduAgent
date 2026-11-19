"""MCP 服务器构建（Phase 5 / S4，§8.3 / 决策③⑫㊾）。

以 S0 探针（mcp 2.3.0）为准：``MCPServer``（非 FastMCP）；工具 type hint → 参数
Schema；``ctx: Context`` 参数自动注入（``mcpserver.context.Context``）；handler 抛
``ToolError`` → 客户端 isError 结果（文本保留）。

两种传输：
- **stdio**：``run_stdio()``，本地受信宿主进程；stdout 仅 JSON-RPC，日志走 stderr。
- **Streamable HTTP**：``create_mcp_app()`` 返回 Starlette app（挂载到 FastAPI 的
  ``/mcp``）+ session manager（需在 FastAPI lifespan 里 ``async with ... .run()``）。
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from backend.app.mcp.tools import (
    diagnose_learning_gaps,
    query_question_bank,
    search_knowledge_points,
)
from backend.config import settings

SERVICE_NAME = "edumate-mcp"
SERVICE_VERSION = "1.0.0"


def mcp_transport_security() -> TransportSecuritySettings:
    """DNS Rebinding 防护设置（SDK Host/Origin 白名单）。

    风险与策略（S4 安全核查）：
    - 认证/限流**不能**替代 DNS Rebinding 防护：浏览器发起的跨源请求可正常携带
      Token（若页面已获得），而 rebinding 攻击利用的是 Host 头仍为攻击者域名、
      解析却指向本机/内网地址；
    - 部署形态：本地 uvicorn 绑定 127.0.0.1；容器内 uvicorn 绑定 0.0.0.0，经
      nginx 反向代理（Host=edumateaip.xyz）或端口直连（Host=IP:8000）；
    - 因此启用 SDK 防护，白名单默认覆盖本地回环 + 正式域名，可由环境变量覆盖；
      Host 不在白名单 → 421，Origin 不在白名单 → 403（SDK 行为）。
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.EDUMATE_MCP_ALLOWED_HOSTS),
        allowed_origins=list(settings.EDUMATE_MCP_ALLOWED_ORIGINS),
    )


def build_mcp_server() -> MCPServer:
    server = MCPServer(name=SERVICE_NAME, version=SERVICE_VERSION,
                       description="EduMate 小学数学教学助手能力开放（MCP）")
    server.add_tool(search_knowledge_points, name="search_knowledge_points",
                    description="检索小学数学知识点（阶段 1 双路召回）")
    server.add_tool(query_question_bank, name="query_question_bank",
                    description="按知识点/章节/难度/题型查询题库题目（阶段 2/3）")
    server.add_tool(diagnose_learning_gaps, name="diagnose_learning_gaps",
                    description="班级学情归因诊断（敏感工具，需管理员 Token + 环境开关）")
    return server


def create_mcp_app(streamable_http_path: str = "/"):
    """构建 Streamable HTTP 端点的 Starlette app + session manager。

    ``streamable_http_path="/"``：便于 FastAPI 以 ``app.mount("/mcp", mcp_app)`` 挂载
    （mount 前缀 /mcp 后，内部路由 / 正好对应 MCP 端点 /mcp/）。
    """
    server = build_mcp_server()
    app = server.streamable_http_app(streamable_http_path=streamable_http_path,
                                     transport_security=mcp_transport_security())
    return app, server.session_manager


def run_stdio() -> None:
    """本地 stdio 传输（受信宿主进程；stdout 仅协议，日志走 stderr）。"""
    build_mcp_server().run(transport="stdio")
