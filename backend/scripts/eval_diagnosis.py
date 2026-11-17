"""Phase 3 / S6 归因一致性评估器（10 例 GT）—— 根因一致率 ≥ 80% 验收。

对 `data/gt/diagnosis_gt_gold.json`（用户冻结 Gold）逐例：
- 构建**独立内存 SQLite**（classes/exams/exam_questions/student_answers/students 最小 schema），
  班级/学生/答题全部为合成 fixture；
- **独立角色映射 fixture**（由用例数据生成，绝不读取生产映射文件 primary_demo_mapping.jsonl）；
- 走与生产一致的确定性链路：`data_access.load_class_evidence → mastery.compute_mastery(λ=0.5,
  全集) → attribution.attribute`；
- 逐条断言（root / no_chain / candidates+conflict / blocked / path / confidence / mastery /
  primary_mastery_min / aux_share / cycles / master_unweighted+root_unweighted）；
- 单例**全部断言通过 = 一致**；根因一致率 = 一致例数 / 10（目标 ≥ 80%）。

修改纪律：评估器只衡量算法与冻结 Gold 的一致性；**不得为达标反向修改 Gold 或算法**，
失败如实报告。

产物（不入 Git）：
- data/staging/eval_diagnosis_report.md/.json（运行产物）
- data/staging/eval_diagnosis_dbs/*.sqlite（每例合成数据库留档）

用法（仓库根执行）：
    .venv/Scripts/python.exe -X utf8 -m backend.scripts.eval_diagnosis [--case GT-01]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from backend.app.diagnosis import data_access
from backend.app.diagnosis import mastery as M
from backend.app.diagnosis import attribution as A

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD_FILE = REPO_ROOT / "data" / "gt" / "diagnosis_gt_gold.json"
STAGING = REPO_ROOT / "data" / "staging"
OUT_MD = STAGING / "eval_diagnosis_report.md"
OUT_JSON = STAGING / "eval_diagnosis_report.json"
DB_DIR = STAGING / "eval_diagnosis_dbs"
LAM = 0.5

_SCHEMA = """
CREATE TABLE classes (
  class_id TEXT PRIMARY KEY, name TEXT, grade INTEGER, stage TEXT, school TEXT,
  import_batch_id TEXT, created_at TEXT);
CREATE TABLE students (
  student_id TEXT PRIMARY KEY, class_id TEXT, student_no TEXT, name TEXT,
  import_batch_id TEXT, created_at TEXT);
CREATE TABLE exams (
  exam_id TEXT PRIMARY KEY, title TEXT, class_id TEXT, exam_date TEXT, subject TEXT,
  stage TEXT, total_score REAL, import_batch_id TEXT, created_at TEXT);
CREATE TABLE exam_questions (
  id INTEGER PRIMARY KEY, exam_id TEXT, q_no INTEGER, q_id TEXT, kp_ids TEXT, score REAL);
CREATE TABLE student_answers (
  exam_id TEXT, student_id TEXT, exam_q_id INTEGER, score REAL, is_correct INTEGER);
"""


def _build_case_db(case: dict) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO classes (class_id,name,grade,stage,school,import_batch_id,created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        ("cls_gt", case["scenario"], 6, "primary", "GT 合成", None, "2026-01-01T00:00:00Z"))
    n = int(case.get("students", 20))
    for i in range(1, n + 1):
        conn.execute(
            "INSERT INTO students (student_id,class_id,student_no,name,import_batch_id,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (f"stu_{i:02d}", "cls_gt", f"S{i:02d}", f"学生{i:02d}", None, "2026-01-01T00:00:00Z"))
    for ex in case["exams"]:
        conn.execute(
            "INSERT INTO exams (exam_id,title,class_id,exam_date,subject,stage,total_score,"
            " import_batch_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (ex["id"], ex.get("title", ex["id"]), "cls_gt", ex["date"], "math", "primary",
             None, None, "2026-01-01T00:00:00Z"))
    qid = 0
    for q in case["questions"]:
        qid += 1
        conn.execute(
            "INSERT INTO exam_questions (id,exam_id,q_no,q_id,kp_ids,score) VALUES (?,?,?,?,?,?)",
            (qid, q["exam"], q["q_no"], f"{case['case_id']}_q{q['q_no']}{q['exam']}",
             json.dumps([q["kp"]], ensure_ascii=False), q["score"]))
        got = round(q["score"] * q["rate"], 6)
        for i in range(1, n + 1):
            conn.execute(
                "INSERT INTO student_answers (exam_id,student_id,exam_q_id,score,is_correct)"
                " VALUES (?,?,?,?,?)",
                (q["exam"], f"stu_{i:02d}", qid, got, None))
    conn.commit()
    return conn


def _build_role_map(case: dict) -> dict[tuple[str, int], dict]:
    """用例级独立角色映射 fixture（键=考试标签A/B + 题号）。"""
    exs = sorted(case["exams"], key=lambda e: e["date"])
    label = {e["id"]: chr(ord("A") + i) for i, e in enumerate(exs)}
    acc: dict[tuple[str, int], dict] = {}
    for q in case["questions"]:
        key = (label[q["exam"]], q["q_no"])
        entry = acc.setdefault(key, {"primary": None, "aux": set(), "all": set()})
        if q["role"] == "aux":
            entry["aux"].add(q["kp"])
        else:
            entry["primary"] = q["kp"]
        entry["all"].add(q["kp"])
    role_map = {}
    for key, v in acc.items():
        # 纯辅助证据题的主角色占位：用该例不会出现在任何题 kp_ids 的哨兵（绝不参与证据行），
        # 仅用于角色判定——使该题对目标 Kp 产出 role=aux 的证据行。
        primary = v["primary"]
        if primary is None:
            primary = f"__carrier_{case['case_id']}__"
        role_map[key] = {"primary": primary, "aux": v["aux"], "all": v["all"]}
    return role_map


def _run_chain(case: dict, conn):
    loaded = data_access.load_class_evidence(conn, "cls_gt", role_map=_build_role_map(case))
    all_kp = set(case["kps"]) | {q["kp"] for q in case["questions"]}
    mastery = M.compute_mastery(loaded["evidence"], loaded["exams"], LAM, all_kp_ids=all_kp)
    edges = [{"from_kp": a, "to_kp": b} for a, b in case["edges"]]
    prereqs = A.build_prereq_graph(edges)
    chains = A.attribute(mastery, prereqs, names=case["kps"], evidence=loaded["evidence"])
    cycles = A.detect_cycles(edges)
    return loaded, mastery, chains, cycles


def _run_unweighted(case: dict, loaded: dict) -> dict:
    """取消近期权重（合并为单场，权重均匀化=1:1）→ 掌握度 + 归因（GT-08 翻转复核）。"""
    ev = [dict(e, exam_id="ex_flat") for e in loaded["evidence"]]
    exams = [{"exam_id": "ex_flat", "title": "flat", "exam_date": "2020-01-01"}]
    mastery = M.compute_mastery(ev, exams, LAM, all_kp_ids=set(case["kps"]))
    edges = [{"from_kp": a, "to_kp": b} for a, b in case["edges"]]
    prereqs = A.build_prereq_graph(edges)
    chains = A.attribute(mastery, prereqs, names=case["kps"], evidence=ev)
    return {"mastery": mastery, "chains": chains}


def _check(case: dict, res: dict, unw: dict | None) -> list[dict]:
    mastery, chains, cycles = res["mastery"], res["chains"], res["cycles"]
    by_target = {c["target_kp"]: c for c in chains}
    chain_targets = set(by_target)
    out: list[dict] = []

    def add(ok: bool, detail: str, extra: dict | None = None):
        out.append({"ok": bool(ok), "detail": detail, **(extra or {})})

    for a in case["gold"]["cases"]:
        t = a["type"]
        if t == "root":
            c = by_target.get(a["target"])
            add(c is not None and c["root_kp"] == a["root"],
                f"目标 {a['target']} 根因 = {c['root_kp'] if c else '（无链）'}（期望 {a['root']}）",
                {"assertion": "root", "target": a["target"]})
        elif t == "no_chain":
            add(a["target"] not in chain_targets,
                f"目标 {a['target']} 不出链：{'✓' if a['target'] not in chain_targets else '✗ 出现在链中'}"
                if False else f"目标 {a['target']} 不出链（实际 {'未出现' if a['target'] not in chain_targets else '出现'}）",
                {"assertion": "no_chain", "target": a["target"]})
        elif t == "path":
            c = by_target.get(a["target"])
            add(c is not None and c["path"] == a["path"],
                f"目标 {a['target']} 路径 = {c['path'] if c else None}（期望 {a['path']}）",
                {"assertion": "path", "target": a["target"]})
        elif t == "confidence":
            c = by_target.get(a["target"])
            add(c is not None and c["confidence"] == a["value"],
                f"目标 {a['target']} 置信度 = {c['confidence'] if c else None}（期望 {a['value']}）",
                {"assertion": "confidence", "target": a["target"]})
        elif t == "blocked":
            c = by_target.get(a["target"])
            got = set(c["blocked_prereqs"]) if c else set()
            add(c is not None and got == set(a["set"]),
                f"目标 {a['target']} 阻断前置 = {sorted(got)}（期望 {sorted(a['set'])}）",
                {"assertion": "blocked", "target": a["target"]})
        elif t == "candidates":
            c = by_target.get(a["target"])
            got = {x["kp_id"] for x in c["root_candidates"]} if c else set()
            add(c is not None and got == set(a["set"])
                and c["conflict"] is bool(a["conflict"]) and c["root_kp"] == a["root"],
                f"目标 {a['target']} 候选={sorted(got)} conflict={bool(c and c['conflict'])} "
                f"主root={c and c['root_kp']}（期望 候选{ sorted(a['set']) } conflict={a['conflict']} "
                f"root={a['root']}）",
                {"assertion": "candidates", "target": a["target"]})
        elif t == "mastery":
            m = mastery.get(a["kp"], {}).get("mastery")
            tol = a.get("tol", 0.0001)
            add(m is not None and abs(m - a["expected"]) <= tol,
                f"掌握度[{a['kp']}] = {m}（期望 {a['expected']} ±{tol}）",
                {"assertion": "mastery", "kp": a["kp"]})
        elif t == "primary_mastery_min":
            pm = mastery.get(a["kp"], {}).get("primary_only_mastery")
            add(pm is not None and pm >= a["min"],
                f"主证据掌握度[{a['kp']}] = {pm}（期望 ≥{a['min']}）",
                {"assertion": "primary_mastery_min", "kp": a["kp"]})
        elif t == "aux_share":
            ax = mastery.get(a["kp"], {}).get("aux_share")
            tol = a.get("tol", 0.0001)
            add(ax is not None and abs(ax - a["expected"]) <= tol,
                f"aux_share[{a['kp']}] = {ax}（期望 {a['expected']} ±{tol}）",
                {"assertion": "aux_share", "kp": a["kp"]})
        elif t == "cycles":
            add(bool(cycles), f"环检测：{'非空 ✓' if cycles else '空 ✗'}", {"assertion": "cycles"})
        elif t == "mastery_unweighted":
            assert unw is not None
            m = unw["mastery"].get(a["kp"], {}).get("mastery")
            tol = a.get("tol", 0.0001)
            add(m is not None and abs(m - a["expected"]) <= tol,
                f"无近期权重掌握度[{a['kp']}] = {m}（期望 {a['expected']} ±{tol}）",
                {"assertion": "mastery_unweighted", "kp": a["kp"]})
        elif t == "root_unweighted":
            assert unw is not None
            c = {x["target_kp"]: x for x in unw["chains"]}.get(a["target"])
            add(c is not None and c["root_kp"] == a["root"],
                f"无近期权重下 目标 {a['target']} 根因 = {c and c['root_kp']}（期望 {a['root']}，证明翻转）",
                {"assertion": "root_unweighted", "target": a["target"]})
        else:
            raise ValueError(f"未知断言类型：{t}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default=None, help="只跑单个用例，如 GT-08")
    ap.add_argument("--gold", default=str(GOLD_FILE))
    ap.add_argument("--skip-dbs", action="store_true", help="不落盘合成数据库")
    args = ap.parse_args()

    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    case_list = [c for c in gold["cases"] if args.case is None or c["case_id"] == args.case]
    DB_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    for case in case_list:
        conn = _build_case_db(case)
        if not args.skip_dbs:
            db_path = DB_DIR / f"{case['case_id']}.sqlite"
            dst = sqlite3.connect(str(db_path))
            try:
                conn.backup(dst)
            finally:
                dst.close()
        loaded, mastery, chains, cycles = _run_chain(case, conn)
        conn.close()
        unw = None
        if any(a["type"] in ("mastery_unweighted", "root_unweighted")
               for a in case["gold"]["cases"]):
            unw = _run_unweighted(case, loaded)
        checks = _check(case, {"mastery": mastery, "chains": chains, "cycles": cycles}, unw)
        passed = all(c["ok"] for c in checks)
        results.append({
            "case_id": case["case_id"],
            "scenario": case["scenario"],
            "pass": passed,
            "checks": checks,
            "chains": [{"target": c["target_kp"], "root": c["root_kp"],
                        "path": c["path"], "confidence": c["confidence"],
                        "blocked": c["blocked_prereqs"]} for c in chains],
        })

    total = len(results)
    hit = sum(1 for r in results if r["pass"])
    rate = hit / total if total else 0.0
    passed_line = (f"根因一致率：{hit}/{total} = {rate:.0%}"
                   f"（目标 ≥80%）→ {'✅ 达标' if rate >= 0.8 else '❌ 未达标'}")

    # ---- 控制台 ----
    print(f"评估 Gold：{gold['meta']['title']}")
    print(passed_line)
    for r in results:
        print(f"  {r['case_id']} {r['scenario']}: {'PASS' if r['pass'] else 'FAIL'}")
        for c in r["checks"]:
            if not c["ok"]:
                print(f"    ✗ {c['detail']}")

    # ---- 报告落盘 ----
    report = {
        "gold_file": Path(args.gold).name,
        "frozen_by": gold["meta"]["frozen_by"],
        "meta": gold["meta"],
        "lam": LAM,
        "executed_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metric": f"{hit}/{total} = {rate:.0%}",
        "target": "≥80%",
        "reached": rate >= 0.8,
        "cases": results,
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# Phase 3 S6 归因一致性评估报告（10 例 GT）",
        "",
        f"- Gold：`{Path(args.gold).name}`（用户 2026-11-17 冻结，修订后不再修改）",
        f"- 执行：{report['executed_at']}；λ={LAM}",
        f"- 独立评测：每例内存 SQLite + 独立角色映射 fixture（不读生产映射文件）；合成库见 "
        f"`data/staging/eval_diagnosis_dbs/`",
        f"- `{passed_line}`",
        "",
        "| 用例 | 场景 | 结果 | 断言 |",
        "|---|---|---|---|",
    ]
    for r in results:
        npass = sum(1 for c in r["checks"] if c["ok"])
        md.append(f"| {r['case_id']} | {r['scenario']} | {'✅' if r['pass'] else '❌'} | {npass}/{len(r['checks'])} |")
    md.append("")
    for r in results:
        md += ["", f"### {r['case_id']} {r['scenario']}", ""]
        if r["chains"]:
            md.append("实际链：" + "；".join(
                f"{c['target']}→根因 {c['root']}（路径 {'→'.join(c['path'])}，"
                f"置信度 {c['confidence']}，阻断 {c['blocked'] or '[]'}）" for c in r["chains"]))
        else:
            md.append("实际链：无。")
        md.append("")
        for c in r["checks"]:
            md.append(f"- {'✅' if c['ok'] else '❌ '} {c['detail']}")
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print(f"\n报告：{OUT_MD}\n{OUT_JSON}")
    return 0 if rate >= 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())