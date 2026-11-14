"""统一中文分词入口（Phase 2 / S1，决策㉛①）。

FTS5 内置 unicode61 对连续中文串只产生一个 token（Step 4 实测：中文子词不可查，
"应用题"查不到含"应用题"的文档）。本模块以 jieba 预分词解决：

- **索引侧**（``fts_document``）：文本切词后以空格连接写入 FTS——unicode61 按空格
  分 token，得到的即词粒度 token 序列；
- **查询侧**（``fts_query``）：同一分词器切分查询词，逐词双引号包裹转义后拼接
  MATCH 串。引号包裹同时隔离 FTS5 查询语法（AND/OR/NOT/NEAR/^/* 等），用户输入
  只能作为被匹配的短语序列（隐式 AND），不能改变查询结构（注入面收口在此）。

两端共用同一入口，保证索引与查询形态一致。**其他模块不得自行切词**
（单一事实来源；分词规则变更只需改这里并重跑幂等链）。

约定：FTS 表（questions_fts / kp_fts）中的文本是分词形态的**索引文本，不是原文**——
原文以 questions / knowledge_points 表为准，检索命中后回表取原文，禁止把 FTS
内容当题目/知识点内容使用（单测覆盖该断言）。
"""
from __future__ import annotations

import jieba

_jieba_ready = False


def _ensure_init() -> None:
    """惰性初始化 jieba 词典（首次调用约 1s；进程内单例，jieba 内部有锁，线程安全）。"""
    global _jieba_ready
    if not _jieba_ready:
        jieba.initialize()
        _jieba_ready = True


def segment(text: str) -> list[str]:
    """切词：返回非空白 token 列表。

    标点保留在结果中（jieba 会输出独立标点 token）——写入 FTS 后 unicode61 将其
    作为分隔符忽略，不产生额外 token，无需在此过滤。
    """
    _ensure_init()
    return [tok for tok in jieba.cut(str(text)) if tok.strip()]


def fts_document(text: str) -> str:
    """索引形态：分词后空格连接（写入 FTS 的文档文本）。"""
    return " ".join(segment(text))


def fts_query(text: str) -> str:
    """查询形态：分词后逐词双引号包裹（token 内 ``"`` 转义为 ``""``）空格连接。

    返回值只用于 ``questions_fts MATCH ?`` / ``kp_fts MATCH ?`` 的参数绑定。
    输入切不出任何 token 时返回空短语 ``""``（合法 MATCH，零命中）——调用方
    可据此提前返回空结果，避免无谓查询。
    """
    tokens = segment(text)
    if not tokens:
        return '""'
    return " ".join('"' + tok.replace('"', '""') + '"' for tok in tokens)
