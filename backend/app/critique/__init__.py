"""质检 critique 环（Phase 5，§8.1）。

本包只做**确定性规则层**（S1）+ 后续的 LLM 复核/回流节点（S2，checker.py）：
- ``models``：质检结论与规则问题（issue）的结构化模型；
- ``rules``：正确性 / 难度三维确定性规则 + 编排入口 ``run_rules``；
- ``dupcheck``：批内查重 + 历史题库查重（FTS 召回 + 相似度，不整库载入内存）。

确定性规则不依赖 LLM，必须尽可能独立执行（§8.2 降级语义）。
"""
from backend.app.critique.models import (
    BatchCritique, QuestionCritique, RuleIssue, DIMENSIONS,
    SEV_FAIL, SEV_WARN, STATUS_PASS, STATUS_FAIL,
)
from backend.app.critique.rules import run_rules
from backend.app.critique.checker import run_critique

__all__ = [
    "BatchCritique", "QuestionCritique", "RuleIssue", "DIMENSIONS",
    "SEV_FAIL", "SEV_WARN", "STATUS_PASS", "STATUS_FAIL",
    "run_rules", "run_critique",
]
