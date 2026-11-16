"""确定性归因算法（Phase 3 / S2，§5.2）。

规则出骨架、LLM 只在 S3 做叙述。全部由 Python 确定性规则计算：
- 图谱：``kp_edges`` 方向 ``from_kp=前置（先学）→ to_kp=后继``；反向回溯前置边。
- 对每个薄弱（state=weak）KP：沿前置边向上回溯，找“掌握度 <0.7 的最深前置节点”为根因；
  前置全部良好（≥0.7）→ 目标自身为根因。
- **前置证据不足（insufficient/no_coverage）→ 阻断该分支，不猜测根因**，记录 blocked_prereqs。
- 多分支：保留所有最深失败节点为 root_candidates；>1 即分支冲突，反映在置信度。
- 支持孤立节点（无前置/无后继 → 根因=自身）；环保护（visited + 显式检测）。

置信度与状态分离（D3）：
- 状态由 S1 掌握度模块决定（normal/weak/insufficient/no_coverage）；
- 置信度只在此处计算，且**不改状态**。分级：
    low   阻断分支 OR 分支冲突 OR aux_share>0.5 OR 最小样本 ≤3（临界）
    high  无上述降级，且最小样本 ≥5
    mid   无上述降级，且 3 < 最小样本 < 5（即 =4）
- insufficient/no_coverage 不可能为 high（它们只在被阻断时出现，必然 low）。

输出字段：target_kp / root_kp / path / root_candidates / conflict / blocked_prereqs /
prereq_status / evidence / confidence / min_samples / target_mastery。
"""
from __future__ import annotations

from collections import defaultdict

from backend.app.diagnosis.mastery import (
    INSUFFICIENT,
    NO_COVERAGE,
    PREREQ_FAIL_THRESHOLD,
    WEAK,
)

FAIL_THRESHOLD = PREREQ_FAIL_THRESHOLD  # 前置 <0.7 视为失败（§5.1/§5.2 一致）

LOW = "low"
MID = "mid"
HIGH = "high"


def build_prereq_graph(edges: list[dict]) -> dict[str, list[str]]:
    """edges: [{from_kp, to_kp}] → prereqs[后继] = [前置...]（按 kp_id 排序）。"""
    prereqs: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        prereqs[e["to_kp"]].append(e["from_kp"])
    return {k: sorted(v) for k, v in prereqs.items()}


def detect_cycles(edges: list[dict]) -> list[list[str]]:
    """返回 from->to 邻接上的环路径（应为空；防御性检查）。"""
    adj: dict[str, list[str]] = defaultdict(list)
    nodes: set[str] = set()
    for e in edges:
        adj[e["from_kp"]].append(e["to_kp"])
        nodes.add(e["from_kp"])
        nodes.add(e["to_kp"])
    color = {n: 0 for n in nodes}
    cycles: list[list[str]] = []

    def dfs(n, stack):
        color[n] = 1
        stack.append(n)
        for m in adj.get(n, ()):
            if color[m] == 1:
                cycles.append(stack[stack.index(m):] + [m])
            elif color[m] == 0:
                dfs(m, stack)
        stack.pop()
        color[n] = 2

    for n in sorted(nodes):
        if color[n] == 0:
            dfs(n, [])
    return cycles


def _evidence_for(kp_ids: set[str], evidence: list[dict]) -> list[dict]:
    """抽取证据链：(q_id, kp_id, role, exam_label, q_no) 去重，保留题目↔角色↔考试关系。"""
    seen = set()
    out = []
    for e in evidence:
        if e["kp_id"] not in kp_ids:
            continue
        key = (e["q_id"], e["kp_id"], e["role"], e["exam_label"], e["q_no"])
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "q_id": e["q_id"], "kp_id": e["kp_id"], "role": e["role"],
            "exam_label": e["exam_label"], "q_no": e["q_no"],
        })
    out.sort(key=lambda x: (x["kp_id"], x["exam_label"], x["q_no"], x["role"]))
    return out


def _attribute_one(target: str, mastery: dict, prereqs: dict) -> dict:
    """对一个薄弱 target 做确定性归因。"""
    target_m = mastery[target]

    failing: list[tuple[int, str]] = [(0, target)]  # (depth, kp)
    blocked: list[str] = []
    parent: dict[str, str] = {}
    visited: set[str] = {target}
    explored: dict[str, dict] = {}

    def record(kp):
        m = mastery.get(kp)
        explored[kp] = {
            "kp_id": kp,
            "state": m["state"],
            "mastery": m.get("mastery"),
            "aux_share": m.get("aux_share"),
            "sample_count": m["sample_count"],
        }

    record(target)

    def explore(node: str, depth: int):
        """node 是 target 的某个前置（或其上游）。"""
        visited.add(node)
        m = mastery.get(node)
        record(node)
        if m is None or m["state"] in (INSUFFICIENT, NO_COVERAGE):
            blocked.append(node)  # 证据不足：阻断，不猜测
            return
        # 充分（normal/weak）：mastery <0.7 才算失败节点，继续向上找更深
        if m["mastery"] is not None and m["mastery"] < FAIL_THRESHOLD:
            failing.append((depth, node))
            for p in prereqs.get(node, []):
                if p in visited:
                    continue
                parent[p] = node
                explore(p, depth + 1)
        # else: mastery >=0.7 健康，不继续向上（上游不是问题）

    for p in prereqs.get(target, []):
        if p in visited:
            continue
        parent[p] = target
        explore(p, 1)

    deepest = max(d for d, _ in failing)
    cands = [kp for d, kp in failing if d == deepest]
    # 并列：掌握度升序 → kp_id；primary root 取第一个
    cands_sorted = sorted(cands, key=lambda kp: (mastery[kp]["mastery"], kp))
    root = cands_sorted[0]
    conflict = len(cands_sorted) > 1

    # 路径：root 沿 parent 到 target
    path = [root]
    cur = root
    while cur != target:
        cur = parent[cur]
        path.append(cur)
    path_names = path  # 名称由调用方映射

    path_kp_ids = set(path)
    min_samples = min(mastery[k]["sample_count"] for k in path_kp_ids)
    aux_share = target_m.get("aux_share")

    if blocked or conflict or (aux_share is not None and aux_share > 0.5) or min_samples <= 3:
        confidence = LOW
    elif min_samples >= 5:
        confidence = HIGH
    else:
        confidence = MID

    return {
        "target_kp": target,
        "root_kp": root,
        "path": path,
        "root_candidates": [
            {"kp_id": kp, "mastery": mastery[kp]["mastery"], "depth": deepest}
            for kp in cands_sorted
        ],
        "conflict": conflict,
        "blocked_prereqs": sorted(set(blocked)),
        "prereq_status": explored,
        "confidence": confidence,
        "min_samples": min_samples,
        "target_mastery": target_m.get("mastery"),
        "target_aux_share": aux_share,
    }


def attribute(mastery: dict, prereqs: dict, names: dict | None = None,
              evidence: list[dict] | None = None) -> list[dict]:
    """对全部 weak KP 做归因，返回链列表（按掌握度升序 → kp_id）。"""
    weak = [kp for kp, m in mastery.items() if m["state"] == WEAK]
    weak.sort(key=lambda kp: (mastery[kp]["mastery"], kp))
    names = names or {}
    chains = []
    for kp in weak:
        chain = _attribute_one(kp, mastery, prereqs)
        chain["target_name"] = names.get(kp, kp)
        chain["root_name"] = names.get(chain["root_kp"], chain["root_kp"])
        chain["path_names"] = [names.get(k, k) for k in chain["path"]]
        chain["blocked_prereq_names"] = [names.get(k, k) for k in chain["blocked_prereqs"]]
        path_kp_ids = set(chain["path"])
        chain["evidence"] = _evidence_for(path_kp_ids, evidence) if evidence else []
        chains.append(chain)
    return chains
