"""统一中文分词入口测试（Phase 2 / S1，决策㉛①）。

覆盖：分词基本行为 / FTS 索引与查询形态 / MATCH 语法注入隔离 /
索引-查询一致性（中文子词可查，unicode61 时代不可查的用例）。
FTS 行为用 :memory: 真实 FTS5 表验证（不 Mock SQLite）。
"""
import sqlite3

import pytest

from backend.app.retrieval import tokenize


@pytest.fixture(scope="module", autouse=True)
def _warm_jieba():
    """预热分词器词典（进程内一次），避免每条用例重复计入初始化耗时。"""
    tokenize._ensure_init()


def _memory_fts() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE t USING fts5(id UNINDEXED, content, tokenize='unicode61')"
    )
    return conn


def test_segment_basic_chinese_and_numbers():
    toks = tokenize.segment("小明有5个苹果，又买了3个。")
    assert "苹果" in toks
    assert "小明" in toks
    assert "5" in toks and "3" in toks
    assert all(t.strip() for t in toks)  # 无空白 token


def test_segment_math_notation_kept_as_tokens():
    # 乘号/指数记法原样保留为 token（决策㉓：合法数学记法，不清洗不转换）
    toks = tokenize.segment("因为27*324=8,748，所以0.27*3.24=多少")
    assert any("27" in t for t in toks)
    assert any("324" in t for t in toks)


def test_fts_document_is_tokenized_not_verbatim():
    doc = tokenize.fts_document("小明有5个苹果")
    assert doc != "小明有5个苹果"  # FTS 存的是索引文本，非原文（回表取原文原则）
    assert "苹果" in doc.split()  # 词粒度 token 出现


def test_fts_document_and_query_roundtrip_on_real_fts():
    """索引-查询一致性：整句与**中文子词**都能命中（unicode61 直写时代子词不可查）。"""
    conn = _memory_fts()
    try:
        conn.execute("INSERT INTO t (id, content) VALUES (?, ?)",
                     ("q1", tokenize.fts_document("小明有5个苹果，又买了3个，一共有多少个？")))
        conn.execute("INSERT INTO t (id, content) VALUES (?, ?)",
                     ("q2", tokenize.fts_document("一根绳子长10米，用去一半，还剩多少米？")))
        conn.commit()
        # 整句查询：OR 语义下共享「小明」的另一题也会命中，断言 q1 在结果中
        rows = conn.execute(
            "SELECT id FROM t WHERE t MATCH ?", (tokenize.fts_query("小明有5个苹果"),)
        ).fetchall()
        assert "q1" in {r[0] for r in rows}
        # 中文子词查询命中（Step 4 实测的 unicode61 缺陷，分词后消除）
        rows = conn.execute(
            "SELECT id FROM t WHERE t MATCH ?", (tokenize.fts_query("苹果"),)
        ).fetchall()
        assert [r[0] for r in rows] == ["q1"]
        # 另一题的子词不串
        rows = conn.execute(
            "SELECT id FROM t WHERE t MATCH ?", (tokenize.fts_query("绳子"),)
        ).fetchall()
        assert [r[0] for r in rows] == ["q2"]
    finally:
        conn.close()


def test_fts_query_neutralizes_fts5_syntax():
    """FTS5 操作符与引号注入被引号包裹隔离：只作短语匹配，不改变查询结构、不报语法错。"""
    conn = _memory_fts()
    try:
        conn.execute("INSERT INTO t (id, content) VALUES (?, ?)",
                     ("q1", tokenize.fts_document("苹果题干内容")))
        conn.commit()
        malicious = '苹果" NOT (content:x) OR NEAR 1*2^3'
        q = tokenize.fts_query(malicious)
        # 不抛 OperationalError（语法错误）；q1 因「苹果」命中
        rows = conn.execute("SELECT id FROM t WHERE t MATCH ?", (q,)).fetchall()
        assert "q1" in {r[0] for r in rows}
        # 结构断言：所有用户 token 都被双引号包裹；OR 只作为固定分隔符出现
        import re
        pattern = r'^"[^"]*(?:""[^"]*)*"(?: OR "[^"]*(?:""[^"]*)*")*$'
        assert re.match(pattern, q), q
        # 用户文本中的 NOT/NEAR 只能在引号内（不能作为裸操作符）
        assert '"NOT"' in q and '"NEAR"' in q
    finally:
        conn.close()


def test_fts_query_inner_quote_escaped():
    q = tokenize.fts_query('他说"你好"')
    # 内部引号被转义为 ""，且整体仍是合法 MATCH（内存表上执行不报错）
    conn = _memory_fts()
    try:
        conn.execute("INSERT INTO t (id, content) VALUES (?, ?)",
                     ("q1", tokenize.fts_document('他说"你好"再见')))
        conn.commit()
        rows = conn.execute("SELECT id FROM t WHERE t MATCH ?", (q,)).fetchall()
        assert [r[0] for r in rows] == ["q1"]
    finally:
        conn.close()


def test_empty_and_blank_inputs():
    assert tokenize.segment("") == []
    assert tokenize.segment("   ") == []
    assert tokenize.fts_document("") == ""
    assert tokenize.fts_query("") == '""'  # 空短语：合法 MATCH，零命中


def test_query_multi_term_uses_or():
    """Round1：多词查询按 OR 召回（BM25 负责相关性排序）。"""
    conn = _memory_fts()
    try:
        conn.execute("INSERT INTO t (id, content) VALUES (?, ?)",
                     ("q1", tokenize.fts_document("小明有5个苹果")))
        conn.execute("INSERT INTO t (id, content) VALUES (?, ?)",
                     ("q2", tokenize.fts_document("小明买了一支铅笔")))
        conn.commit()
        both = conn.execute("SELECT id FROM t WHERE t MATCH ?",
                            (tokenize.fts_query("小明 苹果"),)).fetchall()
        assert {r[0] for r in both} == {"q1", "q2"}  # OR：任一关键词命中
        apple = conn.execute("SELECT id FROM t WHERE t MATCH ?",
                             (tokenize.fts_query("苹果"),)).fetchall()
        assert {r[0] for r in apple} == {"q1"}
        pencil = conn.execute("SELECT id FROM t WHERE t MATCH ?",
                              (tokenize.fts_query("铅笔"),)).fetchall()
        assert {r[0] for r in pencil} == {"q2"}
    finally:
        conn.close()


def test_query_terms_drop_stopwords_single_chars_and_counts():
    assert tokenize.fts_query_terms("的了吗？") == []
    assert tokenize.fts_query_terms("小明有5个苹果，又买了3个。") == ["小明", "苹果"]
    assert tokenize.fts_query_terms("计算 问题 怎么 讲") == []


def test_query_terms_keep_math_numbers():
    assert tokenize.fts_query_terms("(91/100)/14") == ["91", "100", "14"]
    terms = tokenize.fts_query_terms("47*39，如果47扩大2倍，39不变")
    assert "47" in terms and "39" in terms and "2" in terms


def test_query_terms_non_math_drops_bare_numbers():
    assert "5" not in tokenize.fts_query_terms("小明有5个苹果")
    assert "0.3" in tokenize.fts_query_terms("c=0.3，那么c**2=多少")


def test_fts_query_or_structure_and_empty_fallback():
    assert tokenize.fts_query("苹果 绳子") == '"苹果" OR "绳子"'
    assert tokenize.fts_query("的了吗？") == '""'


def test_single_char_query_falls_back_to_vector():
    """单字中文 token 过泛：查询侧不生成 FTS 关键词，由向量路接管。"""
    assert tokenize.fts_query_terms("圆") == []
    assert tokenize.fts_query("圆") == '""'
