"""官方 acps_sdk 接入核验测试（真实 SDK 代码路径，非 Mock）。

三项「真实」核验（区别于 FakeLLM 离线单测）：
1. 官方 acps_sdk 可实际导入，且版本/关键符号正确。
2. /rpc 由官方 handle_rpc_request 处理（拦截 SDK 函数断言被调用，而非只看自身 mock）。
3. 现有 ACS 构建产物能通过官方 AgentCapabilitySpec 校验（acs_builder 与 SDK 模型一致）。
"""
import asyncio

import httpx
from fastapi import FastAPI

from interop.aip_server import create_aip_router
from interop.config import AcpSettings


def test_sdk_importable():
    from importlib.metadata import version as dist_version

    import acps_sdk
    from acps_sdk.acs.models import AgentCapabilitySpec
    from acps_sdk.aip.aip_base_model import Product, TaskResult, TaskState
    from acps_sdk.aip.aip_rpc_model import RpcResponse
    from acps_sdk.aip.aip_rpc_server import (
        CommandHandlers,
        TaskManager,
        add_aip_rpc_router,
        handle_rpc_request,
    )

    # 安装的发行版为 2.2.0；acps_sdk.__version__ 常量未随 wheel 同步（仍为 "2.0.0"）
    assert dist_version("acps-sdk") == "2.2.0"
    assert acps_sdk.__version__ == "2.0.0"
    assert all([
        AgentCapabilitySpec, TaskResult, TaskState, Product,
        CommandHandlers, TaskManager, add_aip_rpc_router, handle_rpc_request, RpcResponse,
    ])


def test_rpc_delegates_to_official_sdk(monkeypatch):
    """断言 /rpc 由官方 handle_rpc_request 处理（拦截 SDK 函数，非自写 mock）。"""
    import acps_sdk.aip.aip_rpc_server as sdk_server
    from acps_sdk.aip.aip_rpc_model import RpcResponse

    calls = {}

    async def fake_handle(request, handlers, *, local_aic=None, identity_binding_enabled=True, dispatch_request=None):
        calls["is_command_handlers"] = type(handlers).__name__ == "CommandHandlers"
        calls["on_start_set"] = getattr(handlers, "on_start", None) is not None
        calls["local_aic"] = local_aic
        calls["identity_binding_enabled"] = identity_binding_enabled
        return RpcResponse(id=None)

    monkeypatch.setattr(sdk_server, "handle_rpc_request", fake_handle)

    app = FastAPI()
    app.include_router(create_aip_router(AcpSettings()))

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/rpc", json={
                "jsonrpc": "2.0", "method": "rpc", "id": 1,
                "params": {"command": {
                    "type": "task-command", "id": "c", "sentAt": "t",
                    "senderRole": "leader", "senderId": "s",
                    "command": "get", "taskId": "x",
                }},
            })

    resp = asyncio.run(_run())
    assert resp.status_code == 200
    assert calls.get("is_command_handlers") is True
    assert calls.get("on_start_set") is True
    # v2.2.0：无 AIC / 无 mTLS 时身份绑定应自动关闭（local_aic=None、identity_binding=False）
    assert calls.get("identity_binding_enabled") is False
    assert calls.get("local_aic") is None


def test_rpc_passes_local_aic_when_identity_binding_resolved(monkeypatch):
    """identity_binding_resolved=True 时，local_aic 与 identity_binding_enabled 均正确传入。

    P2-3③ 修复：identity_binding_enabled=True 时必须同时传非空 local_aic，否则官方 SDK 在
    handle_rpc_request 内抛 ValueError("local_aic is required when identity_binding_enabled=True")。
    """
    import acps_sdk.aip.aip_rpc_server as sdk_server
    from acps_sdk.aip.aip_rpc_model import RpcResponse

    calls = {}

    async def fake_handle(request, handlers, *, local_aic=None, identity_binding_enabled=True, dispatch_request=None):
        calls["local_aic"] = local_aic
        calls["identity_binding_enabled"] = identity_binding_enabled
        return RpcResponse(id=None)

    monkeypatch.setattr(sdk_server, "handle_rpc_request", fake_handle)

    # 完整 mTLS 服务端场景：ACP_AIC + ACP_MTLS_SERVER + 三项证书路径 → identity_binding_resolved=True
    monkeypatch.setenv("ACP_AIC", "1.2.156.3088.1.BUPT.1LW3RI.PLJ4AG.CPXIZB.0559")
    monkeypatch.setenv("ACP_MTLS_SERVER", "true")
    monkeypatch.setenv("ACP_KEY_PATH", "agent-server-key.pem")
    monkeypatch.setenv("ACP_CERT_PATH", "agent-server-cert.pem")
    monkeypatch.setenv("ACP_TRUST_BUNDLE_PATH", "trust-bundle.pem")

    settings = AcpSettings()
    assert settings.identity_binding_resolved is True

    app = FastAPI()
    app.include_router(create_aip_router(settings))

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/rpc", json={
                "jsonrpc": "2.0", "method": "rpc", "id": 1,
                "params": {"command": {
                    "type": "task-command", "id": "c", "sentAt": "t",
                    "senderRole": "leader", "senderId": "s",
                    "command": "get", "taskId": "x",
                }},
            })

    resp = asyncio.run(_run())
    assert resp.status_code == 200
    assert calls["local_aic"] == settings.aic
    assert calls["identity_binding_enabled"] is True


def test_acs_validates_with_official_sdk():
    """现有 acs_builder 产物通过官方 AgentCapabilitySpec 校验（一致性证据）。"""
    from interop.acs_builder import build_acs, try_validate_with_sdk

    acs = build_acs(AcpSettings(), aic="1.2.156.3088.1.ABC.XYZ.12345.0000.ABCD")
    ok, msg = try_validate_with_sdk(acs)
    assert ok is True, msg
