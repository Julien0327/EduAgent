"""任务持久化与多轮会话测试（Phase 2 / S5，§7）。

覆盖：TaskStore SQLite 写穿透 / 重启恢复（get_from_db）/ 并发 10 任务 WAL /
会话存储（截断/追问计数）/ 会话 API / F1 追问通道与降级。
工作流/LLM 均用 Fake（离线）。
"""
import json
import threading

import pytest

from backend.tests.conftest import AUTH_HEADERS
from fastapi.testclient import TestClient

import backend.app.agent.llm_utils as llm_utils
from backend.app.agent import session_store
from backend.app.agent.context import TaskContext, task_store
from backend.app.agent.llm_client import LLMResponse
from backend.main import app

# fetch_knowledge 走真实检索层——本模块替换为 Fake（离线）
pytestmark = pytest.mark.usefixtures("fake_retrieval")


@pytest.fixture()
def fake_llm(monkeypatch):
    def _install(responses):
        class FakeLLM:
            def __init__(self):
                self.responses = list(responses)

            def chat_with_tools(self, messages, tools=None, tool_choice="auto"):
                if self.responses:
                    return self.responses.pop(0)
                return LLMResponse(content="", model="fake", tool_calls=[])

            def chat(self, message, history=None):
                return {"message": f"回显:{message[:10]}", "model": "fake"}

        fake = FakeLLM()
        monkeypatch.setattr(llm_utils, "get_llm_service", lambda: fake)
        return fake

    return _install


# ---------- TaskStore SQLite（§7.3） ----------

def test_task_store_write_through(tmp_path, monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "t.sqlite"))
    task_store.reset()

    ctx = task_store.create("测试任务", session_id="s1")
    ctx.workflow = "lesson_plan"
    ctx.add_step("understand_task").status = "done"
    task_store.update(ctx)

    row = task_store.get_from_db(ctx.task_id)  # 绕过内存缓存直读库
    assert row["prompt"] == "测试任务"
    assert row["session_id"] == "s1"
    assert row["workflow"] == "lesson_plan"
    assert row["status"] in ("pending", "running", "completed")
    assert row["steps"][0]["node"] == "understand_task"
    task_store.reset()


def test_task_store_survives_restart(tmp_path, monkeypatch):
    """§7.4 重启恢复：内存缓存清空（模拟进程重启）后任务/步骤仍可查。"""
    from backend.config import settings
    db_file = str(tmp_path / "restart.sqlite")
    monkeypatch.setattr(settings, "DB_PATH", db_file)
    task_store.reset()

    ctx = task_store.create("重启前任务")
    ctx.status = "completed"
    ctx.add_step("understand_task").status = "done"
    task_store.update(ctx)
    task_id = ctx.task_id

    task_store.reset()  # 模拟重启（内存缓存清空、连接关闭）
    assert task_store.get(task_id) is None  # 内存确实没了
    row = task_store.get_from_db(task_id)  # 库里还在
    assert row is not None and row["status"] == "completed"
    assert row["steps"][0]["status"] == "done"
    task_store.reset()


def test_concurrent_10_tasks_wal(tmp_path, monkeypatch):
    """§7.4：并发 10 任务提交，WAL 下写不冲突、无错乱。"""
    from backend.config import settings
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "conc.sqlite"))
    task_store.reset()

    errors: list[str] = []
    ids: list[str] = []

    def worker(i: int):
        try:
            ctx = task_store.create(f"并发任务 {i}")
            ctx.add_step("understand_task").status = "done"
            ctx.status = "completed"
            task_store.update(ctx)
            ids.append(ctx.task_id)
        except Exception as exc:  # pragma: no cover
            errors.append(str(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(set(ids)) == 10
    for tid in ids:
        row = task_store.get_from_db(tid)
        assert row is not None and row["status"] == "completed"
    task_store.reset()


# ---------- 会话存储（§7.1/7.2） ----------

def test_session_store_lifecycle():
    sid = session_store.create_session()
    session_store.add_message(sid, "user", "帮我出一份分数与百分数的卷子")
    session_store.add_message(sid, "assistant", "好的，已生成")
    session_store.add_message(sid, "user", "再出一份难度高的")

    data = session_store.get_session(sid)
    assert data["title"] and "分数与百分数" in data["title"]  # 首条消息作标题
    assert len(data["messages"]) == 3
    assert data["messages"][0]["role"] == "user"

    sessions = session_store.list_sessions()
    assert any(s["session_id"] == sid for s in sessions)


def test_context_window_truncates(tmp_path, monkeypatch):
    """§7.2：入上下文每轮截 2000 字符（原文全量落库不受影响）。"""
    long_text = "长" * 3000
    sid = session_store.create_session()
    session_store.add_message(sid, "user", long_text)
    session_store.add_message(sid, "assistant", "ok")
    window = session_store.context_window(sid)
    assert len(window[0]["content"]) == 2000  # 截断
    assert len(session_store.get_session(sid)["messages"][0]["content"]) == 3000  # 原文完整


def test_clarify_count():
    sid = session_store.create_session()
    session_store.add_message(sid, "assistant", session_store.CLARIFY_PREFIX + "哪个章节？")
    session_store.add_message(sid, "assistant", "普通回复")
    session_store.add_message(sid, "assistant", session_store.CLARIFY_PREFIX + "几年级？")
    assert session_store.clarify_count(sid) == 2


# ---------- 会话 API + chat 多轮（§10.2） ----------

def test_sessions_api_and_chat_multiturn(fake_llm):
    fake_llm([])
    with TestClient(app, headers=AUTH_HEADERS) as client:
        # 无 session_id：自动新建并返回
        r1 = client.post("/api/chat", json={"message": "你好"})
        assert r1.status_code == 200
        sid = r1.json()["session_id"]
        assert sid
        # 带 session_id：同一会话
        r2 = client.post("/api/chat", json={"message": "再见", "session_id": sid})
        assert r2.json()["session_id"] == sid
        # 会话列表与回放
        lst = client.get("/api/sessions").json()["sessions"]
        assert any(s["session_id"] == sid for s in lst)
        detail = client.get(f"/api/sessions/{sid}").json()
        assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
        # 不存在 → 404
        assert client.get("/api/sessions/nope").status_code == 404


# ---------- F1 追问通道（§7.1） ----------

def _wait_task_done(task_id: str, timeout: float = 5.0):
    """轮询等待后台任务线程到终态（fixture teardown 前必须让线程跑完，
    否则其依赖的 Fake/连接已被销毁——顺序依赖的根源）。"""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = task_store.get_from_db(task_id)
        if row and row["status"] in ("completed", "failed"):
            return row
        time.sleep(0.05)
    return None


def _plan_resp(need_clarification=None) -> LLMResponse:
    args = {"workflow": "lesson_plan",
            "params": {"subject": "数学", "chapter": "分数与百分数", "topic": "分数与百分数"}}
    if need_clarification:
        args["need_clarification"] = need_clarification
    return LLMResponse(content="", model="fake", tool_calls=[
        {"id": "p1", "name": "submit_plan", "arguments": args}])


def _lesson_resp() -> LLMResponse:
    return LLMResponse(content="", model="fake", tool_calls=[{
        "id": "l1", "name": "submit_lesson_plan",
        "arguments": {"objectives": ["o1", "o2", "o3"], "key_points": "k",
                      "difficult_points": "d",
                      "process": [{"step": "导入", "detail": "x"}], "homework": "h"},
    }])


def test_f1_clarification_then_task(fake_llm):
    """信息不足 → 返回追问（200，不建任务）；补充后正常 202。"""
    fake_llm([_plan_resp("请问针对哪个章节？"),  # 预判：追问
              _plan_resp(), _lesson_resp()])     # 第二次：正常建任务
    with TestClient(app, headers=AUTH_HEADERS) as client:
        sid = "f1s1"
        session_store.create_session("f1 会话") if False else None
        from backend.app.storage import db as _db
        # 直接用指定 sid 建会话行（API 未提供建会话端点，chat 可建）
        session_store.add_message(sid, "user", "帮我出个卷子")
        r1 = client.post("/api/tasks", json={"prompt": "帮我出个卷子", "session_id": sid})
        assert r1.status_code == 200
        body = r1.json()
        assert body["need_clarification"] and body["task_id"] is None
        # 会话里多了追问消息
        assert session_store.clarify_count(sid) == 1
        # 第二次（信息补足的表述）：正常创建
        r2 = client.post("/api/tasks", json={"prompt": "出《分数与百分数》教案", "session_id": sid})
        assert r2.status_code == 202
        assert r2.json().get("clarification_dropped") is None
        assert _wait_task_done(r2.json()["task_id"]) is not None  # 线程跑完再 teardown


def test_f1_drops_after_two_rounds(fake_llm):
    """追问已 2 轮 → 第 3 次降级默认值执行（202 + clarification_dropped）。"""
    fake_llm([_plan_resp("还在追问？"),  # 预判（降级分支）
              _plan_resp(),             # understand_task（降级后任务照常执行）
              _lesson_resp()])          # 教案内容
    with TestClient(app, headers=AUTH_HEADERS) as client:
        sid = "f1s2"
        session_store.add_message(sid, "user", "出卷子")
        # 预置 2 轮追问记录
        session_store.add_message(sid, "assistant", session_store.CLARIFY_PREFIX + "哪个章节？")
        session_store.add_message(sid, "assistant", session_store.CLARIFY_PREFIX + "几年级？")
        r = client.post("/api/tasks", json={"prompt": "出卷子", "session_id": sid})
        assert r.status_code == 202
        assert r.json()["clarification_dropped"] is True
        assert _wait_task_done(r.json()["task_id"]) is not None  # 线程跑完再 teardown


def test_no_session_task_skips_clarification(fake_llm):
    """无会话任务不追问（保持 D4 语义）：直接 202。"""
    fake_llm([_plan_resp("谁问的？"), _lesson_resp()])
    with TestClient(app, headers=AUTH_HEADERS) as client:
        r = client.post("/api/tasks", json={"prompt": "写《分数与百分数》教案"})
        assert r.status_code == 202
        assert "need_clarification" not in r.json()
        assert _wait_task_done(r.json()["task_id"]) is not None
