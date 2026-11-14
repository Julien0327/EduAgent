"""受限域 NL 问数（技术方案 §4.1，Phase 2 / S4）。

**路线：不生成自由 SQL**——LLM 只抽取结构化参数（严格 Pydantic schema），
SQL 由模板 + 参数化绑定组装（复用 question_search 的白名单校验与两步法
查询）。注入面收口在两处：①LLM 输出经 Pydantic 白名单校验（非法值直接
拒绝）；②SQL 全占位符（用户文本永远只作为 MATCH/绑定参数，见 §4.1 安全）。

LLM 不可用/解析失败时**确定性回退**（正则抽难度/题量/学段 + 原文作检索词），
保证无 Key 环境可演示（与 planner 回退同纪律）。

总分约束不进 SQL（§4.1）：候选集选出后按题型分值权重贪心配平
（retrieval.balance.allocate_scores）。
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field

from backend.app.retrieval.question_search import FilterValidationError, validate_filters
from backend.app.retrieval.service import RetrievalService, get_retrieval_service

VALID_TYPES_CN = ("选择题", "填空题", "解答题")
TYPE_CN_TO_DB = {"选择题": "choice", "填空题": "fill", "解答题": "short"}


class BankQueryParams(BaseModel):
    """NL 问数参数（LLM 结构化输出 schema；§4.1 参数表）。"""

    query: str = Field(..., min_length=1, description="检索词：知识点或题面主题，如：分数加减应用题")
    stage: str | None = Field(default=None, description="学段：primary(小学)/junior(初中)/None=不限")
    difficulty: int | None = Field(default=None, ge=1, le=5, description="难度 1-5，None=不限")
    question_type: str | None = Field(
        default=None, description=f"题型：{'/'.join(VALID_TYPES_CN)}，None=不限")
    count: int = Field(default=10, ge=1, le=50, description="题目数量")
    total_score: int | None = Field(
        default=None, ge=1, description="目标总分；提供则对结果做分值配平")


def _deterministic_extract(text: str) -> BankQueryParams:
    """确定性回退：正则抽参数（无 LLM 时；宁可保守不编造）。"""
    count = 10
    m = re.search(r"(\d{1,2})\s*道", text)
    if m and 1 <= int(m.group(1)) <= 50:
        count = int(m.group(1))
    difficulty = None
    m = re.search(r"难度[为在]?\s*([1-5])", text)
    if m:
        difficulty = int(m.group(1))
    stage = None
    if "小学" in text:
        stage = "primary"
    elif "初中" in text:
        stage = "junior"
    question_type = None
    for cn in VALID_TYPES_CN:
        if cn in text:
            question_type = cn
            break
    total_score = None
    m = re.search(r"总分?\s*(\d{2,3})\s*分?", text)
    if m and 1 <= int(m.group(1)) <= 999:
        total_score = int(m.group(1))
    # 检索词：去掉指令词后的剩余文本（保守截取）
    query = re.sub(r"(找|给我|帮我|请|要|出|选|挑)[一-龥]{0,4}", "", text).strip() or text.strip()
    return BankQueryParams(query=query[:40], stage=stage, difficulty=difficulty,
                           question_type=question_type, count=count, total_score=total_score)


def extract_params(text: str) -> tuple[BankQueryParams, str]:
    """LLM 抽参（失败回退确定性）。返回 (参数, 来源 'llm'/'fallback')。"""
    from backend.app.agent.llm_utils import structured_completion

    system = (
        "你是题库查询参数解析器。从教师的自然语言需求中抽取查询参数："
        "query=要找的题目主题（必填，名词短语）；stage=学段（小学→primary/初中→junior/未提=空）；"
        "difficulty=难度1-5（未提=空）；question_type=题型（选择题/填空题/解答题，未提=空）；"
        f"count=题目数量1-50（默认10）；total_score=目标总分（未提=空）。"
        "务必调用 submit_bank_query 工具提交。"
    )
    try:
        result = structured_completion(system, text, "submit_bank_query", BankQueryParams)
        return result, "llm"
    except Exception:
        return _deterministic_extract(text), "fallback"


def query_question_bank(text: str, service: RetrievalService | None = None,
                        params: BankQueryParams | None = None) -> dict:
    """NL 问数主入口：抽参 → 白名单校验 → 三阶段检索 → 配平。

    返回 {questions(带 ref/rerank_score/kp_ids/score), params_used, params_source,
    allocation, meta}。
    """
    from backend.app.retrieval.balance import allocate_scores

    svc = service or get_retrieval_service()
    if params is None:
        params, source = extract_params(text)
    else:
        source = "direct"

    # §4.1 安全：LLM 参数再过一遍白名单（双保险；非法值在这里被拒）
    cn_type = params.question_type
    db_type = TYPE_CN_TO_DB.get(cn_type) if cn_type else None
    filters: dict = {}
    if params.stage:
        filters["stage"] = params.stage  # 合法值在 validate_filters 内校验
    if params.difficulty is not None:
        filters["difficulty"] = params.difficulty
    if db_type:
        filters["question_type"] = db_type
    try:
        validate_filters(filters)
    except FilterValidationError as exc:
        return {"error": f"查询参数非法：{exc}", "params_used": params.model_dump()}

    out = svc.search_questions(
        params.query, stage=params.stage, filters=filters or None,
        question_limit=params.count,
    )
    questions = out["questions"]

    result = {
        "questions": questions,
        "params_used": params.model_dump(),
        "params_source": source,
        "meta": out["meta"],
    }
    if params.total_score is not None and questions:
        result["allocation"] = allocate_scores(questions, params.total_score)
    return result
