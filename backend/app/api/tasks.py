"""Agent 任务接口（D4：异步执行 + SSE 实时进度）。

POST /api/tasks       提交自然语言需求，后台线程执行固定工作流，立即返回 202；
GET  /api/tasks/{id}  回查任务结果（轮询兜底）；
GET  /api/tasks/{id}/events  SSE 实时进度流。

任务 SQLite 持久化（Phase 2 / S5，§7.3：TaskStore 写穿透）；带 session_id 的
任务走 F1 追问通道（§7.1：信息不足先追问，至多 2 轮，超限降级默认值执行）。
"""
import json
import queue
import threading

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from backend.app.agent import session_store
from backend.app.agent.context import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    TaskContext,
    task_store,
)
from backend.app.agent.planner import plan
from backend.app.agent.session_store import CLARIFY_PREFIX, MAX_CLARIFY_ROUNDS
from backend.app.agent.workflow import run_workflow
from backend.app.scope import detect_unsupported_scope
from backend.app.services import preview as preview_service
from backend.app.storage import file_store
from backend.config import settings
from backend.logging_config import get_logger

_log = get_logger("app.api.tasks")

router = APIRouter(prefix="/api", tags=["tasks"])


class TaskRequest(BaseModel):
    prompt: str
    session_id: str | None = None  # §7.1：任务归属会话（缺省=独立任务）


@router.post("/tasks", status_code=202)
def create_task(req: TaskRequest) -> dict:
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="任务描述不能为空，请输入你的需求")

    # 学段范围守卫（决策㉝）：初中/更高学段 → 200 + 提示，不创建任务、不调 LLM、不产生费用
    scope_notice = detect_unsupported_scope(prompt)
    if scope_notice:
        return JSONResponse(status_code=200, content={
            "scope_notice": scope_notice,
            "task_id": None,
            "session_id": req.session_id,
            "stage_supported": "primary",
        })

    if not settings.DEEPSEEK_API_KEY:
        raise HTTPException(status_code=500, detail="服务端未配置 DEEPSEEK_API_KEY，请联系管理员")

    # F1 追问通道（§7.1）：仅会话任务做预判；无会话任务保持现状不追问。
    # 预判失败不阻塞任务创建（降级直跑）。
    if req.session_id:
        try:
            history = session_store.context_window(req.session_id)
            p = plan(prompt, history=history)
            if p.need_clarification:
                if session_store.clarify_count(req.session_id) < MAX_CLARIFY_ROUNDS:
                    session_store.add_message(
                        req.session_id, "assistant", CLARIFY_PREFIX + p.need_clarification)
                    return JSONResponse(status_code=200, content={
                        "need_clarification": p.need_clarification,
                        "session_id": req.session_id,
                        "task_id": None,
                    })
                # 超限：降级默认值执行，在响应注明（任务照常创建）
                ctx = task_store.create(prompt, session_id=req.session_id)
                ctx.push_event({"type": "note",
                                "message": "信息不足追问超限，已按默认参数执行"})
                _log.info("F1 降级：会话 %s 追问超限，任务 %s 按默认参数执行",
                          req.session_id, ctx.task_id)
                resp = ctx.to_dict()
                resp["clarification_dropped"] = True
                threading.Thread(target=run_workflow, args=(ctx,), daemon=True).start()
                return JSONResponse(status_code=202, content=resp)
        except Exception:
            _log.exception("F1 预判失败（不阻塞任务创建）")

    ctx = task_store.create(prompt, session_id=req.session_id)
    ctx.status = STATUS_RUNNING
    _log.info("任务创建：%s prompt=%s", ctx.task_id, prompt[:80])
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


@router.get("/tasks/{task_id}/citations")
def get_task_citations(task_id: str) -> dict:
    """任务引用清单（§4.4）：读产物目录 citations.json（文件为事实源）。

    任务存在但尚未产生引用（如仍在执行/无取数）→ 返回空 citations；
    任务不存在 → 404。
    """
    ctx = task_store.get(task_id)
    if ctx is None:
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")
    path = file_store.build_output_path(task_id, "citations.json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"task_id": task_id, "citations": []}


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
