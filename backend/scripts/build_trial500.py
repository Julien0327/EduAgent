"""500 题分层试验集构建器（Step 5-B1，设计文档 §6 第二版）。

分层协议（决策③：A/B 双臂共用同一批 500 题）：
- 12 层 + 纯随机参考层，优先级互斥（S1→S11 依序首中即入层），S12 从剩余全域抽。
- 全部抽样 SQL 严格限定 quality_state='processed'（第二版修订：processed 口径池）。
- 规则挂接仅用于定义抽样层与分层分析，绝不进入 prompt、绝不作 gold（决策㉔）。
- 确定性：random.Random(seed) 对按 q_id 排序的池抽样；产出冻结清单
  data/annotations/trial500_qids.jsonl（仅 q_id + 层标签，不含题目内容——决策㉒）。

实测池修正（2026-11-14，processed 口径）：S4=含标准乘号/指数记法（非标准记法归 S5，
避免 S4 吞掉 S5 专用池）；S6=退化(574 入库)+非终结符结尾(26)，交集实测 0，其中
零挂接部分会被 S1 优先消耗（实测 314/600），剩余池 286 仍足额。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

from backend.app.storage import db
from backend.scripts.scan_raw_anomalies import DEGEN_EQ, DEFAULT_RAW_DIR, INCOMPLETE_ENDING

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "data" / "annotations" / "trial500_qids.jsonl"

SEED = 42
S12_QUOTA = 120
STRATA_PLAN: list[tuple[str, int]] = [
    ("S1_zero_link", 40), ("S2_main_placeholder", 30), ("S3_other_parens", 10),
    ("S4_star_math", 20), ("S5_nonstandard_star", 10), ("S6_degenerate_incomplete", 10),
    ("S7_lowfreq_kp", 40), ("S8a_single_link", 15), ("S8b_many_links", 15),
    ("S9_d1", 30), ("S9_d2", 30), ("S9_d3", 30), ("S9_d4", 30), ("S9_d5", 30),
    ("S10_long_stem", 20), ("S11_pct_answer", 10), ("S11_frac_answer", 10),
]

_SINGLE_STAR = re.compile(r"(?<!\*)\*(?!\*)")
_EXP_ONLY = re.compile(r"(?<=[0-9A-Za-z一-鿿)])\*\*(?=[0-9])")
LOW_FREQ_KP_THRESHOLD = 1000   # 设计文档 §6：挂接 <1,000 的 24 个低频 KP


def _nonstandard_star(stem: str) -> bool:
    """星号既非单乘号也非指数形态（循环小数 **(•) / 车牌打码等）。"""
    t = _SINGLE_STAR.sub("", stem)
    t = _EXP_ONLY.sub("", t)
    return "*" in t


def scan_s6_raw_ids(raw_dir: Path) -> set[str]:
    """原始文件重扫退化算式 + 非终结符结尾 q_id 集合（与 scan_raw_anomalies 同口径）。"""
    ids: set[str] = set()
    for name in ("train.ape.json", "valid.ape.json", "test.ape.json"):
        with open(raw_dir / name, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                eq = str(item.get("equation") or "").strip()
                stem = str(item.get("original_text") or "")
                if (eq and DEGEN_EQ.fullmatch(eq)) or (stem and stem[-1] in INCOMPLETE_ENDING):
                    ids.add(f"q_ape_{item.get('id')}")
    return ids


def build_trial(db_path: Path | None, output: Path, seed: int = SEED,
                strata_plan: list[tuple[str, int]] | None = None,
                s12_quota: int | None = None,
                s6_raw_ids: set[str] | None = None) -> dict:
    """构建分层试验集。strata_plan/s12_quota/s6_raw_ids 供测试注入小规模数据；
    生产路径（默认参数）= 设计文档 §6 的 500 题协议 + 原始文件重扫 S6。"""
    plan = strata_plan or STRATA_PLAN
    s12 = S12_QUOTA if s12_quota is None else s12_quota
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT q_id, stem, difficulty, answer FROM questions"
        " WHERE q_id LIKE 'q_ape_%' AND quality_state='processed'").fetchall()
    links = {
        r["q_id"]: r["n"]
        for r in conn.execute(
            "SELECT k.q_id q_id, COUNT(*) n FROM q_kp k"
            " JOIN questions q ON q.q_id = k.q_id AND q.quality_state='processed'"
            " GROUP BY k.q_id")
    }
    kp_counts = {
        r["kp_id"]: r["n"]
        for r in conn.execute(
            "SELECT k.kp_id kp_id, COUNT(*) n FROM q_kp k"
            " JOIN questions q ON q.q_id = k.q_id AND q.quality_state='processed'"
            " GROUP BY k.kp_id")
    }
    conn.close()

    info = {r["q_id"]: r for r in rows}
    all_ids = set(info)
    assigned: dict[str, str] = {}
    pool_report: list[dict] = []

    def take(stratum: str, quota: int, pool: set[str]) -> None:
        remaining = pool - set(assigned)
        pool_report.append({"stratum": stratum, "quota": quota,
                            "pool_remaining": len(remaining)})
        k = min(quota, len(remaining))
        if k < quota:
            print(f"⚠️ 层 {stratum} 剩余池 {len(remaining)} < 配额 {quota}，只取 {k}")
        for q_id in random.Random(seed).sample(sorted(remaining), k):
            assigned[q_id] = stratum

    low_freq_kps = {kp for kp, n in kp_counts.items() if n < LOW_FREQ_KP_THRESHOLD}
    low_freq_pool: set[str] = set()
    conn = db.connect(db_path)
    if low_freq_kps:
        ph = ",".join("?" * len(low_freq_kps))
        low_freq_pool = {
            r["q_id"] for r in conn.execute(
                f"SELECT DISTINCT k.q_id q_id FROM q_kp k JOIN questions q"
                f" ON q.q_id = k.q_id AND q.quality_state='processed'"
                f" WHERE k.kp_id IN ({ph})", tuple(sorted(low_freq_kps)))
        }
    conn.close()

    s6_raw = scan_s6_raw_ids(DEFAULT_RAW_DIR) if s6_raw_ids is None else set(s6_raw_ids)

    # ---- 优先级互斥分层（S1→S11 依序首中即入层）----
    pools: dict[str, set[str]] = {}
    pools["S1_zero_link"] = {q for q in all_ids if links.get(q, 0) == 0}
    pools["S2_main_placeholder"] = {q for q in all_ids if "((())/(()))" in info[q]["stem"]}
    pools["S3_other_parens"] = {q for q in all_ids
                                if "()" in info[q]["stem"] and "((())/(()))" not in info[q]["stem"]}
    star_std = {q for q in all_ids if "*" in info[q]["stem"] and not _nonstandard_star(info[q]["stem"])}
    star_nonstd = {q for q in all_ids if _nonstandard_star(info[q]["stem"])}
    pools["S4_star_math"] = star_std
    pools["S5_nonstandard_star"] = star_nonstd
    pools["S6_degenerate_incomplete"] = {q for q in (s6_raw & all_ids)}
    pools["S7_lowfreq_kp"] = low_freq_pool
    pools["S8a_single_link"] = {q for q in all_ids if links.get(q, 0) == 1}
    pools["S8b_many_links"] = {q for q in all_ids if links.get(q, 0) >= 6}
    for d in range(1, 6):
        pools[f"S9_d{d}"] = {q for q in all_ids if info[q]["difficulty"] == d}
    pools["S10_long_stem"] = {q for q in all_ids if len(info[q]["stem"]) > 70}
    pools["S11_pct_answer"] = {q for q in all_ids if "%" in info[q]["answer"]}
    pools["S11_frac_answer"] = {q for q in all_ids
                                if "/" in info[q]["answer"] and "%" not in info[q]["answer"]}

    for stratum, quota in plan:
        take(stratum, quota, pools[stratum])

    # ---- S12 纯随机参考层（processed 全域剩余，seed 固定）----
    take("S12_random", s12, all_ids)

    # 实际样本数 = 各层 min(配额, 剩余池) 之和（配额大于池时如实取池容量并告警）
    expected_total = sum(min(p["quota"], p["pool_remaining"]) for p in pool_report)
    assert len(assigned) == expected_total, \
        f"样本数 {len(assigned)} != 预期 {expected_total}"

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        for q_id, stratum in assigned.items():
            f.write(json.dumps({"q_id": q_id, "stratum": stratum},
                               ensure_ascii=False) + "\n")

    summary = {"seed": seed, "total": len(assigned), "output": str(output),
               "pools": pool_report,
               "by_stratum": _count_by_stratum(assigned)}
    return summary


def _count_by_stratum(assigned: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for s in assigned.values():
        counts[s] = counts.get(s, 0) + 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 500 题分层试验集（processed 口径，确定性抽样）")
    parser.add_argument("--db", default=None)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    summary = build_trial(args.db, Path(args.output), args.seed)
    print(f"试验集构建完成：seed={summary['seed']}，共 {summary['total']} 题 → {summary['output']}")
    print(f"{'层':<28}{'配额':>6}{'剩余池':>10}")
    for p in summary["pools"]:
        print(f"{p['stratum']:<28}{p['quota']:>6}{p['pool_remaining']:>10}")
    print("实际抽样：", json.dumps(summary["by_stratum"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
