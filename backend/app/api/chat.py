"""对话接口：调用 DeepSeek 完成一次对话。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent.llm_client import LLMError, get_llm_service
from config import settings

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    message: str
    model: str


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="消息不能为空，请输入教学内容或问题")

    if not settings.DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="服务端未配置 DEEPSEEK_API_KEY，请联系管理员")

    try:
        result = get_llm_service().chat(message)
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=500, detail="模型调用失败，请稍后重试")

    return ChatResponse(message=result["message"], model=result["model"])
