"""MCP SDK 连通性探针（Phase 5 / S0，§8.3）。

验证当前安装的 ``mcp`` SDK 实际 API，为 S4 编码提供事实依据（不凭印象写 API）：
- SDK 版本与关键迁移点（mcp 2.x 已把 FastMCP 重命名为 MCPServer）；
- MCPServer 实例化 + 工具注册（函数 type hint → 参数 input_schema）；
- 工具发现 list_tools（async）与 call_tool 实际调用（正常 / 缺参错误）；
- structured_output=True 对返回类型的约束（dict 不支持，需 pydantic 返回模型）；
- Streamable HTTP 挂载（streamable_http_app → Starlette，可挂 FastAPI）；
- stdio 传输子进程握手（JSON-RPC initialize + tools/list，验证 stdout 承载协议）。

本脚本不发起任何真实 LLM / DeepSeek 请求，只用本地样例工具。
输出：``data/staging/mcp_sdk_probe_report.json``（UTF-8，含中文详情）+ 控制台 ASCII 摘要。
"""
from __future__ import annotations

import asyncio
import importlib.metadata as md
import json
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "data" / "staging"
STAGING.mkdir(parents=True, exist_ok=True)

results: dict[str, dict] = {}


def record(name: str, ok: bool, detail: str = "") -> None:
    results[name] = {"ok": bool(ok), "detail": str(detail)[:2000]}


# 1. SDK 版本
try:
    record("sdk_version", True, md.version("mcp"))
except Exception as e:  # pragma: no cover
    record("sdk_version", False, repr(e))

from mcp.server.mcpserver import MCPServer  # noqa: E402


# 2. 实例化（v2 用 MCPServer，FastMCP 已重命名）
try:
    srv = MCPServer(name="edumate-probe", version="0.0.1",
                    description="S0 连通性探针服务器")
    record("mcpserver_instantiation", isinstance(srv, MCPServer),
           f"type={type(srv).__name__}")
except Exception as e:
    record("mcpserver_instantiation", False, repr(e))
    srv = None


# 3. 工具注册（type hint → input_schema）
def sample_search(query: str, top_k: int = 5) -> dict:
    """按知识点检索（探针样例）。"""
    return {"points": [], "citations": []}


async def _list_first_tool(server):
    tools = await server.list_tools()
    return tools


try:
    srv.add_tool(sample_search, name="search_knowledge_points",
                 description="检索知识点（探针样例）")
    tools = asyncio.run(_list_first_tool(srv))
    t = tools[0]
    schema = t.input_schema
    record("tool_registration", len(tools) == 1 and t.name == "search_knowledge_points",
           f"tools={len(tools)} name={t.name}")
    record("schema_from_typehints", schema.get("type") == "object",
           json.dumps(schema, ensure_ascii=False))
    record("schema_required_field", schema.get("required") == ["query"],
           f"required={schema.get('required')}")
    record("schema_default_value", schema["properties"]["top_k"].get("default") == 5,
           f"top_k.default={schema['properties']['top_k'].get('default')}")
except Exception as e:
    record("tool_registration", False, repr(e))


# 4. call_tool 实际调用（正常 + 缺参）
async def _call_ok(server):
    return await server.call_tool("search_knowledge_points",
                                  {"query": "分数", "top_k": 3})


try:
    res = asyncio.run(_call_ok(srv))
    record("call_tool_normal", (res.is_error is False and res.result_type == "complete"),
           f"result_type={res.result_type} is_error={res.is_error} "
           f"content_text={bool(res.content)}")
except Exception as e:
    record("call_tool_normal", False, repr(e))

try:
    asyncio.run(srv.call_tool("search_knowledge_points", {}))
    record("call_tool_missing_arg", False, "缺参未抛错（预期应校验拒绝）")
except Exception as e:
    record("call_tool_missing_arg", True, f"{type(e).__name__}: {str(e)[:160]}")


# 5. structured_output 对返回类型的约束
srv2 = MCPServer(name="probe-structured-output")
try:
    srv2.add_tool(sample_search, name="s", structured_output=True)
    record("structured_output_dict", False, "dict 返回未被拒绝（预期应拒绝）")
except Exception as e:
    record("structured_output_dict", True,
           f"{type(e).__name__}: 返回类型须为可序列化模型（如 pydantic）")


# 6. Streamable HTTP 挂载
try:
    app = srv.streamable_http_app(streamable_http_path="/mcp")
    routes = [getattr(r, "path", None) for r in getattr(app, "routes", [])]
    record("streamable_http_app", "/mcp" in routes,
           f"type={type(app).__name__} routes={routes}")
except Exception as e:
    record("streamable_http_app", False, repr(e))


# 7. stdio 子进程握手（JSON-RPC initialize + tools/list）
CHILD = textwrap.dedent("""
    import sys
    from mcp.server.mcpserver import MCPServer
    srv = MCPServer(name="stdio-probe", version="0.0.1")
    def ping(x: int) -> dict:
        return {"echo": x}
    srv.add_tool(ping, name="ping", description="探针工具")
    srv.run(transport="stdio")
""")


def _probe_stdio() -> str:
    with tempfile.TemporaryDirectory() as td:
        child = Path(td) / "stdio_child.py"
        child.write_text(CHILD, encoding="utf-8")
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "probe", "version": "0.0.1"}}}
        notified = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        payload = "\n".join([json.dumps(init), json.dumps(notified),
                             json.dumps(list_req)]) + "\n"
        proc = subprocess.Popen(
            [sys.executable, str(child)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(REPO_ROOT))
        out, err = proc.communicate(payload.encode("utf-8"), timeout=30)
        lines = out.decode("utf-8", "replace").strip().splitlines()
        has_init_result = any("initialize" in ln or "serverInfo" in ln for ln in lines)
        has_tools_list = any("ping" in ln for ln in lines)
        stderr_text = err.decode("utf-8", "replace").strip()
        ok = has_init_result and has_tools_list
        return (f"ok={ok} stdout_lines={len(lines)} "
                f"initialize_result={has_init_result} tools_list={has_tools_list} "
                f"stderr_bytes={len(err)}")


try:
    detail = _probe_stdio()
    record("stdio_handshake", detail.startswith("ok=True"), detail)
except Exception as e:
    record("stdio_handshake", False, f"{type(e).__name__}: {str(e)[:200]}")


# 汇总：写入报告 + 控制台 ASCII 摘要
report = {
    "sdk_version": results.get("sdk_version", {}).get("detail"),
    "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
    "note": "mcp 2.x 迁移要点：FastMCP→MCPServer；list_tools/call_tool 为 async；"
            "工具 schema 由函数 type hint 生成（required=无默认值参数）；"
            "dict 返回序列化为 JSON 文本 content，structured_output=True 需 pydantic 返回模型。",
    "results": results,
}
(STAGING / "mcp_sdk_probe_report.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

summary = {k: v["ok"] for k, v in results.items()}
print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
all_ok = all(v["ok"] for v in results.values())
print(f"ALL_OK={all_ok}")
sys.exit(0 if all_ok else 2)
