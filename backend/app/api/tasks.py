"""Agent 任务接口（D4：异步执行 + SSE 实时进度）。

POST /api/tasks       提交自然语言需求，后台线程执行固定工作流，立即返回 202；
GET  /api/tasks/{id}  回查任务结果（轮询兜底）；
GET  /api/tasks/{id}/events  SSE 实时进度流。

任务存内存（TaskStore），后台线程用 stdlib threading 执行；事件用 stdlib queue
传递，零新增依赖，保证比赛现场稳定。下载 / 预览端点见 D4 阶段 3。
"""
import json
import queue
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from app.agent.context import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    TaskContext,
    task_store,
)
from app.agent.workflow import run_workflow
from app.services import preview as preview_service
from app.storage import file_store
from config import settings

router = APIRouter(prefix="/api", tags=["tasks"])


class TaskRequest(BaseModel):
    prompt: str


@router.post("/tasks", status_code=202)
def create_task(req: TaskRequest) -> dict:
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="任务描述不能为空，请输入你的需求")

    if not settings.DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="服务端未配置 DEEPSEEK_API_KEY，请联系管理员")

    ctx = task_store.create(prompt)
    ctx.status = STATUS_RUNNING
    response = ctx.to_dict()
    threading.Thread(target=run_workflow, args=(ctx,), daemon=True).start()
    return response


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    ctx = task_store.get(task_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    return ctx.to_dict()


def _sse(data: dict) -> str:
    """把事件字典序列化为一条 SSE data 帧。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _event_stream(ctx: TaskContext):
    """SSE 帧生成器：hello 全量快照 + 增量事件，终态后发 done 并结束。"""
    snap = ctx.to_dict()
    snap["type"] = "hello"
    yield _sse(snap)
    q = ctx.events
    while True:
        try:
            ev = q.get(timeout=15)
        except queue.Empty:
            yield ": ping\n\n"
            continue
        yield _sse(ev)
        if ev.get("type") == "status" and ev.get("status") in (STATUS_COMPLETED, STATUS_FAILED):
            yield _sse({"type": "done", "status": ev["status"]})
            break


@router.get("/tasks/{task_id}/events")
def task_events(task_id: str):
    ctx = task_store.get(task_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")

    return StreamingResponse(
        _event_stream(ctx),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/tasks/{task_id}/files/{file_name}")
def download_file(task_id: str, file_name: str, inline: bool = False):
    ctx = task_store.get(task_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    path = file_store.build_output_path(task_id, file_name)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"文件不存在：{file_name}")
    return FileResponse(
        path,
        filename=file_name,
        content_disposition_type="inline" if inline else "attachment",
    )


@router.get("/tasks/{task_id}/preview/{kind}")
def preview_task(task_id: str, kind: str):
    ctx = task_store.get(task_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")

    questions = ctx.params.get("questions")
    title = ctx.params.get("title", "")
    topic = ctx.params.get("topic") or ctx.params.get("chapter", "")

    if kind == "exam":
        if not questions:
            raise HTTPException(status_code=400, detail="该任务没有试卷数据")
        return HTMLResponse(preview_service.render_exam_preview(title, questions))
    if kind == "answer_sheet":
        if not questions:
            raise HTTPException(status_code=400, detail="该任务没有答题卡数据")
        return HTMLResponse(preview_service.render_answer_sheet_preview(title, questions))
    if kind == "lesson":
        content = ctx.params.get("lesson_content")
        if not content:
            raise HTTPException(status_code=400, detail="该任务没有教案数据")
        return HTMLResponse(preview_service.render_lesson_preview(topic, content))
    raise HTTPException(status_code=404, detail=f"未知预览类型：{kind}")
