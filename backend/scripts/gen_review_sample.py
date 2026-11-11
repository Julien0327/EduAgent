"""图谱抽样复核清单生成（Phase 1 / Step 2 收尾）。

从业务库读取已入库图谱，输出：
1. 统计摘要（总数 / 边数 / 根节点数 / 按年级与章节分布 / 孤岛与环检查）
2. 20% 随机抽样复核清单（固定 seed 可复现），供人工核对

产物（决策⑳：校验报告与统计摘要入 Git）：
- data/reports/kp_review_sample.md    人工核对用清单
- data/reports/kp_review_sample.json  机器可读版本

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.gen_review_sample [--db <路径>]
"""
import argparse
import json
import random
import sys
from pathlib import Path

from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = REPO_ROOT / "data" / "reports"
SAMPLE_RATE = 0.20
SAMPLE_SEED = 42


def load_graph_from_db(conn) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    """从库中读取节点与前置边，组装成校验器输入形态。"""
    nodes = {
        row["kp_id"]: {
            "kp_id": row["kp_id"],
            "name": row["name"],
            "chapter": row["chapter"],
            "description": row["description"],
        }
        for row in conn.execute("SELECT * FROM knowledge_points")
    }
    edges = [
        (row["from_kp"], row["to_kp"])
        for row in conn.execute("SELECT * FROM kp_edges")
    ]
    return nodes, edges


def main() -> int:
    parser = argparse.ArgumentParser(description="生成图谱抽样复核清单")
    parser.add_argument("--db", default=None)
    args = parser.parse_args()

    from backend.scripts.build_graph import validate_graph

    conn = db.connect(args.db)
    try:
        nodes, edges = load_graph_from_db(conn)
        grade_rows = conn.execute("SELECT kp_id, grade FROM knowledge_points").fetchall()
    finally:
        conn.close()
    grade_map = {r["kp_id"]: r["grade"] for r in grade_rows}

    # 入库态复跑校验器（fail-fast：已入库数据必须仍通过全部规则）
    errors = validate_graph(nodes, edges)
    if errors:
        print(f"[入库态校验失败] {len(errors)} 项：\n" + "\n".join(errors), file=sys.stderr)
        return 1

    rows = list(nodes.values())

    # 统计
    by_grade: dict[int, int] = {}
    by_chapter: dict[str, int] = {}
    for r in rows:
        g = grade_map.get(r["kp_id"])
        by_grade[g] = by_grade.get(g, 0) + 1
        ch = (r.get("chapter") or "未标注").rsplit("·", 1)[0]
        by_chapter[ch] = by_chapter.get(ch, 0) + 1
    targets = {to for _, to in edges}
    roots = [kp for kp in nodes if kp not in targets]

    # 固定 seed 抽样（可复现）
    rng = random.Random(SAMPLE_SEED)
    sample_size = max(1, round(len(rows) * SAMPLE_RATE))
    sample = sorted(rng.sample(rows, sample_size), key=lambda r: r["kp_id"])

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # 机器可读版
    payload = {
        "sample_rate": SAMPLE_RATE,
        "seed": SAMPLE_SEED,
        "summary": {
            "total_kp": len(rows),
            "total_edges": len(edges),
            "roots": len(roots),
            "by_grade": {str(k): v for k, v in sorted(by_grade.items(), key=lambda x: (x[0] is None, x[0]))},
            "by_chapter": dict(sorted(by_chapter.items())),
            "validation": "pass (入库态复跑：引用/DAG/孤岛/主干链/根节点元数据 全部通过)",
        },
        "sample": [
            {"kp_id": r["kp_id"], "name": r["name"], "chapter": r.get("chapter"),
             "description": r.get("description")}
            for r in sample
        ],
    }
    json_path = REPORTS_DIR / "kp_review_sample.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 人工核对用 Markdown
    lines = [
        "# 知识图谱 20% 抽样复核清单",
        "",
        f"> 抽样比例 {SAMPLE_RATE:.0%}（seed={SAMPLE_SEED}，可复现）；共 {sample_size}/{len(rows)} 条。",
        f"> 入库态校验：引用完整 ✅ DAG 无环 ✅ 意外孤岛 0 ✅ 主干链可达 ✅ 根节点元数据 ✅",
        f"> 图谱规模：{len(rows)} 知识点 / {len(edges)} 条前置边 / {len(roots)} 根节点。",
        "",
        "## 请核对每条：①名称是否准确 ②章节归属是否合理 ③前置依赖是否符合教学顺序 ④描述是否有误导",
        "",
        "| # | kp_id | 名称 | 章节 | 难度 | 前置依赖 | 描述 | 核对结果（OK/问题） |",
        "|---|---|---|---|---|---|---|---|",
    ]
    # 难度与前置需回库查（summary 只取了部分列）
    conn = db.connect(args.db)
    try:
        full = {r["kp_id"]: r for r in conn.execute("SELECT * FROM knowledge_points").fetchall()}
    finally:
        conn.close()
    succ_map: dict[str, list[str]] = {}
    for src, dst in edges:
        succ_map.setdefault(dst, []).append(src)
    for i, r in enumerate(sample, 1):
        kp = full[r["kp_id"]]
        pre = "、".join(sorted(succ_map.get(r["kp_id"], []))) or "（无前置，根节点）"
        lines.append(
            f"| {i} | {r['kp_id']} | {r['name']} | {r.get('chapter') or '—'} "
            f"| {kp['difficulty']} | {pre} | {r.get('description') or '—'} | |"
        )
    lines += ["", "## 各章分布", "", "| 教材分册 | 知识点数 |", "|---|---|"]
    lines += [f"| {ch} | {n} |" for ch, n in by_chapter.items()]
    md_path = REPORTS_DIR / "kp_review_sample.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(
        f"抽样清单已生成：{md_path}（{sample_size} 条）\n"
        f"统计摘要：{json_path}\n"
        f"图谱：{len(rows)} 知识点 / {len(edges)} 边 / {len(roots)} 根节点；年级分布 {by_grade}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
