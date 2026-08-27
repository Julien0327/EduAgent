"""种子数据加载（知识点 / 题库 / 预置答题）。

从 data/ 下的 JSON 文件读取并缓存到内存，供文档服务与（后续）Agent 使用。
数据源是只读的种子数据，加载结果用 lru_cache 缓存。
"""
import json
from functools import lru_cache
from pathlib import Path

# 仓库根目录：backend/app/knowledge/loader.py -> 上溯 3 级到 backend -> 再上一级到仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"

KNOWLEDGE_POINTS_FILE = DATA_DIR / "knowledge" / "knowledge_points.json"
QUESTION_BANK_FILE = DATA_DIR / "questions" / "question_bank.json"
STUDENT_ANSWERS_FILE = DATA_DIR / "demo" / "student_answers.json"


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_knowledge_points() -> dict:
    """加载知识点（7 个，含层级/描述/典型题型）。"""
    return _load_json(KNOWLEDGE_POINTS_FILE)


@lru_cache(maxsize=1)
def load_question_bank() -> dict:
    """加载题库（10 题，共 100 分，每题关联知识点 ID）。"""
    return _load_json(QUESTION_BANK_FILE)


@lru_cache(maxsize=1)
def load_student_answers() -> dict:
    """加载预置答题（20 名学生的逐题得分）。"""
    return _load_json(STUDENT_ANSWERS_FILE)
