"""统一中文分词入口（Phase 2 / S1 建立；Round1 优化查询侧）。

索引侧（``fts_document``）与查询侧共用 jieba 分词。FTS5 内置 unicode61 对连续
中文串只产生一个 token，故索引写分词文本；查询侧 Round1 起改为**语义关键词
OR 匹配**（不再使用全 token 隐式 AND）：

- 查询预处理：去除标点、单字 CJK token、停用词/泛化词、无信息纯数字；
- 数学表达式（含 ``+ - * / × ÷ = ^ %`` 等）**保留数字**，避免纯算式查询被清空；
- 有效词 ≤ ``MAX_QUERY_TERMS`` 个，按词长优先保留（长词更具体），数学数字单独保留；
- FTS 查询串 = ``"词1" OR "词2" ...``，由 BM25 做相关性排序；
- 有效词为空（如「的了吗？」）→ 返回 ``'""'``，调用方退回向量检索。

注入面：每个用户 token 仍用双引号包裹并转义 ``"``；OR 是固定拼接，用户文本
不能改变查询结构。索引与查询两端共用同一分词器（其他模块不得自行切词）。

约定：FTS 表文本是**索引文本，不是原文**——原文以 questions /
knowledge_points 表为准，检索命中后回表取原文。
"""
from __future__ import annotations

import re

import jieba

# 查询侧停用词/泛化词（不用于索引侧；索引仍保留完整原文分词）
STOPWORDS = frozenset({
    # 功能词
    "的", "了", "着", "过", "是", "有", "在", "和", "与", "及", "或", "而", "但",
    "就", "都", "也", "又", "还", "很", "太", "更", "最", "把", "被", "让", "给",
    "从", "向", "对", "为", "以", "于", "中", "上", "下", "里", "外", "前", "后",
    "先", "再", "这", "那", "每", "各", "某", "些", "个", "只", "条", "支", "件",
    "种", "次", "遍", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    "两", "第",
    # 连接/条件词
    "如果", "那么", "因为", "所以", "但是", "然后", "而且", "并且", "或者", "还是",
    # 问句/任务泛化词
    "多少", "几", "什么", "为什么", "怎么", "怎样", "如何", "请问", "请", "帮我",
    "给我", "找", "出", "要", "需要", "可以", "能够", "进行", "完成", "得到",
    "结果", "答案", "答", "问", "求", "已知",
    # 泛化名词（对 FTS 区分度低；与名称加分禁用词对齐）
    "问题", "计算",
    # 任务/问法泛化词（不承载知识点语义）
    "常见", "题型", "哪些", "例子", "例题", "典型", "方法", "方式",
    "情况", "几种", "做法", "步骤", "过程", "意思", "含义",
})

# 数学表达式特征（含这些字符时保留数字 token）
_MATH_RE = re.compile(r"[+\-*/×÷=^%％]|\d+\s*[/／]\s*\d+")
_CJK_RE = re.compile(r"[一-鿿]")
_ALNUM_RE = re.compile(r"[A-Za-z0-9]")
_NUMERIC_RE = re.compile(r"^\d+(?:[./]\d+)?[%％]?$")
_QUERY_WS_RE = re.compile(r"\s+")

MAX_QUERY_TERMS = 8

_jieba_ready = False


def _ensure_init() -> None:
    """惰性初始化 jieba 词典（首次调用约 1s；进程内单例，jieba 内部有锁，线程安全）。"""
    global _jieba_ready
    if not _jieba_ready:
        jieba.initialize()
        _jieba_ready = True


def segment(text: str) -> list[str]:
    """切词：返回非空白 token 列表（索引与查询共用）。"""
    _ensure_init()
    return [tok for tok in jieba.cut(str(text)) if tok.strip()]


def fts_document(text: str) -> str:
    """索引形态：分词后空格连接（写入 FTS 的文档文本）。"""
    return " ".join(segment(text))


def _is_math_like(text: str) -> bool:
    return bool(_MATH_RE.search(str(text)))


def _meaningful(tok: str, math_like: bool) -> bool:
    """查询侧 token 过滤：标点/单字 CJK/停用词/泛化词/无信息数字剔除。"""
    if not tok or tok in STOPWORDS:
        return False
    if not (_CJK_RE.search(tok) or _ALNUM_RE.search(tok)):
        return False  # 纯标点
    if len(tok) == 1:
        if _CJK_RE.search(tok):
            return False  # 单字中文过泛（和/倍/积/行/位/数等）
        return math_like  # 单字母/单数字仅在数学表达式中保留
    if _NUMERIC_RE.match(tok):
        if math_like:
            return True
        # 非数学语境：小数/分数/百分数可能携带信息，普通整数视为无信息数字
        return any(ch in tok for ch in "./%％")
    return True


def fts_query_terms(text: str) -> list[str]:
    """提取有检索价值的查询关键词（去重、限长、保留原始顺序）。"""
    text = str(text)
    math_like = _is_math_like(text)
    terms: list[str] = []
    seen: set[str] = set()
    for tok in segment(text):
        if not _meaningful(tok, math_like) or tok in seen:
            continue
        seen.add(tok)
        terms.append(tok)
    if not terms:
        return []

    if math_like:
        numeric = [t for t in terms if _NUMERIC_RE.match(t)][:4]
        others = [t for t in terms if not _NUMERIC_RE.match(t)]
        others = sorted(others, key=lambda t: (-len(t), t))[:max(MAX_QUERY_TERMS - len(numeric), 0)]
        selected = others + numeric
    else:
        selected = sorted(terms, key=lambda t: (-len(t), t))[:MAX_QUERY_TERMS]

    order = {t: i for i, t in enumerate(terms)}
    selected.sort(key=lambda t: order[t])  # 恢复原顺序，便于调试/日志
    return selected


def _quote(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def fts_query(text: str) -> str:
    """查询形态：语义关键词以固定 ``OR`` 连接，每词双引号包裹。

    返回空短语 ``'""'`` 表示关键词不足（调用方应退回向量检索）。
    """
    terms = fts_query_terms(text)
    if not terms:
        return '""'
    return " OR ".join(_quote(t) for t in terms)
