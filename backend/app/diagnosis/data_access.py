"""诊断数据访问层（Phase 3 / S1，§5.1）。

只读导入业务库的班级/考试/题目/答题数据，产出：
- 逐条证据行（题目 ID + 主/辅角色 + 学生答题 + 得分/满分/对错）；
- data_scope（考试数/题目数/学生数/答题数/覆盖 KP 数）。

纪律：
- **正式诊断只认导入数据**：不读种子、不推测；无导入班级数据 → 拒绝。
- **不修改冻结的小学题库/知识图谱/演示数据**。
- 主/辅角色来源于已提交的映射清单（``data/annotations/primary_demo_mapping.jsonl``），
  而非 ``exam_questions.kp_ids`` 的数组顺序（后者是主+辅的并集，不区分角色）。
  考试标签 A/B 按考试日期升序推导（A=最早，B=最新；当前演示班恰为 2 场）。
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[3]
MAPPING_FILE = REPO_ROOT / "data" / "annotations" / "primary_demo_mapping.jsonl"

PRIMARY = "primary"
AUX = "aux"


class DiagnosisDataError(Exception):
    """诊断数据不足或角色映射缺失（可预期，消息对用户友好）。"""


def load_role_map(path: Path = MAPPING_FILE) -> dict[tuple[str, int], dict]:
    """读取映射清单 → {(exam_label, q_no): {primary, aux_set, all_set}}。"""
    if not path.exists():
        raise DiagnosisDataError(f"主/辅映射清单缺失：{path}")
    role_map: dict[tuple[str, int], dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        key = (r["exam"], r["q_no"])
        aux = set(r.get("aux_kp_ids", []))
        all_ids = set(r.get("all_kp_ids", []))
        role_map[key] = {
            "primary": r["primary_kp_id"],
            "aux": aux,
            "all": all_ids,
        }
    return role_map


def _label_for(index: int) -> str:
    return chr(ord("A") + index)


def find_class(conn, class_id: str | None = None, class_name: str | None = None):
    if class_id:
        row = conn.execute("SELECT * FROM classes WHERE class_id=?", (class_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM classes WHERE name=? ORDER BY created_at, class_id LIMIT 1",
            (class_name,),
        ).fetchone()
    return row


def load_class_evidence(conn, class_id: str, role_map: dict | None = None) -> dict:
    """读取一个班级的全部导入证据。无班级/无考试 → DiagnosisDataError。"""
    cls = find_class(conn, class_id=class_id)
    if cls is None:
        raise DiagnosisDataError(f"班级不存在或未导入数据：{class_id}")
    if role_map is None:
        role_map = load_role_map()

    exams = conn.execute(
        "SELECT exam_id, title, exam_date, created_at FROM exams WHERE class_id=?"
        " ORDER BY COALESCE(exam_date, created_at), created_at, exam_id",
        (class_id,),
    ).fetchall()
    if not exams:
        raise DiagnosisDataError(f"班级 {cls['name']} 尚未导入考试数据，无法诊断")

    exam_labels = {ex["exam_id"]: _label_for(i) for i, ex in enumerate(exams)}
    exam_ids = [ex["exam_id"] for ex in exams]
    placeholders = ",".join("?" * len(exam_ids))

    exam_questions = conn.execute(
        f"SELECT id, exam_id, q_no, q_id, kp_ids, score FROM exam_questions"
        f" WHERE exam_id IN ({placeholders}) ORDER BY exam_id, q_no",
        exam_ids,
    ).fetchall()

    answers = conn.execute(
        f"SELECT sa.exam_id, sa.student_id, sa.exam_q_id, sa.score, sa.is_correct,"
        f" eq.q_no, eq.q_id, eq.kp_ids, eq.score AS full_score"
        f" FROM student_answers sa"
        f" JOIN exam_questions eq ON eq.id = sa.exam_q_id"
        f" WHERE sa.exam_id IN ({placeholders}) ORDER BY sa.exam_id, eq.q_no, sa.student_id",
        exam_ids,
    ).fetchall()

    student_count = conn.execute(
        "SELECT COUNT(*) FROM students WHERE class_id=?", (class_id,)
    ).fetchone()[0]

    evidence = []
    unmapped = []
    for a in answers:
        label = exam_labels[a["exam_id"]]
        q_no = a["q_no"]
        kp_ids = json.loads(a["kp_ids"] or "[]")
        for kp_id in kp_ids:
            role_entry = role_map.get((label, q_no))
            if role_entry is None or kp_id not in role_entry["all"]:
                unmapped.append((label, q_no, kp_id))
                continue
            role = PRIMARY if kp_id == role_entry["primary"] else AUX
            evidence.append({
                "exam_id": a["exam_id"],
                "exam_label": label,
                "q_no": q_no,
                "q_id": a["q_id"],
                "kp_id": kp_id,
                "role": role,
                "student_id": a["student_id"],
                "got_score": a["score"],
                "full_score": a["full_score"],
                "is_correct": a["is_correct"],
            })
    if unmapped:
        raise DiagnosisDataError(
            f"存在无法确定主/辅角色的映射：{unmapped[:5]}（共 {len(unmapped)}）")

    kp_covered = {e["kp_id"] for e in evidence}
    return {
        "class": dict(cls),
        "exams": [dict(ex) for ex in exams],
        "exam_labels": exam_labels,
        "exam_questions": [dict(q) for q in exam_questions],
        "evidence": evidence,
        "data_scope": {
            "class_id": cls["class_id"],
            "class_name": cls["name"],
            "exam_count": len(exams),
            "question_count": len(exam_questions),
            "student_count": student_count,
            "answer_count": len(answers),
            "kp_covered_count": len(kp_covered),
        },
    }


def load_demo_class(conn, class_name: str = "六年级演示班"):
    """按名称定位演示班（school 字段为演示标记），返回 class row。"""
    row = conn.execute(
        "SELECT * FROM classes WHERE name=? ORDER BY created_at, class_id LIMIT 1",
        (class_name,),
    ).fetchone()
    if row is None:
        raise DiagnosisDataError(f"未找到演示班级：{class_name}")
    return row


def connect(db_path=None):
    return db.connect(db_path)
