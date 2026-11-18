"""LLM-as-judge 五维评分 + 规则评分兜底 + 校准一致率（S3-A）。

依据技术方案 §9.1：
- judge 五维：内容正确性 / 难度适切 / 覆盖完整 / 表述规范 / 引用规范，1–5 分；
- judge 失败（无 Key / 解析失败 / 异常）→ **规则评分兜底**（确定性检查，S2 复盘口径）；
- 校准：judge 与人工在同 50 条上的分档（四舍五入）一致比例；
  ≥80% 采信 judge 全量打分，否则回退规则评分并记录（决策㊲④⑤）。

纪律：
- judge 只打分，不改 golden、不改业务 Workflow；
- 规则兜底包含 **degraded 标记检测**（真实冒烟发现的 gc_e03 类「暂不可用」），
  使「完成任务但内容降级」能被识别为低分（决策㊶处置路线①）。
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

DIMENSIONS = ("content_correctness", "difficulty_fit", "coverage_completeness",
              "expression", "citation_compliance")
DIM_LABELS = {
    "content_correctness": "内容正确性",
    "difficulty_fit": "难度适切",
    "coverage_completeness": "覆盖完整",
    "expression": "表述规范",
    "citation_compliance": "引用规范",
}
JUDGE_TOOL = "submit_judge_scores"
DEGRADED_MARKERS = ("暂不可用", "学情诊断暂不可用", "外部Agent与本地LLM均失败")
CALIBRATION_ADOPT_THRESHOLD = 0.80


class JudgeScores(BaseModel):
    """judge 结构化输出（五维 1–5 分 + 理由）。"""
    content_correctness: int = Field(ge=1, le=5, description="内容正确性 1-5")
    difficulty_fit: int = Field(ge=1, le=5, description="难度适切 1-5")
    coverage_completeness: int = Field(ge=1, le=5, description="覆盖完整 1-5")
    expression: int = Field(ge=1, le=5, description="表述规范 1-5")
    citation_compliance: int = Field(ge=1, le=5, description="引用规范 1-5")
    reason: str = Field(min_length=1, description="评分理由（简短）")


# ===================== 制品文本提取 =====================
def extract_artifact_text(paths: list[str], max_chars: int = 3000) -> str:
    """读取制品文本摘要（docx/pdf/json/xlsx 首部内容），供 judge 评分。"""
    chunks: list[str] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        try:
            text = _read_one(path)
        except Exception:
            continue
        if text.strip():
            chunks.append(f"【{path.name}】\n{text.strip()[:max_chars]}")
    return "\n\n".join(chunks)


def _read_one(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".docx":
        from docx import Document
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages)
    if ext == ".json":
        return path.read_text(encoding="utf-8")
    if ext == ".xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(str(path), read_only=True)
        rows = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True, max_row=10):
                rows.append(" | ".join("" if c is None else str(c) for c in row))
        return "\n".join(rows)
    return ""


def is_degraded(text: str) -> bool:
    return any(m in (text or "") for m in DEGRADED_MARKERS)


# ===================== 规则评分兜底（确定性） =====================
def rule_score(case: dict, rec: dict, artifact_text: str) -> dict:
    """确定性规则评分（1–5 分/维），judge 不可用时的回退路径。

    依据：任务状态 / 参数命中 / 产物有效 / degraded 标记 / 引用覆盖。
    """
    exp = case.get("expect", {})
    status = rec.get("task_status", "")
    completed = status == "completed"
    degraded = is_degraded(artifact_text)
    artifacts = rec.get("artifacts") or []
    artifacts_ok = bool(rec.get("artifacts_ok")) and bool(artifacts)
    params_matched = bool(rec.get("params_matched"))
    # 引用覆盖：同一产物目录下 citations.json 存在且含引用
    citation_ok = False
    for a in artifacts:
        ap = a.get("path") or ""
        if ap:
            cit = Path(ap).parent / "citations.json"
            if cit.exists():
                try:
                    data = json.loads(cit.read_text(encoding="utf-8"))
                    refs = data.get("refs") if isinstance(data, dict) else data
                    citation_ok = bool(refs)
                except Exception:
                    citation_ok = False
            break

    content = 3 if completed else 1
    if completed and params_matched:
        content += 1
    if degraded:
        content = min(content, 2)          # 内容降级：内容分压到 ≤2
    difficulty = 4 if params_matched else 3
    coverage = 4 if artifacts_ok else (2 if artifacts else 1)
    if degraded:
        coverage = min(coverage, 2)
    expression = 4 if artifacts_ok and not degraded else (2 if artifacts_ok else 1)
    citation = 4 if citation_ok else 2
    return {
        "scores": {
            "content_correctness": max(1, min(5, content)),
            "difficulty_fit": max(1, min(5, difficulty)),
            "coverage_completeness": max(1, min(5, coverage)),
            "expression": max(1, min(5, expression)),
            "citation_compliance": max(1, min(5, citation)),
        },
        "reason": ("规则评分：" + ("完成" if completed else "未完成")
                   + ("；参数命中" if params_matched else "")
                   + ("；产物有效" if artifacts_ok else "")
                   + ("；引用覆盖" if citation_ok else "")
                   + ("；**内容降级**" if degraded else "")),
        "scoring_source": "rule_fallback",
    }


# ===================== LLM-as-judge =====================
def llm_judge(case: dict, rec: dict, artifact_text: str) -> dict:
    """LLM 五维评分；任何失败 → 规则评分兜底（记录 judge_error）。

    走 tracing 节点采集，返回真实 cost（供应商 usage 缺失时为 None，不造假 0）。
    """
    from backend.app.agent.llm_utils import structured_completion

    col = None
    try:
        from backend.app.tracing import collector as tracing
        col = tracing.begin_node(f"judge:{case.get('case_id', '')}", "judge")
    except Exception:
        col = None
    cost = None
    system = (
        "你是资深小学数学教研评审专家，对 AI 生成的教学资料按五维打 1–5 分。"
        "评分口径必须严格执行，勿把某一维度的问题算到另一维度：\n"
        "1) 内容正确性：只看题目/答案/教学内容本身是否正确，有无知识错误或编造；"
        "主题是否贴合任务不在此维度扣分。1=存在明显错误或编造；2=多处错误；"
        "3=基本正确但有可指出的瑕疵；4=正确；5=完全正确严谨。"
        "若存在可指出的答案/表述瑕疵，最高给 4 分。\n"
        "2) 难度适切：只看题目本身难度是否适合小学年级；与主题是否匹配无关。"
        "1=严重不符；2=偏难或偏易较多；3=基本符合；4=符合；5=完全贴合（难度分层合理）。\n"
        "3) 覆盖完整：任务要求（主题/知识点/题量/结构/交付物）是否覆盖。"
        "1=主题跑题（产物主题与任务要求不符）或遗漏关键交付物；2=主题基本相关但遗漏较多；"
        "3=覆盖主要部分；4=基本覆盖；5=完全覆盖。\n"
        "4) 表述规范：语言与格式是否规范清晰。1=混乱；2=多处不规范；3=基本通顺；"
        "4=规范清晰；5=专业规范（无格式瑕疵）。\n"
        "5) 引用规范：题目/知识点引用是否可追溯、无编造。1=编造或无任何来源；"
        "2=基本无可见引用；3=部分可见但不可追溯；4=可见具体引用且基本可追溯；"
        "5=全部可追溯且规范。仅当产物中能看见具体题目/知识点引用时，才可给 4 分及以上。\n"
        "若产物含「暂不可用」等降级标记，内容正确性与覆盖完整不得高于 2 分。"
        "务必调用 submit_judge_scores 工具提交评分。"
    )
    user = (
        f"任务：{case.get('prompt', '')}\n"
        f"工作流：{rec.get('workflow', '')}\n"
        f"任务状态：{rec.get('task_status', '')}\n"
        f"产物内容摘要：\n{artifact_text or '（无产物文本）'}"
    )
    try:
        scores = structured_completion(system, user, JUDGE_TOOL, JudgeScores)
        out = {"scores": scores.model_dump(exclude={"reason"}),
               "reason": scores.reason,
               "scoring_source": "llm_judge"}
    except Exception as exc:
        out = rule_score(case, rec, artifact_text)
        out["judge_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if col is not None:
            try:
                from backend.app.tracing import collector as tracing
                ended = tracing.end_node()
                agg = tracing.aggregate(ended if ended is not None else col)
                cost = agg.get("cost")
            except Exception:
                cost = None
    out["cost"] = cost
    return out


def judge_case(case: dict, rec: dict, use_llm: bool = True) -> dict:
    """单 case 评分入口：use_llm=True 走 LLM；False 直接规则评分。"""
    exp = case.get("expect", {})
    # 无产物（clarify/reject/fail 等）→ judge 不适用，规则评分仍可给出行为性分
    paths = [a.get("path") for a in (rec.get("artifacts") or []) if a.get("path")]
    text = extract_artifact_text(paths) if paths else ""
    if use_llm and paths:
        return llm_judge(case, rec, text)
    out = rule_score(case, rec, text)
    if not paths:
        out["reason"] = (out["reason"] + "；无产物（judge 不适用，规则评分）")
    return out


# ===================== 批量 judge =====================
def judge_report(report_path: Path, calibration_path: Path | None = None,
                 use_llm: bool = False, llm_provider=None,
                 workdir: Path | None = None) -> dict:
    """对一次 run 的报告逐 case 评分（默认规则评分；use_llm=True 走 LLM）。

    返回 {"rows": {case_id: {scores..., scoring_source, reason}}, "summary": ...}
    """
    from backend.app.eval.golden import load_frozen
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    cases, _ = load_frozen()
    case_of = {c["case_id"]: c for c in cases}

    wanted = None
    if calibration_path is not None:
        calib = json.loads(Path(calibration_path).read_text(encoding="utf-8"))
        wanted = set(calib["case_ids"])

    if use_llm and llm_provider is not None:
        import tempfile
        from backend.app.eval.provider import install_online_env
        install_online_env(llm_provider,
                           workdir or Path(tempfile.mkdtemp(prefix="judge_run_")))

    rows: dict[str, dict] = {}
    source_counts = {"llm_judge": 0, "rule_fallback": 0}
    cost_total = 0.0
    cost_known = 0
    for rec in report.get("records", []):
        cid = rec.get("case_id")
        if wanted is not None and cid not in wanted:
            continue
        case = case_of.get(cid)
        if case is None:
            continue
        result = judge_case(case, rec, use_llm=use_llm)
        rows[cid] = result
        source_counts[result["scoring_source"]] = \
            source_counts.get(result["scoring_source"], 0) + 1
        if result.get("cost") is not None:
            cost_total += result["cost"]
            cost_known += 1
    return {"rows": rows, "summary": {"judged_cases": len(rows),
                                      "sources": source_counts,
                                      "cost_known_cases": cost_known,
                                      "cost_total_cny": round(cost_total, 6)}}


# ===================== 校准一致率 =====================
def band(score: float) -> int:
    """分档：四舍五入到整数档（1–5）。"""
    return max(1, min(5, int(round(float(score)))))


def calibration_agreement(judge_scores: dict[str, dict], human_scores: dict[str, dict],
                          threshold: float = CALIBRATION_ADOPT_THRESHOLD) -> dict:
    """judge vs 人工 的分档一致率。

    入参：{case_id: {dim: score}}；仅统计双方都有的 case×dim。
    返回：per_dimension / overall / adopted（>= threshold 采信 judge）。
    """
    per_dim: dict[str, list[int]] = {d: [0, 0] for d in DIMENSIONS}  # [hit, total]
    for cid, js in judge_scores.items():
        hs = human_scores.get(cid)
        if not hs:
            continue
        for d in DIMENSIONS:
            if d not in js or d not in hs:
                continue
            per_dim[d][1] += 1
            if band(js[d]) == band(hs[d]):
                per_dim[d][0] += 1
    total_hit = sum(v[0] for v in per_dim.values())
    total_all = sum(v[1] for v in per_dim.values())
    overall = round(total_hit / total_all, 4) if total_all else None
    per_dimension = {
        d: {"hit": v[0], "total": v[1], "rate": round(v[0] / v[1], 4) if v[1] else None}
        for d, v in per_dim.items()
    }
    return {
        "per_dimension": per_dimension,
        "overall_rate": overall,
        "threshold": threshold,
        "adopted": bool(overall is not None and overall >= threshold),
        "compared_pairs": total_all,
    }