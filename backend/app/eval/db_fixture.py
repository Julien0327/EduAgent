"""独立演示数据 Fixture（S2-A，gc_c01–c04 用；要求 5 / 裁决⑤）。

**目标**：class_diagnosis 评测**不依赖开发者本机库**中"恰好存在的班级/题库"——
在独立临时 SQLite 中幂等构建最小诊断环境，走与产品一致的写入路径：
1. `build_primary_kp.run(db_path)`  → 82 个小学 KP（规则表内嵌，确定性）；
2. `build_primary_graph.build(db_path)` → 87 条前置边（冻结清单）；
3. 最小题库行（推荐 source 查询用，确定性题干，标注 eval-fixture source）；
4. F16 importer 导入最小班级/学生/考试/题目/答题（含角色映射清单）。

**角色映射**：诊断主/辅角色来自映射清单 `primary_demo_mapping.jsonl`（键
(exam_label, q_no)）。fixture 生成独立映射文件并在运行期注入 data_access.load_role_map
（不触碰生产映射文件）。

**可重复性**：同一 fixture 内容+同一班名 → 相同 class_id 语义；临时库每次新建。
不做任何个性化库数据假设。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from openpyxl import Workbook

REPO_ROOT = Path(__file__).resolve().parents[3]

from backend.app.storage.templates import HEADER_DISPLAY, DATA_SHEET_ORDER
from backend.app.storage import db as _db

# 允许的诊断 fixture 主/辅助角色按 (exam_label, q_no) 登记；label A=第1场,B=第2场
FIXTURE_MAIN_KP = "分数运算"
FIXTURE_MAIN_KP_ID = "kp_p_075"          # KP_RULES 规则表固定编号（build_primary_kp 保证）
FIXTURE_KP_LIST = [("分数运算", "kp_p_075"), ("鸡兔同笼", "kp_p_005")]


def build_workbook_bytes(class_name: str, student_count: int = 2) -> bytes:
    """最小 F16 工作簿（2 场 × 2 题，覆盖目标 KP；答题让目标 KP 样本≥3 且两卷覆盖）。"""
    wb = Workbook()
    wb.remove(wb.active)
    kp_name, kp_id = FIXTURE_KP_LIST[0]
    sheets = {
        "班级": [[class_name, "6", "primary", "评测样例"]],
        "学生": [[class_name, f"s{1001 + i}", f"评测学生{i + 1}"] for i in range(student_count)],
        "考试": [[f"{class_name}-A", class_name, "2026-09-01", "数学", "primary", 100],
                 [f"{class_name}-B", class_name, "2026-11-01", "数学", "primary", 100]],
        "考试题目": [
            [f"{class_name}-A", 1, f"evalq_001", kp_name, 50, "评测题1（基础）", "1/2"],
            [f"{class_name}-A", 2, f"evalq_002", kp_name, 50, "评测题2（进阶）", "3/4"],
            [f"{class_name}-B", 1, f"evalq_003", kp_name, 50, "评测题3（综合）", "5/6"],
            [f"{class_name}-B", 2, f"evalq_004", kp_name, 50, "评测题4（巩固）", "7/8"],
        ],
        "答题记录": [],
    }
    # 每个学生对每卷两题作答：题1 满分、题2 低分（制造薄弱模式供归因）
    for i in range(student_count):
        for exam in (f"{class_name}-A", f"{class_name}-B"):
            sheets["答题记录"].append([exam, class_name, f"s{1001 + i}", 1, 50, "对"])
            sheets["答题记录"].append([exam, class_name, f"s{1001 + i}", 2, 10, "错"])
    for name in DATA_SHEET_ORDER:
        ws = wb.create_sheet(title=name)
        ws.append(HEADER_DISPLAY[name])
        for row in sheets[name]:
            ws.append(row)
    import io
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _seed_min_questions(db_path: Path) -> None:
    """最小题库行：供推荐 source_lookup 查询（确定性的 eval-fixture 题 4 条）。"""
    conn = _db.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        kp_name, kpid = FIXTURE_KP_LIST[0]
        for i in range(1, 5):
            conn.execute(
                "INSERT OR REPLACE INTO questions "
                "(q_id, stage, subject, type, stem, options, answer, analysis, "
                " score, difficulty, source, quality_state, verified, created_at) "
                "VALUES (?, 'primary', '数学', '解答题', ?, NULL, '1', '', 100, 2, "
                " 'eval-fixture', 'processed', 0, datetime('now'))",
                (f"evalq_00{i}", f"评测fixture题{i}：{kp_name}"),
            )
            conn.execute(
                "INSERT OR IGNORE INTO q_kp (q_id, kp_id) VALUES (?, ?)",
                (f"evalq_00{i}", kpid),
            )
        conn.commit()
    finally:
        conn.close()


def _mapping_jsonl_bytes(db_path: Path) -> None:
    """fixture 专属角色映射（(exam_label, q_no)）——写入临时库目录，注入 runner 用。"""
    primary, _ = FIXTURE_KP_LIST[0]
    rows = [
        {"exam": "A", "q_no": 1, "primary_kp_id": FIXTURE_MAIN_KP_ID,
         "aux_kp_ids": [], "all_kp_ids": [FIXTURE_MAIN_KP_ID]},
        {"exam": "A", "q_no": 2, "primary_kp_id": FIXTURE_MAIN_KP_ID,
         "aux_kp_ids": [], "all_kp_ids": [FIXTURE_MAIN_KP_ID]},
        {"exam": "B", "q_no": 1, "primary_kp_id": FIXTURE_MAIN_KP_ID,
         "aux_kp_ids": [], "all_kp_ids": [FIXTURE_MAIN_KP_ID]},
        {"exam": "B", "q_no": 2, "primary_kp_id": FIXTURE_MAIN_KP_ID,
         "aux_kp_ids": [], "all_kp_ids": [FIXTURE_MAIN_KP_ID]},
    ]
    path = db_path.parent / "fixture_role_map.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                    encoding="utf-8")
    return path


class Fixture:
    """一个独立诊断环境的封装（临时库 + 图谱 + 题库 + 导入数据 + 角色映射）。"""

    def __init__(self, db_path: Path | None = None, class_name: str = "评测演示班",
                 student_count: int = 2):
        self.class_name = class_name
        self.student_count = student_count
        self.root = Path(tempfile.mkdtemp(prefix="eval_fixture_"))
        self.db_path = db_path or (self.root / "fixture.sqlite")
        self.build()

    def build(self) -> None:
        from backend.scripts import build_primary_kp, build_primary_graph
        from backend.app.storage import importer

        _db.apply_migrations(self.db_path)
        build_primary_kp.run(str(self.db_path))                    # 82 KP
        build_primary_graph.build(str(self.db_path))               # 87 边
        _seed_min_questions(self.db_path)                          # 最小题库
        self.role_map_path = _mapping_jsonl_bytes(self.db_path)    # 角色映射
        data = build_workbook_bytes(self.class_name, self.student_count)
        report = importer.import_file(data, "eval_fixture.xlsx", kind="full",
                                      db_path=str(self.db_path))
        if report["status"] != "committed":
            raise RuntimeError(f"fixture 导入失败：{report}")
        self.batch_id = report["batch_id"]
        # 校验签名
        self.verify()

    def verify(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        n_kp = conn.execute("SELECT COUNT(*) FROM knowledge_points WHERE stage='primary'").fetchone()[0]
        n_edges = conn.execute("SELECT COUNT(*) FROM kp_edges").fetchone()[0]
        n_q = conn.execute("SELECT COUNT(*) FROM questions WHERE source='eval-fixture'").fetchone()[0]
        n_students = conn.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        n_answers = conn.execute("SELECT COUNT(*) FROM student_answers").fetchone()[0]
        conn.close()
        if not (n_kp == 82 and n_edges >= 87 and n_q == 4 and n_students == self.student_count
                and n_answers == self.student_count * 4):
            raise RuntimeError(
                f"fixture 校验失败：kp={n_kp} edges={n_edges} q={n_q} "
                f"students={n_students} answers={n_answers}")

    def class_id(self) -> str:
        conn = sqlite3.connect(str(self.db_path))
        row = conn.execute(
            "SELECT class_id FROM classes WHERE name=? LIMIT 1", (self.class_name,)).fetchone()
        conn.close()
        if row is None:
            raise RuntimeError(f"fixture 类不存在：{self.class_name}")
        return row[0]

    def conn(self):
        return _db.connect(self.db_path)


if __name__ == "__main__":
    f = Fixture()
    print(f"fixture db={f.db_path} class_id={f.class_id()} role_map={f.role_map_path}")