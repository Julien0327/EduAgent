"""S5 真实演示验证（Phase 3）：演示班 class_diagnosis → PDF 实读核验。

- 强制模板兜底（DEEPSEEK_API_KEY 置 None）：**零 LLM 调用、零费用**；
  LLM 叙述路径已在 S4 真实验证过（任务 9213b5b1f55f）。
- 跑真实工作流（真实库 + 真实检索），产物写入 data/outputs/<task_id>/；
- 用 pypdf 实读 PDF 文本，与 04_诊断结果.json 交叉核验（q_id/KP 名/掌握度/叙述来源）；
- 输出 staging 核验报告。

用法（仓库根执行）：
    .venv/Scripts/python.exe -X utf8 -m backend.scripts.run_diagnosis_demo
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_MD = REPO_ROOT / "data" / "staging" / "diagnosis_demo_verification.md"
OUT_JSON = REPO_ROOT / "data" / "staging" / "diagnosis_demo_verification.json"


def main() -> int:
    from pypdf import PdfReader

    from backend.config import settings
    settings.DEEPSEEK_API_KEY = None   # 模板兜底（零费用）；LLM 路径 S4 已真实验证

    from backend.app.agent.context import task_store
    from backend.app.agent.workflow import run_class_diagnosis_task
    from backend.app.storage import db

    conn = db.connect(None)
    try:
        cls = conn.execute(
            "SELECT class_id FROM classes WHERE name='六年级演示班' LIMIT 1").fetchone()
    finally:
        conn.close()
    if cls is None:
        print("演示班不存在")
        return 1

    ctx = run_class_diagnosis_task(cls["class_id"])
    print(f"任务：{ctx.task_id} status={ctx.status}")
    if ctx.status != "completed":
        print(f"失败：{ctx.error}")
        return 1

    arts = {a.file_name: Path(a.path) for a in ctx.artifacts}
    json_path = arts.get("04_诊断结果.json")
    pdf_path = arts.get("05_学情诊断报告.pdf")
    assert json_path and json_path.exists(), "缺 JSON 产物"
    assert pdf_path and pdf_path.exists(), "缺 PDF 产物"

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    diag = payload["diagnosis"]
    narration = payload["narration"]

    text = "\n".join(p.extract_text() or ""
                     for p in PdfReader(str(pdf_path)).pages)
    pages = len(PdfReader(str(pdf_path)).pages)

    checks = {
        "pdf_size_bytes": pdf_path.stat().st_size,
        "pdf_pages": pages,
        "narration_source": narration.get("narration_source"),
        "template_badge_in_pdf": "模板兜底" in text,
        "demo_badge_in_pdf": "合成演示数据" in text,
        "title_in_pdf": "学情诊断报告" in text,
        "class_in_pdf": diag["data_scope"]["class_name"] in text,
        "all_rec_qids_in_pdf": all(
            q["q_id"] in text
            for g in diag["recommendations"] for q in g["questions"]),
        "all_chain_names_in_pdf": all(
            n in text
            for c in diag["chains"] for n in [c["target_name"], c["root_name"]]
            + c["path_names"]),
        "all_chain_mastery_in_pdf": all(
            f"{c['target_mastery']:.4f}" in text for c in diag["chains"]),
        "rec_qid_count": sum(len(g["questions"]) for g in diag["recommendations"]),
        "chain_count": len(diag["chains"]),
        "weak_count": sum(1 for m in diag["mastery"].values() if m["state"] == "weak"),
        "llm_calls_this_run": sum(
            1 for s in ctx.steps for c in (s.llm_calls or []) if c.get("kind") == "llm"),
    }
    ok = all(v for k, v in checks.items()
             if k not in ("pdf_size_bytes", "pdf_pages", "rec_qid_count",
                          "chain_count", "weak_count", "llm_calls_this_run",
                          "narration_source"))
    ok = ok and checks["narration_source"] == "template"
    ok = ok and checks["llm_calls_this_run"] == 0   # 成本控制：本次零 LLM 调用

    # steps Trace 快照（观测页数据源核验）
    trace_rows = [{
        "node": s.node, "status": s.status, "latency_ms": s.latency_ms,
        "llm_model": s.llm_model, "usage_source": s.usage_source,
        "tokens_in": s.tokens_in, "tokens_out": s.tokens_out, "cost": s.cost,
        "tool_calls": [c["tool"] for c in (s.llm_calls or []) if c.get("kind") == "tool"],
    } for s in ctx.steps]

    report = {
        "verified_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task_id": ctx.task_id,
        "pdf_path": str(pdf_path),
        "json_path": str(json_path),
        "checks": checks,
        "trace": trace_rows,
        "ok": ok,
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    lines = [
        "# S5 演示班诊断 PDF 实读核验报告",
        "",
        f"- 时间：{report['verified_at']}｜任务：`{ctx.task_id}`｜结论：**{'通过' if ok else '未通过'}**",
        f"- PDF：`{pdf_path}`（{checks['pdf_size_bytes']} 字节 / {pages} 页，pypdf 实读文本核验）",
        f"- 本次 LLM 调用：**{checks['llm_calls_this_run']} 次**（模板兜底，零费用；LLM 叙述路径见 S4 真实验证）",
        "",
        "## 一致性检查（PDF 文本 vs 诊断 JSON）",
        "",
        "| 检查项 | 结果 |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in checks.items()],
        "",
        "## 任务 Trace（观测页数据源）",
        "",
        "| 节点 | 状态 | 延迟ms | 模型 | usage_source | tokens in/out | 成本 | 工具调用 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for t in trace_rows:
        lines.append(
            f"| {t['node']} | {t['status']} | {t['latency_ms']} | {t['llm_model'] or '—'} | "
            f"{t['usage_source'] or '—'} | {t['tokens_in']}/{t['tokens_out']} | "
            f"{t['cost']} | {', '.join(t['tool_calls']) or '—'} |")
    lines += [
        "",
        "> 推荐题 q_id、归因链 KP 名与掌握度数值、叙述来源徽标、合成数据声明均已在 PDF 文本中逐项核验。",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    print(f"结论：{'通过' if ok else '未通过'}；报告：{OUT_MD}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
