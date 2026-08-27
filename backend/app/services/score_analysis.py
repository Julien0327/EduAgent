"""成绩分析（确定性统计）。

用 pandas 对预置答题做确定性聚合，输出结构化 stats + 薄弱知识点。
全部为确定性计算，无随机性（对应技术方案 §16）。
"""
import pandas as pd

from app.knowledge import loader

PASS_THRESHOLD = 60       # 及格线（≥60）
EXCELLENT_THRESHOLD = 85  # 优秀线（≥85）
WEAK_THRESHOLD = 0.6      # 正确率低于该值视为薄弱

# 分数段分布：（标签, 下界, 上界），下界/上界为 None 表示开区间
SCORE_BINS = [
    ("<60", None, 60),
    ("60-69", 60, 70),
    ("70-79", 70, 80),
    ("80-89", 80, 90),
    ("≥90", 90, None),
]


def analyze_scores(student_answers: dict, question_bank: dict) -> dict:
    """对预置答题做确定性统计。

    返回：
      {
        "stats": {...},
        "weak_knowledge_points": [{kp_id, name, accuracy}, ...]  # 按正确率升序
      }
    """
    students = student_answers["students"]
    questions = question_bank["questions"]
    total = len(students)

    totals = pd.Series([s["total"] for s in students])

    # 1. 班级整体指标
    avg = round(float(totals.mean()), 1)
    median = round(float(totals.median()), 1)
    max_score = int(totals.max())
    min_score = int(totals.min())
    std = round(float(totals.std(ddof=1)), 2)  # 样本标准差
    pass_rate = round(float((totals >= PASS_THRESHOLD).sum() / total), 4)
    excellent_rate = round(float((totals >= EXCELLENT_THRESHOLD).sum() / total), 4)

    # 2. 分数段分布
    distribution = []
    for label, low, high in SCORE_BINS:
        if low is None:
            count = int((totals < high).sum())
        elif high is None:
            count = int((totals >= low).sum())
        else:
            count = int(((totals >= low) & (totals < high)).sum())
        distribution.append({"label": label, "count": count})

    # 3. 逐题正确率：每题得分=满分 的人数 / 总人数
    per_question = []
    for q in questions:
        qid = q["question_id"]
        full = q["score"]
        count_full = sum(
            1 for s in students if s["question_scores"].get(qid, 0) == full
        )
        per_question.append(
            {
                "question_id": qid,
                "type": q["type"],
                "full_score": full,
                "accuracy": round(count_full / total, 4),
            }
        )

    # 4. 知识点聚合：题目正确率 -> 关联知识点 -> 薄弱知识点按正确率升序
    q_accuracy = {p["question_id"]: p["accuracy"] for p in per_question}
    kp_accs: dict[str, list[float]] = {}
    for q in questions:
        for kp_id in q["knowledge_points"]:
            kp_accs.setdefault(kp_id, []).append(q_accuracy[q["question_id"]])

    kp_name = {
        kp["kp_id"]: kp["name"]
        for kp in loader.load_knowledge_points()["knowledge_points"]
    }
    weak_kp = [
        {
            "kp_id": kp_id,
            "name": kp_name.get(kp_id, kp_id),
            "accuracy": round(sum(accs) / len(accs), 4),
        }
        for kp_id, accs in kp_accs.items()
    ]
    weak_kp.sort(key=lambda x: x["accuracy"])  # 升序，最薄弱在前

    stats = {
        "class_name": student_answers.get("class_name", ""),
        "student_count": total,
        "full_marks": student_answers.get("full_marks", 100),
        "avg": avg,
        "median": median,
        "max": max_score,
        "min": min_score,
        "std": std,
        "pass_rate": pass_rate,
        "excellent_rate": excellent_rate,
        "score_distribution": distribution,
        "per_question_accuracy": per_question,
    }

    return {"stats": stats, "weak_knowledge_points": weak_kp}
