"""S3-A：从冻结 Golden 选取 judge 校准集（50 条）。

组成（确定性，不含随机）：
- exam_loop 20（含 gc_e03 真实诊断降级样本——检验 judge 识别差内容能力）
- question_gen 16
- lesson_plan 10
- class_diagnosis 4
合计 50 条；全部为**有产物**的 case（judge 五维评分针对制品内容）。

输出：`data/staging/calibration_set_v1.json`（提案，用户确认后冻结）。
不改冻结 Golden、不调 LLM。

用法：
    .venv/Scripts/python.exe -m backend.scripts.select_calibration_set [--out <路径>]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FROZEN = REPO_ROOT / "data" / "gt" / "golden_set_v1.jsonl"
DEFAULT_OUT = REPO_ROOT / "data" / "staging" / "calibration_set_v1.json"

QUOTA = {"exam_loop": 20, "question_gen": 16, "lesson_plan": 10, "class_diagnosis": 4}
MUST_INCLUDE = {"gc_e03"}   # 真实诊断降级样本（决策㊶）


def _even_pick(items: list[str], n: int) -> list[str]:
    """确定性均匀抽样（按下标等距）。"""
    if n >= len(items):
        return list(items)
    step = len(items) / n
    picked = [items[int(i * step)] for i in range(n)]
    return picked


def main() -> int:
    ap = argparse.ArgumentParser(description="选取 judge 校准集（50 条）")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    cases = [json.loads(l) for l in FROZEN.read_text(encoding="utf-8").splitlines() if l.strip()]
    from backend.app.eval.golden import load_frozen
    _, sha = load_frozen(FROZEN)

    by_wf: dict[str, list[str]] = {wf: [] for wf in QUOTA}
    wf_of = {}
    for c in cases:
        wf = c["expect"].get("workflow")
        behavior = c["expect"].get("behavior")
        # 只选有产物的 execute/fallback（judge 五维针对制品内容；排除 fail/clarify/reject）
        if wf in QUOTA and behavior in ("execute", "fallback"):
            by_wf[wf].append(c["case_id"])
            wf_of[c["case_id"]] = wf

    selected: list[str] = []
    for wf, quota in QUOTA.items():
        ids = sorted(by_wf[wf])
        picked = _even_pick(ids, quota)
        # 必选样本兜底（如 gc_e03）
        for must in MUST_INCLUDE:
            if wf_of.get(must) == wf and must not in picked:
                picked[-1] = must
        selected += picked

    composition = {wf: sum(1 for cid in selected if wf_of.get(cid) == wf) for wf in QUOTA}
    payload = {
        "version": "calibration_set_v1",
        "frozen_sha256": sha,
        "size": len(selected),
        "case_ids": selected,
        "composition": composition,
        "must_include": sorted(MUST_INCLUDE),
        "status": "proposal（待用户确认后冻结）",
        "note": "全部为有产物 case（judge 五维针对制品）；含 gc_e03 真实诊断降级样本",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"校准集已生成：{out}（{len(selected)} 条）")
    print("构成：", composition)
    print("含 gc_e03:", "gc_e03" in selected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())