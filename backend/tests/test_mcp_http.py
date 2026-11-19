"""MCP Streamable HTTP + stdio 传输测试（Phase 5 / S4，协议级）。

覆盖：认证（无/错 Token 401）、双 Token 权限矩阵、敏感工具环境开关、
限流规则、非法参数、异常结构化错误、stdio 握手与 stdout 输出污染。
纯离线，不调用真实 LLM/DeepSeek。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from backend.app.api.security import SecurityMiddleware, classify_limit
from backend.app.mcp import build_mcp_server
from backend.app.mcp.server import mcp_transport_security
from backend.app.retrieval import service as retrieval_service
from backend.config import settings

REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeRetrieval:
    def search_knowledge_points(self, query, stage=None, top_k=5):
        return [{"kp_id": "kp_p_0001", "ref": "kp_p_0001", "name": "分数运算",
                 "stage": "primary", "grade": 6, "chapter": "分数运算",
                 "description": "", "score": 0.9, "via": "both"}]

    def search_by_kp(self, kp_ids, filters=None, question_limit=20):
        return [{"q_id": "q_ape_1", "ref": "q_ape_1", "stem": "题", "type": "fill",
                 "difficulty": 2, "score": 5, "kp_ids": list(kp_ids)}]

    def search_questions(self, query, stage=None, filters=None, question_limit=20):
        return {"kps": [], "questions": [], "meta": {}}


def _build_server() -> tuple[MCPServer, object]:
    server = build_mcp_server()
    # 使用生产同款 DNS Rebinding 防护（Host/Origin 白名单）
    sub = server.streamable_http_app(streamable_http_path="/",
                                     transport_security=mcp_transport_security(),
                                     json_response=True)
    return server, sub


@pytest.fixture
def mcp_client(monkeypatch):
    monkeypatch.setattr(settings, "EDUMATE_API_TOKEN", "user-tok")
    monkeypatch.setattr(settings, "EDUMATE_MCP_ADMIN_TOKEN", "admin-tok")
    monkeypatch.setattr(settings, "EDUMATE_MCP_ALLOW_SENSITIVE", False)
    monkeypatch.setattr(retrieval_service, "get_retrieval_service", lambda: FakeRetrieval())

    server, sub = _build_server()
    sm = server.session_manager

    @asynccontextmanager
    async def lifespan(app):
        async with sm.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(SecurityMiddleware)
    app.mount("/mcp", sub)

    # Host 用白名单内的回环地址（DNS Rebinding 防护白名单默认含 127.0.0.1:*）
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        yield client


_HDR = {"Accept": "application/json", "Content-Type": "application/json"}


def _init_body():
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "1"}}}


def _handshake(client, token):
    hdr = dict(_HDR)
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    r = client.post("/mcp/", json=_init_body(), headers=hdr)
    return r, r.headers.get("mcp-session-id", "")


def _call(client, sid, method, params, token):
    hdr = dict(_HDR)
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    if sid:
        hdr["mcp-session-id"] = sid
    return client.post("/mcp/", json={"jsonrpc": "2.0", "id": 2, "method": method,
                                      "params": params}, headers=hdr)


def _result(r):
    return r.json().get("result", {})


# ===================== 认证 =====================
def test_mcp_no_token_401(mcp_client):
    r = mcp_client.post("/mcp/", json=_init_body(), headers=_HDR)
    assert r.status_code == 401


def test_mcp_bad_token_401(mcp_client):
    hdr = {**_HDR, "Authorization": "Bearer wrong-tok"}
    r = mcp_client.post("/mcp/", json=_init_body(), headers=hdr)
    assert r.status_code == 401


def test_mcp_handshake_with_user_token(mcp_client):
    r, sid = _handshake(mcp_client, "user-tok")
    assert r.status_code == 200
    assert "serverInfo" in r.json()["result"]


# ===================== 权限矩阵 =====================
def test_mcp_user_calls_public_tool(mcp_client):
    _, sid = _handshake(mcp_client, "user-tok")
    r = _call(mcp_client, sid, "tools/list", {}, "user-tok")
    tools = [t["name"] for t in _result(r)["tools"]]
    assert "search_knowledge_points" in tools

    r2 = _call(mcp_client, sid, "tools/call",
               {"name": "search_knowledge_points", "arguments": {"query": "分数"}}, "user-tok")
    assert _result(r2).get("isError") is not True


def test_mcp_user_sensitive_denied(mcp_client, monkeypatch):
    monkeypatch.setattr(settings, "EDUMATE_MCP_ALLOW_SENSITIVE", True)
    _, sid = _handshake(mcp_client, "user-tok")
    r = _call(mcp_client, sid, "tools/call",
              {"name": "diagnose_learning_gaps", "arguments": {"class_name": "x"}}, "user-tok")
    res = _result(r)
    assert res.get("isError") is True
    assert "管理员" in str(res.get("content", ""))


def test_mcp_admin_sensitive_disabled(mcp_client):
    # 开关默认关（fixture 设 False）→ 管理员也被拒绝
    _, sid = _handshake(mcp_client, "admin-tok")
    r = _call(mcp_client, sid, "tools/call",
              {"name": "diagnose_learning_gaps", "arguments": {"class_name": "x"}}, "admin-tok")
    res = _result(r)
    assert res.get("isError") is True
    assert "未开启" in str(res.get("content", ""))


def test_mcp_admin_sensitive_allowed(mcp_client, monkeypatch):
    monkeypatch.setattr(settings, "EDUMATE_MCP_ALLOW_SENSITIVE", True)
    import backend.app.diagnosis.service as ds
    import backend.app.diagnosis.data_access as da
    import backend.app.storage.db as dbmod

    class C:
        def close(self):
            pass

    monkeypatch.setattr(dbmod, "connect", lambda path=None: C())
    monkeypatch.setattr(da, "find_class",
                        lambda conn, class_name=None, class_id=None: {"class_id": "cls_x", "name": "六（1）班"})
    monkeypatch.setattr(ds, "build_diagnosis", lambda conn, class_id: {
        "chains": [{"root_name": "乘除运算", "path_names": ["乘除运算"], "confidence": "high"}],
        "data_scope": {"class_id": "cls_x", "class_name": "六（1）班", "student_count": 20}})

    _, sid = _handshake(mcp_client, "admin-tok")
    r = _call(mcp_client, sid, "tools/call",
              {"name": "diagnose_learning_gaps", "arguments": {"class_name": "六（1）班"}}, "admin-tok")
    res = _result(r)
    assert res.get("isError") is not True


def test_mcp_invalid_param_is_error(mcp_client):
    _, sid = _handshake(mcp_client, "user-tok")
    r = _call(mcp_client, sid, "tools/call",
              {"name": "search_knowledge_points", "arguments": {"query": ""}}, "user-tok")
    res = _result(r)
    assert res.get("isError") is True
    assert "query 不能为空" in str(res.get("content", ""))


# ===================== 限流规则 =====================
def test_mcp_classify_limit():
    assert classify_limit("POST", "/mcp") == 60
    assert classify_limit("POST", "/mcp/") == 60


# ===================== DNS Rebinding 防护（S4 安全核查） =====================
def _post_init(client, *, host=None, origin=None, token="user-tok"):
    hdr = dict(_HDR)
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    if host is not None:
        hdr["Host"] = host
    if origin is not None:
        hdr["Origin"] = origin
    return client.post("/mcp/", json=_init_body(), headers=hdr)


def test_dns_rebinding_allowed_localhost(mcp_client):
    """白名单 Host（127.0.0.1:*）无 Origin → 放行。"""
    r = _post_init(mcp_client)
    assert r.status_code == 200


def test_dns_rebinding_rejects_foreign_host(mcp_client):
    """攻击者域名 Host（DNS rebinding 典型）→ SDK 421 Misdirected Request。"""
    r = _post_init(mcp_client, host="evil.example.com")
    assert r.status_code == 421


def test_dns_rebinding_rejects_foreign_origin(mcp_client):
    """Host 合法但 Origin 为 attacker → 403。"""
    r = _post_init(mcp_client, origin="http://evil.example.com")
    assert r.status_code == 403


def test_dns_rebinding_allows_production_domain(mcp_client):
    """正式域名（nginx 反代场景）在白名单内 → 放行。"""
    r = _post_init(mcp_client, host="edumateaip.xyz",
                   origin="https://edumateaip.xyz")
    assert r.status_code == 200


def test_dns_rebinding_env_override(monkeypatch):
    """环境变量白名单覆盖默认值：自定义 Host 放行、回环地址被拒。"""
    monkeypatch.setattr(settings, "EDUMATE_MCP_ALLOWED_HOSTS", ["custom.local:*"])
    monkeypatch.setattr(settings, "EDUMATE_MCP_ALLOWED_ORIGINS", ["http://custom.local:*"])
    monkeypatch.setattr(settings, "EDUMATE_API_TOKEN", "user-tok")
    monkeypatch.setattr(settings, "EDUMATE_MCP_ADMIN_TOKEN", "admin-tok")

    server, sub = _build_server()
    sm = server.session_manager

    @asynccontextmanager
    async def lifespan(app):
        async with sm.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", sub)
    with TestClient(app, base_url="http://custom.local:8000") as client:
        assert _post_init(client, host="custom.local:8000").status_code == 200
        assert _post_init(client, host="127.0.0.1:8000").status_code == 421


# ===================== stdio 传输 + stdout 污染 =====================
def test_mcp_stdio_handshake_no_stdout_pollution(tmp_path):
    child = tmp_path / "stdio_child.py"
    child.write_text(textwrap.dedent("""
        import sys
        from backend.app.mcp import build_mcp_server
        build_mcp_server().run(transport="stdio")
    """), encoding="utf-8")

    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "1"}}}
    notified = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    payload = "\n".join(json.dumps(x) for x in [init, notified, list_req]) + "\n"

    proc = subprocess.Popen(
        [sys.executable, str(child)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT), env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
    # 分步写入 + flush 后关闭 stdin，避免 communicate 一次性写入在子进程异步读循环就绪前
    # 就关闭管道导致后续行丢失（Windows 竞态，flaky 根因）。
    proc.stdin.write(payload.encode("utf-8"))
    proc.stdin.flush()
    proc.stdin.close()
    out, err = proc.communicate(timeout=60)

    stdout = out.decode("utf-8", "replace")
    stderr = err.decode("utf-8", "replace").strip()
    assert "serverInfo" in stdout                    # initialize 握手成功
    assert "search_knowledge_points" in stdout       # tools/list 含三工具
    assert "diagnose_learning_gaps" in stdout
    assert stderr == ""                              # stdout 无日志污染（日志走 stderr）
