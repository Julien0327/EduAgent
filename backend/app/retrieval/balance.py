"""组卷分值配平（技术方案 §4.1：总分约束不进 SQL，候选集选出后贪心配平）。

从 question_tools 上移（Phase 2/S4，NL 问数与组卷共用同一配平实现；
question_tools 保持向后兼容的再导出）。

确定性算法：题型权重（解答题 2 / 选择与填空 1）→ 均分基数 + 余数按序补 1
→ 总分精确等于 total_score。题型缺权重默认 1。
"""
from __future__ import annotations

TYPE_WEIGHT = {"选择题": 1, "填空题": 1, "解答题": 2}


def allocate_scores(questions: list[dict], total_score: int) -> list[int]:
    """按题型权重确定性分配分值，保证总分精确等于 total_score。

    questions 元素需含 ``type``（中文题型名）；返回与输入等长的分值列表，
    并**就地写入**每题的 ``score``（供产物/账本直接消费）。
    """
    if not questions:
        return []
    weights = [TYPE_WEIGHT.get(q.get("type", ""), 1) for q in questions]
    total_weight = sum(weights)
    base = total_score // total_weight
    remainder = total_score - base * total_weight
    scores = []
    for i, w in enumerate(weights):
        s = base * w
        if i < remainder:
            s += 1
        scores.append(int(s))
        questions[i]["score"] = int(s)
    return scores
