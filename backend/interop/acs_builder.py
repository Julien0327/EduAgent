"""ACS（Agent Capability Specification）能力描述构建与校验。

按已核实 schema（ACPs-community/acps-sdk/acps_sdk/acs/models.py 的
AgentCapabilitySpec）构建 EduMate-AIP 的 acs.json 供注册提交。字段名采用线格式
（camelCase），与官方 beijing_food/acs.json 一致。

本模块只负责「生成正确的数据契约」；真实注册必须提交到梧桐平台并走审核。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from interop.config import AcpSettings

PROTOCOL_VERSION = "02.02"

# EduMate-AIP 三条教学能力（对齐现有工作流 exam_loop / question_gen / lesson_plan）
SKILLS: list[dict[str, Any]] = [
    {
        "id": "edumate.exam-loop",
        "name": "章节小测一条龙",
        "description": "根据章节自动完成：检索知识点 → 出卷 → 生成试卷(docx) → 答题卡(xlsx) → 加载预置答题 → 成绩统计 → 学情报告(pdf)。",
        "version": "1.0.0",
        "tags": ["组卷", "答题卡", "学情报告", "教学闭环"],
        "examples": [
            "请对《一元二次方程》做章节小测并生成学情报告",
        ],
        "inputModes": ["text/plain"],
        "outputModes": ["text/plain", "application/json"],
    },
    {
        "id": "edumate.question-gen",
        "name": "智能组卷",
        "description": "LLM 实时命制题目，产出试卷(docx)与答题卡(xlsx)。",
        "version": "1.0.0",
        "tags": ["智能组卷", "出题"],
        "examples": ["帮我智能组一套《一元二次方程》的卷子"],
        "inputModes": ["text/plain"],
        "outputModes": ["text/plain", "application/json"],
    },
    {
        "id": "edumate.lesson-plan",
        "name": "AI 教案生成",
        "description": "LLM 生成结构化教案（教学目标/重难点/教学过程/课后作业），产出 docx。",
        "version": "1.0.0",
        "tags": ["教案", "教学设计"],
        "examples": ["帮我写一份《一元二次方程》的教案"],
        "inputModes": ["text/plain"],
        "outputModes": ["text/plain", "application/json"],
    },
]


def build_acs(
    settings: AcpSettings,
    *,
    aic: str | None = None,
    now: datetime | None = None,
    provider: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构建 ACS 字典（线格式 camelCase）。

    aic / lastModifiedTime 由注册服务分配；此处可用占位，注册后由平台回填。
    """
    ts = (now or datetime.now(timezone.utc)).isoformat()
    provider = provider or {
        "countryCode": "CN",
        "organization": "EduMate 项目组",  # 材料不得出现学校名称/LOGO/指导教师
        "department": "",
        "url": "",
        "license": "",
    }
    return {
        "aic": aic or "",
        "active": True,
        "lastModifiedTime": ts,
        "protocolVersion": PROTOCOL_VERSION,
        "name": settings.name,
        "description": (
            "面向教师的「教学任务自动化智能体」（AIP Partner）。"
            "用自然语言驱动多步自动执行，产出可直接使用的 Word/Excel/PDF 教学文档，"
            "覆盖组卷、答题卡、学情报告、教案四条能力。"
        ),
        "version": "1.0.0",
        "provider": provider,
        "securitySchemes": {
            "mtls": {"type": "mutualTLS", "description": "智能体间 mTLS 双向认证"},
        },
        "endPoints": [
            {
                "url": settings.rpc_url,
                "transport": "JSONRPC",
                "security": [{"mtls": []}],
            }
        ],
        "capabilities": {
            "streaming": False,
            "notification": False,
            "messageQueue": [],
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": SKILLS,
    }


# ACS 必需字段（已核实 schema 的必填项）
_REQUIRED_FIELDS = [
    "aic",
    "active",
    "lastModifiedTime",
    "protocolVersion",
    "name",
    "description",
    "version",
    "provider",
    "securitySchemes",
    "endPoints",
    "capabilities",
    "defaultInputModes",
    "defaultOutputModes",
    "skills",
]


def validate_acs(acs: dict[str, Any]) -> list[str]:
    """离线校验 ACS 结构与关键字段，返回问题列表（空列表表示通过）。"""
    problems: list[str] = []
    if not isinstance(acs, dict):
        return ["ACS 必须是 JSON 对象"]
    for field in _REQUIRED_FIELDS:
        if field not in acs:
            problems.append(f"缺少必填字段：{field}")
    if acs.get("protocolVersion") != PROTOCOL_VERSION:
        problems.append(f"protocolVersion 应为 {PROTOCOL_VERSION}，实际 {acs.get('protocolVersion')!r}")
    end_points = acs.get("endPoints")
    if isinstance(end_points, list):
        for i, ep in enumerate(end_points):
            if not isinstance(ep, dict) or "url" not in ep or "transport" not in ep:
                problems.append(f"endPoints[{i}] 缺少 url/transport")
    skills = acs.get("skills")
    if isinstance(skills, list):
        for i, sk in enumerate(skills):
            for key in ("id", "name", "description", "version"):
                if not sk.get(key):
                    problems.append(f"skills[{i}] 缺少 {key}")
    return problems


def try_validate_with_sdk(acs: dict[str, Any]) -> tuple[bool, str]:
    """若已安装官方 acps_sdk，用其 AgentCapabilitySpec 做二次校验（诊断用，非必需）。"""
    try:
        from acps_sdk.acs.models import AgentCapabilitySpec  # type: ignore
    except Exception as exc:  # noqa: BLE001 —— SDK 未安装
        return False, f"官方 acps_sdk 未安装，跳过 SDK 校验：{exc}"
    try:
        AgentCapabilitySpec.model_validate(acs)
        return True, "通过官方 AgentCapabilitySpec 校验"
    except Exception as exc:  # noqa: BLE001
        return False, f"官方 AgentCapabilitySpec 校验失败：{exc}"


def write_acs(path: str | Path, acs: dict[str, Any]) -> Path:
    """把 ACS 写出为 UTF-8 JSON（供注册提交）。"""
    import json

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(acs, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
