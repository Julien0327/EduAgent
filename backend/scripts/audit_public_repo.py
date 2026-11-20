"""公开仓库合规审计（Phase 6 S0/S1）。

用途：在仓库保持公开的前提下，检查「工作区 + 全部 Git 历史」是否包含
密钥、隐私信息、个人绝对路径或非工程类材料。只读，不修改仓库。

用法（仓库根执行）：
    python backend/scripts/audit_public_repo.py
    python backend/scripts/audit_public_repo.py --json data/staging/report.json \
        --markdown data/staging/report.md

退出码：0 = 无 high 级发现；1 = 存在 high 级发现；2 = 运行错误。

设计边界：
- 只做模式匹配，不判断语义；命中需人工复核。
- 输出中的命中片段一律脱敏（保留前 4 位），避免报告本身泄露密钥。
- 历史扫描按 blob 去重（同一内容只扫一次），路径取首次出现位置。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# (名称, 严重级别, 正则, 说明)
PATTERNS = [
    ("deepseek_key", "high", re.compile(rb"sk-[A-Za-z0-9]{20,}"), "DeepSeek/OpenAI 风格 API Key"),
    ("private_key", "high", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"), "私钥文件内容"),
    ("aliyun_ak", "high", re.compile(rb"LTAI[A-Za-z0-9]{12,}"), "阿里云 AccessKey"),
    ("aws_ak", "high", re.compile(rb"AKIA[0-9A-Z]{16}"), "AWS AccessKey"),
    ("github_token", "high", re.compile(rb"gh[pousr]_[A-Za-z0-9]{36,}"), "GitHub Token"),
    (
        "env_secret_assignment",
        "high",
        re.compile(
            rb"(?im)^[ \t]*(?:DEEPSEEK_API_KEY|EDUMATE_API_TOKEN|EDUMATE_MCP_ADMIN_TOKEN|LLM_API_KEY|PASSWORD|SECRET)[ \t]*=[ \t]*([^\s#\r\n]{6,})"
        ),
        "环境变量文件中的非空密钥赋值",
    ),
    ("cn_mobile", "high", re.compile(rb"(?<!\d)1[3-9]\d{9}(?!\d)"), "中国大陆手机号"),
    ("email", "low", re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "邮箱地址（可能是公开署名，也可能是个隐私）"),
    ("windows_user_path", "medium", re.compile(rb"[A-Za-z]:\\\\Users\\\\[A-Za-z0-9_.-]+"), "Windows 个人目录绝对路径"),
    ("personal_project_path", "medium", re.compile(rb"[A-Za-z]:\\\\Competition[ \t]?project"), "本机项目目录绝对路径"),
    ("posix_home_path", "medium", re.compile(rb"/home/[A-Za-z0-9_.-]+/"), "Linux 个人目录绝对路径"),
    (
        "career_content",
        "medium",
        re.compile(r"(面试|求职|简历|实习|招聘|offer|Offer)".encode("utf-8")),
        "非工程类个人材料措辞（公开仓库应为纯工程内容）",
    ),
]

# 检测器自身含检测词，不作为被扫描内容
EXEMPT_PATHS = {"backend/scripts/audit_public_repo.py"}

TEXT_EXTENSIONS = {
    ".py", ".js", ".html", ".css", ".md", ".json", ".jsonl", ".yml", ".yaml",
    ".txt", ".ini", ".toml", ".sql", ".example", ".cfg", ".conf", ".sh", ".ps1",
}


def _is_text_path(path: str) -> bool:
    suffix = Path(path).suffix.lower()
    if suffix in TEXT_EXTENSIONS:
        return True
    return Path(path).name in {".env.example", ".gitignore", ".dockerignore", "Dockerfile"}


def _mask(value: bytes) -> str:
    text = value.decode("utf-8", errors="replace")
    if len(text) <= 6:
        return text[:2] + "***"
    return text[:4] + "***" + f"（{len(text)} chars）"


def _line_number(data: bytes, offset: int) -> int:
    return data.count(b"\n", 0, offset) + 1


_PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:sk-)?(?:x+|y+|z+|\*+|<[^>]*>|your[_-].*|test.*|dummy.*|example.*|placeholder.*|changeme.*)$"
)


def _looks_like_placeholder(value: bytes) -> bool:
    text = value.decode("utf-8", errors="replace").strip().strip("'\"")
    return bool(_PLACEHOLDER_RE.match(text))


def scan_bytes(data: bytes, source: str) -> list[dict]:
    findings: list[dict] = []
    if b"\x00" in data[:4096]:
        return findings
    for name, severity, pattern, description in PATTERNS:
        for match in pattern.finditer(data):
            candidate = match.group(1) if match.lastindex else match.group(0)
            if _looks_like_placeholder(candidate):
                continue
            findings.append(
                {
                    "pattern": name,
                    "severity": severity,
                    "description": description,
                    "source": source,
                    "line": _line_number(data, match.start()),
                    "match": _mask(match.group(0)),
                }
            )
    return findings


def _git(*args: str) -> bytes:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {result.stderr.decode('utf-8', 'replace')}"
        )
    return result.stdout


def scan_worktree() -> list[dict]:
    findings: list[dict] = []
    tracked = _git("ls-files").decode("utf-8", "replace").splitlines()
    for path in tracked:
        if path in EXEMPT_PATHS or not _is_text_path(path):
            continue
        file_path = Path(path)
        if not file_path.is_file():
            continue
        findings.extend(scan_bytes(file_path.read_bytes(), f"worktree:{path}"))
    return findings


def scan_history() -> list[dict]:
    """扫描全部历史 blob，按内容去重；source 标记为 history:<path>@<commit>。"""
    objects = _git("rev-list", "--objects", "--all").decode("utf-8", "replace").splitlines()

    blob_paths: dict[str, tuple[str, str]] = {}
    for line in objects:
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        sha = parts[0]
        path = parts[1] if len(parts) > 1 else ""
        if not path or path in EXEMPT_PATHS or not _is_text_path(path):
            continue
        blob_paths.setdefault(sha, (path, ""))

    if not blob_paths:
        return []

    # 用 --batch 一次取回所有 blob 内容
    payload = ("\n".join(blob_paths) + "\n").encode("utf-8")
    result = subprocess.run(
        ["git", "cat-file", "--batch"],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace"))

    data = result.stdout
    index = 0
    findings: list[dict] = []
    while index < len(data):
        header_end = data.find(b"\n", index)
        if header_end == -1:
            break
        header = data[index:header_end].decode("utf-8", "replace").split()
        index = header_end + 1
        if len(header) < 3:
            # "<sha> missing" 等
            continue
        sha, obj_type, size_text = header[0], header[1], header[2]
        try:
            size = int(size_text)
        except ValueError:
            continue
        content = data[index : index + size]
        index += size + 1  # 跳过内容后的换行
        if obj_type != "blob" or sha not in blob_paths:
            continue
        path = blob_paths[sha][0]
        findings.extend(scan_bytes(content, f"history:{path}@{sha[:8]}"))
    return findings


def summarize(findings: Iterable[dict]) -> dict:
    counts: dict[str, dict[str, int]] = {}
    for finding in findings:
        bucket = counts.setdefault(finding["pattern"], {"high": 0, "medium": 0, "low": 0})
        bucket[finding["severity"]] += 1
    by_severity = {"high": 0, "medium": 0, "low": 0}
    for finding in findings:
        by_severity[finding["severity"]] += 1
    return {"by_pattern": counts, "by_severity": by_severity}


def render_markdown(report: dict) -> str:
    lines = [
        "# 公开仓库合规审计报告",
        "",
        f"- 生成时间（UTC）：{report['generated_at']}",
        f"- 工作区命中：{report['worktree_count']}",
        f"- 历史命中：{report['history_count']}",
        f"- 严重级别：high={report['summary']['by_severity']['high']} / "
        f"medium={report['summary']['by_severity']['medium']} / "
        f"low={report['summary']['by_severity']['low']}",
        "",
        "## 按模式汇总",
        "",
        "| 模式 | high | medium | low |",
        "|---|---:|---:|---:|",
    ]
    for name, counts in sorted(report["summary"]["by_pattern"].items()):
        lines.append(f"| {name} | {counts['high']} | {counts['medium']} | {counts['low']} |")
    lines += ["", "## 命中明细（已脱敏，最多 200 条）", ""]
    for finding in report["findings"][:200]:
        lines.append(
            f"- [{finding['severity']}] `{finding['pattern']}` "
            f"{finding['source']}:{finding['line']} — {finding['match']}"
        )
    if len(report["findings"]) > 200:
        lines.append(f"- …… 其余 {len(report['findings']) - 200} 条见 JSON")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="公开仓库合规审计（只读）")
    parser.add_argument("--json", dest="json_path", help="JSON 报告输出路径")
    parser.add_argument("--markdown", dest="markdown_path", help="Markdown 报告输出路径")
    parser.add_argument("--skip-history", action="store_true", help="跳过 Git 历史扫描")
    args = parser.parse_args()

    try:
        worktree_findings = scan_worktree()
        history_findings = [] if args.skip_history else scan_history()
    except RuntimeError as exc:
        print(f"[audit] 运行失败：{exc}", file=sys.stderr)
        return 2

    all_findings = worktree_findings + history_findings
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "worktree_count": len(worktree_findings),
        "history_count": len(history_findings),
        "summary": summarize(all_findings),
        "findings": all_findings,
    }

    if args.json_path:
        json_path = Path(args.json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.markdown_path:
        markdown_path = Path(args.markdown_path)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown(report), encoding="utf-8")

    summary = report["summary"]["by_severity"]
    print(
        f"[audit] worktree={len(worktree_findings)} history={len(history_findings)} "
        f"high={summary['high']} medium={summary['medium']} low={summary['low']}"
    )
    for finding in all_findings:
        if finding["severity"] == "high":
            print(
                f"[audit][high] {finding['pattern']} {finding['source']}:{finding['line']} "
                f"{finding['match']}"
            )
    return 1 if summary["high"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
