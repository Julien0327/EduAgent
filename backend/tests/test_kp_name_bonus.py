"""名称加分规则测试（Phase 2 Round1）：仅完整名称/显式别名触发。"""
from backend.app.retrieval import kp_recall
from backend.app.retrieval.kp_recall import name_bonus_for


def test_full_name_match_triggers():
    assert name_bonus_for("请讲讲一元二次方程的概念", "一元二次方程的概念") == 0.05
    assert name_bonus_for("一元二次方程的解法", "一元二次方程") == 0.05


def test_partial_name_does_not_trigger():
    # 查询含「判别式」，但完整名称是「根的判别式」→ 不触发
    assert name_bonus_for("一元二次方程的判别式", "根的判别式") == 0.0


def test_single_char_names_do_not_trigger():
    assert name_bonus_for("圆", "圆") == 0.0
    assert name_bonus_for("求圆的面积", "圆") == 0.0


def test_generic_names_do_not_trigger():
    for generic in ["问题", "计算", "积", "行", "位", "和", "倍", "数", "则", "与"]:
        assert name_bonus_for(f"这是{generic}的题", generic) == 0.0


def test_common_tokens_do_not_trigger_for_normal_names():
    """通用词出现在查询里，不得让普通 KP 名称误触发。"""
    query = "计算积行位和倍数问题"
    for name in ["质量单位换算", "体积容积单位换算", "长度单位换算", "一般行程", "加减运算"]:
        assert name_bonus_for(query, name) == 0.0


def test_explicit_alias_triggers(monkeypatch):
    monkeypatch.setitem(kp_recall.KP_ALIASES, "一元二次方程", ("二次方程",))
    assert name_bonus_for("二次方程怎么讲", "一元二次方程") == 0.05


def test_unlisted_abbreviation_does_not_trigger():
    assert name_bonus_for("二次方程怎么讲", "一元二次方程") == 0.0
