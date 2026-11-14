"""任务上下文与任务存储（Phase 2 / S5 起 SQLite 持久化，§7.3）。

TaskContext 承载一次 Agent 任务的参数、步骤、产物与状态（对应技术方案 §4 的
Context 组件，无 LLM、无编排逻辑）。
"""
import json
import queue
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.app.retrieval.citations import CitationLedger

# 任务状态
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

# 步骤状态
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_FAILED = "failed"


@dataclass
class StepRecord:
    node: str          # 节点名（如 understand_task）
    status: str        # running / done / failed
    message: str = ""  # 节点说明或错误信息


@dataclass
class ArtifactRecord:
    name: str       # 展示名，如 "试卷"
    file_name: str  # 文件名，如 "01_试卷.docx"
    path: str       # 绝对路径


@dataclass
class TaskContext:
    task_id: str
    prompt: str
    status: str = STATUS_PENDING
    workflow: str = ""               # Planner 选中的工作流
    params: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    error: str = ""

    # D4：事件队列，供 SSE 实时进度流消费（纯增量，不影响既有字段与行为）。
    events: queue.Queue = field(default_factory=queue.Queue, repr=False)

    # Phase 2/S2（§4.4）：任务级引用账本——凡题库/图谱取数必带 ref，装配进账本，
    # 任务收尾落盘 citations.json。__post_init__ 按 task_id 初始化。
    citations: CitationLedger = field(default=None, repr=False, compare=False)

    # Phase 2/S5（§7.1）：所属会话（None=无会话/CLI 直跑）；tasks.session_id 落库
    session_id: str | None = None

    def __post_init__(self):
        if self.citations is None:
            self.citations = CitationLedger(self.task_id)

    def push_event(self, event: dict) -> None:
        """向事件队列推送一条事件。"""
        self.events.put(event)

    def add_step(self, node: str, message: str = "") -> StepRecord:
        """追加一个 running 步骤并返回引用，工作流可原地更新其状态。"""
        step = StepRecord(node=node, status=STEP_RUNNING, message=message)
        self.steps.append(step)
        return step

    def add_artifact(self, name: str, file_name: str, path: str) -> None:
        self.artifacts.append(ArtifactRecord(name=name, file_name=file_name, path=str(path)))
        self.push_event({"type": "artifact", "name": name, "file_name": file_name})

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "workflow": self.workflow,
            "params": self.params,
            "steps": [
                {"node": s.node, "status": s.status, "message": s.message}
                for s in self.steps
            ],
            "artifacts": [
                {"name": a.name, "file_name": a.file_name, "path": a.path}
                for a in self.artifacts
            ],
            "error": self.error,
        }


class TaskStore:
    """SQLite 任务存储（Phase 2 / S5，§7.3：接口不变 create/get/update）。

    进程内 dict 为读缓存，``update()`` 全量写穿透（tasks 行 UPSERT + steps 重建）；
    工作流层零改动（只在状态变化点显式调 update）。重启后任务仍可从库直查
    （get_from_db，§7.4 重启恢复验收）。
    """

    def __init__(self, db_path: str | None = None):
        self._tasks: dict[str, TaskContext] = {}
        self._lock = threading.Lock()
        self._db_path = db_path  # None = 惰性取 settings.DB_PATH（测试可注入）
        self._conn: sqlite3.Connection | None = None
        self._conn_lock = threading.Lock()

    def _get_conn(self) -> sqlite3.Connection:
        """惰性连接（首次穿透时建立；WAL：单写连接队列，读不阻塞，§7.3）。

        connect 前跑 apply_migrations（幂等）——保证 tasks/steps 表存在
        （CLI run_task / 测试环境不经 lifespan 时也能落库）。
        """
        with self._conn_lock:
            if self._conn is None:
                from backend.app.storage import db as _db
                path = self._db_path or _db.get_db_path()
                _db.apply_migrations(path)
                self._conn = _db.connect(path, check_same_thread=False)
                try:
                    self._conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.OperationalError:
                    pass  # :memory: 库不支持 WAL，忽略
            return self._conn

    def reset(self) -> None:
        """关闭连接并清空缓存（测试隔离用；生产不调用）。"""
        with self._conn_lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
        self._tasks.clear()

    def create(self, prompt: str, session_id: str | None = None) -> TaskContext:
        task_id = uuid.uuid4().hex[:12]
        ctx = TaskContext(task_id=task_id, prompt=prompt)
        ctx.session_id = session_id
        with self._lock:
            self._tasks[task_id] = ctx
        self.update(ctx)
        return ctx

    def get(self, task_id: str) -> TaskContext | None:
        with self._lock:
            return self._tasks.get(task_id)

    def get_from_db(self, task_id: str) -> dict | None:
        """从库直读（重启后内存缓存为空，任务/步骤仍可查——重启恢复验收路径）。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT task_id, session_id, prompt, status, workflow, params, error, "
            "created_at, started_at, ended_at FROM tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        if d.get("params"):
            try:
                d["params"] = json.loads(d["params"])
            except (ValueError, TypeError):
                pass
        d["steps"] = [
            dict(r) for r in conn.execute(
                "SELECT step_no, node, status, output_summary FROM steps "
                "WHERE task_id=? ORDER BY step_no", (task_id,))
        ]
        return d

    def update(self, ctx: TaskContext) -> None:
        """全量快照写穿透（接口不变，§7.3；steps 表为该任务步骤的全量重建）。"""
        conn = self._get_conn()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._conn_lock:
            conn.execute(
                """INSERT INTO tasks
                     (task_id, session_id, prompt, status, workflow, params, error,
                      created_at, started_at, ended_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(
                       (SELECT created_at FROM tasks WHERE task_id=?), ?), ?, ?)
                   ON CONFLICT(task_id) DO UPDATE SET
                     session_id=excluded.session_id, status=excluded.status,
                     workflow=excluded.workflow, params=excluded.params,
                     error=excluded.error, started_at=excluded.started_at,
                     ended_at=excluded.ended_at""",
                (ctx.task_id, ctx.session_id, ctx.prompt, ctx.status, ctx.workflow,
                 json.dumps(ctx.params, ensure_ascii=False, default=str), ctx.error,
                 ctx.task_id, now, now, now),
            )
            conn.execute("DELETE FROM steps WHERE task_id=?", (ctx.task_id,))
            conn.executemany(
                """INSERT INTO steps
                     (task_id, step_no, node, status, output_summary)
                   VALUES (?, ?, ?, ?, ?)""",
                [(ctx.task_id, i, s.node, s.status, (s.message or "")[:500])
                 for i, s in enumerate(ctx.steps, 1)],
            )
            conn.commit()


task_store = TaskStore()
