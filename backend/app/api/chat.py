"""对话接口：调用 DeepSeek 完成一次对话（Phase 2 / S5 起支持多轮会话）。

多轮机制（§7.1）：请求带 session_id（缺省新建会话）；消息全量落库，注入
上下文时取最近 6 轮、每轮截断 2000 字符（原文不受影响，§7.2）。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.app.agent import session_store
from backend.app.agent.llm_client import LLMError, get_llm_service
from backend.app.scope import detect_unsupported_scope
from backend.config import settings

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None  # §10.1：新增可选字段，向后兼容


class ChatResponse(BaseModel):
    message: str
    model: str
    session_id: str


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空，请输入教学内容或问题")

    # 学段范围守卫（决策㉝）：初中/更高学段 → 友好提示，不调 LLM、不产生费用
    scope_notice = detect_unsupported_scope(message)
    if scope_notice:
        session_id = req.session_id or session_store.create_session()
        session_store.add_message(session_id, "user", message)
        session_store.add_message(session_id, "assistant", scope_notice)
        return ChatResponse(message=scope_notice, model="scope-guard", session_id=session_id)

    if not settings.DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="服务端未配置 DEEPSEEK_API_KEY，请联系管理员")

    # 会话：缺省新建；用户消息原文全量落库
    session_id = req.session_id or session_store.create_session()
    session_store.add_message(session_id, "user", message)
    history = session_store.context_window(session_id)  # 最近 6 轮（截断版）

    try:
        result = get_llm_service().chat(message, history=history)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except TypeError:
        # 旧 LLM 服务无 history 参数（接口演进前的实现）——退回单轮
        result = get_llm_service().chat(message)
    except Exception:
        raise HTTPException(status_code=500, detail="模型调用失败，请稍后重试")

    session_store.add_message(session_id, "assistant", result["message"])
    return ChatResponse(message=result["message"], model=result["model"], session_id=session_id)
