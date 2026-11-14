"""检索层 embedding 服务（Phase 2 / S3，决策㉛②）。

模型：bge-small-zh-v1.5（BAAI，512 维）量化 ONNX（``model_optimized.onnx``，
94.8MB，复用 fastembed 的本地缓存文件——决策㉛②：不做 torch 自导出）。

**直连 onnxruntime + tokenizers**（不经过 fastembed API）：§4.3 的服务器形态
本就是"onnxruntime + tokenizer"；直连后 session options 可控
（``enable_cpu_mem_arena=False`` 省掉 arena 预分配、``intra_op_num_threads=1``
对齐 §11.2 推理限并发 1）。实测动机：fastembed API 封装内存增量 ~224MB
（超 §4.5 ≤150MB 目标），直连后回落（见 STATUS 实测表）。

池化与归一化与 fastembed 对齐（CLS pooling + L2 归一化，一致性与 fastembed
输出余弦相似度 ≥0.999 由测试验证——§4.5"导出一致性"口径的实现等价替代）。

bge 官方用法：**查询侧**加指令前缀"为这个句子生成表示以用于检索相关文章："，
**索引侧**（KP 描述）不加——embed_query / embed_documents 显式区分。
"""
from __future__ import annotations

import threading
from pathlib import Path

MODEL_NAME = "BAAI/bge-small-zh-v1.5"
BGE_QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："
DIMENSION = 512
MAX_SEQ_LEN = 256  # KP 描述短文本（p90 < 100 字），256 足够且省算力

# 模型缓存目录：仓库内 data/models/fastembed（.gitignore models/ 覆盖，不入 Git；
# 与 annotation/recall.py 共用同一缓存，Phase 1 遗产直接复用）
REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_CACHE_DIR = REPO_ROOT / "data" / "models" / "fastembed"


class EmbeddingNotAvailableError(RuntimeError):
    """embedding 推理不可用（onnxruntime/tokenizers 未装或模型缓存缺失）。"""


def _locate_model_files(cache_dir: Path) -> tuple[Path, Path]:
    """在 fastembed 缓存结构（models--Qdrant--bge-small-zh-v1.5/snapshots/<sha>/）
    下定位 model_optimized.onnx 与 tokenizer.json。"""
    cache_dir = Path(cache_dir)
    snapshots = cache_dir / "models--Qdrant--bge-small-zh-v1.5" / "snapshots"
    if snapshots.is_dir():
        for snap in sorted(snapshots.iterdir()):
            onnx = snap / "model_optimized.onnx"
            tok = snap / "tokenizer.json"
            if onnx.is_file() and tok.is_file():
                return onnx, tok
    onnx = cache_dir / "model_optimized.onnx"
    tok = cache_dir / "tokenizer.json"
    if onnx.is_file() and tok.is_file():
        return onnx, tok
    raise EmbeddingNotAvailableError(
        f"未在 {cache_dir} 找到 bge-small-zh-v1.5 的 onnx/tokenizer"
        "（先经 fastembed 下载模型缓存，见 requirements.txt 注释）")


class EmbeddingService:
    """bge-small-zh-v1.5 直连推理（CLS pooling，L2 归一化输出，线程安全）。"""

    def __init__(self, cache_dir: Path | str | None = None):
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise EmbeddingNotAvailableError(
                "embedding 依赖未安装（onnxruntime/tokenizers，见 requirements.txt）") from exc
        self._np = np
        onnx_path, tok_path = _locate_model_files(Path(cache_dir or MODEL_CACHE_DIR))
        so = ort.SessionOptions()
        so.enable_cpu_mem_arena = False   # 省 arena 预分配（内存增量调优，2026-11-14）
        so.intra_op_num_threads = 1       # §11.2：embedding 推理限并发 1
        so.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(onnx_path), sess_options=so, providers=["CPUExecutionProvider"])
        self._tokenizer = Tokenizer.from_file(str(tok_path))
        self._tokenizer.enable_truncation(max_length=MAX_SEQ_LEN)
        self._tokenizer.enable_padding()  # 批内 pad 到同长（CLS pooling 只取 [0] 位，pad 不入向量）
        self._lock = threading.Lock()

    @property
    def dimension(self) -> int:
        return DIMENSION

    def _encode(self, texts: list[str], with_query_prefix: bool) -> "np.ndarray":
        """编码 → CLS pooling → L2 归一化（(n, 512) float32）。"""
        import numpy as np
        if with_query_prefix:
            texts = [BGE_QUERY_PREFIX + str(t) for t in texts]
        encs = self._tokenizer.encode_batch([str(t) for t in texts])
        ids = np.array([e.ids for e in encs], dtype="int64")
        mask = np.array([e.attention_mask for e in encs], dtype="int64")
        type_ids = np.zeros_like(ids)
        with self._lock:  # §11.2：推理串行
            hidden = self._session.run(
                ["last_hidden_state"],
                {"input_ids": ids, "attention_mask": mask, "token_type_ids": type_ids},
            )[0]
        vecs = hidden[:, 0, :].astype("float32")  # CLS pooling（bge 官方用法）
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.maximum(norms, 1e-12)

    def embed_documents(self, texts: list[str]) -> "np.ndarray":
        """索引侧：批量嵌入（不加查询前缀），返回 (n, 512) float32 已 L2 归一化。"""
        return self._encode(texts, with_query_prefix=False)

    def embed_query(self, text: str) -> "np.ndarray":
        """查询侧：单条嵌入（加 bge 检索指令前缀），返回 (512,) float32 已 L2 归一化。"""
        return self._encode([text], with_query_prefix=True)[0]


_singleton: EmbeddingService | None = None
_singleton_lock = threading.Lock()


def get_embedding_service() -> EmbeddingService:
    """进程内单例（惰性；首次调用加载模型，FastAPI lifespan 预热避免首查延迟）。"""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = EmbeddingService()
        return _singleton
