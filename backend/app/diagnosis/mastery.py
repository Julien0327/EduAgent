"""确定性掌握度计算（Phase 3 / S1，§5.1）。

纯 Python、零 LLM。掌握度、近期权重、卷面分值权重、样本数门槛全部由本模块
计算；LLM 仅在后继步骤做叙述（不改任何数值）。

口径（决策㉝ + 用户 D1/D2）：
- 单题对错：``is_correct`` 非空优先；否则 ``得分/满分``（部分得分）。
- 每 (考试, 题) 班级平均得分率 r。
- 掌握度 = Σ(r × 卷面分值 × 近期权重 × 角色权重) / Σ(卷面分值 × 近期权重 × 角色权重)。
- 近期权重：最新一场考试 =2，其余 =1（§5.1 2:1）。
- 角色权重：primary=1.0，aux=λ（D1）。
- 样本数 = 不同 q_id 数（主辅都计入原始样本；角色权重只影响证据强度，不改样本口径）。
- 四种状态（D2）：
    no_coverage   该班考试无任何题映射到该 KP
    insufficient  有映射题但不同 q_id 数 < 3
    weak          样本足够且掌握度 < 0.6
    normal        样本足够且掌握度 ≥ 0.6
- aux_share（D3）= Σ(aux 行：卷面分值 × 近期权重 × λ) / Σ(全部行：卷面分值 × 近期权重 × 角色权重)。
  —— 即“辅助证据权重占全部证据权重的比例”，**不含得分率**（衡量证据构成，而非答对贡献）；
  随 λ 变化，用于置信度，不改状态。
- weighted_evidence_count（D2）= Σ(近期权重 × 角色权重)，不含分值量纲，表示“加权题量”。
"""
from __future__ import annotations

from collections import defaultdict

from backend.app.diagnosis.rounding import round_half_up

WEAK_THRESHOLD = 0.6
INSUFFICIENT_SAMPLES = 3
PREREQ_FAIL_THRESHOLD = 0.7
RECENCY_LATEST = 2.0
RECENCY_OTHER = 1.0
ROLE_PRIMARY = 1.0

NO_COVERAGE = "no_coverage"
INSUFFICIENT = "insufficient"
WEAK = "weak"
NORMAL = "normal"


def _no_coverage_entry(kp_id: str) -> dict:
    return {
        "kp_id": kp_id,
        "state": NO_COVERAGE,
        "mastery": None,
        "primary_only_mastery": None,
        "aux_share": None,
        "sample_count": 0,
        "primary_sample_count": 0,
        "aux_sample_count": 0,
        "weighted_evidence_count": 0.0,
        "weighted_primary_evidence_count": 0.0,
        "weighted_aux_evidence_count": 0.0,
        "q_ids": [],
    }


def recency_weights(exams: list[dict]) -> dict[str, float]:
    """exams 已按日期升序；最后一场 = 最新 = 权重 2，其余 1。"""
    weights: dict[str, float] = {}
    for i, ex in enumerate(exams):
        weights[ex["exam_id"]] = RECENCY_LATEST if i == len(exams) - 1 else RECENCY_OTHER
    return weights


def _correctness(got_score, full_score, is_correct) -> float:
    if is_correct is not None:
        return 1.0 if is_correct else 0.0
    if full_score:
        return got_score / full_score
    return 0.0


def _exam_q_ratios(evidence: list[dict]) -> dict[tuple[str, str], float]:
    """按 (exam_id, q_id) 聚合班级平均得分率。"""
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for e in evidence:
        groups[(e["exam_id"], e["q_id"])].append(
            _correctness(e["got_score"], e["full_score"], e["is_correct"])
        )
    return {
        key: round_half_up(sum(v) / len(v), 4) for key, v in groups.items()
    }


def compute_mastery(evidence: list[dict], exams: list[dict], lam: float,
                    all_kp_ids: set[str] | None = None) -> dict[str, dict]:
    """按 KP 计算掌握度与四状态。lam = 辅助证据权重（D1）。

    all_kp_ids：图谱全集 KP；提供时对未出现在证据中的 KP 输出 no_coverage。
    """
    weights = recency_weights(exams)
    ratios = _exam_q_ratios(evidence)

    by_kp: dict[str, list[dict]] = defaultdict(list)
    for e in evidence:
        e = dict(e)
        e["ratio"] = ratios[(e["exam_id"], e["q_id"])]
        e["recency_w"] = weights[e["exam_id"]]
        e["role_w"] = ROLE_PRIMARY if e["role"] == "primary" else lam
        by_kp[e["kp_id"]].append(e)

    out: dict[str, dict] = {}
    for kp, rows in by_kp.items():
        primary_rows = [r for r in rows if r["role"] == "primary"]
        aux_rows = [r for r in rows if r["role"] == "aux"]

        def weighted_num_den(rows_subset):
            num = sum(r["ratio"] * r["full_score"] * r["recency_w"] * r["role_w"]
                      for r in rows_subset)
            den = sum(r["full_score"] * r["recency_w"] * r["role_w"]
                      for r in rows_subset)
            return num, den

        p_num, p_den = weighted_num_den(primary_rows)
        num, den = weighted_num_den(rows)
        aux_weight = sum(r["full_score"] * r["recency_w"] * r["role_w"]
                         for r in aux_rows)

        mastery = round_half_up(num / den, 4) if den else None
        primary_only = round_half_up(p_num / p_den, 4) if p_den else None
        aux_share = round_half_up(aux_weight / den, 4) if den else None

        q_ids = {r["q_id"] for r in rows}
        sample_count = len(q_ids)
        primary_sample_count = len({r["q_id"] for r in primary_rows})
        aux_sample_count = len({r["q_id"] for r in aux_rows})
        weighted_evidence_count = round_half_up(
            sum(r["recency_w"] * r["role_w"] for r in rows), 4)
        weighted_primary = round_half_up(
            sum(r["recency_w"] * r["role_w"] for r in primary_rows), 4)
        weighted_aux = round_half_up(
            sum(r["recency_w"] * r["role_w"] for r in aux_rows), 4)

        if sample_count == 0:
            state = NO_COVERAGE
        elif sample_count < INSUFFICIENT_SAMPLES:
            state = INSUFFICIENT
        elif mastery is not None and mastery < WEAK_THRESHOLD:
            state = WEAK
        else:
            state = NORMAL

        out[kp] = {
            "kp_id": kp,
            "state": state,
            "mastery": mastery,
            "primary_only_mastery": primary_only,
            "aux_share": aux_share,
            "sample_count": sample_count,
            "primary_sample_count": primary_sample_count,
            "aux_sample_count": aux_sample_count,
            "weighted_evidence_count": weighted_evidence_count,
            "weighted_primary_evidence_count": weighted_primary,
            "weighted_aux_evidence_count": weighted_aux,
            "q_ids": sorted(q_ids),
        }

    if all_kp_ids is not None:
        for kp in sorted(all_kp_ids - set(out)):
            out[kp] = _no_coverage_entry(kp)
    return out
