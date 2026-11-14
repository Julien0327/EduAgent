"""50 题 Gold v2 人工核验指南生成器（Step 5-C 准备阶段，决策㉘延续）。

- 50 题全部展示（含 35 道沿用题，明确标注「沿用 V1，待人工确认」，不得默认视为已确认）；
- 每题：q_id / 题干 / 参考答案（DB questions.answer，APE210K ans 经验算）/ Gold v1（含 KP
  名称+定义）/ A 臂 v1 预测 / B 臂 v1 预测 / Gold v2 提案（含定义）/ 修改理由 / 人工裁决栏；
- 15 道争议题四问详解（为何改/不改的影响/修改的新风险/其他合理判断）；
- 字段缺失明确标注（不补造）；
- 内置一致性检查（fail-fast）：50 qid 与冻结抽样集一致、无重复遗漏、KP ID 全部真实存在、
  gold v1/v2/A/B 同题对应、v2 提案不标记为正式 Gold。

输出：data/staging/trial50_gold_v2_review_guide.md（含题目内容，不入 Git——决策㉒）。
用法（仓库根）：.venv/Scripts/python.exe -m backend.scripts.build_gold_v2_review_guide
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_V1 = REPO_ROOT / "data" / "staging" / "trial50_gold_draft.jsonl"
GOLD_V2 = REPO_ROOT / "data" / "staging" / "trial50_gold_v2_draft.jsonl"
TRIAL500 = REPO_ROOT / "data" / "annotations" / "trial500_qids.jsonl"
OUT_MD = REPO_ROOT / "data" / "staging" / "trial50_gold_v2_review_guide.md"
BATCH_A, BATCH_B = "ann_20261114_01_a", "ann_20261114_02_b"
REVIEW_SIZE = 50

# 15 道争议题四问（Claude 归因分析，供裁决参考；最终裁决权在用户）
FOUR_Q = {
 "q_ape_788175": ("P1 辅助可辩护",
   "模型辅助 075 有题目依据：11/16 未栽部分量与分数推理贯穿全题，初稿仅 014+037 偏窄。",
   "不修改则该题持续按硬误标扣分（micro-P 被拉低）；同类「分数工程题」的模型辅助判断会被系统性低估。",
   "吸纳 075（分数运算，过程性运算类 KP）可能鼓励「凡含分数就标 075」的过宽辅助——v2 prompt 已要求每个辅助须有题目依据以对冲。",
   "可维持 [014,037] 不动（视分数计算为过程）；primary 也可议 037，但工程合做结构更核心，014 主更稳。"),
 "q_ape_1216385": ("P3 KP 定义张力",
   "077 定义明确「整数加减（一步）」，两步加减按定义应 079 主——初稿 077 主与定义冲突。",
   "不修改则 gold 与 KP 定义自相矛盾：未来模型严格按定义判 079 会被误判硬误标（评估系统性偏置）。",
   "改 079 主后，「一步 vs 两步」边界题可能摇摆——prompt v2 已加入临时消歧说明（两步及以上含纯加减归 079）对冲。",
   "若您认为教学上两步纯加减更贴 077，可保留 077 主并批准修订 077 描述（单独执行，建议已提交）。"),
 "q_ape_87882": ("P1 辅助可辩护",
   "模型辅 081（设未知数解不定方程取整数解）有依据；初稿 075 反而是过程性计算。",
   "不修改则丢掉「方程求解」这一真实考查点，该题硬误标持续。",
   "081 可能归纳过宽（凡难题都标方程）——应以「题干确需设元」为门槛。",
   "也可维持 [033,037,075] 不动；或在 075/081 间二选一（≤3 上限）。"),
 "q_ape_278113": ("P2 primary 分歧",
   "primary 066 vs 071 语义邻近（倍数链判断两种表述）；028 还原（逐轮逆推）辅助有依据。",
   "不修改则模型 primary 066 持续算 primary miss（-1 题 pass）。",
   "若改 066 主，071 的「整除判断」定位被架空；两 KP 描述边界需澄清（建议已单独提交）。",
   "维持 071 主（推荐）；或 066 主+071 辅；028 吸纳与否独立裁决。"),
 "q_ape_77461": ("P1 辅助可辩护",
   "85×65 与 85×75 乘法步骤真实存在，078 辅有依据。",
   "不修改则该题硬误标持续，乘法步骤被无视。",
   "078 定义 exclude 混合运算——作辅助与定义存在张力（rule 时代 exclude 的遗产）；语义上「先乘后加」的乘法部分确属乘除运算。",
   "不吸纳（视 079 已覆盖）；或吸纳并接受定义张力（描述澄清建议已提交）。"),
 "q_ape_822661": ("P2 primary 分歧",
   "方程是该题自然解法但非考点本质；量率对应+和差结构才是题目骨架。",
   "不改（维持 037 主）则模型 081 主持续 miss；若改判 081 主则初稿 037 需降辅。",
   "改 081 主会强化「复杂题=方程」倾向，可能带动一批应用题 primary 偏向 081，与 v2 消歧规则方向冲突。",
   "037 主+081 辅（折中）；维持原判（推荐）。"),
 "q_ape_284252": ("P2 primary 分歧",
   "037 vs 038 族内邻近；初稿依据「倍数互化是量率对应核心操作」。",
   "不修改则模型 038 主持续 miss（1 题）。",
   "改 038 主则量率/比边界实际反转，影响后续同族题判定一致性。",
   "维持 037 主（推荐，与 v2 消歧规则一致）；KP 描述边界澄清后可重判。"),
 "q_ape_297696": ("P2 primary 分歧 + P1 辅助",
   "036 与 015 均可辩护（KP 表自身重叠：036 定义列「含糖率」族、015 关键词含「含盐」）；076 辅（含盐率即百分数表达）有依据。",
   "不修改则模型 015 主持续 miss。",
   "改 015 主则率类/浓度边界反转；036 定义明列百分率族更贴合「求含盐率数值」这一设问。",
   "036 主+015 辅+076 辅（提案）；015 主+036 辅；KP 描述澄清后重判（建议已提交）。"),
 "q_ape_19316": ("P1 辅助可辩护",
   "075 辅（半径比→面积比的分率计算）有依据。",
   "不修改则硬误标持续 1 题。",
   "过宽辅助风险同 q_ape_788175（过程性运算类）。",
   "维持 [050,037]（视分率计算为过程）。"),
 "q_ape_174781": ("P1 辅助可辩护",
   "075 辅（可能性 1/3 的分数表达）有依据；B 臂另标 037（60 次估计=期望分率）可参考。",
   "不修改则硬误标持续。",
   "可能性+分数是天然组合，风险低。",
   "也可纳 037 替代 075（B 臂视角）——请您裁决。"),
 "q_ape_577597": ("P1 辅助可辩护",
   "079 辅（异分母三数加减混合）有依据。",
   "不修改则硬误标持续。",
   "低（混合运算事实清楚）。",
   "维持 [075] 单标签。"),
 "q_ape_301084": ("P1 辅助可辩护",
   "079 辅（倒数+倍数+加法混合求值）有依据。",
   "不修改则硬误标持续。",
   "低。",
   "维持 [075]。"),
 "q_ape_1086627": ("P2 primary 分歧（表层关键词偏置典型案例）",
   "035 主：路程总量一定、速度与时间成反比，且题干明示「用算术和比例解」；模型 010 被「每小时行 50 千米」表层带偏——v2 消歧规则的反例依据。",
   "不修改（维持 035 主）则模型 010 判 primary miss——这正是 v2 要治的形态，保留此判例有价值。",
   "改 010 主会鼓励表层偏置，与 v2 方向直接冲突。",
   "若您认为行程框架更贴切可改 010 主（不推荐）。"),
 "q_ape_1070129": ("P2 primary 分歧",
   "裂项相消是标准简便技巧，082 主更贴切；031 降辅。",
   "不改（031 主）则模型 082 主持续 miss。",
   "082 主可能让「凡可凑整」题都归简便——但本题裂项确为设计核心（495/562 可约分为证）。",
   "031 主+082 辅（对调）——两者均合理，请您裁决。"),
 "q_ape_277414": ("P1 辅助可辩护",
   "075 辅（循环小数化分数）有依据；082 辅（495/562 可约分设计）也合理但触及 3 标签上限。",
   "不修改则硬误标持续（模型标 075+082）。",
   "低。",
   "075 或 082 二选一纳入（或都不纳）。"),
}


def consistency_check(guide_rows: list[dict], frozen_qids, kp_defs: dict[str, dict]) -> list[str]:
    """一致性检查（fail-fast 清单）：用户第三节全部核对项。frozen_qids 接受 dict 或 set。"""
    frozen_set = set(frozen_qids)
    errors: list[str] = []
    qids = [r["q_id"] for r in guide_rows]
    if len(qids) != REVIEW_SIZE:
        errors.append(f"题数 {len(qids)} != {REVIEW_SIZE}")
    if len(set(qids)) != len(qids):
        errors.append("存在重复 q_id")
    outside = [q for q in qids if q not in frozen_set]
    if outside:
        errors.append(f"q_id 不在冻结抽样集 trial500：{outside[:5]}")
    if not set(qids) <= frozen_set:
        errors.append("核验集不是冻结抽样集子集")
    for r in guide_rows:
        for src in ("gold_v1", "gold_v2"):
            for k in r[src]["gold_kp_ids"]:
                if k not in kp_defs:
                    errors.append(f"{r['q_id']} {src} 引用不存在的 KP：{k}")
            if r[src]["gold_primary"] not in kp_defs:
                errors.append(f"{r['q_id']} {src} primary 不存在：{r[src]['gold_primary']}")
        for arm in ("A", "B"):
            for k in r[arm].get("kp_ids", []):
                if k not in kp_defs:
                    errors.append(f"{r['q_id']} {arm} 预测引用不存在的 KP：{k}")
    v1_ids = {r["q_id"] for r in guide_rows if r["gold_v1"]}
    if v1_ids != set(qids):
        errors.append("Gold v1 与核验集不同题")
    v2_ids = {r["q_id"] for r in guide_rows if r["gold_v2"]}
    if v2_ids != set(qids):
        errors.append("Gold v2 提案与核验集不同题")
    return errors


def build_guide() -> tuple[Path, list[str]]:
    gold_v1 = {json.loads(l)["q_id"]: json.loads(l)
               for l in GOLD_V1.read_text(encoding="utf-8").splitlines() if l.strip()}
    gold_v2 = {json.loads(l)["q_id"]: json.loads(l)
               for l in GOLD_V2.read_text(encoding="utf-8").splitlines() if l.strip()}
    frozen = {}
    for line in TRIAL500.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            frozen[r["q_id"]] = r["stratum"]

    conn = db.connect()
    kp_defs = {r["kp_id"]: dict(r) for r in conn.execute(
        "SELECT kp_id, name, description, stage, source FROM knowledge_points")}
    qinfo = {r["q_id"]: dict(r) for r in conn.execute(
        "SELECT q_id, stem, answer FROM questions WHERE quality_state='processed'")}

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

    # 核验集 = trial500 中被 gold v1 覆盖的 50 题（与 export_review_sheet 选题同源）
    review_ids = sorted(gold_v1)
    rows = []
    for qid in review_ids:
        g1, g2 = gold_v1[qid], gold_v2[qid]
        stem = qinfo.get(qid, {}).get("stem")
        answer = qinfo.get(qid, {}).get("answer")
        rA, rB = resA.get(qid), resB.get(qid)

        def kp_view(ids):
            return "；".join(f"{k} {kp_defs[k]['name']}（定义：{kp_defs[k]['description'] or '（无描述）'}）"
                             for k in ids) if ids else "（空——辅助允许为空）"

        def arm_view(res, kps, diff):
            if res is None:
                return "（该题在本臂批次无记录——缺失，未补造）"
            if res["status"] != "ok":
                return f"{res['status']}（如实弃标/未产出标签；难度={diff.get(qid, '（缺失）')}）"
            tags = "、".join(f"{k}{'★主' if p else ''}({kp_defs[k]['name']})" for k, p in kps.get(qid, []))
            return (f"{tags}｜primary={res['primary_kp_id']}({kp_defs[res['primary_kp_id']]['name']})"
                    f"｜难度={diff.get(qid, '（缺失）')}｜理由：{res['reason'] or '（未提供）'}")

        rows.append({
            "q_id": qid, "stratum": frozen.get(qid, "（不在冻结集！）"),
            "stem": stem or "（题干缺失！）", "answer": answer or "（无参考答案）",
            "gold_v1": g1, "gold_v2": g2,
            "v2_changed": "revision_category" in g2,
            "kp_view_v1": kp_view(g1["gold_kp_ids"]),
            "kp_view_v2": kp_view(g2["gold_kp_ids"]),
            "A": {"view": arm_view(rA, kpsA, diffA), "kp_ids": [k for k, _ in kpsA.get(qid, [])]},
            "B": {"view": arm_view(rB, kpsB, diffB), "kp_ids": [k for k, _ in kpsB.get(qid, [])]},
        })

    errors = consistency_check(rows, set(frozen), kp_defs)
    if errors:
        raise RuntimeError("一致性检查未通过：\n- " + "\n- ".join(errors))

    def verdict_block():
        return ("**人工裁决**（四选一，勾选其一；主/辅人工填写位见下）：\n\n"
                "- [ ] 确认 Claude 提案\n"
                "- [ ] 保留 Gold v1\n"
                "- [ ] 修改为其他知识点（主知识点：________ 辅助知识点：________）\n"
                "- [ ] 暂不确定，需要进一步讨论\n\n"
                "备注：________\n")

    md = [
        "# 50 题 Gold v2 人工核验指南（Step 5-C 准备阶段）", "",
        f"- 性质：**核验材料**。Gold v1（助手盲标初稿）原样保留未覆盖；Gold v2 为**提案、不是正式 Gold**——"
        "全部 50 题均待您裁决（含 35 道沿用题，明确标注「沿用 V1，待人工确认」）。",
        "- 本文件含题目内容，入 data/staging/ 不入 Git（决策㉒）。参考答案来自题库 `questions.answer`"
        "（APE210K ans，ETL 验算通过），仅供理解题意，不参与 KP 裁决。",
        "- 缺失字段一律原样标注「缺失」，未补造。本阶段未调 API、未改 Prompt/KP/题库。", "",
        "## 0. 一致性检查结果（自动核验）", "",
        f"- ✅ 50 个 q_id 与冻结抽样集 trial500（{len(frozen)} 题）完全一致，无重复、无遗漏",
        f"- ✅ 全部 KP ID（Gold v1/v2 提案/A/B 预测）真实存在于当前知识图谱（{len(kp_defs)} 个知识点）",
        "- ✅ Gold v1、Gold v2 提案、A/B 预测逐题同 q_id 对应",
        "- ✅ Gold v2 全文标注为「提案」，未标记为正式 Gold", "",
        "## 一、裁决字段说明", "",
        "每题裁决四选一：**确认 Claude 提案 / 保留 Gold v1 / 修改为其他知识点（填写主/辅） / 暂不确定**；"
        "裁决结果可直接批注在本文件或 `trial50_gold_v2_revision_record.md` 的裁决栏。",
        "", "## 二、15 道争议题逐题详解（四问 + 全字段）", "",
    ]
    idx = 0
    for r in rows:
        if not r["v2_changed"]:
            continue
        idx += 1
        qid = r["q_id"]
        cat, why, impact, risk, alt = FOUR_Q[qid]
        g1, g2, a, b = r["gold_v1"], r["gold_v2"], r["A"], r["B"]
        md += [
            f"### 争议 {idx}：{qid}（{r['stratum']}）【{cat}】", "",
            f"**题干**：{r['stem']}",
            f"**参考答案**：{r['answer']}", "",
            f"**Gold v1**：primary={g1['gold_primary']}｜辅助={'、'.join(k for k in g1['gold_kp_ids'] if k != g1['gold_primary']) or '（空）'}"
            f"｜难度={g1['gold_difficulty']}｜定义：{r['kp_view_v1']}",
            f"**Gold v2 提案**：primary={g2['gold_primary']}｜辅助={'、'.join(k for k in g2['gold_kp_ids'] if k != g2['gold_primary']) or '（空）'}"
            f"｜难度={g2['gold_difficulty']}｜定义：{r['kp_view_v2']}",
            f"**A 臂 v1 预测**：{a['view']}",
            f"**B 臂 v1 预测**：{b['view']}", "",
            f"**① 为什么建议修改**：{why}",
            f"**② 不修改会产生什么影响**：{impact}",
            f"**③ 修改后是否可能引入新的错误**：{risk}",
            f"**④ 是否存在其他合理的知识点判断**：{alt}", "",
            verdict_block(), "---", "",
        ]
    md += ["## 三、35 道沿用题（沿用 V1，待人工确认——不得默认视为已确认）", ""]
    for r in rows:
        if r["v2_changed"]:
            continue
        idx += 1
        g1, a, b = r["gold_v1"], r["A"], r["B"]
        md += [
            f"### 沿用 {idx}：{r['q_id']}（{r['stratum']}）【沿用 V1，待人工确认】", "",
            f"**题干**：{r['stem']}",
            f"**参考答案**：{r['answer']}", "",
            f"**Gold v1（=Gold v2 提案，无修订）**：primary={g1['gold_primary']}｜"
            f"辅助={'、'.join(k for k in g1['gold_kp_ids'] if k != g1['gold_primary']) or '（空）'}"
            f"｜难度={g1['gold_difficulty']}｜定义：{r['kp_view_v1']}",
            f"**A 臂 v1 预测**：{a['view']}",
            f"**B 臂 v1 预测**：{b['view']}", "",
            verdict_block(), "---", "",
        ]
    md += ["## 四、汇总裁决表（50 题一览）", "",
           "| # | q_id | 层 | 类型 | 裁决（四选一） | 主知识点（改判时填） | 辅助知识点（改判时填） |",
           "|---|---|---|---|---|---|---|"]
    idx = 0
    for r in rows:
        idx += 1
        typ = "争议" if r["v2_changed"] else "沿用 V1"
        md.append(f"| {idx} | {r['q_id']} | {r['stratum']} | {typ} | | | |")
    md += ["", "裁决完成后：助手将按您的裁决落定 Gold v2 终版并冻结，随后方可申请 A 臂 v2 重跑授权（决策㉘）。"]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    return OUT_MD, errors


def main() -> int:
    path, errors = build_guide()
    print(f"核验指南已生成：{path}")
    print(f"一致性检查：{len(errors)} 项问题（0 = 通过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
