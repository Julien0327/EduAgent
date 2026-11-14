"""bge-small-zh-v1.5 ONNX int8 动态量化脚本（Phase 2 / S3，§4.3/§4.5）。

背景（2026-11-14 实测）：fastembed 缓存的 ``model_optimized.onnx`` 是 **FP32**
（94.8MB = 24M 参数 × 4B），内存增量 ~194MB 超 §4.5 ≤150MB 目标；§4.3/§11.2
的量化预算（~90MB 常驻、2C2G 服务器）从未真正落地。

本脚本在**开发机**一次性完成（服务器不装 onnx 量化工具链，§4.3）：
  FP32 ONNX → onnxruntime.quantization.quantize_dynamic（权重 int8）
  → 输出 data/models/bge-int8/（model.onnx + tokenizer.json，不入 Git）

校验（§4.5 INT8 量化一致性，目标 ≥0.99）：同批文本 FP32 vs INT8 余弦相似度；
不达标即拒绝交付（退出非 0），不静默替换。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.quantize_bge_onnx
"""
import json
import shutil
import sys
import time
from pathlib import Path

from backend.app.retrieval.embedding import MODEL_CACHE_DIR, _locate_model_files

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "data" / "models" / "bge-int8"
COS_THRESHOLD = 0.99  # §4.5 INT8 量化一致性阈值

# 一致性校验样例：覆盖学科词/题型/数字/长句等 KP 描述形态
SAMPLE_TEXTS = [
    "一元二次方程的解法与判别式",
    "分数加减混合运算应用题",
    "小明有5个苹果，又买了3个，一共有多少个？",
    "已知方程 x^2-3x+2=0 的两个根，求韦达定理的应用",
    "圆柱的表面积与体积计算",
    "正比例与反比例的关系判断",
    "植树问题两端都栽时的间隔数计算",
    "百分数与分数小数的互化",
    "三角形的内角和定理及其推论",
    "统计图中的条形图折线图扇形图选择",
]


def main() -> int:
    try:
        import numpy as np
        import onnx
        from onnx import TensorProto, shape_inference
        from onnxruntime.quantization import QuantType, quantize_dynamic
    except ImportError as exc:
        print(f"缺少依赖：{exc}")
        return 1

    fp32_path, tok_path = _locate_model_files(Path(MODEL_CACHE_DIR))
    print(f"FP32 模型：{fp32_path}（{fp32_path.stat().st_size/1e6:.1f}MB）")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 预处理：shape inference（fastembed 导出的 ONNX 缺形状信息，量化器需要；
    # 另设 DefaultTensorType 兜底——优化图内部分张量推断不出类型）
    inferred_path = OUT_DIR / "fp32_inferred.onnx"
    if not inferred_path.exists():
        model = onnx.load(str(fp32_path))
        model = shape_inference.infer_shapes(model)
        onnx.save(model, str(inferred_path))
        print(f"shape inference 完成：{inferred_path}")

    int8_path = OUT_DIR / "model.onnx"
    t0 = time.time()
    quantize_dynamic(
        model_input=str(inferred_path),
        model_output=str(int8_path),
        weight_type=QuantType.QInt8,
        extra_options={"DefaultTensorType": TensorProto.FLOAT},
    )
    elapsed = time.time() - t0
    tok_out = OUT_DIR / "tokenizer.json"
    shutil.copyfile(tok_path, tok_out)
    print(f"量化完成：{int8_path}（{int8_path.stat().st_size/1e6:.1f}MB，{elapsed:.1f}s）")

    # 一致性校验：FP32 vs INT8 同批文本余弦
    import onnxruntime as ort

    def embed(onnx_path: Path) -> np.ndarray:
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        sess = ort.InferenceSession(str(onnx_path), sess_options=so,
                                    providers=["CPUExecutionProvider"])
        from tokenizers import Tokenizer
        tok = Tokenizer.from_file(str(tok_out))
        tok.enable_truncation(max_length=256)
        tok.enable_padding()
        encs = tok.encode_batch(SAMPLE_TEXTS)
        ids = np.array([e.ids for e in encs], dtype="int64")
        mask = np.array([e.attention_mask for e in encs], dtype="int64")
        zero = np.zeros_like(ids)
        hidden = sess.run(
            ["last_hidden_state"],
            {"input_ids": ids, "attention_mask": mask, "token_type_ids": zero},
        )[0]
        vecs = hidden[:, 0, :].astype("float32")
        return vecs / np.maximum(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-12)

    v32 = embed(fp32_path)
    v8 = embed(int8_path)
    sims = (v32 * v8).sum(axis=1)
    worst = float(sims.min())
    print(f"INT8 量化一致性（§4.5，目标 ≥{COS_THRESHOLD}）：最低余弦 = {worst:.4f}")
    for t, s in zip(SAMPLE_TEXTS, sims):
        flag = "OK " if s >= COS_THRESHOLD else "LOW"
        print(f"  [{flag}] {s:.4f}  {t[:24]}")

    report = {
        "fp32_mb": round(fp32_path.stat().st_size / 1e6, 1),
        "int8_mb": round(int8_path.stat().st_size / 1e6, 1),
        "min_cosine": round(worst, 4),
        "mean_cosine": round(float(sims.mean()), 4),
        "threshold": COS_THRESHOLD,
        "elapsed_seconds": round(elapsed, 1),
        "samples": len(SAMPLE_TEXTS),
    }
    (OUT_DIR / "quantize_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{OUT_DIR / 'quantize_report.json'}")

    if worst < COS_THRESHOLD:
        print("一致性不达标——拒绝交付（int8 目录保留供排查，生产加载方不切换）")
        return 1
    print("一致性达标 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
