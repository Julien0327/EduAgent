"""小学前置图入库（Phase 3 / S0-1）。

读取冻结边清单 ``data/knowledge/primary_edges.json``（v1.0，87 条，用户
2026-11-15 裁决），校验后写入 ``kp_edges``。

边界：
- **只管理 primary 边**（``from_kp LIKE 'kp_p_%'``）：先删 primary 边再全量插入；
  不删除初中 KP、不删除初中边、不修改 knowledge_points。
- 幂等：重复运行结果一致；``--dry-run`` 只校验与统计，不写库。
- 初中运行时清理由单独的 ``switch_to_primary_scope`` 脚本负责，且必须先 dry-run
  并经用户批准；本脚本不承担删除职责。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.build_primary_graph --dry-run
    .venv/Scripts/python.exe -m backend.scripts.build_primary_graph
    .venv/Scripts/python.exe -m backend.scripts.build_primary_graph --db <路径>
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
EDGES_FILE = REPO_ROOT / "data" / "knowledge" / "primary_edges.json"
REPORT_PATH = REPO_ROOT / "data" / "staging" / "primary_graph_build_report.json"


class PrimaryGraphError(Exception):
    """小学前置图校验失败（引用断裂 / 自环 / 重复 / 环）。"""


def load_edges(path: Path | str = EDGES_FILE) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    edges = data.get("edges")
    if not isinstance(edges, list) or not edges:
        raise PrimaryGraphError(f"边清单为空或格式非法：{path}")
    return data


def validate_edges(conn, edges: list[dict]) -> list[str]:
    """返回错误列表；空列表 = 通过。"""
    errors: list[str] = []
    primary = {
        r["kp_id"] for r in conn.execute(
            "SELECT kp_id FROM knowledge_points WHERE stage='primary'"
        )
    }
    seen: set[tuple[str, str]] = set()
    for i, e in enumerate(edges, 1):
        src, tgt = e.get("source_id"), e.get("target_id")
        if src not in primary:
            errors.append(f"E_REF：第 {i} 条 source_id 不是小学 KP：{src}")
        if tgt not in primary:
            errors.append(f"E_REF：第 {i} 条 target_id 不是小学 KP：{tgt}")
        if src == tgt:
            errors.append(f"E_SELF：第 {i} 条自环：{src}")
        if (src, tgt) in seen:
            errors.append(f"E_DUP：第 {i} 条重复边：{src} -> {tgt}")
        seen.add((src, tgt))

    # DAG（Kahn）：只在 primary 节点上检查
    adj: dict[str, list[str]] = defaultdict(list)
    indeg: Counter = Counter({kp: 0 for kp in primary})
    for e in edges:
        if e["source_id"] in primary and e["target_id"] in primary:
            adj[e["source_id"]].append(e["target_id"])
            indeg[e["target_id"]] += 1
    q = deque(sorted(kp for kp in primary if indeg[kp] == 0))
    visited = 0
    while q:
        n = q.popleft()
        visited += 1
        for m in adj.get(n, ()):
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    if visited != len(primary):
        cyclic = sorted(kp for kp in primary if indeg[kp] > 0)
        errors.append(f"E_CYCLE：小学前置图存在环（环上节点：{', '.join(cyclic[:20])}）")
    return errors


def graph_stats(conn, edges: list[dict]) -> dict:
    primary = [
        r["kp_id"] for r in conn.execute(
            "SELECT kp_id FROM knowledge_points WHERE stage='primary' ORDER BY kp_id"
        )
    ]
    src = {e["source_id"] for e in edges}
    tgt = {e["target_id"] for e in edges}
    isolated = sorted(kp for kp in primary if kp not in src and kp not in tgt)
    return {
        "primary_kps": len(primary),
        "edges": len(edges),
        "roots": len([kp for kp in primary if kp not in tgt]),
        "leaves": len([kp for kp in primary if kp not in src]),
        "isolated": isolated,
    }


def build(db_path: Path | str | None = None, edges_file: Path | str = EDGES_FILE,
          dry_run: bool = False) -> dict:
    payload = load_edges(edges_file)
    edges = payload["edges"]
    db.apply_migrations(db_path)
    conn = db.connect(db_path)
    try:
        errors = validate_edges(conn, edges)
        if errors:
            raise PrimaryGraphError(
                f"小学前置图校验未通过（{len(errors)} 项）：\n" + "\n".join(errors)
            )
        existing = conn.execute(
            "SELECT COUNT(*) AS n FROM kp_edges"
            " WHERE from_kp LIKE 'kp_p_%' OR to_kp LIKE 'kp_p_%'"
        ).fetchone()["n"]
        stats = graph_stats(conn, edges)
        summary = {
            "db_path": str(db_path),
            "dry_run": dry_run,
            "edges_file": str(edges_file),
            "version": payload.get("version"),
            "status": payload.get("status"),
            "existing_primary_edges_before": existing,
            **stats,
        }
        if dry_run:
            summary["written"] = 0
            return summary
        with conn:
            conn.execute(
                "DELETE FROM kp_edges"
                " WHERE from_kp LIKE 'kp_p_%' OR to_kp LIKE 'kp_p_%'"
            )
            conn.executemany(
                "INSERT INTO kp_edges (from_kp, to_kp, relation) VALUES (?, ?, 'prerequisite')",
                [(e["source_id"], e["target_id"]) for e in edges],
            )
        summary["written"] = len(edges)
        return summary
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="小学前置图入库（Phase 3 / S0-1）")
    parser.add_argument("--db", default=None, help="业务库路径（默认 data/db/edumate.sqlite）")
    parser.add_argument("--edges", default=str(EDGES_FILE), help="冻结边清单 JSON")
    parser.add_argument("--dry-run", action="store_true", help="只校验与统计，不写库")
    parser.add_argument("--report", default=str(REPORT_PATH), help="构建报告 JSON 输出路径")
    args = parser.parse_args()

    try:
        summary = build(args.db, args.edges, args.dry_run)
    except PrimaryGraphError as exc:
        print(f"[小学前置图构建失败] {exc}", file=sys.stderr)
        return 1

    payload = json.loads(Path(args.edges).read_text(encoding="utf-8"))
    summary["edge_categories"] = dict(Counter(e["category"] for e in payload["edges"]))
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    mode = "DRY-RUN" if args.dry_run else "写入"
    print(
        f"[{mode}] 小学 KP {summary['primary_kps']} / 边 {summary['edges']} / "
        f"根 {summary['roots']} / 叶 {summary['leaves']} / 孤立 {len(summary['isolated'])}"
    )
    print(f"原有小学边 {summary['existing_primary_edges_before']} 条；"
          f"本次写入 {summary['written']} 条；DAG 校验通过")
    print(f"报告：{report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
