"""三阶段检索引擎测试（Phase 2 / S3，§4.2）。

策略：
- 引擎逻辑（融合/扩展/重排/白名单/processed-only）用**临时库 + FakeEmbedder**
  离线验证（conftest.FakeEmbedder，确定性 hash 向量）；
- 真实库集成（82 小学 KP + 真实 bge 模型冒烟）用 skipif 守卫——库/模型缺失时
  跳过而不是失败（本仓库测试基线惯例：不依赖环境完整数据也保持绿）。
"""
import numpy as np
import pytest

from backend.app.retrieval import question_search as qs
from backend.app.retrieval.kp_recall import KpRetriever
from backend.app.retrieval.service import RetrievalService
from backend.app.retrieval.tokenize import fts_document
from backend.app.storage import db
from backend.tests.conftest import FakeEmbedder

DB_EXISTS = (db.DEFAULT_DB_PATH).exists()


@pytest.fixture()
def mini_bank_db(tmp_path):
    """最小检索库：3 个 KP（带向量）+ 4 道题（2 processed 初中 + 1 rejected + 1 processed 小学）。"""
    db_path = tmp_path / "mini.sqlite"
    db.apply_migrations(db_path)
    rng = np.random.default_rng(42)
    kps = [
        ("kp_j7_0001", "一元二次方程", "junior", 7, "一元二次方程", "形如 ax^2+bx+c=0 的方程"),
        ("kp_j7_0002", "一元二次方程的判别式", "junior", 7, "一元二次方程", "用判别式判断根的情况"),
        ("kp_p_001", "加法应用", "primary", 1, "加减法", "20 以内加法应用题"),
    ]
    conn = db.connect(db_path)
    with conn:
        conn.executemany(
            """INSERT INTO knowledge_points
                 (kp_id, name, subject, stage, grade, chapter, difficulty, description,
                  source, embedding)
               VALUES (?, ?, 'math', ?, ?, ?, 2, ?, 'curriculum', ?)""",
            [(k[0], k[1], k[2], k[3], k[4], k[5], rng.standard_normal(512).astype("float32").tobytes())
             for k in kps],
        )
        conn.executemany(
            """INSERT INTO questions
                 (q_id, stage, subject, type, stem, answer, score, difficulty,
                  source, quality_state, created_at)
               VALUES (?, ?, 'math', 'application', ?, ?, 5, ?, 'ape210k', 'processed', '2026-11-14')""",
            [
                ("q_j1", "junior", "解方程 x^2-3x+2=0", "x=1 或 x=2", 3),
                ("q_j2", "junior", "已知方程有两个不等实根，求 a 的取值范围", "a<1", 4),
                ("q_j3", "junior", "此题验算不一致（rejected 不可检索）", "x", 2),
            ],
        )
        conn.execute(
            """INSERT INTO questions
                 (q_id, stage, subject, type, stem, answer, score, difficulty,
                  source, quality_state, created_at)
               VALUES ('q_p1', 'primary', 'math', 'application', '小明有5个苹果又买3个，共几个？', '8', 5, 1,
                       'ape210k', 'processed', '2026-11-14')"""
        )
        conn.execute("UPDATE questions SET quality_state='rejected' WHERE q_id='q_j3'")
        # questions_fts（S1 分词形态，与 ETL 写入一致；rejected 不进索引）
        conn.executemany(
            "INSERT INTO questions_fts (q_id, stem, answer) VALUES (?, ?, ?)",
            [
                ("q_j1", fts_document("解方程 x^2-3x+2=0"), fts_document("x=1 或 x=2")),
                ("q_j2", fts_document("已知方程有两个不等实根，求 a 的取值范围"), fts_document("a<1")),
                ("q_p1", fts_document("小明有5个苹果又买3个，共几个？"), fts_document("8")),
            ],
        )
        conn.executemany(
            "INSERT OR IGNORE INTO q_kp (q_id, kp_id) VALUES (?, ?)",
            [
                ("q_j1", "kp_j7_0001"), ("q_j1", "kp_j7_0002"),
                ("q_j2", "kp_j7_0002"),
                ("q_j3", "kp_j7_0001"),  # rejected 题也挂接（FTS/q_kp 不索引它）
                ("q_p1", "kp_p_001"),
            ],
        )
    conn.close()
    return db_path


# ---------- 阶段 1：KP 召回 ----------

def test_kp_recall_returns_ref_and_meta(mini_bank_db):
    ret = KpRetriever(mini_bank_db, embedding_service=FakeEmbedder())
    try:
        out = ret.recall("一元二次方程", top_k=5)
        assert out and len(out) <= 5
        for kp in out:
            assert kp["ref"] == kp["kp_id"]  # §4.4
            assert kp["stage"] in ("junior", "primary")
            assert 0 <= kp["score"] <= 1.1  # 融合分（含规则加分为 0–1.05+）
        assert any("判别式" in kp["name"] for kp in out)  # 关键词路应命中
    finally:
        ret.close()


def test_kp_recall_stage_filter(mini_bank_db):
    ret = KpRetriever(mini_bank_db, embedding_service=FakeEmbedder())
    try:
        out = ret.recall("应用", stage="primary", top_k=10)
        assert out and all(kp["stage"] == "primary" for kp in out)
    finally:
        ret.close()


def test_kp_fts_self_heal(mini_bank_db):
    """kp_fts 为空时自动重建（自愈同步；S1 决策：不入幂等命令链）。"""
    conn = db.connect(mini_bank_db)
    n_before = conn.execute("SELECT COUNT(*) n FROM kp_fts").fetchone()["n"]
    with conn:
        conn.execute("DELETE FROM kp_fts")
    conn.close()
    # 构造 retriever → 自愈重建
    ret = KpRetriever(mini_bank_db, embedding_service=FakeEmbedder())
    try:
        conn = db.connect(mini_bank_db)
        n = conn.execute("SELECT COUNT(*) n FROM kp_fts").fetchone()["n"]
        conn.close()
        assert n == 3
    finally:
        ret.close()
    # 幂等：再次构造不再重建（集合一致零操作）
    ret2 = KpRetriever(mini_bank_db, embedding_service=FakeEmbedder())
    ret2.close()
    conn = db.connect(mini_bank_db)
    n2 = conn.execute("SELECT COUNT(*) n FROM kp_fts").fetchone()["n"]
    conn.close()
    assert n2 == 3
    assert n_before in (0, 3)  # fixture 初态未约束（自愈会补齐）


def test_kp_recall_requires_embedding(mini_bank_db):
    """KP 缺向量 → 明确报错（提示跑 build_kp_embeddings），不静默降级。"""
    conn = db.connect(mini_bank_db)
    with conn:
        conn.execute("UPDATE knowledge_points SET embedding=NULL WHERE kp_id='kp_p_001'")
    conn.close()
    with pytest.raises(ValueError, match="build_kp_embeddings"):
        KpRetriever(mini_bank_db, embedding_service=FakeEmbedder())


def test_kp_recall_empty_query(mini_bank_db):
    ret = KpRetriever(mini_bank_db, embedding_service=FakeEmbedder())
    try:
        assert ret.recall("") == []
        assert ret.recall("   ") == []
    finally:
        ret.close()


# ---------- 阶段 2：白名单与扩展 ----------

def test_filter_whitelist_rejects_unknown_keys_and_values():
    with pytest.raises(qs.FilterValidationError, match="非法过滤键"):
        qs.validate_filters({"evil": "1; DROP TABLE questions"})
    with pytest.raises(qs.FilterValidationError, match="stage"):
        qs.validate_filters({"stage": "ALL"})
    with pytest.raises(qs.FilterValidationError, match="question_type"):
        qs.validate_filters({"question_type": "hack"})
    with pytest.raises(qs.FilterValidationError, match="整数"):
        qs.validate_filters({"difficulty": "3 OR 1=1"})
    with pytest.raises(qs.FilterValidationError, match="1–5"):
        qs.validate_filters({"difficulty": 9})
    assert qs.validate_filters(None) == {}
    assert qs.validate_filters({"stage": "junior", "difficulty": 3})["stage"] == "junior"


def test_expand_by_kp_processed_only_and_ref(mini_bank_db):
    """决策⑲：检索只见 processed；候选必带 ref。"""
    conn = db.connect(mini_bank_db)
    try:
        out = qs.expand_by_kp(conn, ["kp_j7_0001", "kp_j7_0002"], {"stage": "junior"})
        ids = [c["q_id"] for c in out]
        assert "q_j3" not in ids  # rejected 不可检索
        assert {"q_j1", "q_j2"} <= set(ids)
        for c in out:
            assert c["ref"] == c["q_id"]
            assert c["kp_ids"]  # 知识点关联校验（JOIN 自带）
    finally:
        conn.close()


def test_expand_by_kp_inject_attempt_is_parameterized(mini_bank_db):
    """注入尝试只产生零结果而非泄漏（参数化绑定 + 白名单）。"""
    conn = db.connect(mini_bank_db)
    try:
        out = qs.expand_by_kp(conn, ["kp_j7_0001' OR '1'='1"], None)
        assert out == []  # 当作普通字符串绑定，无命中、无异常
    finally:
        conn.close()


def test_supplement_by_fts_requires_kp_link(mini_bank_db):
    """题面 FTS 补充路：命中题必须挂接知识点（§4.2 关联校验）。"""
    conn = db.connect(mini_bank_db)
    try:
        out = qs.supplement_by_fts(conn, "苹果", {"stage": "primary"})
        assert [c["q_id"] for c in out] == ["q_p1"]
    finally:
        conn.close()


# ---------- 阶段 3：重排 ----------

def test_rerank_prefers_kp_overlap_and_difficulty_fit():
    recall_kps = ["kp_a", "kp_b"]
    cands = [
        {"q_id": "q1", "kp_ids": ["kp_a", "kp_b"], "difficulty": 3},   # 全中 + 难度贴
        {"q_id": "q2", "kp_ids": ["kp_a"], "difficulty": 5},           # 半中 + 难度偏
        {"q_id": "q3", "kp_ids": ["kp_x"], "difficulty": 3},           # 不中
    ]
    ranked = qs.rerank(cands, recall_kps, target_difficulty=3)
    assert [r["q_id"] for r in ranked] == ["q1", "q2", "q3"]
    assert ranked[0]["rerank_score"] > ranked[1]["rerank_score"] > ranked[2]["rerank_score"]


def test_rerank_no_difficulty_target_is_neutral():
    cands = [{"q_id": "q1", "kp_ids": ["kp_a"], "difficulty": 5},
             {"q_id": "q2", "kp_ids": ["kp_a"], "difficulty": 1}]
    ranked = qs.rerank(cands, ["kp_a"], target_difficulty=None)
    # 无目标难度时难度特征同为中性 → 排序由 KP 特征（相同）决定 → 稳定
    assert {r["q_id"] for r in ranked} == {"q1", "q2"}


def test_rerank_fts_intent_gate():
    """题面相关性仅在题面检索意图时计入（§4.2）。"""
    cands = [
        {"q_id": "q1", "kp_ids": ["kp_a"], "difficulty": 3, "fts_rank": -5.0},
        {"q_id": "q2", "kp_ids": ["kp_a"], "difficulty": 3, "fts_rank": -1.0},
    ]
    off = qs.rerank(cands, ["kp_a"])
    on = qs.rerank(cands, ["kp_a"], with_fts_intent=True)
    assert off[0]["rerank_score"] == off[1]["rerank_score"]  # 无意图：同分
    assert on[0]["q_id"] == "q1" and on[0]["rerank_score"] > on[1]["rerank_score"]


# ---------- 编排门面 ----------

def test_service_full_pipeline(mini_bank_db):
    svc = RetrievalService(mini_bank_db, kp_retriever=KpRetriever(
        mini_bank_db, embedding_service=FakeEmbedder()))
    try:
        # 查询词与题面匹配（"苹果" 在 q_p1 题面）→ FTS 补充路启用
        out = svc.search_questions("苹果", stage="primary",
                                   filters={"difficulty_max": 5}, question_limit=10)
        assert out["kps"] and out["questions"]
        assert out["meta"]["fts_supplement_used"] is True
        for q in out["questions"]:
            assert q["ref"] == q["q_id"]
            assert "rerank_score" in q
        # 无题面意图（查询为空）→ 只走结构化扩展路
        out2 = svc.search_questions("", stage="junior", filters={"difficulty_max": 5})
        assert out2["meta"]["fts_supplement_used"] is False
    finally:
        svc.close()


# ---------- 真实库集成（skipif 守卫） ----------

@pytest.mark.skipif(not DB_EXISTS, reason="真实业务库不存在（data/db/edumate.sqlite）")
def test_real_bank_kp_recall_smoke():
    """真实 82 小学 KP + FakeEmbedder 冒烟：载入、自愈同步（应零重建）、召回结构。"""
    ret = KpRetriever(db.DEFAULT_DB_PATH, embedding_service=FakeEmbedder())
    try:
        out = ret.recall("分数运算", stage="primary", top_k=5)
        assert len(out) == 5
        assert all(kp["ref"] == kp["kp_id"] for kp in out)
    finally:
        ret.close()


@pytest.mark.skipif(not DB_EXISTS, reason="真实业务库不存在")
def test_real_bank_questions_smoke():
    """真实 20.8 万题库三阶段冒烟：结构化过滤 + 参数化 + processed-only。"""
    svc = RetrievalService(db.DEFAULT_DB_PATH)
    try:
        out = svc.search_questions("分数加减应用题", stage="primary",
                                   filters={"difficulty_max": 3}, question_limit=5)
        assert out["kps"]
        assert len(out["questions"]) <= 5
        for q in out["questions"]:
            assert q["stage"] == "primary"
            assert q["ref"] == q["q_id"]
    finally:
        svc.close()
