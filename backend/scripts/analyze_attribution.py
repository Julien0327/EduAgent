"""归因结果分析（Phase 3 / S2，只读，λ=0.5 固定）。

对演示班计算 λ=0.5 掌握度 → 归因链 → staging 报告。不改库、不调 LLM。

用法（仓库根执行）：
    .venv/Scripts/python.exe -X utf8 -m backend.scripts.analyze_attribution
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from backend.app.diagnosis import data_access
from backend.app.diagnosis.attribution import attribute, build_prereq_graph, detect_cycles
from backend.app.diagnosis.mastery import compute_mastery

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_MD = REPO_ROOT / "data" / "staging" / "attribution_report.md"
OUT_JSON = REPO_ROOT / "data" / "staging" / "attribution_report.json"

LAM = 0.5
CLASS_NAME = "六年级演示班"


def main() -> int:
    conn = data_access.connect()
    try:
        cls = data_access.load_demo_class(conn, CLASS_NAME)
        loaded = data_access.load_class_evidence(conn, cls["class_id"])
        all_kp_ids = {r["kp_id"] for r in conn.execute(
            "SELECT kp_id FROM knowledge_points WHERE stage='primary'")}
        names = {r["kp_id"]: r["name"] for r in conn.execute(
            "SELECT kp_id, name FROM knowledge_points WHERE stage='primary'")}
        edges = [{"from_kp": r["from_kp"], "to_kp": r["to_kp"]}
                 for r in conn.execute("SELECT from_kp, to_kp FROM kp_edges")]
    finally:
        conn.close()

    cycles = detect_cycles(edges)
    mastery = compute_mastery(loaded["evidence"], loaded["exams"], LAM, all_kp_ids=all_kp_ids)
    prereqs = build_prereq_graph(edges)
    chains = attribute(mastery, prereqs, names=names, evidence=loaded["evidence"])

    lines = [
        "# 归因结果报告（Phase 3 / S2）",
        "",
        f"- 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"- 班级：{loaded['data_scope']['class_name']}；λ=0.5（固定，已裁决）",
        f"- 图谱：{len(edges)} 条前置边；环检测：{'发现环 ' + str(cycles) if cycles else '无环'}",
        f"- 薄弱 KP {len(chains)} 个；数据范围：{loaded['data_scope']}",
        "",
        "## 1. 归因链",
        "",
        "| 目标 | 根因 | 路径 | 冲突 | 阻断前置 | 置信 | 最小样本 | 目标掌握度 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in chains:
        lines.append(
            f"| {c['target_name']} | {c['root_name']} | "
            f"{' → '.join(c['path_names'])} | {'是' if c['conflict'] else '否'} | "
            f"{'、'.join(c['blocked_prereq_names']) or '无'} | {c['confidence']} | "
            f"{c['min_samples']} | {c['target_mastery']:.4f} |")

    lines += [
        "",
        "## 2. 逐链详情",
        "",
    ]
    for c in chains:
        lines += [
            f"### {c['target_name']}（{c['target_kp']}）",
            f"- 根因：{c['root_name']}（{c['root_kp']}）",
            f"- 路径：{' → '.join(c['path_names'])}",
            f"- 根因候选：{'、'.join(str(x['kp_id']) + '(' + format(x['mastery'], '.4f') + ')' for x in c['root_candidates'])}",
            f"- 分支冲突：{c['conflict']}；阻断前置：{c['blocked_prereq_names'] or '无'}",
            f"- 置信度：{c['confidence']}；最小样本：{c['min_samples']}；aux_share：{c['target_aux_share']}",
            f"- 证据链（题↔角色↔考试）：",
        ]
        for e in c["evidence"]:
            lines.append(f"    - {e['q_id']} [{e['role']}] 考试{e['exam_label']}#{e['q_no']} → {names.get(e['kp_id'], e['kp_id'])}")
        lines.append("")

    lines += [
        "## 3. 结论",
        "",
        f"- 归因链 {len(chains)} 条；无环（{'是' if not cycles else '否'}）。",
        "- 归因为确定性规则计算；LLM 仅在后续步骤据此做自然语言叙述，不改变事实。",
        f"- 报告：{OUT_JSON}",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    OUT_JSON.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "class_id": loaded["data_scope"]["class_id"],
        "data_scope": loaded["data_scope"],
        "lam": LAM,
        "cycles": cycles,
        "chains": chains,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"归因链 {len(chains)} 条；环 {len(cycles)}")
    for c in chains:
        print(f"  {c['target_name']} ← {c['root_name']} | {' → '.join(c['path_names'])} | {c['confidence']}")
    print(f"报告：{OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
