"""种子数据加载（知识点 / 题库 / 预置答题）。

从 data/ 下的 JSON 文件读取并缓存到内存，供文档服务与（后续）Agent 使用。
数据源是只读的种子数据，加载结果用 lru_cache 缓存。
"""
import json
from functools import lru_cache
from pathlib import Path

from backend.app.retrieval.citations import REF_SEED_PREFIX

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
    """加载题库（10 题，共 100 分，每题关联知识点 ID）。

    Phase 2/S2（§4.4）：凡题库取数必带 ref——种子题 ref = ``seed:<question_id>``
    （与 DB 题 q_id 明确区分，见 citations.py ref 形态约定）。缓存内共享 dict，
    注入在缓存前完成一次性处理。
    """
    bank = _load_json(QUESTION_BANK_FILE)
    for q in bank["questions"]:
        q.setdefault("ref", f"{REF_SEED_PREFIX}{q['question_id']}")
    return bank


@lru_cache(maxsize=1)
def load_student_answers() -> dict:
    """加载预置答题（20 名学生的逐题得分）。"""
    return _load_json(STUDENT_ANSWERS_FILE)


class SeedValidationError(Exception):
    """种子数据校验失败（文件缺失或 JSON 非法），供启动期 fail-fast。"""


def validate_seed() -> str:
    """校验三份种子数据的存在性与 JSON 合法性，返回摘要。

    任一文件缺失或 JSON 非法时抛 SeedValidationError（含文件名与原因），
    供应用启动阶段 fail-fast。校验只读，不修改 seed 数据，也不改变现有加载函数行为。
    """
    checks = (
        ("知识点", KNOWLEDGE_POINTS_FILE, "knowledge_points"),
        ("题库", QUESTION_BANK_FILE, "questions"),
        ("预置答题", STUDENT_ANSWERS_FILE, "students"),
    )
    counts: dict[str, int] = {}
    for label, path, list_key in checks:
        if not path.exists():
            raise SeedValidationError(f"种子数据文件缺失：{path}")
        try:
            data = _load_json(path)
        except (ValueError, OSError) as exc:
            raise SeedValidationError(f"种子数据 JSON 非法：{path}（{exc}）") from exc
        items = data.get(list_key) if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise SeedValidationError(f"种子数据缺少「{list_key}」列表：{path}")
        counts[label] = len(items)

    return (
        f"种子数据校验通过：{counts['知识点']} 个知识点 / "
        f"{counts['题库']} 道题 / {counts['预置答题']} 名学生"
    )
