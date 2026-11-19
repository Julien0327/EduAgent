"""质检确定性规则层（S1，§8.1）：正确性 / 难度 三维规则 + 编排入口。

**纪律**：
- 确定性规则**不依赖 LLM**，必须尽可能独立执行（§8.2 降级语义：LLM 不可用仍跑
  规则层）；难度为「偏离判定 + 告警」（warn，非 fail），规则优先于 LLM 复判。
- 正确性规则只覆盖**可确定性判定**的项：答案非空/无占位符、选择题选项数量与前缀、
  选择题答案在选项内、题型与 options 字段一致性、单位量纲启发（warn）。**不宣称
  规则层能判断应用题答案的数学对错**——完整正确性由 S2 的 LLM 一题一验承担（诚实边界）。
- 输入为 ``generate_questions`` 产出的题目 dict（question_id/type/stem/options/
  answer/difficulty/analysis/score/knowledge_points）；访问全部防御式，缺字段不崩溃。
"""
from __future__ import annotations

import re

from backend.app.critique.models import (
    QuestionCritique, BatchCritique, RuleIssue,
    DIM_CORRECTNESS, DIM_DIFFICULTY, DIM_DUPLICATE,
    SEV_FAIL, SEV_WARN, STATUS_FAIL, STATUS_PASS,
)
from backend.app.critique.dupcheck import batch_duplicates, history_duplicates

# ---- 常量 ----
# 答案占位符（生成题不应出现；命中即 fail）
PLACEHOLDER_MARKERS = ("略", "todo", "待补充", "答案略", "（略）", "(略)", "暂无", "无解析")

# 单位量纲启发（warn only）：题干明确问「多少X」且答案带不同单位时告警
_KNOWN_UNITS = ("元", "角", "分", "米", "千米", "厘米", "毫米", "克", "千克", "吨",
                "升", "毫升", "个", "人", "只", "次", "小时", "分钟", "秒",
                "平方米", "立方米", "%", "％", "度", "天", "周", "年", "本", "支", "块")
_ASK_UNIT_RE = re.compile(r"多少(元|角|分|米|千米|厘米|毫米|克|千克|吨|升|毫升|个|人|只|次|小时|分钟|秒|平方米|立方米|天|周|年|本|支|块|%)")

# 难度步骤代理（warn only）
_STEP_MARKERS = ("先", "然后", "接着", "最后", "所以", "因此", "可得", "解得", "再")


# ===================== 访问辅助 =====================
def _s(v) -> str:
    return str(v or "").strip()


# ===================== 正确性 =====================
def _answer_matches_option(answer: str, options: list[str]) -> bool:
    """选择题答案是否落在某选项内：字母（A/B/C/D）或选项正文匹配。"""
    a = _s(answer)
    if not a:
        return False
    m = re.match(r"^([A-Da-d])[.、．]?\s*$", a)
    if m:
        letter = m.group(1).upper()
        return any(_s(o).upper().startswith(letter + ".") for o in options)
    for o in options:
        body = re.sub(r"^[A-Da-d][.、．]\s*", "", _s(o))
        if body and (a == body or a in body or body in a):
            return True
    return False


def _unit_sanity(stem: str, answer: str) -> dict | None:
    """单位量纲启发：题干问「多少X」，答案含不同单位 → 返回不一致信息（warn）。"""
    m = _ASK_UNIT_RE.search(_s(stem))
    if not m:
        return None
    asked = m.group(1)
    a = _s(answer)
    for u in _KNOWN_UNITS:
        if u != asked and u in a:
            return {"asked_unit": asked, "answer_unit": u}
    return None


def check_correctness(q: dict) -> list[RuleIssue]:
    """正确性确定性规则（fail=硬错误；warn=量纲启发）。"""
    issues: list[RuleIssue] = []
    qtype = _s(q.get("type"))
    answer = _s(q.get("answer"))
    options = q.get("options")
    stem = _s(q.get("stem"))

    if not answer:
        issues.append(RuleIssue(DIM_CORRECTNESS, SEV_FAIL, "correctness_answer_empty", "答案为空"))
    else:
        for m in PLACEHOLDER_MARKERS:
            if m.lower() in answer.lower():
                issues.append(RuleIssue(
                    DIM_CORRECTNESS, SEV_FAIL, "correctness_answer_placeholder",
                    f"答案含占位符「{m}」"))
                break

    if qtype == "选择题":
        if not options:
            issues.append(RuleIssue(DIM_CORRECTNESS, SEV_FAIL, "correctness_options_missing",
                                    "选择题缺少选项"))
        elif len(options) != 4:
            issues.append(RuleIssue(DIM_CORRECTNESS, SEV_FAIL, "correctness_option_count",
                                    f"选择题选项应为 4 个，实际 {len(options)}"))
        else:
            for i, opt in enumerate(options):
                if not _s(opt).startswith(f"{chr(ord('A') + i)}."):
                    issues.append(RuleIssue(
                        DIM_CORRECTNESS, SEV_WARN, "correctness_option_prefix",
                        f"选项 {i + 1} 缺少「{chr(ord('A') + i)}.」前缀"))
                    break
            if answer and not _answer_matches_option(answer, options):
                issues.append(RuleIssue(DIM_CORRECTNESS, SEV_FAIL,
                                        "correctness_answer_not_in_options",
                                        "选择题答案不在任一选项内"))
    else:
        # 填空/解答题 options 应为 null/空
        if options:
            issues.append(RuleIssue(DIM_CORRECTNESS, SEV_FAIL,
                                    "correctness_unexpected_options",
                                    f"{qtype or '非选择题'} 不应携带选项"))

    u = _unit_sanity(stem, answer)
    if u:
        issues.append(RuleIssue(
            DIM_CORRECTNESS, SEV_WARN, "correctness_unit_sanity",
            f"题干问「多少{u['asked_unit']}」但答案含单位「{u['answer_unit']}」", details=u))
    return issues


# ===================== 难度 =====================
def _length_band(n: int) -> str:
    if n < 15:
        return "简单"
    if n <= 60:
        return "中等"
    return "较难"


def _count_steps(answer: str, analysis: str) -> int:
    text = f"{_s(answer)} {_s(analysis)}"
    n = sum(text.count(m) for m in _STEP_MARKERS)
    n += sum(text.count(op) for op in ("+", "×", "÷", "*", "/", "="))
    return n


def check_difficulty(q: dict) -> list[RuleIssue]:
    """难度偏离判定（代理指标 + 告警，warn only；§8.1 规则优先于 LLM 复判）。"""
    issues: list[RuleIssue] = []
    stem = _s(q.get("stem"))
    declared = _s(q.get("difficulty"))
    band = _length_band(len(stem))

    if declared == "较难" and band == "简单":
        issues.append(RuleIssue(
            DIM_DIFFICULTY, SEV_WARN, "difficulty_stem_too_short",
            f"标注「较难」但题干仅 {len(stem)} 字（长度代理判「简单」）",
            {"stem_len": len(stem), "band": band}))
    elif declared == "简单" and band == "较难":
        issues.append(RuleIssue(
            DIM_DIFFICULTY, SEV_WARN, "difficulty_stem_too_long",
            f"标注「简单」但题干 {len(stem)} 字（长度代理判「较难」）",
            {"stem_len": len(stem), "band": band}))

    steps = _count_steps(q.get("answer"), q.get("analysis"))
    if declared == "简单" and steps >= 3:
        issues.append(RuleIssue(
            DIM_DIFFICULTY, SEV_WARN, "difficulty_steps_high",
            f"标注「简单」但步骤代理达 {steps}（偏难信号）", {"steps": steps}))
    elif declared == "较难" and steps == 0:
        issues.append(RuleIssue(
            DIM_DIFFICULTY, SEV_WARN, "difficulty_steps_low",
            "标注「较难」但无多步信号", {"steps": steps}))
    return issues


# ===================== 编排入口 =====================
def run_rules(questions: list[dict], conn=None) -> BatchCritique:
    """对一批生成题执行全部确定性规则（正确性 + 难度 + 批内/历史查重）。

    ``conn`` 为可选只读业务库连接（None 则跳过历史查重）。
    总裁定：任一 fail 级 issue → 该题 fail。查重阈值见 dupcheck。
    """
    batch = BatchCritique()
    pairs, batch_issues = batch_duplicates(questions)
    batch.duplicate_pairs = pairs
    matches, history_issues = history_duplicates(conn, questions)
    batch.history_matches = matches

    for i, q in enumerate(questions):
        qc = QuestionCritique(index=i, question_id=q.get("question_id", f"q{i + 1}"))
        qc.issues.extend(check_correctness(q))
        qc.issues.extend(check_difficulty(q))
        qc.issues.extend(batch_issues.get(i, []))
        qc.issues.extend(history_issues.get(i, []))
        qc.status = STATUS_FAIL if qc.has_fail else STATUS_PASS
        batch.questions.append(qc)
    return batch
