"""质检确定性规则层单测（S1，纯离线，零 LLM）。

覆盖：正确性规则、难度规则、批内查重、历史题库查重（FTS + 相似度）、
``run_rules`` 编排与总裁定（fail 判定 / to_dict 序列化）。
"""
from __future__ import annotations

import sqlite3

from backend.app.critique.models import (
    BatchCritique, SEV_FAIL, SEV_WARN, STATUS_FAIL, STATUS_PASS,
    DIM_CORRECTNESS, DIM_DIFFICULTY, DIM_DUPLICATE,
)
from backend.app.critique.rules import check_correctness, check_difficulty, run_rules
from backend.app.critique.dupcheck import (
    batch_duplicates, find_similar_questions, similarity, normalize_stem,
)
from backend.app.retrieval.tokenize import fts_document


# ---------- 题目样例构造 ----------
_DEFAULT_OPTIONS = ["A. 5个", "B. 6个", "C. 7个", "D. 8个"]


def _choice(stem="小明有5个苹果", answer="A", options=_DEFAULT_OPTIONS, difficulty="中等", **kw):
    q = {
        "question_id": "q1", "type": "选择题", "stem": stem, "answer": answer,
        "options": options, "difficulty": difficulty, "analysis": "",
    }
    q.update(kw)
    return q


def _fill(stem="填空：1+1=____", answer="2", **kw):
    q = {"question_id": "q1", "type": "填空题", "stem": stem, "answer": answer,
         "options": None, "difficulty": "中等", "analysis": ""}
    q.update(kw)
    return q


def _codes(issues):
    return [i.code for i in issues]


# ---------- 正确性 ----------
def test_answer_empty_is_fail():
    issues = check_correctness(_choice(answer="  "))
    assert "correctness_answer_empty" in _codes(issues)


def test_answer_placeholder_is_fail():
    issues = check_correctness(_fill(answer="略"))
    assert "correctness_answer_placeholder" in _codes(issues)


def test_choice_valid_passes_correctness():
    issues = check_correctness(_choice(answer="A"))
    assert not any(i.severity == SEV_FAIL for i in issues)


def test_choice_answer_by_text_matches_option():
    issues = check_correctness(_choice(answer="5个"))
    assert "correctness_answer_not_in_options" not in _codes(issues)


def test_choice_option_count_must_be_four():
    issues = check_correctness(_choice(options=["A. 5", "B. 6", "C. 7"]))
    assert "correctness_option_count" in _codes(issues)


def test_choice_missing_options_is_fail():
    issues = check_correctness(_choice(options=None))
    assert "correctness_options_missing" in _codes(issues)


def test_choice_answer_not_in_options_is_fail():
    issues = check_correctness(_choice(answer="E"))
    assert "correctness_answer_not_in_options" in _codes(issues)


def test_fill_with_options_is_fail():
    issues = check_correctness(_fill(options=["A. 1", "B. 2", "C. 3", "D. 4"]))
    assert "correctness_unexpected_options" in _codes(issues)


def test_unit_sanity_warns_only():
    q = _fill(stem="小明买了多少元的东西？", answer="5米")
    issues = check_correctness(q)
    codes = _codes(issues)
    assert "correctness_unit_sanity" in codes
    unit = next(i for i in issues if i.code == "correctness_unit_sanity")
    assert unit.severity == SEV_WARN
    assert unit.details == {"asked_unit": "元", "answer_unit": "米"}


# ---------- 难度 ----------
def test_difficulty_short_stem_hard_warns():
    issues = check_difficulty(_choice(stem="1+1=?", difficulty="较难"))
    assert "difficulty_stem_too_short" in _codes(issues)
    assert issues[0].severity == SEV_WARN


def test_difficulty_long_stem_easy_warns():
    issues = check_difficulty(_choice(stem="题" * 61, difficulty="简单"))
    assert "difficulty_stem_too_long" in _codes(issues)


def test_difficulty_match_no_issue():
    issues = check_difficulty(_choice(stem="小明买了若干个苹果和梨，已知苹果比梨多两个，求苹果数量", difficulty="中等"))
    assert issues == []


def test_difficulty_easy_many_steps_warns():
    q = _choice(difficulty="简单", answer="先算a=1，再算b=2，然后c=a+b，所以c=3")
    issues = check_difficulty(q)
    assert "difficulty_steps_high" in _codes(issues)


# ---------- 查重：批内 ----------
def test_batch_duplicate_near_identical():
    qs = [_fill(stem="小明有5个苹果，小红有3个苹果，一共有多少个苹果？", question_id="q1"),
          _fill(stem="小明有5个苹果，小红有3个苹果，一共有多少个苹果", question_id="q2")]
    pairs, issues = batch_duplicates(qs)
    assert len(pairs) == 1
    assert pairs[0]["ratio"] >= 0.95
    assert issues[0][0].severity == SEV_FAIL
    assert issues[1][0].severity == SEV_FAIL


def test_batch_distinct_no_duplicate():
    qs = [_fill(stem="鸡兔同笼，鸡兔共10只", question_id="q1"),
          _fill(stem="一个长方形长5厘米宽3厘米求面积", question_id="q2")]
    pairs, issues = batch_duplicates(qs)
    assert pairs == []
    assert issues == {}


def test_normalize_removes_formatting_keeps_math():
    assert normalize_stem("小明有5个苹果，小红有3个。") == "小明有5个苹果小红有3个"
    assert normalize_stem("1+2=3") == "1+2=3"


def test_similarity_short_inputs_are_zero():
    assert similarity("a", "a") == 0.0
    assert similarity("短", "短") == 0.0


# ---------- 查重：历史题库（FTS） ----------
def _make_fts_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE questions (
            q_id TEXT PRIMARY KEY, stage TEXT, type TEXT, stem TEXT, answer TEXT,
            difficulty INTEGER, score REAL, source TEXT,
            quality_state TEXT NOT NULL DEFAULT 'processed',
            verified INTEGER DEFAULT 0, verified_at TEXT, created_at TEXT
        );
        CREATE VIRTUAL TABLE questions_fts USING fts5(
            q_id UNINDEXED, stem, answer, tokenize='unicode61'
        );
        """
    )
    return conn


def _insert_question(conn, q_id, stem, answer="8个"):
    conn.execute(
        "INSERT INTO questions (q_id, stage, type, stem, answer, difficulty, score, source, quality_state) "
        "VALUES (?, 'primary', 'application', ?, ?, 2, 5.0, 'ape210k', 'processed')",
        (q_id, stem, answer),
    )
    conn.execute(
        "INSERT INTO questions_fts (q_id, stem, answer) VALUES (?, ?, ?)",
        (q_id, fts_document(stem), fts_document(answer)),
    )
    conn.commit()


def test_history_duplicate_matches_similar(tmp_path):
    conn = _make_fts_db(str(tmp_path / "fts.sqlite"))
    _insert_question(conn, "q_ape_1", "小明有5个苹果，小红有3个苹果，一共有多少个苹果？")
    sims = find_similar_questions(conn, "小明有5个苹果，小红有3个苹果，一共有多少个苹果")
    assert sims, "应命中相似历史题"
    assert sims[0]["q_id"] == "q_ape_1"
    assert sims[0]["ratio"] >= 0.95
    conn.close()


def test_history_distinct_no_match(tmp_path):
    conn = _make_fts_db(str(tmp_path / "fts2.sqlite"))
    _insert_question(conn, "q_ape_1", "小明有5个苹果，小红有3个苹果，一共有多少个苹果？")
    sims = find_similar_questions(conn, "鸡兔同笼，鸡兔共10只，脚共28只，鸡兔各几只")
    assert sims == []
    conn.close()


# ---------- 编排 ----------
def test_run_rules_orchestrates_and_verdict():
    qs = [_choice(answer="A", question_id="q1", stem="小明有5个苹果"),
          _choice(answer="", question_id="q2", stem="一辆汽车每小时行60千米")]  # q2 空答案 → fail
    batch = run_rules(qs)
    assert isinstance(batch, BatchCritique)
    assert batch.status == "completed"
    assert batch.questions[0].status == STATUS_PASS
    assert batch.questions[1].status == STATUS_FAIL
    s = batch.summary()
    assert s == {"total": 2, "passed": 1, "failed": 1,
                 "duplicate_pairs_batch": 0, "history_matches": 0}


def test_run_rules_to_dict_roundtrip():
    batch = run_rules([_choice(answer="A")])
    d = batch.to_dict()
    assert d["status"] == "completed"
    assert d["summary"]["total"] == 1
    assert d["questions"][0]["question_id"] == "q1"
    assert "issues" in d["questions"][0]
