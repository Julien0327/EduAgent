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

    # DeepSeek（文本任务，OpenAI 兼容 API）
    DEEPSEEK_API_KEY: str | None = os.getenv("DEEPSEEK_API_KEY")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
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


settings = Settings()
