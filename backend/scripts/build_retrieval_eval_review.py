"""检索评测查询集核验材料生成脚本（Phase 2 人工验收，只读不改数据）。

输入（均已冻结，不修改）：
    data/staging/retrieval_eval_queries_v1.jsonl  50 条查询集
    data/staging/trial50_gold_v2.jsonl            Gold v2 终版（49 confirmed + 1 pending）
    业务库 knowledge_points                         KP 名称

输出：
    data/staging/retrieval_eval_queries_review.md  完整核验指南

内容纪律：
- 两条来源**分区展示**：A 区 verified 派生（30 条，沿用 Gold v2 已核验标签，
  待用户抽查）、B 区 手写草稿（20 条，**助手初拟、未转正**，待用户逐条核验）；
- 每条展示 QID / 查询全文 / Gold primary（ID+名称）/ Gold 全量（ID+名称）/
  来源 / 待核验状态 / 裁决栏；
- 附录列出未纳入查询集的 19 条 confirmed 题（仅 q_id + gold 名称，供用户决定
  是否替换），**不自动纳入**；
- 本脚本不修改 jsonl、不改 Gold、不评分。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.build_retrieval_eval_review
"""
import json
import sys
from pathlib import Path

from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "data" / "staging"
QUERY_FILE = STAGING / "retrieval_eval_queries_v1.jsonl"
GOLD_FILE = STAGING / "trial50_gold_v2.jsonl"
OUT_FILE = STAGING / "retrieval_eval_queries_review.md"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main() -> int:
    queries = load_jsonl(QUERY_FILE)
    gold_rows = load_jsonl(GOLD_FILE)
    gold = {g["q_id"]: g for g in gold_rows}
    confirmed = [g for g in gold_rows if g.get("status") == "confirmed"]

    conn = db.connect()
    try:
        kp_name = {r["kp_id"]: r["name"]
                   for r in conn.execute("SELECT kp_id, name FROM knowledge_points")}
    finally:
        conn.close()

    verified = [q for q in queries if q["source"] == "verified_derived"]
    drafts = [q for q in queries if q["source"] == "handwritten_draft"]
    used_ids = {q["query_id"].removeprefix("v_") for q in verified}
    unused = [g for g in confirmed if g["q_id"] not in used_ids]

    def kp_label(kp_id: str) -> str:
        return f"{kp_id}（{kp_name.get(kp_id, '未找到名称')}）"

    lines = [
        "# 检索评测查询集 v1 完整核验指南（Phase 2 人工验收）",
        "",
        "> 本文件由 `backend/scripts/build_retrieval_eval_review.py` 生成（只读数据，不修改查询集与 Gold）。",
        "> **未评分、未计算 Recall/MRR**；所有裁决栏留空，等待用户核验。",
        "",
        "## 0. 构成与口径（如实说明）",
        "",
        f"- 查询集总数：**{len(queries)} 条** = A 区 verified 派生 **{len(verified)}** + B 区 手写草稿 **{len(drafts)}**。",
        f"- verified 题池：Gold v2 终版 **confirmed {len(confirmed)} 条**（另有 1 条 pending_verification=q_ape_1070129，按决策㉚不纳入）。",
        f"- **本查询集取 49 条中的 {len(verified)} 条**（50 条总上限 − 20 条手写配额）；**其余 {len(unused)} 条未纳入**，清单见附录，是否替换由您裁决。",
        "- A 区标签来源 = Gold v2 终版人工核验结果（决策㉙），本次**未改动**，请抽查；B 区为助手初拟查询变体，**未转正**，请逐条核验。",
        "- 未计算任何正式指标（Recall@5 / Recall@1 / MRR / NDCG@5）；待您核验冻结后另行实测。",
        "",
        "## A 区：verified 派生（30 条，沿用 Gold v2 已核验标签，请抽查）",
        "",
    ]

    for i, q in enumerate(verified, 1):
        g = gold.get(q["query_id"].removeprefix("v_"), {})
        gold_all = "；".join(kp_label(k) for k in q["gold_kp_ids"])
        lines += [
            f"### A-{i:02d} `{q['query_id']}`　来源：verified_derived　状态：**待您抽查（标签未改动）**",
            "",
            f"- **查询全文**：{q['query_text']}",
            f"- **Gold primary**：{kp_label(q['gold_primary'])}",
            f"- **Gold 全量**：{gold_all}",
            f"- **标签溯源**：{g.get('verdict_source', '（gold 记录缺省）')}",
            f"- **裁决**：☐ 通过　☐ 修改（请注明）　☐ 删除　｜ 备注：",
            "",
        ]

    lines += [
        "## B 区：手写草稿（20 条，助手初拟，**未转正**，请逐条核验）",
        "",
        "> 每条需核验：①查询表述是否像真实教师问法；②gold 知识点是否正确；③是否需改写或删除。",
        "",
    ]

    for i, q in enumerate(drafts, 1):
        gold_all = "；".join(kp_label(k) for k in q["gold_kp_ids"])
        lines += [
            f"### B-{i:02d} `{q['query_id']}`　来源：handwritten_draft　状态：**待您核验（草稿，未转正）**",
            "",
            f"- **查询文本**：{q['query_text']}",
            f"- **Gold primary**：{kp_label(q['gold_primary'])}",
            f"- **Gold 全量**：{gold_all}",
            f"- **助手备注**：{q.get('note', '')}",
            f"- **裁决**：☐ 通过并转正　☐ 修改（请注明）　☐ 删除　｜ 备注：",
            "",
        ]

    lines += [
        "## 附录：未纳入查询集的 confirmed 题（19 条，供您决定是否替换）",
        "",
        "> 仅列 q_id 与 Gold 名称；题干未纳入本查询集。**未自动加入**，替换需您明确指示。",
        "",
        "| # | q_id | Gold primary | 名称 |",
        "|---|---|---|---|",
    ]
    for i, g in enumerate(unused, 1):
        lines.append(f"| {i} | `{g['q_id']}` | {g['gold_primary']} | "
                     f"{kp_name.get(g['gold_primary'], '未找到名称')} |")

    lines += [
        "",
        "## 核验完成后的下一步（等待用户）",
        "",
        "1. 您在两个分区填写裁决（通过/修改/删除）；",
        "2. 助手按裁决冻结查询集终版（改动逐条留痕）；",
        "3. 冻结后运行 §4.5 指标实测（Recall@5 / Recall@1 / MRR / NDCG@5）并回填 STATUS。",
        "",
    ]

    OUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"核验指南已生成：{OUT_FILE}")
    print(f"A 区 {len(verified)} 条 / B 区 {len(drafts)} 条 / 附录未纳入 {len(unused)} 条")
    print("未评分、未改数据、未计算正式指标。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
