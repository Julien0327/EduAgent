"""质检结论结构化模型（S1，§8.1）。

三维：correctness（正确性）/ difficulty（难度）/ duplicate（查重）。
每维产出 RuleIssue（severity=fail/warn + 规则码 + 中文说明 + 明细），
总裁定：任一 fail 级 issue → 该题 status=fail，否则 pass。

S2 将在此基础上追加：LLM 一题一验结果、回流轮次、retired 状态、三态降级
（completed/partial/skipped）。模型保持向前兼容，新增字段不破坏 S1 语义。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

# ---- 维度 ----
DIM_CORRECTNESS = "correctness"
DIM_DIFFICULTY = "difficulty"
DIM_DUPLICATE = "duplicate"
DIMENSIONS = (DIM_CORRECTNESS, DIM_DIFFICULTY, DIM_DUPLICATE)

# ---- 严重度 ----
SEV_FAIL = "fail"
SEV_WARN = "warn"

# ---- 总裁定 ----
STATUS_PASS = "pass"
STATUS_FAIL = "fail"


@dataclass
class RuleIssue:
    """单条质检问题（规则命中）。"""

    dimension: str
    severity: str           # fail / warn
    code: str               # 机器可读规则码，如 correctness_answer_empty
    message: str            # 中文说明
    details: dict = field(default_factory=dict)


@dataclass
class QuestionCritique:
    """单题质检结论。"""

    index: int
    question_id: str
    status: str = STATUS_PASS
    issues: list[RuleIssue] = field(default_factory=list)

    @property
    def has_fail(self) -> bool:
        return any(i.severity == SEV_FAIL for i in self.issues)

    def add_issue(self, dimension: str, severity: str, code: str,
                  message: str, **details) -> None:
        self.issues.append(RuleIssue(dimension, severity, code, message, details))


@dataclass
class BatchCritique:
    """一批题目的确定性质检结果。

    ``status``：S1 恒为 ``completed``（规则层总是完整执行）；S2 引入
    partial/skipped 三态降级。
    ``duplicate_pairs``：批内重复对（index_a/index_b/ratio/ids）。
    ``history_matches``：与历史题库相似命中（index/q_id/stem/ratio/…）。
    """

    status: str = "completed"
    questions: list[QuestionCritique] = field(default_factory=list)
    duplicate_pairs: list[dict] = field(default_factory=list)
    history_matches: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        total = len(self.questions)
        passed = sum(1 for qc in self.questions if qc.status == STATUS_PASS)
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "duplicate_pairs_batch": len(self.duplicate_pairs),
            "history_matches": len(self.history_matches),
        }

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "summary": self.summary(),
            "questions": [
                {
                    "index": qc.index,
                    "question_id": qc.question_id,
                    "status": qc.status,
                    "issues": [asdict(i) for i in qc.issues],
                }
                for qc in self.questions
            ],
            "duplicate_pairs": self.duplicate_pairs,
            "history_matches": self.history_matches,
        }
