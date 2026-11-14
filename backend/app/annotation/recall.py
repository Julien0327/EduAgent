"""B 臂候选知识点召回协议（Step 5-B1）。

设计文档 §3/§6：B 臂 = 本地 embedding（bge-small-zh-v1.5）召回 top-K 候选 + LLM 复判。
embedding 依赖（onnxruntime/fastembed）尚未安装且未经用户批准——本模块只定义协议与
明确的不可用错误；真实召回实现属于 5-B2 前置项（STATUS 已知问题）。测试注入 Mock。
"""
from __future__ import annotations

from typing import Protocol

RECALL_TOP_K = 10  # 设计文档 §8：候选数量为设计参数，5-B2 以 gold 实测 recall@10 校准


class CandidateRecaller(Protocol):
    """B 臂候选召回协议：返回按相似度降序的 kp_id 列表（可为空）。"""

    def recall(self, q_id: str, stem: str) -> list[str]:
        ...


class RecallNotAvailableError(RuntimeError):
    """B 臂真实召回不可用（依赖未装/未经用户批准）。"""


class NotImplementedRecaller:
    """占位实现：B 臂真实运行（5-B2）前必须替换为 embedding 召回器。

    任何尝试用占位召回器跑真实 B 臂批次的操作都会 fail-fast，不会静默降级。
    """

    def recall(self, q_id: str, stem: str) -> list[str]:
        raise RecallNotAvailableError(
            "B 臂 embedding 召回尚未实现（需 onnxruntime/fastembed 依赖经用户批准后"
            "于 5-B2 实现）；测试请注入 Mock 召回器"
        )
