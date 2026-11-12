"""APE210K ETL 测试（Phase 1 / Step 4）。

覆盖：清洗纯函数（方案 A 星号规则：保留乘号/指数，仅清行首/句末残留）/
验算与注入防护 / 去重语义（完全重复 vs 疑似变式）/ 字段映射与剔除 /
小学 KP 规则匹配 / 端到端小样本入库 + 幂等重跑 / FTS5 同步
（processed 才索引、全量重建语义）/ B 类真实样本恢复回归（2026-11-12）。
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

def test_normalize_stem_preserves_math_notation():
    """方案 A（2026-11-12 用户裁决）：乘号/指数/百分数/分数记法原样保留，不做记法转换。"""
    assert ape210k.normalize_stem("因为27*324=8,748，所以0.27*3.24=多少") == "因为27*324=8,748，所以0.27*3.24=多少"
    assert ape210k.normalize_stem("计算123*456=") == "计算123*456="
    assert ape210k.normalize_stem("它的体积是多少cm**3？") == "它的体积是多少cm**3？"
    assert ape210k.normalize_stem("计算2**3") == "计算2**3"
    assert ape210k.normalize_stem("占地面积增加了多少米**2．") == "占地面积增加了多少米**2．"  # 中文底数指数
    assert ape210k.normalize_stem("3.14*4**2") == "3.14*4**2"
    assert ape210k.normalize_stem("(75%+(1/3))*24") == "(75%+(1/3))*24"
    assert ape210k.normalize_stem("1/2") == "1/2"


def test_normalize_stem_strips_confirmed_residue_only():
    """仅清除已确认残留：行首 ** 串（校名打码）与句末标点（。．？！？）后的 ** 串。"""
    # 实测 95177/128301：行首 **** 校名打码 -> 星号串删除，正文保留
    assert ape210k.normalize_stem("****小学要修建一个圆柱形的游泳池") == "小学要修建一个圆柱形的游泳池"
    # 实测 36130：句末 ． 后 ** 残留 -> 星号删除，句末标点保留
    assert ape210k.normalize_stem("这个圆的面积是多少平方厘米．**") == "这个圆的面积是多少平方厘米．"
    # 句中标点（，；）后的 ** 未确认为残留（全库无此形态）-> 保守保留
    assert ape210k.normalize_stem("已知，**3 是假设形态") == "已知，**3 是假设形态"
    # 空白归一与首尾空白语义不变
    assert ape210k.normalize_stem("  一共有　多少  个 ") == "一共有 多少 个"
    assert ape210k.normalize_stem("无残留") == "无残留"


# 2026-11-12 B 类清洗损坏真实样本（original_text 逐字取自原始数据文件）：
# 抽样发现的 10 道 + 库内同形态 1 道（325488，米**2 中文底数指数，不在抽样内）
B_CLASS_REAL_SAMPLES = [
    ("q_ape_209048", "当123456789*987654321被6除时，余数是多少．"),
    ("q_ape_230513", "因为27*324=8,748，所以0.27*3.24=多少"),
    ("q_ape_290121", "(12/5)*((5/6)+(3/4))+(4/5)．"),
    ("q_ape_58143", "一个长方体铁皮水箱，容积是60L，高是4dm，它的底面积是多少dm**2．"),
    ("q_ape_634755", "(75%+(1/3))*24；3.8*30%+6.2*30%"),
    ("q_ape_678403", "一个正方体的棱长之和是84cm，它的体积是多少cm**3？"),
    ("q_ape_87500", "计算：0.625*(1(2/3)+3(1/6))+(1/6)/1(3/5)-(5/8)．"),
    ("q_ape_942935", "计算：((1**2+2**2)/(1*2))+((2**2+3**2)/(2*3))+…+((100**2+101**2)/(100*101))+((101**2+102**2)/(101*102))=．"),
    ("q_ape_1060185", "计算(1/((1/(1*2))+(1/(2*3))+(1/(3*4))+…+(1/(2016*2017))))="),
    ("q_ape_1200515", "巧算：36*111+888*5+222*2．"),
    ("q_ape_325488", "一个圆形花坛的半径是4米，现在要扩建花坛，将半径增加1米，这时花坛的占地面积增加了多少米**2．"),
]


@pytest.mark.parametrize("q_id,original", B_CLASS_REAL_SAMPLES)
def test_b_class_real_samples_restored(q_id, original):
    """B 类真实样本回归：清洗后星号逐一保留、题干与原始逐字一致（无损坏）。"""
    cleaned = ape210k.normalize_stem(original)
    assert cleaned == original, f"{q_id} 清洗后应与原始一致（无残留、无多余空白）"
    assert cleaned.count("*") == original.count("*"), f"{q_id} 星号数量应不变"


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
