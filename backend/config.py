"""集中读取环境变量与应用配置。

D1 阶段仅包含最小配置；后续 LLM 等配置项在对应阶段再补充。
"""
import os


class Settings:
    """应用配置（从环境变量读取，提供默认值）。"""

    SERVICE_NAME: str = os.getenv("SERVICE_NAME", "EduAgent")
    HOST: str = os.getenv("APP_HOST", "127.0.0.1")
    PORT: int = int(os.getenv("APP_PORT", "8000"))


settings = Settings()
