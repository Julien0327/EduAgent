"""打标 CLI（Step 5-B1）。真实运行 = 5-B2 试跑（500 题双臂，范围与预算由用户批准）。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.tag_questions --arm A \
        --qids data/annotations/trial500_qids.jsonl \
        --budget-group trial500 --budget-cny 10

安全边界（5-B1 交付要求）：
- 本脚本不自动执行；仅显式运行才装配真实客户端。
- DEEPSEEK_API_KEY 只做存在性检查（OpenAICompatClient.from_settings），不打印、不入日志。
- arm=B 需要 embedding 召回器——当前为占位实现（RecallNotAvailableError），fail-fast 拒绝，
  真实召回实现待依赖批准后于 5-B2 落地。
- 预算 cap 必须 >0（决策②：拒绝无预算运行）；试跑 A/B 两臂共用同一 --budget-group
  的 ¥10 cap（决策②③）。
- 本脚本自身不做任何真实 API 调用的测试；Mock 验证见 backend/tests/test_annotation_executor.py。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.app.annotation.client import OpenAICompatClient
from backend.app.annotation.executor import ExecutorConfig, run_annotation
from backend.app.annotation.recall import NotImplementedRecaller, RecallNotAvailableError
from backend.app.storage import db


def load_qids(path: Path) -> list[str]:
    qids: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                qids.append(json.loads(line)["q_id"])
    if not qids:
        raise ValueError(f"qids 文件为空：{path}")
    return qids


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 打标执行 CLI（真实运行前须用户批准范围与预算）")
    parser.add_argument("--arm", choices=("A", "B"), required=True)
    parser.add_argument("--qids", required=True, help="试验集 q_id JSONL（build_trial500 产出）")
    parser.add_argument("--db", default=None, help="业务库路径（默认取集中配置）")
    parser.add_argument("--budget-group", required=True, help="预算组（同组批次共享闸门）")
    parser.add_argument("--budget-cny", type=float, required=True, help="预算组 cap（元）")
    parser.add_argument("--prompt-version", default="v1.0")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--max-concurrency", type=int, default=8)
    args = parser.parse_args()

    if args.arm == "B":
        raise RecallNotAvailableError(
            "B 臂 embedding 召回尚未实现（待依赖批准后 5-B2 落地）；当前仅 A 臂可真实运行")

    qids = load_qids(Path(args.qids))
    cfg = ExecutorConfig(
        db_path=Path(args.db) if args.db else db.get_db_path(),
        qids=qids,
        arm=args.arm,
        prompt_version=args.prompt_version,
        budget_group=args.budget_group,
        budget_cap_cny=args.budget_cny,
        max_items=args.max_items,
        concurrency_max=args.max_concurrency,
    )
    client = OpenAICompatClient.from_settings()  # Key 存在性检查（不打印）
    summary = run_annotation(cfg, client)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "completed" else 2


if __name__ == "__main__":
    sys.exit(main())
