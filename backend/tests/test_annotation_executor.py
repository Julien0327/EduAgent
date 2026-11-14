"""标注执行器测试（Step 5-B1）：计费/校验/AIMD/预算闸门/幂等断点/manifest/B 臂。

全部使用 Mock 客户端——**本文件不含任何真实 API 调用**（Mock 通过 ≠ 真实 API 验收）。
"""
import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from backend.app.annotation import executor, pricing
from backend.app.annotation.client import (
    ChatResult,
    FatalError,
    RetryableError,
    Usage,
)
from backend.app.annotation.executor import ExecutorConfig, run_annotation, validate_output
from backend.app.annotation.recall import RecallNotAvailableError
from backend.app.storage import db

VALID = {"primary": "kp_p_001", "kp_ids": ["kp_p_001", "kp_p_002"], "difficulty": 3,
         "confidence": {"kp": 0.9, "difficulty": 0.8}, "reason": "测试判定"}
UNMAPPED = {"primary": "", "kp_ids": [], "difficulty": 2,
            "confidence": {"kp": 0.3, "difficulty": 0.9}, "reason": "不属于清单"}

# Mock usage（flash 高峰价）：hit 80×0.04 + miss 20×2 + out 50×8 = 443.2/1e6 元
PER_CALL_COST = 0.0004432


# ---------------------------------------------------------------- 计费

def test_period_at_peak_and_idle():
    from datetime import timedelta, timezone

    bj = timezone(timedelta(hours=8))
    assert pricing.period_at(datetime(2026, 11, 16, 10, 0, tzinfo=bj)) == "peak"   # 周一 10:00
    assert pricing.period_at(datetime(2026, 11, 16, 12, 30, tzinfo=bj)) == "idle"  # 午休
    assert pricing.period_at(datetime(2026, 11, 16, 14, 0, tzinfo=bj)) == "peak"   # 下午段
    assert pricing.period_at(datetime(2026, 11, 16, 18, 30, tzinfo=bj)) == "idle"  # 18:00 后
    assert pricing.period_at(datetime(2026, 11, 14, 10, 0, tzinfo=bj)) == "idle"   # 周六
    assert pricing.period_at(datetime(2026, 11, 16, 2, 0, tzinfo=bj)) == "idle"    # 凌晨
    # naive 输入按 UTC 处理：UTC 02:00 = 北京 10:00（周一）→ peak
    assert pricing.period_at(datetime(2026, 11, 16, 2, 0)) == "peak"


def test_cost_cny_hit_miss_out():
    usage = Usage(prompt_tokens=1000, completion_tokens=500, cache_hit_tokens=800)
    assert abs(pricing.cost_cny(usage, "deepseek-flash", "peak") - 0.004432) < 1e-12
    assert abs(pricing.cost_cny(usage, "deepseek-flash", "idle")
               - pricing.cost_cny(usage, "deepseek-flash", "peak") / 2) < 1e-12


def test_worst_case_ge_cached_and_unknown_model():
    worst = pricing.worst_case_cost_cny("deepseek-flash", 1000, 500)
    cached = pricing.cost_cny(Usage(1000, 500, 1000), "deepseek-flash", "idle")
    assert worst >= cached  # 决策②：预算保护按无缓存最坏口径
    with pytest.raises(KeyError):
        pricing.prices_for("gpt-x", "peak")


# ---------------------------------------------------------------- 输出校验

def test_validate_output_valid_and_fence():
    parsed, err = validate_output("```json\n" + json.dumps(VALID) + "\n```",
                                  {"kp_p_001", "kp_p_002"})
    assert err is None
    assert parsed["primary"] == "kp_p_001"
    assert parsed["kp_ids"] == ["kp_p_001", "kp_p_002"]


def test_validate_output_unmapped_is_valid():
    parsed, err = validate_output(json.dumps(UNMAPPED), {"kp_p_001"})
    assert err is None
    assert parsed["primary"] is None and parsed["kp_ids"] == []


def test_validate_output_rejections():
    allowed = {"kp_p_001", "kp_p_002", "kp_p_003", "kp_p_999"}
    bad_cases = [
        "not json",
        json.dumps({**VALID, "kp_ids": ["kp_p_888"]}),            # 清单外
        json.dumps({**VALID, "primary": "kp_p_002", "kp_ids": ["kp_p_001"]}),  # primary 不在 kp_ids
        json.dumps({"primary": "kp_p_001",
                    "kp_ids": ["kp_p_001", "kp_p_002", "kp_p_003", "kp_p_999"]}),  # >3 个
        json.dumps({**VALID, "difficulty": 6}),
        json.dumps({**VALID, "difficulty": "3"}),
        json.dumps({**VALID, "confidence": {"kp": 1.5, "difficulty": 0.5}}),
        json.dumps({"kp_ids": ["kp_p_001"], "difficulty": 3}),    # 缺 primary
    ]
    for text in bad_cases:
        parsed, err = validate_output(text, allowed)
        assert parsed is None and err, f"应当拒绝：{text[:50]}"


def test_validate_output_dedup_preserves_order():
    parsed, err = validate_output(json.dumps(VALID).replace(
        '["kp_p_001", "kp_p_002"]', '["kp_p_002", "kp_p_001", "kp_p_002"]'),
        {"kp_p_001", "kp_p_002"})
    assert err is None
    assert parsed["kp_ids"] == ["kp_p_002", "kp_p_001"]


# ---------------------------------------------------------------- Mock 与基建

class MockClient:
    """脚本化 mock：script 逐次弹出（Exception 或 str/dict）；耗尽后走 default。"""

    def __init__(self, script=(), default=None,
                 model_version="DeepSeek-V4.1-Flash-Test"):
        self.script = list(script)
        self.default = default
        self.model_version = model_version
        self.calls: list[dict] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._lock = threading.Lock()

    def chat_json(self, messages, model, temperature):
        with self._lock:
            self.calls.append({"model": model, "n_messages": len(messages)})
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            time.sleep(0.003)
            with self._lock:
                action = self.script.pop(0) if self.script else None
            if action is None:
                action = self.default(messages) if callable(self.default) else self.default
            if isinstance(action, Exception):
                raise action
            content = action if isinstance(action, str) else json.dumps(action, ensure_ascii=False)
            return ChatResult(content=content, model_version=self.model_version,
                              usage=Usage(prompt_tokens=100, completion_tokens=50,
                                          cache_hit_tokens=80))
        finally:
            with self._lock:
                self.in_flight -= 1


class MockRecaller:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}
        self.called = []

    def recall(self, q_id, stem):
        self.called.append(q_id)
        return self.mapping.get(q_id, ["kp_p_001", "kp_p_002"])


def _seed_db(path: Path) -> Path:
    db.apply_migrations(path)
    conn = db.connect(path)
    for i in (1, 2, 3, 4):
        conn.execute(
            "INSERT INTO knowledge_points (kp_id, name, subject, stage, source,"
            " description) VALUES (?, ?, 'math', 'primary', 'ape210k_rule', '描述')",
            (f"kp_p_{i:03d}", f"知识点{i}"))
    for i in range(1, 21):
        conn.execute(
            "INSERT INTO questions (q_id, stage, subject, type, stem, answer, score,"
            " difficulty, source) VALUES (?, 'primary', 'math', 'application',"
            " ?, '42', 1.0, 3, 'ape210k')", (f"q_ape_{i:06d}", f"测试题干{i}"))
    conn.execute(
        "INSERT INTO questions (q_id, stage, subject, type, stem, answer, score,"
        " difficulty, source, quality_state) VALUES"
        " ('q_ape_000021', 'primary', 'math', 'application', '拒绝题', '42', 1.0, 3,"
        " 'ape210k', 'rejected')")
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def seeded_db(tmp_path):
    return _seed_db(tmp_path / "db.sqlite")


def make_cfg(seeded_db, tmp_path, qids, cap=10.0, arm="A", budget_group="t", **kw):
    return ExecutorConfig(
        db_path=seeded_db, qids=qids, arm=arm, budget_group=budget_group,
        budget_cap_cny=cap,
        manifest_dir=tmp_path / "manifests", detail_dir=tmp_path / "staging",
        backoff_base=0.0, force_period="peak", **kw)


def qids5():
    return [f"q_ape_{i:06d}" for i in range(1, 6)]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


# ---------------------------------------------------------------- 执行器主流程

def test_happy_path_a_arm(seeded_db, tmp_path):
    cfg = make_cfg(seeded_db, tmp_path, qids5())
    client = MockClient(default=VALID)
    summary = run_annotation(cfg, client)

    assert summary["status"] == "completed" and summary["counters"]["ok"] == 5
    assert summary["calls"] == 5
    assert abs(summary["cost_cny"] - 5 * PER_CALL_COST) < 1e-9

    conn = db.connect(seeded_db)
    try:
        batch = conn.execute("SELECT * FROM annotation_batches").fetchone()
        assert batch["status"] == "completed" and batch["succeeded"] == 5
        assert batch["unmapped"] == 0 and batch["failed"] == 0 and batch["invalid"] == 0
        assert batch["input_tokens_sum"] == 500 and batch["output_tokens_sum"] == 250
        assert batch["cache_hit_tokens_sum"] == 400
        assert batch["model_version"] == "DeepSeek-V4.1-Flash-Test"
        assert batch["finished_at"] is not None
        assert conn.execute("SELECT COUNT(*) n FROM q_annotation_results"
                            " WHERE status='ok'").fetchone()["n"] == 5
        assert conn.execute("SELECT COUNT(*) n FROM q_kp_llm").fetchone()["n"] == 10
        primary_rows = conn.execute(
            "SELECT COUNT(*) n FROM q_kp_llm WHERE is_primary=1 AND kp_id='kp_p_001'"
        ).fetchone()["n"]
        assert primary_rows == 5
        assert conn.execute("SELECT COUNT(*) n FROM q_difficulty_llm"
                            " WHERE difficulty=3").fetchone()["n"] == 5
        # 幂等键唯一约束：同版本重复批次被拒
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO annotation_batches (batch_id, task_type, scope, strategy,"
                " provider, model, prompt_id, prompt_version, prompt_sha256, created_at)"
                " VALUES ('ann_dup', 'kp_difficulty', 'ape_primary', 'A_full_list',"
                " 'deepseek', 'deepseek-flash', 'kp_primary', 'v1.0', 'x', ?)",
                ("2026-11-14T00:00:00Z",))
    finally:
        conn.close()

    manifest = read_jsonl(tmp_path / "manifests" / f"{summary['batch_id']}.jsonl")
    assert manifest[0]["event"] == "batch_start" and manifest[-1]["event"] == "batch_end"
    assert manifest[-1]["ok"] == 5
    detail = read_jsonl(tmp_path / "staging" / f"tag_detail_{summary['batch_id']}.jsonl")
    assert len(detail) == 5 and all("parsed" in d and d["parsed"] for d in detail)


def test_unmapped_persisted_no_labels(seeded_db, tmp_path):
    conn = db.connect(seeded_db)
    conn.execute("INSERT INTO q_kp (q_id, kp_id) VALUES ('q_ape_000001', 'kp_p_003')")
    conn.commit()
    conn.close()

    cfg = make_cfg(seeded_db, tmp_path, qids5())
    summary = run_annotation(cfg, MockClient(default=UNMAPPED))

    assert summary["counters"]["unmapped"] == 5 and summary["counters"]["ok"] == 0
    conn = db.connect(seeded_db)
    try:
        assert conn.execute("SELECT COUNT(*) n FROM q_annotation_results"
                            " WHERE status='unmapped'").fetchone()["n"] == 5
        assert conn.execute("SELECT COUNT(*) n FROM q_kp_llm").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM q_difficulty_llm").fetchone()["n"] == 5
        # 消费视图：unmapped 行存在且无 rule 回退
        rows = conn.execute(
            "SELECT tag_source FROM v_q_kp_effective WHERE q_id='q_ape_000001'"
        ).fetchall()
        assert [r["tag_source"] for r in rows] == ["llm_unmapped"]
    finally:
        conn.close()


def test_invalid_then_corrective_retry(seeded_db, tmp_path):
    bad = "这不是 JSON"
    cfg = make_cfg(seeded_db, tmp_path, qids5()[:2], concurrency_initial=1,
                   concurrency_max=1)
    client = MockClient(script=[bad, VALID, bad, VALID], default=VALID)
    summary = run_annotation(cfg, client)
    assert summary["counters"]["ok"] == 2 and summary["counters"]["invalid"] == 0
    assert summary["calls"] == 4  # 每题 1 次失效 + 1 次纠错
    detail = read_jsonl(tmp_path / "staging" / f"tag_detail_{summary['batch_id']}.jsonl")
    assert all(d["retries"] == 1 for d in detail)


def test_always_invalid_marked_no_labels(seeded_db, tmp_path):
    cfg = make_cfg(seeded_db, tmp_path, qids5()[:2], concurrency_initial=1,
                   concurrency_max=1)
    client = MockClient(default=" garbage 输出 ")
    summary = run_annotation(cfg, client)
    assert summary["counters"]["invalid"] == 2
    assert set(summary["invalid_qids"]) == {"q_ape_000001", "q_ape_000002"}
    conn = db.connect(seeded_db)
    try:
        assert conn.execute("SELECT COUNT(*) n FROM q_kp_llm").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) n FROM q_annotation_results"
                            " WHERE status='invalid'").fetchone()["n"] == 2
    finally:
        conn.close()


def test_throttle_aimd_drops_concurrency(seeded_db, tmp_path):
    """429（throttled）→ 并发降半至下限 1；429 永远是降并发的信号（第二版修订）。"""
    state = {"n": 0}

    def default(messages):
        state["n"] += 1
        if state["n"] <= 10:
            raise RetryableError("429", throttled=True)
        return VALID

    cfg = make_cfg(seeded_db, tmp_path, qids5())
    client = MockClient(default=default)
    summary = run_annotation(cfg, client)
    assert summary["throttle_events"] == 10
    assert summary["concurrency_final"] == 1
    assert summary["counters"]["ok"] + summary["counters"]["failed"] == 5
    assert summary["counters"]["ok"] >= 1
    assert all(c["model"] == "deepseek-flash" for c in client.calls)


def test_aimd_promotes_on_stable_success(seeded_db, tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "PROMOTE_STREAK", 2)
    cfg = make_cfg(seeded_db, tmp_path, [f"q_ape_{i:06d}" for i in range(1, 13)],
                   concurrency_initial=2, concurrency_max=4)
    client = MockClient(default=VALID)
    summary = run_annotation(cfg, client)
    assert summary["counters"]["ok"] == 12
    assert summary["concurrency_final"] == 4  # 2 → 3 → 4（上限）
    assert client.max_in_flight <= 4


def test_budget_hard_stop_no_overshoot(seeded_db, tmp_path):
    """100% 硬停止：实际支出永不越 cap（预留按无缓存最坏口径，决策②）。"""
    cfg = make_cfg(seeded_db, tmp_path, qids5(), cap=0.001,
                   worst_per_call_cny=0.0005)
    client = MockClient(default=VALID)
    summary = run_annotation(cfg, client)
    assert summary["status"] == "aborted"
    assert summary["stop_reason"] == "budget_hard_stop"
    assert summary["cost_cny"] <= 0.001 + 1e-12
    assert summary["calls"] <= 4
    assert summary["counters"]["ok"] >= 1
    manifest = read_jsonl(tmp_path / "manifests" / f"{summary['batch_id']}.jsonl")
    assert any(e["event"] == "budget_stop" for e in manifest)


def test_budget_pause_line_stops_submission(seeded_db, tmp_path):
    """¥15 暂停线（决策㉖）：停止提交新请求（在途排水），批次 aborted 待人工确认；
    budget_pause 事件含进度/费用/剩余题量/预计剩余费用。"""
    cfg = make_cfg(seeded_db, tmp_path, qids5(), cap=0.001, budget_pause_at_cny=0.0008,
                   worst_per_call_cny=0.0005, concurrency_initial=1, concurrency_max=1)
    client = MockClient(default=VALID)
    summary = run_annotation(cfg, client)
    assert summary["status"] == "aborted"
    assert summary["stop_reason"] == "budget_pause_line"
    assert summary["pause_requested"] is True
    assert summary["counters"]["ok"] == 2  # 0.443 分/次 × 2 ≥ 暂停线 0.0008 → 停止
    assert summary["cost_cny"] < 0.001
    manifest = read_jsonl(tmp_path / "manifests" / f"{summary['batch_id']}.jsonl")
    pause_events = [e for e in manifest if e["event"] == "budget_pause"]
    assert len(pause_events) == 1
    assert pause_events[0]["remaining_items"] == 3
    assert pause_events[0]["est_remaining_cny"] > 0


def test_budget_remind_line_keeps_running(seeded_db, tmp_path):
    """¥10 提醒线（决策㉖）：通知+记录实际支出，**不自动停止**。"""
    cfg = make_cfg(seeded_db, tmp_path, qids5()[:3], cap=10.0,
                   budget_warn_at_cny=0.0004, concurrency_initial=1, concurrency_max=1)
    client = MockClient(default=VALID)
    summary = run_annotation(cfg, client)
    assert summary["status"] == "completed" and summary["counters"]["ok"] == 3
    assert summary["reminded"] is True and summary["pause_requested"] is False
    manifest = read_jsonl(tmp_path / "manifests" / f"{summary['batch_id']}.jsonl")
    assert any(e["event"] == "budget_remind" for e in manifest)
    assert not any(e["event"] == "budget_pause" for e in manifest)


def test_budget_group_cumulative_prevents_bypass(seeded_db, tmp_path):
    """组级累计（决策㉖）：同一 budget_group 新建批次不得绕过暂停线/硬停线。"""
    cfg_a = make_cfg(seeded_db, tmp_path, qids5()[:2], cap=0.001,
                     worst_per_call_cny=0.0005, budget_group="trial",
                     concurrency_initial=1, concurrency_max=1)
    s1 = run_annotation(cfg_a, MockClient(default=VALID))
    assert s1["status"] == "aborted" and s1["cost_cny"] > 0

    # 新建批次（B 臂，同预算组）→ 组级累计已超暂停线 → 零调用直接拦截
    cfg_b = make_cfg(seeded_db, tmp_path, qids5()[:2], arm="B", cap=0.001,
                     worst_per_call_cny=0.0005, budget_group="trial",
                     concurrency_initial=1, concurrency_max=1)
    s2 = run_annotation(cfg_b, MockClient(default=VALID), recaller=MockRecaller())
    assert s2["calls"] == 0 and s2["counters"]["ok"] == 0
    assert s2["stop_reason"] in ("budget_pause_line", "budget_hard_stop")
    assert s2["status"] == "aborted"
    assert s2["group_cost_cny"] >= s1["cost_cny"]  # 组累计包含 A 臂支出


def test_resume_same_batch_and_completed_gate(seeded_db, tmp_path):
    """断点续跑：同批次复用（UNIQUE 幂等键）、已完成题跳过；completed 批次拒绝重跑。"""
    cfg1 = make_cfg(seeded_db, tmp_path, qids5(), cap=0.001,
                    worst_per_call_cny=0.0005, concurrency_initial=1,
                    concurrency_max=1)
    client1 = MockClient(default=VALID)
    summary1 = run_annotation(cfg1, client1)
    assert summary1["status"] == "aborted" and summary1["counters"]["ok"] == 2

    cfg2 = make_cfg(seeded_db, tmp_path, qids5(), cap=10.0,
                    concurrency_initial=1, concurrency_max=1)
    client2 = MockClient(default=VALID)
    summary2 = run_annotation(cfg2, client2)
    assert summary2["batch_id"] == summary1["batch_id"]
    assert summary2["status"] == "completed" and summary2["counters"]["ok"] == 3
    assert len(client2.calls) == 3  # 只跑剩余 3 题（已完成 2 题跳过）

    manifest = read_jsonl(tmp_path / "manifests" / f"{summary1['batch_id']}.jsonl")
    starts = [e for e in manifest if e["event"] == "batch_start"]
    assert len(starts) == 2 and starts[1]["resumed"] is True

    conn = db.connect(seeded_db)
    try:
        assert conn.execute("SELECT COUNT(*) n FROM annotation_batches").fetchone()["n"] == 1
        assert conn.execute("SELECT succeeded FROM annotation_batches"
                            ).fetchone()["succeeded"] == 5
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="已完成"):
        run_annotation(make_cfg(seeded_db, tmp_path, qids5(), cap=10.0),
                       MockClient(default=VALID))


def test_b_arm_recall_restriction_and_empty(seeded_db, tmp_path):
    # 正常：候选内标签
    cfg = make_cfg(seeded_db, tmp_path, qids5()[:2], arm="B")
    summary = run_annotation(cfg, MockClient(default=VALID), recaller=MockRecaller())
    assert summary["counters"]["ok"] == 2

    # 候选外标签 → 纠错重试 → 候选内（独立库，避免与上一子用例批次冲突）
    db2 = _seed_db(tmp_path / "db2.sqlite")
    cfg2 = make_cfg(db2, tmp_path, qids5()[:2], arm="B",
                    concurrency_initial=1, concurrency_max=1)
    out_of_pool = {**VALID, "kp_ids": ["kp_p_003"], "primary": "kp_p_003"}
    in_pool = {**VALID, "kp_ids": ["kp_p_001"], "primary": "kp_p_001"}
    client2 = MockClient(script=[out_of_pool, in_pool, out_of_pool, in_pool],
                         default=in_pool)
    recaller2 = MockRecaller(mapping={f"q_ape_{i:06d}": ["kp_p_001"] for i in range(1, 3)})
    summary2 = run_annotation(cfg2, client2, recaller=recaller2)
    assert summary2["counters"]["ok"] == 2 and summary2["calls"] == 4

    # 空召回 → 任何非空 kp_ids 均无效 → invalid（不硬挂）
    db3 = _seed_db(tmp_path / "db3.sqlite")
    cfg3 = make_cfg(db3, tmp_path, qids5()[:1], arm="B",
                    concurrency_initial=1, concurrency_max=1)
    recaller3 = MockRecaller(mapping={"q_ape_000001": []})
    summary3 = run_annotation(cfg3, MockClient(default=VALID), recaller=recaller3)
    assert summary3["counters"]["invalid"] == 1


def test_b_arm_requires_recaller(seeded_db, tmp_path):
    with pytest.raises(ValueError, match="recaller"):
        run_annotation(make_cfg(seeded_db, tmp_path, qids5()[:1], arm="B"),
                       MockClient(default=VALID))


def test_real_recaller_placeholder_fails_fast(seeded_db, tmp_path):
    from backend.app.annotation.recall import NotImplementedRecaller
    cfg = make_cfg(seeded_db, tmp_path, qids5()[:1], arm="B")
    summary = run_annotation(cfg, MockClient(default=VALID),
                             recaller=NotImplementedRecaller())
    assert summary["counters"]["failed"] == 1  # fail-fast，不静默降级


def test_processed_only_guard_before_batch_creation(seeded_db, tmp_path):
    cfg = make_cfg(seeded_db, tmp_path, ["q_ape_000001", "q_ape_000021"])  # 21=rejected
    with pytest.raises(ValueError, match="非 processed"):
        run_annotation(cfg, MockClient(default=VALID))
    conn = db.connect(seeded_db)
    try:
        assert conn.execute("SELECT COUNT(*) n FROM annotation_batches"
                            ).fetchone()["n"] == 0
    finally:
        conn.close()


def test_manifest_batch_end_consistency(seeded_db, tmp_path):
    cfg = make_cfg(seeded_db, tmp_path, qids5())
    summary = run_annotation(cfg, MockClient(default=VALID))
    manifest = read_jsonl(tmp_path / "manifests" / f"{summary['batch_id']}.jsonl")
    end = manifest[-1]
    assert end["event"] == "batch_end" and end["status"] == "completed"
    conn = db.connect(seeded_db)
    try:
        batch = conn.execute("SELECT * FROM annotation_batches").fetchone()
    finally:
        conn.close()
    assert end["ok"] == batch["succeeded"] and end["failed"] == batch["failed"]
    assert abs(end["cost_cny"] - batch["cost_cny_est"]) < 1e-9
    assert end["input_tokens"] == batch["input_tokens_sum"]
