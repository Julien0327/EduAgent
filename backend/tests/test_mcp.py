"""MCP 能力开放测试（Phase 5 / S4）：工具 handler + 认证权限 + 协议级。

纯离线：检索/诊断服务层用 Fake；不调用真实 LLM/DeepSeek。
覆盖：三工具输入输出契约、非法参数、异常结构化错误、双 Token 权限矩阵、
敏感工具环境开关、工具发现与 Schema。
"""
from __future__ import annotations

import asyncio

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from backend.app.mcp import build_mcp_server
from backend.app.mcp import tools as mcp_tools
from backend.app.mcp.auth import (
    PermissionDeniedError,
    SensitiveDisabledError,
    classify_token,
    require_sensitive,
)
from backend.app.retrieval import service as retrieval_service
from backend.config import settings


# ===================== Fake 服务层 =====================
class FakeCtx:
    def __init__(self, headers):
        self.headers = headers


class FakeRetrieval:
    def search_knowledge_points(self, query, stage=None, top_k=5):
        return [{"kp_id": "kp_p_0001", "ref": "kp_p_0001", "name": "分数运算",
                 "stage": "primary", "grade": 6, "chapter": "分数运算",
                 "description": "分数加减乘除", "score": 0.9, "via": "both"}]

    def search_by_kp(self, kp_ids, filters=None, question_limit=20):
        return [{"q_id": "q_ape_1", "ref": "q_ape_1", "stem": "一道填空题",
                 "type": "fill", "difficulty": 2, "score": 5, "kp_ids": list(kp_ids)}]

    def search_questions(self, query, stage=None, filters=None, question_limit=20):
        return {"kps": [], "questions": [{"q_id": "q_ape_2", "ref": "q_ape_2",
                                          "stem": "一道选择题", "type": "choice",
                                          "difficulty": 1, "score": 3, "kp_ids": []}],
                "meta": {}}


class FakeConn:
    def close(self):
        pass


# ===================== 认证与权限 =====================
def test_classify_token(monkeypatch):
    monkeypatch.setattr(settings, "EDUMATE_API_TOKEN", "user-tok")
    monkeypatch.setattr(settings, "EDUMATE_MCP_ADMIN_TOKEN", "admin-tok")
    assert classify_token("admin-tok") == "admin"
    assert classify_token("user-tok") == "user"
    assert classify_token("bad") is None
    assert classify_token(None) is None


def test_require_sensitive_disabled(monkeypatch):
    monkeypatch.setattr(settings, "EDUMATE_MCP_ALLOW_SENSITIVE", False)
    with pytest.raises(SensitiveDisabledError):
        require_sensitive(None)


def test_require_sensitive_stdio_allowed(monkeypatch):
    monkeypatch.setattr(settings, "EDUMATE_MCP_ALLOW_SENSITIVE", True)
    require_sensitive(None)            # ctx=None（直接调用）视为受信
    require_sensitive(FakeCtx(None))   # headers=None（stdio）→ 本地受信


def test_require_sensitive_remote_denies_user(monkeypatch):
    monkeypatch.setattr(settings, "EDUMATE_MCP_ALLOW_SENSITIVE", True)
    monkeypatch.setattr(settings, "EDUMATE_API_TOKEN", "user-tok")
    monkeypatch.setattr(settings, "EDUMATE_MCP_ADMIN_TOKEN", "admin-tok")
    with pytest.raises(PermissionDeniedError):
        require_sensitive(FakeCtx({"authorization": "Bearer user-tok"}))


def test_require_sensitive_remote_allows_admin(monkeypatch):
    monkeypatch.setattr(settings, "EDUMATE_MCP_ALLOW_SENSITIVE", True)
    monkeypatch.setattr(settings, "EDUMATE_MCP_ADMIN_TOKEN", "admin-tok")
    require_sensitive(FakeCtx({"authorization": "Bearer admin-tok"}))  # 不抛


# ===================== 工具 handler =====================
def test_search_knowledge_points_contract(monkeypatch):
    monkeypatch.setattr(retrieval_service, "get_retrieval_service", lambda: FakeRetrieval())
    out = mcp_tools.search_knowledge_points("分数")
    assert out["citations"] == ["kp_p_0001"]
    assert out["points"][0] == {"kp_id": "kp_p_0001", "name": "分数运算",
                                "stage": "primary", "grade": 6,
                                "chapter": "分数运算", "score": 0.9}


def test_search_knowledge_points_invalid(monkeypatch):
    with pytest.raises(ToolError, match="query 不能为空"):
        mcp_tools.search_knowledge_points("")
    with pytest.raises(ToolError, match="stage 非法"):
        mcp_tools.search_knowledge_points("分数", stage="senior")
    with pytest.raises(ToolError, match="top_k"):
        mcp_tools.search_knowledge_points("分数", top_k=99)


def test_query_question_bank_by_kp(monkeypatch):
    monkeypatch.setattr(retrieval_service, "get_retrieval_service", lambda: FakeRetrieval())
    out = mcp_tools.query_question_bank(kp_ids=["kp_p_0001"], count=5)
    assert out["total"] == 1
    assert out["questions"][0]["q_id"] == "q_ape_1"


def test_query_question_bank_by_chapter(monkeypatch):
    monkeypatch.setattr(retrieval_service, "get_retrieval_service", lambda: FakeRetrieval())
    out = mcp_tools.query_question_bank(chapter="分数", count=5)
    assert out["questions"][0]["q_id"] == "q_ape_2"


def test_query_question_bank_invalid(monkeypatch):
    with pytest.raises(ToolError, match="至少提供"):
        mcp_tools.query_question_bank()
    with pytest.raises(ToolError, match="difficulty"):
        mcp_tools.query_question_bank(kp_ids=["x"], difficulty=9)
    with pytest.raises(ToolError, match="q_type"):
        mcp_tools.query_question_bank(kp_ids=["x"], q_type="bad")
    with pytest.raises(ToolError, match="count"):
        mcp_tools.query_question_bank(kp_ids=["x"], count=999)


def test_diagnose_learning_gaps_contract(monkeypatch):
    monkeypatch.setattr(settings, "EDUMATE_MCP_ALLOW_SENSITIVE", True)
    monkeypatch.setattr(settings, "EDUMATE_API_TOKEN", "user-tok")
    monkeypatch.setattr(settings, "EDUMATE_MCP_ADMIN_TOKEN", "admin-tok")

    import backend.app.diagnosis.service as ds
    import backend.app.diagnosis.data_access as da
    import backend.app.storage.db as dbmod

    monkeypatch.setattr(dbmod, "connect", lambda path=None: FakeConn())
    monkeypatch.setattr(da, "find_class",
                        lambda conn, class_name=None, class_id=None: {"class_id": "cls_x", "name": "六（1）班"})
    monkeypatch.setattr(ds, "build_diagnosis", lambda conn, class_id: {
        "chains": [{"root_name": "乘除运算", "path_names": ["乘除运算", "分数运算"], "confidence": "high"}],
        "data_scope": {"class_id": "cls_x", "class_name": "六（1）班",
                       "student_count": 20, "answer_count": 980},
    })

    out = mcp_tools.diagnose_learning_gaps(class_name="六（1）班",
                                           ctx=FakeCtx({"authorization": "Bearer admin-tok"}))
    assert out["gaps"][0]["root_kp"] == "乘除运算"
    assert out["gaps"][0]["confidence"] == "high"
    assert out["insufficient"] is None
    assert out["scope"]["student_count"] == 20          # 最小必要：计数保留
    assert out["scope"]["class_name"] != "六（1）班"      # 班级名脱敏


def test_diagnose_learning_gaps_no_locator(monkeypatch):
    monkeypatch.setattr(settings, "EDUMATE_MCP_ALLOW_SENSITIVE", True)
    with pytest.raises(ToolError, match="恰好一个"):
        mcp_tools.diagnose_learning_gaps(ctx=FakeCtx(None))


def test_diagnose_learning_gaps_exactly_one_param(monkeypatch):
    """三选一必须严格恰好一个：0 个 / 2 个 / 3 个均拒绝；空白视为未提供。"""
    monkeypatch.setattr(settings, "EDUMATE_MCP_ALLOW_SENSITIVE", True)
    ctx = FakeCtx(None)
    # 零参数
    with pytest.raises(ToolError, match="恰好一个"):
        mcp_tools.diagnose_learning_gaps(ctx=ctx)
    # 双参数
    with pytest.raises(ToolError, match="恰好一个"):
        mcp_tools.diagnose_learning_gaps(exam_id="exam_1", class_name="六（1）班", ctx=ctx)
    # 三参数
    with pytest.raises(ToolError, match="恰好一个"):
        mcp_tools.diagnose_learning_gaps(exam_id="exam_1", class_name="六（1）班",
                                         student_no="P01", ctx=ctx)
    # 空白字符串视同未提供（再叠加一个有效 → 仍是一个）
    with pytest.raises(ToolError, match="恰好一个"):
        mcp_tools.diagnose_learning_gaps(exam_id="  ", class_name="", student_no="  ", ctx=ctx)
    # 空白 + 有效 = 恰好一个 → 进入定位（无数据时抛定位错误而非参数错误）
    import backend.app.storage.db as dbmod

    class _C:
        def close(self):
            pass

    monkeypatch.setattr(dbmod, "connect", lambda path=None: _C())
    import backend.app.diagnosis.data_access as da
    monkeypatch.setattr(da, "find_class", lambda conn, class_name=None, class_id=None: None)
    with pytest.raises(ToolError, match="无法定位班级"):
        mcp_tools.diagnose_learning_gaps(exam_id="  ", class_name="六（1）班", ctx=ctx)


# ===================== 协议级：工具发现 + Schema + call_tool =====================
def test_list_tools_schema():
    server = build_mcp_server()
    tools_list = asyncio.run(server.list_tools())
    names = [t.name for t in tools_list]
    assert names == ["search_knowledge_points", "query_question_bank", "diagnose_learning_gaps"]
    schemas = {t.name: t.input_schema for t in tools_list}
    assert schemas["search_knowledge_points"]["required"] == ["query"]
    assert "ctx" not in schemas["search_knowledge_points"].get("properties", {})
    assert schemas["query_question_bank"]["properties"]["count"]["default"] == 5
    # 敏感工具：三选一定位参数均为可选
    d = schemas["diagnose_learning_gaps"]
    assert "exam_id" in d["properties"] and "class_name" in d["properties"]


def test_call_tool_returns_json(monkeypatch):
    monkeypatch.setattr(retrieval_service, "get_retrieval_service", lambda: FakeRetrieval())
    server = build_mcp_server()
    res = asyncio.run(server.call_tool("search_knowledge_points", {"query": "分数"}))
    assert res.is_error is False
    text = res.content[0].text
    import json
    data = json.loads(text)
    assert data["citations"] == ["kp_p_0001"]


def test_call_tool_invalid_param_is_error(monkeypatch):
    server = build_mcp_server()
    with pytest.raises(ToolError) as exc:
        asyncio.run(server.call_tool("search_knowledge_points", {"query": ""}))
    assert "query 不能为空" in str(exc.value)
