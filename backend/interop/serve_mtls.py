"""AIP mTLS 专用 Uvicorn 启动入口（0.0.0.0:9443，Uvicorn 直连 TLS/mTLS）。

仅用于 edumate-aip.service；业务 edumate.service 仍走
`uvicorn main:app --host 127.0.0.1 --port 8000`，不进入本入口。

对齐官方 acps-sdk 2.2.0 的集成测试实现（tests/integration/_tls_test_utils.py）：
- http = AipPeerCertH11Protocol：从 TLS socket 提取对端证书写入 PeerCertificateRegistry，
  供 AipPeerCertificateMiddleware → get_request_peer_aic → identity binding 使用。
- ssl_context：serverAuth 证书 + 私钥 + trust-bundle（CERT_REQUIRED），由 Uvicorn 自身终止 mTLS；
  Nginx 不参与 9443。
- ws="none"、proxy_headers=False、lifespan="on"：与官方集成测试保持一致。
"""
from __future__ import annotations

import ssl

import uvicorn

from acps_sdk.aip.aip_peer_cert import AipPeerCertH11Protocol
from interop.config import AcpSettings, load_acp_settings


def _build_server_ssl_context(settings: AcpSettings) -> ssl.SSLContext:
    """构造服务端 mTLS SSLContext：serverAuth 证书 + trust-bundle 验证客户端证书。"""
    if not (settings.cert_path and settings.key_path and settings.trust_bundle_path):
        raise SystemExit(
            "ACP_MTLS_SERVER 需要 ACP_CERT_PATH / ACP_KEY_PATH / ACP_TRUST_BUNDLE_PATH 三项证书路径。"
        )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(
        certfile=str(settings.cert_path),
        keyfile=str(settings.key_path),
    )
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cafile=str(settings.trust_bundle_path))
    context.verify_mode = ssl.CERT_REQUIRED
    return context


def main() -> None:
    """AIP mTLS 进程入口：Uvicorn 直连 0.0.0.0:9443 并自终止 mTLS。"""
    settings = load_acp_settings()
    if not settings.enabled or not settings.mtls_server_enabled:
        raise SystemExit(
            "serve_mtls 仅用于 AIP mTLS 进程：请设置 ACP_ENABLED=true 且 ACP_MTLS_SERVER=true。"
        )

    from main import app  # 复用业务 FastAPI app（含 AIP /rpc 路由）

    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=settings.service_port,
        http=AipPeerCertH11Protocol,
        ws="none",
        log_level="info",
        proxy_headers=False,
        lifespan="on",
    )
    config.load()
    config.ssl = _build_server_ssl_context(settings)
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
