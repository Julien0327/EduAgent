"""S3-A：人工评分表生成与回读（judge 校准用）。

从一次评测 run 报告（report.json）与校准集清单生成人工评分表：
- Markdown：逐条 prompt + 产物内容摘录 + 五维评分栏 + 备注栏；
- CSV：case_id/workflow/prompt/内容摘录/五个评分列（留空待填）/备注。

回读：`read_human_csv` 把填好的 CSV 转为 {case_id: {dim: score}}。
不调 LLM、不改 golden。
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from backend.app.eval.judge import DIMENSIONS, DIM_LABELS, extract_artifact_text

EXCERPT_CHARS = 600


def _artifact_texts(rec: dict) -> str:
    paths = [a.get("path") for a in (rec.get("artifacts") or []) if a.get("path")]
    return extract_artifact_text(paths, max_chars=EXCERPT_CHARS) if paths else ""


def generate_sheet(report_path: Path, calibration_path: Path,
                   out_md: Path, out_csv: Path) -> dict:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    calib = json.loads(Path(calibration_path).read_text(encoding="utf-8"))
    wanted = set(calib["case_ids"])
    records = {r["case_id"]: r for r in report.get("records", [])}

    rows = []
    for cid in calib["case_ids"]:
        rec = records.get(cid)
        if rec is None:
            continue
        rows.append({"case_id": cid,
                     "workflow": rec.get("workflow", ""),
                     "prompt": "",
                     "excerpt": _artifact_texts(rec),
                     "status": rec.get("task_status", "")})
    # prompt 需要从冻结 golden 读（report records 不含 prompt）
    from backend.app.eval.golden import load_frozen
    cases, _ = load_frozen()
    prompt_of = {c["case_id"]: c["prompt"] for c in cases}
    for r in rows:
        r["prompt"] = prompt_of.get(r["case_id"], "")

    out_md.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# judge 校准：人工评分表（50 条）", "",
             f"> 来源 run：`{Path(report_path).parent.name}`；校准集：`{Path(calibration_path).name}`",
             "> 评分方式：五维各 1–5 分（整数），填入 CSV 的对应列；备注可写理由。",
             "> 提示：产物含「暂不可用」等降级标记时，内容正确性/覆盖完整应 ≤2。", ""]
    for i, r in enumerate(rows, 1):
        lines += [f"## {i}. {r['case_id']}（{r['workflow']}）", "",
                  f"- **prompt**：{r['prompt']}",
                  f"- **任务状态**：{r['status']}",
                  "- **产物摘录**：", "```", r["excerpt"] or "（无产物）", "```",
                  "- **评分**：内容正确性 ___ ｜ 难度适切 ___ ｜ 覆盖完整 ___ ｜ 表述规范 ___ ｜ 引用规范 ___",
                  "- **备注**：", ""]
    out_md.write_text("\n".join(lines), encoding="utf-8")

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    header = ["case_id", "workflow", "prompt"] + list(DIMENSIONS) + ["remarks"]
    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([r["case_id"], r["workflow"], r["prompt"]]
                       + [""] * len(DIMENSIONS) + [""])
    return {"cases": len(rows), "md": str(out_md), "csv": str(out_csv)}


def read_human_csv(csv_path: Path) -> tuple[dict[str, dict], list[str]]:
    """读取人工评分 CSV → ({case_id: {dim: score}}, warnings)。空值跳过。"""
    scores: dict[str, dict] = {}
    warnings: list[str] = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            cid = (row.get("case_id") or "").strip()
            if not cid:
                continue
            dim_scores = {}
            for d in DIMENSIONS:
                raw = (row.get(d) or "").strip()
                if not raw:
                    continue
                try:
                    v = int(float(raw))
                except ValueError:
                    warnings.append(f"{cid}.{d} 非数值：{raw!r}")
                    continue
                if not 1 <= v <= 5:
                    warnings.append(f"{cid}.{d} 越界：{v}")
                    continue
                dim_scores[d] = v
            if dim_scores:
                scores[cid] = dim_scores
    return scores, warnings