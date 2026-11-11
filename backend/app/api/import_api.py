"""F16 数据导入 API（Phase 1 / Step 3，§10.2 三端点）。

- GET  /api/import/template      统一导入模板下载（xlsx）
- POST /api/import               导入数据文件（body = xlsx 二进制；query: kind/force/file_name）
- GET  /api/import/{batch_id}    批次状态与错误报告查询

POST body 为 xlsx 原始二进制（application/octet-stream），不使用 multipart 表单
（避免引入 python-multipart 依赖）；kind 声明与文件实际包含的数据表由 importer
统一校验，不一致返回 400。

状态码：200 批次报告；400 文件不可导入/kind 不符；409 指纹重复(duplicate_fingerprint)
或并发冲突(import_busy)；404 批次不存在。
限流（10/min，§10.5）随 Phase 2 API 安全层统一落地，Phase 1 不提前实现。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from backend.app.storage import importer
from backend.app.storage.templates import build_import_template
from backend.config import settings

router = APIRouter(prefix="/api/import", tags=["import"])

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _db_path() -> str | None:
    return settings.DB_PATH


@router.get("/template")
def get_template() -> Response:
    """统一导入模板（五个数据工作表 + 填写说明）。"""
    return Response(
        content=build_import_template(),
        media_type=XLSX_MEDIA,
        headers={"Content-Disposition": 'attachment; filename="edumate_import_template.xlsx"'},
    )


@router.post("")
async def import_data(request: Request,
                      kind: str = Query("full"),
                      force: bool = Query(False),
                      file_name: str = Query("upload.xlsx")) -> dict:
    """导入数据文件，返回批次报告（状态/计数/错误明细）。

    kind: full/classes/students/exam/answers（须与文件实际包含的数据表一致）。
    force=true 覆盖重导仅支持 answers（只含答题记录表的文件）。
    """
    data = await request.body()
    if not data:
        raise HTTPException(400, "请求体为空：请上传 xlsx 文件二进制")
    try:
        report = importer.import_file(
            data, file_name, kind=kind, force=force, db_path=_db_path()
        )
    except importer.ImportFatalError as exc:
        raise HTTPException(400, str(exc)) from exc
    except importer.ImportConflictError as exc:
        raise HTTPException(409, detail={
            "reason": "duplicate_fingerprint",
            "message": str(exc),
            "original_batch": exc.original,
        }) from exc
    except importer.ImportBusyError as exc:
        raise HTTPException(409, detail={
            "reason": "import_busy",
            "message": str(exc),
            "rejected_batch": exc.report,
        }) from exc
    return report


@router.get("/{batch_id}")
def get_import_batch(batch_id: str) -> dict:
    """批次状态与错误报告查询。"""
    report = importer.get_batch(_db_path(), batch_id)
    if report is None:
        raise HTTPException(404, f"批次不存在：{batch_id}")
    return report
