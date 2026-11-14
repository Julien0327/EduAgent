"""检索评测查询集构建脚本（Phase 2 / S7，决策㉛④，零 API / 零标注变更）。

§4.5 要求 50 条标注查询（Recall@5 / Recall@1 / MRR 评测用）。构成（决策㉛④）：
- 来源 A（verified 派生）：verified=1 的人工核验题（49 道，决策㉚⑤）——
  查询=题干，gold=Gold v2 终版的人工核验知识点（kp_ids/primary）；
- 来源 B（手写草稿）：初中图谱按章节抽样的 KP——查询=名称+描述组合短语的
  **助手初拟变体**，gold=该 KP；**标 draft 待用户核验修订**（Gold 归用户惯例，
  决策㉗③）。

输出（staging 不入 Git，决策⑳）：
    data/staging/retrieval_eval_queries_v1.jsonl   50 条查询
    data/staging/retrieval_eval_queries_review.md  核验指南（含裁决栏模板）

复现：python -m backend.scripts.build_retrieval_eval_queries
"""
import json
import sys
from pathlib import Path

from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "data" / "staging"
GOLD_V2 = REPO_ROOT / "data" / "staging" / "trial50_gold_v2.jsonl"
TARGET_TOTAL = 50
HANDWRITTEN_TARGET = 20  # 初中手写配额（verified 49 中取 30 + 手写 20 = 50）


def load_gold_v2() -> dict[str, dict]:
    rows = [json.loads(line) for line in
            GOLD_V2.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["q_id"]: r for r in rows if r.get("status") == "confirmed"}


def build_verified_queries(conn) -> list[dict]:
    gold = load_gold_v2()
    queries = []
    for q_id, g in gold.items():
        row = conn.execute(
            "SELECT stem, stage, verified FROM questions WHERE q_id=?", (q_id,)
        ).fetchone()
        if row is None or not row["verified"]:
            continue  # 未核验/不在库（防静默漏）
        queries.append({
            "query_id": f"v_{q_id}",
            "query_text": row["stem"],
            "stage": row["stage"],
            "gold_kp_ids": g["gold_kp_ids"],
            "gold_primary": g["gold_primary"],
            "source": "verified_derived",
            "note": "题干作查询；gold=人工核验知识点（Gold v2 终版 confirmed）",
        })
    return queries


def build_handwritten_drafts(conn, count: int) -> list[dict]:
    """初中 KP 手写查询草稿：按章节分组抽样，查询=口语化组合短语（助手初拟）。"""
    rows = conn.execute(
        """SELECT kp_id, name, chapter, description FROM knowledge_points
           WHERE stage='junior' ORDER BY chapter, kp_id"""
    ).fetchall()
    by_chapter: dict[str, list] = {}
    for r in rows:
        by_chapter.setdefault(r["chapter"] or "未分章", []).append(r)
    # 轮转抽样保证章节覆盖
    picked: list = []
    chapters = sorted(by_chapter)
    i = 0
    while len(picked) < count:
        advanced = False
        for ch in chapters:
            bucket = by_chapter[ch]
            if i < len(bucket):
                picked.append(bucket[i])
                advanced = True
                if len(picked) >= count:
                    break
        if not advanced:
            break
        i += 1
    queries = []
    for r in picked:
        # 口语化查询变体（初拟）：教师可能的问法；**待用户核验修订**
        variants = [
            f"怎么讲{r['name']}？",
            f"{r['chapter']}中{r['name']}的题目",
            f"{r['name']}典型例题",
        ]
        q = variants[len(queries) % len(variants)]
        queries.append({
            "query_id": f"h_{r['kp_id']}",
            "query_text": q,
            "stage": "junior",
            "gold_kp_ids": [r["kp_id"]],
            "gold_primary": r["kp_id"],
            "source": "handwritten_draft",
            "note": f"助手初拟查询变体（gold={r['kp_id']} {r['name']}）——待用户核验修订",
        })
    return queries


def main() -> int:
    conn = db.connect()
    try:
        verified = build_verified_queries(conn)
        n_verified = min(len(verified), TARGET_TOTAL - HANDWRITTEN_TARGET)
        verified = verified[:n_verified]
        drafts = build_handwritten_drafts(conn, TARGET_TOTAL - n_verified)
    finally:
        conn.close()

    all_q = verified + drafts
    STAGING.mkdir(parents=True, exist_ok=True)
    out = STAGING / "retrieval_eval_queries_v1.jsonl"
    out.write_text(
        "\n".join(json.dumps(q, ensure_ascii=False) for q in all_q), encoding="utf-8")

    lines = [
        "# 检索评测查询集 v1 核验指南（Phase 2 / S7，决策㉛④）",
        "",
        f"- 总数 {len(all_q)} 条 = verified 派生 {len(verified)} + 手写草稿 {len(drafts)}",
        f"- 文件：`retrieval_eval_queries_v1.jsonl`（staging，不入 Git）",
        "- **Gold 归用户**（决策㉗③惯例）：verified 派生部分沿用人工核验标签（抽查即可）；"
        "手写草稿部分**逐条核验**：查询表述是否自然、gold KP 是否正确、是否需要改写。",
        "- 用途：§4.5 的 Recall@5 / Recall@1 / MRR / NDCG@5 实测（检索层向量+关键词双路）。",
        "",
        "| query_id | 查询 | gold primary | 来源 | 裁决（通过/修改/删除） |",
        "|---|---|---|---|---|",
    ]
    for q in all_q:
        lines.append(f"| {q['query_id']} | {q['query_text'][:40]} | "
                     f"{q['gold_primary']} | {q['source']} | |")
    (STAGING / "retrieval_eval_queries_review.md").write_text(
        "\n".join(lines), encoding="utf-8")

    print(f"查询集 v1：{len(all_q)} 条（verified {len(verified)} + draft {len(drafts)}）")
    print(f"输出：{out}")
    print(f"核验指南：{STAGING / 'retrieval_eval_queries_review.md'}")
    print("等待用户核验（手写草稿逐条）后冻结为终版。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
