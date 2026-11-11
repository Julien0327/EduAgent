"""知识图谱构建脚本（Phase 1 / Step 2）。

读取 ``data/knowledge/kp_junior_source/*.json``（册级源数据）合并后入库：
- upsert knowledge_points / kp_edges（幂等，可重跑不重复插入）
- fail-fast 校验（用户 2026-11-11 确认的规则）：
  1. 引用完整：前置依赖指向的 kp_id 必须存在于图谱
  2. 根节点元数据：无前置边的节点必须有章节归属与描述
  3. DAG 无环：前置关系满足有向无环图约束（Kahn 拓扑排序检测）
  4. 主干链可达：数→式→方程→函数主线、几何推理链锚点对必须存在直接前置边
  5. 意外孤岛：非根节点必须至少有一条前置边或后继边

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.build_graph [--db <路径>]
"""
import argparse
import json
import sys
from pathlib import Path

from backend.app.storage import db

# 仓库根目录：backend/scripts/build_graph.py -> 上溯 2 级到 backend -> 再上一级到仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = REPO_ROOT / "data" / "knowledge" / "kp_junior_source"

# 主干链锚点对（from, to）：相邻锚点之间必须存在直接前置边。
# 数→式→方程→函数主线（课标 2022 数与代数主线）+ 几何推理链（图形与几何主线）。
TRUNK_ANCHOR_EDGES = (
    ("kp_j7_0101", "kp_j7_0203"),  # 有理数 → 整式（数到式的过渡）
    ("kp_j7_0203", "kp_j7_0303"),  # 整式 → 一元一次方程
    ("kp_j7_0303", "kp_j7_0801"),  # 一元一次方程 → 二元一次方程组
    ("kp_j7_0303", "kp_j8_1901"),  # 方程 → 函数（一次函数的变量关系基础）
    ("kp_j8_1901", "kp_j9_2201"),  # 一次函数 → 二次函数
    ("kp_j8_1901", "kp_j9_2601"),  # 函数概念 → 反比例函数（与二次函数并列，不互为前置）
    ("kp_j7_0401", "kp_j7_0501"),  # 几何图形初步 → 相交线与平行线
    ("kp_j7_0501", "kp_j8_1101"),  # 平行线 → 三角形
    ("kp_j8_1101", "kp_j8_1201"),  # 三角形 → 全等三角形
    ("kp_j8_1201", "kp_j8_1801"),  # 全等 → 平行四边形（判定复用全等）
    ("kp_j8_1801", "kp_j9_2701"),  # 平行四边形 → 相似（推理链延伸）
    ("kp_j8_1101", "kp_j9_2401"),  # 三角形 → 圆（圆周角/内接等关联）
)


class GraphValidationError(Exception):
    """图谱校验失败（引用断裂 / 环 / 孤岛 / 主干断链 / 根节点元数据缺失）。"""


def _reachable(src: str, dst: str, adj: dict[str, list[str]]) -> bool:
    """BFS 判断 src 沿前置边方向能否到达 dst。"""
    if src == dst:
        return True
    visited = {src}
    stack = [src]
    while stack:
        cur = stack.pop()
        for nxt in adj.get(cur, ()):
            if nxt == dst:
                return True
            if nxt not in visited:
                visited.add(nxt)
                stack.append(nxt)
    return False


def load_source_files(source_dir: Path) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    """读取目录下全部册级源 JSON，合并为 {kp_id: 节点} 与前置边列表。

    同名文件按文件名排序依次合并；kp_id 重复视为源数据错误（fail-fast）。
    """
    nodes: dict[str, dict] = {}
    edges: list[tuple[str, str]] = []
    files = sorted(Path(source_dir).glob("*.json"))
    if not files:
        raise GraphValidationError(f"图谱源数据目录为空：{source_dir}")
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        for kp in data["knowledge_points"]:
            kp_id = kp["kp_id"]
            if kp_id in nodes:
                raise GraphValidationError(f"kp_id 重复：{kp_id}（{path.name}）")
            nodes[kp_id] = kp
            for pre in kp.get("prerequisites", []):
                edges.append((pre, kp_id))
    return nodes, edges


def validate_graph(nodes: dict[str, dict], edges: list[tuple[str, str]]) -> list[str]:
    """按用户确认的规则校验图谱，返回错误列表（空列表 = 通过）。"""
    errors: list[str] = []
    targets = {to for _, to in edges}

    # 1. 引用完整：前置依赖必须指向存在的 kp_id
    for src, dst in edges:
        if src not in nodes:
            errors.append(f"E_REF：{dst} 的前置依赖 {src} 不存在于图谱")

    # 2. 根节点元数据：无前置边的节点必须有章节归属与描述
    for kp_id, kp in nodes.items():
        if kp_id not in targets:
            if not kp.get("chapter"):
                errors.append(f"E_META：根节点 {kp_id} 缺少章节归属 chapter")
            if not kp.get("description"):
                errors.append(f"E_META：根节点 {kp_id} 缺少描述 description")

    # 3. DAG 无环（Kahn 拓扑排序）
    indegree = {kp_id: 0 for kp_id in nodes}
    adj: dict[str, list[str]] = {kp_id: [] for kp_id in nodes}
    for src, dst in edges:
        if src in nodes and dst in nodes:
            indegree[dst] += 1
            adj[src].append(dst)
    queue = [k for k, d in indegree.items() if d == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for nxt in adj[cur]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if seen != len(nodes):
        cyclic = sorted(k for k, d in indegree.items() if d > 0)
        errors.append(f"E_CYCLE：前置关系存在环，环上节点：{', '.join(cyclic)}")

    # 4. 主干链可达：锚点对之间必须存在由前置边构成的路径
    for src, dst in TRUNK_ANCHOR_EDGES:
        if src not in nodes or dst not in nodes:
            errors.append(f"E_TRUNK：主干链锚点缺失：{src} 或 {dst} 不存在于图谱")
        elif not _reachable(src, dst, adj):
            errors.append(f"E_TRUNK：主干链断链：{src} 无法到达 {dst}")

    # 5. 意外孤岛：非根节点必须至少有一条前置边或后继边
    sources = {src for src, _ in edges}
    for kp_id in nodes:
        if kp_id not in targets and kp_id not in sources:
            errors.append(f"E_ISLAND：非根孤岛节点 {kp_id}（无任何前置/后继边）")

    return errors


def build(db_path: Path | str | None = None, source_dir: Path = SOURCE_DIR) -> dict:
    """校验通过后 upsert 入库，返回统计摘要。幂等：节点 INSERT OR REPLACE，边全量重建。"""
    nodes, edges = load_source_files(source_dir)
    errors = validate_graph(nodes, edges)
    if errors:
        raise GraphValidationError(f"图谱校验未通过（{len(errors)} 项）：\n" + "\n".join(errors))

    db.apply_migrations(db_path)
    conn = db.connect(db_path)
    try:
        for kp_id, kp in nodes.items():
            conn.execute(
                "INSERT OR REPLACE INTO knowledge_points"
                " (kp_id, name, subject, stage, grade, chapter, difficulty, description, source)"
                " VALUES (?, ?, 'math', ?, ?, ?, ?, ?, ?)",
                (
                    kp_id,
                    kp["name"],
                    kp["stage"],
                    kp.get("grade"),
                    kp.get("chapter"),
                    kp.get("difficulty", 2),
                    kp.get("description"),
                    kp.get("source", "curriculum"),
                ),
            )
        conn.execute("DELETE FROM kp_edges")
        for src, dst in edges:
            conn.execute(
                "INSERT INTO kp_edges (from_kp, to_kp, relation) VALUES (?, ?, 'prerequisite')",
                (src, dst),
            )
        conn.commit()
        summary = {
            "knowledge_points": len(nodes),
            "edges": len(edges),
            "roots": sum(1 for n in nodes if n not in {to for _, to in edges}),
        }
        log_summary = (
            f"图谱入库完成：{summary['knowledge_points']} 个知识点 / "
            f"{summary['edges']} 条前置边 / {summary['roots']} 个根节点"
        )
        print(log_summary)
        return summary
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="构建知识图谱（Phase 1 / Step 2）")
    parser.add_argument("--db", default=None, help="业务库路径（默认 data/db/edumate.sqlite）")
    parser.add_argument("--source-dir", default=str(SOURCE_DIR), help="图谱源数据目录")
    args = parser.parse_args()
    try:
        build(args.db, Path(args.source_dir))
        return 0
    except GraphValidationError as exc:
        print(f"[图谱构建失败] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
