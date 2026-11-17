"""前端 F16 导入 → 学情分析 → 班级诊断 → 真实题库推荐 → PDF 全链路（L2 HTTP 集成）。

在「真实业务库的临时副本」上跑 TestClient（隔离、可重跑、不污染真库）：
模板下载 → 上传 E2E 工作簿 → 批次查询 → /api/classes 出现新班 → 确定性诊断
（归因链 + 真实题库推荐）→ 报告任务 → 轮询完成 → 04_json/05_pdf 产物可下载。

与 S5 真实演示班测试同约定：真实业务库存在时执行（``_REAL_DB.exists()`` skipif）。
"""
import io
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from backend.tests.conftest import AUTH_HEADERS
from backend.config import settings
from backend.app.storage import db
from backend.main import app

_REAL_DB = Path("data/db/edumate.sqlite")
XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DEMO_CLASS_NAME = "六年级演示班"


def _db_retriever(db_copy: Path):
    """以 db_copy 为背景的检索替身（同 test_diagnosis_s3._db_retriever）。"""
    from backend.app.retrieval.question_search import expand_by_kp, rerank

    class DBRetriever:
        def search_by_kp(self, kp_ids, filters=None, question_limit=20,
                         target_difficulty=None):
            conn = db.connect(db_copy)
            try:
                cands = expand_by_kp(conn, kp_ids, filters, limit=question_limit * 3)
            finally:
                conn.close()
            return rerank(cands, kp_ids, target_difficulty=target_difficulty)[:question_limit]

    return DBRetriever()


def _copy_real_db(tmp_path) -> Path:
    copy = tmp_path / "edumate_copy.sqlite"
    src = sqlite3.connect(str(_REAL_DB))
    dst = sqlite3.connect(str(copy))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return copy


def _wait_task(client, task_id: str, tries: int = 60, interval: float = 0.5):
    snap = None
    for _ in range(tries):
        r = client.get(f"/api/tasks/{task_id}")
        assert r.status_code == 200, r.text
        snap = r.json()
        if snap["status"] in ("completed", "failed"):
            return snap
        time.sleep(interval)
    raise AssertionError(f"任务 {task_id} 超时：{snap}")


@pytest.mark.skipif(not _REAL_DB.exists(), reason="真实业务库不存在")
def test_f16_import_to_diagnosis_pdf_full_chain(monkeypatch, tmp_path):
    from backend.scripts.build_f16_e2e_workbook import BASE_CLASS_NAME, build_e2e_workbook

    copy = _copy_real_db(tmp_path)
    # 用唯一 run_suffix 生成「全新一版」班级：即便真实库已含基础演示班，副本里也不会撞名，
    # 每次测试都真正走一遍「新班导入 → 诊断」；不依赖也不污染真实库。
    run_suffix = datetime.now().strftime("%H%M%S%f")
    class_name = BASE_CLASS_NAME + f" {run_suffix}"

    # 工作簿：复用 49 道 verified 题 + 全新班级/学生/考试/答题；F16 模板格式不变
    wb_bytes = build_e2e_workbook(run_suffix)
    wb = load_workbook(io.BytesIO(wb_bytes), data_only=True)
    assert set(wb.sheetnames) == {"班级", "学生", "考试", "考试题目", "答题记录", "填写说明"}
    assert wb["班级"]["A2"].value == class_name
    assert wb["考试题目"].max_row == 1 + 49
    assert wb["答题记录"].max_row == 1 + 980

    monkeypatch.setattr(settings, "DB_PATH", str(copy))
    monkeypatch.setattr("backend.app.retrieval.service.get_retrieval_service",
                        lambda: _db_retriever(copy))
    monkeypatch.setattr("backend.config.settings.DEEPSEEK_API_KEY", None)  # 叙述模板兜底（¥0）

    with TestClient(app, headers=AUTH_HEADERS) as client:
        # ① 模板下载（前端「下载导入模板」）
        r = client.get("/api/import/template")
        assert r.status_code == 200 and r.headers["content-type"] == XLSX_CT

        # ② 上传导入（前端「文件上传」，octet-stream）
        r = client.post("/api/import", params={"kind": "full", "file_name": "f16_e2e_workbook.xlsx"},
                        content=wb_bytes,
                        headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 200, r.text
        report = r.json()
        assert report["status"] == "committed", report["fatal"]
        assert (report["total_rows"], report["ok_rows"], report["failed_rows"]) == (1052, 1052, 0)
        assert report["errors"] == [] and report["warnings"] == []
        batch_id = report["batch_id"]

        # ③ 批次状态查询（前端「批次查询」）
        got = client.get(f"/api/import/{batch_id}")
        assert got.status_code == 200
        assert got.json()["status"] == "committed"
        assert got.json()["file_sha256"] == report["file_sha256"]

        # ④ 新班出现在「学情分析」班级列表（/api/classes）；demo 班不受影响
        classes = client.get("/api/classes").json()
        new_class = next((c for c in classes if c["name"] == class_name), None)
        assert new_class is not None
        assert new_class["students"] == 20 and new_class["answers"] == 980
        assert any(c["name"] == DEMO_CLASS_NAME for c in classes), "演示班不应受影响"
        demo_class = next(c for c in classes if c["name"] == DEMO_CLASS_NAME)
        demo_diag = client.get(f"/api/diagnosis/class/{demo_class['class_id']}")
        assert demo_diag.status_code == 200, "演示班诊断应仍可用"

        # ⑤ 确定性诊断（学情分析页「加载诊断」）
        d = client.get(f"/api/diagnosis/class/{new_class['class_id']}")
        assert d.status_code == 200, d.text
        body = d.json()
        assert body["data_scope"]["class_name"] == class_name
        assert body["data_scope"]["exam_count"] == 2
        assert body["data_scope"]["answer_count"] == 980
        assert body["chains"], "应有归因链"
        assert body["recommendations"], "应有真实题库推荐"
        for g in body["recommendations"]:
            for q in g["questions"]:
                assert q["q_id"].startswith("q_ape_")
                assert q["source"] == "ape210k"
                assert q["recommendation_reason"]

        # ⑥ 报告任务（「生成诊断报告」→ SSE/轮询）→ 产物 04_json + 05_pdf
        r = client.post(f"/api/diagnosis/class/{new_class['class_id']}/report")
        assert r.status_code == 202
        task_id = r.json()["task_id"]
        snap = _wait_task(client, task_id)
        assert snap["status"] == "completed", snap.get("error")
        names = [a["file_name"] for a in (snap["artifacts"] or [])]
        assert "04_诊断结果.json" in names and "05_学情诊断报告.pdf" in names

        # ⑦ 诊断结果 JSON：确定性结论 + 叙述来源
        j = client.get(f"/api/tasks/{task_id}/files/04_诊断结果.json?inline=true")
        assert j.status_code == 200
        payload = j.json()
        assert payload["diagnosis"]["chains"]
        assert payload["diagnosis"]["recommendations"]
        assert payload["narration"]["narration_source"] == "template"

        # ⑧ PDF 可下载、非空、页面可读
        pdf = client.get(f"/api/tasks/{task_id}/files/05_学情诊断报告.pdf")
        assert pdf.status_code == 200
        assert len(pdf.content) > 1000