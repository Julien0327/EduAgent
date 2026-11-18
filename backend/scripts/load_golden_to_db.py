"""Phase 4 / S1：Golden Set 冻结入库（data/gt/golden_set_v1.jsonl → eval_cases）。

用途：把用户裁决后的冻结版 golden 写入真实业务库 eval_cases 表，供 S2 runner 使用。
- 幂等：基于 PK(case_id) 做按 case UPSERT（INSERT ... ON CONFLICT DO UPDATE）；
  重复导入不产生重复记录（case_id 唯一），内容变更时更新行；
- 事务安全：单事务提交；任一 case 失败整体回滚；提交后逐条回读核对；
- 校验：入库前对 expect/tags 做 JSON 序列化合法性检查；
  入库后逐条比对 case_id/prompt/expect 深度相等（JSON 反序列化后比较）；
- 不覆盖原始草稿：冻结 JSON 由 `build_golden_set.py --freeze-out` 生成，导入前不重写。

用法：
    .venv/Scripts/python.exe -m backend.scripts.load_golden_to_db \
        --json data/gt/golden_set_v1.jsonl                 # 默认读该路径
    # 只核对不写库：
    .venv/Scripts/python.exe -m backend.scripts.load_golden_to_db --check-only
"""
import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_JSON = REPO_ROOT / "data" / "gt" / "golden_set_v1.jsonl"

EXPECTED_FIELDS = {"behavior", "entry", "workflow", "params", "artifacts_min"}


def _load_cases(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"冻结 JSON 不存在：{path}")
    cases = []
    for ln, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"JSONL 第 {ln} 行解析失败：{exc}")
        assert "case_id" in c and "prompt" in c and "expect" in c, f"第 {ln} 行缺字段"
        exp = c["expect"]
        missing = EXPECTED_FIELDS - set(exp)
        if missing:
            raise SystemExit(f"{c['case_id']}: expect 缺字段 {missing}")
        cases.append(c)
    return cases


def _notes(case: dict) -> str:
    exp = case.get("expect", {})
    note = exp.get("status")
    if exp.get("mode"):
        note = f"{note} mode={exp['mode']}" if note else f"mode={exp['mode']}"
    return note or ""


def import_cases(cases: list[dict]) -> dict:
    from backend.app.storage import db

    db.apply_migrations(None)  # 幂等：确保 eval_cases 表存在
    conn = db.connect(None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        for c in cases:
            exp_json = json.dumps(c["expect"], ensure_ascii=False, sort_keys=True)
            tags_json = json.dumps(c["tags"], ensure_ascii=False)
            conn.execute(
                "INSERT INTO eval_cases (case_id, prompt, expect, tags) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(case_id) DO UPDATE SET "
                "  prompt=excluded.prompt, expect=excluded.expect, tags=excluded.tags",
                (c["case_id"], c["prompt"], exp_json, tags_json),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"imported": len(cases), "note": _notes(cases[0]) if cases else ""}


def verify_cases(cases: list[dict]) -> tuple[int, list[str]]:
    from backend.app.storage import db

    conn = db.connect(None)
    mismatches: list[str] = []
    try:
        ids = [c["case_id"] for c in cases]
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT case_id, prompt, expect, tags FROM eval_cases "
            f"WHERE case_id IN ({placeholders})", tuple(ids)).fetchall()
        db_map = {r["case_id"]: r for r in rows}
    finally:
        conn.close()
    for c in cases:
        row = db_map.get(c["case_id"])
        if row is None:
            mismatches.append(f"{c['case_id']}: 库中缺失")
            continue
        expect_json = json.dumps(c["expect"], ensure_ascii=False, sort_keys=True)
        tags_json = json.dumps(c["tags"], ensure_ascii=False)
        if row["expect"] != expect_json:
            mismatches.append(f"{c['case_id']}: expect 不一致")
        if row["tags"] != tags_json:
            mismatches.append(f"{c['case_id']}: tags 不一致")
        if row["prompt"] != c["prompt"]:
            mismatches.append(f"{c['case_id']}: prompt 不一致")
    return len(rows), mismatches


def main() -> int:
    ap = argparse.ArgumentParser(description="Golden freeze → eval_cases 入库")
    ap.add_argument("--json", default=str(DEFAULT_JSON), help="冻结 JSONL 路径")
    ap.add_argument("--check-only", action="store_true", help="只核对不写库")
    args = ap.parse_args()

    cases = _load_cases(Path(args.json))
    print(f"[加载] {len(cases)} 条来自 {args.json}")

    import_backend = True
    if args.check_only:
        print("[只读模式] 不写库")
        import_backend = False
    else:
        try:
            result = import_cases(cases)
            print(f"[导入] committed {result['imported']} 条（UPSERT by case_id）")
        except Exception as exc:
            print(f"[导入失败] {exc}")
            return 1

    n, mismatches = verify_cases(cases)
    print(f"[核对] 库中命中 {n}/{len(cases)} 条")
    if mismatches:
        print("\n".join(f"[不一致] {m}" for m in mismatches))
        return 1
    # 重复计数不变量：PK 唯一，导入前后 eval_cases 总数应 == 导入条数（空库起步）或正确累积
    from backend.app.storage import db
    conn = db.connect(None)
    try:
        total = conn.execute("SELECT COUNT(*) FROM eval_cases").fetchone()[0]
    finally:
        conn.close()
    print(f"[总计] eval_cases 现有 {total} 行（幂等：重复导入不产生重复行）")
    print("[核验通过] JSON 与 DB 逐条一致 (case_id/prompt/expect/tags)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())