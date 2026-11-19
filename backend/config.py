"""集中读取环境变量与应用配置。
D1-2 阶段：接入 DeepSeek（文本任务）。
Qwen（多模态任务）在后续阶段启用。
"""
import os


class Settings:
    """应用配置（从环境变量读取，提供默认值）。"""

    # 应用
    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "EduAgent")
    HOST: str = os.getenv("APP_HOST", "127.0.0.1")
    PORT: int = int(os.getenv("APP_PORT", "8000"))

    # CORS 白名单（Phase 0/I3）：逗号分隔的允许跨域来源；默认仅本机开发源。
    ALLOWED_ORIGINS: list[str] = [
        o.strip()
        for o in os.getenv(
            "ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
        ).split(",")
        if o.strip()
    ]

    # SQLite 业务库（Phase 1）：默认 data/db/edumate.sqlite；可用环境变量覆盖（测试/部署）。
    DB_PATH: str | None = os.getenv("EDUMATE_DB_PATH")

    # API 认证（Phase 2 / S6，§10.5 / 决策⑪）：env 注入 Access Token；
    # 未配置 → fail-closed（仅 /api/health 可用）。不硬编码、不入库、不进日志。
    EDUMATE_API_TOKEN: str | None = os.getenv("EDUMATE_API_TOKEN")

    # F16 导入（Phase 1 / Step 3）：单批数据行数上限（§3.4 IMPORT_BATCH_MAX_ROWS）。
    IMPORT_BATCH_MAX_ROWS: int = int(os.getenv("EDUMATE_IMPORT_MAX_ROWS", "5000"))

    # DeepSeek（文本任务，OpenAI 兼容 API）
    DEEPSEEK_API_KEY: str | None = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    # 决策④（2026-11-14）：显式 deepseek-flash（官方定价页现行模型 DeepSeek-V4.1-Flash-1017）；
    # deepseek-v4-flash 为已迁移遗留别名，弃用。
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    LLM_TIMEOUT: float = float(os.getenv("LLM_TIMEOUT", "60"))

    # 说明：Qwen（多模态任务）的 Key 现存于环境变量 LLM_API_KEY，后续阶段接入。

    # === P2-7A: Agent-to-Agent RPC Client (mTLS) ===
    ACP_CLIENT_CERT_PATH: str = os.getenv("ACP_CLIENT_CERT_PATH", "test-client/agent-client-cert.pem")
    ACP_CLIENT_KEY_PATH: str = os.getenv("ACP_CLIENT_KEY_PATH", "test-client/agent-client-key.pem")
    # 目标外部 Agent RPC endpoint (https://host:port/rpc)
    ACP_PARTNER_URL: str = os.getenv("ACP_PARTNER_URL", "")
    # 本方 Leader Agent ID (AIC identity)
    ACP_LEADER_ID: str = os.getenv("ACP_LEADER_ID", "1.2.156.3088.1.BUPT.1LW3RI.PLJ4AG.CPXIZB.0559")
    # 对方 Agent 的 AIC (用于 identity binding 验证)
    ACP_EXPECTED_PARTNER_AIC: str = os.getenv("ACP_EXPECTED_PARTNER_AIC", "")

    # === MCP 能力开放（Phase 5 / S4，§8.3 / 决策㊾③） ===
    # 管理员 Token：远程 Streamable HTTP 下调用敏感工具（diagnose_learning_gaps）需持有；
    # 未配置 → 管理员权限不可用（敏感工具在远程只能被拒绝）。不入库、不进日志。
    EDUMATE_MCP_ADMIN_TOKEN: str | None = os.getenv("EDUMATE_MCP_ADMIN_TOKEN")
    # 敏感工具环境总开关：默认关闭；开=管理员(远程)/本地(stdio)可调敏感工具。
    EDUMATE_MCP_ALLOW_SENSITIVE: bool = os.getenv(
        "EDUMATE_MCP_ALLOW_SENSITIVE", "0") in ("1", "true", "True")
    # DNS Rebinding 防护（SDK Host/Origin 白名单，逗号分隔）。默认值覆盖本地开发
    # 与正式域名 edumateaip.xyz（含 nginx 反向代理场景）；部署时可用环境变量覆盖。
    # 端口通配语法：`127.0.0.1:*` 匹配任意端口。
    EDUMATE_MCP_ALLOWED_HOSTS: list[str] = [
        h.strip() for h in os.getenv(
            "EDUMATE_MCP_ALLOWED_HOSTS",
            "127.0.0.1:*,localhost:*,[::1]:*,edumateaip.xyz,edumateaip.xyz:*",
        ).split(",") if h.strip()
    ]
    EDUMATE_MCP_ALLOWED_ORIGINS: list[str] = [
        o.strip() for o in os.getenv(
            "EDUMATE_MCP_ALLOWED_ORIGINS",
            "http://127.0.0.1:*,http://localhost:*,http://[::1]:*,"
            "https://edumateaip.xyz,http://edumateaip.xyz",
        ).split(",") if o.strip()
    ]


settings = Settings()
