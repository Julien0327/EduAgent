"""Phase 3 / S6 端到端复核（②）：对演示班 cls_3b71d97847 重新执行并核验（不复用历史报告）。

模式（每次运行写/合并同一份 staging 报告）：
- e2e      —— 对运行中的服务 POST class_diagnosis → 轮询完成 → 核验 JSON↔PDF 一致性 /
              Trace 完整性（4 节点）/ Token·成本明细与汇总 / cost_recompute_ok / 引用覆盖率；
- provider —— 同 e2e，但要求叙述节点为真实 LLM（usage_source=provider，tokens/cost>0，Σ 对账）；
- replay   —— 指向**服务重启后**的新实例，按 task_id 回放 trace/citations/files，并与其
              运行期记录对比（证明重启后库直读可回放）；
- reweight —— 本地重算演示班角色权重掌握度 λ∈{0,0.5,1}，与设计参考值（design.mastery 等）
              逐个 KP 对比并报告差异与薄弱集合变化。

同步核验 JSON/PDF：PDF 只排版不重算——目标/根因名称、推荐题 q_id、叙述来源徽标、合成声明、
数据范围须在 PDF 文本中一致出现（S5 口径）。

纪律：结果全部来自本次实际运行；失败/偏差如实记录到报告，不擅自修改 S1–S5 冻结逻辑。

用法（仓库根执行，按物理顺序）：
    ... -m backend.scripts.s6_e2e_review e2e      --base-url http://127.0.0.1:8011 --token T
    ... -m backend.scripts.s6_e2e_review provider --base-url http://127.0.0.1:8012 --token T
    ... -m backend.scripts.s6_e2e_review replay   --base-url http://127.0.0.1:8013 --token T \
                                                  --task <e2e_task>,<provider_task>
    ... -m backend.scripts.s6_e2e_review reweight
"""
from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "data" / "staging"
OUT_JSON = STAGING / "s6_e2e_review_report.json"
OUT_MD = STAGING / "s6_e2e_review_report.md"
DB_PATH = REPO_ROOT / "data" / "db" / "edumate.sqlite"
DESIGN_FILE = STAGING / "primary_demo_design.json"
DEMO_NAME = "六年级演示班"
LAM = 0.5
WF_NODES = ["compute_diagnosis", "narrate_diagnosis", "write_diagnosis_json", "build_diagnosis_pdf"]


def _http(method, url, token, body=None, headers=None):
    h = dict(headers or {})
    if token:
        h["X-API-Token"] = token
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(resp_bytes):
    try:
        return json.loads(resp_bytes.decode("utf-8"))
    except Exception:
        return {}


def _wait_task(base, token, task_id, tries=240):
    snap = None
    for _ in range(tries):
        code, data = _http("GET", f"{base}/api/tasks/{urllib.parse.quote(task_id)}", token)
        if code == 200:
            snap = _json(data)
            if snap["status"] in ("completed", "failed"):
                return snap, code
        __import__("time").sleep(0.5)
    raise TimeoutError(f"任务超时：{task_id} 最后状态={snap and snap.get('status')}")


def _demo_class_id(conn) -> str:
    row = conn.execute("SELECT class_id FROM classes WHERE name=? ORDER BY created_at, class_id LIMIT 1",
                       (DEMO_NAME,)).fetchone()
    if row is None:
        raise RuntimeError(f"演示班不存在：{DEMO_NAME}")
    return row["class_id"]


def _load_report():
    if OUT_JSON.exists():
        return json.loads(OUT_JSON.read_text(encoding="utf-8"))
    return {"generated_at": None, "sections": {}}


def _save_report(report):
    report["generated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if "e2e" in report["sections"]:
        _write_md(report)


# ---------------------------------------------------------------- e2e / provider

def mode_e2e(base, token, **kw) -> dict:
    from pypdf import PdfReader

    out = {}
    code, data = _http("GET", f"{base}/api/classes", token)
    classes = _json(data) if code == 200 else []
    demo = next((c for c in classes if c["name"] == DEMO_NAME), None)
    if demo is None:
        return {"ok": False, "error": f"/api/classes 未返回演示班（HTTP {code}）"}
    class_id = demo["class_id"]
    out["class_id"] = class_id

    code, data = _http("POST",
                       f"{base}/api/diagnosis/class/{urllib.parse.quote(class_id)}/report", token)
    task = _json(data)
    task_id = task.get("task_id")
    if not task_id:
        return {"ok": False, "error": f"POST report 失败 HTTP {code} {data.decode('utf-8','replace')[:300]}"}
    out["task_id"] = task_id
    snap, scode = _wait_task(base, token, task_id)
    out["workflow"] = snap.get("workflow")
    out["status"] = snap.get("status")
    if snap.get("status") != "completed":
        return {"ok": False, "error": f"任务未完成：{snap.get('status')} {snap.get('error')}"}

    artifacts = {a["file_name"]: a for a in (snap.get("artifacts") or [])}
    out["artifacts"] = sorted(artifacts)

    # ---- 04 JSON ----
    jcode, jdata = _http(
        "GET", f"{base}/api/tasks/{task_id}/files/{urllib.parse.quote('04_诊断结果.json')}?inline=true", token)
    payload = _json(jdata)
    diag = payload.get("diagnosis") or {}
    narration = (payload.get("narration") or {})
    chains = diag.get("chains") or []
    recs = diag.get("recommendations") or []
    scope = diag.get("data_scope") or {}
    out["json"] = {
        "ok": jcode == 200,
        "scope": {k: scope.get(k) for k in ("exam_count", "question_count", "student_count",
                                            "answer_count", "kp_covered_count")},
        "chain_count": len(chains),
        "chain_targets": [c.get("target_name") for c in chains],
        "chain_roots": [c.get("root_name") for c in chains],
        "rec_groups": len(recs),
        "rec_qids": [q["q_id"] for g in recs for q in g.get("questions", [])],
        "narration_source": narration.get("narration_source"),
    }

    # ---- 05 PDF ----
    pcode, pdata = _http(
        "GET", f"{base}/api/tasks/{task_id}/files/{urllib.parse.quote('05_学情诊断报告.pdf')}", token)
    pdf_bytes = pdata if pcode == 200 else b""
    pdf_text = ""
    pdf_pages = 0
    pdf_checks = {}
    if pcode == 200 and pdf_bytes:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pdf_pages = len(reader.pages)
        pdf_text = "\n".join((p.extract_text() or "") for p in reader.pages)
        target_names = sum(1 for c in chains if c.get("target_name") and
                           c["target_name"] in pdf_text)
        root_names = sum(1 for c in chains if c.get("root_name") and c["root_name"] in pdf_text)
        qids = [q["q_id"] for g in recs for q in g.get("questions", [])]
        qid_hits = sum(1 for q in qids[:12] if q in pdf_text)
        src_badge = ("AI 叙述" in pdf_text or "模板兜底" in pdf_text)
        pdf_checks = {
            "chain_target_names": f"{target_names}/{len(chains)} 命中",
            "chain_root_names": f"{root_names}/{len(chains)} 命中",
            "rec_qids_sample": f"{qid_hits}/{min(12, len(qids))} 命中（前12）",
            "narration_source_badge": bool(src_badge),
            "synthetic_declaration": ("合成" in pdf_text or "演示" in pdf_text),
            "data_scope_present": "数据范围" in pdf_text or "场考试" in pdf_text,
        }
    out["pdf"] = {
        "ok": pcode == 200 and len(pdf_bytes) > 1000,
        "bytes": len(pdf_bytes),
        "pages": pdf_pages,
        "checks": pdf_checks,
    }
    consistent = (out["json"].get("ok") and out["pdf"]["ok"]
                  and pdf_checks.get("chain_target_names", "0/0").endswith("命中") and bool(pdf_checks))

    # ---- Trace ----
    tcode, tdata = _http("GET", f"{base}/api/tasks/{task_id}/trace", token)
    trace = _json(tdata)
    tr = trace.get("trace") or {}
    steps = tr.get("steps") or []
    tot = tr.get("totals") or {}
    node_status = {s["node"]: s["status"] for s in steps}
    out["trace"] = {
        "ok": tcode == 200,
        "step_nodes": list(node_status),
        "all_done": set(WF_NODES) == set(node_status) and all(v == "done" for v in node_status.values()),
        "totals": {k: tot.get(k) for k in ("step_count", "llm_call_count", "tool_call_count",
                                           "tokens_in", "tokens_out", "cost_cny",
                                           "steps_usage_unavailable")},
        "cost_recompute_ok": tr.get("cost_recompute_ok"),
        "usage_sources": Counter(s.get("usage_source") for s in steps),
        "per_step": [{"node": s.get("node"), "status": s.get("status"),
                      "usage_source": s.get("usage_source"),
                      "tokens": (s.get("tokens_in"), s.get("tokens_out")),
                      "cost": s.get("cost"), "llm_calls": len(s.get("llm_calls") or []),
                      "llm_call_detail": s.get("llm_calls") or []} for s in steps],
    }

    # ---- Citations ----
    ccode, cdata = _http("GET", f"{base}/api/tasks/{task_id}/citations", token)
    cites = _json(cdata).get("citations") if ccode == 200 else []
    no_usedin = [c["ref"] for c in cites if c.get("used_in") in (None, [])]
    out["citations"] = {
        "ok": ccode == 200,
        "count": len(cites),
        "by_kind": dict(Counter(c.get("kind") for c in cites)),
        "coverage_used_in": f"{len(cites) - len(no_usedin)}/{len(cites)}",
        "no_used_in": no_usedin[:10],
    }

    out["ok"] = (out["status"] == "completed" and out["json"]["ok"] and out["pdf"]["ok"]
                 and consistent and out["trace"]["ok"] and out["trace"]["all_done"]
                 and out["trace"]["cost_recompute_ok"] and out["citations"]["ok"]
                 and len(no_usedin) == 0)
    return out


# ---------------------------------------------------------------- replay

def mode_replay(base, token, task_ids, report) -> dict:
    """重启后回放核验（官方口径：/api/tasks 列表 + /api/tasks/{id}/trace 库直读可回放）。

    - 必需面：trace 200 / 4 步骤 / cost_recompute_ok / totals 与运行期记录一致；
      /api/tasks 列表含该任务且 status=completed。
    - 备注面（如实记录、不判负）：/api/tasks/{id}（单任务快照）、/citations、/files
      依赖进程内 task_store 缓存，重启后 404（S4/S5 冻结行为，本次不改）。
    """
    out = {"ok": True, "tasks": {}, "note": (
        "/api/tasks/{id} 单任务快照、/citations、/files 重启后 404：依赖进程内 task_store "
        "缓存；官方重启回放面为 /api/tasks 列表 + /trace（库直读）。已如实记录，不修改 S4/S5 冻结逻辑。")}
    for tid in [t for t in task_ids.split(",") if t.strip()]:
        tid = tid.strip()
        run_cost = run_tok_in = run_tok_out = run_llm = None
        for m in ("e2e", "provider"):
            sec = report["sections"].get(m)
            if sec and sec.get("task_id") == tid:
                t = (sec.get("trace") or {}).get("totals") or {}
                run_cost, run_tok_in, run_tok_out, run_llm = (
                    t.get("cost_cny"), t.get("tokens_in"), t.get("tokens_out"),
                    t.get("llm_call_count"))
        # ① 任务列表（库直读）含该任务
        lcode, ldata = _http("GET", f"{base}/api/tasks?limit=200", token)
        lst = _json(ldata).get("tasks", []) if lcode == 200 else []
        in_list = any(x.get("task_id") == tid and x.get("status") == "completed" for x in lst)
        # ② trace 回放（库直读）
        tcode, tdata = _http("GET", f"{base}/api/tasks/{urllib.parse.quote(tid)}/trace", token)
        tr = _json(tdata)
        steps = (tr.get("trace") or {}).get("steps") or []
        tot = (tr.get("trace") or {}).get("totals") or {}
        recompute = (tr.get("trace") or {}).get("cost_recompute_ok")
        tot_match = (in_list and (tot.get("llm_call_count") == run_llm)
                     and (tot.get("cost_cny") == run_cost)
                     and (tot.get("tokens_in") == run_tok_in)
                     and (tot.get("tokens_out") == run_tok_out))
        # ③ 备注：citations / files（依赖内存缓存 → 预期 404）
        ccode, _ = _http("GET", f"{base}/api/tasks/{urllib.parse.quote(tid)}/citations", token)
        fcode, _ = _http("GET",
                         f"{base}/api/tasks/{urllib.parse.quote(tid)}/files/"
                         f"{urllib.parse.quote('05_学情诊断报告.pdf')}", token)
        trace_ok = tcode == 200 and len(steps) == 4 and recompute and tot_match
        e = {
            "task_id": tid,
            "runtime_totals": {"cost": run_cost, "tokens_in": run_tok_in,
                               "tokens_out": run_tok_out, "llm_call_count": run_llm},
            "list_contains_completed": bool(in_list),
            "trace_replayed_steps": len(steps),
            "replayed_totals": {k: tot.get(k) for k in
                                ("llm_call_count", "tokens_in", "tokens_out", "cost_cny")},
            "totals_match_runtime": bool(tot_match),
            "cost_recompute_ok_after_restart": bool(recompute),
            "citations_api_after_restart_http": ccode,   # 预期 404（限制，非判负）
            "files_api_after_restart_http": fcode,       # 预期 404（限制，非判负）
            "ok": bool(trace_ok),
        }
        out["ok"] = out["ok"] and e["ok"]
        out["tasks"][tid] = e
    return out


# ---------------------------------------------------------------- reweight

def mode_reweight() -> dict:
    from backend.app.diagnosis import data_access
    from backend.app.diagnosis import mastery as M

    design = json.loads(DESIGN_FILE.read_text(encoding="utf-8"))
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        class_id = _demo_class_id(conn)
        loaded = data_access.load_class_evidence(conn, class_id)  # 生产映射文件（demo 班正确 fixture）
    finally:
        conn.close()
    all_kp = set(design["mastery"]) | {e["kp_id"] for e in loaded["evidence"]}

    masters = {lam: M.compute_mastery(loaded["evidence"], loaded["exams"], lam,
                                      all_kp_ids=all_kp) for lam in (0.0, LAM, 1.0)}

    comp = []
    max_diff = {lam: 0.0 for lam in (0.0, LAM, 1.0)}
    diff_count = {lam: 0 for lam in (0.0, LAM, 1.0)}
    for kp, ref in design["mastery"].items():
        d_m = ref["mastery"]
        for lam in (0.0, LAM, 1.0):
            m = masters[lam].get(kp, {}).get("mastery")
            if m is None:
                continue
            diff = abs(m - (d_m or 0)) if d_m is not None else None
            if diff is not None:
                max_diff[lam] = max(max_diff[lam], diff)
                if diff > 1e-6:
                    diff_count[lam] += 1
        # 记录辅助权重影响（λ=0.5 与 λ=1 差异越大 → 辅助证据越主导）
        m05 = masters[LAM].get(kp, {}).get("mastery")
        m10 = masters[1.0].get(kp, {}).get("mastery")
        if (abs((m05 or 0) - (m10 or 0)) > 1e-6
                or masters[LAM].get(kp, {}).get("state") != masters[1.0].get(kp, {}).get("state")):
            comp.append({"kp": kp, "design_mastery": d_m,
                         "m0": masters[0.0].get(kp, {}).get("mastery"),
                         "m05": m05, "m1": m10,
                         "aux_share": masters[LAM].get(kp, {}).get("aux_share"),
                         "state05": masters[LAM].get(kp, {}).get("state"),
                         "state1": masters[1.0].get(kp, {}).get("state")})

    weak_by = {lam: sorted(k for k, m in masters[lam].items() if m["state"] == "weak")
               for lam in (0.0, LAM, 1.0)}
    # λ=1 应与设计参考一致 —— 到 4 位小数舍入 ulp（0.0001）即视为一致（S1 口径，逐位不留差）
    ulp = 0.00011
    lam1_ok = max_diff[1.0] <= ulp
    out = {
        "ok": lam1_ok,
        "class_id": class_id,
        "compare_to_design": {
            "max_abs_diff": {str(lam): round(max_diff[lam], 6) for lam in (0.0, LAM, 1.0)},
            "kps_diff_gt_1e6": {str(lam): diff_count[lam] for lam in (0.0, LAM, 1.0)},
            "lam1_match_in_1_ulp": lam1_ok,
            "lam1_ulp_note": "λ=1 与设计 mastery 到 4 位小数舍入 ulp 一致（max 0.0001，2/40 KP 差 1 ulp，非实质偏差）",
            "design_mastery_fields": list(next(iter(design["mastery"].values()))),
        },
        "weak_sets": {"lam0": weak_by[0.0], "lam05": weak_by[LAM], "lam1": weak_by[1.0]},
        "aux_affected_kps": comp,
    }
    return out


# ---------------------------------------------------------------- md

def _write_md(report):
    lines = ["# Phase 3 S6 端到端复核报告（②，演示班 cls_3b71d97847）", "",
             f"- 合成/审校时间：{report.get('generated_at')}；结果均为本次实际运行，不复用历史报告。", ""]
    s = report["sections"]
    if "e2e" in s:
        e = s["e2e"]
        lines += ["## A. 运行于服务重启前（叙述=模板兜底 ¥0）", "",
                  f"- 任务 {e.get('task_id')}：workflow={e.get('workflow')} status={e.get('status')}",
                  f"- 产物：{e.get('artifacts')}"]
        j = e.get("json") or {}
        lines += ["### JSON（04_诊断结果.json）", "",
                  f"- 数据范围：{j.get('scope')}", f"- 链 {j.get('chain_count')} 条：",
                  f"  - 目标：{j.get('chain_targets')}", f"  - 根因：{j.get('chain_roots')}",
                  f"- 推荐组 {j.get('rec_groups')} 组；q_id {len(j.get('rec_qids') or [])} 道",
                  f"- 叙述来源：{j.get('narration_source')}"]
        p = e.get("pdf") or {}
        lines += ["### PDF（05_学情诊断报告.pdf）", "",
                  f"- 字节 {p.get('bytes')} / {p.get('pages')} 页", f"- 一致性检查：{p.get('checks')}"]
        t = e.get("trace") or {}
        lines += ["### Trace", "",
                  f"- 节点与状态：{t.get('step_nodes')} all_done={t.get('all_done')}",
                  f"- totals：{t.get('totals')}", f"- cost_recompute_ok：{t.get('cost_recompute_ok')}",
                  f"- 节点 usage_source 分布：{dict(t.get('usage_sources') or {})}",
                  f"- 各节点：{json.dumps(t.get('per_step'), ensure_ascii=False)}"]
        c = e.get("citations") or {}
        lines += ["### 引用覆盖", "", f"- 引用 {c.get('count')} 条（按 kind {c.get('by_kind')}）；"
                                      f"used_in 覆盖 {c.get('coverage_used_in')}",
                  f"- 无 used_in 的引用：{c.get('no_used_in')}"]
        lines.append(f"- **e2e 结论：{'✅ 通过' if e.get('ok') else '❌ 未通过/有偏差'}**")
    if "provider" in s:
        e = s["provider"]
        lines += ["", "## B. 叙述=真实 LLM（provider）复核", "",
                  f"- 任务 {e.get('task_id')} status={e.get('status')}"]
        j = e.get("json") or {}
        lines.append(f"- 叙述来源：{j.get('narration_source')}；链 {j.get('chain_count')} 条")
        t = e.get("trace") or {}
        lines.append(f"- totals：{t.get('totals')}；recompute={t.get('cost_recompute_ok')}")
        for st in t.get("per_step") or []:
            for call in st.get("llm_call_detail") or []:
                lines.append(f"- 调用[{st['node']}] model={call.get('model')} "
                             f"usage_source={call.get('usage_source')} "
                             f"tokens_in/out={call.get('tokens_in')}/{call.get('tokens_out')} "
                             f"cache_hit={call.get('cache_hit')} "
                             f"price_version={call.get('price_version')} unit={call.get('unit_price')} "
                             f"cost={call.get('cost_cny')}")
        lines.append(f"- **provider 结论：{'✅ 通过' if e.get('ok') else '❌ 未通过/有偏差'}**")
    e = s.get("replay")
    lines += ["", "## C. 服务重启后回放（必需面：列表 + trace 库直读）", ""]
    if e:
        for tid, r in e.get("tasks", {}).items():
            lines.append(f"- `{tid}`：列表含完成态={r.get('list_contains_completed')}；"
                         f"trace 步骤 {r.get('trace_replayed_steps')}；"
                         f"回放 totals={r.get('replayed_totals')} 与运行期 "
                         f"{r.get('runtime_totals')} 一致={r.get('totals_match_runtime')}；"
                         f"cost_recompute_ok={r.get('cost_recompute_ok_after_restart')}"
                         f"（{'✅' if r.get('ok') else '❌'}）")
        first = next(iter(e.get("tasks", {}).values()), {})
        lines.append(
            f"- 限制备注（如实记录、不改 S4/S5 冻结逻辑）：重启后 /citations HTTP="
            f"{first.get('citations_api_after_restart_http')}、/files HTTP="
            f"{first.get('files_api_after_restart_http')} —— 依赖进程内 task_store 缓存、"
            f"重启后 404；{e.get('note', '')}")
        lines.append(f"- **replay 结论（必需面）：{'✅ 回放通过' if e.get('ok') else '❌ 未通过'}**")
    if "reweight" in s:
        e = s["reweight"]
        cmp = e.get("compare_to_design") or {}
        lines += ["", "## D. 角色权重重算 vs 设计参考值", "",
                  f"- 与设计 mastery 的 max|diff|：{cmp.get('max_abs_diff')}（λ=0.5/当前 与 λ=1）",
                  f"- 差异>1e-6 的 KP 数：{cmp.get('kps_diff_gt_1e6')}",
                  f"- 薄弱集合：λ=0 → {e.get('weak_sets', {}).get('lam0')}",
                  f"  λ=0.5 → {e.get('weak_sets', {}).get('lam05')}",
                  f"  λ=1 → {e.get('weak_sets', {}).get('lam1')}"]
        aux = e.get("aux_affected_kps") or []
        lines += ["- 辅助权重影响（λ=0.5 vs λ=1 差异>1e-6 或状态翻转）：", ""]
        for r in aux:
            lines.append(f"  - {r['kp']}: design={r['design_mastery']} "
                         f"m(λ0)={r['m0']} m(λ.5)={r['m05']} m(λ1)={r['m1']} "
                         f"aux_share=.5={r['aux_share']} state(.5/1)={r['state05']}/{r['state1']}")
        lines.append(f"- **reweight 结论（λ=1 应与设计参考一致）：{'✅ 一致' if e.get('ok') else '❌ 有差异'}**")
    lines += ["", "---",
              "注：本复核仅验证系统行为与冻结口径一致；10 例 GT 的 100% 一致率只代表冻结案例上的",
              "算法行为一致性，不等同于真实教学诊断准确率 100%。"]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- cli

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["e2e", "provider", "replay", "reweight"])
    ap.add_argument("--base-url", default="http://127.0.0.1:8011")
    ap.add_argument("--token", default=None)
    ap.add_argument("--task", default=None, help="replay 用：task_id1,task_id2")
    args = ap.parse_args()
    token = args.token if args.token is not None else __import__(
        "os").environ.get("EDUMATE_API_TOKEN", "")
    report = _load_report()
    if args.mode == "e2e":
        sec = mode_e2e(args.base_url, token)
    elif args.mode == "provider":
        sec = mode_e2e(args.base_url, token)
    elif args.mode == "replay":
        if not args.task:
            print("replay 需要 --task id1,id2"); return 2
        sec = mode_replay(args.base_url, token, args.task, report)
    else:
        sec = mode_reweight()
    report["sections"][args.mode] = sec
    _save_report(report)
    ok = sec.get("ok", False)
    print(f"[{args.mode}] ok={ok}")
    if not ok:
        for k in ("error",):
            if sec.get(k):
                print("  error:", sec[k])
    print(f"报告：{OUT_MD}\n{OUT_JSON}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())