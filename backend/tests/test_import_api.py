"""F16 导入服务端到端测试（Phase 1 / Step 3）。

全部用例经 main.app（TestClient，含真实 lifespan：迁移 + 启动清扫）与真实
openpyxl 生成的 xlsx 字节流走完整 HTTP → importer → SQLite 链路，不 Mock 文件层。
唯一例外：中断回滚用例 monkeypatch 写入函数注入写入中途崩溃（故障注入）。

覆盖五类验收用例 + 用户五个验收重点：
1 合法导入 / 2 含错文件定位与 partial / 3 重复指纹拒绝 / 4 force 覆盖重导 /
5 写入中断整批回滚 + 启动清扫 + 并发互斥；业务归属校验、kind 校验、模板端点。
"""
import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from backend.app.storage import db, importer
from backend.config import settings
from backend.main import app

XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# ---------------------------------------------------------------- 基建


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """每个测试独立临时库：迁移 + Step2 图谱真实入库；settings.DB_PATH 指向它。"""
    db_file = tmp_path / "edumate_api.sqlite"
    monkeypatch.setattr(settings, "DB_PATH", str(db_file))
    from backend.scripts.build_graph import build

    build(db_file)  # 303 知识点入库，供 kp_names 匹配与 q_id 引用
    return db_file


@pytest.fixture()
def client(env):
    with TestClient(app) as c:
        yield c


def build_workbook(**sheets: list[list]) -> bytes:
    """真实生成 xlsx 字节流（openpyxl），sheet 名 → 行列表（首行表头）。"""
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def post_import(client, data: bytes, **params) -> object:
    return client.post("/api/import", params=params, content=data,
                       headers={"Content-Type": "application/octet-stream"})


def counts(env) -> dict:
    conn = db.connect(env)
    try:
        tables = ("classes", "students", "exams", "exam_questions", "student_answers")
        return {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in tables}
    finally:
        conn.close()


def kp_name(env) -> str:
    """取图谱中一个真实知识点名称（用于 kp_names 匹配成功的用例）。"""
    conn = db.connect(env)
    try:
        return conn.execute(
            "SELECT name FROM knowledge_points ORDER BY kp_id LIMIT 1"
        ).fetchone()["name"]
    finally:
        conn.close()


def full_book(kp: str | None = None) -> bytes:
    """合法整簿工作簿（中文表头，贴近真实教师填写）。"""
    return build_workbook(
        班级=[["班级名称", "年级", "学段", "学校"],
             ["初一（1）班", 7, None, "演示中学"]],
        学生=[["班级", "学号", "姓名"],
             ["初一（1）班", "S01", "张三"],
             ["初一（1）班", "S02", "李四"]],
        考试=[["考试名称", "班级", "考试日期", "科目", "学段", "总分"],
             ["七下期中考试", "初一（1）班", "2026-04-20", "math", None, 150]],
        考试题目=[["考试名称", "题号", "题库ID", "知识点", "分值", "题干", "答案"],
                 ["七下期中考试", 1, None, kp or "有理数", 5, "1+1=?", "2"],
                 ["七下期中考试", 2, None, None, 5, "2×3=?", "6"],
                 ["七下期中考试", 3, None, None, 5, "3×3=?", "9"]],
        答题记录=[["考试名称", "班级", "学号", "题号", "得分", "对错"],
                 ["七下期中考试", "初一（1）班", "S01", 1, 5, "对"],
                 ["七下期中考试", "初一（1）班", "S01", 2, 4, "错"],
                 ["七下期中考试", "初一（1）班", "S02", 1, 3, "错"],
                 ["七下期中考试", "初一（1）班", "S02", 2, 5, "对"]],
    )


def answers_only_book(scores: dict[str, float]) -> bytes:
    """只含答题记录表的工作簿（覆盖重导场景；数据引用库内既有班级/学生/考试）。"""
    rows = [["考试名称", "班级", "学号", "题号", "得分", "对错"]]
    for key, score in scores.items():  # key: "S01:1"
        student_no, q_no = key.split(":")
        rows.append(["七下期中考试", "初一（1）班", student_no, int(q_no), score,
                     "对" if score >= 3 else "错"])
    return build_workbook(答题记录=rows)


# ---------------------------------------------------------------- 模板端点


def test_template_endpoint_returns_real_xlsx(client):
    resp = client.get("/api/import/template")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == XLSX_CT
    wb = load_workbook(io.BytesIO(resp.content))
    assert set(wb.sheetnames) == {"班级", "学生", "考试", "考试题目", "答题记录", "填写说明"}
    assert wb["班级"]["A1"].value == "班级名称"
    assert wb["答题记录"]["A1"].value == "考试名称"


def test_empty_template_import_reports_failed_batch(client):
    """模板原样上传（只有表头无数据行）→ 批次 failed，报告可读。"""
    resp = post_import(client, client.get("/api/import/template").content,
                       kind="full", file_name="template.xlsx")
    assert resp.status_code == 200
    report = resp.json()
    assert report["status"] == "failed"
    assert report["total_rows"] == 0
    assert "没有可写入的合法数据行" in report["fatal"]


# ---------------------------------------------------------------- 用例1：合法导入


def test_full_import_commits(env, client):
    kp = kp_name(env)
    resp = post_import(client, full_book(kp), kind="full", file_name="期中导入.xlsx")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["status"] == "committed"
    assert report["kind"] == "full"
    assert (report["total_rows"], report["ok_rows"], report["failed_rows"]) == (11, 11, 0)
    assert report["errors"] == []

    # 库内核对：数据全部落表且字段正确
    conn = db.connect(env)
    try:
        assert counts(env) == {"classes": 1, "students": 2, "exams": 1,
                               "exam_questions": 3, "student_answers": 4}
        cls = conn.execute("SELECT * FROM classes").fetchone()
        assert (cls["name"], cls["grade"], cls["stage"]) == ("初一（1）班", 7, "junior")  # 学段留空推断
        exam = conn.execute("SELECT * FROM exams").fetchone()
        assert (exam["title"], exam["stage"], exam["subject"], exam["total_score"]) == (
            "七下期中考试", "junior", "math", 150.0)
        assert exam["class_id"] == cls["class_id"]  # 业务归属：考试绑定班级落库
        stu = conn.execute("SELECT * FROM students ORDER BY student_no").fetchall()
        assert [s["student_no"] for s in stu] == ["S01", "S02"]
        q1 = conn.execute(
            "SELECT * FROM exam_questions WHERE q_no = 1"
        ).fetchone()
        assert json.loads(q1["kp_ids"]) == [conn.execute(
            "SELECT kp_id FROM knowledge_points WHERE name = ?", (kp,)
        ).fetchone()["kp_id"]]  # 知识点名称匹配落库
        ans = conn.execute(
            "SELECT a.score, a.is_correct FROM student_answers a"
            " JOIN students s ON a.student_id = s.student_id"
            " JOIN exam_questions q ON a.exam_q_id = q.id"
            " WHERE s.student_no = 'S01' AND q.q_no = 2"
        ).fetchone()
        assert (ans["score"], ans["is_correct"]) == (4.0, 0)
    finally:
        conn.close()

    # 批次查询端点与导入返回一致
    got = client.get(f"/api/import/{report['batch_id']}")
    assert got.status_code == 200
    assert got.json()["status"] == "committed"
    assert got.json()["file_sha256"] == report["file_sha256"]


# ---------------------------------------------------------------- 用例2：含错文件定位


def test_import_errors_partial_with_precise_location(env, client):
    book = build_workbook(
        班级=[["班级名称", "年级", "学段", "学校"],
             ["初一（1）班", 7, None, "演示中学"],
             ["初一（2）班", 13, None, None]],                       # E_RANGE 年级越界（第3行）
        学生=[["班级", "学号", "姓名"],
             ["初一（1）班", "S01", "张三"],
             ["初一（1）班", "S02", "李四"]],
        考试=[["考试名称", "班级", "考试日期", "科目", "学段", "总分"],
             ["七下期中考试", "初一（1）班", "2026-04-20", "math", None, 100]],
        考试题目=[["考试名称", "题号", "题库ID", "知识点", "分值", "题干", "答案"],
                 ["七下期中考试", 1, None, "不存在的知识点XYZ", 5, "a?", "b"],   # W_KP
                 ["七下期中考试", 2, None, None, 5, "c?", "d"],
                 ["七下期中考试", 3, "q_notexist", None, 5, None, None]],        # E_REF 题库无此题（第4行）
        答题记录=[["考试名称", "班级", "学号", "题号", "得分", "对错"],
                 ["七下期中考试", "初一（1）班", "S01", 1, 5, "对"],        # 合法
                 ["七下期中考试", "初一（1）班", "S02", 2, 4, "错"],        # 合法
                 ["七下期中考试", "不存在班", "S01", 1, 3, "错"],           # E_REF 班级定位失败（第4行）
                 ["七下期中考试", "初一（1）班", "S02", 1, 6, "对"]],       # E_RANGE 超卷面分值（第5行）
    )
    resp = post_import(client, book, kind="full")
    assert resp.status_code == 200
    report = resp.json()
    assert report["status"] == "partial"
    assert (report["total_rows"], report["ok_rows"], report["failed_rows"]) == (12, 8, 4)

    errs = {(e["sheet"], e["row"], e["field"]): e["code"] for e in report["errors"]}
    assert errs[("班级", 3, "grade")] == "E_RANGE"
    assert errs[("考试题目", 4, "q_id")] == "E_REF"
    assert errs[("答题记录", 4, "class_name")] == "E_REF"
    assert errs[("答题记录", 5, "score")] == "E_RANGE"
    codes = {e["code"] for e in report["warnings"]}
    assert {"W_TOTAL_SCORE", "W_KP"} <= codes

    # 库内只有合法行：错误行一律不写入（业务归属校验失败的行同样不入库）
    assert counts(env) == {"classes": 1, "students": 2, "exams": 1,
                           "exam_questions": 2, "student_answers": 2}

    # 批次查询端点返回同一份报告
    got = client.get(f"/api/import/{report['batch_id']}").json()
    assert got["status"] == "partial" and len(got["errors"]) == len(report["errors"])


# ---------------------------------------------------------------- 用例3：重复指纹拒绝


def test_duplicate_fingerprint_rejected_with_original_report(env, client):
    data = full_book(kp_name(env))
    first = post_import(client, data, kind="full").json()
    assert first["status"] == "committed"

    resp = post_import(client, data, kind="full")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reason"] == "duplicate_fingerprint"
    assert detail["original_batch"]["batch_id"] == first["batch_id"]

    # 拒绝路径不产生新数据（无重复写入）
    assert counts(env) == {"classes": 1, "students": 2, "exams": 1,
                           "exam_questions": 3, "student_answers": 4}
    # force=true 豁免指纹（进入覆盖重导语义，此处文件含非 answers 表被拒，见用例4）
    resp_force = post_import(client, data, kind="full", force="true")
    assert resp_force.status_code == 400


# ---------------------------------------------------------------- 用例4：force 覆盖重导（仅 answers）


def test_force_reimport_answers_only(env, client):
    resp = post_import(client, full_book(kp_name(env)), kind="full")
    assert resp.json()["status"] == "committed"

    changed = answers_only_book({"S01:1": 2.5, "S01:2": 5, "S02:1": 1, "S02:2": 4})
    # 未 force：库内 UNIQUE 预检发现既有记录 → 该行报 E_DUP 不写入 → 无合法行整批 failed
    dup = post_import(client, changed, kind="answers")
    assert dup.status_code == 200
    dup_report = dup.json()
    assert dup_report["status"] == "failed"
    assert any(e["code"] == "E_DUP" and "force" in e["message"] for e in dup_report["errors"])
    # 数据未被改动
    conn = db.connect(env)
    try:
        old = conn.execute(
            "SELECT a.score FROM student_answers a JOIN students s ON a.student_id = s.student_id"
            " JOIN exam_questions q ON a.exam_q_id = q.id"
            " WHERE s.student_no='S01' AND q.q_no = 1"
        ).fetchone()["score"]
        assert old == 5.0
    finally:
        conn.close()

    # force=true：同事务先删该考试原 answers 再写入 → 分数更新、总数不变
    forced = post_import(client, changed, kind="answers", force="true")
    assert forced.status_code == 200
    report = forced.json()
    assert report["status"] == "committed"
    assert report["kind"] == "answers"
    assert (report["ok_rows"], report["failed_rows"]) == (4, 0)
    conn = db.connect(env)
    try:
        assert counts(env) == {"classes": 1, "students": 2, "exams": 1,
                               "exam_questions": 3, "student_answers": 4}
        new = conn.execute(
            "SELECT a.score FROM student_answers a JOIN students s ON a.student_id = s.student_id"
            " JOIN exam_questions q ON a.exam_q_id = q.id"
            " WHERE s.student_no='S01' AND q.q_no = 1"
        ).fetchone()["score"]
        assert new == 2.5
    finally:
        conn.close()


def test_force_full_file_rejected(env, client):
    """force 仅限只含答题记录表的文件；full 文件 + force → 400。"""
    resp = post_import(client, full_book(kp_name(env)), kind="full", force="true")
    assert resp.status_code == 400
    assert "force" in resp.json()["detail"]


# ---------------------------------------------------------------- 用例5：中断回滚 + 清扫 + 并发互斥


def test_crash_mid_write_rolls_back_everything(env, client, monkeypatch):
    """写入中途崩溃 → 整批回滚不留半批数据，批次 failed 带原因（重点2）。"""
    original = importer._write_rows

    def crash_after_full_write(conn, valid_by_sheet, batch_id, force_answers):
        original(conn, valid_by_sheet, batch_id, force_answers)  # 全部行已在事务内写入
        raise RuntimeError("simulated crash mid-write")

    monkeypatch.setattr(importer, "_write_rows", crash_after_full_write)
    resp = post_import(client, full_book(kp_name(env)), kind="full")
    assert resp.status_code == 200
    report = resp.json()
    assert report["status"] == "failed"
    assert "整批回滚" in report["fatal"]
    assert report["ok_rows"] == 0

    assert counts(env) == {"classes": 0, "students": 0, "exams": 0,
                           "exam_questions": 0, "student_answers": 0}
    got = client.get(f"/api/import/{report['batch_id']}").json()
    assert got["status"] == "failed"

    # 回滚后同文件可重新导入（failed 批次不占用指纹）
    monkeypatch.setattr(importer, "_write_rows", original)  # 恢复（undo 会连 settings 一起撤销）
    resp2 = post_import(client, full_book(kp_name(env)), kind="full")
    assert resp2.json()["status"] == "committed"


def test_startup_sweeps_stale_validating(env):
    """启动清扫：滞留 validating 批次在 lifespan 中被标为 failed（重点4）。"""
    conn = db.connect(env)
    conn.execute(
        "INSERT INTO import_batches (batch_id, kind, file_name, file_sha256, status, created_at)"
        " VALUES ('imp_stale', 'full', 'stale.xlsx', ?, 'validating', '2026-01-01T00:00:00+00:00')",
        (uuid.uuid4().hex,),
    )
    conn.commit()
    conn.close()
    with TestClient(app):  # 触发 lifespan（清扫）
        pass
    conn = db.connect(env)
    try:
        row = conn.execute(
            "SELECT status, error_report FROM import_batches WHERE batch_id='imp_stale'"
        ).fetchone()
        assert row["status"] == "failed"
        assert "清扫" in json.loads(row["error_report"])["fatal"]
    finally:
        conn.close()


def test_concurrent_import_rejected_with_409(env, client):
    """存在 validating 批次时新导入被拒绝（409 import_busy）并留痕。"""
    conn = db.connect(env)
    conn.execute(
        "INSERT INTO import_batches (batch_id, kind, file_name, file_sha256, status, created_at)"
        " VALUES ('imp_running', 'full', 'running.xlsx', ?, 'validating', '2026-01-01T00:00:00+00:00')",
        (uuid.uuid4().hex,),
    )
    conn.commit()
    conn.close()
    resp = post_import(client, full_book(kp_name(env)), kind="full")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["reason"] == "import_busy"
    assert detail["rejected_batch"]["status"] == "failed"
    conn = db.connect(env)
    try:
        statuses = [r["status"] for r in conn.execute("SELECT status FROM import_batches")]
        assert "committed" not in statuses  # 被拒批次的数据没有写入
    finally:
        conn.close()


# ---------------------------------------------------------------- 业务归属校验（重点1）


def test_business_ownership_violations(env, client):
    """学生不属于所填班级、跨班冒名、考试绑班不符、同名班级歧义均被拒绝。"""
    book = build_workbook(
        班级=[["班级名称", "年级", "学段", "学校"],
             ["初二（1）班", 8, None, None],
             ["初二（1）班", 9, None, None]],                       # 同名跨年级（合法但定位歧义）
        学生=[["班级", "学号", "姓名"],
             ["初二（1）班", "S01", "张三"]],                        # E_REF 歧义
        考试=[["考试名称", "班级", "考试日期", "科目", "学段", "总分"],
             ["八上单元测", "初二（1）班", None, "math", None, None]],  # E_REF 绑班歧义
        考试题目=[["考试名称", "题号", "题库ID", "知识点", "分值", "题干", "答案"],
                 ["八上单元测", 1, None, None, 10, "x?", "y"]],
        答题记录=[["考试名称", "班级", "学号", "题号", "得分", "对错"],
                 ["八上单元测", "初二（1）班", "S01", 1, 8, "对"]],       # E_REF 学生定位歧义
    )
    resp = post_import(client, book, kind="full")
    report = resp.json()
    assert report["status"] == "failed"  # 全部行依赖歧义班级 → 断裂阈值触发整批拒绝
    ref_codes = [e["code"] for e in report["errors"]]
    assert ref_codes.count("E_REF") >= 3  # 学生行/考试绑班/答题行均报班级歧义
    assert counts(env) == {"classes": 0, "students": 0, "exams": 0,
                           "exam_questions": 0, "student_answers": 0}


def test_exam_bound_class_mismatch(env, client):
    """考试绑定班级时，其他班级学生的答题行被业务归属校验拒绝。"""
    book = build_workbook(
        班级=[["班级名称", "年级", "学段", "学校"],
             ["初一（1）班", 7, None, None],
             ["初一（2）班", 7, None, None]],
        学生=[["班级", "学号", "姓名"],
             ["初一（1）班", "S01", "张三"],
             ["初一（2）班", "S01", "王五"]],
        考试=[["考试名称", "班级", "考试日期", "科目", "学段", "总分"],
             ["一（1）班随堂测", "初一（1）班", None, "math", None, None]],
        考试题目=[["考试名称", "题号", "题库ID", "知识点", "分值", "题干", "答案"],
                 ["一（1）班随堂测", 1, None, None, 10, "x?", "y"]],
        答题记录=[["考试名称", "班级", "学号", "题号", "得分", "对错"],
                 ["一（1）班随堂测", "初一（2）班", "S01", 1, 8, "对"]],  # 学号同但属于 (2) 班
    )
    resp = post_import(client, book, kind="full")
    report = resp.json()
    # 答题表 1 行且 E_REF → 关联断裂阈值触发整批 failed，不写入
    assert report["status"] == "failed"
    err = next(e for e in report["errors"] if e["sheet"] == "答题记录")
    assert err["code"] == "E_REF" and "业务归属不符" in err["message"]
    conn = db.connect(env)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM student_answers").fetchone()["n"] == 0
    finally:
        conn.close()


def test_joint_exam_allows_any_bound_class(env, client):
    """多班联考（考试不绑班）不限制学生班级。"""
    book = build_workbook(
        班级=[["班级名称", "年级", "学段", "学校"],
             ["初一（1）班", 7, None, None],
             ["初一（2）班", 7, None, None]],
        学生=[["班级", "学号", "姓名"],
             ["初一（1）班", "S01", "张三"],
             ["初一（2）班", "S01", "王五"]],
        考试=[["考试名称", "班级", "考试日期", "科目", "学段", "总分"],
             ["年级联考", None, None, "math", None, None]],           # 不绑班 = 联考
        考试题目=[["考试名称", "题号", "题库ID", "知识点", "分值", "题干", "答案"],
                 ["年级联考", 1, None, None, 10, "x?", "y"]],
        答题记录=[["考试名称", "班级", "学号", "题号", "得分", "对错"],
                 ["年级联考", "初一（1）班", "S01", 1, 9, "对"],
                 ["年级联考", "初一（2）班", "S01", 1, 7, "错"]],
    )
    resp = post_import(client, book, kind="full")
    report = resp.json()
    assert report["status"] == "committed", report["errors"]
    assert counts(env)["student_answers"] == 2


# ---------------------------------------------------------------- kind 校验与杂项


def test_kind_mismatch_rejected(client):
    full = full_book(None)
    resp = post_import(client, full, kind="answers")   # full 内容 ≠ answers
    assert resp.status_code == 400
    resp = post_import(client, answers_only_book({"S01:1": 5}), kind="full")  # 单表 ≠ full
    assert resp.status_code == 400
    resp = post_import(client, full, kind="bogus")
    assert resp.status_code == 400
    resp = post_import(client, b"", kind="full")
    assert resp.status_code == 400


def test_kind_exam_single_sheet_commits(env, client):
    """kind=exam 只传考试表 = 先建考试后补题（合法路径）。"""
    book = build_workbook(
        考试=[["考试名称", "班级", "考试日期", "科目", "学段", "总分"],
             ["先建考试", None, "2026-05-01", "math", "junior", None]],
    )
    resp = post_import(client, book, kind="exam")
    report = resp.json()
    assert report["status"] == "committed", report["errors"]
    assert report["kind"] == "exam"
    conn = db.connect(env)
    try:
        exam = conn.execute("SELECT * FROM exams WHERE title='先建考试'").fetchone()
        assert exam["stage"] == "junior" and exam["class_id"] is None
    finally:
        conn.close()


def test_batch_query_404(client):
    assert client.get("/api/import/imp_nonexistent").status_code == 404


def test_reimport_guarded_by_business_rules(env, client):
    """指纹豁免后的库内重导：班级/学生可复用，考试撞名拒绝、答题撞记录拒绝。"""
    data = full_book(kp_name(env))
    first = post_import(client, data, kind="full").json()
    assert first["status"] == "committed"
    # 模拟指纹被豁免的路径：直接删掉批次记录（等价于历史批次被清除）
    conn = db.connect(env)
    conn.execute("DELETE FROM import_batches WHERE batch_id = ?", (first["batch_id"],))
    conn.commit()
    conn.close()
    second = post_import(client, data, kind="full").json()
    # 考试行 E_CONFLICT、答题行 E_DUP → 考试表/答题表全部行报错 → 无合法行整批 failed
    assert second["status"] == "failed"
    # 业务数据没有被重复写入
    assert counts(env) == {"classes": 1, "students": 2, "exams": 1,
                           "exam_questions": 3, "student_answers": 4}
    codes = {e["code"] for e in second["errors"]}
    assert "E_CONFLICT" in codes and "E_DUP" in codes
