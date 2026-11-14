"""会话接口（Phase 2 / S5，§10.2）：会话列表与回放。

GET /api/sessions        会话列表（id/标题/时间/消息数）
GET /api/sessions/{id}   会话消息 + 关联任务卡（前端会话切换回放）
"""
from fastapi import APIRouter, HTTPException

from backend.app.agent import session_store

router = APIRouter(prefix="/api", tags=["sessions"])


@router.get("/sessions")
def list_sessions() -> dict:
    return {"sessions": session_store.list_sessions()}


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    data = session_store.get_session(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"会话不存在：{session_id}")
    return data
