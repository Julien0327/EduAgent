"""AMP（Agent Monitoring Protocol）日志构造与落盘。

按已核实 schema（amp/models.py）构造 `message` / `access` 两类日志记录，
字段语义严格对齐官方：

- 顶层 LogRecord 字段 snake_case；body 子字段 camelCase。
- `correlation_id` = 业务关联 ID（AIP 场景 = sessionId，发送/接收双方一致）。
- 同一条消息的 send 与 receive 各为独立日志，通过相同 `messageId` + `correlationId`
  在查询层归并为一条生命周期。

上报通道：官方为 Kafka/TLS（wt.ioa.pub:19092，需平台证书）。P0 提供本地
JSON-lines sink 做结构留档；Kafka 上报在 P1 接入官方 emitter，未配置时明确报错而非静默。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from interop.models import (
    AccessBody,
    AccessParticipant,
    AccessRequest,
    AccessResponse,
    LogRecord,
    MessageBody,
    MessageDestination,
    to_wire,
)


def new_log_id() -> str:
    """生成 log_id（Python 3.14+ uuid7，旧版本回退 uuid4，与官方一致）。"""
    try:
        return str(uuid.uuid7())  # type: ignore[attr-defined]
    except AttributeError:
        return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_message_record(
    *,
    event_type: str,
    system: str,
    destination_name: str,
    message_id: str,
    aic: str,
    session_id: str | None = None,
    destination_kind: str | None = "exchange",
    virtual_host: str | None = None,
    payload_size_bytes: int | None = None,
    delivery_attempt: int | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    attributes: dict[str, Any] | None = None,
    timestamp: str | None = None,
    log_id: str | None = None,
) -> dict[str, Any]:
    """构造一条 AMP `message` 日志记录（线格式 dict）。

    关键不变量：`correlation_id = session_id`；`message_id` 在 send/receive 两侧一致。
    """
    body = MessageBody(
        event_type=event_type,
        system=system,
        destination=MessageDestination(
            name=destination_name,
            kind=destination_kind,  # type: ignore[arg-type]
            virtual_host=virtual_host,
        ),
        message_id=message_id,
        payload_size_bytes=payload_size_bytes,
        delivery_attempt=delivery_attempt,
        attributes=attributes,
    )
    record = LogRecord(
        timestamp=timestamp or now_iso(),
        aic=aic,
        log_type="message",
        log_id=log_id or new_log_id(),
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        correlation_id=session_id,
        body=to_wire(body),
    )
    return to_wire(record)


def build_access_record(
    *,
    aic: str,
    method: str,
    url: str,
    status_code: int,
    duration_ms: float | None = None,
    caller_aic: str | None = None,
    caller_service: str | None = None,
    callee_service: str | None = None,
    request_body_size: int | None = None,
    response_body_size: int | None = None,
    session_id: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    timestamp: str | None = None,
    log_id: str | None = None,
) -> dict[str, Any]:
    """构造一条 AMP `access` 日志记录（线格式 dict）。"""
    body = AccessBody(
        duration_ms=duration_ms,
        request=AccessRequest(
            method=method,
            url=url,
            route=url,
            body_size_bytes=request_body_size,
        ),
        response=AccessResponse(status_code=status_code, body_size_bytes=response_body_size),
        caller=AccessParticipant(aic=caller_aic, service_name=caller_service),
        callee=AccessParticipant(aic=aic, service_name=callee_service),
    )
    record = LogRecord(
        timestamp=timestamp or now_iso(),
        aic=aic,
        log_type="access",
        log_id=log_id or new_log_id(),
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        correlation_id=session_id,
        body=to_wire(body),
    )
    return to_wire(record)


class JsonLinesSink:
    """把 AMP 记录逐行追加到 JSON-lines 文件（P0 结构留档）。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def write(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


class AmpEmitter:
    """AMP 发射器：构造记录并写入本地 sink。

    P1 将接入官方 Kafka/TLS emitter；届时 `sink` 可替换为官方上报通道。
    """

    def __init__(self, aic: str, sink: JsonLinesSink):
        self._aic = aic
        self._sink = sink

    def emit_message(self, **kwargs: Any) -> dict[str, Any]:
        record = build_message_record(aic=self._aic, **kwargs)
        self._sink.write(record)
        return record

    def emit_access(self, **kwargs: Any) -> dict[str, Any]:
        record = build_access_record(aic=self._aic, **kwargs)
        self._sink.write(record)
        return record
