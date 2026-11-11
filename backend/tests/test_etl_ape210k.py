"""APE210K ETL 测试（Phase 1 / Step 4）。

覆盖：清洗纯函数 / 验算与注入防护 / 去重语义（完全重复 vs 疑似变式）/
字段映射与剔除 / 小学 KP 规则匹配 / 端到端小样本入库 + 幂等重跑 /
FTS5 同步（processed 才索引、全量重建语义）。
"""
import json

import pytest

from backend.app.data import ape210k, primary_kp
from backend.scripts import etl_ape210k
from backend.app.storage import db


@pytest.fixture()
def etl_db(tmp_path):
    db_file = tmp_path / "etl.sqlite"
    db.apply_migrations(db_file)
    return str(db_file)


# ---------------------------------------------------------------- 清洗纯函数

def test_normalize_stem_cleans_markdown_residue():
    assert ape210k.normalize_stem("增加**2倍") == "增加2倍"
    assert ape210k.normalize_stem("  一共有　多少  个 ") == "一共有 多少 个"
    assert ape210k.normalize_stem("无残留") == "无残留"


def test_parse_answer_shapes():
    assert ape210k.parse_answer("450") == ("numeric", 450.0)
    assert ape210k.parse_answer("-2.5") == ("numeric", -2.5)
    assert ape210k.parse_answer("3/4") == ("fraction", 0.75)
    assert ape210k.parse_answer("25%") == ("percent", 0.25)
    assert ape210k.parse_answer("三分之一")[0] == "other"
    assert ape210k.parse_answer("1/0") == ("other", None)  # 分母 0 不可验算


def test_prep_equation_percent_and_prefix():
    assert ape210k.prep_equation("x=150*20%/5%-150") == "150*(20/100)/(5/100)-150"
    assert ape210k.prep_equation("y=2+3") == "2+3"
    assert ape210k.prep_equation("x=") is None


def test_safe_eval_whitelist_blocks_injection():
    assert ape210k.safe_eval("(1+2)*3") == 9.0
    assert ape210k.safe_eval("2**3") == 8.0
    assert ape210k.safe_eval("__import__('os').system('echo hi')") is None
    assert ape210k.safe_eval("(lambda: 1)()") is None
    assert ape210k.safe_eval("1/0") is None
    assert ape210k.safe_eval("2**999") is None  # 指数防护
    assert ape210k.safe_eval("'a'+'b'") is None  # 非数值常量拒绝


def test_verify_equation_verdicts():
    assert ape210k.verify_equation("x=150*20%/5%-150", "450") == "match"
    assert ape210k.verify_equation("x=(3.14*(4+1)**2)-(3.14*4**2)", "28.26") == "match"
    assert ape210k.verify_equation("x=2+2", "5") == "mismatch"
    # 能力不足不误判：中文答案/解析失败 = unverifiable
    assert ape210k.verify_equation("x=1/2", "二分之一") == "unverifiable"
    assert ape210k.verify_equation("不合法算式(((", "5") == "unverifiable"


def test_difficulty_from_equation_rule():
    assert ape210k.difficulty_from_equation("x=1") == 1
    assert ape210k.difficulty_from_equation("x=3+5*2") == 2
    assert ape210k.difficulty_from_equation("x=1+2+3+4+5+6") == 5


def test_map_raw_drops_invalid_stem():
    mapped, reason = ape210k.map_raw({"id": "1", "original_text": "**", "equation": "x=1", "ans": "1"})
    assert mapped is None and reason == "stem_invalid"
    mapped, reason = ape210k.map_raw({"id": "", "original_text": "完整题干内容", "equation": "x=1", "ans": "1"})
    assert mapped is None and reason == "id_missing"


def test_dedup_key_semantics():
    # 完全重复：题干空白/标点差异 + 答案数值等价 -> 同键
    k1 = ape210k.dedup_key("小明有5个苹果。", "5")
    k2 = ape210k.dedup_key("小明有 5 个苹果！", "5.00")
    assert k1 == k2
    # 同题干异答案（疑似变式）：键不同，两题都保留
    k3 = ape210k.dedup_key("小明有5个苹果。", "6")
    assert k1 != k3


# ---------------------------------------------------------------- KP 规则匹配

def test_kp_rules_scale_in_range():
    assert 80 <= len(primary_kp.KP_RULES) <= 120


def test_match_kps_keyword_hits():
    hits = primary_kp.match_kps("鸡和兔关在一个笼子里，共有35个头、94只脚。", "x=2*35")
    assert "鸡兔同笼" in hits
    hits = primary_kp.match_kps("一个圆形花坛的半径是4米，求面积。", "x=3.14*4**2")
    assert "圆的面积" in hits
    assert "鸡兔同笼" not in hits


def test_match_kps_multiple_and_none():
    # 多命中允许（多对多）
    hits = primary_kp.match_kps("小明以每小时5千米的速度步行，求平均速度。", "x=20/5")
    assert any(h in hits for h in ("一般行程", "平均数问题"))
    # 无命中返回空（不硬挂）
    assert primary_kp.match_kps("完全无关的题目内容描述。", "x=1") == []


def test_match_kps_ops_constraints():
    # 加减单步：无乘除特征才命中
    assert "加减运算" in primary_kp.match_kps("一共有多少个？", "x=3+5")
    assert "加减运算" not in primary_kp.match_kps("一共有多少个？", "x=3+5*2")
    assert "四则混合运算" in primary_kp.match_kps("一共有多少个？", "x=3+5*2")
    # 幂运算特征
    assert "平方与立方" in primary_kp.match_kps("求面积。", "x=3.14*4**2")


# ---------------------------------------------------------------- 端到端小样本

def _make_raw(qid, stem, equation, ans):
    return {"id": qid, "original_text": stem, "segmented_text": stem,
            "equation": equation, "ans": ans}


def _write_raw_dir(tmp_path, rows):
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    for name in etl_ape210k.SPLITS:
        (raw / name).write_text("", encoding="utf-8")
    (raw / "train.ape.json").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return raw


def test_etl_end_to_end_small_sample(etl_db, tmp_path):
    rows = [
        _make_raw("9001", "小明有5个苹果，又买了3个，一共有多少个？", "x=5+3", "8"),
        _make_raw("9002", "一根绳子长10米，用去一半，还剩多少米？", "x=10/2", "5"),
        _make_raw("9003", "一个圆形花坛半径4米，面积是多少？", "x=3.14*4**2", "50.24"),
        _make_raw("9004", "验算不一致的题。", "x=2+2", "5"),  # mismatch -> rejected
        _make_raw("9005", "完全重复的题干内容一样。", "x=1+1", "2"),
        _make_raw("9006", "完全重复的题干内容一样！", "x=1+1", "2"),  # 完全重复剔除
        _make_raw("9007", "完全重复的题干内容一样。", "x=1+2", "3"),  # 同题干异答案：疑似变式保留
        _make_raw("9008", "**", "x=1", "1"),  # 题干无效
    ]
    raw = _write_raw_dir(tmp_path, rows)

    report = etl_ape210k.run(etl_db, raw)
    f = report["funnel"]
    assert f["raw_total"] == 8
    assert f["verify_mismatch_rejected"] == 1
    assert f["duplicate_exact"] == 1
    assert f["similar_variant_kept"] == 1
    assert f["stem_invalid"] == 1
    assert f["written_processed"] == 5
    assert f["written_rejected"] == 1

    conn = db.connect(etl_db)
    try:
        n = conn.execute("SELECT COUNT(*) n FROM questions").fetchone()["n"]
        assert n == 6  # 5 processed + 1 rejected（rejected 入库可追溯）
        states = dict(conn.execute(
            "SELECT quality_state, COUNT(*) n FROM questions GROUP BY quality_state"
        ).fetchall())
        assert states == {"processed": 5, "rejected": 1}
        # FTS5 只索引 processed
        fts = conn.execute("SELECT COUNT(*) n FROM questions_fts").fetchone()["n"]
        assert fts == 5
        # 验算不一致题在 questions 但不在 fts
        bad = conn.execute("SELECT q_id FROM questions WHERE quality_state='rejected'").fetchone()
        in_fts = conn.execute("SELECT COUNT(*) n FROM questions_fts WHERE q_id=?", (bad["q_id"],)).fetchone()["n"]
        assert in_fts == 0
        # FTS5 可查（unicode61 分词：连续中文串整体成 token——子词"苹果"查不到，
        # 中文语义级检索属 Phase 2 分词优化；此处验证同步性与可查性）
        hit = conn.execute(
            "SELECT q_id FROM questions_fts WHERE questions_fts MATCH '小明有5个苹果'"
        ).fetchall()
        assert len(hit) == 1 and hit[0]["q_id"] == "q_ape_9001"
    finally:
        conn.close()


def test_etl_idempotent_rerun(etl_db, tmp_path):
    rows = [
        _make_raw("8001", "小明有5个苹果，又买了3个，一共有多少个？", "x=5+3", "8"),
        _make_raw("8002", "一根绳子长10米，用去一半，还剩多少米？", "x=10/2", "5"),
    ]
    raw = _write_raw_dir(tmp_path, rows)

    r1 = etl_ape210k.run(etl_db, raw)
    r2 = etl_ape210k.run(etl_db, raw)  # 重跑：INSERT OR IGNORE + FTS 全量重建

    conn = db.connect(etl_db)
    try:
        assert conn.execute("SELECT COUNT(*) n FROM questions").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) n FROM questions_fts").fetchone()["n"] == 2
        # 数据内容不变
        stem = conn.execute("SELECT stem FROM questions WHERE q_id='q_ape_8001'").fetchone()["stem"]
        assert "苹果" in stem
    finally:
        conn.close()
    assert r1["funnel"]["written_processed"] == r2["funnel"]["written_processed"] == 2


def test_fts_survives_full_rebuild_semantics(etl_db, tmp_path):
    """FTS5 全量重建语义：重跑后旧行被清理、新 processed 全部可查。"""
    raw1 = _write_raw_dir(tmp_path, [_make_raw("7001", "苹果题干内容。", "x=1+1", "2")])
    etl_ape210k.run(etl_db, raw1)
    # 第二批数据（新文件重写）
    raw2 = _write_raw_dir(tmp_path, [_make_raw("7001", "苹果题干内容。", "x=1+1", "2"),
                                     _make_raw("7002", "橘子题干内容。", "x=2+2", "4")])
    etl_ape210k.run(etl_db, raw2)

    conn = db.connect(etl_db)
    try:
        assert conn.execute("SELECT COUNT(*) n FROM questions_fts").fetchone()["n"] == 2
        # unicode61：完整连续串可查（中文子词限制见 test_etl_end_to_end_small_sample 注释）
        assert len(conn.execute(
            "SELECT q_id FROM questions_fts WHERE questions_fts MATCH '橘子题干内容'"
        ).fetchall()) == 1
    finally:
        conn.close()


def test_primary_kp_rebuild_is_isolated(etl_db, tmp_path):
    """KP 重建只动小学规则数据，初中图谱不受影响（Step 2 保护）。"""
    # 预置一条初中 KP（模拟 Step 2 数据）
    conn = db.connect(etl_db)
    try:
        with conn:
            conn.execute(
                "INSERT INTO knowledge_points (kp_id, name, subject, stage, grade, difficulty, description, source)"
                " VALUES ('kp_j7_0101', '有理数', 'math', 'junior', 7, 2, '测试', 'curriculum')"
            )
    finally:
        conn.close()

    rows = [_make_raw("6001", "鸡和兔关在一个笼子里，共有35个头、94只脚。", "x=(94-2*35)/2", "12")]
    raw = _write_raw_dir(tmp_path, rows)
    etl_ape210k.run(etl_db, raw)

    from backend.scripts import build_primary_kp
    build_primary_kp.run(etl_db)

    conn = db.connect(etl_db)
    try:
        primary_kps = conn.execute(
            "SELECT COUNT(*) n FROM knowledge_points WHERE source='ape210k_rule'"
        ).fetchone()["n"]
        assert primary_kps == len(primary_kp.KP_RULES)
        # 初中 KP 未被动过
        assert conn.execute("SELECT name FROM knowledge_points WHERE kp_id='kp_j7_0101'").fetchone()["name"] == "有理数"
        # ape 题挂上了鸡兔同笼
        link = conn.execute(
            "SELECT kp.kp_id FROM q_kp JOIN knowledge_points kp ON kp.kp_id=q_kp.kp_id"
            " WHERE q_kp.q_id='q_ape_6001' AND kp.name='鸡兔同笼'"
        ).fetchone()
        assert link is not None
        # 重跑幂等：挂接数不变
        n1 = conn.execute("SELECT COUNT(*) n FROM q_kp").fetchone()["n"]
    finally:
        conn.close()

    build_primary_kp.run(etl_db)
    conn = db.connect(etl_db)
    try:
        n2 = conn.execute("SELECT COUNT(*) n FROM q_kp").fetchone()["n"]
        assert n1 == n2
    finally:
        conn.close()
