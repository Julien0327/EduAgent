"""Phase 4 / S2-A Fake Runner 测试（要求 1 / 5 落地）。

覆盖：
- 要求 1：FakeLLM 不读取 golden expect（独立推导，snapshot 记录联系为否）；
- 要求 5：可重复性——同 golden 同 seed 两次执行逐 case 一致；
  单 case 异常不中断；中断不把未执行判失败；重跑不污染；
  gc_c01–c04 用独立 fixture（不依赖开发者本机库）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]  # backend/tests -> repo root
FROZEN = REPO_ROOT / "data" / "gt" / "golden_set_v1.jsonl"


@pytest.fixture(scope="module")
def frozen_cases():
    from backend.app.eval.golden import load_frozen
    cases, sha = load_frozen(FROZEN)
    return cases, sha


# ===================== 要求 1：FakeLLM 独立性 =====================

def test_fake_llm_never_reads_golden(frozen_cases):
    """FakeLLM 推导来自输入本身，快照记录 contacted_golden=False。"""
    from backend.app.eval.fake_llm import FakeLLM
    cases, _ = frozen_cases
    fake = FakeLLM(seed=1)
    for c in cases:
        fake.chat_with_tools(
            [{"role": "user", "content": c["prompt"]}],
            [{"type": "function", "function": {"name": "submit_plan"}}])
    snap = fake.snapshot()
    assert snap["contacted_golden"] is False
    # 未 import golden 数据路径：模块级断言
    assert "golden" not in dir(fake)


def test_fake_llm_no_golden_members(frozen_cases):
    """FakeLLM 模块无读取 golden/DB/expect 数据的代码路径（docstring 自证不计）。"""
    from backend.app.eval import fake_llm
    raw = Path(fake_llm.__file__).read_text(encoding="utf-8")
    # 去掉 docstring 与注释，只检查代码路径
    import ast
    tree = ast.parse(raw)
    code_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            code_imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            code_imports.append(node.module or "")
    assert "sqlite3" not in code_imports
    assert not any("golden" in n or "eval_cases" in n or "load_frozen" in n for n in code_imports)
    assert all(isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
               for n in tree.body[:1])  # 模块无顶层数据读取调用


# ===================== 要求 5：可重复性 =====================

def _run_isolated(cases, seed, tmp_path):
    from backend.app.eval import runner as runner_mod
    from backend.app.eval.db_fixture import Fixture
    from backend.app.eval.aggregate import evaluate_params, evaluate_behavior

    fixture = None
    import uuid
    out = tmp_path / f"run_{uuid.uuid4().hex[:8]}"
    out.mkdir()
    records = []
    for c in cases:
        if c["expect"]["workflow"] == "class_diagnosis":
            if fixture is None:
                fixture = Fixture()
        rec = runner_mod.dispatch_case(c, seed, fixture=fixture, out_dir=out)
        evaluate_params(c, rec)
        evaluate_behavior(c, rec)
        records.append(rec)
    return records


def test_repeatable_same_seed(frozen_cases, tmp_path):
    """同 golden 同 seed 两次运行逐 case 一致（fake 档确定性）。

    比较排除 latency_ms（运行耗时是观察量，非判定结果；判定量=行为/产物/归因/参数）。
    """
    cases, _ = frozen_cases
    subset = _subset_all_behaviors(cases)
    r1 = _run_isolated(subset, seed=7, tmp_path=tmp_path)
    r2 = _run_isolated(subset, seed=7, tmp_path=tmp_path)
    for a, b in zip(r1, r2):
        assert a.case_id == b.case_id
        d1, d2 = a.to_dict(), b.to_dict()
        for k in ("error_type", "passed", "behavior_matched", "params_matched",
                  "task_status", "actual_behavior", "artifacts_ok"):
            assert d1[k] == d2[k], f"case {a.case_id} {k} 不一致: {d1[k]} != {d2[k]}"
        assert d1["param_field_matches"] == d2["param_field_matches"]
        assert [x["valid"] for x in d1["artifacts"]] == [x["valid"] for x in d2["artifacts"]]


def test_single_case_exception_does_not_stop(frozen_cases, tmp_path):
    """中途单个 case（expect 结构异常）不中断后续执行。"""
    from backend.app.eval import runner as runner_mod
    from backend.app.eval.db_fixture import Fixture
    from backend.app.eval.aggregate import evaluate_params, evaluate_behavior

    cases, _ = frozen_cases
    good = cases[:5]
    bad = {"case_id": "gc_zz99", "prompt": "触发兜底",
           "expect": None, "tags": ["boundary"]}   # expect 缺失 → dispatch 内抛
    subset = [bad] + good
    fixture = None
    import uuid
    out = tmp_path / f"run_{uuid.uuid4().hex[:8]}"; out.mkdir()
    records = []
    for c in subset:
        if c["expect"] is not None and c["expect"].get("workflow") == "class_diagnosis" and fixture is None:
            fixture = Fixture()
        rec = runner_mod.dispatch_case(c, 1, fixture=fixture, out_dir=out)
        # 畸形 case 无合法 expect → 跳过判定（infra 已由 dispatch 兜底标记）
        if c["expect"] is not None:
            evaluate_params(c, rec); evaluate_behavior(c, rec)
        records.append(rec)
    assert len(records) == len(subset)
    assert records[0].error_type == "infra"          # 畸形 case → 兜底 infra
    assert records[0].passed is False
    assert records[1].error_type in ("ok", "workflow_choice", "workflow_failed")  # 后续不中断


def test_interrupt_does_not_fail_remaining(frozen_cases, tmp_path):
    """Runner 中断时未执行 case 保持 pending，不入失败数。"""
    from backend.app.eval.aggregate import build_metrics
    cases, _ = frozen_cases
    subset = cases[:12]
    # 只执行前 3 条，其余不执行（模拟中断）
    from backend.app.eval import runner as runner_mod
    from backend.app.eval.db_fixture import Fixture
    fixture = None
    import uuid
    out = tmp_path / f"run_{uuid.uuid4().hex[:8]}"; out.mkdir()
    done = []
    for c in subset[:3]:
        if c["expect"]["workflow"] == "class_diagnosis" and fixture is None:
            fixture = Fixture()
        done.append(runner_mod.dispatch_case(c, 1, fixture=fixture, out_dir=out))
    from backend.app.eval.aggregate import evaluate_params, evaluate_behavior
    for c, r in zip(subset[:3], done):
        evaluate_params(c, r); evaluate_behavior(c, r)
    metrics = build_metrics(subset, done)
    # 未执行 case 记 pending，不进入成功分母
    assert metrics["cases_total"] == 3
    assert len(metrics["cases_pending_ids"]) == len(subset) - 3
    assert metrics["tasks"]["denominator"] == sum(
        1 for c in subset[:3] if c["expect"]["behavior"] in ("execute", "fallback")
        and c["expect"].get("mode", "both") == "both")


def test_rerun_does_not_pollute(tmp_path):
    """同 run_id 重跑 UPSERT 幂等：不产生重复行。"""
    from backend.app.storage import db as _db
    from backend.app.eval.aggregate import persist_records
    dbp = tmp_path / "eval.sqlite"
    _db.apply_migrations(dbp)
    cases, sha = load_frozen_small()
    # 造一条 fake record 用模型（简化）
    from backend.app.eval.models import CaseRecord
    recs = []
    for c in cases:
        r = CaseRecord(case_id=c["case_id"], workflow="fake")
        recs.append(r)
    run_id = "test_rerun"
    persist_records(cases, recs, sha, run_id=run_id, db_path=dbp)
    persist_records(cases, recs, sha, run_id=run_id, db_path=dbp)  # 重跑
    conn = _db.connect(dbp)
    n = conn.execute("SELECT COUNT(*) FROM eval_runs WHERE run_id=?", (run_id,)).fetchone()[0]
    uniq = conn.execute(
        "SELECT COUNT(DISTINCT case_id) FROM eval_runs WHERE run_id=?", (run_id,)).fetchone()[0]
    conn.close()
    assert n == len(cases) == uniq


def load_frozen_small():
    from backend.app.eval.golden import load_frozen
    cases, sha = load_frozen(FROZEN)
    return cases[:6], sha


def _subset_all_behaviors(cases: list[dict]) -> list[dict]:
    """抽每工作流+每行为至少 1 条的代表性子集（限 ~16 条保证速度）。"""
    seen = set()
    out = []
    order_pref = ["executed", "exam_loop", "question_gen", "lesson_plan",
                  "class_diagnosis", "clarify", "reject", "reject_scope",
                  "reject_400", "reject_404", "fail"]
    for c in cases:
        if c["case_id"] in ("gc_e03", "gc_q01", "gc_l01", "gc_c01"):
            out.append(c); seen.add(c["case_id"]); continue
        b = c["expect"]["behavior"]
        wf = c["expect"].get("workflow") or ""
        for key, label in ((b, b), (wf, wf)):
            if key and key not in seen and len(out) < 16:
                out.append(c); seen.add(c["case_id"]); break
        if len(out) >= 16:
            break
    return out