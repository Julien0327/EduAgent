"""ACP 部署配置读取（环境变量优先，均为可选）。

字段语义对齐官方 demo-partner 的 config.toml 与 acps-cli.toml，全部从环境变量读取，
避免把 AIC / 证书路径 / 私钥等敏感信息写进代码或提交仓库。

来源核实：
- acps-cli/acps-cli.toml（[registry]/[ca]/[discovery] base_url）
- demo-partner/partners/online/beijing_food/config.toml（[server.mtls]/[rabbitmq]）
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _path(name: str) -> Path | None:
    raw = os.getenv(name)
    return Path(raw) if raw else None


@dataclass(frozen=True)
class AcpSettings:
    """ACP 运行时配置（只读快照）。"""

    # 总开关：AIP 路由是否挂载到主应用
    enabled: bool = field(default_factory=lambda: _bool("ACP_ENABLED", False))

    # 身份
    aic: str | None = field(default_factory=lambda: os.getenv("ACP_AIC"))
    name: str = field(default_factory=lambda: os.getenv("ACP_NAME", "EduMate-AIP"))
    service_name: str = field(default_factory=lambda: os.getenv("SERVICE_NAME", "EduMate-AIP"))

    # 平台端点（对齐 acps-cli.toml）
    registry_base_url: str = field(
        default_factory=lambda: os.getenv("ACP_REGISTRY_BASE_URL", "https://wt.ioa.pub/registry-server")
    )
    discovery_base_url: str = field(
        default_factory=lambda: os.getenv("ACP_DISCOVERY_BASE_URL", "https://wt.ioa.pub/discovery")
    )
    ca_base_url: str = field(
        default_factory=lambda: os.getenv("ACP_CA_BASE_URL", "https://wt.ioa.pub/ca-server")
    )

    # mTLS 证书（由平台 CA 签发，见 acps-cli cert issue）
    key_path: Path | None = field(default_factory=lambda: _path("ACP_KEY_PATH"))
    cert_path: Path | None = field(default_factory=lambda: _path("ACP_CERT_PATH"))
    trust_bundle_path: Path | None = field(default_factory=lambda: _path("ACP_TRUST_BUNDLE_PATH"))
    identity_binding_enabled: bool = field(
        default_factory=lambda: _bool("ACP_IDENTITY_BINDING_ENABLED", True)
    )

    # RabbitMQ 消息面（群组模式，P1）
    mq_host: str = field(default_factory=lambda: os.getenv("ACP_MQ_HOST", "localhost"))
    mq_port: int = field(default_factory=lambda: int(os.getenv("ACP_MQ_PORT", "5671")))
    mq_vhost: str = field(default_factory=lambda: os.getenv("ACP_MQ_VHOST", "acps"))

    # AMP 日志：本地 JSON-lines 落盘路径（Kafka 上报在 P1 接入官方 emitter）
    amp_log_path: Path = field(
        default_factory=lambda: Path(os.getenv("ACP_AMP_LOG_PATH", "logs/amp.jsonl"))
    )

    # AIP 服务端点（用于生成 ACS endPoints 的 url）
    service_host: str = field(default_factory=lambda: os.getenv("ACP_SERVICE_HOST", "localhost"))
    service_port: int = field(default_factory=lambda: int(os.getenv("ACP_SERVICE_PORT", "9443")))

    @property
    def rpc_url(self) -> str:
        """AIP RPC 端点 URL（ACS endPoints 声明用）。"""
        scheme = "https" if self.cert_path else "http"
        return f"{scheme}://{self.service_host}:{self.service_port}/rpc"

    @property
    def has_mtls(self) -> bool:
        return bool(self.cert_path and self.key_path and self.trust_bundle_path)


def load_acp_settings() -> AcpSettings:
    """读取当前环境下的 ACP 配置快照。"""
    return AcpSettings()
