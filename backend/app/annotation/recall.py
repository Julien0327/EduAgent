"""B 臂候选知识点召回（Step 5-B2）。

设计文档 §3/§6/§13：B 臂 = 本地 embedding（BAAI/bge-small-zh-v1.5，512 维）召回
top-K 候选 + LLM 复判；决策③：B 为对照实验与未来备选，A 为主线。

实现要点：
- 候选文本 = KP name+description（与 A 臂 prompt 同源快照；**不触 q_kp**——决策㉔）；
- 82 条 KP 向量 L2 归一化后驻内存，每题 embedding 题干一次 → 余弦相似度 top-K；
- 模型缓存默认仓库内 ``data/models/fastembed``（.gitignore ``models/`` 覆盖，不入 Git，
  用户指定 D 盘）；
- 本地推理零 API 费用。实测（2026-11-14，fastembed 0.9.1/onnxruntime 1.32.0）：
  首次下载 18.6s、缓存 90.8MB、维度 512、单条 ~2ms、82 条批量 0.06s。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Protocol

RECALL_TOP_K = 10  # 设计文档 §8：候选数量为设计参数，5-B2 以 50 题 gold 实测 recall@10 校准
MODEL_NAME = "BAAI/bge-small-zh-v1.5"  # 项目既定模型（用户指令：不得擅自替换）
# 模型缓存默认目录：仓库内 D 盘 data/models/fastembed（.gitignore models/ 覆盖，不入 Git）
REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_MODELS_DIR = REPO_ROOT / "data" / "models" / "fastembed"


class CandidateRecaller(Protocol):
    """B 臂候选召回协议：返回按相似度降序的 kp_id 列表（可为空）。"""

    def recall(self, q_id: str, stem: str) -> list[str]:
        ...


class RecallNotAvailableError(RuntimeError):
    """B 臂真实召回不可用（依赖未装/模型缺失）。"""


class NotImplementedRecaller:
    """占位实现：保留用于依赖缺失环境的明确 fail-fast（不静默降级）。"""

    def recall(self, q_id: str, stem: str) -> list[str]:
        raise RecallNotAvailableError(
            "B 臂 embedding 召回不可用：fastembed/onnxruntime 未安装或模型未下载"
            "（依赖清单见设计文档 §13；测试请注入 Mock 召回器）")


class EmbeddingRecaller:
    """真实召回器：fastembed + bge-small-zh-v1.5 本地推理。"""

    def __init__(self, kp_rows: list[dict], cache_dir: Path | str | None = None,
                 top_k: int = RECALL_TOP_K):
        try:
            import numpy as np
        except ImportError as exc:  # pragma: no cover
            raise RecallNotAvailableError("需要 numpy（fastembed 传递依赖）") from exc
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise RecallNotAvailableError(
                "fastembed 未安装（依赖清单见设计文档 §13）") from exc
        self._np = np
        self._top_k = max(1, int(top_k))
        self._embed_lock = threading.Lock()  # fastembed 推理串行化（并发 worker 安全防护）
        if not kp_rows:
            raise ValueError("kp_rows 不能为空")
        self._kp_ids = [r["kp_id"] for r in kp_rows]
        texts = [f"{r['name']}：{r['description'] or ''}" for r in kp_rows]
        self._model = TextEmbedding(
            model_name=MODEL_NAME,
            cache_dir=str(cache_dir) if cache_dir else None)
        vecs = np.asarray(list(self._model.embed(texts)), dtype="float32")
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        self._kp_vecs = vecs / np.maximum(norms, 1e-12)
        if self._kp_vecs.shape[0] != len(self._kp_ids):
            raise RuntimeError("KP 向量数量与 kp_rows 不一致")

    def recall(self, q_id: str, stem: str) -> list[str]:
        np = self._np
        with self._embed_lock:  # 单模型会话推理串行化（单条 ~2ms，串行无吞吐影响）
            q = np.asarray(list(self._model.embed([str(stem)])), dtype="float32")[0]
        q = q / max(float(np.linalg.norm(q)), 1e-12)
        sims = self._kp_vecs @ q
        k = min(self._top_k, len(self._kp_ids))
        order = np.argsort(-sims)[:k]
        return [self._kp_ids[int(i)] for i in order]
