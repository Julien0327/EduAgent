"""AIP mTLS 启动入口 + 对端证书中间件的「接线」验证（离线，不启动真实 TLS 服务）。

覆盖：
1. serve_mtls._build_server_ssl_context 的守卫与 SSLContext 构造（CERT_REQUIRED + serverAuth + trust-bundle）。
2. serve_mtls.main 的进程守卫（非 ACP_MTLS_SERVER 进程直接 SystemExit）。
3. serve_mtls 引用的是官方 AipPeerCertH11Protocol。
4. main.py 仅在 ACP_ENABLED=true 且 ACP_MTLS_SERVER=true 时挂载 AipPeerCertificateMiddleware；
   业务 8000（两者均未设）或仅 ACP_ENABLED=true 时不挂载，绝不强制 mTLS。
5. identity_binding_resolved 需 ACP_MTLS_SERVER=true 才为真（避免业务进程误开身份绑定）。
"""
import importlib
import ssl
from pathlib import Path
from types import SimpleNamespace

import pytest

import interop.serve_mtls as serve_mtls
from acps_sdk.aip.aip_peer_cert import (
    AipPeerCertH11Protocol,
    AipPeerCertificateMiddleware,
)


# ---------- _build_server_ssl_context ----------

class _FakeSSLContext:
    def __init__(self, protocol):
        self.protocol = protocol
        self.certfile = None
        self.keyfile = None
        self.cafile = None
        self.verify_mode = None
        self.minimum_version = None

    def load_cert_chain(self, *, certfile, keyfile):
        self.certfile = certfile
        self.keyfile = keyfile

    def load_verify_locations(self, *, cafile):
        self.cafile = cafile


def _settings(cert="agent-server-cert.pem", key="agent-server-key.pem", ca="trust-bundle.pem"):
    return SimpleNamespace(
        cert_path=Path(cert) if cert else None,
        key_path=Path(key) if key else None,
        trust_bundle_path=Path(ca) if ca else None,
    )


def test_build_ssl_context_requires_all_three_paths():
    with pytest.raises(SystemExit):
        serve_mtls._build_server_ssl_context(_settings(cert=None))
    with pytest.raises(SystemExit):
        serve_mtls._build_server_ssl_context(_settings(key=None))
    with pytest.raises(SystemExit):
        serve_mtls._build_server_ssl_context(_settings(ca=None))


def test_build_ssl_context_sets_cert_required_and_loads_chain(monkeypatch):
    fake = _FakeSSLContext(ssl.PROTOCOL_TLS_SERVER)
    monkeypatch.setattr(serve_mtls.ssl, "SSLContext", lambda *a, **k: fake)
    ctx = serve_mtls._build_server_ssl_context(_settings())
    assert ctx is fake
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
    assert ctx.certfile == "agent-server-cert.pem"   # serverAuth 作为服务端证书
    assert ctx.keyfile == "agent-server-key.pem"
    assert ctx.cafile == "trust-bundle.pem"           # trust-bundle 验证客户端证书


def test_main_guards_when_not_mtls_server(monkeypatch):
    monkeypatch.setattr(serve_mtls, "load_acp_settings",
                        lambda: SimpleNamespace(enabled=True, mtls_server_enabled=False))
    with pytest.raises(SystemExit):
        serve_mtls.main()

    monkeypatch.setattr(serve_mtls, "load_acp_settings",
                        lambda: SimpleNamespace(enabled=False, mtls_server_enabled=True))
    with pytest.raises(SystemExit):
        serve_mtls.main()


def test_serve_mtls_uses_official_peer_cert_protocol():
    assert serve_mtls.AipPeerCertH11Protocol is AipPeerCertH11Protocol


# ---------- main.py middleware 挂载门控 ----------

def _middleware_classes(app):
    return [m.cls for m in app.user_middleware]


def test_no_middleware_when_acp_disabled(monkeypatch):
    import main as main_mod
    monkeypatch.delenv("ACP_ENABLED", raising=False)
    monkeypatch.delenv("ACP_MTLS_SERVER", raising=False)
    importlib.reload(main_mod)
    assert AipPeerCertificateMiddleware not in _middleware_classes(main_mod.app)


def test_no_middleware_when_acp_enabled_without_mtls_server(monkeypatch):
    import main as main_mod
    monkeypatch.setenv("ACP_ENABLED", "true")
    monkeypatch.delenv("ACP_MTLS_SERVER", raising=False)
    importlib.reload(main_mod)
    assert AipPeerCertificateMiddleware not in _middleware_classes(main_mod.app)


def test_middleware_mounted_when_mtls_server_enabled(monkeypatch):
    import main as main_mod
    monkeypatch.setenv("ACP_ENABLED", "true")
    monkeypatch.setenv("ACP_MTLS_SERVER", "true")
    importlib.reload(main_mod)
    assert AipPeerCertificateMiddleware in _middleware_classes(main_mod.app)


# ---------- identity_binding_resolved 需 mtls_server ----------

def test_identity_binding_resolved_requires_mtls_server(monkeypatch):
    from interop.config import AcpSettings

    monkeypatch.setenv("ACP_AIC", "1.2.156.3088.1.BUPT.1LW3RI.PLJ4AG.CPXIZB.0559")
    monkeypatch.setenv("ACP_KEY_PATH", "agent-server-key.pem")
    monkeypatch.setenv("ACP_CERT_PATH", "agent-server-cert.pem")
    monkeypatch.setenv("ACP_TRUST_BUNDLE_PATH", "trust-bundle.pem")
    monkeypatch.delenv("ACP_MTLS_SERVER", raising=False)
    assert AcpSettings().identity_binding_resolved is False

    monkeypatch.setenv("ACP_MTLS_SERVER", "true")
    assert AcpSettings().identity_binding_resolved is True
