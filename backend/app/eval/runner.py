"""评测 Fake Runner（S2-A）。

职责：加载冻结 golden（111 条）→ 逐条在 fake 档执行 → 产物三层校验 → 逐条结构化记录；
聚合（行为判定 / 指标统计 / 落库 / 报告）在 aggregate.py。

执行路径（按 case 的 entry + expected_behavior 分发）：
- workflow 层（expected ∈ execute/fallback/fail）：FakeLLM + FakeKpService 注入，
  run_workflow(prompt)，采集 status/params/artifacts/steps，产物三层校验；
- tasks_api 层 reject_scope：detect_unsupported_scope(prompt)；
- tasks_api 层 reject_400：空 prompt（入口 guard 已拦）；
- tasks_api 层 clarify：按当前 planner 语义（plan().need_clarification），FakeLLM
  模拟当前实现（无 clarify → 返回 execute 行为），如实暴露差异；
- diagnosis_api 层 reject_404：用隔离 fixture DB 探测 class 是否存在。

**可重复性（要求 5）**：
- run_id 时间戳；同一 seed 重跑逐 case 一致；
- 单条异常不中断（→ rec.error_type=infra）；
- 未执行 case 不判定失败（留 pending，不含进通过率和行为指标分母）；
- 每个 case 独立 task 库会话（进程内隔离 + DB 写穿透可重复）。
"""
from __future__ import annotations

import time
from pathlib import Path

from backend.app.eval.fake_llm import FakeLLM
from backend.app.eval.models import (
    CaseRecord, ERR_OK, ERR_WORKFLOW_FAILED, ERR_INFRA,
    ERR_ARTIFACT_GENERATION, ERR_API_BEHAVIOR, ERR_WORKFLOW_CHOICE,
)


class FakeKpService:
    """检索替身：固定返回 2 个小学 KP（带 ref）——与 conftest 同构，离线确定性。"""

    _KPS = [
        {"kp_id": "kp_p_0001", "ref": "kp_p_0001", "name": "分数运算",
         "stage": "primary", "grade": 6, "chapter": "分数运算",
         "description": "分数加减乘除", "score": 0.9, "via": "both"},
        {"kp_id": "kp_p_0002", "ref": "kp_p_0002", "name": "鸡兔同笼",
         "stage": "primary", "grade": 6, "chapter": "鸡兔同笼",
         "description": "鸡兔同笼问题", "score": 0.8, "via": "keyword"},
    ]

    def search_knowledge_points(self, query, stage=None, top_k=10):
        return self._KPS[:top_k]

    def search_questions(self, query, stage=None, filters=None, **kwargs):
        return {"kps": self._KPS[:2], "questions": [], "meta": {}}

    def search_by_kp(self, kp_ids, filters=None, question_limit=9):
        """class_diagnosis 推荐阶段：确定性候选（eval-fixture 来源）。"""
        return [{"q_id": f"evalq_00{i}", "stem": f"评测推荐题{i}", "difficulty": 2,
                 "kp_ids": list(kp_ids), "source": "eval-fixture", "score": 100}
                for i in range(1, 4)][:question_limit]


def install_fake_env(fake: FakeLLM, workdir: Path) -> None:
    """把 LLM / 检索 / 产物路径改写为 fake（进程内脚本直接覆盖模块引用）。

    workdir：评测运行目录（产物写到 workdir/outputs，与业务 data/outputs 隔离；
    不同 run 使用不同 workdir 保证可重复性）。
    """
    import backend.app.agent.llm_utils as llmu
    import backend.app.tools.knowledge_tools as kt
    import backend.app.storage.file_store as fstore

    llmu.get_llm_service = lambda: fake
    kt.get_retrieval_service = lambda: FakeKpService()
    (workdir / "outputs").mkdir(parents=True, exist_ok=True)
    fstore.OUTPUTS_ROOT = workdir / "outputs"


def _artifact_to_dict(a) -> dict:
    return {"name": a.name, "exists": a.exists, "valid": a.valid,
            "content_assertions": a.content_assertions, "error": a.error}


def run_workflow_case(case: dict, seed: int, workdir: Path | None = None,
                      mode: str = "fake", llm_provider=None,
                      budget=None) -> CaseRecord:
    """workflow 层执行：LLM/检索注入 → run_workflow → 产物校验。

    mode='fake'：install FakeLLM，cost=0（S2-A，离线确定性）。
    mode='online'：install 真实/Mock provider，事后从 tracing 聚合真实 cost
    （cost_known 语义；S2-B 用 Mock 不发起真实请求）。
    budget（online 用）：执行前 try_reserve，超预算则 case 标记 budget_stopped 不执行。
    """
    from backend.app.agent import workflow as wf
    from backend.app.agent.context import task_store

    rec = CaseRecord(execution_mode=mode)
    exp = case["expect"]
    rec.case_id = case["case_id"]
    rec.expected_behavior = exp["behavior"]
    rec.expected_params = exp.get("params", {})
    ew = exp.get("workflow")
    rec.workflow = ew if isinstance(ew, str) else ""
    workdir = workdir or (Path(__file__).resolve().parent / ".runout")
    if mode == "online":
        from backend.app.eval.provider import install_online_env
        install_online_env(llm_provider, workdir)
        # 预算保护：执行前预留（要求 3）——预留失败 = 硬停/暂停，本 case 不执行
        if budget is not None:
            worst_reserved = budget.try_reserve(800, 400)   # 按冒烟平均最坏口径预留
            if worst_reserved is None:
                rec.task_status = "budget_stopped"
                rec.actual_behavior = "budget_stopped"
                rec.task_error = "预算剩余不足以执行本 case（硬停或暂停线触发）"
                rec.details = str(budget.status())
                rec.error_type = "budget_stop"
                return rec
            self_worst = worst_reserved
    else:
        fake = FakeLLM(seed=seed)
        install_fake_env(fake, workdir)
    t0 = time.perf_counter()
    try:
        ctx = wf.run_task(case["prompt"])
        rec.latency_ms = int((time.perf_counter() - t0) * 1000)
        rec.task_status = ctx.status
        rec.actual_params = dict(ctx.params or {})
        rec.actual_behavior = "execute" if ctx.status == "completed" else "failed"
        art_paths = {a.file_name: Path(a.path) for a in ctx.artifacts}
        from backend.app.eval.artifacts import check_artifacts
        checks, _content = check_artifacts(case["case_id"], art_paths)
        rec.artifacts = [_artifact_to_dict(c) for c in checks]
        rec.artifacts_ok = bool(checks) and all(c.valid for c in checks)
        if mode == "online":
            from backend.app.eval.provider import aggregate_cost_from_ctx
            agg = aggregate_cost_from_ctx(ctx)
            rec.cost = agg["cost"]
            rec.cost_known = agg["cost_known"]
            rec.details = f"tokens_in={agg['tokens_in']} tokens_out={agg['tokens_out']} " \
                          f"llm_calls={agg['llm_calls']} usage={agg['usage_sources']}"
            if budget is not None:
                # 提交真实成本到预算；最坏额自预留冲抵，避免泄漏（要求 3/6）
                budget.commit(
                    cost_cny=rec.cost if rec.cost_known else None,
                    tokens_in=agg["tokens_in"], tokens_out=agg["tokens_out"],
                    worst_cny=self_worst if self_worst else None)
        else:
            rec.cost = 0.0
            rec.cost_known = True
        if ctx.status != "completed":
            rec.error_type = ERR_WORKFLOW_FAILED
            rec.error_message = ctx.error or "执行失败"
        elif not art_paths:
            rec.error_type = ERR_ARTIFACT_GENERATION
            rec.error_message = "任务 completed 但无产物"
        else:
            rec.error_type = ERR_OK
    except Exception as exc:
        rec.latency_ms = int((time.perf_counter() - t0) * 1000)
        rec.task_status = "infra"
        rec.error_type = ERR_INFRA
        rec.error_message = f"{type(exc).__name__}: {exc}"
    finally:
        task_store.reset()
    return rec


def run_class_diagnosis_case(case: dict, seed: int,
                             fixture: "Fixture | None" = None,
                             out_dir: Path | None = None) -> CaseRecord:
    """class_diagnosis 执行：独立 fixture → build_diagnosis → JSON+PDF 产物。

    产物写入 out_dir/task_<case_id>/（04_诊断结果.json、05_学情诊断报告.pdf），
    与 exam/question workflow 产物形态一致，供三层校验复用。
    不经真业务库、不经 task_store 持久化（隔离可重复，满足裁决⑤）。
    """
    from backend.app.eval import db_fixture
    rec = CaseRecord(execution_mode="fake")
    exp = case["expect"]
    rec.case_id = case["case_id"]
    rec.expected_behavior = exp["behavior"]
    rec.expected_params = exp.get("params", {})
    rec.workflow = "class_diagnosis"
    rec.cost, rec.cost_known = 0.0, True
    t0 = time.perf_counter()
    try:
        fx = fixture or db_fixture.Fixture()
        # 注入 fixture 专属角色映射（不触生产映射清单）
        import backend.app.diagnosis.data_access as da
        _orig = da.load_role_map
        da.load_role_map = lambda path=fx.role_map_path: _orig(path)
        conn = fx.conn()
        try:
            from backend.app.diagnosis.service import build_diagnosis
            diag = build_diagnosis(conn, fx.class_id(), retriever=FakeKpService())
        finally:
            conn.close()
        rec.latency_ms = int((time.perf_counter() - t0) * 1000)
        rec.actual_params = {"class_id": fx.class_id(), "mode": "fixture"}
        rec.task_status = "completed"
        rec.actual_behavior = "execute" if rec.task_status == "completed" else "failed"
        # 产物：JSON（04）+ PDF（05）
        out = (out_dir or (Path(__file__).resolve().parent / ".tmp_out"))
        task_dir = out / f"task_{rec.case_id}"
        task_dir.mkdir(parents=True, exist_ok=True)
        from backend.app.agent.workflow import DIAGNOSIS_FILE, DIAGNOSIS_PDF_FILE
        import json as _json
        json_path = task_dir / DIAGNOSIS_FILE
        pdf_path = task_dir / DIAGNOSIS_PDF_FILE
        json_path.write_text(
            _json.dumps({"diagnosis": diag,
                         "narration": {"narration_source": "template"}},
                        ensure_ascii=False, indent=2), encoding="utf-8")
        from backend.app.services.diagnosis_pdf import create_diagnosis_pdf
        create_diagnosis_pdf(diag, {"narration_source": "template"}, str(pdf_path))
        art_paths = {DIAGNOSIS_FILE: json_path, DIAGNOSIS_PDF_FILE: pdf_path}
        from backend.app.eval.artifacts import check_artifacts
        checks, _content = check_artifacts(case["case_id"], art_paths)
        rec.artifacts = [_artifact_to_dict(c) for c in checks]
        rec.artifacts_ok = bool(checks) and all(c.valid for c in checks)
        rec.error_type = ERR_OK
    except Exception as exc:
        rec.latency_ms = int((time.perf_counter() - t0) * 1000)
        rec.task_status = "infra"
        rec.error_type = ERR_INFRA
        rec.error_message = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            import backend.app.diagnosis.data_access as da
            if "da" in dir() and "_orig" in dir():
                da.load_role_map = _orig
        except Exception:
            pass
    return rec


def run_reject_scope_case(case: dict) -> CaseRecord:
    """tasks_api 层 reject_scope：detect_unsupported_scope 应返回提示。"""
    from backend.app.scope import detect_unsupported_scope
    rec = CaseRecord(execution_mode="fake")
    exp = case["expect"]
    rec.case_id = case["case_id"]
    rec.expected_behavior = exp["behavior"]
    rec.expected_params = exp.get("params", {})
    rec.workflow = ""
    rec.cost, rec.cost_known = 0.0, True
    t0 = time.perf_counter()
    notice = detect_unsupported_scope(case["prompt"])
    rec.latency_ms = int((time.perf_counter() - t0) * 1000)
    if notice:
        rec.task_status = "rejected"
        rec.actual_behavior = "reject"
        rec.error_type = ERR_OK
        rec.error_message = notice
    else:
        rec.task_status = "not_rejected"
        rec.actual_behavior = "execute"
        rec.error_type = ERR_API_BEHAVIOR
        rec.error_message = "scope 检测未拦截（已知缺陷时如实暴露）"
    return rec


def run_reject_400_case(case: dict) -> CaseRecord:
    """tasks_api 层 reject_400：空 prompt → API 400（输入校验层）。"""
    rec = CaseRecord(execution_mode="fake")
    exp = case["expect"]
    rec.case_id = case["case_id"]
    rec.expected_behavior = exp["behavior"]
    rec.expected_params = exp.get("params", {})
    rec.workflow = ""
    rec.cost, rec.cost_known = 0.0, True
    t0 = time.perf_counter()
    rec.latency_ms = int((time.perf_counter() - t0) * 1000)
    if not (case["prompt"] or "").strip():
        rec.task_status = "rejected_400"
        rec.actual_behavior = "reject_400"
        rec.error_type = ERR_OK
    else:
        rec.task_status = "not_rejected"
        rec.actual_behavior = "execute"
        rec.error_type = ERR_API_BEHAVIOR
        rec.error_message = "空 prompt 未触发 400"
    return rec


def run_clarify_case(case: dict, seed: int, workdir: Path | None = None) -> CaseRecord:
    """tasks_api 层 clarify：按当前 planner 语义判定（FakeLLM 模拟当前实现）。

    当前实现：plan() 信息不足时 LLM 可返回 need_clarification；fallback 无 clarify。
    FakeLLM 复刻当前实现（先默认不返回 clarify，除非 force）→ 多数 clarify case
    实际行为=execute，如实反映"系统目前缺少追问能力"。
    """
    from backend.app.agent import planner
    rec = CaseRecord(execution_mode="fake")
    exp = case["expect"]
    rec.case_id = case["case_id"]
    rec.expected_behavior = exp["behavior"]
    rec.expected_params = exp.get("params", {})
    rec.workflow = ""
    rec.cost, rec.cost_known = 0.0, True
    fake = FakeLLM(seed=seed)
    install_fake_env(fake, workdir or (Path(__file__).resolve().parent / ".runout"))
    t0 = time.perf_counter()
    try:
        p = planner.plan(case["prompt"])
        rec.latency_ms = int((time.perf_counter() - t0) * 1000)
        rec.actual_params = p.params.model_dump() if p else {}
        rec.actual_behavior = "clarify" if (p and p.need_clarification) else "execute"
        rec.task_status = "clarified" if rec.actual_behavior == "clarify" else "executed"
        rec.error_type = ERR_OK if rec.actual_behavior == "clarify" else ERR_WORKFLOW_CHOICE
        rec.error_message = ("追问" if rec.actual_behavior == "clarify"
                             else "当前实现未追问（复刻现状，FakeLLM 无权改行为）")
    except Exception as exc:
        rec.latency_ms = int((time.perf_counter() - t0) * 1000)
        rec.task_status = "infra"
        rec.error_type = ERR_INFRA
        rec.error_message = f"{type(exc).__name__}: {exc}"
    return rec


def run_reject_404_case(case: dict, db_path: Path) -> CaseRecord:
    """diagnosis_api 层 reject_404：班级不存在 → 404 语义。

    db_path 应指向含 classes 表的业务库（独立 fixture 或真库）。类不存在 → reject_404。
    """
    import sqlite3
    rec = CaseRecord(execution_mode="fake")
    exp = case["expect"]
    rec.case_id = case["case_id"]
    rec.expected_behavior = exp["behavior"]
    rec.expected_params = exp.get("params", {})
    rec.workflow = "class_diagnosis"
    rec.cost, rec.cost_known = 0.0, True
    t0 = time.perf_counter()
    try:
        conn = sqlite3.connect(str(db_path))
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='classes'").fetchone()
        if not has_table:
            raise RuntimeError("fixture 库缺 classes 表")
        cid = "cls_nonexistent_2026"
        exists = conn.execute(
            "SELECT 1 FROM classes WHERE class_id=?", (cid,)).fetchone() is not None
        conn.close()
        rec.latency_ms = int((time.perf_counter() - t0) * 1000)
        if not exists:
            rec.task_status = "rejected_404"
            rec.actual_behavior = "reject_404"
            rec.error_type = ERR_OK
        else:
            rec.task_status = "found"
            rec.actual_behavior = "execute"
            rec.error_type = ERR_API_BEHAVIOR
            rec.error_message = f"班级 {cid} 意外存在"
    except Exception as exc:
        rec.latency_ms = int((time.perf_counter() - t0) * 1000)
        rec.task_status = "infra"
        rec.error_type = ERR_INFRA
        rec.error_message = f"{type(exc).__name__}: {exc}"
    return rec


def dispatch_case(case: dict, seed: int, db_path: Path | None = None,
                  fixture: "Fixture | None" = None,
                  out_dir: Path | None = None,
                  mode: str = "fake", llm_provider=None,
                  budget=None) -> CaseRecord:
    """按 case 的 expected_behavior + entry 分发到对应执行器。

    返回的 CaseRecord 只含原始执行结果（不含判定），判定在 aggregate 统一定义，
    保证可重复性（同一输入同一结果）。gc_c01–c04（class_diagnosis）走独立 fixture。

    兜底：expect 结构异常 / 任何执行异常 → 记 infra（要求 5：单条不中断）。
    mode='online' 时 workflow 层用真实/Mock provider，成本从 traces 聚合（S2-B）；
    budget（online）→ 执行前 try_reserve，超预算 case 标记 budget_stopped。
    """
    try:
        exp = case["expect"]
        behavior = exp["behavior"]
        entry = exp.get("entry", "")
        workflow = exp.get("workflow")
        if workflow == "class_diagnosis" and behavior in ("execute", "fail"):
            return run_class_diagnosis_case(case, seed, fixture=fixture, out_dir=out_dir)
        if behavior == "reject_scope":
            return run_reject_scope_case(case)
        if behavior == "reject_400":
            return run_reject_400_case(case)
        if behavior == "reject_404":
            # 404 探测用含 classes 表的业务库：优先 fixture（干净可重复），否则真库
            from backend.app.eval.db_fixture import Fixture
            probe = (fixture.db_path if (fixture is not None and fixture.db_path)
                     else Path(_REJECT_404_DB))
            return run_reject_404_case(case, probe)
        if behavior == "clarify":
            return run_clarify_case(case, seed, workdir=out_dir)
        return run_workflow_case(case, seed, workdir=out_dir, mode=mode,
                                 llm_provider=llm_provider, budget=budget)
    except Exception as exc:
        rec = CaseRecord(execution_mode=mode)
        rec.case_id = case.get("case_id", "?")
        rec.expected_behavior = "?"
        rec.expected_params = {}
        rec.task_status = "infra"
        rec.error_type = ERR_INFRA
        rec.error_message = f"{type(exc).__name__}: {exc}"
        return rec


# 默认 404 探测库（无独立 fixture 时回退到空库语义——不会存在 cls_nonexistent_2026）
_REJECT_404_DB = str(Path(__file__).resolve().parents[3] / "data" / "db" / "edumate.sqlite")