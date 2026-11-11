"""SQLite 存储层：连接管理与迁移执行（Phase 1 / Step 1）。

- 库文件：默认 ``data/db/edumate.sqlite``（不入 Git，决策⑳；
  可用环境变量 ``EDUMATE_DB_PATH`` 覆盖，测试与部署各自注入）。
- 迁移：V1 = 全量 DDL（技术方案 §2.3「首次建库即含全部表」），后续增量按序号追加。
- 约束：每个连接统一 ``PRAGMA foreign_keys=ON``（外键是连接级开关，不依赖全局默认）；
  UNIQUE/CHECK 兜底 + 应用层校验双保险（§3.4）。
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.config import settings
from backend.logging_config import get_logger

log = get_logger("storage")

# 仓库根目录：backend/app/storage/db.py -> 上溯 3 级到 backend -> 再上一级到仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "db" / "edumate.sqlite"

MIGRATIONS_DIR = Path(__file__).resolve().parent
# (版本号, 文件名)：V1 为全量 DDL，后续增量迁移按序号追加到本表
MIGRATIONS: list[tuple[int, str]] = [(1, "migrations.sql")]

# 建库后必须存在的全部表（§2.3 全部表 + schema_migrations；缺任一即 fail-fast）
EXPECTED_TABLES = (
    "knowledge_points",
    "kp_edges",
    "questions",
    "q_kp",
    "questions_fts",
    "classes",
    "students",
    "exams",
    "exam_questions",
    "student_answers",
    "import_batches",
    "sessions",
    "messages",
    "tasks",
    "steps",
    "eval_cases",
    "eval_runs",
    "schema_migrations",
)

BUSY_TIMEOUT_MS = 5000


class SchemaValidationError(Exception):
    """建库/迁移完成后校验失败（缺表等），供调用方 fail-fast。"""


def get_db_path() -> Path:
    """业务库路径：环境变量 EDUMATE_DB_PATH 优先，否则仓库内默认路径。"""
    if settings.DB_PATH:
        return Path(settings.DB_PATH)
    return DEFAULT_DB_PATH


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """新建 SQLite 连接：统一开启外键约束与 busy_timeout，行结果用 Row 工厂。

    所有数据库访问都必须经由本函数创建连接，保证外键约束无遗漏。
    """
    path = Path(db_path) if db_path is not None else get_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def apply_migrations(db_path: Path | str | None = None) -> int:
    """按序应用未执行的迁移并校验建表结果，返回当前 schema 版本。幂等，可重复调用。

    新增增量迁移：往 MIGRATIONS 追加 (版本号, 文件名) 即可，已应用版本自动跳过。
    """
    path = Path(db_path) if db_path is not None else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        # WAL：单写多读，导入事务与业务查询互不长时间阻塞（对 :memory: 无效，自动忽略）
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        current = _current_version(conn)
        for version, filename in MIGRATIONS:
            if version <= current:
                continue
            sql = (MIGRATIONS_DIR / filename).read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, _utcnow_iso()),
            )
            conn.commit()
            log.info("迁移已应用：v%s（%s）-> %s", version, filename, path)
        _verify_tables(conn)
        return _current_version(conn)
    finally:
        conn.close()


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    return int(row["v"] or 0)


def _verify_tables(conn: sqlite3.Connection) -> None:
    """建库结果 fail-fast：EXPECTED_TABLES 缺任一即抛 SchemaValidationError。"""
    present = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = [name for name in EXPECTED_TABLES if name not in present]
    if missing:
        raise SchemaValidationError(f"建库校验失败，缺少表：{', '.join(missing)}")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
