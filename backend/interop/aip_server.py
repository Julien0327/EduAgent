"""AIP Partner 服务端点：把 EduMate 教学能力暴露为 AIP `/rpc`（JSON-RPC 2.0）。

语义对齐官方（acps_sdk/aip/aip_rpc_server.py + aip_base_model.py）：
- `start`：把 TaskCommand.dataItems 中的文本拼成 prompt，交给现有 workflow 同步执行，
  返回 `completed`/`failed` 的 TaskResult，产物映射为 AIP `Product[]`（file DataItem）。
- `get`：返回该 taskId 的任务快照。
- 其余命令（cancel/continue/complete/re-stream）：P0 返回明确错误，P1 补齐。

角色定位：EduMate-AIP 作为 AIP Partner（详见 AIC_TARGET_ARCHITECTURE.md）。
本模块不依赖官方 acps_sdk（只依赖 interop.models 数据契约）；mTLS / identity_binding
由部署层（uvicorn ssl + 官方 AipPeerCertificateMiddleware）承担，属 P1。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.agent.context import STATUS_COMPLETED, TaskContext
from app.agent.workflow import run_task
from interop.config import AcpSettings
from interop.models import (
    FileDataItem,
    Product,
    RpcError,
    RpcRequest,
    RpcResponse,
    TaskCommand,
    TaskCommandType,
    TaskResult,
    TaskState,
    TaskStatus,
    TextDataItem,
)

# 内存任务快照：AIP taskId -> TaskResult（单进程，P0 简化，P1 换持久化）
_TASKS: dict[str, TaskResult] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def extract_text(command: TaskCommand) -> str:
    """从 TaskCommand.dataItems 中拼接文本（对齐官方 extract_text_from_command 语义）。"""
    texts: list[str] = []
    for item in command.dataItems or []:
        text = getattr(item, "text", None)
        if text:
            texts.append(str(text))
    return "\n".join(texts).strip()


def _ctx_to_result(
    task_id: str,
    internal_task_id: str,
    sender_id: str,
    session_id: str | None,
    ctx: TaskContext,
) -> TaskResult:
    """把内部 TaskContext 映射为 AIP TaskResult。"""
    ok = ctx.status == STATUS_COMPLETED
    state = TaskState.Completed if ok else TaskState.Failed
    products = [
        Product(
            id=f"p-{i + 1}",
            name=artifact.name,
            dataItems=[
                FileDataItem(
                    name=artifact.file_name,
                    uri=f"/api/tasks/{internal_task_id}/files/{artifact.file_name}",
                )
            ],
        )
        for i, artifact in enumerate(ctx.artifacts)
    ]
    status_items = [] if ok else [TextDataItem(text=ctx.error or "任务失败")]
    return TaskResult(
        type="task-result",
        id=f"result-{uuid.uuid4().hex[:12]}",
        sentAt=_now(),
        senderRole="partner",
        senderId=sender_id,
        taskId=task_id,
        sessionId=session_id,
        status=TaskStatus(state=state, stateChangedAt=_now(), dataItems=status_items),
        products=products,
    )


def _error(rpc_id: Any, code: int, message: str, data: Any = None) -> RpcResponse:
    return RpcResponse(id=rpc_id, error=RpcError(code=code, message=message, data=data))


def handle_rpc(rpc_request: RpcRequest, settings: AcpSettings) -> RpcResponse:
    """处理一条 AIP RPC 请求（纯函数，便于离线单测）。"""
    command = rpc_request.params.command
    sender_id = settings.aic or settings.service_name

    if command.command == TaskCommandType.Start:
        prompt = extract_text(command)
        if not prompt:
            return _error(rpc_request.id, -32602, "Invalid params", "start 命令缺少文本 dataItems")
        task_id = command.taskId or f"t-{uuid.uuid4().hex[:12]}"
        ctx = run_task(prompt)
        result = _ctx_to_result(
            task_id=task_id,
            internal_task_id=ctx.task_id,
            sender_id=sender_id,
            session_id=command.sessionId,
            ctx=ctx,
        )
        _TASKS[task_id] = result
        return RpcResponse(id=rpc_request.id, result=result)

    if command.command == TaskCommandType.Get:
        task_id = command.taskId
        if not task_id or task_id not in _TASKS:
            return _error(rpc_request.id, -32001, "Task not found", {"taskId": task_id})
        return RpcResponse(id=rpc_request.id, result=_TASKS[task_id])

    # cancel/continue/complete/re-stream：P0 未实现，返回明确错误
    return _error(
        rpc_request.id,
        -32601,
        "Method not found",
        f"命令 {command.command.value} 在 P0 未实现，P1 补齐",
    )


def create_aip_router(settings: AcpSettings) -> APIRouter:
    """构造 AIP Partner 路由（POST /rpc + GET /health）。"""
    router = APIRouter(tags=["aip"])

    @router.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "agent": settings.name,
            "status": "online",
            "aic": (settings.aic or "")[-8:] if settings.aic else "",
            "tasks": {"active": 0},
        }

    @router.post("/rpc", response_model=RpcResponse)
    async def rpc(request: Request) -> RpcResponse:
        try:
            body = await request.json()
            rpc_request = RpcRequest.model_validate(body)
        except Exception as exc:  # noqa: BLE001 —— 解析失败按 JSON-RPC 规范返回 parse error
            return _error(None, -32700, "Parse error", str(exc))
        return handle_rpc(rpc_request, settings)

    return router


def reset_tasks() -> None:
    """清空内存任务快照（测试隔离用）。"""
    _TASKS.clear()
