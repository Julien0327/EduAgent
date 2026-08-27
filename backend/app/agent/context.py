"""任务上下文与内存任务存储。

TaskContext 承载一次 Agent 任务的参数、步骤、产物与状态（对应技术方案 §4 的
Context 组件，无 LLM、无编排逻辑）。任务存内存字典，无数据库（对应 §3 / §11）。
"""
import queue
import threading
import uuid
from dataclasses import dataclass, field

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
    """内存任务存储（单进程，含并发访问锁）。"""

    def __init__(self):
        self._tasks: dict[str, TaskContext] = {}
        self._lock = threading.Lock()

    def create(self, prompt: str) -> TaskContext:
        task_id = uuid.uuid4().hex[:12]
        ctx = TaskContext(task_id=task_id, prompt=prompt)
        with self._lock:
            self._tasks[task_id] = ctx
        return ctx

    def get(self, task_id: str) -> TaskContext | None:
        with self._lock:
            return self._tasks.get(task_id)


task_store = TaskStore()
