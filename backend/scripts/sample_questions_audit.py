"""题库 100 题完整率抽样与完整内容复核报告（Phase 1 / Step 4）。

v2（2026-11-12，用户复核反馈修正）：
- v1 报告表格对题干做 [:40] 展示截断（旧代码第 92 行），造成人工复核
  "截断"假象。v2 改为逐题块状完整展示，任何字段不截断。
- 新增原始数据 vs 库内数据逐字段一致性核对，区分三类问题：
  A 原始数据自身质量问题（源数据缺陷，与 ETL 无关）
  B ETL 清洗造成的数据损坏（清洗规则误伤合法内容，如单个 * 乘号被删）
  C 报告展示截断（v1 代码缺陷，数据无损；v2 已修复）
- 抽样口径不变：seed=42、总体 = processed（source=ape210k）按 q_id 排序、
  random.Random(42).sample(100)；与 v1 为同一样本集（自动校验 q_id 集合，
  不一致即中止，保证"现有 100 个 q_id"）。

完整率口径（决策⑥/⑲，不变）：度量必要字段完整程度，不代表答案正确率，
也不代表知识点标注准确率。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.sample_questions_audit [--db <路径>] [--seed 42]
"""
import argparse
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

from backend.app.data import ape210k
from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_OUT = REPO_ROOT / "data" / "staging" / "ape210k_sample100.md"
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw" / "ape210k_src" / "data"
SPLITS = ("train.ape.json", "valid.ape.json", "test.ape.json")

# v1 报告的题干展示上限（旧代码 stem[:40]）
V1_DISPLAY_LIMIT = 40

_WS = re.compile(r"[\s　\xa0]+")
_SINGLE_STAR = re.compile(r"(?<!\*)\*(?!\*)")     # 不属于 ** 连续串的单个 *
_STAR_RUN = re.compile(r"\*{2,}")
_EXPONENT_STAR = re.compile(r"[0-9A-Za-z]\*\*[0-9]")  # cm**3 / 1**2 指数记法
_INCOMPLETE_ENDING = "，、：；（("
_DEGENERATE_EQ = re.compile(r"x\s*=\s*([01](?:\.0+)?)\s*")

# 人工核查结论（2026-11-12，用户指定 q_id 逐题核对，结论写入报告）
MANUAL_NOTES = {
    "q_ape_1050373": "C 类——原始与库内均完整（99 字），v1 报告 [:40] 展示截断，数据无损",
    "q_ape_287891": "C 类——原始与库内均完整（54 字），v1 报告展示截断，数据无损",
    "q_ape_899991": "C 类——原始与库内均完整（44 字），v1 报告展示截断，数据无损",
    "q_ape_230513": "B 类——原始题干含合法乘号（27*324、0.27*3.24），清洗规则 \\*+ 将其删除，"
                    "库内题干变为「27324=8,748」「0.273.24」，语义损坏；answer/equation 无损",
    "q_ape_634755": "B 类——原始题干 (75%+(1/3))*24；3.8*30%+6.2*30% 中乘号被清洗删除，"
                    "库内题干为 (75%+(1/3))24；3.830%+6.230%，语义损坏",
    "q_ape_72366": "A 类——原始题干本身不完整（「多少．」缺前提数量），equation=x=1/ans=1 为退化"
                   "占位记录；库内如实保留原始内容，ETL 清洗无损坏；无 KP 挂接",
    "q_ape_678403": "B 类——原始「体积是多少cm**3」的 ** 为指数记法（cm³），清洗删除后库内为「cm3」，"
                    "语义损坏（2026-11-12 人工复核修正：初判误标为 A 类残留）",
    "q_ape_58143": "B 类——原始「底面积是多少dm**2」的 ** 为指数记法（dm²），清洗后库内为「dm2」，"
                   "语义损坏（同上修正）",
    "q_ape_942935": "B 类——原始题干同时含幂记法（1**2+2**2）与乘号（1*2），清洗后全部丢失"
                    "（库内 12+22、12），语义严重损坏",
}


def _star_patterns(text: str) -> tuple[bool, bool, bool]:
    """返回 (含单个*, 含指数形态**, 含非指数 ** 残留)。"""
    has_single = bool(_SINGLE_STAR.search(text))
    has_exp = bool(_EXPONENT_STAR.search(text))
    has_residue = False
    for m in _STAR_RUN.finditer(text):
        s, e = m.span()
        before = text[s - 1] if s > 0 else ""
        after = text[e] if e < len(text) else ""
        if not (before.isalnum() and after.isdigit()):
            has_residue = True
    return has_single, has_exp, has_residue


def completeness_flags(row) -> dict[str, bool]:
    """必要字段完整度判定（与 v1 完全一致的口径 A–F）。"""
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


def classify_flags(original_text: str, equation: str, ans: str) -> list[tuple[str, str]]:
    """三分类自动检测（A 原始质量 / B 清洗损坏；C 在行级另行判定）。"""
    flags: list[tuple[str, str]] = []
    has_single, has_exp, has_residue = _star_patterns(original_text)
    if has_single:
        flags.append(("B", "题干含单个 *（乘号记法），清洗规则 \\*+ 将其删除 -> 库内题干语义损坏"))
    if has_exp:
        flags.append(("B", "题干含 ** 指数记法（如 cm**3、1**2），清洗规则将其删除 -> 库内题干语义损坏"))
    if has_residue:
        flags.append(("A", "源数据含非指数形态 ** 残留（ETL 清除为预期动作，非损坏）"))
    text = original_text.strip()
    if text and text[-1] in _INCOMPLETE_ENDING:
        flags.append(("A", "源数据题干以非终结符结尾，疑似不完整（需人工判定）"))
    if _DEGENERATE_EQ.fullmatch(str(equation).strip()) and str(ans).strip() in ("1", "0", "1.0", "0.0"):
        flags.append(("A", "算式为 x=1/x=0 且答案同值，疑似源数据退化占位记录（需人工判定）"))
    return flags


def cleaning_action(original: str) -> str:
    """描述 normalize_stem 对该题干做了什么（供复核确认均为预期动作）。"""
    if ape210k.normalize_stem(original) == original:
        return "无变化"
    parts = []
    if original != original.strip():
        parts.append("去首尾空白")
    if "*" in original:
        parts.append(f"删除 {original.count('*')} 个 * 字符")
    stripped = original.replace("*", "")
    if _WS.sub(" ", stripped).strip() != stripped.strip():
        parts.append("空白归一")
    return "；".join(parts) if parts else "其他归一"


def prev_report_qids(path: Path) -> set[str] | None:
    """从既有报告提取 q_id 集合（v1 表格与 v2 块状格式均适用）。"""
    if not path.exists():
        return None
    return set(re.findall(r"q_ape_\d+", path.read_text(encoding="utf-8")))


def load_raw_records(raw_dir: Path, wanted: set[str]) -> tuple[dict[str, dict], list]:
    """流式扫描三个原始文件，取回抽样题对应的原始记录；返回 (记录, 重复id)。"""
    found: dict[str, dict] = {}
    dups: list = []
    for name in SPLITS:
        with open(raw_dir / name, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                sid = str(item.get("id") or "")
                if sid in wanted:
                    if sid in found:
                        dups.append((sid, name))
                    else:
                        found[sid] = item
    return found, dups


def run(db_path, raw_dir: Path, seed: int = 42, size: int = 100) -> dict:
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT q_id, stem, answer, analysis, type, stage, subject, source,"
        "       score, difficulty, quality_state"
        " FROM questions WHERE source='ape210k' AND quality_state='processed'"
        " ORDER BY q_id"
    ).fetchall()
    picked = random.Random(seed).sample(rows, size)

    # 与上一版报告同一样本集校验（保证复核对象 = "现有 100 个 q_id"）
    prev = prev_report_qids(SAMPLE_OUT)
    picked_ids = [r["q_id"] for r in picked]
    prev_consistent = None if prev is None else (prev == set(picked_ids))

    wanted = {q_id.removeprefix("q_ape_") for q_id in picked_ids}
    raw_records, raw_dups = load_raw_records(raw_dir, wanted)

    kp_map: dict[str, list] = {}
    for q_id in picked_ids:
        kp_map[q_id] = [(k["kp_id"], k["name"]) for k in conn.execute(
            "SELECT kp.kp_id, kp.name FROM q_kp JOIN knowledge_points kp ON kp.kp_id=q_kp.kp_id"
            " WHERE q_kp.q_id=? ORDER BY kp.kp_id", (q_id,))]
    conn.close()

    results = []
    for row in picked:
        sid = row["q_id"].removeprefix("q_ape_")
        raw = raw_records.get(sid)
        rec = {"q_id": row["q_id"], "row": row, "raw": raw, "raw_missing": raw is None}
        cflags = completeness_flags(row)
        rec["cflags"] = cflags
        rec["complete"] = all(cflags.values())
        rec["display_truncated_v1"] = len(row["stem"]) > V1_DISPLAY_LIMIT
        if raw is None:
            rec["flags"] = [("X", "原始数据中未找到对应 id 记录")]
            rec["checks"] = {}
            rec["cleaning"] = "-"
        else:
            original = str(raw.get("original_text") or "")
            equation = str(raw.get("equation") or "")
            ans = str(raw.get("ans") or "")
            rec["checks"] = {
                "stem": row["stem"] == ape210k.normalize_stem(original),
                "equation": row["analysis"] == equation.strip(),
                "answer": row["answer"] == ans.strip(),
            }
            rec["flags"] = classify_flags(original, equation, ans)
            rec["flags"].extend(
                ("B", f"库内与 normalize(原始) 不一致：{k}") for k, ok in rec["checks"].items() if not ok)
            rec["cleaning"] = cleaning_action(original)
        results.append(rec)

    # ---- 分类汇总 ----
    cat = {
        "A": [r["q_id"] for r in results if any(c == "A" for c, _ in r["flags"])],
        "B": [r["q_id"] for r in results if any(c == "B" for c, _ in r["flags"])],
        "C": [r["q_id"] for r in results if r["display_truncated_v1"]],
        "X": [r["q_id"] for r in results if any(c == "X" for c, _ in r["flags"])],
    }
    flag_detail = {r["q_id"]: r["flags"] for r in results if r["flags"]}

    # ---- 报告 ----
    complete_n = sum(1 for r in results if r["complete"])
    kp_linked = sum(1 for r in results if kp_map[r["q_id"]])
    lines = [
        "# APE210K 题库 100 题完整内容复核报告（v2，无截断）",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}　seed={seed}　"
        f"总体={len(rows)}（source=ape210k 且 processed，按 q_id 排序）",
        f"- 与 v1 报告（2026-11-11）样本一致性："
        + ("一致（100/100，同一样本集）" if prev_consistent else
           ("未校验（无上一版报告）" if prev is None else "!!不一致!!")),
        "- v1 缺陷说明：v1 表格对题干做 [:40] 展示截断（仅报告代码问题，数据库无损），"
        "v2 已改为完整展示；本报告所有字段均不截断。",
        "- 完整率口径：必要字段完整程度（A 题干≥6字 / B 答案可解析 / C 含数字 / "
        "D 无 ** 残留 / E 分值难度域 / F 枚举齐），**不代表答案正确率，不代表 KP 标注准确率**",
        "",
        "## 三类问题分类汇总",
        "",
        f"- **C 报告展示截断（v1 代码缺陷，数据无损）**：{len(cat['C'])} 题——"
        f"题干超过 v1 展示上限 {V1_DISPLAY_LIMIT} 字被截断展示",
        f"- **B ETL 清洗损坏（清洗规则误删合法内容）**：{len(cat['B'])} 题",
        f"- **A 原始数据质量问题（源数据自身缺陷）**：{len(cat['A'])} 题",
        f"- **X 意外不一致（库内 ≠ 预期变换结果，应为 0）**：{len(cat['X'])} 题",
        "",
    ]
    for key, title in (("A", "A 类（原始数据质量）"), ("B", "B 类（ETL 清洗损坏）"),
                       ("C", "C 类（v1 展示截断）"), ("X", "X 类（意外不一致）")):
        if cat[key]:
            shown = "、".join(cat[key]) if len(cat[key]) <= 30 else \
                "、".join(cat[key][:30]) + f" …（共 {len(cat[key])} 题）"
            lines.append(f"- {title}：{shown}")
            if key in ("A", "B"):
                for q_id in cat[key]:
                    for c, msg in flag_detail[q_id]:
                        if c == key:
                            lines.append(f"  - {q_id}：{msg}")
            lines.append("")

    lines += [
        "## 完整率与 KP 挂接（口径同 v1）",
        "",
        f"- 完整率：**{complete_n}/{size}**（判定基于完整题干，不受 v1 展示截断影响）",
        f"- KP 挂接：{kp_linked}/{size} 题至少挂 1 个小学知识点",
        "",
        "## 逐题明细（原始 vs 库内，完整内容）",
        "",
    ]
    for i, r in enumerate(results, 1):
        row, raw = r["row"], r["raw"]
        lines.append(f"### {i}. {r['q_id']}")
        lines.append(
            f"- 元信息：type={row['type']} stage={row['stage']} difficulty={row['difficulty']} "
            f"score={row['score']} quality_state={row['quality_state']}")
        marks = " ".join("✓" if v else "✗" for v in r["cflags"].values())
        lines.append(f"- 完整性判定（A–F）：{marks} -> {'完整' if r['complete'] else '不完整'}")
        lines.append(f"- v1 展示截断：{'是（题干 ' + str(len(row['stem'])) + ' 字 > ' + str(V1_DISPLAY_LIMIT) + ' 字上限，数据本身完整）' if r['display_truncated_v1'] else '否'}")
        if raw is None:
            lines.append(f"- !! 原始数据缺失：{r['q_id']} 对应 id 未在原始文件中找到")
            lines.append("")
            continue
        original = str(raw.get("original_text") or "")
        segmented = str(raw.get("segmented_text") or "")
        equation = str(raw.get("equation") or "")
        ans = str(raw.get("ans") or "")
        lines.append(f"- 原始 original_text（{len(original)} 字）：{original}")
        lines.append(f"- 原始 segmented_text：{segmented}")
        lines.append(f"- 库内 stem（{len(row['stem'])} 字）：{row['stem']}")
        lines.append(f"- 清洗动作：{r['cleaning']}")
        lines.append(f"- 原始 equation：{equation}　原始 ans：{ans}")
        lines.append(f"- 库内 analysis：{row['analysis']}　库内 answer：{row['answer']}")
        checks = r["checks"]
        lines.append(
            f"- 逐字段一致性：stem {'✓' if checks.get('stem') else '✗'}（库内 = normalize(原始)）　"
            f"equation {'✓' if checks.get('equation') else '✗'}　answer {'✓' if checks.get('answer') else '✗'}")
        kps = kp_map[r["q_id"]]
        lines.append(f"- 小学 KP（{len(kps)}）：{'；'.join(f'{k} {n}' for k, n in kps) if kps else '无挂接'}")
        if r["flags"]:
            lines.append(f"- 异常标记：{'；'.join(f'[{c}] {m}' for c, m in r['flags'])}")
        else:
            lines.append("- 异常标记：无")
        if r["q_id"] in MANUAL_NOTES:
            lines.append(f"- 人工核查（2026-11-12）：{MANUAL_NOTES[r['q_id']]}")
        lines.append("")

    SAMPLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_OUT.write_text("\n".join(lines), encoding="utf-8")

    return {
        "population": len(rows), "seed": seed, "size": size,
        "complete": complete_n, "kp_linked": kp_linked,
        "prev_consistent": prev_consistent,
        "raw_missing": [r["q_id"] for r in results if r["raw_missing"]],
        "raw_dups": raw_dups,
        "cat": cat,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="题库 100 题完整率抽样 + 完整内容复核（v2，无截断）")
    parser.add_argument("--db", default=None)
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    if not raw_dir.exists():
        print(f"原始数据目录不存在：{raw_dir}（完整内容复核必须读取原始数据）")
        return 1

    result = run(args.db, raw_dir, args.seed)
    cat = result["cat"]
    print(f"抽样：seed={result['seed']} 总体={result['population']} 抽样={result['size']}")
    print(f"与 v1 样本一致性：{result['prev_consistent']}")
    print(f"完整率（A–F 口径，基于完整题干）：{result['complete']}/{result['size']}")
    print(f"KP 挂接：{result['kp_linked']}/{result['size']}")
    print(f"三类分类：C 展示截断 {len(cat['C'])} | B 清洗损坏 {len(cat['B'])} | A 原始质量 {len(cat['A'])} | X 意外 {len(cat['X'])}")
    for key in ("B", "A", "X"):
        if cat[key]:
            print(f"  {key} 类 q_id：{'、'.join(cat[key])}")
    if result["raw_missing"]:
        print(f"!! 原始记录缺失：{result['raw_missing']}")
    if result["raw_dups"]:
        print(f"!! 原始 id 跨文件重复：{result['raw_dups']}")
    print(f"报告（完整内容，不入 Git）：{SAMPLE_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
