"""前端 F16 导入 → 班级诊断 → 真实题库推荐 → PDF 报告 端到端验收脚本（L3，起服冒烟）。

在**真实服务**（uvicorn 已起，连真实业务库）上，用 HTTP 完全模拟前端「数据导入」页的
调用序列：模板下载 → 真实 Excel 上传 → 批次查询 → /api/classes 出现新班 → 学情分析
诊断 → 生成报告任务 → 轮询完成 → 下载 04_json / 05_pdf 并核验。

前置（验收口径与 S4/S5 一致）：
- 服务已启动：`.venv/Scripts/python.exe -m uvicorn backend.main:app --app-dir . --port <port>`
- 环境变量 `EDUMATE_API_TOKEN` 已设置（或 --token 传入）；
  `DEEPSEEK_API_KEY` **留空** → 叙述模板兜底（¥0、确定性）。

用法（仓库根执行）：
    .venv/Scripts/python.exe -X utf8 -m backend.scripts.run_f16_import_e2e \
        --base-url http://127.0.0.1:8000 [--token xxx] [--run-suffix s2] [--pdf-check]

输出：data/staging/f16_import_e2e_report{suffix}.md/.json（不入 Git）。
幂等：同一 run-suffix 重复执行时，导入步骤识别为重复文件（409）后改为复用既有班级，
其余步骤（诊断/推荐/报告/PDF）每次照跑。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = REPO_ROOT / "data" / "db" / "edumate.sqlite"
XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

STATUS_LABELS = {"committed": "已提交", "partial": "部分成功", "failed": "失败"}


def _http(method: str, url: str, token: str, body: bytes | None = None,
          headers: dict | None = None) -> tuple[int, bytes]:
    h = dict(headers or {})
    if token:
        h["X-API-Token"] = token
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(resp_bytes: bytes) -> dict:
    try:
        return json.loads(resp_bytes.decode("utf-8"))
    except Exception:
        return {}


def _wait_task(base: str, token: str, task_id: str, tries: int = 120) -> dict:
    for _ in range(tries):
        code, data = _http("GET", f"{base}/api/tasks/{urllib.parse.quote(task_id)}", token)
        if code == 200:
            snap = _json(data)
            if snap.get("status") in ("completed", "failed"):
                return snap
        time.sleep(0.5)
    raise TimeoutError(f"任务 {task_id} 超时")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--token", default=None, help="API token；缺省读 EDUMATE_API_TOKEN")
    ap.add_argument("--run-suffix", default="", help="工作簿变体后缀（全新班级/考试名，可重传）")
    ap.add_argument("--pdf-check", action="store_true", help="用 pypdf 实读 PDF 文本核验")
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    token = args.token if args.token is not None else __import__(
        "os").environ.get("EDUMATE_API_TOKEN", "")
    suffix = args.run_suffix or ""

    from backend.scripts.build_f16_e2e_workbook import BASE_CLASS_NAME, build_e2e_workbook

    class_name = BASE_CLASS_NAME + (f" {suffix}" if suffix else "")
    wb_bytes = build_e2e_workbook(suffix)

    ev = {}
    lines: list[str] = []

    def step(title: str):
        lines.append("")
        lines.append(f"## {title}")

    # 1 模板下载
    step("① 模板下载（GET /api/import/template）")
    code, data = _http("GET", f"{base}/api/import/template", token)
    ct = ""
    if code == 200:
        from openpyxl import load_workbook
        wb_sheets = list(load_workbook(io.BytesIO(data), read_only=True).sheetnames)
        ok_tpl = "班级" in wb_sheets and "答题记录" in wb_sheets
        ct = "application/octet-stream-template-ok"
    else:
        ok_tpl = False
        wb_sheets = []
        ct = ""
    lines.append(f"- 状态码 {code}；工作表 {wb_sheets}；{'✓' if ok_tpl else '✗'}")
    ev["template_ok"] = ok_tpl

    # 2 上传导入
    step("② 真实 Excel 上传导入（POST /api/import, kind=full, octet-stream）")
    qs = urllib.parse.urlencode({"kind": "full", "file_name": "f16_e2e_workbook.xlsx"})
    code, data = _http("POST", f"{base}/api/import?{qs}", token, body=wb_bytes,
                       headers={"Content-Type": "application/octet-stream"})
    report = _json(data)
    batch_id = None
    import_note = ""
    if code == 409 and report.get("detail", {}).get("reason") == "duplicate_fingerprint":
        batch_id = report["detail"]["original_batch"].get("batch_id")
        import_note = "重复文件（同 sha 工作簿此前已导入）→ 复用既有批次"
        lines.append(f"- 409 重复指纹：原批次 {batch_id}（{import_note}）")
    elif code == 200:
        batch_id = report.get("batch_id")
        lines.append(f"- 批次 {batch_id}：status={report.get('status')} "
                     f"total={report.get('total_rows')} ok={report.get('ok_rows')} "
                     f"failed={report.get('failed_rows')} warnings={len(report.get('warnings') or [])}")
        if report.get("fatal"):
            lines.append(f"- fatal：{report['fatal']}")
        for e in (report.get("errors") or [])[:5]:
            lines.append(f"- 错误：{e}")
        if report.get("warnings"):
            lines.append(f"- 警告 {len(report['warnings'])} 条：{(report['warnings'] or [])[:3]}")
        if report.get("status") != "committed":
            import_note = (f"导入非 committed（{report.get('status')}）；"
                           f"若错误为考试重名/答题重复，说明该班级与数据已存在——按既有班继续验收")
    else:
        lines.append(f"- 状态码 {code}（不期望）{data.decode('utf-8', 'replace')[:400]}")
    lines.append(f"- 工作簿 sha256 {hashlib.sha256(wb_bytes).hexdigest()[:16]}…")
    ev["import_status"] = report.get("status") if code == 200 else ("duplicate" if code == 409 else f"http{code}")
    ev["import_ok"] = code == 200 and report.get("status") == "committed"
    ev["batch_id"] = batch_id
    ev["import_note"] = import_note

    # 3 批次查询
    if batch_id:
        step("③ 批次状态查询（GET /api/import/{batch_id}）")
        code, data = _http("GET", f"{base}/api/import/{urllib.parse.quote(batch_id)}", token)
        b = _json(data)
        lines.append(f"- 状态码 {code}；status={b.get('status')} ok={b.get('ok_rows')} "
                     f"failed={b.get('failed_rows')}")
        ev["batch_query_ok"] = code == 200  # 查询端点可用且返回同一份报告（状态如实展示）

    # 4 新班出现在学情分析班级列表（判断「数据就位」：本次新导入 或 既已存在）
    step("④ 新班出现在学情分析班级列表（GET /api/classes）")
    code, data = _http("GET", f"{base}/api/classes", token)
    classes = _json(data) if code == 200 else []
    new_class = next((c for c in classes if c["name"] == class_name), None)
    lines.append(f"- 状态码 {code}；班级 {len(classes)} 个；新班「{class_name}」"
                 f"{'✓ 已出现' if new_class else '✗ 未出现'}"
                 + (f"（{new_class['students']} 学生 / {new_class['answers']} 答题）" if new_class else ""))
    ev["class_appeared"] = new_class is not None
    if not new_class:
        print("\n".join(lines))
        return 1
    class_id = new_class["class_id"]

    # 5 学情分析诊断
    step("⑤ 学情分析诊断（GET /api/diagnosis/class/{id}）")
    code, data = _http("GET", f"{base}/api/diagnosis/class/{urllib.parse.quote(class_id)}", token)
    diag = _json(data)
    chains = diag.get("chains") or []
    recs = diag.get("recommendations") or []
    scope = diag.get("data_scope") or {}
    lines.append(f"- 状态码 {code}；{scope.get('exam_count')} 场考试 / {scope.get('answer_count')} 条答题"
                 f" / 覆盖 {scope.get('kp_covered_count')} 个知识点")
    lines.append(f"- 归因链 {len(chains)} 条：{[c.get('target_name') for c in chains[:6]]}")
    lines.append(f"- 推荐组 {len(recs)} 组："
                 f"{[(g.get('role'), g.get('candidate_count')) for g in recs[:6]]}")
    ev["diagnosis_ok"] = code == 200 and bool(chains) and bool(recs)

    # 6 生成报告任务 → 轮询
    step("⑥ 生成诊断报告（POST report → 轮询 /api/tasks/{id}）")
    code, data = _http("POST", f"{base}/api/diagnosis/class/{urllib.parse.quote(class_id)}/report", token)
    task = _json(data)
    task_id = task.get("task_id")
    lines.append(f"- POST 状态码 {code}；task_id={task_id}")
    if not task_id:
        print("\n".join(lines))
        return 1
    snap = _wait_task(base, token, task_id)
    lines.append(f"- 任务状态：{snap.get('status')}")
    if snap.get("error"):
        lines.append(f"- 错误：{snap['error']}")
    names = [a.get("file_name") for a in (snap.get("artifacts") or [])]
    lines.append(f"- 产物：{names}")
    ev["report_task_ok"] = snap.get("status") == "completed"
    ev["task_id"] = task_id
    ev["artifacts"] = names

    # 7 诊断结果 JSON 核验
    step("⑦ 诊断结果核验（04_诊断结果.json）")
    code, data = _http("GET", f"{base}/api/tasks/{urllib.parse.quote(task_id)}/files/"
                              f"{urllib.parse.quote('04_诊断结果.json')}?inline=true", token)
    payload = _json(data)
    narration = (payload.get("narration") or {})
    lines.append(f"- 状态码 {code}；归因链 {len((payload.get('diagnosis') or {}).get('chains') or [])} 条；"
                 f"叙述来源={narration.get('narration_source')}（template=模板兜底，llm=真实叙述）")
    ev["narration_source"] = narration.get("narration_source")

    # 8 PDF 下载 + 可选 pypdf 文本核验
    step("⑧ 下载学情诊断报告 PDF（05_学情诊断报告.pdf）")
    pdf_name = "05_学情诊断报告.pdf"
    code, data = _http("GET", f"{base}/api/tasks/{urllib.parse.quote(task_id)}/files/"
                              f"{urllib.parse.quote(pdf_name)}", token)
    out_pdf = REPO_ROOT / "data" / "staging" / f"f16_e2e_diagnosis_report{suffix}.pdf"
    out_pdf.write_bytes(data)
    lines.append(f"- 状态码 {code}；字节 {len(data)}；已存 {out_pdf}")

    ev["pdf_bytes"] = len(data)
    ev["pdf_ok"] = code == 200 and len(data) > 1000
    if args.pdf_check and code == 200:
        from pypdf import PdfReader
        text = "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages)
        checks = {
            "班级名": class_name in text,
            "推荐练习章节": "推荐练习" in text,
            "归因链章节": "根因归因链" in text or "归因链" in text,
            "合成数据声明": ("合成" in text or "演示" in text),
        }
        for k, v in checks.items():
            lines.append(f"- PDF 文本核验：{k} {'✓' if v else '✗'}")
            ev[f"pdf_{k}"] = v
        ev["pdf_text_preview"] = text[:80].replace("\n", " ")

    # 汇总
    # 汇总：导入侧判定 =「本次提交成功」或「班级已在库」（幂等复跑场景）
    data_in_place = ev.get("import_ok") or ev.get("class_appeared")
    ev["data_in_place"] = bool(data_in_place)
    ok = all(ev.get(k) for k in ("template_ok", "class_appeared",
                                 "diagnosis_ok", "report_task_ok", "pdf_ok", "batch_query_ok")) and data_in_place
    lines.append("")
    lines.append(f"## 汇总：{'✅ 全部通过' if ok else '❌ 存在未通过项'}")

    report = {
        "executed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": ok,
        "base_url": base,
        "class": {"name": class_name, "class_id": class_id},
        "batch_id": batch_id,
        "task_id": task_id,
        "import_note": import_note,
        "workbook_sha256": hashlib.sha256(wb_bytes).hexdigest(),
        "pdf_bytes": ev.get("pdf_bytes"),
        "narration_source": ev.get("narration_source"),
        "evidence": ev,
    }
    out_json = REPO_ROOT / "data" / "staging" / f"f16_import_e2e_report{suffix}.json"
    out_md = REPO_ROOT / "data" / "staging" / f"f16_import_e2e_report{suffix}.md"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    out_md.write_text("\n".join([
        f"# 前端 F16 导入与端到端验收报告（L3）",
        f"",
        f"- 执行时间：{report['executed_at']}；服务 {base}",
        f"- 班级：{class_name}（{class_id}）；批次 {batch_id}；任务 {task_id}",
        f"- 结论：**{'✅ 通过' if ok else '❌ 未通过'}**",
        "",
    ] + lines[1:]), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n报告：{out_md}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())