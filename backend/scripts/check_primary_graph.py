"""小学前置图草案校验与报告生成（Phase 3 / S0-1 草案工具，不写业务库）。

输入：``data/staging/primary_graph_draft.json``（草案边清单）
输出：``data/staging/primary_graph_review.md``（完整边清单 + 检查报告）

校验项：
1. 字段完整 / 引用完整（kp_id 必须存在于 knowledge_points 且 stage=primary）
2. 无自环、无重复边、无同 ID
3. DAG 无环（Kahn 拓扑排序；失败时用 DFS 定位环路径）
4. 统计：边数、类别分布、根/叶/孤立节点、多前置节点、最长链
5. 推荐补充边单独校验，并给出“纳入后”的孤立节点变化

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.check_primary_graph
    .venv/Scripts/python.exe -m backend.scripts.check_primary_graph --draft <json> --out <md>
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DRAFT = REPO_ROOT / "data" / "staging" / "primary_graph_draft.json"
DEFAULT_OUT = REPO_ROOT / "data" / "staging" / "primary_graph_review.md"
DEFAULT_DB = REPO_ROOT / "data" / "db" / "edumate.sqlite"

CATEGORY_ORDER = [
    "数与运算",
    "数量关系与百分率",
    "典型应用题",
    "行程",
    "单位换算",
    "数论",
    "几何",
    "统计与概率",
]


def load_primary_kps(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT kp_id, name FROM knowledge_points WHERE stage='primary' ORDER BY kp_id"
        ).fetchall()
    finally:
        conn.close()
    return {r["kp_id"]: r["name"] for r in rows}


def validate_edges(edges: list[dict], kps: dict[str, str], label: str) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    required = ("source_id", "target_id", "category", "note", "basis")
    for i, e in enumerate(edges, 1):
        for f in required:
            if not str(e.get(f, "")).strip():
                errors.append(f"{label}[{i}] 缺少字段 {f}")
        src, tgt = e.get("source_id"), e.get("target_id")
        if src not in kps:
            errors.append(f"{label}[{i}] source_id 不存在或非小学 KP：{src}")
        if tgt not in kps:
            errors.append(f"{label}[{i}] target_id 不存在或非小学 KP：{tgt}")
        if src == tgt:
            errors.append(f"{label}[{i}] 自环：{src}")
        key = (src, tgt)
        if key in seen:
            errors.append(f"{label}[{i}] 重复边：{src} -> {tgt}")
        seen.add(key)
    return errors


def build_graph(edges: list[dict], nodes: set[str]):
    adj: dict[str, list[str]] = defaultdict(list)
    indeg = Counter({n: 0 for n in nodes})
    for e in edges:
        src, tgt = e["source_id"], e["target_id"]
        adj[src].append(tgt)
        indeg[tgt] += 1
    return adj, indeg


def topo_sort(nodes: set[str], adj: dict[str, list[str]], indeg: Counter):
    indeg = Counter(indeg)
    q = deque(sorted(n for n in nodes if indeg[n] == 0))
    order: list[str] = []
    while q:
        n = q.popleft()
        order.append(n)
        for m in sorted(adj.get(n, ())):
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    return order


def find_cycle(nodes: set[str], adj: dict[str, list[str]]) -> list[str] | None:
    color: dict[str, int] = {n: 0 for n in nodes}  # 0=未访问 1=栈中 2=完成
    stack: list[str] = []

    def dfs(n: str) -> list[str] | None:
        color[n] = 1
        stack.append(n)
        for m in adj.get(n, ()):
            if color[m] == 1:
                i = stack.index(m)
                return stack[i:] + [m]
            if color[m] == 0:
                found = dfs(m)
                if found:
                    return found
        stack.pop()
        color[n] = 2
        return None

    for n in sorted(nodes):
        if color[n] == 0:
            found = dfs(n)
            if found:
                return found
    return None


def all_longest_chains(nodes: set[str], adj: dict[str, list[str]],
                       order: list[str]) -> tuple[dict[str, int], dict[str, list[str]]]:
    """返回每个节点作为终点的最长链（深度 + 路径）。"""
    depth: dict[str, int] = {n: 0 for n in nodes}
    parent: dict[str, str | None] = {n: None for n in nodes}
    for n in order:
        for m in adj.get(n, ()):
            if depth[n] + 1 > depth[m]:
                depth[m] = depth[n] + 1
                parent[m] = n
    chains: dict[str, list[str]] = {}
    for end in nodes:
        if depth[end] == 0:
            continue
        path: list[str] = []
        cur: str | None = end
        while cur is not None:
            path.append(cur)
            cur = parent[cur]
        path.reverse()
        chains[end] = path
    return depth, chains


def top_chains(chains: dict[str, list[str]], depth: dict[str, int],
               top: int = 5) -> list[tuple[int, list[str]]]:
    """取最长的若干条链，跳过已选链的尾段，避免重复。"""
    out: list[tuple[int, list[str]]] = []
    for end in sorted(chains, key=lambda n: (-depth[n], n)):
        path = chains[end]
        if any(path == c[1][-len(path):] for c in out):
            continue
        out.append((depth[end], path))
        if len(out) >= top:
            break
    return out


def render_report(draft: dict, kps: dict[str, str], errors: list[str],
                  additions: list[dict], omission_errors: list[str]) -> str:
    edges = draft["edges"]
    nodes = set(kps)
    adj, indeg = build_graph(edges, nodes)
    order = topo_sort(nodes, adj, indeg)
    acyclic = len(order) == len(nodes)
    cycle = None if acyclic else find_cycle(nodes, adj)
    isolated = sorted(n for n in nodes if indeg[n] == 0 and not adj.get(n))
    roots = sorted(n for n in nodes if indeg[n] == 0)
    leaves = sorted(n for n in nodes if not adj.get(n))
    multi_in = sorted((n, indeg[n]) for n in nodes if indeg[n] > 1)
    cat_counts = Counter(e["category"] for e in edges)
    depth, chains_by_end = all_longest_chains(nodes, adj, order) if acyclic else ({}, {})
    chains = top_chains(chains_by_end, depth) if acyclic else []
    max_depth = max(depth.values(), default=0)
    edge_cat = {(e["source_id"], e["target_id"]): e["category"] for e in edges}
    cat_chains: dict[str, list[str]] = {}
    for end, path in chains_by_end.items():
        if len(path) < 2:
            continue
        cat = edge_cat.get((path[-2], path[-1]))
        if cat and (cat not in cat_chains or depth[end] > depth[cat_chains[cat][-1]]):
            cat_chains[cat] = path

    # 推荐补充边纳入后的图
    add_ok = not omission_errors and not validate_edges(additions, kps, "推荐补充")
    full_edges = edges + additions
    fadj, findeg = build_graph(full_edges, nodes)
    f_isolated = sorted(n for n in nodes if findeg[n] == 0 and not fadj.get(n))

    def name(kp_id: str) -> str:
        return f"{kp_id} {kps.get(kp_id, '?')}"

    lines: list[str] = []
    add = lines.append
    frozen = draft.get("status") == "frozen"
    status_label = "已冻结" if frozen else "草案"
    add("# 小学前置图检查报告")
    add("")
    add(f"- 版本：`{draft.get('version', '?')}`　状态：**{status_label}**　生成日期：{draft.get('created_at', '?')}")
    add(f"- 范围：仅小学（primary）82 个 KP；初中图谱**不在本图内**，将按决策归档")
    add(f"- 边数：**{len(edges)}** 条（初步目标 60–80；实际以教学依赖为准）")
    add(f"- 推荐补充边：{len(additions)} 条" + ("（已纳入正式边清单）" if frozen and not additions else "（待裁决）"))
    if frozen:
        add(f"- 冻结依据：{draft.get('notes', '用户批准')}")
    add("- 状态说明：本文件由脚本从边清单 JSON + 业务库 KP 名称生成；未修改数据库、未调用 LLM、未产生费用。")
    add("")
    add("## 1. 概览")
    add("")
    add("| 项 | 结果 |")
    add("|---|---|")
    add(f"| 小学 KP 总数 | {len(nodes)} |")
    add(f"| 边数 | {len(edges)} |")
    add(f"| 推荐补充边 | {len(additions)} |")
    add(f"| 类别数 | {len(cat_counts)} |")
    add(f"| 根节点（无前置） | {len(roots)} |")
    add(f"| 叶节点（无后继） | {len(leaves)} |")
    if frozen and not additions:
        add(f"| 孤立节点 | {len(isolated)} |")
    else:
        add(f"| 孤立节点（当前清单） | {len(isolated)} |")
        add(f"| 孤立节点（纳入推荐补充后） | {len(f_isolated)} |")
    add(f"| 最长链深度 | {max_depth} 条边 |")
    add(f"| DAG 无环 | {'✅ 通过' if acyclic else '❌ 失败'} |")
    add("")
    add("### 类别分布")
    add("")
    add("| 类别 | 边数 |")
    add("|---|---|")
    for cat in CATEGORY_ORDER:
        add(f"| {cat} | {cat_counts.get(cat, 0)} |")
    add("")
    add("## 2. 校验结果")
    add("")
    add("| 检查项 | 结果 |")
    add("|---|---|")
    add(f"| 字段完整 / 引用完整 / 无自环 / 无重复 | {'✅ 通过' if not errors else '❌ ' + str(len(errors)) + ' 项错误'} |")
    add(f"| DAG 无环（Kahn） | {'✅ 通过' if acyclic else '❌ 检测到环：' + ' → '.join(cycle or [])} |")
    add(f"| 推荐补充边校验 | {'✅ 通过' if add_ok else '❌ ' + str(len(omission_errors)) + ' 项错误'} |")
    add(f"| 全部 ID 属于小学 KP | {'✅ 通过' if not errors else '见错误清单'} |")
    add("")
    if errors:
        add("### 错误清单")
        add("")
        for e in errors:
            add(f"- {e}")
        add("")
    if omission_errors:
        add("### 推荐补充边错误")
        add("")
        for e in omission_errors:
            add(f"- {e}")
        add("")

    add("## 3. 完整边清单")
    add("")
    add("> 方向约定：`source_id` = 先学的前置知识点，`target_id` = 后学的后继知识点。")
    add("")
    idx = 0
    for cat in CATEGORY_ORDER:
        cat_edges = [e for e in edges if e["category"] == cat]
        if not cat_edges:
            continue
        add(f"### {cat}（{len(cat_edges)} 条）")
        add("")
        add("| # | 前置 source | 后继 target | 关系说明 | 判断依据 |")
        add("|---|---|---|---|---|")
        for e in cat_edges:
            idx += 1
            add(f"| {idx} | {name(e['source_id'])} | {name(e['target_id'])} | {e['note']} | {e['basis']} |")
        add("")

    add("## 4. 拓扑结构")
    add("")
    add("### 4.1 根节点（无前置，含孤立）")
    add("")
    add("、".join(name(n) for n in roots) or "无")
    add("")
    add("### 4.2 孤立节点（无前置且无后继）")
    add("")
    if not isolated:
        add("无")
    else:
        add("| KP | 说明 | 建议 |")
        add("|---|---|---|")
        notes = {
            "kp_p_012": ("火车过桥：行程子类，车长不可忽略", "建议纳入 010→012（推荐补充）"),
            "kp_p_013": ("环形跑道：行程子类，封闭路径", "建议纳入 010→013（推荐补充）"),
            "kp_p_024": ("时钟问题：时刻、角度与分数", "建议纳入 063→024（推荐补充）"),
            "kp_p_026": ("抽屉原理：组合推理原则", "保留独立（无明确算术前置）"),
            "kp_p_027": ("容斥问题：集合计数策略", "保留独立；可选 077→027"),
            "kp_p_029": ("方阵问题：平方数计数", "建议纳入 080→029（推荐补充）"),
            "kp_p_030": ("排队问题：序数/位置计数", "保留独立；可选 077→030"),
            "kp_p_031": ("找规律：跨题型规律观察", "保留独立（难设单一前置）"),
            "kp_p_033": ("最大最小问题：跨题型最值策略", "保留独立（难设单一前置）"),
            "kp_p_039": ("连续数问题：求和与平均数", "建议纳入 077→039、020→039（推荐补充）"),
            "kp_p_051": ("环形面积：圆面积的扩展", "建议纳入 050→051（推荐补充）"),
        }
        for n in isolated:
            note, suggestion = notes.get(n, ("独立模块", "待审核"))
            add(f"| {name(n)} | {note} | {suggestion} |")
        add("")
    add("### 4.3 多前置节点（前置 > 1）")
    add("")
    if not multi_in:
        add("无")
    else:
        add("| KP | 前置数 | 前置列表 |")
        add("|---|---|---|")
        for n, cnt in multi_in:
            pres = sorted(s for s, t in [(e["source_id"], e["target_id"]) for e in edges] if t == n)
            add(f"| {name(n)} | {cnt} | {'、'.join(name(p) for p in pres)} |")
        add("")

    add(f"### 4.4 最长归因链（Top {len(chains)}）")
    add("")
    add("> Top 5 为全局最长链；各主题代表链见 4.5（按链路最后一条边的类别归类）。")
    add("")
    if not chains:
        add("无")
    else:
        add("| # | 长度 | 链路 |")
        add("|---|---|---|")
        for i, (d, path) in enumerate(chains, 1):
            add(f"| {i} | {d} | {' → '.join(name(n) for n in path)} |")
        add("")
    add("### 4.5 各主题代表链（每类最长）")
    add("")
    add("| 主题 | 长度 | 链路 |")
    add("|---|---|---|")
    for cat in CATEGORY_ORDER:
        path = cat_chains.get(cat)
        if path:
            add(f"| {cat} | {len(path) - 1} | {' → '.join(name(n) for n in path)} |")
    add("")

    if additions:
        add("## 5. 推荐补充边（待你裁决）")
        add("")
        add("> 草案 80 条；以下 7 条可把孤立节点从 11 个降到 5 个。纳入后正式版为 87 条，略超 60–80 初步目标，但不属于凑数：每条都连接一个真实教学依赖。")
        add("")
        add("| # | 前置 source | 后继 target | 类别 | 关系说明 | 判断依据 |")
        add("|---|---|---|---|---|---|")
        for i, e in enumerate(additions, 1):
            add(f"| {i} | {name(e['source_id'])} | {name(e['target_id'])} | {e['category']} | {e['note']} | {e['basis']} |")
        add("")
        add(f"纳入后孤立节点：{'、'.join(name(n) for n in f_isolated) if f_isolated else '无'}")
        add("")
    else:
        add("## 5. 推荐补充边")
        add("")
        add("无（7 条推荐补充边已按用户 2026-11-15 裁决纳入正式边清单）。")
        add("")

    add("## 6. 有意未纳入的候选边" + ("（已按裁决保留不纳入）" if frozen else "（供你复核）"))
    add("")
    add("| # | 前置 source | 后继 target | 关系说明 | 未纳入理由 |")
    add("|---|---|---|---|---|")
    for i, e in enumerate(draft.get("deliberate_omissions", []), 1):
        add(f"| {i} | {name(e['source_id'])} | {name(e['target_id'])} | {e['note']} | {e['basis']} |")
    add("")
    if frozen:
        add("## 7. 冻结说明")
        add("")
        decisions = draft.get("review_decisions", {})
        add(f"- 边数：{decisions.get('edge_count', len(edges))} 条（用户 2026-11-15 裁决）")
        add(f"- 接受的独立节点：{'、'.join(decisions.get('isolated_accepted', [])) or '无'}")
        add(f"- 3 条有意未纳入边：按裁决不恢复；后续如真实诊断数据证明需要，再通过版本迭代调整。")
        add(f"- 方向问题：{decisions.get('direction_issues', '无')}")
        add("- 本文件已冻结为 `data/knowledge/primary_edges.json`；入库由 `build_primary_graph.py` 执行（仅管理 primary 边，不删除初中数据）。")
    else:
        add("## 7. 审核要点")
        add("")
        add("1. **方向**：每条边是否满足“source 先学、target 后学”；重点看多前置节点是否合理。")
        add("2. **孤立节点**：草案 11 个孤立节点。是否接受其中 5 个独立模块（026/027/030/031/033），并把其余 6 个用推荐补充边接上？")
        add("3. **边数**：草案 80 条；纳入 7 条推荐补充后 87 条。你希望冻结在 80 还是 87？")
        add("4. **有意未纳入的 3 条**：077→078、077→028、046→057 是否恢复？")
        add("5. **冻结后**：移动到 `data/knowledge/primary_edges.json`，再写 `build_primary_graph.py` 入库；本草案不写业务库。")
    add("")
    add("## 8. 复现")
    add("")
    add("```bash")
    add(".venv/Scripts/python.exe -m backend.scripts.check_primary_graph --draft data/knowledge/primary_edges.json --out data/staging/primary_graph_frozen_review.md")
    add("```")
    add("")
    add("> 本报告由脚本从边清单 JSON + 业务库 KP 名称生成；未修改数据库、未调用 LLM、未产生费用。")
    add("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="小学前置图草案校验与报告生成")
    parser.add_argument("--draft", default=str(DEFAULT_DRAFT))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()

    draft_path = Path(args.draft)
    if not draft_path.exists():
        print(f"边清单不存在：{draft_path}", file=sys.stderr)
        return 2
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    kps = load_primary_kps(Path(args.db))
    if len(kps) != 82:
        print(f"⚠️ 小学 KP 数量为 {len(kps)}（预期 82）", file=sys.stderr)

    edges = draft.get("edges", [])
    additions = draft.get("recommended_additions", [])
    errors = validate_edges(edges, kps, "草案")
    add_errors = validate_edges(additions, kps, "推荐补充")

    nodes = set(kps)
    adj, indeg = build_graph(edges, nodes)
    order = topo_sort(nodes, adj, indeg)
    if len(order) != len(nodes):
        cycle = find_cycle(nodes, adj)
        errors.append("检测到环：" + " → ".join(cycle or []))

    report = render_report(draft, kps, errors, additions, add_errors)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    isolated = [n for n in nodes if indeg[n] == 0 and not adj.get(n)]
    print(f"小学 KP：{len(kps)}；边数：{len(edges)}；推荐补充：{len(additions)}")
    print(f"类别：{dict(Counter(e['category'] for e in edges))}")
    print(f"孤立节点：{len(isolated)} -> {sorted(isolated)}")
    print(f"DAG：{'通过' if len(order) == len(nodes) else '失败'}")
    print(f"错误：{len(errors) + len(add_errors)}")
    print(f"报告已生成：{out}")
    return 0 if not errors and not add_errors else 1


if __name__ == "__main__":
    sys.exit(main())
