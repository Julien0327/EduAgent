"""演示题目校验（Phase 6 / S2）。

三层校验：
1. 规则层：题干完整性、答案格式、单位、数值范围、年级适配、选项一致性；
2. 算式层：``equation`` 安全求值必须等于 ``answer_value``（数学一致性）；
3. LLM 复核层：题干是否完整、条件是否充分、答案是否唯一、是否适合六年级
   ——专门覆盖「计算正确但题目本身不成立」的情况。

注意：LLM 复核只给结论，不修改题目；任何一层不通过都保留问题记录，
最终由人工抽检决定是否接受。
"""
from __future__ import annotations

import ast
import json
import math
import re
from difflib import SequenceMatcher

from backend.app.agent.llm_utils import structured_completion
from backend.app.demo.models import DemoQuestion, ReviewVerdict
from backend.app.tracing import collector as tracing

UNIT_WORDS = [
    "厘米", "分米", "米", "千米", "平方厘米", "平方分米", "平方米", "公顷",
    "平方千米", "立方厘米", "立方分米", "立方米", "毫升", "升", "克", "千克",
    "吨", "分钟", "小时", "秒", "天", "元", "角", "分", "个", "本", "倍",
]

_PLACEHOLDER_RE = re.compile(r"(略去|从略|见解析|答案见|自行|略\s*[。，,]|待补|TODO)", re.I)
_FORBIDDEN_RE = re.compile(r"(负数|二元一次|不等式|根号|函数图像|微积分)")
_ALLOWED_AST = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Add, ast.Sub,
    ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd, ast.Load,
)


def safe_eval(expr: str) -> float:
    """只允许数字与 + - * / ** 括号的安全求值；兼容 × ÷ − 写法。"""
    expr = (expr or "").replace("×", "*").replace("÷", "/").replace("−", "-")
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_AST):
            raise ValueError(f"算式包含不允许的语法：{type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise ValueError("算式只能包含数字")
    value = eval(compile(tree, "<demo-equation>", "eval"), {"__builtins__": {}}, {})
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError("算式结果不是有限数值")
    return float(value)


def normalize_stem(text: str) -> str:
    # 保留括号与运算符：``（3+4）×5`` 与 ``3+4×5`` 不是同一道题
    return re.sub(r"[\s，。；：、!?！？]", "", text or "")


def _contains_unit(answer: str) -> str | None:
    for unit in UNIT_WORDS:
        if unit in answer:
            return unit
    return None


_LETTER_RE = re.compile(r"^[A-Da-d]$")
_OPTION_PREFIX_RE = re.compile(r"^[A-Da-d][.．、]\s*")


def normalize_choice_options(q: DemoQuestion) -> bool:
    """选择题格式归一：选项无 A./B./C./D. 前缀但答案是字母时补齐前缀。

    这是格式修复，不改变题目内容与答案；返回是否发生修改。
    """
    if q.qtype != "选择题" or not q.options:
        return False
    if all(_OPTION_PREFIX_RE.match(str(o).strip()) for o in q.options):
        return False
    answer = (q.answer or "").strip()
    if not _LETTER_RE.match(answer):
        return False
    idx = ord(answer.upper()) - ord("A")
    if not (0 <= idx < len(q.options)):
        return False
    q.options = [f"{chr(65 + i)}. {str(o).strip()}" for i, o in enumerate(q.options)]
    q.answer = answer.upper()
    return True


def rule_validate(q: DemoQuestion) -> list[str]:
    """返回问题列表；空列表表示规则层通过。"""
    issues: list[str] = []
    stem = (q.stem or "").strip()
    answer = (q.answer or "").strip()
    solution = (q.solution or "").strip()

    if not (8 <= len(stem) <= 300):
        issues.append(f"题干长度异常：{len(stem)}")
    if not re.search(r"[。？?]\s*(（[^（）]*）)?\s*$", stem):
        issues.append("题干结尾不完整（应为问句或句号）")
    if _PLACEHOLDER_RE.search(stem) or _PLACEHOLDER_RE.search(answer):
        issues.append("题干或答案含占位/省略表达")
    if _FORBIDDEN_RE.search(stem):
        issues.append("题干含超纲概念")
    if not answer or len(answer) > 80:
        issues.append("答案缺失或过长")
    if not solution or len(solution) > 300:
        issues.append("解析缺失或过长")

    if q.qtype == "选择题":
        if len(q.options) < 2:
            issues.append("选择题选项少于 2 个")
        else:
            letters = [opt.strip()[:1] for opt in q.options]
            if answer[:1] in letters:
                pass
            elif not any(answer == opt.strip() or answer in opt for opt in q.options):
                issues.append("选择题答案不在选项中")
    else:
        if q.options:
            issues.append("非选择题不应带选项")

    if q.answer_value is not None:
        try:
            value = float(q.answer_value)
        except (TypeError, ValueError):
            issues.append("answer_value 不是数值")
            value = None
        if value is not None:
            if not math.isfinite(value) or abs(value) > 1e7:
                issues.append("答案数值超出范围")
            if q.unit is None and _contains_unit(answer):
                issues.append("答案含单位但 unit 字段为空")
            if q.unit and _contains_unit(answer) and q.unit not in answer:
                issues.append("答案单位与 unit 字段不一致")

    if q.equation:
        try:
            computed = safe_eval(q.equation)
        except Exception as exc:  # noqa: BLE001 - 记录具体原因
            issues.append(f"算式不可求值：{exc}")
        else:
            if q.answer_value is None:
                issues.append("有算式但缺少 answer_value")
            elif abs(computed - float(q.answer_value)) > 1e-6:
                issues.append(
                    f"算式结果 {computed:g} 与答案 {q.answer_value:g} 不一致"
                )
    elif q.answer_value is not None and q.qtype != "选择题":
        # 非数值题（判断题/概念题）允许没有算式；数值题应尽量提供算式
        pass

    if q.difficulty not in range(1, 6):
        issues.append("难度不在 1–5")
    return issues


def llm_review(
    q: DemoQuestion, max_attempts: int = 2, budget_cap: float | None = None
) -> tuple[list[str], list[str], dict | None]:
    """LLM 复核；返回（硬性问题, 备注, 原始判定）。

    硬性问题只来自四个布尔判定；模型写在 ``issues`` 里的正面说明/建议
    归入备注，不用于判定不通过——避免把「条件完整、答案唯一」误判成问题。
    """
    if budget_cap is not None:
        agg = tracing.aggregate(tracing.current())
        cost = agg.get("cost")
        if cost is not None and cost >= budget_cap:
            return [f"LLM 复核预算耗尽（已用 ¥{cost:.4f}）"], [], {"budget_exhausted": True}
    system = (
        "你是小学数学教研员，负责审核六年级题目。请严格判断：\n"
        "1. 题干是否完整，条件是否充分；\n"
        "2. 答案是否能由条件唯一推出，是否存在歧义；\n"
        "3. 是否适合小学六年级，是否超纲；\n"
        "4. 单位、问法、数据是否合理。\n"
        "只返回结构化结论；不要改写题目。"
    )
    user = json.dumps(
        {
            "知识点": q.kp_name,
            "难度": q.difficulty,
            "题型": q.qtype,
            "题干": q.stem,
            "选项": q.options,
            "答案": q.answer,
            "算式": q.equation,
            "解析": q.solution,
        },
        ensure_ascii=False,
    )
    try:
        verdict = structured_completion(
            system, user, "submit_review", ReviewVerdict, max_attempts=max_attempts
        )
    except Exception as exc:  # noqa: BLE001 - 复核失败不阻断
        return [f"LLM 复核失败：{type(exc).__name__}: {exc}"], [], None
    hard: list[str] = []
    if not verdict.stem_complete:
        hard.append("LLM：题干不完整")
    if not verdict.conditions_sufficient:
        hard.append("LLM：条件不充分")
    if not verdict.answer_unique:
        hard.append("LLM：答案不唯一或有歧义")
    if not verdict.grade_fit:
        hard.append("LLM：不适合六年级")
    notes = [f"LLM：{item}" for item in verdict.issues]
    return hard, notes, verdict.model_dump()


def mark_duplicates(questions: list[DemoQuestion]) -> None:
    """精确与近似查重；后出现者标记 duplicate_of。"""
    exact: dict[str, str] = {}
    for q in questions:
        key = normalize_stem(q.stem)
        if key in exact:
            q.duplicate_of = exact[key]
        else:
            exact[key] = q.q_id

    ordered = sorted(questions, key=lambda x: (x.kp_id, x.difficulty, x.q_id))
    for i, left in enumerate(ordered):
        if left.duplicate_of:
            continue
        left_norm = normalize_stem(left.stem)
        for right in ordered[i + 1:]:
            if right.duplicate_of:
                continue
            right_norm = normalize_stem(right.stem)
            if not left_norm or not right_norm:
                continue
            # 同 KP 更严格；跨 KP 只在几乎相同文本时判重。
            # 题干相似但答案不同（如 + 与 - 的两道计算题）不判重。
            threshold = 0.97 if left.kp_id == right.kp_id else 0.98
            ratio = SequenceMatcher(None, left_norm, right_norm).ratio()
            same_answer = (left.answer or "").strip() == (right.answer or "").strip()
            if ratio >= threshold and (same_answer or ratio >= 0.999):
                right.duplicate_of = left.q_id


def validate_all(
    questions: list[DemoQuestion],
    *,
    llm_review_qids: set[str] | None = None,
    budget_cap: float | None = None,
) -> dict:
    """规则校验 + 可选 LLM 复核 + 查重；原地更新状态并返回统计。"""
    mark_duplicates(questions)
    reviewed = 0
    review_total = len(llm_review_qids) if llm_review_qids else 0
    for q in questions:
        issues = rule_validate(q)
        if q.duplicate_of:
            issues.append(f"与 {q.duplicate_of} 近似重复")
        if llm_review_qids and q.q_id in llm_review_qids:
            hard, notes, verdict = llm_review(q, budget_cap=budget_cap)
            reviewed += 1
            if reviewed % 20 == 0 or reviewed == review_total:
                print(f"[llm-review] {reviewed}/{review_total}", flush=True)
            issues.extend(hard)
            q.review_notes = notes
            q.validation_issues = issues
            q.validation_status = "passed" if not issues else "needs_review"
        else:
            q.validation_issues = issues
            q.validation_status = "passed" if not issues else "needs_review"
    passed = sum(1 for q in questions if q.validation_status == "passed")
    return {
        "total": len(questions),
        "passed": passed,
        "needs_review": len(questions) - passed,
        "llm_reviewed": reviewed,
        "duplicates": sum(1 for q in questions if q.duplicate_of),
    }


HARD_REVIEW_PHRASES = {
    "LLM：题干不完整",
    "LLM：条件不充分",
    "LLM：答案不唯一或有歧义",
    "LLM：不适合六年级",
}


def reclassify_review_issues(questions: list[DemoQuestion]) -> dict:
    """把旧版复核结果中的正面备注与硬性问题分开。

    旧版把模型的所有 ``issues`` 都当作问题；此函数按四个布尔判定对应的
    硬性短语重新分类，正面说明移入 ``review_notes``。
    """
    hard_count = 0
    cleared = 0
    for q in questions:
        if q.validation_status != "needs_review" or not q.validation_issues:
            continue
        hard = [
            item for item in q.validation_issues
            if item in HARD_REVIEW_PHRASES
            or item.startswith("LLM 复核失败")
            or item.startswith("LLM 复核预算耗尽")
        ]
        notes = [item for item in q.validation_issues if item not in hard]
        q.review_notes = list(q.review_notes) + notes
        if hard:
            q.validation_issues = hard
            hard_count += 1
        else:
            q.validation_issues = []
            q.validation_status = "passed"
            cleared += 1
    return {"hard_kept": hard_count, "soft_cleared": cleared}


def sample_for_human_review(
    questions: list[DemoQuestion], count: int = 41, seed: int = 20261120
) -> list[DemoQuestion]:
    """人工抽检样本：优先 LLM 生成的应用题/几何题/复杂题，再补模板题。"""
    import random

    complex_kps = {
        "行程问题", "相遇问题", "追及问题", "流水行船", "火车过桥", "环形跑道",
        "工程问题", "浓度问题", "牛吃草问题", "抽屉原理", "容斥问题", "时钟问题",
        "组合图形面积", "图形切拼", "统计图表", "可能性",
    }
    geometry_kps = {
        "长方形周长", "正方形周长", "长方形面积", "正方形面积", "三角形面积",
        "平行四边形面积", "梯形面积", "圆的周长", "圆的面积", "环形面积",
        "扇形面积", "长方体表面积体积", "正方体表面积体积", "圆柱", "圆锥",
    }
    eligible = [q for q in questions if q.validation_status == "passed"]
    rng = random.Random(seed)
    selected: list[DemoQuestion] = []
    seen: set[str] = set()

    def take(pool: list[DemoQuestion], limit: int) -> None:
        rng.shuffle(pool)
        for item in pool:
            if len(selected) >= count or limit <= 0:
                return
            if item.q_id in seen:
                continue
            selected.append(item)
            seen.add(item.q_id)
            limit -= 1

    # 分层：几何 10、LLM 应用题 20、复杂题 11，不足时用其余题补齐
    take([q for q in eligible if q.kp_name in geometry_kps], 10)
    take([
        q for q in eligible
        if q.generation_method.startswith("llm") and q.qtype == "解答题"
    ], 20)
    take([q for q in eligible if q.kp_name in complex_kps], 11)
    take([q for q in eligible], count - len(selected))
    return selected[:count]
