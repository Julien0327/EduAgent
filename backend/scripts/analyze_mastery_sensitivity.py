"""掌握度角色权重敏感性分析（Phase 3 / S1，D1）。

对演示班（默认按名称定位）分别计算 λ∈{0, 0.5, 1} 的掌握度：
- 逐 KP 掌握度、状态、primary_only_mastery、aux_share、样本明细；
- λ 之间状态翻转数量；
- 与 S0 历史设计值的差异（设计是主+辅等权，等价 λ=1，仅舍入方式不同）。

只读，不写库、不改冻结数据、不调 LLM。输出 staging 报告。

用法（仓库根执行）：
    .venv/Scripts/python.exe -X utf8 -m backend.scripts.analyze_mastery_sensitivity
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from backend.app.diagnosis import data_access
from backend.app.diagnosis.mastery import compute_mastery

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN_FILE = REPO_ROOT / "data" / "staging" / "primary_demo_design.json"
OUT_MD = REPO_ROOT / "data" / "staging" / "mastery_sensitivity_report.md"
OUT_JSON = REPO_ROOT / "data" / "staging" / "mastery_sensitivity_report.json"

LAMBDAS = (0.0, 0.5, 1.0)
CLASS_NAME = "六年级演示班"


def load_design_mastery() -> dict[str, dict]:
    if not DESIGN_FILE.exists():
        return {}
    design = json.loads(DESIGN_FILE.read_text(encoding="utf-8"))
    return design.get("mastery", {})


def main() -> int:
    conn = data_access.connect()
    try:
        cls = data_access.load_demo_class(conn, CLASS_NAME)
        loaded = data_access.load_class_evidence(conn, cls["class_id"])
        all_kp_ids = {r["kp_id"] for r in conn.execute(
            "SELECT kp_id FROM knowledge_points WHERE stage='primary'")}
    finally:
        conn.close()

    evidence = loaded["evidence"]
    exams = loaded["exams"]
    design = load_design_mastery()

    results = {str(lam): compute_mastery(evidence, exams, lam, all_kp_ids=all_kp_ids)
               for lam in LAMBDAS}

    all_kps = sorted(results["0.5"])
    def state_of(lam, kp):
        return results[str(lam)][kp]["state"]

    flip_counts = {}
    for i in range(len(LAMBDAS) - 1):
        a, b = LAMBDAS[i], LAMBDAS[i + 1]
        flips = [kp for kp in all_kps if state_of(a, kp) != state_of(b, kp)]
        flip_counts[f"{a}->{b}"] = {"count": len(flips), "kps": flips}

    lines = [
        "# 掌握度角色权重敏感性分析（Phase 3 / S1，D1）",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- 班级：{loaded['data_scope']['class_name']}（{loaded['data_scope']['class_id']}）",
        f"- 数据范围：{loaded['data_scope']['exam_count']} 考试 / "
        f"{loaded['data_scope']['question_count']} 题 / "
        f"{loaded['data_scope']['student_count']} 学生 / "
        f"{loaded['data_scope']['answer_count']} 答题 / "
        f"覆盖 {loaded['data_scope']['kp_covered_count']} 个 KP（全集 {len(all_kp_ids)} 个）",
        "- λ 含义：辅助 KP 证据权重（primary 恒为 1.0）",
        "",
        "## 1. 状态翻转数量（D1）",
        "",
        "| 对比 | 翻转数 | 翻转 KP |",
        "|---|---|---|",
    ]
    for key, info in flip_counts.items():
        kps = "、".join(info["kps"]) or "无"
        lines.append(f"| {key} | {info['count']} | {kps} |")
    lines += [
        "",
        "## 2. 逐 KP 对比（λ=0 / 0.5 / 1）",
        "",
        "| KP | 状态(0/0.5/1) | mastery 0 | 0.5 | 1 | primary_only | aux_share(0.5) | sample | 主样本 | 辅样本 | 加权题量 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for kp in all_kps:
        r0, r05, r1 = results["0.0"][kp], results["0.5"][kp], results["1.0"][kp]
        def fmt(x):
            return "-" if x is None else f"{x:.4f}"
        lines.append(
            f"| {kp} | {r0['state']}/{r05['state']}/{r1['state']} | "
            f"{fmt(r0['mastery'])} | {fmt(r05['mastery'])} | {fmt(r1['mastery'])} | "
            f"{fmt(r05['primary_only_mastery'])} | {fmt(r05['aux_share'])} | "
            f"{r05['sample_count']} | {r05['primary_sample_count']} | "
            f"{r05['aux_sample_count']} | {fmt(r05['weighted_evidence_count'])} |")

    lines += [
        "",
        "## 3. 与历史设计值差异（D1）",
        "",
        "> 历史设计掌握度是主+辅等权（等价 λ=1），但用 Python round() 银行家舍入；",
        "> 本表与设计值对比仅作参考，不为了匹配设计而调整数据或公式。",
        "",
        "| KP | 设计值 | λ=1 本实现 | 差 |",
        "|---|---|---|---|",
    ]
    for kp in all_kps:
        d = design.get(kp, {}).get("mastery")
        m1 = results["1.0"][kp]["mastery"]
        diff = "-" if (d is None or m1 is None) else f"{m1 - d:+.4f}"
        lines.append(f"| {kp} | {d if d is None else f'{d:.4f}'} | "
                     f"{m1 if m1 is None else f'{m1:.4f}'} | {diff} |")

    lines += [
        "",
        "## 4. 结论",
        "",
        f"- λ=0.5 时薄弱（weak）KP："
        f"{', '.join(sorted(k for k in all_kps if state_of(0.5, k) == 'weak')) or '无'}",
        f"- λ=0.5 时证据不足（insufficient）KP 数："
        f"{sum(1 for k in all_kps if state_of(0.5, k) == 'insufficient')}",
        f"- λ=0.5 时无覆盖（no_coverage）KP 数："
        f"{sum(1 for k in all_kps if state_of(0.5, k) == 'no_coverage')}",
        "- 以上为确定性计算结果，未调整数据或公式；λ 最终值待用户结合本报告裁决（D1）。",
        "",
        "## 5. 无覆盖 KP（no_coverage）",
        "",
        f"- 全集 {len(all_kp_ids)} 个小学 KP，覆盖 {loaded['data_scope']['kp_covered_count']} 个；",
        f"- no_coverage：{', '.join(sorted(k for k in all_kp_ids if state_of(0.5, k) == 'no_coverage')) or '无'}",
        f"- 数量：{sum(1 for k in all_kp_ids if state_of(0.5, k) == 'no_coverage')}",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "class_id": loaded["data_scope"]["class_id"],
        "data_scope": loaded["data_scope"],
        "all_kp_count": len(all_kp_ids),
        "results": {str(lam): results[str(lam)] for lam in LAMBDAS},
        "flip_counts": flip_counts,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"数据范围：{loaded['data_scope']}")
    for key, info in flip_counts.items():
        print(f"状态翻转 {key}: {info['count']} -> {info['kps']}")
    print(f"λ=0.5 薄弱: {[k for k in all_kps if state_of(0.5, k) == 'weak']}")
    print(f"报告：{OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
