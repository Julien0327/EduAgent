"""打标 CLI（Step 5-B1）。真实运行 = 5-B2 试跑（500 题双臂，范围与预算由用户批准）。

用法（仓库根执行）：
    .venv/Scripts/python.exe -m backend.scripts.tag_questions --arm A \
        --qids data/annotations/trial500_qids.jsonl \
        --budget-group trial500 --budget-cny 20 \
        --budget-warn-cny 10 --budget-pause-cny 15

安全边界（5-B1/5-B2 交付要求）：
- 本脚本不自动执行；仅显式运行才装配真实客户端。
- DEEPSEEK_API_KEY 只做存在性检查（OpenAICompatClient.from_settings），不打印、不入日志。
- arm=B 需要 embedding 召回器——占位实现 fail-fast（RecallNotAvailableError），
  真实召回实现待依赖批准后落地。
- 预算三线（决策㉖）：--budget-cny 硬停线（必须 >0）；--budget-warn-cny 提醒线
  （达到→通知记录不停止）；--budget-pause-cny 暂停线（达到→停止新请求等待人工
  确认）。同一 budget_group 的历史批次费用全部计入累计——新建批次/续跑无法绕过。
- 两臂（A/B）共用同一 --qids 冻结题集与同一 --budget-group。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backend.app.annotation.client import OpenAICompatClient
from backend.app.annotation.executor import ExecutorConfig, run_annotation
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
    parser.add_argument("--budget-group", required=True, help="预算组（同组批次共享闸门与累计）")
    parser.add_argument("--budget-cny", type=float, required=True,
                        help="硬停线 cap（元）；500 题双臂试跑=20")
    parser.add_argument("--budget-warn-cny", type=float, default=None,
                        help="提醒线（元，默认 cap×0.5）；试跑传 10（通知不停止）")
    parser.add_argument("--budget-pause-cny", type=float, default=None,
                        help="暂停线（元，默认 cap×0.75）；试跑传 15（停止新请求待确认）")
    parser.add_argument("--prompt-version", default="v1.0")
    parser.add_argument("--max-items", type=int, default=None)
    parser.add_argument("--max-concurrency", type=int, default=8)
    parser.add_argument("--embed-cache-dir", default=None,
                        help="B 臂模型缓存目录（默认 data/models/fastembed，D 盘不入 Git）")
    args = parser.parse_args()

    resolved_db = Path(args.db) if args.db else db.get_db_path()
    if args.arm == "B":
        # B 臂：真实 embedding 召回（bge-small-zh-v1.5 本地推理，零 API 费用；
        # 模型缓存 data/models/fastembed 不入 Git）。KP 定义与 A 臂同源（不触 q_kp）。
        from backend.app.annotation.executor import _load_kp_rows
        from backend.app.annotation.recall import EmbeddingRecaller, REPO_MODELS_DIR
        kp_rows = _load_kp_rows(resolved_db)
        embed_dir = Path(args.embed_cache_dir) if args.embed_cache_dir else REPO_MODELS_DIR
        recaller = EmbeddingRecaller(kp_rows, cache_dir=embed_dir)
        print(f"B 臂召回器就绪：{len(kp_rows)} 个 KP，模型缓存 {embed_dir}")
    else:
        recaller = None

    qids = load_qids(Path(args.qids))
    cfg = ExecutorConfig(
        db_path=resolved_db,
        qids=qids,
        arm=args.arm,
        prompt_version=args.prompt_version,
        budget_group=args.budget_group,
        budget_cap_cny=args.budget_cny,
        budget_warn_at_cny=args.budget_warn_cny,
        budget_pause_at_cny=args.budget_pause_cny,
        max_items=args.max_items,
        concurrency_max=args.max_concurrency,
    )
    client = OpenAICompatClient.from_settings()  # Key 存在性检查（不打印）
    summary = run_annotation(cfg, client, recaller=recaller)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "completed" else 2


if __name__ == "__main__":
    sys.exit(main())
