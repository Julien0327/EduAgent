"""50 题 Gold 核验材料包构建（Step 5 质量验收阶段，用户裁决㉗）。

- 保留助手盲标初稿不覆盖（数据源 trial50_gold_draft.jsonl 原样引用）；
- 标明 A/B 预测与初稿 Gold 的差异；重点逐题展开 A 臂 15 道硬误标题
  （题干/模型输出/初稿 Gold/差异说明/核验点——评注为助手归因分析，
  供用户裁决参考，不替代用户确认）；
- 辅助知识点允许为空，不强制凑标签；
- 输出 staging（含题目内容，不入 Git——决策㉒）。

用法（仓库根）：.venv/Scripts/python.exe -m backend.scripts.build_gold_review_pack
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = REPO_ROOT / "data" / "staging" / "trial50_gold_draft.jsonl"
TRIAL_PATH = REPO_ROOT / "data" / "annotations" / "trial500_qids.jsonl"
RECALL_PATH = REPO_ROOT / "data" / "staging" / "recall10_report.json"
OUT_MD = REPO_ROOT / "data" / "staging" / "trial50_review_pack.md"
OUT_JSON = REPO_ROOT / "data" / "staging" / "trial50_review_pack.json"
BATCH_A, BATCH_B = "ann_20261114_01_a", "ann_20261114_02_b"

# A 臂 15 道硬误标题：助手归因评注（分类 + 核验点建议；最终裁决权在用户）
COMMENTARY = {
 "q_ape_788175": ("P1 辅助可辩护（初稿 Gold 偏窄）",
   "模型辅助标「分数运算」——题干含 11/16 部分量与分数推理，辅助有题目依据；初稿未含属偏窄。",
   "核验点：是否将 kp_p_075 纳入辅助；primary 双方一致（工程问题）无需裁决。"),
 "q_ape_1216385": ("P3 KP 定义张力",
   "两步加减题：kp_p_077 定义「整数加减（一步）」、kp_p_079「加减乘除混合」——模型按定义判 079 主+077 辅更自洽，初稿 077 主与「一步」定义冲突。",
   "核验点：①修订初稿 gold（079 主+077 辅）或②修订 077/079 描述（编号冻结，改描述需您批准）。"),
 "q_ape_87882": ("P1 辅助可辩护",
   "模型辅助标「方程求解」——题需设未知数解不定方程取整数解，辅助有依据；primary 双方一致（最大最小问题）。",
   "核验点：kp_p_081 是否纳入辅助。"),
 "q_ape_278113": ("P2 primary 分歧 + P1 辅助",
   "primary 分歧：模型「因数与倍数」vs 初稿「整除特征」（语义邻近：4^5 的倍数链两种表述）；辅助「还原问题」（逐轮逆推）有依据，「分数运算」较弱。",
   "核验点：primary 裁决（066 vs 071）；辅助 028 纳入与否、075 取舍。"),
 "q_ape_77461": ("P1 辅助可辩护",
   "模型辅助标「乘除运算」——85×65 与 85×75 求各量确为乘法步骤，辅助有依据；primary 双方一致（四则混合）。",
   "核验点：kp_p_078 是否纳入辅助（注意其定义 exclude 混合运算——题含加法，纳入辅助是否与定义冲突请一并裁决）。"),
 "q_ape_822661": ("P2 primary 分歧",
   "模型 primary「方程求解」vs 初稿「量率对应」。方程是该题自然解法但非唯一（算术分数推理可行）；考点本质更贴近量率对应+和差。",
   "核验点：primary 裁决（081 vs 037）；若裁 037 主，081 可否作辅助。"),
 "q_ape_284252": ("P2 primary 分歧",
   "模型 primary「比与按比例分配」vs 初稿「量率对应」——倍数↔比↔分数同族语义，两 KP 边界模糊（初稿依据：倍数互化是量率对应核心操作）。",
   "核验点：primary 裁决（038 vs 037）；若长期两可，建议澄清两 KP 描述边界（需您批准）。"),
 "q_ape_297696": ("P2 primary 分歧 + P1 辅助",
   "primary 分歧：模型「浓度问题」vs 初稿「率类问题」——含盐率两可（率类定义明列「含糖率」族，浓度定义含「含盐」关键词，KP 表自身重叠）；辅助「百分数运算」（含盐率即百分数表达）有依据。",
   "核验点：primary 裁决（036 vs 015）+ 辅助 076 纳入与否；此题暴露 KP 表内部重叠，可作 KP 描述澄清候选。"),
 "q_ape_19316": ("P1 辅助可辩护",
   "模型辅助标「分数运算」（半径比→面积比的分率计算）有依据；primary 双方一致（圆的面积）。",
   "核验点：kp_p_075 是否纳入辅助。"),
 "q_ape_174781": ("P1 辅助可辩护",
   "模型辅助标「分数运算」（可能性=1/3 的分数表达）有依据；primary 双方一致（可能性）。",
   "核验点：kp_p_075 是否纳入辅助（B 臂另标了量率对应，可一并参考）。"),
 "q_ape_577597": ("P1 辅助可辩护",
   "模型辅助标「四则混合运算」（异分母三数加减混合）有依据；primary 双方一致（分数运算）。",
   "核验点：kp_p_079 是否纳入辅助。"),
 "q_ape_1086627": ("P2 primary 分歧（表层关键词偏置典型案例）",
   "模型 primary「一般行程」——被「每小时行 50 千米」表层关键词带偏；题目本质=路程总量一定、速度与时间成反比（归总+反比例），且题干明示「用算术和比例解」。倾向初稿正确（035 主）。",
   "核验点：primary 裁决（035 vs 010）；此题是 prompt v2「表层关键词 ≠ 考点本质」消歧规则的依据例。"),
 "q_ape_1070129": ("P2 primary 分歧",
   "模型 primary「简便计算」（裂项相消是标准简便技巧）vs 初稿「找规律」——both defensible（裂项属简便计算族）。",
   "核验点：primary 裁决（082 vs 031）；辅助 075 双方一致，079 已在初稿。"),
 "q_ape_301084": ("P1 辅助可辩护",
   "模型辅助标「四则混合运算」（倒数+倍数+加法混合求值）有依据；primary 双方一致（分数运算）。",
   "核验点：kp_p_079 是否纳入辅助。"),
 "q_ape_277414": ("P1 辅助可辩护",
   "模型辅助标「分数运算」（循环小数化分数）与「简便计算」（495/562 为可约分而设计）均有依据；primary 双方一致（小数运算）。",
   "核验点：kp_p_075 与 kp_p_082 是否纳入辅助。"),
}


def load_state():
    gold = [json.loads(l) for l in GOLD_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    stratum = {}
    for line in TRIAL_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            stratum[r["q_id"]] = r["stratum"]
    conn = db.connect()
    kpname = {r["kp_id"]: r["name"] for r in conn.execute("SELECT kp_id, name FROM knowledge_points")}
    stems = {r["q_id"]: r["stem"] for r in conn.execute(
        "SELECT q_id, stem FROM questions WHERE quality_state='processed'")}

    def load_batch(batch):
        res = {r["q_id"]: dict(r) for r in conn.execute(
            "SELECT q_id, status, primary_kp_id, reason FROM q_annotation_results WHERE batch_id=?",
            (batch,))}
        kps: dict[str, list[tuple[str, int]]] = {}
        for r in conn.execute("SELECT q_id, kp_id, is_primary FROM q_kp_llm WHERE batch_id=?", (batch,)):
            kps.setdefault(r["q_id"], []).append((r["kp_id"], r["is_primary"]))
        for q in kps:
            kps[q].sort(key=lambda x: -x[1])
        diff = {r["q_id"]: r["difficulty"] for r in conn.execute(
            "SELECT q_id, difficulty FROM q_difficulty_llm WHERE batch_id=?", (batch,))}
        return res, kps, diff

    resA, kpsA, diffA = load_batch(BATCH_A)
    resB, kpsB, diffB = load_batch(BATCH_B)
    conn.close()
    return gold, stratum, kpname, stems, (resA, kpsA, diffA), (resB, kpsB, diffB)


def tag_repr(kps, kpname):
    return "、".join(f"{k}{'★' if p else ''}({kpname[k]})" for k, p in kps) or "（无）"


def main() -> int:
    gold, stratum, kpname, stems, (resA, kpsA, diffA), (resB, kpsB, diffB) = load_state()
    rows = []
    hard_ids = []
    for g in gold:
        qid = g["q_id"]
        rA, rB = resA.get(qid), resB.get(qid)
        a_tags = kpsA.get(qid, [])
        a_hard = sorted({k for k, _ in a_tags} - set(g["gold_kp_ids"])) if rA and rA["status"] == "ok" else []
        b_status = rB["status"] if rB else "?"
        rows.append({
            "q_id": qid, "stratum": stratum.get(qid, "?"), "stem": stems[qid],
            "gold": {"kp_ids": g["gold_kp_ids"], "names": [kpname[k] for k in g["gold_kp_ids"]],
                     "primary": g["gold_primary"], "primary_name": kpname[g["gold_primary"]],
                     "difficulty": g["gold_difficulty"]},
            "A": {"status": rA["status"] if rA else "?",
                  "tags": tag_repr(a_tags, kpname),
                  "primary": rA.get("primary_kp_id") if rA else None,
                  "primary_name": kpname.get(rA.get("primary_kp_id")) if rA else None,
                  "difficulty": diffA.get(qid), "reason": rA.get("reason") if rA else None,
                  "hard_errors": [{"kp_id": k, "name": kpname[k]} for k in a_hard]},
            "B": {"status": b_status,
                  "tags": tag_repr(kpsB.get(qid, []), kpname) if b_status == "ok" else None,
                  "difficulty": diffB.get(qid)},
            "a_primary_hit": bool(rA and rA["status"] == "ok" and rA.get("primary_kp_id") in set(g["gold_kp_ids"])),
            "a_pass_without_hard": bool(rA and rA["status"] == "ok" and not a_hard),
        })
        if a_hard:
            hard_ids.append(qid)

    json_out = {"generated_note": "材料包：助手盲标初稿保留不覆盖；最终 Gold 由用户确认", "rows": rows}
    OUT_JSON.write_text(json.dumps(json_out, ensure_ascii=False, indent=2), encoding="utf-8")

    cat_of = {qid: COMMENTARY[qid][0] for qid in hard_ids if qid in COMMENTARY}
    md = [
        "# 50 题 Gold 核验材料包（质量验收阶段）", "",
        f"- 生成：材料仅整理既有数据；**助手盲标初稿原样保留未覆盖**（`trial50_gold_draft.jsonl`）。",
        "- **最终 Gold 由用户确认**；助手初标与评注仅作裁决参考，不自行确认为终版。",
        "- 本文件含题目内容，入 data/staging/ 不入 Git（决策㉒）。本阶段未调用 API、未改题库标注。", "",
        "## 0. A 臂 15 道硬误标题归因汇总（助手分析，供裁决参考）", "",
        "| 归因类 | 题数 | 题目 |",
        "|---|---|---|",
        "| P2 primary 分歧（相邻 KP 无消歧规则 / 表层关键词偏置） | 6 | " +
        "、".join(q for q in hard_ids if "P2" in cat_of.get(q, "")) + " |",
        "| P1 辅助标签可辩护（初稿 Gold 偏窄） | 8 | " +
        "、".join(q for q in hard_ids if cat_of.get(q, "").startswith("P1")) + " |",
        "| P3 KP 定义张力（077 一步定义 vs 两步题） | 1 | q_ape_1216385 |", "",
        "**关键洞察**：硬误标的主因不是模型「过度打标」，而是 ①初稿 Gold 偏窄（8 题辅助标签有题目依据）"
        "②相邻 KP 的 primary 选择无消歧规则（6 题）③一处 KP 定义张力。若您核验后吸纳可辩护辅助并裁决 primary，"
        "A 臂 pass 率将显著上升；**单纯「收紧标签数量」可能误伤有依据的辅助标签**——prompt v2 方向详见设计文档 §14。", "",
        "## 一、15 道硬误标题逐题详解", "",
    ]
    for r in rows:
        if r["q_id"] not in hard_ids:
            continue
        cat, analysis, check = COMMENTARY[r["q_id"]]
        g, a, b = r["gold"], r["A"], r["B"]
        md += [
            f"### {r['q_id']}（{r['stratum']}）｜{cat}", "",
            f"**题干**：{r['stem']}", "",
            f"**初稿 Gold**：{('、'.join(f'{k}({n})' for k, n in zip(g['kp_ids'], g['names'])))}"
            f"｜primary={g['primary']}({g['primary_name']})｜难度={g['difficulty']}",
            f"**A 臂输出**：{a['tags']}｜primary={a['primary']}({a['primary_name']})｜难度={a['difficulty']}｜理由：{a['reason']}",
            f"**B 臂输出**（参考）：{b['tags'] or b['status']}｜难度={b['difficulty']}", "",
            f"**差异**：primary {'一致 ✓' if r['a_primary_hit'] else '分歧 ✗'}；"
            f"硬误标 {'、'.join(f"{h['kp_id']}({h['name']})" for h in a['hard_errors'])}；"
            f"难度 {'一致' if a['difficulty'] == g['difficulty'] else f'差 {abs((a["difficulty"] or 0) - g["difficulty"])} 级'}", "",
            f"**助手归因**：{analysis}",
            f"**建议核验点**：{check}", "",
            "---", "",
        ]
    md += ["## 二、其余 35 题简表（一致性一览）", "",
           "| q_id | 层 | primary 一致 | A 硬误标 | 难度一致 | B 状态 |", "|---|---|---|---|---|---|"]
    for r in rows:
        if r["q_id"] in hard_ids:
            continue
        g, a, b = r["gold"], r["A"], r["B"]
        md.append(f"| {r['q_id']} | {r['stratum']} | {'✓' if r['a_primary_hit'] else '✗'} | "
                  f"{'无' if not a['hard_errors'] else '、'.join(h['kp_id'] for h in a['hard_errors'])} | "
                  f"{'✓' if a['difficulty'] == g['difficulty'] else '✗'} | {b['status']} |")
    md += ["", "## 三、核验操作指引", "",
           "1. 逐题复核（重点第一节 15 题）：可直接在 `trial50_review_sheet.csv` 的 gold 列修订——"
           "该 CSV 已填助手初稿并逐行标注「请复核/修订」；修订后保留该文件为**用户确认终版**。",
           "2. 终版定稿后运行复算（不调 API）：`python -m backend.scripts.trial_metrics`（指标）与 "
           "`python -m backend.scripts.eval_recall`（recall@10）自动改用终版 gold。",
           "3. KP 描述修订（如 077/079 边界、量率/比 边界、率类/浓度 重叠）：编号冻结只许追加，"
           "**改描述需您逐条批准**（决策㉔）。", ""]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print(f"材料包已生成：\n  {OUT_MD}\n  {OUT_JSON}")
    print(f"硬误标题数核对：{len(hard_ids)}（应=15）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
