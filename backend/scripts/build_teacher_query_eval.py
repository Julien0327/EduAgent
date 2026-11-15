"""20 条典型教师问句检索评测材料生成脚本（Phase 2 人工验收）。

- 问句为**助手草拟**（5 类 × 4 条），未经用户确认；
- 实际调用当前检索系统（真实库 385 KP + 真实 bge FP32 直连模型）：
  每条问句输出知识点 Top-5（融合分 + 召回路）；题目查询类问句额外输出
  题目 Top-5（重排分）；
- 生成人工评判表：1–3 分评分栏与备注栏**留空**，不代用户评分；
- **不计算 Recall/MRR**（无 gold），仅展示检索输出供人工判读。

输出（staging 不入 Git）：
    data/staging/teacher_queries_20_results.md   展示 + 评判表
    data/staging/teacher_queries_20_results.jsonl 原始结果

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.build_teacher_query_eval
"""
import json
import sys
from pathlib import Path

from backend.app.retrieval.service import RetrievalService

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "data" / "staging"
OUT_MD = STAGING / "teacher_queries_20_results.md"
OUT_JSONL = STAGING / "teacher_queries_20_results.jsonl"

# 5 类 × 4 条（助手草拟；类别与问法覆盖 §4.5「典型查询相关性」评判场景）
QUESTIONS = [
    # A. 知识点查询
    ("A", "知识点查询", "一元二次方程的判别式怎么判断根的情况？"),
    ("A", "知识点查询", "分数加减混合运算有哪些常见题型？"),
    ("A", "知识点查询", "圆柱的表面积公式是什么？"),
    ("A", "知识点查询", "什么是正比例关系？"),
    # B. 题目查询
    ("B", "题目查询", "找10道小学分数加减法应用题"),
    ("B", "题目查询", "有没有适合五年级的植树问题练习题？"),
    ("B", "题目查询", "给我几道关于人民币计算的题"),
    ("B", "题目查询", "找一些求平均数的应用题"),
    # C. 相近知识点辨析
    ("C", "相近知识点辨析", "一元二次方程的配方法和公式法有什么区别？"),
    ("C", "相近知识点辨析", "正比例和反比例怎么区分？"),
    ("C", "相近知识点辨析", "周长和面积容易混淆，怎么讲清楚？"),
    ("C", "相近知识点辨析", "质数、合数、奇数、偶数之间是什么关系？"),
    # D. 教学内容查询
    ("D", "教学内容查询", "怎么设计一节关于三角形内角和的课？"),
    ("D", "教学内容查询", "教学重点和难点应该怎么写？"),
    ("D", "教学内容查询", "期末复习课应该怎么安排？"),
    ("D", "教学内容查询", "怎样帮助学生理解分数的意义？"),
    # E. 模糊自然语言查询
    ("E", "模糊自然语言查询", "孩子应用题老是不会做怎么办？"),
    ("E", "模糊自然语言查询", "有没有难一点的题？"),
    ("E", "模糊自然语言查询", "帮我准备一下明天要用的材料"),
    ("E", "模糊自然语言查询", "这块内容讲不明白"),
]


def main() -> int:
    svc = RetrievalService()
    results = []
    try:
        for idx, (cat, cat_name, text) in enumerate(QUESTIONS, 1):
            kps = svc.search_knowledge_points(text, top_k=5)
            entry = {
                "no": idx, "category": cat, "category_name": cat_name,
                "query": text, "knowledge_points": kps,
            }
            # 题目查询类（B）与含题目意图的 E-2：补题目 Top-5
            need_questions = cat == "B" or text.startswith("有没有难一点的题")
            if need_questions:
                # 仅当问句明示学段时才过滤（不替用户做未声明的学段假设）
                stage = "primary" if ("小学" in text or "五年级" in text) else None
                qres = svc.search_questions(text, stage=stage, question_limit=5)
                entry["questions"] = qres["questions"]
                entry["question_meta"] = qres["meta"]
            results.append(entry)
    finally:
        svc.close()

    STAGING.mkdir(parents=True, exist_ok=True)
    OUT_JSONL.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results), encoding="utf-8")

    lines = [
        "# 20 条典型教师问句检索结果与人工评判表（Phase 2 人工验收）",
        "",
        "> 本文件由 `backend/scripts/build_teacher_query_eval.py` 生成。",
        "> **问句为助手草拟、未经您确认**；结果=当前检索系统真实输出（385 KP + bge FP32 直连），",
        "> 未做任何人工调参或结果挑选；**未评分、未计算 Recall/MRR**。",
        "",
        "## 0. 说明",
        "",
        "- 知识点 Top-5：融合分 = α·关键词归一化 + (1-α)·向量归一化（α=0.4），名称直配另有 +0.05；",
        "  `via` 表示召回路：both=双路命中 / keyword=仅关键词 / vector=仅向量。",
        "- 题目 Top-5：重排分 = 0.706·KP 重合 + 0.294·难度贴合（无目标难度时中性；FTS 特征仅在题面意图时计入）。",
        "- 评分栏留空：**请按 3 分制人工判读**（3=高度相关 / 2=部分相关 / 1=不相关），备注自由填写。",
        "- 学段口径：知识点 Top-5 为**全学段召回**（未按问句自动推断学段）；题目 Top-5 仅在问句",
        "  明示\"小学/五年级\"时过滤 primary，其余全学段。这与当前系统调用路径一致，不做人为美化。",
        "",
        "**观察与已知限制（基于本次实际输出，供您判读参考，不代评分）**：",
        "",
        "1. 题目查询类 Top-5 可能被**弱相关知识点占位**：阶段 3 的 KP 重合度（Jaccard）在",
        "   \"每个候选只挂 1 个召回 KP\" 时全部相同（1/10），重排退化为平局，候选顺序受",
        "   召回集合中的弱相关 KP 影响（如 B-6 的题目结果偏向年龄问题）。这是 Phase 2",
        "   验收应判读的真实缺陷，未做结果挑选。",
        "2. 关键词路对**通用词过宽**：\"问题/练习题/怎么/有没有\" 等 token 会命中大量 KP，",
        "   关键词权重（α=0.4）下可能推高非主题 KP。",
        "3. 规则挂接是**候选标签**（决策㉔）：题目扩展依赖 q_kp 规则挂接，标签噪声会传导到",
        "   题目级结果；步骤 4 已记录系统性过挂接，Phase 2 未修改该数据。",
        "4. 长问句的向量语义被稀释：查询越长，主题词在嵌入中的相对权重越低，向量路偏向",
        "   泛化语义相近的 KP。",
        "",
        "## 1. 逐条结果",
        "",
    ]

    for r in results:
        lines += [
            f"### {r['no']}. [{r['category_name']}] {r['query']}",
            "",
            "**知识点 Top-5**",
            "",
            "| # | kp_id | 名称 | 学段 | 融合分 | 召回路 |",
            "|---|---|---|---|---|---|",
        ]
        for j, kp in enumerate(r["knowledge_points"], 1):
            lines.append(
                f"| {j} | {kp['kp_id']} | {kp['name']} | {kp['stage']} | "
                f"{kp['score']:.4f} | {kp['via']} |")
        if "questions" in r:
            lines += [
                "",
                "**题目 Top-5**（重排分）",
                "",
                "| # | q_id | 题干（截断 40 字） | 学段 | 难度 | 重排分 |",
                "|---|---|---|---|---|---|",
            ]
            if r["questions"]:
                for j, q in enumerate(r["questions"], 1):
                    stem = q["stem"].replace("|", "｜")
                    if len(stem) > 40:
                        stem = stem[:40] + "…"
                    lines.append(
                        f"| {j} | {q['q_id']} | {stem} | {q['stage']} | "
                        f"{q['difficulty']} | {q['rerank_score']:.4f} |")
            else:
                lines.append("| - | （该问句未检索到题目） | | | | |")
        lines += [
            "",
            f"**人工评分**：相关度 ☐1　☐2　☐3　｜ 备注：",
            "",
        ]

    lines += [
        "## 2. 汇总评判表（留空待您填写）",
        "",
        "| 编号 | 类别 | 问句 | 相关度（1–3） | 备注 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(f"| {r['no']} | {r['category_name']} | {r['query']} |  |  |")

    lines += [
        "",
        "## 3. 评分口径（§4.5 典型查询相关性）",
        "",
        "- 判读对象：该问句的 **Top-5 知识点**是否对教师有实际帮助（可含题目结果一并判读）；",
        "- 3 分=高度相关（Top-1/2 即切题）；2 分=部分相关（Top-5 内含切题项）；1 分=不相关；",
        "- 汇总口径（**待您评分后由助手计算**）：3 分制 ≥2 记为相关，相关率 ≥85% 达标。",
        "",
    ]

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    n_q = sum(1 for r in results if "questions" in r)
    print(f"已生成：{OUT_MD}")
    print(f"原始结果：{OUT_JSONL}")
    print(f"20 条问句，其中 {n_q} 条附题目 Top-5；评分栏留空，未代评。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
