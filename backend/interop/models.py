"""AIP / AMP 数据模型 —— 对官方 acps_sdk 已核实 schema 的忠实复刻。

字段名、类型、枚举值与线格式逐一对齐以下已核实源码（本地核查于
ACPs-community/acps-sdk/acps_sdk/）：
- AIP 基础模型：aip/aip_base_model.py（Message/TaskCommand/TaskResult/TaskState/...）
- AIP RPC 模型：aip/aip_rpc_model.py（RpcRequest/RpcResponse，JSON-RPC 2.0）
- AMP 日志模型：amp/models.py（LogRecord/MessageBody/AccessBody/...）

命名约定（与官方一致）：
- AIP 模型字段直接使用 camelCase 作为 Python 属性名（线格式即 camelCase），无 alias。
- AMP 顶层 LogRecord 字段为 snake_case（线格式即 snake_case）。
- AMP body 子字段为 snake_case 属性名 + camelCase alias，序列化用 by_alias=True。

本模块是「数据契约」，不是官方 SDK 的替代品；真实协议运行时须使用官方 acps_sdk。
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# AIP 基础模型（来源：aip_base_model.py）
# =============================================================================


class TaskState(str, Enum):
    Accepted = "accepted"
    Working = "working"
    AwaitingInput = "awaiting-input"
    AwaitingCompletion = "awaiting-completion"
    Completed = "completed"
    Canceled = "canceled"
    Failed = "failed"
    Rejected = "rejected"


class TaskCommandType(str, Enum):
    Get = "get"
    Start = "start"
    Continue = "continue"
    Cancel = "cancel"
    Complete = "complete"
    ReStream = "re-stream"


class TextDataItem(BaseModel):
    type: Literal["text"] = "text"
    text: str


class FileDataItem(BaseModel):
    type: Literal["file"] = "file"
    name: Optional[str] = None
    mimeType: Optional[str] = None
    uri: Optional[str] = None
    bytes: Optional[str] = None  # noqa: A003 —— 与官方字段名一致


class StructuredDataItem(BaseModel):
    type: Literal["data"] = "data"
    data: dict[str, Any]


DataItem = Union[TextDataItem, FileDataItem, StructuredDataItem]


class TaskStatus(BaseModel):
    state: TaskState
    stateChangedAt: str
    dataItems: Optional[list[DataItem]] = None


class Product(BaseModel):
    id: str
    name: Optional[str] = None
    description: Optional[str] = None
    dataItems: list[DataItem]


class Message(BaseModel):
    type: str = "message"
    id: str
    sentAt: str
    senderRole: Literal["leader", "partner"]
    senderId: str
    mentions: Optional[Union[Literal["all"], list[str]]] = None
    dataItems: Optional[list[DataItem]] = None
    groupId: Optional[str] = None
    sessionId: Optional[str] = None


class TaskCommand(Message):
    type: Literal["task-command"] = "task-command"
    command: TaskCommandType
    commandParams: Optional[dict[str, Any]] = None
    taskId: Optional[str] = None


class TaskResult(Message):
    type: Literal["task-result"] = "task-result"
    taskId: str
    status: TaskStatus
    products: Optional[list[Product]] = None
    commandHistory: Optional[list[TaskCommand]] = None
    statusHistory: Optional[list[TaskStatus]] = None


# =============================================================================
# AIP RPC 模型（来源：aip_rpc_model.py，JSON-RPC 2.0）
# =============================================================================


class RpcRequestParams(BaseModel):
    command: TaskCommand


class RpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: Literal["rpc"] = "rpc"
    id: Optional[Union[str, int]] = None
    params: RpcRequestParams


class RpcError(BaseModel):
    code: int
    message: str
    data: Optional[Any] = None


class RpcResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: Optional[Union[str, int]] = None
    result: Optional[TaskResult] = None
    error: Optional[RpcError] = None


# =============================================================================
# AMP 日志模型（来源：amp/models.py）
# =============================================================================


class ErrorInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: Optional[Union[int, str]] = None
    message: Optional[str] = None
    data: Optional[Any] = None
    stack_trace: Optional[str] = Field(default=None, alias="stackTrace")


class MessageDestination(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    kind: Optional[Literal["topic", "queue", "exchange"]] = None
    virtual_host: Optional[str] = Field(default=None, alias="virtualHost")


class MessageRouting(BaseModel):
    key: Optional[str] = None
    partition: Optional[str] = None
    offset: Optional[int] = None


class MessageSettlement(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    latency_ms: Optional[float] = Field(default=None, alias="latencyMs")
    reason: Optional[str] = None


class MessageBody(BaseModel):
    """AMP message 日志 body（Spec §5.5）。

    每条消息日志通过 eventType 精确描述一次生命周期边事件；
    同一条消息的 send 与 receive 各为独立日志，用相同 messageId + correlationId 归并。
    """

    model_config = ConfigDict(populate_by_name=True)

    event_type: Literal[
        "send", "receive", "ack", "nack", "reject", "timeout", "dead_letter"
    ] = Field(alias="eventType")
    operation_name: Optional[str] = Field(default=None, alias="operationName")
    system: str
    destination: MessageDestination
    consumer_group_name: Optional[str] = Field(default=None, alias="consumerGroupName")
    subscription_name: Optional[str] = Field(default=None, alias="subscriptionName")
    routing: Optional[MessageRouting] = None
    message_id: Optional[str] = Field(default=None, alias="messageId")
    payload_size_bytes: Optional[int] = Field(default=None, alias="payloadSizeBytes")
    delivery_attempt: Optional[int] = Field(default=None, alias="deliveryAttempt")
    settlement: Optional[MessageSettlement] = None
    error: Optional[ErrorInfo] = None
    attributes: Optional[dict[str, Any]] = None


class AccessRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    method: Optional[str] = None
    url: Optional[str] = None
    route: Optional[str] = None
    headers: Optional[dict[str, str]] = None
    body_size_bytes: Optional[int] = Field(default=None, alias="bodySizeBytes")


class AccessResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status_code: Optional[int] = Field(default=None, alias="statusCode")
    headers: Optional[dict[str, str]] = None
    body_size_bytes: Optional[int] = Field(default=None, alias="bodySizeBytes")


class AccessParticipant(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    aic: Optional[str] = None
    service_name: Optional[str] = Field(default=None, alias="serviceName")
    ip: Optional[str] = None


class AccessBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    duration_ms: Optional[float] = Field(default=None, alias="durationMs")
    request: Optional[AccessRequest] = None
    response: Optional[AccessResponse] = None
    caller: Optional[AccessParticipant] = None
    callee: Optional[AccessParticipant] = None
    error: Optional[ErrorInfo] = None


class LogRecord(BaseModel):
    """AMP 顶层日志记录（顶层字段 snake_case，与官方线格式一致）。"""

    schema_version: str = "1.0"
    timestamp: str
    aic: str
    log_type: str  # heartbeat / metrics / access / message / audit / system
    log_id: Optional[str] = None
    observed_timestamp: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    correlation_id: Optional[str] = None
    severity_text: Optional[str] = None
    severity_number: Optional[int] = None
    body: Optional[dict[str, Any]] = None
    resource: Optional[dict[str, Any]] = None
    attributes: Optional[dict[str, Any]] = None


# =============================================================================
# 序列化辅助
# =============================================================================


def to_wire(model: BaseModel) -> dict[str, Any]:
    """按官方约定序列化：camelCase alias、剔除 None、JSON 安全类型。"""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)
