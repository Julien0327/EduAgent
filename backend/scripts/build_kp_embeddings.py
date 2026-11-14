"""知识点向量构建脚本（Phase 2 / S3，决策㉛②）。

对 knowledge_points 全表（初中 303 + 小学 82 = 385 个）批量生成 bge-small-zh-v1.5
向量（512d float32 L2 归一化），写入 V1 预留的 ``embedding`` BLOB 列
（numpy ``.tobytes()`` 序列化，读取方 ``np.frombuffer(blob, dtype='float32')``）。

嵌入文本 = ``{name}：{description}``（与 annotation/recall.py 同构：名称+描述，
语义密度最高）；**只嵌知识点不嵌题目**（§4.3 决策：题目由结构化条件过滤，
向量语义检索题干引入噪声——面试考点）。

幂等：全量覆盖语义（385 行全部重写），重跑结果一致（确定性模型推理）。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.build_kp_embeddings [--db <路径>]
"""
import argparse
import json
import sys
import time
from pathlib import Path

from backend.app.retrieval.embedding import MODEL_NAME, EmbeddingService
from backend.app.storage import db

REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = REPO_ROOT / "data" / "staging" / "kp_embeddings_report.json"


def run(db_path) -> dict:
    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT kp_id, name, description FROM knowledge_points ORDER BY kp_id"
    ).fetchall()
    if not rows:
        raise ValueError("knowledge_points 为空——先跑 build_graph.py / build_primary_kp.py")

    t0 = time.time()
    svc = EmbeddingService()  # 含模型加载（实测首载 ~1-2s）
    load_seconds = round(time.time() - t0, 2)

    texts = [f"{r['name']}：{r['description'] or ''}" for r in rows]
    t1 = time.time()
    vecs = svc.embed_documents(texts)
    embed_seconds = round(time.time() - t1, 2)

    if vecs.shape[0] != len(rows):
        raise RuntimeError(f"向量数量 {vecs.shape[0]} != KP 行数 {len(rows)}")
    dim = vecs.shape[1]

    t2 = time.time()
    with conn:
        conn.executemany(
            "UPDATE knowledge_points SET embedding=? WHERE kp_id=?",
            [(vecs[i].tobytes(), rows[i]["kp_id"]) for i in range(len(rows))],
        )
    write_seconds = round(time.time() - t2, 2)

    # 核对：落库行数与向量可解析性
    written = conn.execute(
        "SELECT COUNT(*) n FROM knowledge_points WHERE embedding IS NOT NULL"
    ).fetchone()["n"]
    probe = conn.execute(
        "SELECT embedding FROM knowledge_points WHERE kp_id=?", (rows[0]["kp_id"],)
    ).fetchone()["embedding"]
    conn.close()
    if len(probe) != dim * 4:
        raise RuntimeError(f"BLOB 长度 {len(probe)} != {dim} * 4（float32 序列化不一致）")

    return {
        "model": MODEL_NAME,
        "dimension": dim,
        "kp_total": len(rows),
        "written": written,
        "blob_bytes_per_kp": len(probe),
        "model_load_seconds": load_seconds,
        "embed_seconds": embed_seconds,
        "write_seconds": write_seconds,
        "vector_bytes_total": int(vecs.nbytes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="知识点向量构建（bge→embedding BLOB，幂等覆盖）")
    parser.add_argument("--db", default=None, help="业务库路径（默认 data/db/edumate.sqlite）")
    args = parser.parse_args()

    report = run(args.db)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"KP 向量构建完成：{report['kp_total']} 个 KP × {report['dimension']}d，"
          f"落库 {report['written']} 行（{report['blob_bytes_per_kp']}B/个）")
    print(f"耗时：模型载入 {report['model_load_seconds']}s + "
          f"推理 {report['embed_seconds']}s + 写库 {report['write_seconds']}s")
    print(f"报告已写入：{REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
