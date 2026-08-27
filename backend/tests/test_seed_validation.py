"""启动期种子数据校验测试（D5 阶段 B）。"""
import pytest

from app.knowledge import loader


def test_validate_seed_pass():
    summary = loader.validate_seed()
    assert summary == "种子数据校验通过：7 个知识点 / 10 道题 / 20 名学生"


def test_validate_seed_missing_file(tmp_path, monkeypatch):
    missing = tmp_path / "no_such_file.json"
    monkeypatch.setattr(loader, "KNOWLEDGE_POINTS_FILE", missing)
    with pytest.raises(loader.SeedValidationError) as exc:
        loader.validate_seed()
    assert "no_such_file.json" in str(exc.value)
    assert "缺失" in str(exc.value)


def test_validate_seed_invalid_json(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{ 这不是合法 JSON", encoding="utf-8")
    monkeypatch.setattr(loader, "QUESTION_BANK_FILE", bad)
    with pytest.raises(loader.SeedValidationError) as exc:
        loader.validate_seed()
    assert "bad.json" in str(exc.value)
    assert "JSON" in str(exc.value)
