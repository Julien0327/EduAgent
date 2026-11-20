"""演示题库生成（Phase 6 / S2，方案 C）。

- 模板侧：KP 042–082 由 ``templates`` 确定性生成；
- LLM 侧：应用题类 KP 001–041 由 DeepSeek 生成，一 KP 一次调用产出
  难度 1–5 共 5 题；校验不通过时只补生成失败难度，最多 2 轮；
- 预算：调用前后经 tracing collector 聚合真实成本，超过 ``budget_cap`` 即停止；
- 可复现：模板由 seed 决定；LLM 结果冻结在 staging JSONL，组装可重复。

本模块不写业务库；落库由 ``backend/scripts/load_demo_bank.py`` 负责。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from backend.app.agent.llm_utils import structured_completion
from backend.app.demo.models import DemoQuestion, GeneratedQuestionSet
from backend.app.demo.templates import (
    TEMPLATE_GENERATORS,
    generate_template_questions,
)
from backend.app.demo.validation import normalize_choice_options, rule_validate
from backend.app.tracing import collector as tracing
from backend.config import settings

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = REPO_ROOT / "data" / "db" / "edumate.sqlite"
DEFAULT_RAW = REPO_ROOT / "data" / "staging" / "demo_bank" / "llm_raw_v1.jsonl"
DEFAULT_CANDIDATE = REPO_ROOT / "data" / "staging" / "demo_bank" / "demo_bank_candidate_v1.jsonl"

PROMPT_VERSION = "demo_bank_prompt_v1"

SYSTEM_PROMPT = """你是资深小学数学命题教师，为小学六年级学生命制题目。
硬性要求：
- 题干完整，包含所有已知条件；不得省略，不得出现“略”“见解析”。
- 答案唯一且能由条件推出；不得有歧义。
- 数值答案必须给出 answer_value 和 unit；equation 只写最终算式（只能包含数字、
  + - * / ** 和括号），其计算结果必须等于 answer_value。
- 选择题 options 给 4 个选项，answer 写选项字母（A/B/C/D）。
- 不使用负数、二元一次方程、根号、不等式等超纲内容。
- solution 用 1–2 句话写清思路，不超过 100 字。
- 五道题难度依次为 1、2、3、4、5，各一道；题目之间不得重复。"""


def load_kps(db_path: str | Path = DEFAULT_DB) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT kp_id, name, description FROM knowledge_points "
            "WHERE kp_id LIKE 'kp_p_%' ORDER BY kp_id"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def split_kps(kps: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    template, llm = [], []
    for kp in kps:
        (template if kp["name"] in TEMPLATE_GENERATORS else llm).append(kp)
    return template, llm


def _user_prompt(kp: dict) -> str:
    return (
        f"知识点：{kp['name']}\n"
        f"说明：{kp.get('description') or '（无）'}\n"
        "请生成 5 道题，难度 1–5 各一道。"
    )


def _data_to_demo(kp: dict, data, method: str, seed: int) -> DemoQuestion:
    q = DemoQuestion(
        q_id=f"q_demo_{kp['kp_id'].split('_')[-1]}_{data.difficulty}",
        kp_id=kp["kp_id"],
        kp_name=kp["name"],
        difficulty=data.difficulty,
        qtype=data.qtype,
        stem=data.stem.strip(),
        answer=data.answer.strip(),
        solution=data.solution.strip(),
        generation_method=method,
        options=list(data.options),
        answer_value=data.answer_value,
        unit=data.unit,
        equation=data.equation,
        model=settings.DEEPSEEK_MODEL if method.startswith("llm") else None,
        prompt_version=PROMPT_VERSION if method.startswith("llm") else None,
        seed=seed,
    )
    normalize_choice_options(q)
    return q


def _generate_one_kp(
    kp: dict,
    seed: int,
    *,
    max_rounds: int = 3,
) -> tuple[list[DemoQuestion], list[dict]]:
    """生成一个 KP 的 5 题；返回（已通过规则校验的题, 原始记录）。"""
    accepted: dict[int, DemoQuestion] = {}
    raw_records: list[dict] = []
    feedback = ""
    for round_no in range(1, max_rounds + 1):
        missing = [d for d in range(1, 6) if d not in accepted]
        if not missing:
            break
        user = _user_prompt(kp)
        if feedback:
            user += f"\n上次未通过校验的难度：{', '.join(map(str, missing))}。{feedback}"
        try:
            result = structured_completion(
                SYSTEM_PROMPT, user, "submit_questions", GeneratedQuestionSet,
                max_attempts=2,
            )
        except Exception as exc:  # noqa: BLE001 - 记录后重试
            raw_records.append({
                "kp_id": kp["kp_id"], "round": round_no, "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            })
            feedback = f"上次调用失败：{type(exc).__name__}: {exc}"
            continue

        issues_by_diff: list[str] = []
        for item in result.questions:
            difficulty = item.difficulty
            if difficulty in accepted:
                continue
            q = _data_to_demo(kp, item, "llm", seed)
            issues = rule_validate(q)
            if issues:
                q.validation_status = "needs_review"
                q.validation_issues = issues
                issues_by_diff.append(f"难度 {difficulty}：{'；'.join(issues)}")
                raw_records.append({
                    "kp_id": kp["kp_id"], "round": round_no, "ok": False,
                    "difficulty": difficulty, "issues": issues,
                    "question": q.to_dict(),
                })
            else:
                q.validation_status = "passed"
                accepted[difficulty] = q
                raw_records.append({
                    "kp_id": kp["kp_id"], "round": round_no, "ok": True,
                    "difficulty": difficulty, "question": q.to_dict(),
                })
        feedback = "；".join(issues_by_diff)

    return [accepted[d] for d in sorted(accepted)], raw_records


_MATH_TRANSLATION = str.maketrans({
    "−": "-",  # U+2212 minus，Excel 以 GBK 保存时会变成 ?
    "–": "-",  # en dash
    "—": "-",  # em dash
    "－": "-",  # fullwidth hyphen-minus
    "﹣": "-",  # small hyphen-minus
    "²": "^2",  # 上标 2，GBK 不支持
    "³": "^3",  # 上标 3，GBK 不支持
})


def normalize_question_text(q: DemoQuestion) -> bool:
    """把 Excel/GBK 不友好的数学符号归一为 ASCII，避免复核表乱码。"""
    changed = False
    for field_name in ("stem", "answer", "solution", "equation"):
        value = getattr(q, field_name)
        if isinstance(value, str):
            new = value.translate(_MATH_TRANSLATION)
            if new != value:
                setattr(q, field_name, new)
                changed = True
    new_options = [str(opt).translate(_MATH_TRANSLATION) for opt in q.options]
    if new_options != q.options:
        q.options = new_options
        changed = True
    return changed


def normalize_text(questions: list[DemoQuestion]) -> int:
    return sum(1 for q in questions if normalize_question_text(q))


def apply_manual_fixes(
    questions: list[DemoQuestion], fixes_path: Path
) -> tuple[list[DemoQuestion], dict]:
    """应用人工复核后的定点修正；修正后重新跑规则校验。"""
    if not fixes_path.exists():
        return questions, {"fixes": 0, "applied": 0}
    payload = json.loads(fixes_path.read_text(encoding="utf-8"))
    by_id = {q.q_id: q for q in questions}
    applied = 0
    for fix in payload.get("fixes", []):
        q = by_id.get(fix["q_id"])
        if q is None:
            continue
        for key, value in fix.get("fields", {}).items():
            setattr(q, key, value)
        q.review_notes = list(q.review_notes) + [
            f"人工修正：{fix.get('reason', '')}（原问题：{'；'.join(q.validation_issues)}）"
        ]
        q.validation_issues = rule_validate(q)
        q.validation_status = "passed" if not q.validation_issues else "needs_review"
        applied += 1
    return questions, {"fixes": len(payload.get("fixes", [])), "applied": applied}


def salvage_from_raw(
    raw_path: Path, questions: list[DemoQuestion]
) -> tuple[list[DemoQuestion], dict]:
    """从失败原始记录中救回「仅选项格式不合格」的题（不产生新调用）。"""
    existing = {(q.kp_id, q.difficulty) for q in questions}
    added: list[DemoQuestion] = []
    if not raw_path.exists():
        return added, {"raw": 0, "salvaged": 0}
    records = 0
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records += 1
        record = json.loads(line)
        if record.get("ok"):
            continue
        payload = record.get("question")
        if not payload:
            continue
        q = DemoQuestion.from_dict(payload)
        normalize_choice_options(q)
        issues = rule_validate(q)
        if issues or (q.kp_id, q.difficulty) in existing:
            continue
        q.validation_status = "passed"
        q.validation_issues = []
        added.append(q)
        existing.add((q.kp_id, q.difficulty))
    return added, {"raw": records, "salvaged": len(added)}


def generate_templates(kps: list[dict], seed: int) -> list[DemoQuestion]:
    out: list[DemoQuestion] = []
    for kp in kps:
        out.extend(generate_template_questions(kp["name"], kp["kp_id"], seed))
    return out


def generate_llm_bank(
    kps: list[dict],
    seed: int,
    *,
    budget_cap: float = 5.0,
    raw_path: Path = DEFAULT_RAW,
) -> tuple[list[DemoQuestion], dict]:
    """生成 LLM 侧题目；返回（题目, 运行统计）。"""
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    accepted: list[DemoQuestion] = []
    records: list[dict] = []
    failures: dict[str, str] = {}
    col = tracing.begin_node("demo_bank_generate", "demo_bank")
    cost_used = 0.0
    stopped_reason = ""
    raw_fh = raw_path.open("w", encoding="utf-8")
    try:
        for idx, kp in enumerate(kps, start=1):
            if cost_used >= budget_cap:
                stopped_reason = f"预算上限 ¥{budget_cap:.2f} 已触发"
                break
            questions, kp_records = _generate_one_kp(kp, seed)
            accepted.extend(questions)
            records.extend(kp_records)
            for record in kp_records:
                raw_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            raw_fh.flush()
            missing = {1, 2, 3, 4, 5} - {q.difficulty for q in questions}
            if missing:
                failures[kp["kp_id"]] = f"缺少难度 {sorted(missing)}"
            agg = tracing.aggregate(col)
            if agg.get("cost") is None:
                # usage 缺失时按调用次数做保守估算，仍受 cap 约束
                calls = len([c for c in agg.get("llm_calls", []) if c.get("kind") == "llm"])
                cost_used = calls * 0.02
            else:
                cost_used = float(agg.get("cost") or 0.0)
            print(f"[demo] {idx}/{len(kps)} {kp['kp_id']} {kp['name']} "
                  f"accepted={len(questions)}/5 cost≈¥{cost_used:.4f}")
    finally:
        raw_fh.close()
        tracing.end_node()
    agg = tracing.aggregate(col)
    stats = {
        "kps": len(kps),
        "accepted": len(accepted),
        "failures": failures,
        "cost": agg.get("cost"),
        "usage_source": agg.get("usage_source"),
        "llm_calls": len([c for c in agg.get("llm_calls", []) if c.get("kind") == "llm"]),
        "stopped_reason": stopped_reason,
        "raw_path": str(raw_path),
    }
    return accepted, stats


def build_bank(
    *,
    db_path: str | Path = DEFAULT_DB,
    seed: int = 20261120,
    allow_live: bool = False,
    budget_cap: float = 5.0,
    candidate_path: Path = DEFAULT_CANDIDATE,
) -> tuple[list[DemoQuestion], dict]:
    kps = load_kps(db_path)
    template_kps, llm_kps = split_kps(kps)
    template_questions = generate_templates(template_kps, seed)
    llm_questions: list[DemoQuestion] = []
    llm_stats: dict = {}
    if llm_kps:
        if not allow_live:
            raise RuntimeError("LLM 生成需要显式 --allow-live-api")
        llm_questions, llm_stats = generate_llm_bank(
            llm_kps, seed, budget_cap=budget_cap
        )
    questions = sorted(
        template_questions + llm_questions, key=lambda q: (q.kp_id, q.difficulty)
    )
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    with candidate_path.open("w", encoding="utf-8") as fh:
        for q in questions:
            fh.write(json.dumps(q.to_dict(), ensure_ascii=False) + "\n")
    stats = {
        "db_path": str(db_path),
        "seed": seed,
        "template_kps": len(template_kps),
        "llm_kps": len(llm_kps),
        "template_questions": len(template_questions),
        "llm_questions": len(llm_questions),
        "total": len(questions),
        "candidate_path": str(candidate_path),
        "llm": llm_stats,
    }
    return questions, stats
