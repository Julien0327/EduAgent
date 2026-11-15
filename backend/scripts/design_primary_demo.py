"""小学演示数据设计与检查（Phase 3 / S0-3，**不导入**）。

目标：用 49 道 verified 小学题设计两场考试 × 20 名学生，明确 q_id → KP 映射，
确定性生成演示答题，计算“分值加权 + 近期权重 2:1”的掌握度与预期归因链，
供用户审核后再走 F16 导入。

设计纪律（用户 2026-11-15 要求）：
- 不为了漂亮诊断结果反推/微调答题数据；答题由固定的能力模型 + 场景表生成，
  结果由规则计算，若与预期场景不符，如实报告，不改数据凑结果。
- 题目与知识点使用 Gold v2 已确认的显式 q_id / kp_id 映射，不做名称模糊匹配，
  不按题序硬塞标签。
- 演示数据必须体现 Phase 3 核心：分值加权、近期 2:1、薄弱点、前置根因链、
  样本不足（insufficient）与数据范围。

样本口径：**不同 q_id 数**（同一题在两次考试重复出现才计题次；本设计不重复题目）。
掌握度：按卷面分值加权；近期考试权重 2、远期 1；<3 个不同 q_id 标记 insufficient；
<0.6 为薄弱；前置 <0.7 为失败节点。

用法（仓库根执行）：
    .venv/Scripts/python.exe -X utf8 -m backend.scripts.design_primary_demo
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_FILE = REPO_ROOT / "data" / "staging" / "trial50_gold_v2.jsonl"
EDGES_FILE = REPO_ROOT / "data" / "knowledge" / "primary_edges.json"
DB_PATH = REPO_ROOT / "data" / "db" / "edumate.sqlite"
OUT_JSON = REPO_ROOT / "data" / "staging" / "primary_demo_design.json"
OUT_MD = REPO_ROOT / "data" / "staging" / "primary_demo_design.md"

CLASS_NAME = "六年级演示班"
GRADE = 6
STUDENTS = 20
EXAMS = ("A", "B")  # A=远期（2026-09-15），B=近期（2026-11-10）
EXAM_TITLE = {"A": "六年级数学演示测评（一）", "B": "六年级数学演示测评（二）"}
EXAM_DATE = {"A": "2026-09-15", "B": "2026-11-10"}
ABILITY_LEVELS = (0.90, 0.75, 0.60, 0.45, 0.30)
SCORE_BY_DIFF = {1: 3, 2: 3, 3: 4, 4: 5, 5: 5}
TARGETS = ("kp_p_077", "kp_p_078", "kp_p_075", "kp_p_037")

# 场景表：只按“教学机制覆盖”设定，不按目标诊断结果反推。
# 078 基础乘除薄弱 → 075 分数 → 037 量率/079 四则；077 近期恢复、038 近期下降，
# 用于展示近期 2:1 权重会改变掌握度/归因结论；076/074 作为下游观察项。
EFFECTS = {
    "kp_p_077": {"A": -0.33, "B": 0.12},   # 近期恢复案例（等权/加权在 0.7 前置阈值附近翻转）
    "kp_p_078": {"A": -0.30, "B": -0.30},  # 基础乘除薄弱（根因）
    "kp_p_075": {"A": -0.22, "B": -0.22},  # 分数运算受影响
    "kp_p_037": {"A": -0.22, "B": -0.22},  # 量率对应受影响
    "kp_p_038": {"A": -0.05, "B": -0.40},  # 近期下降案例（等权/加权在 0.6 薄弱线附近翻转）
    "kp_p_079": {"A": -0.18, "B": -0.18},  # 四则混合受影响
    "kp_p_074": {"A": -0.15, "B": -0.15},  # 小数运算观察项（分值加权改变结论）
    "kp_p_076": {"A": -0.15, "B": -0.15},  # 百分数观察项
}
DEFAULT_EFFECT = 0.0

SCENARIO_DESC = {
    "kp_p_077": "近期恢复（等权/加权在 0.7 前置阈值两侧翻转）",
    "kp_p_078": "基础乘除薄弱（根因）",
    "kp_p_075": "分数运算受基础影响",
    "kp_p_037": "量率对应受分数影响",
    "kp_p_038": "近期下降（观察项，未跨 0.6）",
    "kp_p_079": "四则混合受基础影响",
    "kp_p_074": "小数运算观察项（分值加权改变数值）",
    "kp_p_076": "百分数观察项",
}


def load_kps_and_questions():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        kps = {r["kp_id"]: dict(r) for r in conn.execute(
            "SELECT kp_id, name, description FROM knowledge_points WHERE stage='primary'")}
        qs = {r["q_id"]: dict(r) for r in conn.execute(
            "SELECT q_id, stem, difficulty, stage FROM questions WHERE verified=1")}
    finally:
        conn.close()
    return kps, qs


def load_gold(verified_qids: set[str]):
    rows = [json.loads(line) for line in GOLD_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    gold = [r for r in rows if r.get("status") == "confirmed" and r["q_id"] in verified_qids]
    return gold


def assign_exams(gold: list[dict]) -> dict[str, list[dict]]:
    """按 primary KP 分组后确定性分卷。

    规则：
    - 每个 primary KP 下按 q_id 排序；
    - 恰好 3 题的 KP：1 题远期(A) + 2 题近期(B)，用于覆盖近期权重机制；
    - 其他：A/B 交替；单题 KP 放到当前题量较少的卷（同量放 A）。
    """
    by_kp: dict[str, list[dict]] = defaultdict(list)
    for r in gold:
        by_kp[r["gold_primary"]].append(r)
    exams: dict[str, list[dict]] = {"A": [], "B": []}
    for kp in sorted(by_kp):
        qs = sorted(by_kp[kp], key=lambda r: r["q_id"])
        if len(qs) == 1:
            target = "A" if len(exams["A"]) <= len(exams["B"]) else "B"
            exams[target].append(qs[0])
        elif len(qs) == 3:
            for i, q in enumerate(qs):
                exams["A" if i == 0 else "B"].append(q)
        else:
            for i, q in enumerate(qs):
                exams["A" if i % 2 == 0 else "B"].append(q)
    for e in exams:
        exams[e].sort(key=lambda r: r["q_id"])
    return exams


def build_exam_questions(exams: dict[str, list[dict]], qmeta: dict) -> dict:
    out = {}
    for e, rows in exams.items():
        items = []
        for r in rows:
            diff = int(r.get("gold_difficulty") or qmeta[r["q_id"]]["difficulty"] or 1)
            score = SCORE_BY_DIFF.get(max(1, min(5, diff)), 4)
            items.append({
                "q_id": r["q_id"],
                "primary_kp": r["gold_primary"],
                "kp_ids": list(dict.fromkeys(r["gold_kp_ids"])),
                "difficulty": diff,
                "score": score,
                "stem": qmeta[r["q_id"]]["stem"],
            })
        out[e] = items
    return out


def build_students():
    return [
        {
            "student_id": f"stu_demo_{i + 1:02d}",
            "student_no": f"S{i + 1:02d}",
            "name": f"学生{i + 1:02d}",
            "ability": ABILITY_LEVELS[i % len(ABILITY_LEVELS)],
        }
        for i in range(STUDENTS)
    ]


def gen_score(ability: float, difficulty: int, effect: float, full: float):
    """确定性答题：能力 + 题目难度 + KP×考试场景效应 → 0/部分/满分。"""
    d = (max(1, min(5, difficulty)) - 1) / 4
    ratio = 0.70 + 0.50 * (ability - 0.60) - 0.25 * (d - 0.50) + effect
    ratio = max(0.0, min(1.0, ratio))
    if ratio >= 0.82:
        return float(full), 1
    if ratio <= 0.28:
        return 0.0, 0
    return round(full * ratio * 2) / 2, None


def build_answers(exam_qs: dict, students: list[dict]) -> list[dict]:
    answers = []
    for e in EXAMS:
        for q in exam_qs[e]:
            effect = EFFECTS.get(q["primary_kp"], {}).get(e, DEFAULT_EFFECT)
            for s in students:
                score, is_correct = gen_score(s["ability"], q["difficulty"], effect, q["score"])
                answers.append({
                    "exam": e,
                    "student_id": s["student_id"],
                    "q_id": q["q_id"],
                    "score": score,
                    "full_score": q["score"],
                    "is_correct": is_correct,
                })
    return answers


def compute_mastery(exam_qs: dict, answers: list[dict]) -> dict:
    by_key = defaultdict(list)
    for a in answers:
        by_key[(a["exam"], a["q_id"])].append(a)
    kp_items = defaultdict(list)
    for e in EXAMS:
        for q in exam_qs[e]:
            rows = by_key[(e, q["q_id"])]
            mean_ratio = sum(a["score"] / a["full_score"] for a in rows) / len(rows)
            for kp in q["kp_ids"]:
                kp_items[kp].append((e, q, mean_ratio))
    mastery = {}
    for kp, items in kp_items.items():
        num = den = 0.0
        eq_num = 0.0
        so_num = so_den = 0.0
        qids = set()
        exams_seen = set()
        for e, q, mean in items:
            w_rec = 2.0 if e == "B" else 1.0
            num += mean * q["score"] * w_rec
            den += q["score"] * w_rec
            eq_num += mean
            so_num += mean * q["score"]
            so_den += q["score"]
            qids.add(q["q_id"])
            exams_seen.add(e)
        mastery[kp] = {
            "mastery": round(num / den, 4),
            "equal_mastery": round(eq_num / len(items), 4),
            "score_only_mastery": round(so_num / so_den, 4),
            "samples": len(qids),
            "insufficient": len(qids) < 3,
            "weak": (len(qids) >= 3 and num / den < 0.6),
            "qids": sorted(qids),
            "exams": sorted(exams_seen),
        }
    return mastery


def load_graph() -> dict[str, list[str]]:
    payload = json.loads(EDGES_FILE.read_text(encoding="utf-8"))
    graph = defaultdict(list)
    for e in payload["edges"]:
        graph[e["target_id"]].append(e["source_id"])
    return graph


def _root_paths(node: str, graph: dict, mastery: dict, visited: frozenset) -> list[tuple]:
    """返回 (root, path_from_root_to_node, depth) 候选；只沿失败且样本足够的前置。"""
    if node in visited:
        return []
    out = []
    for p in graph.get(node, ()):
        m = mastery.get(p)
        if not m or m["insufficient"] or m["mastery"] >= 0.7:
            continue
        for root, path, depth in _root_paths(p, graph, mastery, visited | {node}):
            out.append((root, path + [node], depth + 1))
    if not out:
        return [(node, [node], 0)]
    return out


def attribute(mastery: dict, graph: dict, kps: dict) -> list[dict]:
    chains = []
    for target, m in sorted(mastery.items()):
        if m["insufficient"] or not m["weak"]:
            continue
        candidates = _root_paths(target, graph, mastery, frozenset())
        candidates.sort(key=lambda x: (-x[2], mastery[x[0]]["mastery"], x[0]))
        root, path, depth = candidates[0]
        path_mastery = [mastery[p]["mastery"] for p in path]
        min_samples = min(mastery[p]["samples"] for p in path)
        confidence = "high" if min_samples >= 5 else ("mid" if min_samples >= 3 else "low")
        chains.append({
            "target_kp": target,
            "target_name": kps[target]["name"],
            "root_kp": root,
            "root_name": kps[root]["name"],
            "path": path,
            "path_names": [kps[p]["name"] for p in path],
            "depth": depth,
            "path_mastery": path_mastery,
            "min_samples": min_samples,
            "confidence": confidence,
        })
    return chains


def answer_stats(exam_qs: dict, answers: list[dict], students: list[dict]) -> dict:
    """考试级与题目级答题分布（供设计审核）。"""
    by_key = defaultdict(list)
    by_student_total = defaultdict(float)
    for a in answers:
        by_key[(a["exam"], a["q_id"])].append(a)
        by_student_total[(a["exam"], a["student_id"])] += a["score"]

    exams = {}
    for e in EXAMS:
        totals = [by_student_total[(e, s["student_id"])] for s in students]
        full = partial = zero = 0
        ratios = []
        for q in exam_qs[e]:
            for a in by_key[(e, q["q_id"])]:
                if a["is_correct"] == 1:
                    full += 1
                elif a["is_correct"] == 0:
                    zero += 1
                else:
                    partial += 1
                ratios.append(a["score"] / a["full_score"])
        mean = sum(totals) / len(totals)
        std = (sum((x - mean) ** 2 for x in totals) / (len(totals) - 1)) ** 0.5 if len(totals) > 1 else 0.0
        exams[e] = {
            "students": len(totals),
            "mean_total": round(mean, 2),
            "std_total": round(std, 2),
            "min_total": min(totals),
            "max_total": max(totals),
            "full_marks": sum(q["score"] for q in exam_qs[e]),
            "answers_full": full,
            "answers_partial": partial,
            "answers_zero": zero,
            "mean_ratio": round(sum(ratios) / len(ratios), 4),
        }

    questions = []
    for e in EXAMS:
        for q in exam_qs[e]:
            rows = by_key[(e, q["q_id"])]
            rs = [a["score"] / a["full_score"] for a in rows]
            questions.append({
                "exam": e,
                "q_id": q["q_id"],
                "primary_kp": q["primary_kp"],
                "score": q["score"],
                "mean_ratio": round(sum(rs) / len(rs), 4),
                "answers_full": sum(1 for a in rows if a["is_correct"] == 1),
                "answers_partial": sum(1 for a in rows if a["is_correct"] is None),
                "answers_zero": sum(1 for a in rows if a["is_correct"] == 0),
            })
    return {"exams": exams, "questions": questions}


def checks(gold, exams, exam_qs, students, answers, mastery, chains, kps,
           verified_qids) -> dict:
    target_ok = all(mastery.get(t, {}).get("samples", 0) >= 3 for t in TARGETS)
    target_both = all(set(mastery.get(t, {}).get("exams", [])) == {"A", "B"} for t in TARGETS)
    # 近期权重差异：分值加权口径下，近期 2:1 与两场等权的差（隔离近期权重）
    recency_diffs = {kp: round(m["mastery"] - m["score_only_mastery"], 4)
                     for kp, m in mastery.items() if len(m["exams"]) == 2}
    # 分值加权差异：仅分值加权 vs 题目等权（隔离分值权重）
    score_diffs = {kp: round(m["score_only_mastery"] - m["equal_mastery"], 4)
                   for kp, m in mastery.items()}
    recency_flips = []
    score_flips = []
    for kp, m in mastery.items():
        if m["insufficient"]:
            continue
        for th in (0.6, 0.7):
            if (m["score_only_mastery"] < th <= m["mastery"]
                    or m["mastery"] < th <= m["score_only_mastery"]):
                recency_flips.append({"kp": kp, "threshold": th,
                                      "no_recency": m["score_only_mastery"],
                                      "with_recency": m["mastery"]})
            if (m["equal_mastery"] < th <= m["score_only_mastery"]
                    or m["score_only_mastery"] < th <= m["equal_mastery"]):
                score_flips.append({"kp": kp, "threshold": th,
                                    "equal": m["equal_mastery"],
                                    "score_weighted": m["score_only_mastery"]})
    chain_targets = {c["target_kp"] for c in chains}
    return {
        "verified_questions_49": len(gold) == 49,
        "students_20": len(students) == 20,
        "exams_2": set(exams) == {"A", "B"},
        "all_qids_verified": all(
            q["q_id"] in verified_qids for e in EXAMS for q in exam_qs[e]),
        "all_kps_mapped_to_82": all(
            kp in kps for e in EXAMS for q in exam_qs[e] for kp in q["kp_ids"]),
        "no_duplicate_qid_per_exam": all(
            len({q["q_id"] for q in exam_qs[e]}) == len(exam_qs[e]) for e in EXAMS),
        "answer_matrix_complete": len(answers) == sum(len(exam_qs[e]) for e in EXAMS) * len(students),
        "targets_samples_ge3": target_ok,
        "targets_in_both_exams": target_both,
        "deep_chain_3_nodes": any(len(c["path"]) >= 3 for c in chains),
        "insufficient_present": any(m["insufficient"] for m in mastery.values()),
        "recency_effect_present": any(abs(v) >= 0.02 for v in recency_diffs.values()),
        "score_weight_effect_present": any(abs(v) >= 0.01 for v in score_diffs.values()),
        "recency_flip_present": bool(recency_flips),
        "score_weight_flip_present": bool(score_flips),
        "recency_flips": recency_flips,
        "score_weight_flips": score_flips,
        "recency_diffs_top": sorted(recency_diffs.items(), key=lambda x: -abs(x[1]))[:5],
        "score_diffs_top": sorted(score_diffs.items(), key=lambda x: -abs(x[1]))[:5],
        "chain_targets": sorted(chain_targets),
    }


def render_md(design: dict) -> str:
    kps = design["kps"]
    lines = [
        "# 小学演示数据设计与检查报告（Phase 3 / S0-3）",
        "",
        "> **状态：设计草案，未导入、未写业务库。** 等待用户审核设计与映射后，再走 F16。",
        "",
        f"- 班级：{CLASS_NAME}（{GRADE} 年级 / primary）；学生 {STUDENTS} 人（虚拟）",
        f"- 考试：A=远期 {EXAM_DATE['A']}；B=近期 {EXAM_DATE['B']}",
        f"- 题目：49 道 verified 小学题（Gold v2 confirmed），全部显式 q_id → kp_id 映射",
        "- 样本口径：不同 q_id 数；同一题重复出现才计题次（本设计不重复题目）",
        "- **映射口径**：每题映射 = Gold primary + Gold 辅助 KP（均 verified）；一题可贡献多个 KP，掌握度按全部映射计入。",
        "- 分值口径：掌握度使用 F16 **卷面分值**（3–5 分），不使用题库占位分 1.0。",
        f"- 覆盖：49 道题映射到 **{len(design['mastery'])}** 个 KP；其余 "
        f"{len(design['kps']) - len(design['mastery'])} 个小学 KP 本轮无论证数据，演示中不出现（数据范围）。",
        "- 掌握度：卷面分值加权；近期考试权重 2、远期 1；<3 个不同 q_id 为 insufficient；<0.6 薄弱；前置 <0.7 失败",
        "",
        "## 0. 设计纪律",
        "",
        "1. **不按诊断结果反推数据**：答题由固定能力模型 + 场景表确定性生成；结果由规则计算，若与预期场景不符，如实报告，不调整数据凑结果。",
        "2. **显式映射**：q_id 与 kp_id 均来自 Gold v2 confirmed；不做名称模糊匹配、不按题序硬塞。",
        "3. **核心价值覆盖**：分值加权、近期 2:1、薄弱点、前置根因链、样本不足、数据范围。",
        "",
        "## 1. 考试构成",
        "",
    ]
    for e in EXAMS:
        qs = design["exams"][e]
        total = sum(q["score"] for q in qs)
        lines += [f"### 考试 {e}（{EXAM_DATE[e]}）— {len(qs)} 题 / {total} 分", "",
                  "| # | q_id | primary KP | 辅助 KP | 难度 | 分值 | 题干（截断） |",
                  "|---|---|---|---|---|---|---|"]
        for i, q in enumerate(qs, 1):
            aux = [kps[k]["name"] for k in q["kp_ids"] if k != q["primary_kp"]]
            lines.append(
                f"| {i} | {q['q_id']} | {kps[q['primary_kp']]['name']} | "
                f"{'、'.join(aux) or '—'} | {q['difficulty']} | {q['score']} | "
                f"{(q['stem'] or '').replace('|', '｜')[:40]} |")
        lines.append("")
    total_a = sum(q["score"] for q in design["exams"]["A"])
    total_b = sum(q["score"] for q in design["exams"]["B"])
    recent_share = (2 * total_b) / (2 * total_b + total_a) if (2 * total_b + total_a) else 0
    lines += [
        "### 1.1 分卷规则与近期权重", "",
        "- 每个 primary KP 下按 q_id 排序；恰好 3 题的 KP 分 1 题 A + 2 题 B；其余 A/B 交替；单题 KP 放当前题量较少的卷。",
        f"- 远期卷 A={total_a} 分 × 权重 1；近期卷 B={total_b} 分 × 权重 2 = {2 * total_b}；"
        f"近期权重占比约 {recent_share:.1%}。",
        "- 49 道题一次性分卷，不重复题目；样本数按不同 q_id 计。",
        "",
    ]
    lines += ["## 2. 知识点覆盖", "",
              "| KP | 名称 | 题数（不同 q_id） | 出现在 | 样本状态 | 掌握度 | 薄弱 |",
              "|---|---|---|---|---|---|---|"]
    for kp, m in sorted(design["mastery"].items(), key=lambda x: (x[1]["mastery"], x[0])):
        lines.append(
            f"| {kp} | {kps[kp]['name']} | {m['samples']} | {','.join(m['exams'])} | "
            f"{'insufficient' if m['insufficient'] else 'ok'} | {m['mastery']:.3f} | "
            f"{'是' if m['weak'] else '否'} |")
    lines += ["", "### 2.1 答题模式与分布", "",
              "| 考试 | 人数 | 平均总分 | 标准差 | 最低 | 最高 | 满分/部分/零分 | 平均得分率 |",
              "|---|---|---|---|---|---|---|---|"]
    for e in EXAMS:
        s = design["answer_stats"]["exams"][e]
        lines.append(
            f"| {e} | {s['students']} | {s['mean_total']} | {s['std_total']} | "
            f"{s['min_total']} | {s['max_total']} | "
            f"{s['answers_full']}/{s['answers_partial']}/{s['answers_zero']} | "
            f"{s['mean_ratio']:.4f} |")
    lines += [
        "",
        "目标链题目作答（class mean；完整 980 条答题见 JSON）：",
        "",
        "| 考试 | q_id | primary KP | 分值 | 平均得分率 | 满分/部分/零分 |",
        "|---|---|---|---|---|---|",
    ]
    for q in design["answer_stats"]["questions"]:
        if q["primary_kp"] not in TARGETS:
            continue
        lines.append(
            f"| {q['exam']} | {q['q_id']} | {kps[q['primary_kp']]['name']} | {q['score']} | "
            f"{q['mean_ratio']:.4f} | {q['answers_full']}/{q['answers_partial']}/{q['answers_zero']} |")
    lines += ["", "## 3. 学生能力分布（虚拟）", "",
              "| 编号 | 姓名 | 能力档 |", "|---|---|---|"]
    for s in design["students"]:
        lines.append(f"| {s['student_no']} | {s['name']} | {s['ability']:.2f} |")
    lines += ["", "## 4. 答题生成规则", "",
              "```",
              "d = (难度 - 1) / 4",
              "ratio = clamp(0.70 + 0.50*(能力 - 0.60) - 0.25*(d - 0.50) + 场景效应, 0, 1)",
              "ratio >= 0.82 → 满分 / 对；ratio <= 0.28 → 0 / 错；其余 → 四舍五入到 0.5 分 / 未判",
              "```",
              "",
              "场景效应表（仅按教学机制覆盖设定；默认 0）：",
              "",
              "| primary KP | 远期 A | 近期 B | 说明 |",
              "|---|---|---|---|",
              ]
    for kp, eff in EFFECTS.items():
        lines.append(f"| {kps[kp]['name']} | {eff['A']:+.2f} | {eff['B']:+.2f} | "
                     f"{SCENARIO_DESC.get(kp, '演示场景')} |")
    lines += ["", "## 5. 预期掌握度与诊断场景", "",
              "### 5.1 掌握度（分值加权 + 近期 2:1）", "",
              "| KP | 名称 | 掌握度 | 等权均值 | 仅分值加权 | 样本 | 状态 |",
              "|---|---|---|---|---|---|---|"]
    for kp, m in sorted(design["mastery"].items(), key=lambda x: (x[1]["mastery"], x[0])):
        status = "insufficient" if m["insufficient"] else ("薄弱" if m["weak"] else "正常")
        lines.append(
            f"| {kp} | {kps[kp]['name']} | {m['mastery']:.4f} | {m['equal_mastery']:.4f} | "
            f"{m['score_only_mastery']:.4f} | {m['samples']} | {status} |")
    lines += ["", "### 5.2 预期归因链（规则计算，非导入结果）", ""]
    if design["chains"]:
        lines += ["| 目标 KP | 根因 | 路径 | 置信 | 路径掌握度 |",
                  "|---|---|---|---|---|"]
        for c in design["chains"]:
            lines.append(
                f"| {c['target_name']} | {c['root_name']} | "
                f"{' → '.join(c['path_names'])} | {c['confidence']} | "
                f"{', '.join(f'{x:.3f}' for x in c['path_mastery'])} |")
    else:
        lines.append("无（没有同时满足样本足够且薄弱的 KP）。")
    lines += ["", "### 5.3 机制覆盖检查", ""]
    ck = design["checks"]
    lines += ["| 检查项 | 结果 |", "|---|---|",
              *[f"| {k} | {v} |" for k, v in ck.items()
                if k not in ("recency_diffs_top", "score_diffs_top", "chain_targets",
                             "recency_flips", "score_weight_flips")],
              "",
              "近期权重差异 Top：",
              *[f"- {kps[k]['name']}: {v:+.4f}" for k, v in ck["recency_diffs_top"]],
              "",
              "分值加权差异 Top：",
              *[f"- {kps[k]['name']}: {v:+.4f}" for k, v in ck["score_diffs_top"]],
              "",
              "### 5.4 机制翻转案例（阈值两侧）", "",
              "| 机制 | KP | 阈值 | 未加权/无近期 | 加权/有近期 |",
              "|---|---|---|---|---|",
              *[f"| 近期 2:1 | {kps[x['kp']]['name']} | {x['threshold']} | "
                f"{x['no_recency']:.4f} | {x['with_recency']:.4f} |"
                for x in ck["recency_flips"]],
              *[f"| 分值加权 | {kps[x['kp']]['name']} | {x['threshold']} | "
                f"{x['equal']:.4f} | {x['score_weighted']:.4f} |"
                for x in ck["score_weight_flips"]],
              "",
              "**解读（如实报告，不做数据反推）**：",
              *[f"- 近期 2:1 在 **{kps[x['kp']]['name']}** 出现 {x['threshold']} 阈值翻转："
                f"无近期权重 {x['no_recency']:.4f} → 有近期权重 {x['with_recency']:.4f}；"
                f"若忽略近期权重，它会被判为失败前置并可能改变根因链。"
                for x in ck["recency_flips"]],
              *([f"- 分值加权在 **{kps[x['kp']]['name']}** 出现 {x['threshold']} 阈值翻转："
                 f"等权 {x['equal']:.4f} → 分值加权 {x['score_weighted']:.4f}。"
                 for x in ck["score_weight_flips"]]
                if ck["score_weight_flips"] else
                ["- 分值加权未出现跨阈值翻转：最大差异见“分值加权差异 Top”；权重已生效，但不为了制造翻转而调数据。"]),
              f"- 比与按比例分配近期下降约 {abs(dict(ck['recency_diffs_top']).get('kp_p_038', 0)):.4f}，"
              "但仍在 0.6 以上，作为观察项，不输出归因链。",
              "- 小数运算/比与按比例分配/百分数运算虽有弱前置，但目标未达薄弱线，本轮不输出归因链，避免过度归因。",
              f"- {sum(1 for m in design['mastery'].values() if m['insufficient'])} 个 KP 样本 <3，"
              "Phase 3 将返回 insufficient 与原因，不输出推测性结论；这是 49 道 verified 题的边界，不是失败。",
              "",
              "## 6. 预期诊断结论（供审核）", "",
              f"- 薄弱且样本足够的 KP：{', '.join(sorted(c['target_name'] for c in design['chains'])) or '无'}",
              f"- sufficient 的根因链数量：{len(design['chains'])}",
              f"- insufficient 的 KP：{', '.join(kps[k]['name'] for k, m in design['mastery'].items() if m['insufficient']) or '无'}",
              "",
              "## 7. 待用户确认的设计口径", "",
              "1. **样本数 = 不同 q_id 数**（不是 20 名学生的答题数）。是否同意？",
              "2. **班级级诊断**：当前数据按全班聚合；学生级需要每个学生 ≥3 题/ KP，本数据不满足。是否同意 Phase 3 演示按班级级？",
              "3. 两场考试使用 **49 道 verified 题的一次性分卷**，不做同题重复；是否同意？",
              "4. 场景表是“机制覆盖”设定，不是真实学生数据；导入后实际诊断结果以引擎计算为准，若与预期不一致会如实报告，不调数据。",
              "",
              "## 8. 下一步", "",
              "- 用户审核本设计与映射表 → 确认后生成 F16 工作簿并导入 → 导入后核验 + 业务冒烟 + 测试。",
              "",
              "> 本报告由 `backend/scripts/design_primary_demo.py` 生成；未写业务库、未调 LLM、未产生费用。",
              ""]
    return "\n".join(lines)


def main() -> int:
    kps, qmeta = load_kps_and_questions()
    gold = load_gold(set(qmeta))
    if len(gold) != 49:
        print(f"verified gold 应为 49 条，实际 {len(gold)}")
        return 1
    missing = [r["q_id"] for r in gold if r["q_id"] not in qmeta]
    if missing:
        print(f"存在不在 verified 题库的题：{missing}")
        return 1
    missing = [r["q_id"] for r in gold if any(k not in kps for k in r["gold_kp_ids"])]
    if missing:
        print(f"存在未映射到 82 小学 KP 的题：{missing}")
        return 1

    exams = assign_exams(gold)
    exam_qs = build_exam_questions(exams, qmeta)
    students = build_students()
    answers = build_answers(exam_qs, students)
    mastery = compute_mastery(exam_qs, answers)
    a_stats = answer_stats(exam_qs, answers, students)
    graph = load_graph()
    chains = attribute(mastery, graph, kps)
    ck = checks(gold, exams, exam_qs, students, answers, mastery, chains, kps, set(qmeta))

    design = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "design_draft_not_imported",
        "class": {"name": CLASS_NAME, "grade": GRADE, "stage": "primary"},
        "exam_meta": {e: {"title": EXAM_TITLE[e], "date": EXAM_DATE[e]} for e in EXAMS},
        "kps": {k: {"name": v["name"]} for k, v in kps.items()},
        "exams": exam_qs,
        "students": students,
        "answers": answers,
        "mastery": mastery,
        "answer_stats": a_stats,
        "chains": chains,
        "scenario_effects": EFFECTS,
        "checks": ck,
    }
    OUT_JSON.write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_MD.write_text(render_md(design), encoding="utf-8")
    print(f"设计已生成：{OUT_MD}")
    print(f"原始设计：{OUT_JSON}")
    print(f"A/B 题量：{len(exam_qs['A'])}/{len(exam_qs['B'])}；"
          f"总分：{sum(q['score'] for q in exam_qs['A'])}/"
          f"{sum(q['score'] for q in exam_qs['B'])}；答题记录：{len(answers)}")
    print("检查：")
    for k, v in ck.items():
        if k not in ("recency_diffs_top", "score_diffs_top", "chain_targets"):
            print(f"  {k}: {v}")
    print(f"归因链：{len(chains)}")
    for c in chains:
        print(f"  {c['target_name']} ← root {c['root_name']} | {' → '.join(c['path_names'])} | {c['confidence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
