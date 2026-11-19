"""质检回流 checker 单测（S2，纯离线，零真实 API）。

覆盖：LLM 一题一验（结构化 Schema）/ 结构化返回异常 / LLM 超时降级 /
回流重生成成功 / 两轮后剔除 / 全剔除 / partial 与 skipped 三态 /
分值重分配 / 报告字段。工作流级「全部剔除→任务失败」在 test_workflow.py。
"""
from __future__ import annotations

import pytest

import backend.app.agent.llm_utils as llm_utils
from backend.app.agent.llm_client import LLMError, LLMResponse
from backend.app.critique import checker

# ---------- 样例构造 ----------
def _q(i: int = 0, **kw) -> dict:
    q = {"question_id": f"q{i + 1}", "type": "选择题",
         "stem": f"这是一道用于质检测试的题目{i + 1}",
         "options": ["A. 1", "B. 2", "C. 3", "D. 4"], "answer": "A",
         "difficulty": "简单", "analysis": "", "score": 10,
         "knowledge_points": ["kp_p_0001"]}
    q.update(kw)
    return q


_CORRECTED = {"type": "填空题", "stem": "修正后的题目（有足够长度）", "options": None,
              "answer": "2", "difficulty": "简单", "analysis": ""}


class QueueLLM:
    """按序返回预设响应；耗尽后返回空（无工具调用）。"""

    def __init__(self, responses):
        self.responses = list(responses)

    def chat_with_tools(self, messages, tools=None, tool_choice="auto"):
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(content="", tool_calls=[], model="fake")


def _verify_llm(verdict, reasons) -> LLMResponse:
    return LLMResponse(content="", model="fake", tool_calls=[{
        "id": "v", "name": "submit_verify",
        "arguments": {"verdict": verdict, "reasons": reasons,
                      "difficulty_fit": True, "suggested_difficulty": None},
    }])


def _corrected_llm() -> LLMResponse:
    return LLMResponse(content="", model="fake", tool_calls=[{
        "id": "c", "name": "submit_corrected_question", "arguments": dict(_CORRECTED),
    }])


# ===================== LLM 路径（structured_completion 真实走通） =====================
def test_verify_question_pass(monkeypatch):
    monkeypatch.setattr(llm_utils, "get_llm_service", lambda: QueueLLM([_verify_llm("pass", [])]))
    v = checker.verify_question(_q(0), "分数运算")
    assert v.verdict == "pass"
    assert v.reasons == []


def test_verify_question_fail_with_reasons(monkeypatch):
    monkeypatch.setattr(llm_utils, "get_llm_service",
                        lambda: QueueLLM([_verify_llm("fail", ["答案计算错误"])]))
    v = checker.verify_question(_q(0), "分数运算")
    assert v.verdict == "fail"
    assert v.reasons == ["答案计算错误"]


def test_verify_question_invalid_structure_raises(monkeypatch):
    """结构化返回异常：模型不返回 submit_verify → 重试耗尽 → LLMError。"""
    monkeypatch.setattr(llm_utils, "get_llm_service", lambda: QueueLLM([]))
    with pytest.raises(LLMError):
        checker.verify_question(_q(0), "分数运算")


def test_regenerate_question_returns_corrected(monkeypatch):
    monkeypatch.setattr(llm_utils, "get_llm_service", lambda: QueueLLM([_corrected_llm()]))
    d = checker.regenerate_question(_q(0), ["答案错误"], "分数运算")
    assert d["type"] == "填空题"
    assert d["answer"] == "2"


def test_try_verify_catches_llmerror(monkeypatch):
    """LLM 超时/异常 → _try_verify 返回不可用（不向上抛），供降级使用。"""
    def _boom(*a, **k):
        raise LLMError("模型请求超时，请稍后重试")

    monkeypatch.setattr(checker, "verify_question", _boom)
    ok, verdict, reasons, df, sugg = checker._try_verify(_q(0), "分数运算")
    assert ok is False
    assert verdict is None
    assert reasons[0] == "模型请求超时，请稍后重试"


# ===================== run_critique 回流循环（patch _try_verify/_try_regenerate） =====================
def _patch_verify(monkeypatch, results):
    it = iter(results)

    def _tv(*a, **k):
        return next(it)

    monkeypatch.setattr(checker, "_try_verify", _tv)


def _patch_regen(monkeypatch, results):
    it = iter(results)

    def _tr(*a, **k):
        return next(it)

    monkeypatch.setattr(checker, "_try_regenerate", _tr)


def test_run_critique_all_pass(monkeypatch):
    _patch_verify(monkeypatch, [(True, "pass", [], True, None),
                                (True, "pass", [], True, None)])
    out = checker.run_critique([_q(0), _q(1)], kp_context="分数运算", total_score=100)
    assert out["status"] == "completed"
    assert out["report"]["summary"]["passed"] == 2
    assert len(out["questions"]) == 2
    assert out["questions"][0]["question_id"] == "q1"
    assert sum(q["score"] for q in out["questions"]) == 100


def test_run_critique_regen_success(monkeypatch):
    """首轮不合格 → 携带原因重生成 → 第二轮通过。"""
    _patch_verify(monkeypatch, [(True, "fail", ["答案错误"], True, None),
                                (True, "pass", [], True, None)])
    _patch_regen(monkeypatch, [(True, dict(_CORRECTED))])
    out = checker.run_critique([_q(0)], kp_context="分数运算", total_score=50)
    assert out["status"] == "completed"
    q = out["report"]["questions"][0]
    assert q["status"] == "passed"
    assert q["rounds"] == 2
    assert q["first_round_fail"] is True
    assert len(out["questions"]) == 1


def test_run_critique_two_rounds_retire(monkeypatch):
    """连续两轮重生成仍不合格 → 剔除。"""
    _patch_verify(monkeypatch, [(True, "fail", ["错误1"], True, None),
                                (True, "fail", ["错误2"], True, None),
                                (True, "fail", ["错误3"], True, None)])
    _patch_regen(monkeypatch, [(True, dict(_CORRECTED)), (True, dict(_CORRECTED))])
    out = checker.run_critique([_q(0)], kp_context="分数运算")
    assert out["status"] == "completed"
    assert out["report"]["summary"]["retired"] == 1
    assert out["report"]["questions"][0]["status"] == "retired"
    assert out["report"]["questions"][0]["rounds"] == 3
    assert out["report"]["gaps"][0]["question_id"] == "q1"
    assert out["questions"] == []   # 全部剔除 → 空
    s = out["report"]["summary"]
    assert s["total_fail_verdicts"] == 3          # 三轮均判不合格
    assert s["first_round_fail_eventually_passed"] == 0   # 回流失败


def test_run_critique_partial(monkeypatch):
    """q1 复核通过、q2 复核时 LLM 不可用 → partial；q2 未复核不标完整通过。"""
    _patch_verify(monkeypatch, [(True, "pass", [], True, None),
                                (False, None, ["LLM 不可用"], None, None)])
    out = checker.run_critique([_q(0), _q(1)], kp_context="分数运算")
    assert out["status"] == "partial"
    s = out["report"]["summary"]
    assert s["passed"] == 1 and s["unverified"] == 1 and s["retired"] == 0
    assert out["report"]["questions"][1]["status"] == "unverified"
    assert len(out["questions"]) == 2   # unverified 仍保留（降级不阻塞）


def test_run_critique_skipped(monkeypatch):
    """LLM 自始不可用 → skipped；确定性规则仍执行，确定性 fail 剔除、pass 未复核。"""
    _patch_verify(monkeypatch, [(False, None, ["超时"], None, None),
                                (False, None, ["超时"], None, None)])
    out = checker.run_critique([_q(0), _q(1)], kp_context="分数运算")
    assert out["status"] == "skipped"
    assert out["report"]["summary"]["unverified"] == 2
    assert len(out["questions"]) == 2


def test_unverified_history_records_llm_error(monkeypatch):
    """可追溯性：未复核题必须保存 LLM 校验失败文本（区分网络失败/结构化校验失败）。"""
    msg = "结构化输出在 3 次尝试后仍不合法：verdict 不是 pass/fail"
    _patch_verify(monkeypatch, [(False, None, [msg], None, None)])
    out = checker.run_critique([_q(0)], kp_context="分数运算")
    h = out["report"]["questions"][0]["history"][0]
    assert h["llm_available"] is False
    assert h["llm_error"] == msg
    assert out["report"]["questions"][0]["status"] == "unverified"


# ===================== 预算（Phase 5 / S4） =====================
def test_run_critique_time_budget_zero_skips_llm(monkeypatch):
    """时间预算为 0：不发起任何 LLM 调用，确定性 pass → unverified，整体 skipped。"""
    called = []

    def _tv(*a, **k):
        called.append(1)
        return True, "pass", [], True, None

    monkeypatch.setattr(checker, "_try_verify", _tv)
    out = checker.run_critique([_q(0), _q(1)], kp_context="分数运算",
                               time_budget_seconds=0)
    assert called == []
    assert out["status"] == "skipped"
    assert out["report"]["budget"]["budget_exhausted"] is True
    assert all(q["status"] == "unverified" for q in out["report"]["questions"])
    assert out["report"]["summary"]["verified_questions"] == 0
    assert len(out["questions"]) == 2          # 确定性 pass 仍保留在卷面


def test_run_critique_budget_zero_deterministic_fail_retires(monkeypatch):
    """预算为 0 + 确定性硬错误（空答案）→ 剔除；其余 unverified。"""
    monkeypatch.setattr(checker, "_try_verify", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("预算耗尽时不应调用 LLM")))
    out = checker.run_critique([_q(0, answer=""), _q(1)], kp_context="分数运算",
                               time_budget_seconds=0)
    assert out["status"] == "skipped"
    assert out["report"]["questions"][0]["status"] == "retired"
    assert out["report"]["questions"][1]["status"] == "unverified"
    assert out["report"]["budget"]["budget_exhausted"] is True


def test_run_critique_operation_budget_partial(monkeypatch):
    """操作预算=1：第 1 题完成复核后耗尽，第 2 题不再调用 LLM；整体 partial。"""
    _patch_verify(monkeypatch, [(True, "pass", [], True, None)])
    out = checker.run_critique([_q(0), _q(1)], kp_context="分数运算",
                               max_llm_operations=1)
    assert out["status"] == "partial"
    assert out["report"]["budget"]["budget_exhausted"] is True
    assert out["report"]["budget"]["llm_operations"] == 1
    statuses = [q["status"] for q in out["report"]["questions"]]
    assert statuses == ["passed", "unverified"]


def test_structured_completion_deadline_blocks_call(monkeypatch):
    """deadline 已过：structured_completion 直接抛 LLMError，不发起 LLM 调用。"""
    import time as _time
    import backend.app.agent.llm_utils as llmu

    called = []

    class _L:
        def chat_with_tools(self, *a, **k):  # pragma: no cover
            called.append(1)
            return None

    monkeypatch.setattr(llmu, "get_llm_service", lambda: _L())
    with pytest.raises(LLMError):
        llmu.structured_completion("s", "u", "submit_verify",
                                   checker.QuestionVerification,
                                   deadline=_time.monotonic() - 1)
    assert called == []


def test_run_critique_skipped_with_deterministic_fail(monkeypatch):
    """LLM 不可用 + 确定性硬错误（空答案）→ 该题剔除；其余未复核。"""
    _patch_verify(monkeypatch, [(False, None, ["超时"], None, None)])
    bad = _q(0, answer="")  # 确定性 fail（答案为空）
    out = checker.run_critique([bad], kp_context="分数运算")
    assert out["status"] == "skipped"
    assert out["report"]["summary"]["retired"] == 1
    assert out["questions"] == []


def test_run_critique_report_fields(monkeypatch):
    _patch_verify(monkeypatch, [(True, "pass", [], True, None)])
    out = checker.run_critique([_q(0)], kp_context="分数运算")
    r = out["report"]
    assert r["status"] == "completed"
    assert r["history_dedup"] == "skipped"   # conn=None
    assert set(r["summary"]) >= {"total", "passed", "retired", "unverified",
                                 "first_round_fail", "regenerated",
                                 "total_verification_rounds"}
    assert "gaps" in r and "questions" in r


def test_run_critique_keeps_knowledge_points_after_regen(monkeypatch):
    """重生成后保留原题 knowledge_points / question_id。"""
    _patch_verify(monkeypatch, [(True, "fail", ["答案错误"], True, None),
                                (True, "pass", [], True, None)])
    _patch_regen(monkeypatch, [(True, dict(_CORRECTED))])
    out = checker.run_critique([_q(0)], kp_context="分数运算")
    assert out["questions"][0]["knowledge_points"] == ["kp_p_0001"]


def test_run_critique_raw_metric_fields(monkeypatch):
    """补齐的原始统计字段：全轮拦截率分子 / 回流成功率分子（评测层直接聚合，不反推）。"""
    _patch_verify(monkeypatch, [(True, "fail", ["答案错误"], True, None),
                                (True, "pass", [], True, None)])
    _patch_regen(monkeypatch, [(True, dict(_CORRECTED))])
    out = checker.run_critique([_q(0)], kp_context="分数运算")
    s = out["report"]["summary"]
    assert s["total"] == 1
    assert s["first_round_fail"] == 1
    assert s["first_round_fail_eventually_passed"] == 1   # 回流成功
    assert s["total_fail_verdicts"] == 1                  # 仅第 0 轮判不合格
    assert s["total_verification_rounds"] == 2            # 送检 2 轮
    assert s["retired"] == 0 and s["unverified"] == 0


def test_run_critique_verified_questions_field(monkeypatch):
    """verified_questions：至少完成一次 LLM 复核的题数（全复核 / 部分 / 全未复核）。"""
    # 全部完成复核
    _patch_verify(monkeypatch, [(True, "pass", [], True, None),
                                (True, "pass", [], True, None)])
    out = checker.run_critique([_q(0), _q(1)], kp_context="分数运算")
    assert out["report"]["summary"]["verified_questions"] == 2

    # 部分复核（q1 通过、q2 LLM 不可用 → unverified）
    _patch_verify(monkeypatch, [(True, "pass", [], True, None),
                                (False, None, ["不可用"], None, None)])
    out = checker.run_critique([_q(0), _q(1)], kp_context="分数运算")
    s = out["report"]["summary"]
    assert s["verified_questions"] == 1 and s["unverified"] == 1

    # 全部未复核（skipped）
    _patch_verify(monkeypatch, [(False, None, ["超时"], None, None)])
    out = checker.run_critique([_q(0)], kp_context="分数运算")
    assert out["report"]["summary"]["verified_questions"] == 0
