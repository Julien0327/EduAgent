"""Golden Set 加载（S2-A：从冻结版 JSON/eval_cases 读取，供 runner 使用）。

frozen JSON 为权威来源（data/gt/golden_set_v1.jsonl，Git）；eval_cases 为运行库镜像。
读取时计算 frozen JSON 全文件 SHA256（写进每条 CaseRecord.golden_sha256）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FROZEN = REPO_ROOT / "data" / "gt" / "golden_set_v1.jsonl"


class GoldenError(Exception):
    pass


def _json_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_frozen(path: Path | None = None) -> tuple[list[dict], str]:
    """读取冻结 JSONL，返回 (cases, full_sha256)。逐条结构校验。"""
    path = Path(path) if path else DEFAULT_FROZEN
    if not path.exists():
        raise GoldenError(f"冻结 JSON 不存在：{path}")
    sha = _json_sha256(path)
    cases = []
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldenError(f"第 {ln} 行 JSON 非法：{exc}")
        if "case_id" not in c or "expect" not in c:
            raise GoldenError(f"第 {ln} 行缺关键字段")
        cases.append(c)
    return cases, sha


def load_from_db(db_path: Path | None = None) -> list[dict]:
    """从 eval_cases 表读取（镜像）；幂等校验版一致性由调用方负责。"""
    from backend.app.storage import db as _db
    conn = _db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT case_id, prompt, expect, tags FROM eval_cases ORDER BY case_id"
        ).fetchall()
    finally:
        conn.close()
    cases = []
    for r in rows:
        try:
            exp = json.loads(r["expect"])
        except json.JSONDecodeError:
            continue
        cases.append({
            "case_id": r["case_id"], "prompt": r["prompt"],
            "expect": exp,
            "tags": json.loads(r["tags"]) if r["tags"] else [],
        })
    return cases


if __name__ == "__main__":
    cases, sha = load_frozen()
    print(f"frozen {len(cases)} 条；sha256={sha[:16]}…")