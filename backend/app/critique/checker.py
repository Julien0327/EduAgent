"""质检 critique 环：LLM 复核 + 回流重生成（S2，§8.1）。

流程（每题）：
1. 完整确定性规则（正确性/难度/历史查重，LLM 无关）——**每轮重生成后完整重检**，
   不是只复查之前失败的维度；
2. LLM 一题一验（结构化 Schema，submit_verify）；
3. 不合格（确定性 fail 或 LLM verdict=fail）→ 携带具体质检原因重生成（≤2 轮）；
4. 仍不合格 → 剔除并记录缺口与原因；
5. 剔除后重新分配分值；全部剔除 → 任务显式失败（由工作流节点判定）。

三态降级（completed / partial / skipped）：
- LLM 不可用（超时/结构化异常 → LLMError）时**确定性规则仍执行**；
- 确定性 fail 且无法重生成 → 剔除；确定性 pass 但 LLM 未复核 → ``unverified``
  （保留在卷面但**不标记为完整通过**）；
- 部分结果已产生则保留，不因后续某题异常丢弃先前结果。
"""
from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

from backend.app.agent.llm_client import LLMError
from backend.app.agent.llm_utils import (
    structured_completion,
    structured_completion_with_content,
)
from backend.app.critique.dupcheck import batch_duplicates, DUP_BATCH_HARD
from backend.app.critique.models import SEV_FAIL
from backend.app.critique.rules import run_rules
from backend.app.retrieval.balance import allocate_scores

MAX_REGENERATION_ROUNDS = 2   # 最多重生成 2 轮（共 3 次送检）
DEFAULT_TIME_BUDGET_SECONDS = 120.0   # critique 节点墙钟预算（工作流总上限 180s 内留余量）
DEFAULT_MAX_LLM_OPERATIONS = 60       # 高端操作上限（一次 verify/regenerate 记 1 次）
BUDGET_EXHAUSTED_MSG = "critique 时间/操作预算耗尽，未执行 LLM 复核"


# ===================== LLM 结构化 Schema =====================
class QuestionVerification(BaseModel):
    """一题一验结论（**工具参数只放短字段**）。

    Phase 5 / S4 真实复现：模型会把长篇理由写进 `reasons` 且漏引号，导致工具参数
    不是合法 JSON。因此理由移出参数、放在模型正文 content（见 verify_question），
    参数只保留可稳定序列化的短字段。
    """

    verdict: Literal["pass", "fail"]
    difficulty_fit: bool = True
    suggested_difficulty: Literal["简单", "中等", "较难"] | None = None


class CorrectedQuestion(BaseModel):
    """按质检原因重命制的题目（结构与 RawQuestion 对齐）。"""

    type: Literal["选择题", "填空题", "解答题"]
    stem: str = Field(min_length=1)
    options: list[str] | None = None
    answer: str = Field(min_length=1)
    difficulty: Literal["简单", "中等", "较难"]
    analysis: str = ""


# ===================== 提示词与渲染 =====================
def _render_question(q: dict) -> str:
    lines = [f"题型：{q.get('type', '')}", f"题干：{q.get('stem', '')}"]
    if q.get("options"):
        lines.append("选项：" + " ".join(q["options"]))
    lines.append(f"答案：{q.get('answer', '')}")
    if q.get("analysis"):
        lines.append(f"解析：{q.get('analysis', '')}")
    lines.append(f"难度：{q.get('difficulty', '')}")
    return "\n".join(lines)


_VERIFY_SYSTEM = (
    "你是小学数学题质检员，逐题校验：①答案是否自洽、无知识性错误、无计算错误；"
    "②难度档位（简单/中等/较难）是否与题干相符。若任一有问题判 fail，否则判 pass。"
    "复核理由**直接写在回复正文（content）中**；调用 submit_verify 时**只提交**"
    "verdict（pass/fail）、difficulty_fit（true/false）、suggested_difficulty"
    "（简单/中等/较难或 null）三个短字段，**不要在参数中包含 reasons 或任何长文本**。"
)

_REGEN_SYSTEM = (
    "你是小学数学教师。根据质检原因重新命制这道题，修正指出的问题，"
    "保持题型与知识点不变。务必调用 submit_corrected_question 工具提交。"
)


# ===================== LLM 调用（每题一验 / 携带原因重生成） =====================
def verify_question(question: dict, kp_context: str,
                    deadline: float | None = None) -> tuple[QuestionVerification, str]:
    """返回 (结构化结论, 模型正文 content)。content 用作 fail 时的复核理由。"""
    user = f"知识点：{kp_context}\n题目：\n{_render_question(question)}"
    verification, content = structured_completion_with_content(
        _VERIFY_SYSTEM, user, "submit_verify", QuestionVerification, deadline=deadline)
    return verification, content or ""


def regenerate_question(question: dict, reasons: list[str], kp_context: str,
                        deadline: float | None = None) -> dict:
    reasons_text = "；".join(reasons)
    user = (f"知识点：{kp_context}\n原题：\n{_render_question(question)}\n"
            f"质检原因：{reasons_text}")
    corrected = structured_completion(_REGEN_SYSTEM, user,
                                      "submit_corrected_question", CorrectedQuestion,
                                      deadline=deadline)
    return corrected.model_dump()


def _try_verify(current: dict, kp_context: str, deadline: float | None = None):
    """返回 (llm_ok, verdict, reasons, difficulty_fit, suggested_difficulty)。

    reasons 来自模型正文 content（fail 时用于携带原因重生成）；pass 时为空。
    """
    try:
        v, content = verify_question(current, kp_context, deadline=deadline)
        reasons = []
        if v.verdict == "fail":
            reasons = [content.strip()] if content.strip() else ["LLM 复核判定不合格（未提供理由）"]
        return True, v.verdict, reasons, v.difficulty_fit, v.suggested_difficulty
    except LLMError as exc:
        return False, None, [str(exc)], None, None


def _try_regenerate(current: dict, reasons: list[str], kp_context: str,
                    deadline: float | None = None):
    """返回 (ok, corrected_question_dict)。"""
    try:
        return True, regenerate_question(current, reasons, kp_context, deadline=deadline)
    except LLMError:
        return False, None


def _merge_regenerated(orig: dict, corrected: dict) -> dict:
    """重生成的题目继承原题的 question_id / knowledge_points；score 稍后统一重分配。"""
    merged = dict(corrected)
    merged["question_id"] = orig.get("question_id")
    merged["knowledge_points"] = orig.get("knowledge_points", [])
    merged.pop("score", None)
    return merged


# ===================== 主循环 =====================
def _deterministic_only(current: dict, conn, rnd: int = 0, error: str = BUDGET_EXHAUSTED_MSG):
    """预算耗尽时的确定性收尾：不调用 LLM，仅跑规则层。

    返回 (disposition, final_q, reasons, history_entry)。
    """
    det = run_rules([current], conn)
    qc = det.questions[0]
    det_fail = qc.has_fail
    det_reasons = [iss.message for iss in qc.issues if iss.severity == SEV_FAIL]
    hist = {"round": rnd, "llm_available": False, "llm_error": error,
            "deterministic_fail": det_fail,
            "deterministic_fail_reasons": det_reasons}
    if det_fail:
        return "retired", None, det_reasons, hist
    return "unverified", current, [], hist


def run_critique(questions: list[dict], kp_context: str = "",
                 conn=None, total_score: int = 100,
                 time_budget_seconds: float | None = DEFAULT_TIME_BUDGET_SECONDS,
                 max_llm_operations: int | None = DEFAULT_MAX_LLM_OPERATIONS) -> dict:
    """对一批生成题执行完整质检回流。返回 {status, report, questions}。

    ``questions``：generate_questions 产出的题目 dict 列表。
    ``conn``：只读业务库连接（历史查重用；None 则跳过历史查重）。
    ``time_budget_seconds`` / ``max_llm_operations``：critique 节点预算（Phase 5 /
    S4）。到点/到量后**不再发起新 LLM 调用**：剩余题只跑确定性规则，
    确定性 fail → retired，否则 unverified；整体转 partial/skipped，
    保留已完成结果，避免击穿工作流 180s 上限。
    ``questions`` 返回值：最终幸存题目（已重编号、重分配分值）；空 = 全部剔除。
    """
    started = time.monotonic()
    deadline = (started + time_budget_seconds) if time_budget_seconds is not None else None
    llm_ops = 0
    budget_exhausted = False
    llm_ok_any = False
    llm_err_any = False

    def _budget_hit() -> bool:
        nonlocal budget_exhausted
        if deadline is not None and time.monotonic() >= deadline:
            budget_exhausted = True
            return True
        if max_llm_operations is not None and llm_ops >= max_llm_operations:
            budget_exhausted = True
            return True
        return False

    entries: list[dict] = []
    survivors: list[dict] = []     # [{"entry_index": int, "q": dict}]

    for i, q in enumerate(questions):
        qid = q.get("question_id", f"q{i + 1}")
        current = q
        history: list[dict] = []
        disposition = None
        final_q = None
        reasons_final: list[str] = []

        if _budget_hit():
            # 预算已在上一题耗尽：本批剩余题只跑确定性规则
            disposition, final_q, reasons_final, hist = _deterministic_only(current, conn)
            history.append(hist)
        else:
            for rnd in range(MAX_REGENERATION_ROUNDS + 1):   # 0,1,2
                # 1. 完整确定性规则（正确性 + 难度 + 历史查重；LLM 无关，每轮完整重检）
                det = run_rules([current], conn)
                qc = det.questions[0]
                det_fail = qc.has_fail
                det_fail_reasons = [iss.message for iss in qc.issues if iss.severity == SEV_FAIL]

                # 预算检查：到点/到量后不再发起新的 LLM 调用
                if _budget_hit():
                    history.append({"round": rnd, "llm_available": False,
                                    "llm_error": BUDGET_EXHAUSTED_MSG,
                                    "deterministic_fail": det_fail,
                                    "deterministic_fail_reasons": det_fail_reasons})
                    if det_fail:
                        disposition, reasons_final = "retired", det_fail_reasons
                    else:
                        disposition, final_q = "unverified", current
                    break

                # 2. LLM 一题一验
                llm_ok, verdict, llm_reasons, difficulty_fit, suggested = _try_verify(
                    current, kp_context, deadline=deadline)
                llm_ops += 1
                llm_ok_any = llm_ok_any or llm_ok
                llm_err_any = llm_err_any or (not llm_ok)

                if not llm_ok:
                    # LLM 不可用：确定性规则已执行；确定性 fail 无法修复 → 剔除；否则未复核。
                    # 必须保存 LLM 校验失败文本（可追溯：区分网络失败/结构化校验失败）。
                    history.append({"round": rnd, "llm_available": False,
                                    "llm_error": llm_reasons[0] if llm_reasons else "",
                                    "deterministic_fail": det_fail,
                                    "deterministic_fail_reasons": det_fail_reasons})
                    if det_fail:
                        disposition, reasons_final = "retired", det_fail_reasons
                    else:
                        disposition, final_q = "unverified", current
                    break

                failed = det_fail or verdict == "fail"
                reasons = det_fail_reasons + (llm_reasons if verdict == "fail" else [])
                history.append({"round": rnd, "llm_available": True, "llm_verdict": verdict,
                                "deterministic_fail": det_fail, "difficulty_fit": difficulty_fit,
                                "suggested_difficulty": suggested, "reasons": reasons})

                if not failed:
                    disposition, final_q = "passed", current
                    break

                # 不合格 → 携带原因重生成（若还有轮次且预算允许）
                if rnd < MAX_REGENERATION_ROUNDS:
                    if _budget_hit():
                        # 预算耗尽：无法重生成，按不合格剔除
                        disposition, reasons_final = "retired", reasons
                        break
                    regen_ok, regen_q = _try_regenerate(current, reasons, kp_context,
                                                        deadline=deadline)
                    llm_ops += 1
                    llm_ok_any = llm_ok_any or regen_ok
                    llm_err_any = llm_err_any or (not regen_ok)
                    if regen_ok:
                        current = _merge_regenerated(q, regen_q)
                        continue
                    # 重生成失败（LLM 不可用）：原题已判不合格且无法修复 → 剔除
                    disposition, reasons_final = "retired", reasons
                    break
                else:
                    # 已达最大重生成轮次 → 剔除
                    disposition, reasons_final = "retired", reasons

        rounds = sum(1 for h in history if h.get("llm_available"))
        first_round_fail = bool(history) and (
            history[0].get("deterministic_fail") or history[0].get("llm_verdict") == "fail")
        entries.append({
            "index": i, "question_id": qid, "status": disposition,
            "rounds": rounds, "first_round_fail": first_round_fail,
            "final": final_q, "reasons": reasons_final, "history": history,
        })
        if disposition in ("passed", "unverified") and final_q is not None:
            survivors.append({"entry_index": i, "q": final_q})

    # 批内最终查重：硬重复（≥HARD）剔除后者，软重复仅记录
    dup_pairs_report, survivors = _dedup_survivors(survivors, entries)

    # 重编号 + 重新分配分值（剔除后总分仍精确 = total_score）
    final_questions = []
    for idx, s in enumerate(survivors, 1):
        s["question_id"] = f"q{idx}"
        final_questions.append(s)
    if final_questions:
        allocate_scores(final_questions, int(total_score))

    status = "completed"
    if llm_err_any or budget_exhausted:
        status = "partial" if llm_ok_any else "skipped"

    report = _build_report(
        status, questions, entries, dup_pairs_report, kp_context, conn is not None,
        budget={
            "time_budget_seconds": time_budget_seconds,
            "max_llm_operations": max_llm_operations,
            "budget_exhausted": budget_exhausted,
            "llm_operations": llm_ops,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })
    return {"status": status, "report": report, "questions": final_questions}


def _dedup_survivors(survivors: list[dict], entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """批内最终查重：硬重复剔除后者（更新 entries），软重复仅返回记录。"""
    qs = [s["q"] for s in survivors]
    pairs, _ = batch_duplicates(qs)
    retire_pos: set[int] = set()
    for p in pairs:
        if p["ratio"] >= DUP_BATCH_HARD:
            retire_pos.add(p["index_b"])
    final: list[dict] = []
    for pos, s in enumerate(survivors):
        if pos in retire_pos:
            e = entries[s["entry_index"]]
            e["status"] = "retired"
            e["reasons"] = ["批内查重：与同卷另一题高度重复（硬重复剔除）"]
            e["final"] = None
        else:
            final.append(s["q"])
    return pairs, final


def _build_report(status: str, questions: list[dict], entries: list[dict],
                  dup_pairs: list[dict], kp_context: str, history_dedup: bool,
                  budget: dict | None = None) -> dict:
    def count(st):
        return sum(1 for e in entries if e["status"] == st)

    total = len(questions)
    passed = count("passed")
    retired = count("retired")
    unverified = count("unverified")
    first_round_fail = sum(1 for e in entries if e["first_round_fail"])
    total_rounds = sum(e["rounds"] for e in entries)
    regenerated = sum(1 for e in entries if e["rounds"] > 1)

    # 原始统计字段（供评测层直接聚合，不反推）：
    # 全部判不合格次数 = Σ每题「该轮判定为 fail（确定性 fail 或 LLM fail）」的次数
    total_fail_verdicts = 0
    for e in entries:
        for h in e["history"]:
            if h.get("llm_available") and (h.get("deterministic_fail") or h.get("llm_verdict") == "fail"):
                total_fail_verdicts += 1
    # 回流后最终通过 = 首轮不合格且最终 passed 的题数
    first_round_fail_eventually_passed = sum(
        1 for e in entries if e["first_round_fail"] and e["status"] == "passed")
    # 已完成至少一次实际 LLM 复核的题数（rounds > 0 即至少一轮 llm_available）
    verified_questions = sum(1 for e in entries if e["rounds"] > 0)

    report = {
        "status": status,
        "kp_context": kp_context,
        "history_dedup": "performed" if history_dedup else "skipped",
        "max_regeneration_rounds": MAX_REGENERATION_ROUNDS,
        "summary": {
            "total": total,                                   # 送检题数
            "passed": passed,                                 # 最终通过
            "retired": retired,                               # 最终剔除
            "unverified": unverified,                         # 降级未复核
            "verified_questions": verified_questions,         # 已完成 ≥1 次 LLM 复核的题数
            "first_round_fail": first_round_fail,             # 首轮判不合格题数
            "first_round_fail_eventually_passed": first_round_fail_eventually_passed,  # 回流后最终通过
            "total_fail_verdicts": total_fail_verdicts,       # 全部判不合格次数
            "total_verification_rounds": total_rounds,        # 全部送检（复核）次数
            "regenerated": regenerated,                       # 参与重生成题数
            "duplicate_pairs_batch": len(dup_pairs),
        },
        "questions": entries,
        "duplicate_pairs": dup_pairs,
        "gaps": [{"question_id": e["question_id"], "reasons": e["reasons"]}
                 for e in entries if e["status"] == "retired"],
    }
    if budget is not None:
        report["budget"] = budget      # 预算快照（耗时/操作数/是否耗尽）
    return report
