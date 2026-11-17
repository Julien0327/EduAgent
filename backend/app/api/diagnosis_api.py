"""班级诊断 API（Phase 3 / S3，§5/§10.2）。

- GET  /api/classes                    班级列表 + 计数
- GET  /api/diagnosis/class/{id}       确定性诊断 JSON（无 LLM）
- POST /api/diagnosis/class/{id}/report  202 + SSE 走 class_diagnosis 工作流
"""
import threading

from fastapi import APIRouter, HTTPException

from backend.app.agent.context import STATUS_RUNNING, task_store
from backend.app.agent.workflow import run_workflow
from backend.app.diagnosis import service as diagnosis_service
from backend.app.diagnosis.data_access import DiagnosisDataError
from backend.app.storage import db
from backend.logging_config import get_logger

_log = get_logger("app.api.diagnosis")

router = APIRouter(prefix="/api", tags=["diagnosis"])


@router.get("/classes")
def list_classes() -> list[dict]:
    conn = db.connect(None)
    try:
        rows = conn.execute(
            "SELECT c.class_id, c.name, c.grade, c.stage, c.school,"
            " (SELECT COUNT(*) FROM students s WHERE s.class_id=c.class_id) AS students,"
            " (SELECT COUNT(*) FROM exams e WHERE e.class_id=c.class_id) AS exams,"
            " (SELECT COUNT(*) FROM exam_questions eq JOIN exams e ON e.exam_id=eq.exam_id"
            "  WHERE e.class_id=c.class_id) AS questions,"
            " (SELECT COUNT(*) FROM student_answers sa JOIN exams e ON e.exam_id=sa.exam_id"
            "  WHERE e.class_id=c.class_id) AS answers"
            " FROM classes c ORDER BY c.created_at, c.class_id"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@router.get("/diagnosis/class/{class_id}")
def get_class_diagnosis(class_id: str) -> dict:
    conn = db.connect(None)
    try:
        return diagnosis_service.build_diagnosis(conn, class_id)
    except DiagnosisDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    finally:
        conn.close()


@router.post("/diagnosis/class/{class_id}/report", status_code=202)
def create_diagnosis_report(class_id: str) -> dict:
    conn = db.connect(None)
    try:
        exists = conn.execute(
            "SELECT 1 FROM classes WHERE class_id=?", (class_id,)).fetchone()
    finally:
        conn.close()
    if exists is None:
        raise HTTPException(status_code=404, detail=f"班级不存在：{class_id}")

    ctx = task_store.create(f"班级诊断：{class_id}")
    ctx.workflow = "class_diagnosis"
    ctx.params = {"class_id": class_id}
    ctx.status = STATUS_RUNNING
    _log.info("诊断任务创建：%s class_id=%s", ctx.task_id, class_id)
    threading.Thread(target=run_workflow, args=(ctx, True), daemon=True).start()
    return ctx.to_dict()
