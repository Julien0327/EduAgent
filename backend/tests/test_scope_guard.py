"""学段范围守卫测试（Phase 3 / S0 收尾，决策㉝）。

验证初中请求在 API 层被友好拦截：
- chat：200 + scope-guard 文本，不调 LLM；
- tasks：200 + scope_notice/task_id=None，不创建任务、不调 planner；
- 小学内容不被误拦。
"""
import pytest
from fastapi.testclient import TestClient

from backend.app.agent.context import task_store
from backend.app.scope import SCOPE_NOTICE, detect_unsupported_scope
from backend.main import app
from backend.tests.conftest import AUTH_HEADERS


@pytest.mark.parametrize("text", [
    "请讲一元二次方程", "七年级数学", "勾股定理怎么讲", "初中函数",
    "九年级二次函数", "全等三角形判定", "不等式组",
])
def test_junior_signals_blocked(text):
    assert detect_unsupported_scope(text) == SCOPE_NOTICE


@pytest.mark.parametrize("text", [
    "分数应用题", "鸡兔同笼", "圆的面积", "三角形面积", "长方形面积",
    "方程求解", "六年级数学", "百分数计算",
])
def test_primary_content_not_blocked(text):
    assert detect_unsupported_scope(text) is None


def test_chat_junior_returns_notice_without_llm(monkeypatch):
    called = []

    class NoLLM:
        def chat(self, *args, **kwargs):
            called.append(1)
            raise AssertionError("scope guard must not call LLM")

    monkeypatch.setattr("backend.app.api.chat.get_llm_service", lambda: NoLLM())
    with TestClient(app, headers=AUTH_HEADERS) as client:
        r = client.post("/api/chat", json={"message": "请讲一元二次方程"})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "scope-guard"
    assert body["message"] == SCOPE_NOTICE
    assert body["session_id"]
    assert not called


def test_tasks_junior_returns_notice_without_task(monkeypatch):
    called = []

    def fake_plan(*args, **kwargs):
        called.append(1)
        raise AssertionError("scope guard must not call planner")

    monkeypatch.setattr("backend.app.api.tasks.plan", fake_plan)
    with TestClient(app, headers=AUTH_HEADERS) as client:
        r = client.post("/api/tasks", json={"prompt": "请讲一元二次方程"})
    assert r.status_code == 200
    body = r.json()
    assert body["scope_notice"] == SCOPE_NOTICE
    assert body["task_id"] is None
    assert body["stage_supported"] == "primary"
    assert not called
    assert len(task_store._tasks) == 0
