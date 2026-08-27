"""日志系统（D5 加固，对应技术方案 §24）。

使用标准库 logging：控制台 + 滚动文件（logs/app.log，5MB × 3），
格式为「时间 | 级别 | 模块 | 任务ID | 消息」。task_id 通过 LoggerAdapter
注入，非任务上下文的日志显示 "-"。零第三方依赖。
"""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 仓库根目录：backend/logging_config.py -> 上溯 1 级到 backend -> 再上一级到仓库根
REPO_ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = REPO_ROOT / "logs"
LOG_FILE = LOGS_DIR / "app.log"

_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(task_id)s | %(message)s"
_configured = False


class TaskIdFilter(logging.Filter):
    """为缺失 task_id 的日志记录补默认值 "-"，保证格式化不报错。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "task_id"):
            record.task_id = "-"
        return True


class TaskLoggerAdapter(logging.LoggerAdapter):
    """给日志注入 task_id 上下文的适配器。"""

    def process(self, msg, kwargs):
        extra = kwargs.setdefault("extra", {})
        extra.setdefault("task_id", self.extra.get("task_id", "-"))
        return msg, kwargs


def setup_logging(level: int = logging.INFO) -> None:
    """配置根 logger（控制台 + 滚动文件）。幂等，可重复调用。"""
    global _configured
    if _configured:
        return

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.addFilter(TaskIdFilter())

    file_handler = RotatingFileHandler(
        str(LOG_FILE), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(TaskIdFilter())

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console)
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str) -> TaskLoggerAdapter:
    """返回非任务上下文的 logger（task_id 显示 "-"）。"""
    return TaskLoggerAdapter(logging.getLogger(name), {"task_id": "-"})


def task_logger(name: str, task_id: str) -> TaskLoggerAdapter:
    """返回绑定指定 task_id 的 logger。"""
    return TaskLoggerAdapter(logging.getLogger(name), {"task_id": task_id})
