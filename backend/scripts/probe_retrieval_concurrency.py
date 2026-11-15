"""检索层并发探针（Phase 2 人工验收材料，轻量：10 线程 × 3 次 = 30 次检索）。

目的：为 2C2G 容量评估补充**并发下的延迟与稳定性**证据（内存增量由外部
PowerShell 采样，本脚本不引入 psutil 依赖）。

纪律：
- 只读检索，不写任何业务数据；
- 检索层内部以 threading.Lock 串行化 SQLite 访问与 embedding 推理（§11.2
  推理限并发 1），本探针验证多线程下无异常、延迟可控；
- 不做压力测试、不做长跑（用户要求：不为报告重复跑大量任务）。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.probe_retrieval_concurrency
"""
import json
import statistics
import sys
import threading
import time
from pathlib import Path

from backend.app.retrieval.service import RetrievalService

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT = REPO_ROOT / "data" / "staging" / "retrieval_concurrency_probe.json"

THREADS = 10
CALLS_PER_THREAD = 3
QUERIES = [
    "一元二次方程的判别式", "分数加减应用题", "鸡兔同笼", "植树问题",
    "圆柱的表面积", "正比例和反比例", "平均数问题", "人民币计算",
    "三角形内角和", "百分数应用",
]


def main() -> int:
    svc = RetrievalService()
    latencies: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()

    def worker(tid: int) -> None:
        for i in range(CALLS_PER_THREAD):
            q = QUERIES[(tid * CALLS_PER_THREAD + i) % len(QUERIES)]
            t0 = time.perf_counter()
            try:
                svc.search_questions(q, question_limit=10)
            except Exception as exc:  # pragma: no cover
                with lock:
                    errors.append(f"thread{tid}: {type(exc).__name__}: {exc}")
            with lock:
                latencies.append((time.perf_counter() - t0) * 1000)

    try:
        # 预热（模型/矩阵/连接），不计入并发样本
        svc.search_questions("预热查询", question_limit=5)
        wall0 = time.perf_counter()
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall = (time.perf_counter() - wall0) * 1000
    finally:
        svc.close()

    latencies.sort()
    def pct(p: float) -> float:
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))]

    report = {
        "threads": THREADS,
        "calls_per_thread": CALLS_PER_THREAD,
        "total_calls": len(latencies),
        "errors": errors,
        "wall_ms": round(wall, 1),
        "latency_ms": {
            "p50": round(statistics.median(latencies), 1),
            "p95": round(pct(0.95), 1),
            "max": round(max(latencies), 1),
        },
        "serialization_note": "检索层 threading.Lock 串行化（SQLite 连接 + embedding 推理限并发 1）",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"报告：{OUT}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
