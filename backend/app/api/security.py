"""API 安全：认证与限流（Phase 2 / S6，§10.5，决策⑪）。

**认证**：
- ``EDUMATE_API_TOKEN`` 环境变量注入（§0.3-4），不硬编码、不入库、不进日志；
- 校验 ``Authorization: Bearer <token>``（兼容 ``X-API-Token`` 头），
  ``secrets.compare_digest`` 常量时间比较；
- 401 不区分"缺 token"与"错 token"（防探测）；
- 未配置 token → fail-closed：仅 ``GET /api/health`` 可用，其余 /api/* 一律 503。

**限流**：进程内滑动窗口（自写 ~60 行，单实例部署前提下够用；分布式限流随
多实例需求走变更记录）→ 超限 ``429 + Retry-After``。规则 = 代码分支 classify
（§10.5 保护范围表），放行 OPTIONS 预检与静态资源，AIP /rpc（mTLS 通道）不进本层。

**日志脱敏**：全局 logging.Filter 把 ``Bearer <值>`` 替换为 ``Bearer ***``；
学生姓名/学号脱敏函数供导入链路使用（错误报告仅在接口响应返回，服务端日志
只记批次号与计数）。
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from collections import deque

from backend.config import settings

# 每分钟允许的请求簿记窗口
WINDOW_SECONDS = 60.0


class TokenMissing:
    """认证未配置（fail-closed 状态标记）。"""


def get_expected_token() -> str | None:
    return settings.EDUMATE_API_TOKEN or None


def extract_token(headers, query_string: bytes | None = None) -> str | None:
    """从请求取 token：Authorization: Bearer x 优先，兼容 X-API-Token 头。

    query token（``?token=``）：仅供 SSE 端点使用——EventSource 无法自定义
    请求头（浏览器限制）；token 不入日志（日志脱敏不记 query string）。
    """
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    tok = headers.get("x-api-token")
    if tok:
        return tok
    if query_string:
        from urllib.parse import parse_qs
        qs = parse_qs(query_string.decode("latin-1"))
        vals = qs.get("token")
        if vals and vals[0]:
            return vals[0]
    return None


def token_ok(provided: str | None, expected: str | None) -> bool:
    """常量时间比较；expected 为 None 时不在此判断（fail-closed 由调用方处理）。"""
    if not expected:
        return False
    return bool(provided) and secrets.compare_digest(provided, expected)


# ---------- 限流规则（§10.5 保护范围表） ----------

def classify_limit(method: str, path: str) -> int | None:
    """返回该 (method, path) 的每分钟限流值；None=不限（health）。

    规则按 §10.5 表：/api/health 免；chat/tasks 创建 30；events(SSE) 30；
    files/preview 60；stats/eval 60；import 10；其余业务查询 120。
    """
    if path == "/api/health":
        return None
    if not path.startswith("/api/"):
        return None  # 静态资源/AIP 不进业务限流
    parts = path.rstrip("/").split("/")
    # /api/tasks/{id}/events | /files/{name} | /preview/{kind} | /citations
    if len(parts) >= 4 and parts[2] == "tasks":
        sub = parts[4] if len(parts) >= 5 else ""
        if sub == "events":
            return 30
        if sub in ("files", "preview"):
            return 60
        return 120  # 任务回查 / trace / citations 等
    if method == "POST" and path in ("/api/chat", "/api/tasks"):
        return 30
    if path.startswith("/api/import"):
        return 10
    if path.startswith("/api/stats") or path.startswith("/api/eval"):
        return 60
    return 120  # sessions 等业务数据查询


class SlidingWindowLimiter:
    """进程内滑动窗口限流（单实例够用；key=限流桶，如 f"{ip}:{bucket}"）。"""

    def __init__(self, window_seconds: float = WINDOW_SECONDS):
        self._window = window_seconds
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> tuple[bool, int]:
        """记录一次请求：返回 (是否允许, Retry-After 秒)。"""
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self._window:
                q.popleft()
            return (True, 0)  # 占位：由 check 方法决定

    def check(self, key: str, limit: int) -> tuple[bool, int]:
        """检查并登记：未超限登记并放行；超限返回剩余等待秒（不登记）。"""
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self._window:
                q.popleft()
            if len(q) >= limit:
                retry_after = int(self._window - (now - q[0])) + 1
                return False, max(retry_after, 1)
            q.append(now)
            return True, 0

    def _prune_all(self) -> None:
        """清理空桶（防内存增长；测试/长驻进程低频调用）。"""
        now = time.monotonic()
        with self._lock:
            empty = [k for k, q in self._hits.items() if not q or now - q[-1] > self._window]
            for k in empty:
                self._hits.pop(k, None)


limiter = SlidingWindowLimiter()


class SecurityMiddleware:
    """纯 ASGI 中间件：认证（fail-closed）+ 限流（429+Retry-After）。

    顺序：OPTIONS 预检放行 → /api/health 放行 → 非 /api/* 放行（静态/AIP）→
    认证（未配置=503 fail-closed；错/缺=401 不区分）→ 限流（429）。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "GET")
        if method == "OPTIONS" or path == "/api/health" or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        client = scope.get("client")
        ip = client[0] if client else "unknown"

        expected = get_expected_token()
        if not expected:
            await self._send_json(send, 503, {"detail": "服务端未配置 EDUMATE_API_TOKEN，API 处于 fail-closed 状态"})
            return
        if not token_ok(extract_token(headers, scope.get("query_string")), expected):
            # 401 不区分缺/错（防探测），响应体不含内部细节
            await self._send_json(send, 401, {"detail": "未授权"})
            return

        limit = classify_limit(method, path)
        if limit is not None:
            allowed, retry_after = limiter.check(f"{ip}:{limit}", limit)
            if not allowed:
                await self._send_json(
                    send, 429,
                    {"detail": "请求过于频繁，请稍后重试"},
                    headers={"Retry-After": str(retry_after)},
                )
                return

        await self.app(scope, receive, send)

    async def _send_json(self, send, status: int, body: dict, headers: dict | None = None):
        import json as _json
        body_bytes = _json.dumps(body, ensure_ascii=False).encode("utf-8")
        out_headers = [(b"content-type", b"application/json; charset=utf-8")]
        for k, v in (headers or {}).items():
            out_headers.append((k.encode("latin-1"), v.encode("latin-1")))
        await send({"type": "http.response.start", "status": status, "headers": out_headers})
        await send({"type": "http.response.body", "body": body_bytes})


# ---------- 日志脱敏（§10.5） ----------

_BEARER_RE = re.compile(r"(Bearer\s+)(\S+)")

class RedactFilter(logging.Filter):
    """把日志记录中的 ``Bearer <token值>`` 替换为 ``Bearer ***``（token 绝不入日志）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _BEARER_RE.sub(r"\1***", record.msg)
        if record.args:
            record.args = tuple(
                _BEARER_RE.sub(r"\1***", a) if isinstance(a, str) else a
                for a in record.args
            )
        return True


def mask_student_name(name: str) -> str:
    """学生姓名脱敏：张三→张*；欧阳文山→欧阳**（保留首字/复姓前两字）。"""
    name = (name or "").strip()
    if len(name) <= 1:
        return name or ""
    keep = 2 if len(name) >= 4 else 1
    return name[:keep] + "*" * max(len(name) - keep, 1)


def mask_student_id(sid: str | int) -> str:
    """学号脱敏：仅保留后 4 位。"""
    s = str(sid or "")
    return "*" * max(len(s) - 4, 0) + s[-4:] if len(s) > 4 else s
