"""日志系统测试（D5 阶段 A）。"""
import logging

import logging_config as lc


def test_task_logger_binds_task_id(caplog):
    log = lc.task_logger("test.task", "task-abc-123")
    with caplog.at_level(logging.INFO):
        log.info("执行中")
    assert any(r.task_id == "task-abc-123" and r.getMessage() == "执行中"
               for r in caplog.records)


def test_get_logger_default_task_id(caplog):
    log = lc.get_logger("test.main")
    with caplog.at_level(logging.INFO):
        log.info("启动")
    assert any(getattr(r, "task_id", "-") == "-" and r.getMessage() == "启动"
               for r in caplog.records)


def test_setup_logging_writes_file_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(lc, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(lc, "LOG_FILE", tmp_path / "app.log")
    monkeypatch.setattr(lc, "_configured", False)

    root = logging.getLogger()
    before_handlers = set(root.handlers)
    before_level = root.level
    try:
        lc.setup_logging()
        lc.setup_logging()  # 幂等：不应重复添加 handler
        lc.get_logger("test.setup").warning("写入日志文件")

        log_file = tmp_path / "app.log"
        assert log_file.exists()
        content = log_file.read_text(encoding="utf-8")
        assert "写入日志文件" in content
        assert "test.setup" in content
    finally:
        for h in list(root.handlers):
            if h not in before_handlers:
                root.removeHandler(h)
                h.close()
        root.setLevel(before_level)
