"""正式检索评测集构建（Phase 2 人工验收修正，2026-11-15）。

按决策㉛④修正构成（此前实现 30+20 不符原决策，本次纠正）：
- **正式评测集 = 50 条**：
    49 条 Gold v2 confirmed verified 派生（全量，不排除）
  + 1 条初中手写查询（draft，待用户核验）
- **额外探索集 = 20 条**（原手写草稿，全部保留；标 exploratory，**不转正、不入正式指标**）。
- 原始文件**只读不改**：`trial50_gold_v2.jsonl`、`retrieval_eval_queries_v1.jsonl`
  （脚本记录前后 sha256 以证明未变）。
- 不改检索运行时代码；不计算 Recall/MRR/NDCG；不评分。

输出（staging，不入 Git）：
    retrieval_eval_queries_formal_v1.jsonl   正式集 50 条
    retrieval_eval_queries_extra_drafts.jsonl 探索集 20 条
    retrieval_eval_queries_review.md         核验指南（正式集逐条 + 19 条附录 + 探索集清单）
    retrieval_eval_set_counts.json           数量核对与哈希证据

用法：.venv/Scripts/python.exe -m backend.scripts.build_formal_eval_set
"""
import hashlib
import json
import sys
from pathlib import Path

from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "data" / "staging"
GOLD_FILE = STAGING / "trial50_gold_v2.jsonl"
OLD_MIXED = STAGING / "retrieval_eval_queries_v1.jsonl"
FORMAL_OUT = STAGING / "retrieval_eval_queries_formal_v1.jsonl"
EXTRA_OUT = STAGING / "retrieval_eval_queries_extra_drafts.jsonl"
REVIEW_OUT = STAGING / "retrieval_eval_queries_review.md"
COUNTS_OUT = STAGING / "retrieval_eval_set_counts.json"

# 正式集补充的 1 条手写查询（初中，代表课例；与 20 条探索草稿不重复）
SUPPLEMENT_DRAFT = {
    "query_id": "h_kp_j8_1408",
    "query_text": "因式分解的提公因式法怎么讲？",
    "stage": "junior",
    "gold_primary": "kp_j8_1408",
    "source": "handwritten_draft",
    # 2026-11-15 用户裁决：通过并转正（primary=kp_j8_1408；辅助无；查询原样；
    # 不增加 kp_j8_1404 / kp_j8_1409 等关联知识点）
    "status": "confirmed",
    "gold_status": "confirmed",
    "confirmation_origin": "user_ruling_2026-11-15",
    "verdict_source": "user_ruling_2026-11-15",
    "note": "用户裁决：通过并转正；primary=kp_j8_1408；辅助知识点：无；查询文本保持原样；不增加 kp_j8_1404、kp_j8_1409 等关联知识点。",
    "origin": "assistant_draft_2026-11-15",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main() -> int:
    gold_hash_before = sha256(GOLD_FILE)
    old_hash_before = sha256(OLD_MIXED)

    gold_rows = load_jsonl(GOLD_FILE)
    confirmed = [g for g in gold_rows if g.get("status") == "confirmed"]
    pending = [g for g in gold_rows if g.get("status") != "confirmed"]
    old_queries = load_jsonl(OLD_MIXED)
    old_verified_ids = {q["query_id"].removeprefix("v_")
                        for q in old_queries if q["source"] == "verified_derived"}
    old_drafts = [q for q in old_queries if q["source"] == "handwritten_draft"]

    conn = db.connect()
    try:
        kp_name = {r["kp_id"]: r["name"]
                   for r in conn.execute("SELECT kp_id, name FROM knowledge_points")}
        question_rows = {
            r["q_id"]: r
            for r in conn.execute(
                "SELECT q_id, stem, stage, verified FROM questions "
                "WHERE q_id IN (%s)" % ",".join("?" * len(confirmed)),
                [g["q_id"] for g in confirmed],
            )
        }
    finally:
        conn.close()

    # ---------- 正式集：49 verified 派生（全部 confirmed） ----------
    formal: list[dict] = []
    missing: list[str] = []
    for g in confirmed:
        q = question_rows.get(g["q_id"])
        if q is None:
            missing.append(g["q_id"])
            continue
        formal.append({
            "query_id": f"v_{g['q_id']}",
            "query_text": q["stem"],
            "stage": q["stage"],
            "gold_kp_ids": g["gold_kp_ids"],
            "gold_primary": g["gold_primary"],
            "gold_primary_name": kp_name.get(g["gold_primary"], ""),
            "gold_kp_names": [kp_name.get(k, "") for k in g["gold_kp_ids"]],
            "source": "verified_derived",
            "status": "confirmed",
            "gold_status": g.get("status"),
            "confirmation_origin": "gold_v2_carry_over_2026-11-14",
            "verdict_source": g.get("verdict_source", ""),
            "origin": "trial50_gold_v2.jsonl",
            "usage": "formal",
        })
    if missing:
        raise SystemExit(f"confirmed 题在库中缺失（拒绝继续）：{missing}")

    # ---------- 正式集：补 1 条手写 draft ----------
    supplement = dict(SUPPLEMENT_DRAFT)
    supplement["gold_kp_ids"] = [supplement["gold_primary"]]
    supplement["gold_primary_name"] = kp_name.get(supplement["gold_primary"], "")
    supplement["gold_kp_names"] = [supplement["gold_primary_name"]]
    supplement["usage"] = "formal"
    formal.append(supplement)

    if len(formal) != 50:
        raise SystemExit(f"正式集数量异常：{len(formal)}（期望 50）")

    # ---------- 探索集：原 20 条草稿（全部保留，不转正） ----------
    extra = []
    for q in old_drafts:
        item = dict(q)
        item["gold_primary_name"] = kp_name.get(item["gold_primary"], "")
        item["gold_kp_names"] = [kp_name.get(k, "") for k in item["gold_kp_ids"]]
        item["status"] = "draft_not_promoted"
        item["usage"] = "exploratory"
        item["formal_metric"] = False
        item["origin"] = "retrieval_eval_queries_v1.jsonl"
        extra.append(item)

    FORMAL_OUT.write_text(
        "\n".join(json.dumps(q, ensure_ascii=False) for q in formal), encoding="utf-8")
    EXTRA_OUT.write_text(
        "\n".join(json.dumps(q, ensure_ascii=False) for q in extra), encoding="utf-8")

    # ---------- 核对 ----------
    formal_ids = [q["query_id"] for q in formal]
    gold_by_id = {g["q_id"]: g for g in confirmed}
    original_49_gold_unchanged = all(
        q["gold_kp_ids"] == gold_by_id[q["query_id"].removeprefix("v_")]["gold_kp_ids"]
        and q["gold_primary"] == gold_by_id[q["query_id"].removeprefix("v_")]["gold_primary"]
        for q in formal if q["source"] == "verified_derived"
    )
    checks = {
        "formal_count": len(formal),
        "formal_verified_derived": sum(1 for q in formal if q["source"] == "verified_derived"),
        "formal_newly_confirmed": sum(1 for q in formal if q["status"] == "confirmed"
                                       and q["source"] == "handwritten_draft"),
        "formal_all_confirmed": all(q["status"] == "confirmed" for q in formal),
        "formal_pending": sum(1 for q in formal if q["status"] != "confirmed"),
        "original_49_gold_unchanged": original_49_gold_unchanged,
        "extra_exploratory_count": len(extra),
        "extra_all_not_promoted": all(q["status"] == "draft_not_promoted" for q in extra),
        "formal_query_ids_unique": len(formal_ids) == len(set(formal_ids)),
        "formal_no_empty_stem": all(q["query_text"].strip() for q in formal),
        "formal_gold_primary_all_in_graph": all(q["gold_primary"] in kp_name for q in formal),
        "pending_excluded": [g["q_id"] for g in pending],
        "gold_untouched": sha256(GOLD_FILE) == gold_hash_before,
        "old_mixed_untouched": sha256(OLD_MIXED) == old_hash_before,
        "old_mixed_superseded": True,
    }

    # 19 条“此前构成偏差中被排除”的 confirmed（现已全部纳入正式集）
    previously_excluded = [g for g in confirmed if g["q_id"] not in old_verified_ids]
    formal_no = {q["query_id"]: i for i, q in enumerate(formal, 1)}

    # ---------- 核验指南 ----------
    def kp_label(kp_id: str) -> str:
        return f"{kp_id}（{kp_name.get(kp_id, '未找到名称')}）"

    lines = [
        "# 正式检索评测集核验指南（Phase 2 人工验收，2026-11-15 修正版）",
        "",
        "> 本文件由 `backend/scripts/build_formal_eval_set.py` 生成（只读原始数据，不修改 Gold）。",
        "> **未评分、未计算 Recall@1 / Recall@5 / MRR / NDCG@5**；裁决栏留空等待用户核验。",
        "",
        "## 0. 构成（修正后）",
        "",
        "| 集合 | 数量 | 构成 | 用途 | 状态 |",
        "|---|---|---|---|---|",
        "| **正式评测集** | **50** | 49 条 Gold v2 confirmed verified 派生（全量）+ 1 条新确认查询（h_kp_j8_1408，user_ruling_2026-11-15） | 正式指标计算（已冻结） | **已确认** |",
        "| **额外探索集** | **20** | 原手写草稿全部保留 | 探索性测试，**不入正式指标** | 不转正 |",
        "| 历史混合文件 | 50 | 旧的 30 verified + 20 draft | **已被正式集取代，勿用于指标** | 只读保留 |",
        "",
        f"- Gold v2 confirmed 题池 **49 条全部纳入**；pending_verification："
        f"{', '.join(g['q_id'] for g in pending)}（按决策㉚不纳入，不进指标分母）。",
        "- 原始 `trial50_gold_v2.jsonl` 与 `retrieval_eval_queries_v1.jsonl` **未修改**"
        "（sha256 前后一致，见 `retrieval_eval_set_counts.json`）。",
        "- 本次**不修改检索运行时代码**；题目重排问题作为已知缺陷记录，待正式基线后另行裁决修复范围。",
        "",
        "## A 区：正式集 49 条 verified 派生（沿用 Gold v2 confirmed，待您抽查）",
        "",
    ]

    for i, q in enumerate(formal[:-1], 1):
        gold_all = "；".join(
            f"{kid}（{name}）" for kid, name in zip(q["gold_kp_ids"], q["gold_kp_names"]))
        lines += [
            f"### A-{i:02d} `{q['query_id']}`　来源：verified_derived　状态：**待您抽查（Gold 未改动）**",
            "",
            f"- **查询全文（题干）**：{q['query_text']}",
            f"- **Gold primary**：{kp_label(q['gold_primary'])}",
            f"- **Gold 全量**：{gold_all}",
            f"- **标签状态**：{q['gold_status']}｜溯源：{q['verdict_source'] or '（缺省）'}｜原始来源：{q['origin']}",
            f"- **裁决**：☐ 通过　☐ 修改（请注明）　☐ 删除　｜ 备注：",
            "",
        ]

    draft = formal[-1]
    lines += [
        "## B 区：正式集补充的 1 条查询（**已由用户裁决转正**）",
        "",
        f"### B-01 `{draft['query_id']}`　来源：handwritten_draft　状态：**已确认（user_ruling_2026-11-15）**",
        "",
        f"- **查询文本**：{draft['query_text']}（保持原样）",
        f"- **Gold primary**：{kp_label(draft['gold_primary'])}",
        f"- **Gold 全量**：{kp_label(draft['gold_primary'])}（辅助知识点：无）",
        f"- **裁决记录**：{draft['note']}",
        f"- **确认来源**：{draft.get('confirmation_origin', '')}",
        "",
        "## 附录 A：此前构成偏差中被排除的 19 条 confirmed（现已全部纳入正式集）",
        "",
        "> 应验收要求补充完整题干、Gold primary、Gold 全量标签与来源信息。",
        "> **均已包含在上方 A 区**（正式编号见末列），本附录用于审计对照，**未修改任何标签**。",
        "",
    ]
    for i, g in enumerate(previously_excluded, 1):
        q = question_rows[g["q_id"]]
        gold_all = "；".join(kp_label(k) for k in g["gold_kp_ids"])
        formal_idx = formal_no.get(f"v_{g['q_id']}", "?")
        lines += [
            f"### X-{i:02d} `v_{g['q_id']}`　对应正式编号：A-{formal_idx:02d}",
            "",
            f"- **查询全文（题干）**：{q['stem']}",
            f"- **Gold primary**：{kp_label(g['gold_primary'])}",
            f"- **Gold 全量**：{gold_all}",
            f"- **来源**：Gold v2 终版 confirmed｜溯源：{g.get('verdict_source', '') or '（缺省）'}",
            "",
        ]

    lines += [
        "## 附录 B：额外探索集（20 条，**不转正、不入正式指标**）",
        "",
        "> 完整数据见 `retrieval_eval_queries_extra_drafts.jsonl`（usage=exploratory，"
        "formal_metric=false）。以下仅列编号/查询/目标 KP，供您决定是否后续复核。",
        "",
        "| # | query_id | 查询文本 | Gold primary | 名称 | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    for i, q in enumerate(extra, 1):
        lines.append(
            f"| {i} | `{q['query_id']}` | {q['query_text']} | {q['gold_primary']} | "
            f"{q.get('gold_primary_name', '')} | 草稿未转正 |")

    lines += [
        "",
        "## 附录 C：修改建议（单独列出，**本次未执行**）",
        "",
        "1. `q_ape_1070129`（pending_verification）保持排除在正式 49 条之外；"
        "源数据 ans↔题干不一致已在决策㉚记录，未修改 Gold。",
        "2. ~~正式集补充的 `h_kp_j8_1408` 需核验~~ → **已裁决（2026-11-15）**：通过并转正，"
        "primary=kp_j8_1408，辅助无，查询原样，不增加关联知识点。",
        "3. 20 条探索草稿的查询表述（部分为模板化变体）建议后续人工改写；"
        "本次**未改动**，且**未混入正式集**。",
        "4. 题目重排的弱 KP 占位问题（阶段 3 Jaccard 平局）作为已知缺陷记录，"
        "待正式指标基线后另行决定修复范围；本次未改检索运行时代码。",
        "",
        "## 核验完成后的下一步（等待用户）",
        "",
        "1. 您在 A 区抽查、B 区裁决、附录 C 逐项决定；",
        "2. 助手按裁决冻结正式集（改动逐条留痕，Gold 修改建议单独提交批准）；",
        "3. 冻结后方可计算 Recall@1 / Recall@5 / MRR / NDCG@5 并回填 STATUS。",
        "",
    ]

    REVIEW_OUT.write_text("\n".join(lines), encoding="utf-8")
    COUNTS_OUT.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(checks, ensure_ascii=False, indent=2))
    print(f"\n正式集：{FORMAL_OUT}")
    print(f"探索集：{EXTRA_OUT}")
    print(f"核验指南：{REVIEW_OUT}")
    print(f"数量核对与哈希证据：{COUNTS_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
