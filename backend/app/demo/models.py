"""演示题库数据模型（Phase 6 / S2）。

- ``GeneratedQuestion``：LLM 工具调用返回的单题结构（短字段，理由/解析放 content
  之外时仍保留 solution 字段，但限制长度，避免非法 JSON）。
- ``GeneratedQuestionSet``：一个 KP 的 5 道题（难度 1–5）。
- ``DemoQuestion``：落盘与校验使用的内部结构，含来源与许可元数据。
- ``ReviewVerdict``：LLM 复核结论（题干完整性/条件充分性/答案唯一性/年级适配）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

QTYPE = Literal["选择题", "填空题", "解答题"]


class GeneratedQuestion(BaseModel):
    difficulty: int = Field(ge=1, le=5, description="难度 1–5")
    qtype: QTYPE
    stem: str = Field(min_length=4, max_length=300, description="完整题干，必须包含全部条件")
    options: list[str] = Field(default_factory=list, description="选择题选项；非选择题为空")
    answer: str = Field(min_length=1, max_length=80, description="最终答案（含单位）")
    answer_value: float | None = Field(default=None, description="数值答案；非数值题为空")
    unit: str | None = Field(default=None, max_length=10, description="单位；无单位为 None")
    equation: str | None = Field(
        default=None, max_length=200, description="可求值的算式，用于答案一致性校验"
    )
    solution: str = Field(min_length=2, max_length=300, description="简要解题过程")


class GeneratedQuestionSet(BaseModel):
    questions: list[GeneratedQuestion] = Field(
        min_length=5, max_length=5, description="难度 1–5 各一题，共 5 题"
    )


class ReviewVerdict(BaseModel):
    stem_complete: bool = Field(description="题干是否完整、无缺失条件")
    conditions_sufficient: bool = Field(description="条件是否足以推出唯一答案")
    answer_unique: bool = Field(description="答案是否唯一、无歧义")
    grade_fit: bool = Field(description="是否适合小学六年级")
    issues: list[str] = Field(default_factory=list, description="发现的问题，逐条简述")


@dataclass
class DemoQuestion:
    q_id: str
    kp_id: str
    kp_name: str
    difficulty: int
    qtype: str
    stem: str
    answer: str
    solution: str
    generation_method: str
    score: float = 10.0
    options: list[str] = field(default_factory=list)
    answer_value: float | None = None
    unit: str | None = None
    equation: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    seed: int | None = None
    source: str = "synthetic_demo_v1"
    license: str = "MIT"
    validation_status: str = "pending"
    validation_issues: list[str] = field(default_factory=list)
    review_notes: list[str] = field(default_factory=list)
    duplicate_of: str | None = None
    review_status: str = "pending"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DemoQuestion":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})
