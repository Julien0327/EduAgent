"""MCP 三个工具 handler（Phase 5 / S4，§8.3）。

全部复用现有服务层，handler 只做参数适配与输出契约裁剪：
- search_knowledge_points → retrieval.service.search_knowledge_points（阶段 1）
- query_question_bank     → retrieval.service.search_by_kp / search_questions（阶段 2/3）
- diagnose_learning_gaps  → diagnosis.service.build_diagnosis（敏感工具）

错误语义：非法参数/权限拒绝/数据不足 → 抛 ``ToolError``（SDK 转为 isError 结果，
错误文本保留到客户端）；不吞异常、不伪装成功。
"""
from __future__ import annotations

import logging

from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError

from backend.app.mcp.auth import require_sensitive
from backend.app.retrieval.question_search import VALID_QUESTION_TYPES
from backend.app.api.security import mask_student_name

log = logging.getLogger("app.mcp.tools")


# ===================== 工具 1：知识点检索（公开） =====================
def search_knowledge_points(query: str, stage: str | None = None,
                            top_k: int = 5, ctx: Context = None) -> dict:
    """检索小学数学知识点（阶段 1 双路召回，复用检索服务层）。"""
    if not query or not str(query).strip():
        raise ToolError("query 不能为空")
    if stage is not None and stage not in ("primary", "junior"):
        raise ToolError("stage 非法：应为 primary 或 junior")
    if not 1 <= top_k <= 20:
        raise ToolError("top_k 超出 1–20")

    from backend.app.retrieval.service import get_retrieval_service
    kps = get_retrieval_service().search_knowledge_points(str(query).strip(),
                                                          stage=stage, top_k=top_k)
    points = [{"kp_id": k["kp_id"], "name": k["name"], "stage": k.get("stage"),
               "grade": k.get("grade"), "chapter": k.get("chapter"),
               "score": k.get("score")} for k in kps]
    return {"points": points, "citations": [k["kp_id"] for k in kps]}


# ===================== 工具 2：题库问数（公开） =====================
def query_question_bank(kp_ids: list[str] | None = None, chapter: str | None = None,
                        difficulty: int | None = None, q_type: str | None = None,
                        count: int = 5, ctx: Context = None) -> dict:
    """按知识点/章节/难度/题型查询题库题目（阶段 2/3，复用检索服务层）。"""
    if not kp_ids and not (chapter and str(chapter).strip()):
        raise ToolError("至少提供 kp_ids 或 chapter 之一")
    if difficulty is not None and not 1 <= difficulty <= 5:
        raise ToolError("difficulty 应为 1–5")
    if q_type is not None and q_type not in VALID_QUESTION_TYPES:
        raise ToolError(f"q_type 非法：应为 {'/'.join(sorted(VALID_QUESTION_TYPES))}")
    if not 1 <= count <= 50:
        raise ToolError("count 应为 1–50")

    filters: dict = {}
    if difficulty is not None:
        filters["difficulty"] = difficulty
    if q_type is not None:
        filters["question_type"] = q_type

    from backend.app.retrieval.service import get_retrieval_service
    svc = get_retrieval_service()
    if kp_ids:
        questions = svc.search_by_kp(list(kp_ids), filters=filters, question_limit=count)
    else:
        result = svc.search_questions(str(chapter).strip(), stage=None,
                                      filters=filters, question_limit=count)
        questions = result.get("questions", [])
    questions = questions[:count]
    out = [{"q_id": q["q_id"], "stem": q["stem"], "type": q["type"],
            "difficulty": q.get("difficulty"), "score": q.get("score")}
           for q in questions]
    return {"total": len(out), "truncated": bool(questions) and len(questions) >= count,
            "questions": out}


# ===================== 工具 3：学情归因诊断（敏感） =====================
def _locate_class(conn, exam_id: str | None, class_name: str | None,
                  student_no: str | None) -> str | None:
    if class_name:
        from backend.app.diagnosis import data_access
        row = data_access.find_class(conn, class_name=str(class_name).strip())
        return row["class_id"] if row else None
    if exam_id:
        row = conn.execute("SELECT class_id FROM exams WHERE exam_id=?",
                           (str(exam_id).strip(),)).fetchone()
        return row["class_id"] if row and row["class_id"] else None
    if student_no:
        row = conn.execute("SELECT class_id FROM students WHERE student_no=? "
                           "ORDER BY created_at LIMIT 1", (str(student_no).strip(),)).fetchone()
        return row["class_id"] if row else None
    return None


def diagnose_learning_gaps(exam_id: str | None = None, class_name: str | None = None,
                           student_no: str | None = None, ctx: Context = None) -> dict:
    """班级学情归因诊断（敏感工具：需管理员 Token + 环境总开关）。"""
    require_sensitive(ctx)
    locators = [x for x in (exam_id, class_name, student_no) if x and str(x).strip()]
    if len(locators) != 1:
        raise ToolError(
            "exam_id / class_name / student_no 必须且只能提供一个（恰好一个），"
            f"实际提供 {len(locators)} 个")

    from backend.app.diagnosis import service as diag_service
    from backend.app.storage import db

    conn = db.connect(None)
    try:
        class_id = _locate_class(conn, exam_id, class_name, student_no)
        if class_id is None:
            raise ToolError("无法定位班级：未找到匹配的考试/班级/学号数据")
        diag = diag_service.build_diagnosis(conn, class_id)
    finally:
        conn.close()

    chains = diag.get("chains", [])
    gaps = [{"root_kp": c.get("root_name") or c.get("root_kp"),
             "path": c.get("path_names") or c.get("path", []),
             "confidence": c.get("confidence")} for c in chains]

    scope = diag.get("data_scope", {})
    # 最小必要：不返回学生姓名/学号明细，班级名脱敏
    scope_safe = {k: v for k, v in scope.items() if k not in ("class_name",)}
    if scope.get("class_name"):
        scope_safe["class_name"] = mask_student_name(scope["class_name"])

    # 访问审计：只记 class_id 与定位方式，不记姓名/学号/完整 Token
    log.info("MCP 敏感工具 diagnose_learning_gaps：class_id=%s gaps=%d",
             class_id, len(gaps))

    return {
        "scope": scope_safe,
        "gaps": gaps,
        "insufficient": None if chains else "数据不足或未覆盖知识点，无法给出归因结论",
    }
