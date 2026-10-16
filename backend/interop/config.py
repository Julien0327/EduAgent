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


def _optional_url(name: str) -> str | None:
    """读取可选 URL 配置：空值返回 None，非空去除尾部斜杠。"""
    raw = os.getenv(name)
    return raw.rstrip("/") if raw else None


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

    # 本进程是否为 AIP mTLS 服务端（0.0.0.0:9443，Uvicorn 直连 TLS）。
    # 仅该进程挂载 AipPeerCertificateMiddleware 并启用身份绑定；业务 8000 保持纯 HTTP。
    mtls_server_enabled: bool = field(
        default_factory=lambda: _bool("ACP_MTLS_SERVER", False)
    )

    # RabbitMQ 消息面（群组模式，P1）
    mq_host: str = field(default_factory=lambda: os.getenv("ACP_MQ_HOST", "localhost"))
    mq_port: int = field(default_factory=lambda: int(os.getenv("ACP_MQ_PORT", "5671")))
    mq_vhost: str = field(default_factory=lambda: os.getenv("ACP_MQ_VHOST", "acps"))

    # AMP 日志：本地 JSON-lines 落盘路径（Kafka 上报在 P1 接入官方 emitter）
    amp_log_path: Path = field(
        default_factory=lambda: Path(os.getenv("ACP_AMP_LOG_PATH", "logs/amp.jsonl"))
    )

    # 公开基址（可选）：远程调用方访问本服务的 scheme://host[:port]。
    # 部署时设 ACP_PUBLIC_BASE_URL=https://<公网域名或IP>:<端口>；
    # 未设置时回退 service_host:service_port（AIP 服务自身地址，非公网可达，仅本地/内网）。
    public_base_url: str | None = field(
        default_factory=lambda: _optional_url("ACP_PUBLIC_BASE_URL")
    )

    # AIP 服务端点（用于生成 ACS endPoints 的 url）
    service_host: str = field(default_factory=lambda: os.getenv("ACP_SERVICE_HOST", "localhost"))
    service_port: int = field(default_factory=lambda: int(os.getenv("ACP_SERVICE_PORT", "9443")))

    @property
    def service_base_url(self) -> str:
        """服务公开基址 scheme://host[:port]（无尾斜杠）。

        /rpc 与产物下载 /api/tasks/... 等端点均从此基址拼接。
        优先用 ACP_PUBLIC_BASE_URL；未设置时回退 service_host:service_port（非公网）。
        """
        if self.public_base_url:
            return self.public_base_url.rstrip("/")
        scheme = "https" if self.cert_path else "http"
        return f"{scheme}://{self.service_host}:{self.service_port}"

    @property
    def rpc_url(self) -> str:
        """AIP RPC 端点 URL（ACS endPoints 声明用）。"""
        return f"{self.service_base_url}/rpc"

    @property
    def has_mtls(self) -> bool:
        return bool(self.cert_path and self.key_path and self.trust_bundle_path)

    @property
    def identity_binding_resolved(self) -> bool:
        """实际生效的身份绑定开关（对齐官方 demo-partner 的 resolve_identity_binding_enabled）。

        身份绑定依赖 mTLS 客户端证书提取对端 AIC（get_request_peer_aic），
        缺任一前提（未开启 / 无本地 AIC / 无 mTLS 证书）都只能关闭，否则
        handle_rpc_request 会因 local_aic=None 或 peer AIC 缺失而报错。
        """
        return (
            self.identity_binding_enabled
            and self.mtls_server_enabled
            and bool(self.aic)
            and self.has_mtls
        )


def load_acp_settings() -> AcpSettings:
    """读取当前环境下的 ACP 配置快照。"""
    return AcpSettings()
