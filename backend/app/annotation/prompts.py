"""Prompt 文件装配与版本指纹（Step 5-B1）。

Prompt 文件入 Git（data/annotations/prompts/），registry.jsonl 记录每个文件的
sha256 指纹——文件内容与指纹不一致即拒绝运行（防漂移）；prompt 改动必须升版本号
（设计文档 §9）。KP 清单运行时从 knowledge_points 读取并注入 {{KP_LIST}} 占位符；
清单快照 sha256 落 annotation_batches.kp_snapshot_sha256。

决策㉔红线：本模块不从 q_kp 读取任何数据；规则标签不出现在 prompt 的任何部分
（测试断言 prompt 文件全文不含 q_kp 相关内容）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Sequence

# backend/app/annotation/prompts.py -> annotation(0) -> app(1) -> backend(2) -> 仓库根(3)
REPO_ROOT = Path(__file__).resolve().parents[3]
PROMPTS_DIR = REPO_ROOT / "data" / "annotations" / "prompts"
REGISTRY_PATH = PROMPTS_DIR / "registry.jsonl"
KP_LIST_PLACEHOLDER = "{{KP_LIST}}"


def sha256_of_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_registry() -> list[dict]:
    if not REGISTRY_PATH.exists():
        raise FileNotFoundError(f"Prompt 注册表不存在：{REGISTRY_PATH}")
    rows = []
    for line in REGISTRY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def verify_registry() -> dict[str, dict]:
    """校验注册表：每个 prompt_id/version 的文件存在且 sha256 一致。

    返回 {prompt_id: 注册行}。不一致/缺失即抛 RuntimeError（防漂移 fail-fast）。
    """
    registry: dict[str, dict] = {}
    for row in load_registry():
        path = REPO_ROOT / row["file"]
        if not path.exists():
            raise RuntimeError(f"Prompt 文件缺失：{row['file']}（prompt_id={row['prompt_id']}）")
        actual = sha256_of_file(path)
        if actual != row["sha256"]:
            raise RuntimeError(
                f"Prompt 指纹不一致：{row['file']}（registry={row['sha256'][:12]}…，"
                f"actual={actual[:12]}…）——文件被修改后未升版本号，禁止运行"
            )
        registry[row["prompt_id"]] = row
    return registry


def resolve_prompt_path(prompt_id: str, prompt_version: str) -> Path:
    """按 registry 解析 prompt 文件路径并校验指纹。"""
    for row in load_registry():
        if row["prompt_id"] == prompt_id and row["version"] == prompt_version:
            path = REPO_ROOT / row["file"]
            if sha256_of_file(path) != row["sha256"]:
                raise RuntimeError(f"Prompt 指纹不一致：{row['file']}")
            return path
    raise KeyError(f"registry 中无 prompt_id={prompt_id!r} version={prompt_version!r}")


def kp_lines(kp_rows: Sequence[dict]) -> str:
    """KP 清单渲染（A 臂全 82 行 / B 臂候选子集同格式）。"""
    return "\n".join(
        f"- {r['kp_id']} {r['name']}：{r['description'] or ''}" for r in kp_rows
    )


def kp_snapshot_sha256(kp_rows: Sequence[dict]) -> str:
    """清单快照指纹（落 annotation_batches.kp_snapshot_sha256）。"""
    return hashlib.sha256(kp_lines(kp_rows).encode("utf-8")).hexdigest()


def build_system_prompt(prompt_path: Path, kp_rows: Sequence[dict]) -> tuple[str, str]:
    """渲染 prompt 文件（注入 KP 清单），返回 (system_prompt, kp_snapshot_sha256)。"""
    template = prompt_path.read_text(encoding="utf-8")
    if KP_LIST_PLACEHOLDER not in template:
        raise RuntimeError(f"Prompt 文件缺少 {KP_LIST_PLACEHOLDER} 占位符：{prompt_path}")
    lines = kp_lines(kp_rows)
    return template.replace(KP_LIST_PLACEHOLDER, lines), kp_snapshot_sha256(kp_rows)


def build_user_message(stem: str) -> str:
    return f"题干：{stem}\n\n请输出 JSON。"
