"""ETL 数据完整性核验（Phase 1 / Step 4，方案 A 修复后全量审计，2026-11-12）。

从原始 APE210K 文件独立重放清洗/验算/去重（复用 backend.app.data.ape210k
纯函数），与库内 questions 逐行逐字段比对——不信任 ETL 脚本自身的漏斗计数，
在数据层面提供修复恢复证据（用户要求：不能仅凭测试全绿宣布数据修复完成）。

核验项：
1. SHA-256 门禁（与 DATA-SOURCES.md 基线一致，复用 etl_ape210k 的校验值）
2. 漏斗独立重算：原始/题干无效/id 缺失/完全重复/验算三态/疑似变式/入库数
3. 逐行比对：库内 stem/answer/analysis/difficulty/quality_state 与重放期望
   完全一致（含"库内 stem = normalize(原始)"，证明清洗管线无实现层偏差）
4. 星号保留（方案 A 核心验证）：非残留星号（乘号/指数）在库内 stem 中
   逐一保留（数量一致）；统计乘号行/指数行/非标准记法行/残留清除行
5. 题干差异统计：原始 -> 库内变化分类（无变化/仅空白归一/仅残留清除/
   空白归一+残留清除/其他——其他应为 0）
6. 疑似异常清单（q_id）：非标准星号记法保留行（方案 A 原样保留，如循环
   小数 **(•)）；行首/句末残留清除行（清洗为预期动作）
7. 汇总核对：库内总量 = 漏斗重算入库数；FTS 行数 = processed；B 类 10 题
   FTS 同步；小学 KP 数/挂接数；初中 303 KP 不变

用法（仓库根执行，须先跑 etl_ape210k + build_primary_kp）：
    .venv/Scripts/python.exe -m backend.scripts.verify_etl_integrity [--db <路径>] [--raw-dir <目录>]
"""
import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

from backend.app.data import ape210k
from backend.app.storage import db
from backend.scripts.etl_ape210k import (
    DEFAULT_RAW_DIR, EXPECTED_SHA256, SPLITS, check_raw_files, sha256_of,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / "data" / "staging" / "etl_integrity_report.json"

# 星号形态分类（与 sample_questions_audit 同口径）
_SINGLE_STAR = re.compile(r"(?<!\*)\*(?!\*)")
_EXPONENT_STAR = re.compile(r"[0-9A-Za-z一-鿿)]\*\*[0-9]")   # 含括号底数 (-1)**2
_EXP_ONLY = re.compile(r"(?<=[0-9A-Za-z一-鿿)])\*\*(?=[0-9])")
_WS = re.compile(r"[\s　\xa0]+")

# 方案 A 修复验收对象：抽样发现的 10 道 B 类题（FTS 同步逐题核验）
B_CLASS_QIDS = (
    "q_ape_678403", "q_ape_1060185", "q_ape_58143", "q_ape_209048", "q_ape_230513",
    "q_ape_290121", "q_ape_942935", "q_ape_1200515", "q_ape_634755", "q_ape_87500",
)


def _nonstandard_stars(text: str) -> bool:
    """星号既非单个乘号、也非指数形态（如循环小数 5.{_{4}**(•)}）。"""
    t = _SINGLE_STAR.sub("", text)
    t = _EXP_ONLY.sub("", t)
    return "*" in t


def run(db_path, raw_dir: Path) -> dict:
    conn = db.connect(db_path)
    stats: Counter = Counter()
    diff: Counter = Counter()
    star_stats: Counter = Counter()
    anomalies_nonstandard: list[str] = []   # 非标准记法保留行（方案 A 原样保留）
    anomalies_residue: list[str] = []       # 行首/句末残留清除行（预期清洗动作）
    mismatches: list[dict] = []             # 库内与重放期望不一致（应为空）
    seen_keys: set[str] = set()

    db_total = conn.execute(
        "SELECT COUNT(*) n FROM questions WHERE q_id LIKE 'q_ape_%'").fetchone()["n"]
    fts_total = conn.execute(
        "SELECT COUNT(*) n FROM questions_fts WHERE q_id LIKE 'q_ape_%'").fetchone()["n"]

    t0 = time.time()
    for name in SPLITS:
        with open(raw_dir / name, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                stats["raw_total"] += 1

                mapped, drop_reason = ape210k.map_raw(item)
                if mapped is None:
                    stats[drop_reason] += 1
                    continue

                verdict = ape210k.verify_equation(mapped["analysis"], mapped["answer"])
                stats[f"verify_{verdict}"] += 1
                state = "rejected" if verdict == "mismatch" else "processed"

                key = ape210k.dedup_key(mapped["stem"], mapped["answer"])
                if key in seen_keys:
                    stats["duplicate_exact"] += 1
                    continue
                seen_keys.add(key)
                stats["written_" + state] += 1

                # ---- 库内逐字段比对（q_id 主键点查，流式不整载内存） ----
                q_id = mapped["q_id"]
                row = conn.execute(
                    "SELECT stem, answer, analysis, difficulty, quality_state"
                    " FROM questions WHERE q_id=?", (q_id,)).fetchone()
                if row is None:
                    mismatches.append({"q_id": q_id, "field": "row", "detail": "库内缺失"})
                    continue
                for field, expected in (
                    ("stem", mapped["stem"]), ("answer", mapped["answer"]),
                    ("analysis", mapped["analysis"]), ("difficulty", mapped["difficulty"]),
                    ("quality_state", state),
                ):
                    if row[field] != expected:
                        mismatches.append({"q_id": q_id, "field": field,
                                           "detail": f"库内={row[field]!r} 期望={expected!r}"})

                # ---- 星号保留（数据层证据） ----
                original = str(item.get("original_text") or "")
                kept = ape210k.RESIDUE_STAR_RE.sub(r"\1", original)
                if kept.count("*") != row["stem"].count("*"):
                    mismatches.append({"q_id": q_id, "field": "stars",
                                       "detail": f"原始非残留星号 {kept.count('*')} 个，库内 {row['stem'].count('*')} 个"})
                if "*" in original:
                    star_stats["rows_with_star"] += 1
                    if _SINGLE_STAR.search(kept):
                        star_stats["rows_mult"] += 1
                    if _EXPONENT_STAR.search(kept):
                        star_stats["rows_exp"] += 1
                    if _nonstandard_stars(kept):
                        star_stats["rows_nonstandard"] += 1
                        anomalies_nonstandard.append(q_id)
                    if ape210k.RESIDUE_STAR_RE.search(original):
                        star_stats["rows_residue_cleaned"] += 1
                        anomalies_residue.append(q_id)
                if kept.count("*"):
                    star_stats["restored_rows"] += 1  # 旧规则会删星号、新规则保留的行

                # ---- 题干差异分类（原始 -> 库内） ----
                if original == row["stem"]:
                    diff["无变化"] += 1
                else:
                    residue_hit = ape210k.RESIDUE_STAR_RE.search(original) is not None
                    ws_hit = _WS.sub(" ", original).strip() != original
                    if residue_hit and ws_hit:
                        diff["空白归一+残留清除"] += 1
                    elif residue_hit:
                        diff["仅残留清除"] += 1
                    elif ws_hit:
                        diff["仅空白归一"] += 1
                    else:
                        diff["其他（应为0）"] += 1
    elapsed = round(time.time() - t0, 1)

    # ---- 汇总核对 ----
    checks: dict[str, bool] = {}
    expected_total = stats["written_processed"] + stats["written_rejected"]
    checks["库内总量 = 漏斗重算入库数"] = db_total == expected_total
    checks["FTS 行数 = processed"] = fts_total == stats["written_processed"]

    fts_sync_ok = 0
    for q_id in B_CLASS_QIDS:
        fts_stem = conn.execute(
            "SELECT stem FROM questions_fts WHERE q_id=?", (q_id,)).fetchone()
        db_stem = conn.execute(
            "SELECT stem FROM questions WHERE q_id=?", (q_id,)).fetchone()
        if fts_stem and db_stem and fts_stem["stem"] == db_stem["stem"] and "*" in fts_stem["stem"]:
            fts_sync_ok += 1
    checks[f"B 类 10 题 FTS 同步且含星号"] = fts_sync_ok == len(B_CLASS_QIDS)

    kp_primary = conn.execute(
        "SELECT COUNT(*) n FROM knowledge_points WHERE source='ape210k_rule'").fetchone()["n"]
    kp_junior = conn.execute(
        "SELECT COUNT(*) n FROM knowledge_points WHERE stage='junior'").fetchone()["n"]
    kp_links = conn.execute(
        "SELECT COUNT(*) n FROM q_kp WHERE q_id LIKE 'q_ape_%'").fetchone()["n"]
    checks["小学 KP = 82"] = kp_primary == 82
    checks["初中 KP = 303（不受影响）"] = kp_junior == 303
    checks["逐行比对不一致 = 0"] = not mismatches
    conn.close()

    report = {
        "verified_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_seconds": elapsed,
        "funnel_replay": dict(stats),
        "diff_classification": dict(diff),
        "star_stats": dict(star_stats),
        "db_total": db_total,
        "fts_total": fts_total,
        "kp": {"primary": kp_primary, "junior": kp_junior, "ape_links": kp_links},
        "checks": checks,
        "mismatch_count": len(mismatches),
        "mismatches_sample": mismatches[:50],
        "anomaly_nonstandard_qids": anomalies_nonstandard,
        "anomaly_residue_qids": anomalies_residue,
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def print_report(report: dict) -> None:
    f = report["funnel_replay"]
    print(f"核验完成：{report['elapsed_seconds']}s（原始 {f['raw_total']} 行独立重放 + 全库逐行比对）")
    print(f"漏斗重算：原始 {f['raw_total']} | 题干无效 {f.get('stem_invalid', 0)} | id 缺失 {f.get('id_missing', 0)}"
          f" | 完全重复剔除 {f['duplicate_exact']}")
    print(f"  验算：一致 {f.get('verify_match', 0)} / 不一致(rejected) {f.get('verify_mismatch', 0)}"
          f" / 无法验算 {f.get('verify_unverifiable', 0)}")
    print(f"  入库：processed {f['written_processed']} + rejected {f['written_rejected']}"
          f"（与 ETL 报告一致性见 checks）")
    s = report["star_stats"]
    print(f"星号保留（方案 A）：含星号行 {s.get('rows_with_star', 0)} | 乘号行 {s.get('rows_mult', 0)}"
          f" | 指数行 {s.get('rows_exp', 0)} | 非标准记法行 {s.get('rows_nonstandard', 0)}"
          f" | 残留清除行 {s.get('rows_residue_cleaned', 0)}")
    print(f"  **本次修复恢复（旧规则删星号、新规则保留）的题目：{s.get('restored_rows', 0)} 行**")
    print(f"题干差异分类：{report['diff_classification']}")
    print(f"库内总量 {report['db_total']} / FTS {report['fts_total']} / "
          f"小学 KP {report['kp']['primary']}（挂接 {report['kp']['ape_links']}）/ "
          f"初中 KP {report['kp']['junior']}")
    print("汇总核对：")
    for name, ok in report["checks"].items():
        print(f"  {'✓' if ok else '✗'} {name}")
    if report["mismatch_count"]:
        print(f"!! 逐行比对不一致 {report['mismatch_count']} 处（前 50 条见 JSON 报告）")
    if report["anomaly_nonstandard_qids"]:
        qids = report["anomaly_nonstandard_qids"]
        print(f"疑似异常（非标准星号记法，方案 A 原样保留）：{len(qids)} 行，"
              f"如 {', '.join(qids[:5])}…（全清单见 JSON 报告）")
    if report["anomaly_residue_qids"]:
        print(f"残留清除行（预期清洗动作）：{report['anomaly_residue_qids']}")
    print(f"报告已写入：{REPORT_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description="ETL 后全量数据完整性核验（方案 A 修复验证）")
    parser.add_argument("--db", default=None)
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--skip-hash", action="store_true", help="跳过 SHA-256 校验（仅调试用）")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    check_raw_files(raw_dir)
    if not args.skip_hash:
        for name in SPLITS:
            actual = sha256_of(raw_dir / name)
            if actual != EXPECTED_SHA256[name]:
                print(f"SHA-256 不一致：{name}\n  期望 {EXPECTED_SHA256[name]}\n  实际 {actual}\n拒绝核验。")
                return 1
        print("SHA-256 校验通过（3 个文件与核验记录一致）")

    report = run(args.db, raw_dir)
    print_report(report)
    ok = all(report["checks"].values())
    print(f"\n结论：{'全部核验通过 ✓' if ok else '存在未通过项 ✗（见上）'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
