"""Agent 任务接口。

POST /api/tasks 提交自然语言需求，同步执行固定工作流（Planner → Workflow →
Executor），返回任务结果（状态、步骤、产物）；GET /api/tasks/{id} 按任务 ID
回查结果。任务存内存（TaskStore），单进程同步执行，无线程/队列复杂度，
保证比赛现场稳定。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.agent.context import STATUS_COMPLETED, task_store
from app.agent.workflow import run_workflow
from config import settings

router = APIRouter(prefix="/api", tags=["tasks"])


class TaskRequest(BaseModel):
    prompt: str


@router.post("/tasks")
def create_task(req: TaskRequest) -> dict:
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="任务描述不能为空，请输入你的需求")

    if not settings.DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="服务端未配置 DEEPSEEK_API_KEY，请联系管理员")

    ctx = task_store.create(prompt)
    run_workflow(ctx)

    if ctx.status != STATUS_COMPLETED:
        raise HTTPException(status_code=422, detail=ctx.error or "任务执行失败")

    return ctx.to_dict()


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    ctx = task_store.get(task_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    return ctx.to_dict()
