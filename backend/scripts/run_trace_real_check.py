"""S4 真实 Provider 采集验证（Phase 3，一次性脚本，1 次真实 LLM 调用）。

流程：
1. 对真实库应用 V5 迁移（幂等）；
2. 用演示班跑一次 class_diagnosis 工作流（narrate 节点 1 次真实 DeepSeek 调用；
   compute/recommend 节点零 LLM）；
3. 清空内存缓存模拟服务重启 → 从库直读完整 Trace；
4. 手工复算成本（用明细中固化的单价快照 × 真实 tokens）；
5. 校验 Σsteps.cost == Σ明细 cost；SQL 聚合核对；
6. 输出 staging 报告。

成本控制：全程仅 1 次真实 API 调用（narrate）；不重复跑。
用法（仓库根执行，需 DEEPSEEK_API_KEY）：
    .venv/Scripts/python.exe -X utf8 -m backend.scripts.run_trace_real_check
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "db" / "edumate.sqlite"
OUT_MD = REPO_ROOT / "data" / "staging" / "trace_real_check_report.md"
OUT_JSON = REPO_ROOT / "data" / "staging" / "trace_real_check_report.json"


def main() -> int:
    if not os.getenv("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY 未配置——真实 Provider 验证【未完成】，"
              "不得以 Mock 结果冒充（用户 S4 验收要求 5）。")
        return 2

    from backend.app.agent.context import task_store
    from backend.app.agent.workflow import run_class_diagnosis_task
    from backend.app.storage import db

    db.apply_migrations(DB_PATH)
    conn = db.connect(DB_PATH)
    try:
        cls = conn.execute(
            "SELECT class_id FROM classes WHERE name='六年级演示班' LIMIT 1").fetchone()
    finally:
        conn.close()
    if cls is None:
        print("演示班不存在，先完成 S0-3 导入")
        return 1

    ctx = run_class_diagnosis_task(cls["class_id"])
    print(f"任务完成：{ctx.task_id} status={ctx.status}")
    if ctx.status != "completed":
        print(f"任务失败：{ctx.error}")
        return 1

    # ---- 模拟重启：清空内存缓存，从库直读 ----
    task_id = ctx.task_id
    task_store.reset()
    row = task_store.get_from_db(task_id)
    assert row is not None, "重启后无法从库读取任务"
    steps = row["steps"]

    llm_calls = []
    for s in steps:
        for c in (s.get("llm_calls") or []):
            if c.get("kind") == "llm":
                llm_calls.append({"node": s["node"], **c})

    # ---- 手工复算（用明细固化的单价快照 × 真实 tokens）----
    recompute = []
    for c in llm_calls:
        if c.get("tokens_in") is None or not c.get("unit_price"):
            recompute.append({"node": c["node"], "recomputed": None,
                              "recorded": c.get("cost_cny"), "match": None})
            continue
        hit = min(c.get("cache_hit_tokens") or 0, c["tokens_in"])
        miss = c["tokens_in"] - hit
        up = c["unit_price"]
        cost = (hit * up["in_hit"] + miss * up["in_miss"]
                + (c.get("tokens_out") or 0) * up["out"]) / 1e6
        recompute.append({
            "node": c["node"], "recomputed": round(cost, 6),
            "recorded": c.get("cost_cny"),
            "match": abs(cost - (c.get("cost_cny") or 0)) < 1e-6,
        })

    step_cost_sum = round(sum(s["cost"] for s in steps
                              if s.get("cost") is not None), 6)
    detail_cost_sum = round(sum(c["cost_cny"] for c in llm_calls
                                if c.get("cost_cny") is not None), 6)

    # ---- SQL 聚合核对（/api/stats/cost 同口径）----
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        sql_row = dict(conn.execute(
            "SELECT COUNT(*) AS n, SUM(tokens_in) AS tin, SUM(tokens_out) AS tout, "
            "ROUND(SUM(cost), 6) AS cost FROM steps WHERE task_id=?",
            (task_id,)).fetchone())
    finally:
        conn.close()

    ok = (
        len(llm_calls) >= 1
        and all(c["usage_source"] == "provider" for c in llm_calls)
        and all(r["match"] for r in recompute if r["match"] is not None)
        and abs(step_cost_sum - detail_cost_sum) < 1e-6
        and sql_row["cost"] is not None
        and abs((sql_row["cost"] or 0) - detail_cost_sum) < 1e-6
    )

    report = {
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task_id": task_id,
        "workflow": row["workflow"],
        "status": row["status"],
        "real_llm_calls": len(llm_calls),
        "llm_calls": llm_calls,
        "manual_recompute": recompute,
        "sum_steps_cost": step_cost_sum,
        "sum_detail_cost": detail_cost_sum,
        "sql_aggregate": sql_row,
        "restart_replay_ok": True,
        "steps": [{k: s.get(k) for k in
                   ("step_no", "node", "status", "latency_ms", "llm_model",
                    "usage_source", "tokens_in", "tokens_out", "cost")}
                  for s in steps],
        "ok": ok,
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    lines = [
        "# S4 真实 Provider 采集验证报告",
        "",
        f"- 时间：{report['checked_at']}｜任务：`{task_id}`｜工作流：{row['workflow']}",
        f"- 真实 LLM 调用：**{len(llm_calls)} 次**（成本控制：仅 narrate 节点 1 次）",
        f"- 结论：**{'通过' if ok else '未通过'}**",
        "",
        "## 1. 逐次调用明细（供应商真实 usage）",
        "",
        "| 节点 | 模型 | tokens_in | tokens_out | cache_hit | usage_source | 价格版本 | 时段 | 成本(元) | 延迟(ms) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in llm_calls:
        lines.append(
            f"| {c['node']} | {c['model']} | {c['tokens_in']} | {c['tokens_out']} | "
            f"{c.get('cache_hit_tokens')} | {c['usage_source']} | {c['price_version']} | "
            f"{c['price_period']} | {c['cost_cny']} | {c['latency_ms']} |")
    lines += [
        "",
        "## 2. 手工复算（单价快照 × 真实 tokens）",
        "",
        "| 节点 | 复算成本 | 落库成本 | 一致 |",
        "|---|---|---|---|",
    ]
    for r in recompute:
        lines.append(f"| {r['node']} | {r['recomputed']} | {r['recorded']} | "
                     f"{'✅' if r['match'] else ('—' if r['match'] is None else '❌')} |")
    lines += [
        "",
        "## 3. 汇总核对",
        "",
        f"- Σsteps.cost = {step_cost_sum}；Σ明细 cost = {detail_cost_sum}；"
        f"SQL 聚合 = {sql_row}",
        f"- 重启回放（task_store.reset 后库直读）：成功",
        "",
        "## 4. 节点 Trace 概览",
        "",
        "| # | 节点 | 状态 | 延迟(ms) | 模型 | usage_source | tokens_in/out | 成本 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in report["steps"]:
        tin = s["tokens_in"] if s["tokens_in"] is not None else "NULL"
        tout = s["tokens_out"] if s["tokens_out"] is not None else "NULL"
        cost = s["cost"] if s["cost"] is not None else "NULL"
        lines.append(
            f"| {s['step_no']} | {s['node']} | {s['status']} | {s['latency_ms']} | "
            f"{s['llm_model'] or '—'} | {s['usage_source'] or '—'} | {tin}/{tout} | {cost} |")
    lines += [
        "",
        "> usage_source=none 的节点（确定性计算/文档生成）tokens/cost=0 为事实值；",
        "> unavailable 时保留 NULL（本次未出现）。标注批次费用在独立台账，未混入。",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"真实调用 {len(llm_calls)} 次；Σsteps={step_cost_sum} Σ明细={detail_cost_sum} "
          f"SQL={sql_row['cost']}；复算一致={all(r['match'] for r in recompute if r['match'] is not None)}")
    print(f"结论：{'通过' if ok else '未通过'}；报告：{OUT_MD}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
