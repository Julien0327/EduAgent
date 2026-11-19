"""MCP 认证与工具级权限（Phase 5 / S4，§8.3 / 决策㊾③）。

复用 ``backend.app.api.security`` 的 token 提取/比较，不另建安全体系：
- **认证（401）**：由 SecurityMiddleware 覆盖 ``/mcp`` 完成（普通或管理员 Token 任一放行）；
- **工具级权限**：敏感工具 ``diagnose_learning_gaps`` 需「环境总开关开 + 管理员 Token」；
  stdio 本地受信（``ctx.headers`` 为 None）不受 Token 限制，但仍受环境开关约束；
- **日志脱敏**：沿用全局 RedactFilter（Bearer → ***）；敏感访问审计只记 class_id/类型，
  不记学生姓名/学号/完整 Token。
"""
from __future__ import annotations

from collections.abc import Mapping

from mcp.server.mcpserver.exceptions import ToolError

from backend.app.api.security import (
    extract_token as _extract_token,
    get_admin_token as _get_admin_token,
    get_expected_token as _get_user_token,
    token_ok as _token_ok,
)
from backend.config import settings


class SensitiveDisabledError(ToolError):
    """敏感工具环境总开关关闭（预期失败，文本到达客户端 isError 结果）。"""


class PermissionDeniedError(ToolError):
    """需要管理员 Token 授权（预期失败，文本到达客户端 isError 结果）。"""


def classify_token(provided: str | None) -> str | None:
    """判断 token 类型：'admin' / 'user' / None（无效或未提供）。"""
    if not provided:
        return None
    if _token_ok(provided, _get_admin_token()):
        return "admin"
    if _token_ok(provided, _get_user_token()):
        return "user"
    return None


def _ctx_headers(ctx) -> Mapping[str, str] | None:
    """安全读取 MCP Context 的请求头；不可用（stdio/直接调用/无 request）→ None。"""
    if ctx is None:
        return None
    try:
        return ctx.headers
    except Exception:
        return None


def _extract_mcp_token(headers: Mapping[str, str] | None) -> str | None:
    if not headers:
        return None
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    return _extract_token(lowered)


def require_sensitive(ctx) -> None:
    """敏感工具授权：环境总开关开 +（远程）管理员 Token。

    - 开关关 → ``SensitiveDisabledError``（无论传输/身份）；
    - 开关开 + 远程（``ctx.headers`` 非 None）→ 需管理员 Token，否则 ``PermissionDeniedError``；
    - 开关开 + stdio（``ctx.headers`` 为 None）→ 放行（本地受信）。
    """
    if not settings.EDUMATE_MCP_ALLOW_SENSITIVE:
        raise SensitiveDisabledError(
            "敏感工具 diagnose_learning_gaps 未开启（EDUMATE_MCP_ALLOW_SENSITIVE=0）")
    headers = _ctx_headers(ctx)
    if headers is None:
        return  # stdio 本地受信
    token = _extract_mcp_token(headers)
    if classify_token(token) != "admin":
        raise PermissionDeniedError(
            "diagnose_learning_gaps 需要管理员 Token 授权（普通 Token 无权调用）")
