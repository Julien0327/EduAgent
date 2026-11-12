"""题库 100 题完整率抽样与完整内容复核报告（Phase 1 / Step 4）。

v3（2026-11-12，方案 A 修复后重生成）：
- 清洗规则已按用户裁决修复（方案 A）：乘号 `*` 与指数记法 `**` 原样保留，
  仅清除行首 ** 串与句末标点（。．？！？）后的 ** 残留（全库 3 行）。
  本报告验证修复效果：B 类（清洗损坏）应为 0，10 道 B 类样本全部恢复。
- 新增"星号保留"逐题核对（原始非残留星号数 vs 库内星号数）与
  "B 类恢复核验"章节（修复前损坏形态 vs 修复后库内实测逐字对照）。
- 完整率判定项 D 更新：由"无 ** "改为"无残留形态星号"——乘号/指数是合法
  数学记法，含 `**` 的题干不再判不完整（口径意图不变：无未清理残留）。

v2（2026-11-12，用户复核反馈修正）：
- v1 报告表格对题干做 [:40] 展示截断（旧代码第 92 行），造成人工复核
  "截断"假象。v2 起改为逐题块状完整展示，任何字段不截断。
- 新增原始数据 vs 库内数据逐字段一致性核对，区分三类问题：
  A 原始数据自身质量问题（源数据缺陷，与 ETL 无关）
  B ETL 清洗造成的数据损坏（清洗规则误伤合法内容）
  C 报告展示截断（v1 代码缺陷，数据无损；v2 已修复）

抽样口径：seed=42、总体 = processed（source=ape210k）按 q_id 排序、
random.Random(42).sample(100)。**样本集固定沿用上一版报告的 100 个 q_id**
（验收要求"抽样规则和原样本集保持一致"——方案 A 修复后总体 +6 会导致
seed 重抽索引偏移，故存在上一版报告时按 q_id 集合固定复核对象，报告内
注明；--fresh-sample 可强制按当前总体重抽）。

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
_EXPONENT_STAR = re.compile(r"[0-9A-Za-z一-鿿)]\*\*[0-9]")  # cm**3 / 1**2 / 米**2 / (-1)**2
_INCOMPLETE_ENDING = "，、：；（("
_DEGENERATE_EQ = re.compile(r"x\s*=\s*([01](?:\.0+)?)\s*")

# 修复前库内题干实测（2026-11-12 修复前 DB 提取；旧规则 \\*+ 效果 = 题干
# 去除全部星号）。用于 B 类恢复核验的修复前后对照。
PRE_FIX_STEMS = {
    "q_ape_678403": "一个正方体的棱长之和是84cm，它的体积是多少cm3？",
    "q_ape_1060185": "计算(1/((1/(12))+(1/(23))+(1/(34))+…+(1/(20162017))))=",
    "q_ape_58143": "一个长方体铁皮水箱，容积是60L，高是4dm，它的底面积是多少dm2．",
    "q_ape_209048": "当123456789987654321被6除时，余数是多少．",
    "q_ape_230513": "因为27324=8,748，所以0.273.24=多少",
    "q_ape_290121": "(12/5)((5/6)+(3/4))+(4/5)．",
    "q_ape_942935": "计算：((12+22)/(12))+((22+32)/(23))+…+((1002+1012)/(100101))+((1012+1022)/(101102))=．",
    "q_ape_1200515": "巧算：36111+8885+2222．",
    "q_ape_634755": "(75%+(1/3))24；3.830%+6.230%",
    "q_ape_87500": "计算：0.625(1(2/3)+3(1/6))+(1/6)/1(3/5)-(5/8)．",
}

# 人工核查结论（2026-11-12 用户指定 q_id 逐题核对；B 类结论已更新为修复后状态）
MANUAL_NOTES = {
    "q_ape_1050373": "C 类（历史）——原始与库内均完整（99 字），仅 v1 报告 [:40] 展示截断，数据无损",
    "q_ape_287891": "C 类（历史）——原始与库内均完整（54 字），v1 报告展示截断，数据无损",
    "q_ape_899991": "C 类（历史）——原始与库内均完整（44 字），v1 报告展示截断，数据无损",
    "q_ape_230513": "B 类已修复（方案 A）——库内恢复「因为27*324=8,748，所以0.27*3.24=多少」，乘号 3 个全保留",
    "q_ape_634755": "B 类已修复（方案 A）——库内恢复「(75%+(1/3))*24；3.8*30%+6.2*30%」，乘号 3 个全保留",
    "q_ape_72366": "A 类——原始题干本身不完整（「多少．」缺前提数量），equation=x=1/ans=1 为退化"
                   "占位记录；库内如实保留原始内容，ETL 清洗无损坏；无 KP 挂接",
    "q_ape_678403": "B 类已修复（方案 A）——库内恢复「体积是多少cm**3」，指数记法保留"
                    "（2026-11-12 人工复核修正：初判误标为 A 类残留）",
    "q_ape_58143": "B 类已修复（方案 A）——库内恢复「底面积是多少dm**2」，指数记法保留",
    "q_ape_942935": "B 类已修复（方案 A）——库内恢复全部幂记法（1**2+2**2）与乘号（1*2）",
}


def _star_patterns(text: str) -> tuple[bool, bool, bool]:
    """返回 (含单个*乘号, 含指数形态**, 含非标准星号记法)。

    非标准记法 = 星号既非单个乘号、也非指数形态（如循环小数 5.{_{4}**(•)），
    方案 A 下原样保留（未确认为残留，保守不删）。
    """
    has_single = bool(_SINGLE_STAR.search(text))
    has_exp = bool(_EXPONENT_STAR.search(text))
    has_nonstandard = False
    for m in _STAR_RUN.finditer(text):
        s, e = m.span()
        before = text[s - 1] if s > 0 else ""
        after = text[e] if e < len(text) else ""
        if not ((before.isalnum() or before == ")") and after.isdigit()):
            has_nonstandard = True
    return has_single, has_exp, has_nonstandard


def completeness_flags(row) -> dict[str, bool]:
    """必要字段完整度判定（口径 A–F；D 项 v3 起按残留形态判定）。"""
    stem = row["stem"] or ""
    kind, _ = ape210k.parse_answer(row["answer"] or "")
    return {
        "A_stem_valid": len(stem) >= 6,
        "B_answer_parseable": kind != "other" and bool((row["answer"] or "").strip()),
        "C_stem_has_number": any(c.isdigit() for c in stem),
        "D_no_md_residue": not ape210k.RESIDUE_STAR_RE.search(stem),
        "E_domain_ok": (row["difficulty"] is not None and 1 <= row["difficulty"] <= 5
                        and row["score"] is not None and row["score"] > 0),
        "F_enums_ok": all((row[c] or "").strip() for c in ("type", "stage", "subject", "source")),
    }


def classify_flags(original_text: str, equation: str, ans: str, db_stem: str) -> list[tuple[str, str]]:
    """分类自动检测（A 原始质量 / B 清洗损坏 / INFO 合法记法说明；C 在行级判定）。

    方案 A 修复后：乘号/指数星号为合法记法应原样保留；B 类 = 星号保留失败
    或库内与预期变换不一致（后者由 run() 附加）。
    """
    flags: list[tuple[str, str]] = []
    expected_kept = ape210k.RESIDUE_STAR_RE.sub(r"\1", original_text)
    orig_stars, db_stars = expected_kept.count("*"), db_stem.count("*")
    if orig_stars != db_stars:
        flags.append(("B", f"星号保留失败：原始非残留星号 {orig_stars} 个，库内仅 {db_stars} 个"))
    has_single, has_exp, has_nonstandard = _star_patterns(original_text)
    if has_single or has_exp:
        kinds = "、".join(filter(None, [
            "乘号 *" if has_single else "", "指数 **" if has_exp else ""]))
        flags.append(("INFO", f"题干含合法数学记法（{kinds}，共 {orig_stars} 个星号，已原样保留）"))
    if ape210k.RESIDUE_STAR_RE.search(original_text):
        flags.append(("A", "源数据含行首/句末 ** 残留（已按方案 A 规则清除，标点保留）"))
    if has_nonstandard:
        flags.append(("A", "源数据含非标准星号记法（如循环小数 **(•)），方案 A 原样保留（保守不删）"))
    text = original_text.strip()
    if text and text[-1] in _INCOMPLETE_ENDING:
        flags.append(("A", "源数据题干以非终结符结尾，疑似不完整（需人工判定）"))
    if _DEGENERATE_EQ.fullmatch(str(equation).strip()) and str(ans).strip() in ("1", "0", "1.0", "0.0"):
        flags.append(("A", "算式为 x=1/x=0 且答案同值，疑似源数据退化占位记录（需人工判定）"))
    return flags


def cleaning_action(original: str) -> str:
    """描述 normalize_stem 对该题干做了什么（供复核确认均为预期动作）。"""
    if ape210k.normalize_stem(original) == original:
        return "无变化（含合法星号记法，原样保留）" if "*" in original else "无变化"
    parts = []
    residue_stars = sum(len(m.group(0)) - len(m.group(1))
                        for m in ape210k.RESIDUE_STAR_RE.finditer(original))
    if residue_stars:
        parts.append(f"清除行首/句末 ** 残留 {residue_stars} 个星号（前导标点保留）")
    if original != original.strip():
        parts.append("去首尾空白")
    after_residue = ape210k.RESIDUE_STAR_RE.sub(r"\1", original)
    if _WS.sub(" ", after_residue).strip() != after_residue.strip():
        parts.append("空白归一")
    return "；".join(parts) if parts else "其他归一"


def prev_report_qids(path: Path) -> set[str] | None:
    """从既有报告提取 q_id 集合（v1 表格与 v2/v3 块状格式均适用）。"""
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


def run(db_path, raw_dir: Path, seed: int = 42, size: int = 100, fresh: bool = False) -> dict:
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT q_id, stem, answer, analysis, type, stage, subject, source,"
        "       score, difficulty, quality_state"
        " FROM questions WHERE source='ape210k' AND quality_state='processed'"
        " ORDER BY q_id"
    ).fetchall()

    # 样本集：存在上一版报告时固定沿用其 q_id 集合（可比性优先）；
    # --fresh-sample 按当前总体 + seed 重抽
    prev = prev_report_qids(SAMPLE_OUT)
    pinned = prev is not None and not fresh
    if pinned:
        picked = sorted((r for r in rows if r["q_id"] in prev), key=lambda r: r["q_id"])
        if len(picked) != len(prev):
            raise SystemExit(
                f"上一版报告 {len(prev)} 题中仅 {len(picked)} 题仍在当前 processed 总体中，"
                f"无法固定样本集；请核查（--fresh-sample 可改为重抽）")
    else:
        picked = random.Random(seed).sample(rows, size)

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
            rec["stars"] = None
        else:
            original = str(raw.get("original_text") or "")
            equation = str(raw.get("equation") or "")
            ans = str(raw.get("ans") or "")
            rec["checks"] = {
                "stem": row["stem"] == ape210k.normalize_stem(original),
                "equation": row["analysis"] == equation.strip(),
                "answer": row["answer"] == ans.strip(),
            }
            rec["flags"] = classify_flags(original, equation, ans, row["stem"])
            rec["flags"].extend(
                ("B", f"库内与 normalize(原始) 不一致：{k}") for k, ok in rec["checks"].items() if not ok)
            rec["cleaning"] = cleaning_action(original)
            kept = ape210k.RESIDUE_STAR_RE.sub(r"\1", original)
            rec["stars"] = (kept.count("*"), row["stem"].count("*"))
        results.append(rec)

    # ---- 分类汇总 ----
    cat = {
        "A": [r["q_id"] for r in results if any(c == "A" for c, _ in r["flags"])],
        "B": [r["q_id"] for r in results if any(c == "B" for c, _ in r["flags"])],
        "C": [r["q_id"] for r in results if r["display_truncated_v1"]],
        "X": [r["q_id"] for r in results if any(c == "X" for c, _ in r["flags"])],
    }
    flag_detail = {r["q_id"]: r["flags"] for r in results if r["flags"]}

    # ---- B 类恢复核验（方案 A 修复后）----
    restoration: list[dict] = []
    for q_id, damaged in PRE_FIX_STEMS.items():
        rec = next((r for r in results if r["q_id"] == q_id), None)
        if rec is None or rec["raw"] is None:
            restoration.append({"q_id": q_id, "in_sample": False, "ok": None})
            continue
        original = str(rec["raw"].get("original_text") or "")
        current = rec["row"]["stem"]
        ok = (current == ape210k.normalize_stem(original)
              and current.count("*") == ape210k.RESIDUE_STAR_RE.sub(r"\1", original).count("*")
              and current != damaged)
        restoration.append({"q_id": q_id, "in_sample": True, "ok": ok,
                            "damaged": damaged, "current": current})
    restored_ok = sum(1 for r in restoration if r["ok"])

    # ---- 报告 ----
    complete_n = sum(1 for r in results if r["complete"])
    kp_linked = sum(1 for r in results if kp_map[r["q_id"]])
    star_total = sum(1 for r in results if r["stars"] and r["stars"][0] > 0)
    star_preserved = sum(1 for r in results
                         if r["stars"] and r["stars"][0] > 0 and r["stars"][0] == r["stars"][1])
    lines = [
        "# APE210K 题库 100 题完整内容复核报告（v3，方案 A 修复后）",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}　seed={seed}　"
        f"总体={len(rows)}（source=ape210k 且 processed，按 q_id 排序）",
        f"- 样本集来源："
        + ("**固定沿用上一版报告的 100 个 q_id**（总体因修复 +6 会使 seed 重抽偏移，"
           "按验收要求保持原样本集一致）" if pinned else
           f"按当前总体 seed={seed} 重抽"),
        f"- 与 v1/v2 报告样本一致性："
        + ("一致（100/100，同一样本集）" if prev_consistent else
           ("未校验（无上一版报告）" if prev is None else "!!不一致!!")),
        "- 清洗规则（方案 A，2026-11-12 用户裁决）：乘号 `*` 与指数 `**` 原样保留；"
        "仅清行首 ** 串与句末标点（。．？！？）后的 ** 残留（全库 3 行）；不做记法转换。",
        "- v1 缺陷说明：v1 表格对题干做 [:40] 展示截断（仅报告代码问题，数据库无损），"
        "v2 起完整展示；本报告所有字段均不截断。",
        "- 完整率口径：必要字段完整程度（A 题干≥6字 / B 答案可解析 / C 含数字 / "
        "D 无残留形态星号（乘号/指数为合法记法） / E 分值难度域 / F 枚举齐），"
        "**不代表答案正确率，不代表 KP 标注准确率**",
        "",
        "## 分类汇总（方案 A 修复后）",
        "",
        f"- **B ETL 清洗损坏（应为 0）**：{len(cat['B'])} 题",
        f"- **A 原始数据质量问题（源数据自身缺陷）**：{len(cat['A'])} 题",
        f"- **C 报告展示截断（v1 历史代码缺陷，数据无损）**：{len(cat['C'])} 题——"
        f"题干超过 v1 展示上限 {V1_DISPLAY_LIMIT} 字，v1 时代会被截断展示",
        f"- **X 意外不一致（库内 ≠ 预期变换结果，应为 0）**：{len(cat['X'])} 题",
        f"- 星号保留核对：含合法星号记法的题目 {star_total} 题，星号数与原始一致 {star_preserved}/{star_total} 题",
        "",
    ]
    for key, title in (("A", "A 类（原始数据质量）"), ("B", "B 类（ETL 清洗损坏，应为 0）"),
                       ("C", "C 类（v1 展示截断，历史）"), ("X", "X 类（意外不一致）")):
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
        "## B 类恢复核验（方案 A 修复前后对照，库内实测）",
        "",
        "10 道抽样发现的 B 类损坏题：修复前题干（旧规则 \\*+ 删除全部星号）vs 修复后库内题干。",
        "",
    ]
    for r in restoration:
        if not r["in_sample"]:
            lines.append(f"- {r['q_id']}：不在本样本集（样本固定 100 题，无法在本报告核验）")
            continue
        mark = "恢复 ✓" if r["ok"] else "!! 未恢复"
        lines.append(f"- {r['q_id']}：{mark}")
        lines.append(f"  - 修复前（损坏）：{r['damaged']}")
        lines.append(f"  - 修复后（库内）：{r['current']}")
    lines.append(f"- **恢复合计：{restored_ok}/{len(PRE_FIX_STEMS)}**（判定：库内 = normalize(原始) 且星号数一致且 ≠ 修复前损坏形态）")
    lines.append("")

    lines += [
        "## 完整率与 KP 挂接（口径同 v1/v2）",
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
        if r["stars"]:
            o_s, d_s = r["stars"]
            lines.append(f"- 星号保留：原始非残留 {o_s} 个 * / 库内 {d_s} 个 -> {'一致 ✓' if o_s == d_s else '不一致 ✗'}")
        lines.append(f"- 原始 equation：{equation}　原始 ans：{ans}")
        lines.append(f"- 库内 analysis：{row['analysis']}　库内 answer：{row['answer']}")
        checks = r["checks"]
        lines.append(
            f"- 逐字段一致性：stem {'✓' if checks.get('stem') else '✗'}（库内 = normalize(原始)）　"
            f"equation {'✓' if checks.get('equation') else '✗'}　answer {'✓' if checks.get('answer') else '✗'}")
        kps = kp_map[r["q_id"]]
        lines.append(f"- 小学 KP（{len(kps)}）：{'；'.join(f'{k} {n}' for k, n in kps) if kps else '无挂接'}")
        if r["flags"]:
            lines.append(f"- 标记：{'；'.join(f'[{c}] {m}' for c, m in r['flags'])}")
        else:
            lines.append("- 标记：无")
        if r["q_id"] in MANUAL_NOTES:
            lines.append(f"- 人工核查（2026-11-12）：{MANUAL_NOTES[r['q_id']]}")
        lines.append("")

    SAMPLE_OUT.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_OUT.write_text("\n".join(lines), encoding="utf-8")

    return {
        "population": len(rows), "seed": seed, "size": size, "pinned": pinned,
        "complete": complete_n, "kp_linked": kp_linked,
        "prev_consistent": prev_consistent,
        "star_total": star_total, "star_preserved": star_preserved,
        "restored_ok": restored_ok, "restoration_total": len(PRE_FIX_STEMS),
        "raw_missing": [r["q_id"] for r in results if r["raw_missing"]],
        "raw_dups": raw_dups,
        "cat": cat,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="题库 100 题完整率抽样 + 完整内容复核（v3，方案 A 修复后）")
    parser.add_argument("--db", default=None)
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fresh-sample", action="store_true",
                        help="不固定沿用上一版报告的 q_id 集合，按当前总体重抽")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    if not raw_dir.exists():
        print(f"原始数据目录不存在：{raw_dir}（完整内容复核必须读取原始数据）")
        return 1

    result = run(args.db, raw_dir, args.seed, fresh=args.fresh_sample)
    cat = result["cat"]
    print(f"抽样：seed={result['seed']} 总体={result['population']} 抽样={result['size']}"
          f"（{'固定沿用上一版样本集' if result['pinned'] else '当前总体重抽'}）")
    print(f"与 v1/v2 样本一致性：{result['prev_consistent']}")
    print(f"完整率（A–F 口径，基于完整题干）：{result['complete']}/{result['size']}")
    print(f"KP 挂接：{result['kp_linked']}/{result['size']}")
    print(f"星号保留：{result['star_preserved']}/{result['star_total']}（含合法星号记法的题目，星号数与原始一致）")
    print(f"B 类恢复核验：{result['restored_ok']}/{result['restoration_total']}（修复前后对照，库内实测）")
    print(f"分类：B 清洗损坏 {len(cat['B'])}（应为 0） | A 原始质量 {len(cat['A'])} | C 展示截断(历史) {len(cat['C'])} | X 意外 {len(cat['X'])}")
    for key in ("B", "X"):
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
