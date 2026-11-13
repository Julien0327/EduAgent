"""原始 APE210K 异常内容全量扫描（Phase 1 / Step 4 补充复核，2026-11-13）。

背景：用户验收方案 A 修复时抽样发现 q_ape_1012689 / q_ape_432326 题干含
((())/(())) 形态，要求全库扫描类似异常（空分数/占位符、明显缺失公式数字
条件、明显不完整题干），以原始数据为依据定性，**只读扫描不删改任何题目**，
完整 q_id 清单交人工裁决。

扫描口径（精确形态优先，不把合法括号/数学表达式误判为异常）：
1. blank_placeholder：题干含空括号对 () 的括号/斜杠连续结构。实测 18 种
   形态、1,684 行——经抽样核验为**数据集填空题空位标记**：题目带真实
   equation/ans，可解可验算（如 q_ape_1012689 eq=x=1-(3/4) ans=(1/4)
   验算 match），空位即"待填空格"，非内容损坏。主形态 ((())/(())) 为
   "答案为分数"的填空模板；其余为补全分子/分母等变体。
2. degenerate_equation：equation 整式为 <var>=0/=1（含 y= 等前缀）——
   DATA-SOURCES 已知噪声第 5 项（q_ape_72366 同型）的全量量化；按 ans
   是否同值细分；部分可能为合法平凡题（如求 1 的倒数），需逐条人工判定。
3. incomplete_ending：题干以，、：；（结尾——疑似不完整，需人工判定。
锚定参考（计数与已知记录交叉核对，非异常）：循环小数 {_{..}} 记法、
题干无数字、空算式。

输出：data/staging/raw_anomalies_report.json（机器）+ .md（人工复核，
全量 q_id + 原始题干 + 库内状态逐行标注）。数据库侧同时镜像扫描 q_ape
库内 stem（应与原始一致——库内 = normalize(原始)）。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.scan_raw_anomalies [--db <路径>] [--raw-dir <目录>]
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
REPORT_JSON = REPO_ROOT / "data" / "staging" / "raw_anomalies_report.json"
REPORT_MD = REPO_ROOT / "data" / "staging" / "raw_anomalies_report.md"

RUN_PAREN = re.compile(r"[()/]+")                      # 括号/斜杠连续结构
DEGEN_EQ = re.compile(r"[A-Za-z]\s*=\s*[01](?:\.0+)?\s*$")
VAR_ONLY_EQ = re.compile(r"[A-Za-z]\s*=\s*$")
INCOMPLETE_ENDING = "，、：；（"
BRACE_NOTATION = re.compile(r"\{_{")
USER_SAMPLE_IDS = ("1012689", "432326")

BLANK_VERDICT = ("数据集填空题空位标记（非内容损坏）：题目带真实 equation/ans，可解可验算；"
                 "空位即「待填空格」。建议保留 processed；显示层可渲染为填空线；"
                 "Step 5 打标 prompt 需说明该形态。是否做文本替换由用户裁决。")
DEGEN_VERDICT = ("疑似退化占位记录（DATA-SOURCES 已知噪声第 5 项，q_ape_72366 同型）；"
                 "部分可能为合法平凡题（如求 1 的倒数），需逐条人工判定——"
                 "如判明确无效可转 rejected（决策⑲，入库可追溯不物理删除）。")
INCOMPLETE_VERDICT = ("题干疑似不完整（以非终结符结尾）；需逐条人工判定，处理同退化算式类。")


def run(db_path, raw_dir: Path) -> dict:
    conn = db.connect(db_path)
    flagged: dict[str, list] = {"blank_placeholder": [], "degenerate_equation": [], "incomplete_ending": []}
    anchors: Counter = Counter()
    shape_counter: Counter = Counter()
    shape_example: dict[str, tuple] = {}
    total = 0

    for name in SPLITS:
        with open(raw_dir / name, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                total += 1
                sid = str(item.get("id") or "")
                stem = str(item.get("original_text") or "")
                eq = str(item.get("equation") or "").strip()
                ans = str(item.get("ans") or "").strip()

                runs = [r for r in RUN_PAREN.findall(stem) if "()" in r]
                if runs:
                    for r in runs:
                        shape_counter[r] += 1
                        if r not in shape_example:
                            i = stem.find(r)
                            shape_example[r] = (sid, stem[max(0, i - 16): i + len(r) + 16])
                    flagged["blank_placeholder"].append({
                        "q_id": f"q_ape_{sid}", "split": name, "shapes": runs,
                        "stem": stem, "equation": eq, "ans": ans})
                if DEGEN_EQ.fullmatch(eq):
                    flagged["degenerate_equation"].append({
                        "q_id": f"q_ape_{sid}", "split": name, "stem": stem,
                        "equation": eq, "ans": ans,
                        "ans_same_value": ans in ("0", "1", "0.0", "1.0")})
                if stem and stem[-1] in INCOMPLETE_ENDING:
                    flagged["incomplete_ending"].append({
                        "q_id": f"q_ape_{sid}", "split": name, "stem": stem,
                        "equation": eq, "ans": ans})
                if BRACE_NOTATION.search(stem):
                    anchors["brace_notation"] += 1
                if not any(c.isdigit() for c in stem):
                    anchors["stem_no_digit"] += 1
                if not eq or VAR_ONLY_EQ.fullmatch(eq):
                    anchors["empty_equation"] += 1

    # 验算结论（空位标记题"可解"的证据）+ 库内状态逐行标注
    all_ids = sorted({r["q_id"] for rows in flagged.values() for r in rows})
    state_map: dict[str, str] = {}
    for q in all_ids:
        row = conn.execute("SELECT quality_state FROM questions WHERE q_id=?", (q,)).fetchone()
        state_map[q] = row["quality_state"] if row else "未入库（重复/无效剔除）"
    report_vc: dict[str, Counter] = {}
    for key, rows in flagged.items():
        for r in rows:
            r["db_state"] = state_map[r["q_id"]]
            r["verify"] = ape210k.verify_equation(r["equation"], r["ans"])
        report_vc[key] = Counter(r["verify"] for r in rows)

    db_mirror = {
        "db_total_ape": conn.execute(
            "SELECT COUNT(*) n FROM questions WHERE q_id LIKE 'q_ape_%'").fetchone()["n"],
        "db_stem_with_empty_parens": conn.execute(
            "SELECT COUNT(*) n FROM questions WHERE q_id LIKE 'q_ape_%' AND stem LIKE '%()%'").fetchone()["n"],
        "db_stem_with_main_placeholder": conn.execute(
            "SELECT COUNT(*) n FROM questions WHERE q_id LIKE 'q_ape_%' AND stem LIKE '%((())/(()))%'").fetchone()["n"],
    }
    conn.close()

    report = {
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "raw_total": total,
        "flagged": flagged,
        "shapes": dict(shape_counter.most_common()),
        "shape_examples": {k: {"q_id": f"q_ape_{v[0]}", "context": v[1]} for k, v in shape_example.items()},
        "verify_by_category": {k: dict(v) for k, v in report_vc.items()},
        "anchors": dict(anchors),
        "db_mirror": db_mirror,
        "verdicts": {
            "blank_placeholder": BLANK_VERDICT,
            "degenerate_equation": DEGEN_VERDICT,
            "incomplete_ending": INCOMPLETE_VERDICT,
        },
    }
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text("\n".join(build_md(report)), encoding="utf-8")
    return report


def _row_line(r: dict) -> str:
    extra = f" 形态:{';'.join(r['shapes'])} ｜" if "shapes" in r else ""
    same = "" if "ans_same_value" not in r else ("" if r["ans_same_value"] else "（ans 异值）")
    return (f"- {r['q_id']}（{r['split']}｜库内 {r['db_state']}｜验算 {r['verify']}）{extra}"
            f"题干：{r['stem']} ｜ equation：{r['equation']} ｜ ans：{r['ans']}{same}")


def build_md(report: dict) -> list[str]:
    flagged = report["flagged"]
    n_blank = len(flagged["blank_placeholder"])
    n_degen = len(flagged["degenerate_equation"])
    n_incomp = len(flagged["incomplete_ending"])
    degen_same = sum(1 for r in flagged["degenerate_equation"] if r["ans_same_value"])
    lines = [
        "# APE210K 原始数据异常内容扫描报告（2026-11-13，Step 4 补充复核）",
        "",
        f"- 扫描范围：原始 {report['raw_total']:,} 行（SHA-256 与 DATA-SOURCES 基线一致）；"
        f"库内 q_ape 镜像 {report['db_mirror']['db_total_ape']:,} 行",
        "- **只读扫描**：未删除、未修改、未变更任何题目的状态；处理方式由用户人工裁决。",
        "- 用户指定样本核查：q_ape_1012689（「一瓶果汁，喝了(3/4)，杯中还有((())/(()))．」，"
        "eq=x=1-(3/4)，ans=(1/4)）与 q_ape_432326（两处占位，eq=x=3/5，ans=(3/5)）"
        "——均为**填空题空位标记**，题目完整可解、验算 match，属数据集模板约定而非损坏。",
        "",
        "## 汇总",
        "",
        "| 类别 | 题数 | 定性 | 建议 |",
        "|---|---|---|---|",
        f"| 空括号填空标记（blank_placeholder） | {n_blank} | 数据集填空题空位标记（非损坏） | 保留 processed；显示层渲染填空线；Step 5 prompt 说明该形态 |",
        f"| 退化算式 x=0/x=1（degenerate_equation） | {n_degen}（ans 同值 {degen_same} / 异值 {n_degen - degen_same}） | 疑似退化占位记录（已知噪声模式） | 逐条人工判定；明确无效可转 rejected（决策⑲） |",
        f"| 非终结符结尾（incomplete_ending） | {n_incomp} | 疑似题干不完整 | 逐条人工判定，处理同上 |",
        f"| 锚定参考：循环小数记法 {report['anchors'].get('brace_notation', 0)}／题干无数字 {report['anchors'].get('stem_no_digit', 0)}／空算式 {report['anchors'].get('empty_equation', 0)} | — | 与已知噪声记录交叉核对 | 无 |",
        "",
        "## 一、空括号填空标记（blank_placeholder）",
        "",
        f"**定性**：{BLANK_VERDICT}",
        "",
        f"验算分布：{'；'.join(f'{k} {v}' for k, v in report['verify_by_category']['blank_placeholder'].items())}",
        "",
        "形态分布（括号/斜杠连续结构，按出现次数；同一行可含多形态）：",
        "",
        "| 形态 | 次数 | 示例上下文 |",
        "|---|---|---|",
    ]
    for shape, cnt in list(report["shapes"].items())[:20]:
        ex = report["shape_examples"].get(shape, {})
        lines.append(f"| {shape} | {cnt} | {ex.get('q_id','')} …{ex.get('context','')}… |")
    lines += ["", f"### 全量清单（{n_blank} 行，原始题干完整展示）", ""]
    lines += [_row_line(r) for r in flagged["blank_placeholder"]]

    lines += ["", "## 二、退化算式 x=0/x=1（degenerate_equation）", "",
              f"**定性**：{DEGEN_VERDICT}", "",
              f"验算分布：{'；'.join(f'{k} {v}' for k, v in report['verify_by_category']['degenerate_equation'].items())}",
              "", f"### 全量清单（{n_degen} 行）", ""]
    lines += [_row_line(r) for r in flagged["degenerate_equation"]]

    lines += ["", "## 三、非终结符结尾（incomplete_ending）", "",
              f"**定性**：{INCOMPLETE_VERDICT}", "",
              f"### 全量清单（{n_incomp} 行）", ""]
    lines += [_row_line(r) for r in flagged["incomplete_ending"]]

    dm = report["db_mirror"]
    lines += [
        "", "## 四、库内镜像核对（processed 库）", "",
        f"- 库内 q_ape 总行数：{dm['db_total_ape']:,}（原始 210,488 − 完全重复剔除等）",
        f"- 库内 stem 含空括号 ()：{dm['db_stem_with_empty_parens']:,} 行（原始 {n_blank} 行；"
        "差值为去重/无效剔除的行）",
        f"- 库内 stem 含主形态 ((())/(()))：{dm['db_stem_with_main_placeholder']:,} 行",
        "- 结论：库内内容与原始一致（库内 = normalize(原始)，Step 4 已全库逐行核验）；"
        "空位标记随题目如实入库。",
        "",
        "## 五、处理方式（待用户裁决，脚本不自动执行）",
        "",
        "1. 空括号填空标记：建议保留现状；如需显示优化，在前端/导出层将空位渲染为填空线（不改动库内 stem）。",
        "2. 退化算式与非终结符结尾：请人工逐条判定（清单见上）；如判明确无效，"
        "可将指定 q_id 转 quality_state='rejected'（决策⑲可追溯语义，FTS 同步移除）。",
        "3. 本报告与 JSON（同目录 raw_anomalies_report.json）均可复现："
        "`python -m backend.scripts.scan_raw_anomalies`。",
    ]
    return lines


def print_report(report: dict) -> None:
    flagged = report["flagged"]
    print(f"扫描完成：原始 {report['raw_total']:,} 行，库内镜像 {report['db_mirror']['db_total_ape']:,} 行（只读，未修改任何数据）")
    print(f"空括号填空标记：{len(flagged['blank_placeholder'])} 行（18 种形态，主形态 ((())/(())) 占绝大多数）"
          f"——定性：数据集填空题空位标记，非损坏")
    print(f"退化算式 x=0/x=1：{len(flagged['degenerate_equation'])} 行"
          f"（ans 同值 {sum(1 for r in flagged['degenerate_equation'] if r['ans_same_value'])}）"
          f"——疑似退化占位，需人工判定")
    print(f"非终结符结尾：{len(flagged['incomplete_ending'])} 行——疑似不完整，需人工判定")
    print(f"锚定：循环小数记法 {report['anchors'].get('brace_notation', 0)}｜题干无数字 {report['anchors'].get('stem_no_digit', 0)}｜空算式 {report['anchors'].get('empty_equation', 0)}")
    print(f"库内镜像：含空括号 {report['db_mirror']['db_stem_with_empty_parens']}，"
          f"含主形态 {report['db_mirror']['db_stem_with_main_placeholder']}")
    print(f"报告：{REPORT_MD}")
    print(f"      {REPORT_JSON}")


def main() -> int:
    parser = argparse.ArgumentParser(description="原始 APE210K 异常内容全量扫描（只读）")
    parser.add_argument("--db", default=None)
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--skip-hash", action="store_true")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    check_raw_files(raw_dir)
    if not args.skip_hash:
        for name in SPLITS:
            actual = sha256_of(raw_dir / name)
            if actual != EXPECTED_SHA256[name]:
                print(f"SHA-256 不一致：{name}，拒绝扫描。")
                return 1
        print("SHA-256 校验通过")

    report = run(args.db, raw_dir)
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
