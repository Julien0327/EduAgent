"""健康检查接口。"""
from fastapi import APIRouter

from config import settings

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health_check() -> dict:
    """返回服务健康状态。"""
    return {"status": "ok", "service": settings.SERVICE_NAME}
