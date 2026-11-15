"""Phase 2.2 正式评测集（小学单学段）构建。

背景（决策㉝）：运行时收窄为小学单学段；Phase 2 正式集 50 条中的唯一初中
查询（``h_kp_j8_1408``）不再适用。本脚本从**只读**的原正式集派生 49 条小学
查询，原 50 条文件保持不变。

纪律：
- 不修改原 50 条文件、不修改任何 Gold 标签；
- 只保留 ``stage == "primary"`` 且 ``gold_primary`` 为 ``kp_p_*`` 的行；
- 移除项必须恰好是 ``h_kp_j8_1408``（数量与 ID 双重断言）；
- 输出新文件 + 元数据（数量、移除项、sha256），供 Phase 2.2 重跑使用。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.build_phase2_2_eval_set
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "data" / "staging"
SOURCE = STAGING / "retrieval_eval_queries_formal_v1.jsonl"
OUT = STAGING / "retrieval_eval_queries_formal_primary_v2.jsonl"
META = STAGING / "retrieval_eval_queries_formal_primary_v2_meta.json"

REMOVED_QUERY_ID = "h_kp_j8_1408"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main() -> int:
    rows = load_jsonl(SOURCE)
    if len(rows) != 50:
        print(f"源正式集应为 50 条，实际 {len(rows)} 条", file=sys.stderr)
        return 1

    removed = [r for r in rows if r["stage"] != "primary"]
    if len(removed) != 1 or removed[0]["query_id"] != REMOVED_QUERY_ID:
        print(f"移除项不符合预期：{[(r['query_id'], r['stage']) for r in removed]}",
              file=sys.stderr)
        return 1

    kept = [r for r in rows if r["stage"] == "primary"]
    if len(kept) != 49:
        print(f"小学查询应为 49 条，实际 {len(kept)} 条", file=sys.stderr)
        return 1
    if not all(r["gold_primary"].startswith("kp_p_") for r in kept):
        print("存在非小学 Gold primary，拒绝继续", file=sys.stderr)
        return 1
    if not all(r.get("status") == "confirmed" and r.get("usage") == "formal" for r in kept):
        print("存在未确认或非正式用途行，拒绝继续", file=sys.stderr)
        return 1

    out_rows = [
        {
            **r,
            "eval_version": "primary_v2",
            "source_eval_version": "formal_v1",
            "scope": "primary_only",
            "removal_note": "初中查询已按决策㉝移出正式指标；本行不涉及",
        }
        for r in kept
    ]
    OUT.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out_rows) + "\n",
        encoding="utf-8",
    )
    meta = {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decision": "㉝",
        "source_file": str(SOURCE),
        "source_sha256": sha256(SOURCE),
        "output_file": str(OUT),
        "output_sha256": sha256(OUT),
        "source_count": len(rows),
        "output_count": len(out_rows),
        "removed": [{"query_id": removed[0]["query_id"], "stage": removed[0]["stage"],
                     "reason": "运行时收窄为小学单学段（决策㉝）"}],
        "gold_modified": False,
    }
    META.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成：{OUT}（{len(out_rows)} 条，原 50 → 49；移除 {REMOVED_QUERY_ID}）")
    print(f"元数据：{META}")
    print(f"输出 sha256：{meta['output_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
