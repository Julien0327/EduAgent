"""P2-7A: mTLS RPC Client factory for Agent-to-Agent calls.

Wraps acps_sdk.aip.aip_rpc_client.AipRpcClient with:
- mTLS ssl_context from local client cert/key
- AIC identity binding
- Configuration from backend.config.settings
"""
import ssl
import logging
from pathlib import Path
from typing import Optional

try:  # acps-sdk 为可选依赖（AIP 互联，默认关闭）；未安装时保持本模块可导入（Phase 0 降级修复）
    from acps_sdk.aip.aip_rpc_client import AipRpcClient
except ImportError:  # pragma: no cover —— 最小环境/容器未安装 SDK
    AipRpcClient = None  # type: ignore[assignment,misc]

from backend.config import settings
from backend.interop.config import AcpSettings

logger = logging.getLogger(__name__)


def _build_mtls_ssl_context(
    cert_path: str,
    key_path: str,
    ca_bundle: Optional[str] = None,
) -> ssl.SSLContext:
    """Build an SSLContext for mTLS client authentication."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    # Load our client certificate + private key
    ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
    # Load trust anchors: custom bundle first, then system defaults
    if ca_bundle and Path(ca_bundle).exists():
        ctx.load_verify_locations(cafile=ca_bundle)
        logger.info("Loaded custom CA bundle: %s", ca_bundle)
    ctx.load_default_certs()
    logger.info("mTLS SSLContext built: cert=%s ca_bundle=%s", cert_path, ca_bundle)
    return ctx


def create_rpc_client(
    partner_url: Optional[str] = None,
    expected_partner_aic: Optional[str] = None,
    leader_id: Optional[str] = None,
    ca_bundle: Optional[str] = None,
    use_mtls: bool = True,
) -> AipRpcClient:
    """Create a configured AipRpcClient instance with mTLS + identity binding.

    Args:
        partner_url: Override settings.ACP_PARTNER_URL
        expected_partner_aic: Override settings.ACP_EXPECTED_PARTNER_AIC
        leader_id: Override settings.ACP_LEADER_ID

    Returns:
        AipRpcClient ready for start_task / get_task / etc.

    Raises:
        ValueError: If required config is missing
        FileNotFoundError: If cert/key files don't exist
    """
    if AipRpcClient is None:
        raise RuntimeError(
            "官方 acps_sdk 未安装，无法创建 RPC 客户端（AIP 为可选能力）。"
            "如需 AIP 互联，请安装 acps-sdk==2.2.0（AtomGit 源/本地 wheel，非 PyPI）"
        )
    url = partner_url or settings.ACP_PARTNER_URL
    if not url:
        raise ValueError(
            "ACP_PARTNER_URL is not set. "
            "Set env var or pass partner_url explicitly."
        )

    partner_aic = expected_partner_aic or settings.ACP_EXPECTED_PARTNER_AIC
    lid = leader_id or settings.ACP_LEADER_ID

    # Resolve client cert/key: prefer explicit Settings env vars,
    # fallback to AcpSettings (ACP_CERT_PATH / ACP_KEY_PATH) for production.
    cert_path = Path(settings.ACP_CLIENT_CERT_PATH)
    key_path = Path(settings.ACP_CLIENT_KEY_PATH)
    _acp = AcpSettings()
    if not cert_path.exists() and _acp.cert_path and _acp.cert_path.exists():
        logger.info("Falling back to AcpSettings cert: %s", _acp.cert_path)
        cert_path = _acp.cert_path
    if not key_path.exists() and _acp.key_path and _acp.key_path.exists():
        logger.info("Falling back to AcpSettings key: %s", _acp.key_path)
        key_path = _acp.key_path

    if not cert_path.exists():
        raise FileNotFoundError(f"Client cert not found: {cert_path}")
    if not key_path.exists():
        raise FileNotFoundError(f"Client key not found: {key_path}")

    if use_mtls:
        ssl_ctx = _build_mtls_ssl_context(str(cert_path), str(key_path), ca_bundle=ca_bundle)
        client = AipRpcClient(
            partner_url=url,
            leader_id=lid,
            ssl_context=ssl_ctx,
            caller_service=settings.SERVICE_NAME,
            expected_partner_aic=partner_aic,
            identity_binding_enabled=bool(partner_aic),
        )
        logger.info(
            "AipRpcClient created (mTLS): partner=%s leader=%s identity_binding=%s",
            url, lid, bool(partner_aic),
        )
    else:
        # HTTP mode: no TLS, no identity binding (demo environment fallback)
        client = AipRpcClient(
            partner_url=url,
            leader_id=lid,
            ssl_context=None,
            caller_service=settings.SERVICE_NAME,
            expected_partner_aic=partner_aic,
            identity_binding_enabled=False,
        )
        logger.warning(
            "AipRpcClient created (HTTP demo降级): partner=%s leader=%s identity_binding=False",
            url, lid,
        )
    return client
