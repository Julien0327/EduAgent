"""题库 100 题完整率抽样脚本（Phase 1 / Step 4，用户复核关口）。

口径声明（决策⑥/⑲）：
- 本清单度量**必要字段完整程度**，不代表答案正确率，也不代表知识点标注
  准确率——三者分开统计，不得混淆。
- 抽样可复现：seed=42，总体 = questions 中 source='ape210k' 且
  quality_state='processed' 的题按 q_id 排序，random.Random(42).sample 取 100。
- 判定项（全部确定性规则，逐题在清单中列出，供人工复核）：
  A stem 非空且 ≥6 字符；B answer 非空且可解析（数值/分数/百分数/带分数）；
  C 题干含数字（数学题必要特征）；D 无 Markdown 残留 **；
  E difficulty ∈ [1,5] 且 score > 0；F type/stage/subject/source 非空。
- 输出 data/staging/ape210k_sample100.md（含题目内容，**不入 Git**）；
  统计摘要（完整率数字）回填 STATUS.md。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.sample_questions_audit [--db <路径>] [--seed 42]
"""
import argparse
import random
import sys
from datetime import datetime
from pathlib import Path

from backend.app.data import ape210k
from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_OUT = REPO_ROOT / "data" / "staging" / "ape210k_sample100.md"


def completeness_flags(row) -> dict[str, bool]:
    """逐项完整性判定（确定性规则，与脚本 docstring 口径一致）。"""
    stem = row["stem"] or ""
    kind, _ = ape210k.parse_answer(row["answer"] or "")
    return {
        "A_stem_valid": len(stem) >= 6,
        "B_answer_parseable": kind != "other" and bool((row["answer"] or "").strip()),
        "C_stem_has_number": any(c.isdigit() for c in stem),
        "D_no_md_residue": "**" not in stem,
        "E_domain_ok": (row["difficulty"] is not None and 1 <= row["difficulty"] <= 5
                        and row["score"] is not None and row["score"] > 0),
        "F_enums_ok": all((row[c] or "").strip() for c in ("type", "stage", "subject", "source")),
    }


def run(db_path, seed: int = 42, size: int = 100) -> dict:
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT q_id, stem, answer, analysis, type, stage, subject, source,"
        "       score, difficulty, quality_state"
        " FROM questions WHERE source='ape210k' AND quality_state='processed'"
        " ORDER BY q_id"
    ).fetchall()
    picked = random.Random(seed).sample(rows, size)

    results = []
    for row in picked:
        flags = completeness_flags(row)
        results.append({"row": row, "flags": flags,
                        "complete": all(flags.values())})

    complete_n = sum(1 for r in results if r["complete"])
    kp_linked = conn.execute(
        "SELECT COUNT(*) n FROM q_kp WHERE q_id IN (%s)" % ",".join("?" * size),
        [r["row"]["q_id"] for r in results],
    ).fetchone()["n"]
    distinct_linked = conn.execute(
        "SELECT COUNT(DISTINCT q_id) n FROM q_kp WHERE q_id IN (%s)" % ",".join("?" * size),
        [r["row"]["q_id"] for r in results],
    ).fetchone()["n"]
    conn.close()

    lines = [
        "# APE210K 题库 100 题完整率抽样清单",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}　seed={seed}　总体={len(rows)}（processed）",
        "- 口径：必要字段完整程度（见脚本 docstring 判定项 A–F），"
        "**不代表答案正确率，不代表 KP 标注准确率**",
        f"- 完整率：**{complete_n}/{size}**",
        f"- KP 挂接参考：{distinct_linked}/{size} 题至少挂 1 个小学知识点（共 {kp_linked} 条挂接）",
        "",
        "| # | q_id | A题干≥6字 | B答案可解析 | C含数字 | D无残留 | E分值难度域 | F枚举齐 | 完整 | 题干 | 答案 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(results, 1):
        f = r["flags"]
        mark = lambda ok: "✓" if ok else "✗"
        lines.append(
            f"| {i} | {r['row']['q_id']} | {mark(f['A_stem_valid'])} | {mark(f['B_answer_parseable'])}"
            f" | {mark(f['C_stem_has_number'])} | {mark(f['D_no_md_residue'])} | {mark(f['E_domain_ok'])}"
            f" | {mark(f['F_enums_ok'])} | {mark(r['complete'])}"
            f" | {r['row']['stem'][:40]} | {r['row']['answer']} |"
        )
    SAMPLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_OUT.write_text("\n".join(lines), encoding="utf-8")

    return {"population": len(rows), "seed": seed, "size": size,
            "complete": complete_n, "kp_distinct_linked": distinct_linked,
            "out": str(SAMPLE_OUT)}


def main() -> int:
    parser = argparse.ArgumentParser(description="题库 100 题完整率抽样（可复现）")
    parser.add_argument("--db", default=None)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    result = run(args.db, args.seed)
    print(f"抽样完成：seed={result['seed']} 总体={result['population']}")
    print(f"完整率：{result['complete']}/{result['size']}")
    print(f"KP 挂接参考：{result['kp_distinct_linked']}/{result['size']} 题至少挂 1 个知识点")
    print(f"清单（含题目内容，不入 Git）：{result['out']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
