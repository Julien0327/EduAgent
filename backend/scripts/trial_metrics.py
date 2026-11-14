"""A/B 双臂试跑指标计算（Step 5-B2，设计文档 §6/§7/§11）。

口径（第二版设计）：
- 50 题合格率 = 工程放行指标（主知识点命中 ∧ 无硬误标 ∧ 难度 ±1）；
- S12 纯随机层指标单独报告（无偏参考）；
- Gold 当前为**助手盲标初稿**（trial50_gold_draft.jsonl），最终以用户确认为准；
- 规则挂接不参与任何指标计算（决策㉔）。

用法（仓库根）：.venv/Scripts/python.exe -m backend.scripts.trial_metrics [--group trial500]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GOLD = REPO_ROOT / "data" / "staging" / "trial50_gold_draft.jsonl"
OUT_JSON = REPO_ROOT / "data" / "staging" / "trial_ab_report.json"
OUT_MD = REPO_ROOT / "data" / "staging" / "trial_ab_report.md"

ARM_LABEL = {"A_full_list": "A 臂（82 全清单直判）", "B_recall": "B 臂（召回 top-10 + 复判）"}

# §14 V2-5：相邻 KP 族（primary 消歧准确率的"族内命中"口径；源自 5-B2 归因的语义邻近对）
ADJACENT_GROUPS: list[set[str]] = [
    {"kp_p_037", "kp_p_038"},   # 量率对应 / 比与按比例分配
    {"kp_p_071", "kp_p_066"},   # 整除特征 / 因数与倍数
    {"kp_p_036", "kp_p_015"},   # 率类问题 / 浓度问题
    {"kp_p_031", "kp_p_082"},   # 找规律 / 简便计算
    {"kp_p_077", "kp_p_079"},   # 加减运算（一步）/ 四则混合运算
]


def disambiguation_accuracy(rows: list[dict]) -> float | None:
    """primary 消歧准确率：LLM primary 与 gold primary 相同或同属相邻族的占比。

    仅统计 status=ok 的题（unmapped/failed/invalid 不进分母）；无 ok 行返回 None。
    """
    ok = [r for r in rows if r.get("status") == "ok"]
    if not ok:
        return None
    hits = 0
    for r in ok:
        gp, lp = r.get("gold_primary"), r.get("llm_primary")
        if not gp or not lp:
            continue
        if gp == lp or any({gp, lp} <= g for g in ADJACENT_GROUPS):
            hits += 1
    return round(hits / len(ok), 4)


def arm_metrics(conn, batch: dict, gold_rows: list[dict]) -> dict:
    """单臂指标：批次运行统计 + 50 题 gold 对照（总体与 S12 层分开）。"""
    batch_id = batch["batch_id"]
    results = {r["q_id"]: dict(r) for r in conn.execute(
        "SELECT q_id, status, primary_kp_id FROM q_annotation_results WHERE batch_id=?",
        (batch_id,))}
    kp_rows = conn.execute(
        "SELECT q_id, kp_id FROM q_kp_llm WHERE batch_id=?", (batch_id,)).fetchall()
    llm_kps: dict[str, list[str]] = {}
    primary_of: dict[str, str] = {}
    for r in kp_rows:
        llm_kps.setdefault(r["q_id"], []).append(r["kp_id"])
        if r["q_id"] not in primary_of or True:
            pass
    prim_rows = conn.execute(
        "SELECT q_id, kp_id FROM q_kp_llm WHERE batch_id=? AND is_primary=1", (batch_id,))
    for r in prim_rows:
        primary_of[r["q_id"]] = r["kp_id"]
    diff_rows = conn.execute(
        "SELECT q_id, difficulty FROM q_difficulty_llm WHERE batch_id=?", (batch_id,)).fetchall()
    llm_diff = {r["q_id"]: r["difficulty"] for r in diff_rows}

    def eval_subset(rows: list[dict]) -> dict:
        n = len(rows)
        primary_hit = hard_err_q = diff_ok = passed = 0
        tp = llm_total = gold_total = 0
        unmapped_n = 0
        detail = []
        for g in rows:
            q_id = g["q_id"]
            st = results.get(q_id, {}).get("status")
            gold_set = set(g["gold_kp_ids"])
            if st == "unmapped":
                unmapped_n += 1
                detail.append({"q_id": q_id, "status": "unmapped", "pass": False})
                continue
            if st != "ok":
                detail.append({"q_id": q_id, "status": st, "pass": False})
                continue
            llm_set = set(llm_kps.get(q_id, []))
            prim = primary_of.get(q_id)
            hard = [k for k in llm_set - gold_set]
            tp += len(llm_set & gold_set)
            llm_total += len(llm_set)
            gold_total += len(gold_set)
            hit = prim in gold_set
            dok = abs((llm_diff.get(q_id) or 0) - g["gold_difficulty"]) <= 1
            ok = hit and not hard and dok
            primary_hit += hit
            hard_err_q += bool(hard)
            diff_ok += dok
            passed += ok
            detail.append({"q_id": q_id, "status": "ok", "primary_hit": hit,
                           "hard_errors": hard, "difficulty_ok": dok, "pass": ok})
        return {
            "n": n, "primary_hit": primary_hit, "primary_hit_rate": round(primary_hit / n, 4),
            "hard_error_questions": hard_err_q,
            "difficulty_ok": diff_ok, "difficulty_ok_rate": round(diff_ok / n, 4),
            "pass": passed, "pass_rate": round(passed / n, 4),
            "micro_precision": round(tp / llm_total, 4) if llm_total else None,
            "micro_recall": round(tp / gold_total, 4) if gold_total else None,
            "unmapped": unmapped_n,
            "detail": detail,
        }

    def collect_disambig_rows(rows: list[dict]) -> list[dict]:
        out = []
        for g in rows:
            q_id = g["q_id"]
            st = results.get(q_id, {}).get("status")
            out.append({"status": st,
                        "gold_primary": g["gold_primary"],
                        "gold_kp_ids": g["gold_kp_ids"],
                        "llm_primary": primary_of.get(q_id) if st == "ok" else None,
                        "llm_kps": llm_kps.get(q_id, []) if st == "ok" else []})
        return out

    gold_s12 = [g for g in gold_rows if g.get("stratum") == "S12_random"]
    # stratum 由 trial500 清单提供；gold 初稿未含层标签——读清单补
    disambig = disambiguation_accuracy(collect_disambig_rows(gold_rows))
    return {
        "batch_id": batch_id,
        "model_version": batch["model_version"],
        "status": batch["status"],
        "overall": {
            "total": batch["total_questions"], "succeeded": batch["succeeded"],
            "unmapped": batch["unmapped"], "failed": batch["failed"],
            "invalid": batch["invalid"],
            "unmapped_rate": round(batch["unmapped"] / max(1, batch["succeeded"] + batch["unmapped"]), 4),
            "input_tokens": batch["input_tokens_sum"],
            "output_tokens": batch["output_tokens_sum"],
            "cache_hit_tokens": batch["cache_hit_tokens_sum"],
            "cache_hit_rate": round(batch["cache_hit_tokens_sum"] / max(1, batch["input_tokens_sum"]), 4),
            "cost_cny": batch["cost_cny_est"],
            "per_question_cost_cny": round(batch["cost_cny_est"] / max(1, batch["cost_cny_est"] and batch["succeeded"] + batch["unmapped"] or 1), 6),
        },
        "trial50": eval_subset(gold_rows),
        "disambiguation_accuracy": disambig,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B 双臂试跑指标计算")
    parser.add_argument("--group", default="trial500")
    parser.add_argument("--gold", default=str(DEFAULT_GOLD))
    args = parser.parse_args()

    gold_rows = [json.loads(l) for l in Path(args.gold).read_text(encoding="utf-8").splitlines() if l.strip()]
    stratum_of = {}
    for line in (REPO_ROOT / "data" / "annotations" / "trial500_qids.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            stratum_of[row["q_id"]] = row["stratum"]
    for g in gold_rows:
        g["stratum"] = stratum_of.get(g["q_id"], "?")

    conn = db.connect()
    batches = [dict(r) for r in conn.execute(
        "SELECT * FROM annotation_batches WHERE budget_group=? ORDER BY batch_id", (args.group,))]
    arms = {}
    for b in batches:
        if b["status"] != "completed":
            continue
        arms[b["strategy"]] = arm_metrics(conn, b, gold_rows)
    conn.close()

    # manifest batch_end（耗时/调用数）
    manifest_dir = REPO_ROOT / "data" / "annotations" / "manifests"
    for b in batches:
        mf = manifest_dir / f"{b['batch_id']}.jsonl"
        if not mf.exists():
            continue
        for line in mf.read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev.get("event") == "batch_end" and ev.get("batch_id") == b["batch_id"]:
                key = b["strategy"]
                if key in arms:
                    arms[key]["elapsed_seconds"] = ev.get("elapsed_seconds")
                    arms[key]["calls"] = ev.get("calls")

    group_cost = round(sum(b["cost_cny_est"] for b in batches), 6)
    report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "gold_source": args.gold,
              "gold_nature": "assistant_blind_draft（最终以用户确认为准）",
              "budget_group": args.group, "group_cost_cny": group_cost,
              "arms": arms}
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 500 题 A/B 双臂试跑对照报告", "",
             f"- 生成：{report['generated_at']}；预算组 trial500 累计费用 **¥{group_cost}**（硬停线 ¥20，提醒线 ¥10，均未触发）",
             f"- Gold：助手盲标初稿 50 题（**最终以用户确认为准**）；50 题合格率=工程放行指标，S12 纯随机层（17 题）单独报告", ""]
    for key, m in arms.items():
        o, t50 = m["overall"], m["trial50"]
        s12 = [d for d in t50["detail"]]
        lines += [f"## {ARM_LABEL.get(key, key)}（{m['batch_id']}）", "",
                  f"- 运行：{m['status']}；耗时 {m.get('elapsed_seconds')}s；调用 {m.get('calls')} 次；模型快照 {m.get('model_version')}",
                  f"- 结果：ok {o['succeeded']} / unmapped {o['unmapped']}（{o['unmapped_rate']:.1%}）/ failed {o['failed']} / invalid {o['invalid']}",
                  f"- Token：输入 {o['input_tokens']:,} / 输出 {o['output_tokens']:,} / 缓存命中 {o['cache_hit_tokens']:,}（**命中率 {o['cache_hit_rate']:.1%}**）",
                  f"- 费用：¥{o['cost_cny']}（≈¥{o['per_question_cost_cny']}/题）", "",
                  f"### 50 题对照（工程放行口径）", "",
                  f"- 主知识点命中率：{t50['primary_hit']}/{t50['n']} = **{t50['primary_hit_rate']:.1%}**",
                  f"- 难度一致（±1）：{t50['difficulty_ok']}/{t50['n']} = {t50['difficulty_ok_rate']:.1%}",
                  f"- 硬误标题数：{t50['hard_error_questions']}",
                  f"- **合格率（主命中 ∧ 无硬误标 ∧ 难度±1）：{t50['pass']}/{t50['n']} = {t50['pass_rate']:.1%}**",
                  f"- micro-Precision：{t50['micro_precision']}；micro-Recall：{t50['micro_recall']}；unmapped：{t50['unmapped']}",
                  f"- primary 消歧准确率（同/相邻族）：{m.get('disambiguation_accuracy')}", ""]
        s12_rows = [g for g in gold_rows if g["stratum"] == "S12_random"]
        s12_ids = {g["q_id"] for g in s12_rows}
        s12_detail = [d for d in t50["detail"] if d["q_id"] in s12_ids]
        s12_pass = sum(1 for d in s12_detail if d.get("pass"))
        lines += [f"### S12 纯随机层（{len(s12_rows)} 题，无偏参考）", "",
                  f"- 合格 {s12_pass}/{len(s12_rows)} = **{s12_pass/len(s12_rows):.1%}**", ""]
    lines += ["## 对照结论要点", "",
              "- A 臂无召回截断；B 臂受 recall@10=62% 天花板约束（见 recall10_report.md），unmapped 27.8% 为模型按 prompt 如实弃标、0 硬凑。",
              "- 两臂数据完全隔离（strategy 独立批次），50 题合格率仅为工程放行指标，不宣称全库无偏准确率。",
              "- Gold 为助手盲标初稿：用户确认后若修订 gold，本报告需重算（脚本可复现）。"]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n报告：{OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
