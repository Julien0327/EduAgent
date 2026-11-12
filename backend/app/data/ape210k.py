"""APE210K ETL 清洗核心（Phase 1 / Step 4）。

数据源与许可核验见 docs/development/DATA-SOURCES.md（决策㉒）。原始数据为
JSON Lines（data/raw/ape210k_src/data/*.ape.json，不入 Git），五个字段：
id / original_text / segmented_text / equation / ans。

本模块只实现无副作用的清洗/映射/验算/去重纯函数（可单测）；
流式入库、漏斗统计与 FTS5 同步见 backend/scripts/etl_ape210k.py。

清洗语义（决策⑲四状态）：
- 完全清洗后合法且验算一致   -> quality_state='processed'
- 验算明确不一致            -> quality_state='rejected'（入库可追溯，不物理删除）
- 验算器能力不足（无法验算） -> 不惩罚题目，按 processed 入库并在漏斗中单独计数
- 题干无效 / 完全重复        -> 不入库，漏斗计数剔除原因

题干星号规则（2026-11-12 方案 A，用户裁决，修复旧规则 \\*+ 全量误删缺陷）：
- 乘号 `*`（如 27*324）与指数记法 `**`（如 cm**3、2**3）是合法数学记法，**原样保留**
- 仅清除已确认的残留：行首 ** 串（实测 95177/128301 两行，"****小学" 校名打码）
  与句末标点（。．？！？）之后的 ** 串（实测 36130 一行 "．**"；"？**2．" 编号
  残留同规则）——全库合计 3 行
- 不做 *→×、**→上标等记法转换（显示层职责）
"""
from __future__ import annotations

import ast
import re

# ---------------------------------------------------------------- 题干清洗

# 星号清洗（方案 A，2026-11-12）：仅匹配行首 ** 串或句末标点（。．？！？）后的
# ** 串（已确认的打码/编号残留，全库 3 行）；乘号与指数记法不在匹配范围。
# 替换时保留前导标点（捕获组 1），只删星号串本身。
RESIDUE_STAR_RE = re.compile(r"(^|[。．？！？])\*{2,}")
# 任意空白（含全角空格、不间断空格）归一
_WS_RE = re.compile(r"[\s　\xa0]+")
# 去重键用：仅保留汉字/字母/数字
_KEY_RE = re.compile(r"[^0-9A-Za-z一-鿿]+")

STEM_MIN_CHARS = 6  # 实测题干最短 6 字符；低于此判残缺


def normalize_stem(text: str) -> str:
    """题干清洗：仅清行首/句末 ** 残留（乘号与指数记法原样保留）、空白归一、去首尾空白。"""
    cleaned = RESIDUE_STAR_RE.sub(r"\1", str(text))
    return _WS_RE.sub(" ", cleaned).strip()


def stem_key(stem_norm: str) -> str:
    """去重键的题干部分：仅保留汉字/字母/数字（忽略标点与空白差异）。"""
    return _KEY_RE.sub("", stem_norm)


# ---------------------------------------------------------------- 答案解析

def parse_answer(ans: str) -> tuple[str, float | None]:
    """答案形态解析：('numeric'|'fraction'|'percent'|'other', 数值或 None)。

    数值用于验算比对；'other' 不参与验算（漏斗单独计数，不判不合格）。
    APE210K 分数形态：a/b、(a/b) 纯分数、n(a/b) 带分数（n 又 b 分之 a）。
    """
    text = str(ans).strip()
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return "numeric", float(text)
    m = re.fullmatch(r"(-?\d*)\s*\((-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)\)", text)
    if m and float(m.group(3)) != 0:
        whole = float(m.group(1)) if m.group(1) not in ("", "-") else 0.0
        frac = float(m.group(2)) / float(m.group(3))
        sign = -1.0 if text.startswith("-") else 1.0
        return "fraction", sign * (abs(whole) + frac)
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)", text)
    if m and float(m.group(2)) != 0:
        return "fraction", float(m.group(1)) / float(m.group(2))
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*%", text)
    if m:
        return "percent", float(m.group(1)) / 100.0
    return "other", None


def answer_key(ans: str) -> str:
    """去重键的答案部分：可解析数值按 6 位精度归一，否则用原文。"""
    kind, value = parse_answer(ans)
    if value is None:
        return str(ans).strip()
    return f"{kind}:{round(value, 6)}"


# ---------------------------------------------------------------- 算式验算

# 带分数 n(a/b)（如 81(3/5)=81.6）-> (n+a/b)；纯 (a/b) 本身是合法除法表达式
_MIXED_FRACTION_RE = re.compile(r"(\d+)\((\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)\)")
# 百分数字面量（如 20%/5%），替换为 (20/100) 再求值——Python 的 % 是取模，
# APE210K equation 中的 % 全部是百分号语义
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
# 形如 "x=" / "y=" 的单变量前缀
_VAR_PREFIX_RE = re.compile(r"^[A-Za-z]\s*=\s*")

# ast 白名单节点
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)
_MAX_POW_EXPONENT = 100  # 常量指数防护（数据 SHA-256 锁定，仅做基本防御）
_MAX_RESULT = 1e15


def prep_equation(equation: str) -> str | None:
    """equation 预处理：去变量前缀、带分数与百分数替换；返回可求值表达式或 None。"""
    text = _VAR_PREFIX_RE.sub("", str(equation).strip())
    if not text:
        return None
    text = _MIXED_FRACTION_RE.sub(r"(\1+\2/\3)", text)
    return _PERCENT_RE.sub(r"(\1/100)", text)


def safe_eval(expr: str) -> float | None:
    """AST 白名单安全求值；非算术表达式/数值异常返回 None。"""
    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        return None

    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.BinOp) and isinstance(node.op, _ALLOWED_BINOPS):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Pow) and isinstance(node.right, ast.Constant):
                if not isinstance(node.right.value, bool) and abs(float(node.right.value)) > _MAX_POW_EXPONENT:
                    raise ValueError("exponent too large")
            result: float = {
                ast.Add: lambda: left + right,
                ast.Sub: lambda: left - right,
                ast.Mult: lambda: left * right,
                ast.Div: lambda: left / right,
                ast.Pow: lambda: left**right,
                ast.Mod: lambda: left % right,
                ast.FloorDiv: lambda: left // right,
            }[type(node.op)]()
            if abs(result) > _MAX_RESULT:
                raise ValueError("result too large")
            return result
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, _ALLOWED_UNARYOPS):
            operand = visit(node.operand)
            return +operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise ValueError(f"disallowed node: {type(node).__name__}")

    try:
        return visit(tree)
    except (ValueError, ZeroDivisionError, OverflowError):
        return None


def verify_equation(equation: str, ans: str) -> str:
    """验算：'match' / 'mismatch' / 'unverifiable'（能力不足不算不一致）。"""
    kind, value = parse_answer(ans)
    if value is None:
        return "unverifiable"
    expr = prep_equation(equation)
    if expr is None:
        return "unverifiable"
    result = safe_eval(expr)
    if result is None:
        return "unverifiable"
    # 相对容差 1e-6（浮点除法/幂运算的表示误差）
    return "match" if abs(result - value) <= 1e-6 * max(1.0, abs(value)) else "mismatch"


# ---------------------------------------------------------------- 字段映射

# 题型映射：APE210K 全部为数学应用题（word problem）
QUESTION_TYPE = "application"
# 分值：数据集无分值字段，统一占位 1.0（漏斗报告说明，不代表卷面语义）
PLACEHOLDER_SCORE = 1.0


def difficulty_from_equation(equation: str) -> int:
    """难度粗标（确定性规则，透明可复现；Step 5 LLM 打标时覆盖）。

    按 equation 运算符个数：0-1 个 -> 1，2 -> 2，3 -> 3，4 -> 4，>=5 -> 5。
    """
    text = str(equation)
    ops = text.count("+") + text.count("-") + text.count("/")
    ops += text.count("**")
    ops += text.replace("**", "").count("*")  # ** 已单独计，余下单个 *
    ops += text.count("%")
    if ops <= 1:
        return 1
    return min(ops, 5)


def map_raw(raw: dict) -> tuple[dict | None, str | None]:
    """原始行 -> 题库字段映射（不含验算与去重）。

    返回 (映射结果, 剔除原因)；剔除时结果为 None。
    """
    stem = normalize_stem(raw.get("original_text") or "")
    if len(stem) < STEM_MIN_CHARS:
        return None, "stem_invalid"
    source_id = str(raw.get("id") or "").strip()
    if not source_id:
        return None, "id_missing"
    return {
        "source_id": source_id,
        "q_id": f"q_ape_{source_id}",
        "stage": "primary",
        "subject": "math",
        "type": QUESTION_TYPE,
        "stem": stem,
        "answer": str(raw.get("ans") or "").strip(),
        "analysis": str(raw.get("equation") or "").strip(),
        "score": PLACEHOLDER_SCORE,
        "difficulty": difficulty_from_equation(raw.get("equation") or ""),
        "source": "ape210k",
    }, None


def dedup_key(stem_norm: str, ans: str) -> str:
    """完全重复判定键：题干（仅汉字字母数字）+ 答案数值归一。

    同键 -> 完全重复（剔除保留首条）；同题干不同答案 -> 疑似相似变式
    （两题都保留，不误删，漏斗单独计数）。
    """
    return f"{stem_key(stem_norm)}|{answer_key(ans)}"
