"""500 题分层试验集构建器与 50 题核验选题测试（Step 5-B1）。

小规模注入数据验证：确定性、processed 隔离、层间互斥、S6 注入、配额机制、
核验选题的最低配额与确定性。
"""
import json
from pathlib import Path

import pytest

from backend.app.storage import db
from backend.scripts.build_trial500 import build_trial
from backend.scripts.export_review_sheet import MIN_PER_STRATUM, select_review_50


@pytest.fixture()
def small_db(tmp_path):
    """小型库：构造覆盖各层特征的 processed 题 + 1 rejected + 4 个 KP。"""
    path = tmp_path / "db.sqlite"
    db.apply_migrations(path)
    conn = db.connect(path)
    for i in range(1, 8):
        conn.execute(
            "INSERT INTO knowledge_points (kp_id, name, subject, stage, source,"
            " description) VALUES (?, ?, 'math', 'primary', 'ape210k_rule', '描述')",
            (f"kp_p_{i:03d}", f"知识点{i}"))
    rows = [
        # q_id 后缀, stem, difficulty, answer, links(kp 序号列表)
        ("z1", "普通零挂接题一", 3, "42", []),
        ("z2", "普通零挂接题二", 4, "42", []),
        ("z3", "普通零挂接题三", 5, "42", []),
        ("z4", "普通零挂接题四", 1, "42", []),
        ("z5", "普通零挂接题五", 2, "42", []),
        ("p1", "一瓶果汁，喝了(3/4)，杯中还有((())/(()))．", 2, "1/4", [1]),
        ("p2", "另一个((())/(()))填空题", 2, "(1/4)", [1, 2]),
        ("o1", "含其他空位()的题", 3, "42", [1]),
        ("s1", "因为27*324=8748，所以0.27*3.24=多少", 2, "0.8748", [1]),
        ("s2", "12*13=多少", 1, "156", [2]),
        ("n1", "车牌川A*****限行", 4, "42", [1]),
        ("d1", "低频 KP 题一（统计图表）", 1, "42", [4]),
        ("m1", "恰好一个挂接的题", 3, "42", [1]),
        ("x1", "多挂接题（≥6）", 4, "42", [1, 2, 3, 4, 5, 6, 7]),
        ("g1", "百分之二十五答案题", 3, "25%", [1]),
        ("f1", "分数答案题", 3, "3/4", [2]),
        ("long1", "长题干" * 40, 3, "42", [1]),
        ("r1", "被拒绝的题", 3, "42", []),
    ]
    for suffix, stem, diff, ans, kps in rows:
        conn.execute(
            "INSERT INTO questions (q_id, stage, subject, type, stem, answer, score,"
            " difficulty, source, quality_state) VALUES (?, 'primary', 'math',"
            " 'application', ?, ?, 1.0, ?, 'ape210k', 'processed')",
            (f"q_ape_test{suffix}", stem, ans, diff))
        for kp_seq in kps:
            conn.execute("INSERT INTO q_kp (q_id, kp_id) VALUES (?, ?)",
                         (f"q_ape_test{suffix}", f"kp_p_{kp_seq:03d}"))
    conn.execute(
        "INSERT INTO questions (q_id, stage, subject, type, stem, answer, score,"
        " difficulty, source, quality_state) VALUES"
        " ('q_ape_testrj', 'primary', 'math', 'application', '拒绝题', '42', 1.0, 3,"
        " 'ape210k', 'rejected')")
    conn.commit()
    conn.close()
    return path


PLAN = [("S1_zero_link", 2), ("S2_main_placeholder", 1), ("S4_star_math", 1),
        ("S5_nonstandard_star", 1), ("S6_degenerate_incomplete", 1),
        ("S7_lowfreq_kp", 1), ("S9_d1", 1), ("S10_long_stem", 1),
        ("S11_pct_answer", 1), ("S11_frac_answer", 1)]


def test_build_trial_deterministic_and_processed_only(small_db, tmp_path):
    out1 = tmp_path / "t1.jsonl"
    out2 = tmp_path / "t2.jsonl"
    s6 = {"q_ape_testz1"}  # 注入：零挂接题之一为退化候选（会被 S1 优先消耗 → S6 取其他）
    s6b = {"q_ape_testz2"}
    summary1 = build_trial(small_db, out1, seed=42, strata_plan=PLAN, s12_quota=3,
                           s6_raw_ids=s6)
    build_trial(small_db, out2, seed=42, strata_plan=PLAN, s12_quota=3, s6_raw_ids=s6)
    assert out1.read_bytes() == out2.read_bytes()  # 确定性

    rows = [json.loads(l) for l in out1.read_text(encoding="utf-8").splitlines() if l]
    qids = [r["q_id"] for r in rows]
    assert len(qids) == len(set(qids))  # 无重复（层间互斥）
    assert "q_ape_testrj" not in qids   # rejected 永不入选
    total_quota = sum(q for _, q in PLAN) + 3
    assert summary1["total"] == total_quota

    by = {}
    for r in rows:
        by.setdefault(r["stratum"], []).append(r["q_id"])
    assert len(by["S1_zero_link"]) == 2
    assert len(by["S2_main_placeholder"]) == 1
    assert by["S5_nonstandard_star"] == ["q_ape_testn1"]  # 非标准星号专属池
    assert by["S4_star_math"] == ["q_ape_tests1"]          # 标准星号池（互斥于 S5）
    # S6 取注入集 ∩ processed 且未被 S1 消耗的题
    s1_picks = set(by["S1_zero_link"])
    s6_pool = ({"q_ape_testz1", "q_ape_testz2"} - s1_picks)
    assert by["S6_degenerate_incomplete"] == [sorted(s6_pool)[0]]
    assert all(r["stratum"] for r in rows)


def test_s6_injected_ids_respected(small_db, tmp_path):
    out = tmp_path / "t.jsonl"
    # 把 z2/z3 标为 S6 候选；S1 先取 2 个零挂接（z1,z2 中按排序抽 2 → 取决于 seed），
    # S6 池 = 注入集 ∩ processed − 已分配，配额 1
    summary = build_trial(small_db, out, seed=7,
                          strata_plan=[("S6_degenerate_incomplete", 1)],
                          s12_quota=0, s6_raw_ids={"q_ape_testz2", "q_ape_testz3"})
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
    assert len(rows) == 1
    assert rows[0]["q_id"] in {"q_ape_testz2", "q_ape_testz3"}


def test_quota_larger_than_pool_takes_all(small_db, tmp_path):
    out = tmp_path / "t.jsonl"
    summary = build_trial(small_db, out, seed=42,
                          strata_plan=[("S5_nonstandard_star", 10)],
                          s12_quota=0, s6_raw_ids=set())
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l]
    assert len(rows) == 1  # 池只有 1 题，配额 10 只取 1（构建输出含告警）
    assert summary["pools"][0]["pool_remaining"] == 1


def test_select_review_50_mins_and_determinism():
    from backend.scripts.build_trial500 import STRATA_PLAN, S12_QUOTA
    trial_rows = []
    seq = 0
    for stratum, quota in STRATA_PLAN:
        for _ in range(quota):
            trial_rows.append({"q_id": f"q_{seq:06d}", "stratum": stratum})
            seq += 1
    for _ in range(S12_QUOTA):
        trial_rows.append({"q_id": f"q_{seq:06d}", "stratum": "S12_random"})
        seq += 1
    assert seq == 500

    picked1 = select_review_50(trial_rows, total=50, seed=42)
    picked2 = select_review_50(trial_rows, total=50, seed=42)
    assert picked1 == picked2  # 确定性（不依赖进程内 hash）
    assert len(picked1) == 50
    assert len({r["q_id"] for r in picked1}) == 50
    counts = {}
    for r in picked1:
        counts[r["stratum"]] = counts.get(r["stratum"], 0) + 1
    for stratum, minimum in MIN_PER_STRATUM.items():
        assert counts.get(stratum, 0) >= minimum, f"{stratum} 低于最低配额"
