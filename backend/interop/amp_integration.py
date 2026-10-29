"""AMP 可观测性集成：心跳 + Access 日志。

使用官方 acps_sdk.amp 的 HeartbeatEmitter / AccessEmitter，
将日志写入本地 JSONL 文件，由 Fluent Bit 转发到平台 Kafka。

设计原则：
- 心跳由生产服务进程后台周期运行，非独立脚本。
- 所有 AMP 写日志失败仅记录诊断日志，不影响业务请求。
- 生命周期与 FastAPI lifespan 绑定，关闭时正确取消后台任务。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
_AMP_LOG_DIR = Path(os.getenv("AMP_LOG_DIR", "/opt/EduAgent/logs/amp"))
_HEARTBEAT_INTERVAL = float(os.getenv("AMP_HEARTBEAT_INTERVAL_SECONDS", "15"))


def _ensure_log_dir() -> Path:
    _AMP_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _AMP_LOG_DIR


# ── 心跳 ──────────────────────────────────────────────
_heartbeat_task: asyncio.Task | None = None
_heartbeat_emitter = None  # type: ignore[assignment]


async def start_heartbeat(aic: str) -> None:
    """启动后台周期心跳任务。幂等：重复调用安全。"""
    global _heartbeat_task, _heartbeat_emitter
    if _heartbeat_task is not None and not _heartbeat_task.done():
        logger.debug("AMP heartbeat already running")
        return

    try:
        from acps_sdk.amp import HeartbeatEmitter
    except ImportError:
        logger.warning("acps_sdk.amp.HeartbeatEmitter unavailable; heartbeat disabled")
        return

    log_dir = _ensure_log_dir()
    log_file = log_dir / "amp_heartbeat_edumate.jsonl"

    try:
        _heartbeat_emitter = HeartbeatEmitter(log_file=log_file, aic=aic)
    except Exception:
        logger.exception("Failed to create HeartbeatEmitter")
        return

    interval = _HEARTBEAT_INTERVAL
    logger.info(
        "AMP heartbeat starting: file=%s aic=%s interval=%.1fs",
        log_file, aic, interval,
    )

    async def _periodic_loop() -> None:
        """包装 run_periodic，捕获异常避免泄漏。"""
        try:
            await _heartbeat_emitter.run_periodic(interval)
        except asyncio.CancelledError:
            logger.info("AMP heartbeat task cancelled")
            raise
        except Exception:
            logger.exception("AMP heartbeat loop unexpected error")

    _heartbeat_task = asyncio.create_task(_periodic_loop(), name="amp-heartbeat")


async def stop_heartbeat() -> None:
    """优雅停止心跳任务。"""
    global _heartbeat_task
    if _heartbeat_task is not None and not _heartbeat_task.done():
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except asyncio.CancelledError:
            pass
        logger.info("AMP heartbeat stopped")
    _heartbeat_task = None


# ── Access 日志 ───────────────────────────────────────
_access_emitter = None  # type: ignore[assignment]


def init_access_emitter(aic: str) -> None:
    """初始化 AccessEmitter（同步，在 lifespan 中调用）。"""
    global _access_emitter
    try:
        from acps_sdk.amp import AccessEmitter
    except ImportError:
        logger.warning("acps_sdk.amp.AccessEmitter unavailable; access logging disabled")
        return

    log_dir = _ensure_log_dir()
    log_file = log_dir / "amp_access_edumate.jsonl"

    try:
        _access_emitter = AccessEmitter(log_file=log_file, aic=aic)
        logger.info("AMP AccessEmitter initialized: file=%s aic=%s", log_file, aic)
    except Exception:
        logger.exception("Failed to create AccessEmitter")


def emit_access_log(
    *,
    method: str,
    url: str,
    route: str,
    status_code: int,
    duration_ms: float,
    caller_aic: str | None = None,
    callee_aic: str | None = None,
    request_body_size: int | None = None,
    response_body_size: int | None = None,
    correlation_id: str | None = None,
    error_message: str | None = None,
    error_code: int | str | None = None,
) -> None:
    """写入一条 AMP Access 日志。失败仅记录诊断日志，不抛异常。"""
    if _access_emitter is None:
        return

    try:
        from acps_sdk.amp import (
            AccessBody,
            AccessParticipant,
            AccessRequest,
            AccessResponse,
            ErrorInfo,
        )

        body = AccessBody(
            duration_ms=duration_ms,
            request=AccessRequest(
                method=method,
                url=url,
                route=route,
                body_size_bytes=request_body_size,
            ),
            response=AccessResponse(
                status_code=status_code,
                body_size_bytes=response_body_size,
            ),
            caller=AccessParticipant(aic=caller_aic),
            callee=AccessParticipant(aic=callee_aic),
            error=(
                ErrorInfo(code=error_code, message=error_message)
                if error_message or error_code
                else None
            ),
        )
        _access_emitter.emit_sync(
            body,
            correlation_id=correlation_id,
        )
    except Exception:
        logger.debug("AMP access log write failed", exc_info=True)
