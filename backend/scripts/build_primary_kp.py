"""小学知识点聚合入库脚本（Phase 1 / Step 4，决策㉒）。

从已入库的 APE210K processed 题（source='ape210k'）按规则推导挂接小学
知识点：kp_id kp_p_001..（规则表顺序，编号冻结），source='ape210k_rule'，
独立于 Step 2 初中图谱。

幂等语义：全量重建——先删 source='ape210k_rule' 的 KP 与 q_ape_% 题的
q_kp 挂接，再按当前规则表重建；重跑结果与首次一致。初中图谱数据不受影响。

用法（仓库根执行，需先跑 etl_ape210k.py）：
    .venv/Scripts/python.exe -m backend.scripts.build_primary_kp [--db <路径>]
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from backend.app.data import primary_kp
from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / "data" / "staging" / "primary_kp_report.json"


def run(db_path) -> dict:
    conn = db.connect(db_path)
    t0 = time.time()

    with conn:
        # 只清小学规则 KP 与 ape 来源挂接，初中图谱（source='curriculum'）不动
        conn.execute("DELETE FROM q_kp WHERE q_id LIKE 'q_ape_%'")
        conn.execute("DELETE FROM knowledge_points WHERE source='ape210k_rule'")

        kp_rows = [
            (primary_kp.build_kp_id(i), rule["name"], "math", "primary", None, None, 2,
             f"规则推导：{rule['description']}", "ape210k_rule")
            for i, rule in enumerate(primary_kp.KP_RULES, 1)
        ]
        conn.executemany(
            """INSERT INTO knowledge_points
                 (kp_id, name, subject, stage, grade, chapter, difficulty, description, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            kp_rows,
        )

    name_to_id = {rule["name"]: primary_kp.build_kp_id(i)
                  for i, rule in enumerate(primary_kp.KP_RULES, 1)}

    # 流式遍历 processed 的 ape 题，规则匹配挂接
    cur = conn.execute(
        "SELECT q_id, stem, analysis FROM questions WHERE source='ape210k' AND quality_state='processed'"
    )
    links: list[tuple[str, str]] = []
    kp_hit_counter: Counter[str] = Counter()
    covered = 0
    total = 0
    for row in cur:
        total += 1
        q_id, stem = row["q_id"], row["stem"]
        eq = row["analysis"] or ""  # 挂接特征用原始算式
        hits = primary_kp.match_kps(stem, eq)
        if hits:
            covered += 1
            for name in hits:
                links.append((q_id, name_to_id[name]))
                kp_hit_counter[name] += 1

    with conn:
        conn.executemany("INSERT OR IGNORE INTO q_kp (q_id, kp_id) VALUES (?, ?)", links)

    elapsed = time.time() - t0
    kp_counts = {primary_kp.build_kp_id(i): kp_hit_counter.get(rule["name"], 0)
                 for i, rule in enumerate(primary_kp.KP_RULES, 1)}
    report = {
        "kp_total": len(primary_kp.KP_RULES),
        "questions_total": total,
        "questions_with_kp": covered,
        "coverage_ratio": round(covered / total, 4) if total else None,
        "avg_kp_per_question": round(len(links) / covered, 2) if covered else 0,
        "links_total": len(links),
        "zero_hit_kp": [primary_kp.build_kp_id(i) for i, rule in enumerate(primary_kp.KP_RULES, 1)
                        if kp_hit_counter.get(rule["name"], 0) == 0],
        "top_kp": sorted(((primary_kp.build_kp_id(i), rule["name"], n)
                          for i, rule in enumerate(primary_kp.KP_RULES, 1)
                          for n in [kp_hit_counter.get(rule["name"], 0)]),
                         key=lambda x: -x[2])[:10],
        "bottom_kp": sorted(((primary_kp.build_kp_id(i), rule["name"], n)
                             for i, rule in enumerate(primary_kp.KP_RULES, 1)
                             for n in [kp_hit_counter.get(rule["name"], 0)]),
                            key=lambda x: x[2])[:10],
        "elapsed_seconds": round(elapsed, 1),
    }
    conn.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="小学知识点规则聚合入库（幂等全量重建）")
    parser.add_argument("--db", default=None, help="业务库路径（默认 data/db/edumate.sqlite）")
    args = parser.parse_args()

    report = run(args.db)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"小学 KP 聚合完成：{report['elapsed_seconds']}s")
    print(f"KP 总数 {report['kp_total']}；processed 题 {report['questions_total']}")
    print(f"覆盖：{report['questions_with_kp']} 题（{report['coverage_ratio']:.1%}），"
          f"均 {report['avg_kp_per_question']} KP/题，共 {report['links_total']} 条挂接")
    if report["zero_hit_kp"]:
        print(f"零命中 KP：{report['zero_hit_kp']}")
    print(f"Top10：{[(i, n) for i, _, n in report['top_kp']]}")
    print(f"Bottom10：{[(i, n) for i, _, n in report['bottom_kp']]}")
    print(f"报告已写入：{REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
