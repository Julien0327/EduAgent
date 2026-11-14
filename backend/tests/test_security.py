"""API 安全测试（Phase 2 / S6，§10.5，决策⑪）。

覆盖：401（缺/错 token 不区分）/ 503 fail-closed（未配置 token）/ health 免费 /
限流 429+Retry-After / 分类限流规则 / 日志脱敏（Bearer）与学生姓名学号脱敏。
"""
import logging

import pytest
from fastapi.testclient import TestClient

from backend.app.api.security import (
    RedactFilter,
    classify_limit,
    limiter,
    mask_student_id,
    mask_student_name,
)
from backend.config import settings
from backend.main import app
from backend.tests.conftest import AUTH_HEADERS


@pytest.fixture()
def client():
    with TestClient(app, headers=AUTH_HEADERS) as c:
        yield c


def test_health_open_without_auth(client):
    """存活探针免认证。"""
    assert client.get("/api/health").status_code == 200


def test_401_wrong_token(client):
    r = client.get("/api/tasks", headers={"X-API-Token": "wrong"})
    assert r.status_code == 401
    assert r.json() == {"detail": "未授权"}  # 不含内部细节


def test_401_missing_token_equals_wrong(client):
    """缺 token 与错 token 同响应（防探测，§10.5）。
    client 级默认带 AUTH 头——用空 token 头模拟缺失（extract 后为 None）。"""
    r_missing = client.get("/api/tasks", headers={"X-API-Token": ""})
    r_wrong = client.get("/api/tasks", headers={"X-API-Token": "nope"})
    assert r_missing.status_code == r_wrong.status_code == 401
    assert r_missing.json() == r_wrong.json()


def test_bearer_header_accepted(client):
    r = client.get("/api/health", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    # Bearer 头可访问业务端点
    r = client.get("/api/sessions", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200


def test_fail_closed_when_token_unset(client, monkeypatch):
    """未配置 token → fail-closed：仅 /api/health 可用，其余 503。"""
    monkeypatch.setattr(settings, "EDUMATE_API_TOKEN", None)
    assert client.get("/api/health").status_code == 200
    r = client.get("/api/sessions", headers=AUTH_HEADERS)  # 带正确 token 也拒
    assert r.status_code == 503
    assert "fail-closed" in r.json()["detail"]


def test_rate_limit_429_with_retry_after(client):
    """滑动窗口：超限 429 + Retry-After（sessions 档 120/min——直接塞满）。"""
    key = "testclient:2"
    limiter._hits.clear()
    for _ in range(120):
        allowed, _ = limiter.check(key, 120)
        assert allowed
    allowed, retry_after = limiter.check(key, 120)
    assert not allowed and 1 <= retry_after <= 61
    # HTTP 层验证（用 citations 档 120——同窗口键不同桶；直接压 sessions 端点）
    limiter._hits.clear()
    for _ in range(120):
        client.get("/api/sessions", headers=AUTH_HEADERS)
    r = client.get("/api/sessions", headers=AUTH_HEADERS)
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    limiter._hits.clear()


def test_classify_limit_rules():
    """§10.5 保护范围表的规则映射。"""
    assert classify_limit("GET", "/api/health") is None
    assert classify_limit("POST", "/api/chat") == 30
    assert classify_limit("POST", "/api/tasks") == 30
    assert classify_limit("GET", "/api/tasks") == 120
    assert classify_limit("GET", "/api/tasks/abc") == 120
    assert classify_limit("GET", "/api/tasks/abc/events") == 30
    assert classify_limit("GET", "/api/tasks/abc/files/x.docx") == 60
    assert classify_limit("GET", "/api/tasks/abc/preview/exam") == 60
    assert classify_limit("GET", "/api/tasks/abc/citations") == 120
    assert classify_limit("POST", "/api/import") == 10
    assert classify_limit("GET", "/api/import/imp_1") == 10
    assert classify_limit("GET", "/api/sessions") == 120
    assert classify_limit("GET", "/api/stats/cost") == 60
    assert classify_limit("GET", "/some/static") is None  # 非 /api 不限


# ---------- 日志脱敏 ----------

def test_redact_filter_bearer(caplog):
    logger = logging.getLogger("sec_test")
    f = RedactFilter()
    logger.addFilter(f)
    with caplog.at_level(logging.INFO, logger="sec_test"):
        logger.info("请求头 Authorization: Bearer super-secret-token-value")
        logger.warning("透传参数 %s", "Bearer abc.def.ghi")
    assert "super-secret-token-value" not in caplog.text
    assert "abc.def.ghi" not in caplog.text
    assert "Bearer ***" in caplog.text
    logger.removeFilter(f)


def test_student_masking():
    assert mask_student_name("张三") == "张*"
    assert mask_student_name("欧阳文山") == "欧阳**"
    assert mask_student_name("王") == "王"
    assert mask_student_id("20240101") == "****0101"  # 8 位保留后 4 位
    assert mask_student_id("123") == "123"
