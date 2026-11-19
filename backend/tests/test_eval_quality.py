"""Phase 5 / S3：质检指标接入评测（runner 读取 03_质检报告.json + 持久化到 eval_runs）。"""
from __future__ import annotations

import json
from pathlib import Path

from backend.app.eval.runner import _read_quality
from backend.app.eval.models import CaseRecord


def _report(status="completed", **kw) -> dict:
    summary = {"total": 2, "passed": 2, "retired": 0, "unverified": 0,
               "verified_questions": 2,
               "first_round_fail": 0, "first_round_fail_eventually_passed": 0,
               "total_fail_verdicts": 0, "total_verification_rounds": 2,
               "regenerated": 0, "duplicate_pairs_batch": 0}
    summary.update(kw)
    return {"status": status, "summary": summary}


def test_read_quality_question_gen(tmp_path):
    path = tmp_path / "03_质检报告.json"
    path.write_text(json.dumps(_report(total_verification_rounds=3, first_round_fail=1),
                               ensure_ascii=False), encoding="utf-8")
    q = _read_quality("question_gen", {"03_质检报告.json": path})
    assert q["status"] == "completed"
    assert q["total"] == 2
    assert q["total_verification_rounds"] == 3
    assert q["first_round_fail"] == 1
    assert q["verified_questions"] == 2


def test_read_quality_non_question_gen_none():
    assert _read_quality("exam_loop", {}) is None
    assert _read_quality("lesson_plan", {}) is None


def test_read_quality_missing_or_invalid_none(tmp_path):
    assert _read_quality("question_gen", {}) is None
    bad = tmp_path / "03_质检报告.json"
    bad.write_text("not-json", encoding="utf-8")
    assert _read_quality("question_gen", {"03_质检报告.json": bad}) is None


def test_persist_records_includes_quality(tmp_path):
    """aggregate.persist_records 把 rec.quality 写入 eval_runs.metrics（无迁移）。"""
    from backend.app.eval.aggregate import persist_records
    from backend.app.storage import db as _db

    db_path = tmp_path / "e.sqlite"
    cases = [{"case_id": "gc_q01"}]
    rec = CaseRecord(case_id="gc_q01", workflow="question_gen",
                     quality={"status": "completed", "total": 2})
    run_id = persist_records(cases, [rec], "sha", db_path=db_path)

    conn = _db.connect(db_path)
    try:
        row = conn.execute("SELECT metrics FROM eval_runs WHERE run_id=?", (run_id,)).fetchone()
        m = json.loads(row["metrics"])
        assert m["quality"]["status"] == "completed"
        assert m["quality"]["total"] == 2
    finally:
        conn.close()


def test_write_report_mode_reflects_real_mode(tmp_path):
    """报告 JSON 的 mode 必须是实际执行档位（真实 vs Fake 严格区分的事实源）。"""
    from backend.app.eval.aggregate import write_report

    p = write_report([], [], {}, "sha", "run_online", "commit",
                     mode="online", out_dir=tmp_path)
    assert json.loads(p.read_text(encoding="utf-8"))["mode"] == "online"

    p2 = write_report([], [], {}, "sha", "run_fake", "commit", out_dir=tmp_path)
    assert json.loads(p2.read_text(encoding="utf-8"))["mode"] == "fake"
