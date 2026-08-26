"""EduAgent 后端入口。

启动方式（在仓库根目录执行）：
    uvicorn main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import chat, health
from config import settings

app = FastAPI(title=settings.SERVICE_NAME)

# CORS：开发阶段放开跨域，便于前端访问后端；后续按需收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 业务路由（先注册，保证 /api/* 优先于静态资源匹配）
app.include_router(health.router)
app.include_router(chat.router)

# 前端静态资源（最后挂载，作为兜底；index.html 由 html=True 在 / 提供）
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
