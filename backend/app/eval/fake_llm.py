"""FakeLLM —— 评测 Fake 档的确定性语言模型替身（S2-A）。

**角色定位（用户裁决 2026-11-18）**：复刻"当前实现"的确定性规则引擎，用于验证
评测系统的执行链路；**不是**理想模型，也**不提供**模型能力成绩。

**与 Golden 完全独立（用户要求 1）**：
- 本模块不 import golden / eval_cases / expect，不读取任何 golden 数据；
- 所有响应从「收到什么(工具名+参数/系统提示+用户内容)」推导；
- 这就杜绝了 "FakeLLM 照抄标准答案导致 Runner 通过率虚高" 的伪阳性。

**行为规则（对应 planner / 各 submit 工具的真实逻辑）**：
- submit_plan   ：按 Planner 的确定性回退逻辑（fallback_plan 同款关键字规则）推工作流与参数；
                   返回一个**合法** PlanParams（只在 `force_invalid_plan` 时返回非法值以测 Planner 校验重试）；
- submit_questions：按 count 生成 count 条结构化题目（确定性内容）；
- submit_diagnosis：按 stats/薄弱点 生成 3 条建议（确定性内容）；
- submit_lesson_plan：按已检索知识点生成教学目标/重难点/过程/作业（确定性内容）；
- 其余未识别工具：返回空 tool_calls（视作模型未调用 → 触发 structured_completion 的
   "未按要求调用" 重试，可注入到测试中测 Planner 重试路径）。

**可注入性**：
- `FakeLLM(seed=...)`：同一 seed 的响应确定性一致（可重复性验证的重心）；
- 提供 `snapshot()` 返回本轮全部调用记录（供断言 FakeLLM 未接触 golden）。
"""
from __future__ import annotations

import re
from typing import Literal

from backend.app.agent.llm_client import LLMResponse


def _extract_chapter(text: str) -> str:
    """与 backend/app/agent/planner.py 同一「《…》」抽取规则（防语义漂移）。"""
    m = re.search(r"《(.+?)》", text)
    return m.group(1) if m else ""


DEFAULT_CHAPTER = "分数与百分数"          # FakeLLM 默认章节（合法小学值，非遗留初中值）
DEFAULT_TOTAL_SCORE = 100
DEFAULT_COUNT = 10
MAX_QUESTIONS = 12                        # 模型命制数量上限（gc_b24 裁定：只回 ≤12）
_DIFFICULTIES = ("简单", "中等", "较难", "混合")


def _find_plan_workflow(prompt: str) -> dict:
    """复刻 planner.fallback_plan 的关键字规则（决策：FakeLLM=复刻当前实现）。"""
    if "教案" in prompt or "教学设计" in prompt:
        return {"workflow": "lesson_plan", "topic": _extract_chapter(prompt) or DEFAULT_CHAPTER}
    if ("分析" in prompt) or ("报告" in prompt) or ("学情" in prompt):
        return {"workflow": "exam_loop", "topic": ""}
    return {"workflow": "question_gen", "topic": ""}


def _parse_count(prompt: str, user: str = "") -> int:
    m = re.search(r"(\d+)\s*道", prompt)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 20:
            return n
        # 越界：复刻 Planner 的 Pydantic 校验拒绝 → 返回默认（Planner 会回退）
        return DEFAULT_COUNT
    return DEFAULT_COUNT


def _parse_difficulty(user: str) -> str:
    for d in _DIFFICULTIES:
        if d in user:
            return d
    return "混合"


def _parse_question_types(prompt: str) -> list[str]:
    types = []
    for t in ("选择题", "填空题", "解答题"):
        if t in prompt:
            types.append(t)
    return types or ["选择题", "填空题", "解答题"]


def plan_arguments(prompt: str) -> dict:
    """由用户 prompt 推导 TaskPlan.params（对应 planner.py 的确定性抽取 + 默认值）。"""
    wf = _find_plan_workflow(prompt)
    chapter = _extract_chapter(prompt) or DEFAULT_CHAPTER
    return {
        "subject": "数学",
        "chapter": chapter,
        "count": _parse_count(prompt),
        "difficulty": _parse_difficulty(prompt),
        "question_types": _parse_question_types(prompt),
        "total_score": DEFAULT_TOTAL_SCORE,
        "topic": wf.get("topic", ""),
    }


class FakeLLM:
    """确定性 LLM 替身：实现 chat_with_tools，挂到 structured_completion.get_llm_service。"""

    def __init__(self, seed: int = 0, force_invalid_plan: bool = False):
        self.seed = seed
        self.force_invalid_plan = force_invalid_plan
        self.calls: list[dict] = []      # 记录（调试可重复性）

    # ---- chat_with_tools 是 structured_completion 的唯一调用点 ----
    def chat_with_tools(self, messages, tools=None, tool_choice="auto") -> LLMResponse:
        tool_name = ""
        if tools:
            first = tools[0] if isinstance(tools, (list, tuple)) else tools
            if isinstance(first, dict):
                fn = first.get("function") or first
                tool_name = fn.get("name", "") if isinstance(fn, dict) else ""
        self.calls.append({"tool": tool_name, "messages": messages})
        # 把 user 消息（最后一条）拼接，作为推导输入
        user = "\n".join(
            m.get("content", "") for m in messages if m.get("role") == "user"
        )
        if tool_name == "submit_plan":
            return self._resp_submit_plan(user)
        if tool_name == "submit_questions":
            return self._resp_submit_questions(user)
        if tool_name == "submit_diagnosis":
            return self._resp_submit_diagnosis(user)
        if tool_name == "submit_lesson_plan":
            return self._resp_submit_lesson_plan(user)
        if tool_name == "submit_verify":
            return self._resp_submit_verify(user)
        if tool_name == "submit_corrected_question":
            return self._resp_submit_corrected_question(user)
        return LLMResponse(content="", model="fake", tool_calls=[])

    def _resp_submit_plan(self, user: str) -> LLMResponse:
        if self.force_invalid_plan:
            # 触发 Planner 的 Pydantic 校验重试（测试用：正常 runner 不设此项）
            return self._tool_call("submit_plan", {"chapter": ""})
        return self._tool_call("submit_plan", {"workflow": _find_plan_workflow(user)["workflow"],
                                               "params": plan_arguments(user)})

    def _resp_submit_questions(self, user: str) -> LLMResponse:
        """按请求 count 生成确定性题目（test_workflow 同款的 q{id} 命名）。

        确定性上限 MAX_QUESTIONS=12：仅对请求 count≥20（越界上界）生效——模拟
        "当前模型命制高量题目的数量上限"（gc_b24 请求 20 → 回 12 → 工具返回数量
        不足 → 任务失败）。正常的 count<20（含 15/18）全部照常生成，不误伤合法
        用例。规则独立于 golden，复刻现状（非理想模型）。
        """
        count = DEFAULT_COUNT
        m = re.search(r"(\d+)\s*道(?:练习题|题)?", user)
        if m:
            count = int(m.group(1))
        if count >= 20:
            count = MAX_QUESTIONS   # 模拟高量命制上限（gc_b24 预期失败）
        questions = []
        for i in range(1, count + 1):
            qtype = ("填空题", "解答题", "选择题")[i % 3]
            questions.append({
                "type": qtype,
                "stem": f"Fake 出的第 {i} 道题（确定性）",
                "options": ["A. 1", "B. 2", "C. 3", "D. 4"] if qtype == "选择题" else None,
                "answer": str(i),
                "difficulty": "简单",
                "analysis": "Fake 解析",
            })
        if not questions:
            return LLMResponse(content="", model="fake", tool_calls=[])
        return self._tool_call("submit_questions", {"questions": questions})

    def _resp_submit_diagnosis(self, user: str) -> LLMResponse:
        return self._tool_call("submit_diagnosis", {
            "summary": "班级整体成绩居中，部分知识点掌握不牢。",
            "diagnosis": "薄弱知识点需要针对性巩固。",
            "suggestions": ["加强基础训练", "分层布置作业", "定期回顾错题"],
        })

    def _resp_submit_lesson_plan(self, user: str) -> LLMResponse:
        return self._tool_call("submit_lesson_plan", {
            "objectives": ["掌握基础知识", "会解决典型问题", "培养迁移能力"],
            "key_points": "核心概念与方法",
            "difficult_points": "难点突破策略",
            "process": [
                {"step": "导入", "detail": "情境引入"},
                {"step": "新授", "detail": "讲解例题"},
                {"step": "练习", "detail": "随堂练习"},
            ],
            "homework": "完成课后练习题",
        })

    def _resp_submit_verify(self, user: str) -> LLMResponse:
        """质检一题一验：确定性默认通过（eval fake 档验证链路，非模型能力）。"""
        return self._tool_call("submit_verify", {
            "verdict": "pass", "reasons": [],
            "difficulty_fit": True, "suggested_difficulty": None,
        })

    def _resp_submit_corrected_question(self, user: str) -> LLMResponse:
        """携带质检原因重生成：确定性回显一道可解析题（eval fake 档）。"""
        return self._tool_call("submit_corrected_question", {
            "type": "填空题", "stem": "Fake 修正后的题（确定性）", "options": None,
            "answer": "1", "difficulty": "简单", "analysis": "Fake 修正解析",
        })

    def _tool_call(self, name: str, arguments: dict) -> LLMResponse:
        return LLMResponse(
            content="", model=f"fake-seed{self.seed}",
            tool_calls=[{"id": f"fake-{self.seed}", "name": name, "arguments": arguments}],
        )

    def snapshot(self) -> dict:
        return {"seed": self.seed, "calls": list(self.calls), "contacted_golden": False}