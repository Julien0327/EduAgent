"""Prompt 文件与装配测试（Step 5-B1）：指纹校验、特殊形态说明、规则标签泄漏防线。"""
import pytest

from backend.app.annotation import prompts

KP_ROWS = [
    {"kp_id": "kp_p_001", "name": "和差问题", "description": "已知两数之和与差求两数"},
    {"kp_id": "kp_p_075", "name": "分数运算", "description": "含分数的四则运算"},
]


def test_registry_fingerprints_match():
    """registry.jsonl 的 sha256 与实际文件一致；篡改即拒绝（防漂移）。"""
    registry = prompts.verify_registry()
    assert set(registry) == {"kp_primary", "kp_recall"}


def test_primary_prompt_structure():
    path = prompts.resolve_prompt_path("kp_primary", "v1.0")
    text = path.read_text(encoding="utf-8")
    assert prompts.KP_LIST_PLACEHOLDER in text
    # 特殊形态说明（设计文档 §4.3：空括号/星号/分数/带分数/百分数/循环小数）
    for marker in ("((())/(()))", "**", "(3/4)", "n(a/b)", "%", "{_{..}}",
                   "填空题的待填空位", "不是 Markdown"):
        assert marker in text, f"缺特殊形态说明：{marker}"
    # 难度锚点五级
    for level in ("1 = ", "2 = ", "3 = ", "4 = ", "5 = "):
        assert level in text
    # 不可映射是有效结果
    assert "不要勉强硬挂" in text


def test_recall_prompt_candidates_may_be_incomplete():
    path = prompts.resolve_prompt_path("kp_recall", "v1.0")
    text = path.read_text(encoding="utf-8")
    assert "候选清单可能不含正确答案" in text
    assert "不要为了迎合候选清单而硬挑" in text


def test_no_rule_tag_leakage_in_prompts():
    """决策㉔红线：prompt 全文不出现规则标签相关内容。"""
    for pid in ("kp_primary", "kp_recall"):
        path = prompts.resolve_prompt_path(pid, "v1.0")
        text = path.read_text(encoding="utf-8")
        for banned in ("q_kp", "ape210k_rule", "规则标签", "规则挂接"):
            assert banned not in text, f"{pid} 泄漏 {banned!r}"


def test_build_system_prompt_injects_list_and_snapshot():
    path = prompts.resolve_prompt_path("kp_primary", "v1.0")
    rendered, snapshot = prompts.build_system_prompt(path, KP_ROWS)
    assert "{{KP_LIST}}" not in rendered
    assert "- kp_p_001 和差问题：已知两数之和与差求两数" in rendered
    assert "- kp_p_075 分数运算：含分数的四则运算" in rendered
    assert snapshot == prompts.kp_snapshot_sha256(KP_ROWS)
    assert len(snapshot) == 64


def test_kp_lines_format():
    lines = prompts.kp_lines(KP_ROWS)
    assert lines == ("- kp_p_001 和差问题：已知两数之和与差求两数\n"
                     "- kp_p_075 分数运算：含分数的四则运算")


def test_resolve_unknown_version():
    with pytest.raises(KeyError):
        prompts.resolve_prompt_path("kp_primary", "v9.9")
