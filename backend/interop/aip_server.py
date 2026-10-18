"""AIP Partner 服务端点：用官方 acps_sdk 暴露 `/rpc`（JSON-RPC 2.0）。

Phase 2：本模块从「自写 RPC 解析」切换为「官方 acps_sdk.aip」——
- `handle_rpc_request`：官方请求解析 + 校验 + 命令分发（JSON-RPC 2.0 错误码语义）。
- `CommandHandlers.on_start`：把 TaskCommand.dataItems 中的文本拼成 prompt，交给现有
  workflow 同步执行（在线程池中运行，避免阻塞事件循环），产物映射为官方 `Product[]`。
- `get` / `cancel` / `complete` / `continue`：复用官方 `DefaultHandlers` 语义，不自写状态机。

角色定位：EduMate-AIP 作为 AIP Partner（见 docs/AIC_TARGET_ARCHITECTURE.md）。

身份绑定说明：当前接入的官方版本 acps-sdk 2.1.0（PyPI）的 AIP RPC 未提供 identity_binding /
local_aic 参数（该能力存在于未发布的 2.2.0 源码，本机已核查但未采用）。因此本轮**不宣称**
mTLS / 身份认证已完成；待取得平台 AIC 与 mTLS 证书后，在部署层（uvicorn ssl + 反向代理
双向认证）接入，属 P1。

官方 acps_sdk 为运行时必需依赖；本模块在构造路由时惰性导入，未安装时抛出明确错误，
保持 `main.py` 在 `ACP_ENABLED=false`（默认）下仍可正常导入。
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request

from app.agent.context import STATUS_COMPLETED
from app.agent.workflow import run_task
from interop.config import AcpSettings


def extract_text(command: Any) -> str:
    """从 TaskCommand.dataItems 中拼接文本（对齐官方 extract_text_from_command 语义）。"""
    texts: list[str] = []
    for item in command.dataItems or []:
        text = getattr(item, "text", None)
        if text:
            texts.append(str(text))
    return "\n".join(texts).strip()


def product_file_uri(settings: AcpSettings, task_id: str, file_name: str) -> str:
    """构造产物文件的绝对下载 URI（供远程调用方获取产物）。

    与现有下载端点 `GET /api/tasks/{task_id}/files/{file_name}` 一致；
    文件名做 percent-encoding，避免中文/空格等导致 URL 非法。
    """
    safe_name = quote(str(file_name), safe="")
    return f"{settings.service_base_url}/api/tasks/{task_id}/files/{safe_name}"


def _import_sdk() -> tuple:
    """惰性导入官方 acps_sdk 的 AIP RPC 相关符号（运行时真实依赖）。"""
    try:
        from acps_sdk.aip.aip_base_model import (  # noqa: F401
            FileDataItem,
            Product,
            TaskState,
            TextDataItem,
        )
        from acps_sdk.aip.aip_rpc_model import RpcResponse  # noqa: F401
        from acps_sdk.aip.aip_rpc_server import (  # noqa: F401
            CommandHandlers,
            TaskManager,
            handle_rpc_request,
        )
    except ImportError as exc:  # pragma: no cover —— SDK 未安装
        raise RuntimeError(
            "官方 acps_sdk 未安装，无法启用 AIP /rpc。请先执行：pip install acps-sdk==2.1.0"
        ) from exc
    return (
        CommandHandlers,
        TaskManager,
        handle_rpc_request,
        RpcResponse,
        FileDataItem,
        Product,
        TaskState,
        TextDataItem,
    )


def create_aip_router(settings: AcpSettings) -> APIRouter:
    """构造 AIP Partner 路由（POST /rpc + GET /health），RPC 由官方 SDK 处理。"""
    (
        CommandHandlers,
        TaskManager,
        handle_rpc_request,
        RpcResponse,
        FileDataItem,
        Product,
        TaskState,
        TextDataItem,
    ) = _import_sdk()

    sender_id = settings.aic or settings.service_name
    local_aic = settings.aic
    identity_binding = settings.identity_binding_resolved

    async def on_start(command, task):
        """start 命令：建任务 → 跑现有 workflow → 映射产物为官方 Product[]。"""
        task_id = command.taskId or f"t-{uuid.uuid4().hex[:12]}"
        command.taskId = task_id

        # 幂等 Start：同一 taskId 已存在 → 返回当前快照（对齐官方 spec）
        if task is not None:
            return task

        prompt = extract_text(command)
        if not prompt:
            task = TaskManager.create_task(
                command,
                initial_state=TaskState.Failed,
                data_items=[TextDataItem(text="start 命令缺少文本 dataItems")],
            )
            task.senderId = sender_id
            return task

        task = TaskManager.create_task(command)
        task.senderId = sender_id  # 覆盖 SDK 占位 "server"
        TaskManager.update_task_status(task_id, TaskState.Working)

        try:
            # run_task 为同步阻塞（含 LLM 调用、180s 超时），放入线程池避免阻塞事件循环
            ctx = await asyncio.to_thread(run_task, prompt)
        except Exception as exc:  # noqa: BLE001 —— 兜底；run_task 内部通常已捕获
            TaskManager.update_task_status(
                task_id, TaskState.Failed, [TextDataItem(text=f"任务执行异常：{exc}")]
            )
            return TaskManager.get_task(task_id)

        if ctx.status == STATUS_COMPLETED:
            products = [
                Product(
                    id=f"p-{i + 1}",
                    name=artifact.name,
                    dataItems=[
                        FileDataItem(
                            name=artifact.file_name,
                            uri=product_file_uri(settings, ctx.task_id, artifact.file_name),
                        )
                    ],
                )
                for i, artifact in enumerate(ctx.artifacts)
            ]
            TaskManager.set_products(task_id, products)
            TaskManager.update_task_status(task_id, TaskState.Completed)
        else:
            TaskManager.update_task_status(
                task_id, TaskState.Failed, [TextDataItem(text=ctx.error or "任务失败")]
            )
        return TaskManager.get_task(task_id)

    handlers = CommandHandlers(on_start=on_start)

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
    async def rpc(request: Request):
        return await handle_rpc_request(
            request,
            handlers,
            local_aic=local_aic,
            identity_binding_enabled=identity_binding,
        )

    return router


def reset_tasks() -> None:
    """清空官方 TaskManager 内存任务快照（测试隔离用）。"""
    from acps_sdk.aip.aip_rpc_server import TaskManager

    TaskManager._tasks.clear()
