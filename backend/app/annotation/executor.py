"""标注执行器（Step 5-B1）。

设计依据：docs/development/STEP5-ANNOTATION-DESIGN.md 第二版 §4/§5/§8/§9。

落实的既定决策：
- 决策㉑幂等键 (q_id, prompt_version)：批次 UNIQUE(scope,task_type,strategy,model,
  prompt_id,prompt_version)，同键重跑复用批次；断点以 q_annotation_results 终态驱动
  （status ok/unmapped 跳过，failed/invalid 重跑覆盖）。
- 决策②/㉖预算三线：budget_group 级台账（初始值=该组历史批次费用累计——重试、
  续跑、新建批次均无法绕过）；提醒线→通知记录不停止；暂停线→停止提交新请求
  （在途排水后批次 aborted，人工确认后续跑）；硬停线 cap→绝对上限禁止任何新
  请求；提交前按「无缓存最坏单次成本」预留——缓存命中不作为预算保护前提。
  cap 必须 >0 显式给定，拒绝无预算运行。
- 决策③双臂：A/B 各自独立批次（strategy 列），共用执行器；两臂费用各自计入
  同一 budget_group（组级累计）。
- 决策④模型：批次记录请求模型名 + 每次调用取响应 model 快照回填。
- 并发 AIMD（第二版修订）：初始 4、上限 8；连续 PROMOTE_STREAK 次成功缓升 +1；
  收到 429（throttled）→ 并发降半（下限 1）+ 指数退避；稳定后缓升。429 永远是
  降并发的信号。
- 决策①manifest：运行级 JSONL 入 Git（batch_start / 每 CHECKPOINT_EVERY 题
  checkpoint / budget 事件 / batch_end 含失败汇总与 Token 费用汇总）；逐题明细
  JSONL 写 data/staging/（不入 Git）。
- 决策㉔红线：本模块不从 q_kp 读取任何数据；规则标签不进入任何 prompt 输入。

真实 API 隔离：执行器只依赖注入的 ChatClient；Key 仅在 CLI 装配层
（client.OpenAICompatClient.from_settings）读取，且只判存在、不打印。
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.app.annotation import pricing, prompts
from backend.app.annotation.client import (
    BACKOFF_STEPS,
    RETRY_MAX,
    ChatClient,
    ChatResult,
    FatalError,
    RetryableError,
    Usage,
)
from backend.app.annotation.recall import CandidateRecaller
from backend.app.storage import db
from backend.logging_config import get_logger

log = get_logger("annotation.executor")

ALLOWED_KP_MAX = 3                 # kp_ids 上限（设计文档 §4 输出规范）
PROMOTE_STREAK = 20                # 连续成功 N 次 → 并发 +1（AIMD 加性增）
CHECKPOINT_EVERY = 1000            # manifest 周期 checkpoint（题数）
DEFAULT_WARN_RATIO = 0.5           # 默认提醒线 = cap×0.5（试跑显式传 ¥10/¥20，决策㉖）
DEFAULT_PAUSE_RATIO = 0.75         # 默认暂停线 = cap×0.75（试跑显式传 ¥15/¥20，决策㉖）
REASON_MAX_CHARS = 60              # reason 持久化截断（prompt 要求 ≤30，执行器放宽容错）
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST_DIR = REPO_ROOT / "data" / "annotations" / "manifests"
DEFAULT_DETAIL_DIR = REPO_ROOT / "data" / "staging"

_ARM_CONFIG = {
    "A": {"strategy": "A_full_list", "prompt_id": "kp_primary", "use_recall": False},
    "B": {"strategy": "B_recall", "prompt_id": "kp_recall", "use_recall": True},
}
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")
_DONE_STATUSES = ("ok", "unmapped")  # 终态：断点续跑跳过；failed/invalid 为中间态可覆盖


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- 输出校验

def validate_output(content: str, allowed_kp_ids: set[str]) -> tuple[dict | None, str | None]:
    """解析并校验模型 JSON 输出。返回 (parsed, error)；error 非空表示无效。

    parsed 结构：{"primary": str|None, "kp_ids": [...], "difficulty": int,
                  "confidence": {"kp": float|None, "difficulty": float|None},
                  "reason": str|None}
    不可映射（kp_ids=[]）是有效结果：primary=None，进入 results.status='unmapped'。
    """
    text = _JSON_FENCE_RE.sub("", (content or "").strip()).strip()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析失败：{exc}"
    if not isinstance(raw, dict):
        return None, "输出必须是 JSON 对象"

    kp_ids = raw.get("kp_ids")
    if not isinstance(kp_ids, list) or any(not isinstance(k, str) for k in kp_ids):
        return None, "kp_ids 必须是字符串数组"
    kp_ids = list(dict.fromkeys(kp_ids))  # 去重保序

    difficulty = raw.get("difficulty")
    if isinstance(difficulty, bool) or not isinstance(difficulty, int) \
            or not 1 <= difficulty <= 5:
        return None, "difficulty 必须是 1-5 整数"

    unknown = [k for k in kp_ids if k not in allowed_kp_ids]
    if unknown:
        return None, f"kp_ids 含清单外 id：{unknown[:3]}"

    if kp_ids:
        if not ALLOWED_KP_MAX >= len(kp_ids) >= 1:
            return None, f"kp_ids 数量须 1-{ALLOWED_KP_MAX}"
        primary = raw.get("primary")
        if not isinstance(primary, str) or primary not in kp_ids:
            return None, "primary 必须是 kp_ids 之一"
    else:
        primary = None  # 不可映射：有效结果

    conf_raw = raw.get("confidence") or {}
    if not isinstance(conf_raw, dict):
        return None, "confidence 必须是对象"
    confidence: dict[str, float | None] = {}
    for key in ("kp", "difficulty"):
        value = conf_raw.get(key)
        if value is None:
            confidence[key] = None
        elif isinstance(value, (int, float)) and not isinstance(value, bool) \
                and 0 <= value <= 1:
            confidence[key] = float(value)
        else:
            return None, f"confidence.{key} 必须是 0-1 数值"

    reason = raw.get("reason")
    reason = str(reason)[:REASON_MAX_CHARS] if reason is not None else None
    return {
        "primary": primary,
        "kp_ids": kp_ids,
        "difficulty": difficulty,
        "confidence": confidence,
        "reason": reason,
    }, None


# ---------------------------------------------------------------- 运行期组件

class BudgetLedger:
    """budget_group 级预算台账（线程安全，决策㉖三线规则）。

    - 提醒线 warn_at：实际支出达到 → 通知并记录（budget_remind 事件），**不停止**；
    - 暂停线 pause_at：达到 → 停止提交新请求（在途调用排水完成后批次 aborted），
      等待人工确认后续跑（budget_pause 事件含进度/费用/剩余题量/预计剩余费用）；
    - 硬停线 cap：绝对上限——cost+reserved ≥ cap 即拒绝任何新调用预留，
      **不得通过重试、续跑或新建批次绕过**（组级累计：ledger 初始值 = 该
      budget_group 全部历史批次的 cost_cny_est 之和，见 run_annotation）。
    费用预留一律按无缓存最坏口径（worst_per_call_cny），缓存命中不作保护前提。
    """

    def __init__(self, cap_cny: float, warn_at_cny: float | None = None,
                 pause_at_cny: float | None = None,
                 initial_cost_cny: float = 0.0,
                 warn_ratio: float = DEFAULT_WARN_RATIO,
                 pause_ratio: float = DEFAULT_PAUSE_RATIO):
        if cap_cny <= 0:
            raise ValueError("预算 cap 必须 > 0（决策②：拒绝无预算运行）")
        warn = warn_at_cny if warn_at_cny is not None else cap_cny * warn_ratio
        pause = pause_at_cny if pause_at_cny is not None else cap_cny * pause_ratio
        if not 0 < warn <= pause <= cap_cny:
            raise ValueError(
                f"预算线非法：提醒线={warn} 暂停线={pause} cap={cap_cny}"
                "（须 0 < 提醒线 ≤ 暂停线 ≤ cap）")
        self.cap = float(cap_cny)
        self.warn_at = float(warn)
        self.pause_at = float(pause)
        # 组级既有累计作为初始值：新建批次/续跑都从组累计起步（决策㉖防绕过）
        self.cost = max(0.0, float(initial_cost_cny))
        self.reserved = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_hit_tokens = 0
        self.calls = 0
        self.reminded = self.cost >= self.warn_at
        self.pause_requested = self.cost >= self.pause_at
        self.hard_stopped = self.cost >= self.cap
        self._lock = threading.Lock()

    def try_reserve(self, worst_cny: float) -> bool:
        with self._lock:
            if self.cost + self.reserved + worst_cny >= self.cap:
                self.hard_stopped = True
                return False
            self.reserved += worst_cny
            return True

    def commit(self, actual_cny: float, usage: Usage) -> None:
        """计入一次调用的真实费用与 token（预留额由调用方在 finally 中释放）。"""
        with self._lock:
            self.cost += actual_cny
            self.calls += 1
            self.input_tokens += usage.prompt_tokens
            self.output_tokens += usage.completion_tokens
            self.cache_hit_tokens += usage.cache_hit_tokens
            if self.cost >= self.cap:
                self.hard_stopped = True
            if self.cost >= self.pause_at:
                self.pause_requested = True
            if self.cost >= self.warn_at:
                self.reminded = True


class AdaptiveLimiter:
    """AIMD 并发控制（线程安全）：成功稳定缓升；429 降半（下限 1）。

    加性增：连续 promote_every 次成功 → limit+1（不超过 maximum）。
    乘性减：收到 throttled（429）→ limit 减半（不低于 minimum）。
    """

    def __init__(self, initial: int, maximum: int, minimum: int = 1,
                 promote_every: int | None = None):
        self._lock = threading.Lock()
        self._limit = initial
        self._max = maximum
        self._min = minimum
        self._streak = 0
        # 运行时读取模块常量（便于测试 monkeypatch）；默认连续 20 次成功升 1 级
        self.promote_every = PROMOTE_STREAK if promote_every is None else promote_every
        self.throttle_events = 0

    @property
    def limit(self) -> int:
        with self._lock:
            return self._limit

    def on_success(self) -> None:
        with self._lock:
            self._streak += 1
            if self._streak >= self.promote_every and self._limit < self._max:
                self._limit += 1
                self._streak = 0

    def on_throttled(self) -> None:
        with self._lock:
            self._limit = max(self._min, self._limit // 2)
            self._streak = 0
            self.throttle_events += 1


class _JsonlWriter:
    """线程安全 JSONL 追加写（manifest 入 Git / 逐题明细入 staging，决策①）。"""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()

    def write(self, event: dict) -> None:
        line = json.dumps({"ts": _utcnow(), **event}, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


# ---------------------------------------------------------------- 执行器配置

@dataclass
class ExecutorConfig:
    db_path: Path
    qids: list[str]
    arm: str                          # 'A' / 'B'
    model: str = "deepseek-flash"     # 决策④
    prompt_version: str = "v1.0"
    scope: str = "ape_primary"
    task_type: str = "kp_difficulty"
    temperature: float = 0.2
    budget_group: str = "trial"
    budget_cap_cny: float = 0.0       # 必须 >0（决策②/㉖：硬停线）
    budget_warn_at_cny: float | None = None   # 提醒线（默认 cap×0.5；试跑传 10）
    budget_pause_at_cny: float | None = None  # 暂停线（默认 cap×0.75；试跑传 15）
    worst_per_call_cny: float = 0.005  # 无缓存最坏单次预留基准（决策②口径）
    max_items: int | None = None      # 分步执行用；终止时批次置 aborted（可续跑）
    concurrency_initial: int = 4
    concurrency_max: int = 8
    backoff_base: float = 1.0         # 测试注入 0 加速
    force_period: str | None = None   # 测试固定 peak/idle；None = 按 wall clock 判定
    manifest_dir: Path = DEFAULT_MANIFEST_DIR
    detail_dir: Path = DEFAULT_DETAIL_DIR

    def __post_init__(self):
        if self.arm not in _ARM_CONFIG:
            raise ValueError(f"arm 必须是 A/B，收到 {self.arm!r}")
        if self.budget_cap_cny <= 0:
            raise ValueError("budget_cap_cny 必须 > 0（决策②：拒绝无预算运行）")
        if self.force_period is not None and self.force_period not in pricing.VALID_PERIODS:
            raise ValueError(f"force_period 必须是 {pricing.VALID_PERIODS}")


# ---------------------------------------------------------------- 主流程

def run_annotation(cfg: ExecutorConfig, client: ChatClient,
                   recaller: CandidateRecaller | None = None) -> dict:
    """执行一次打标批次（可断点续跑）。返回汇总 dict。

    arm='A'：全清单直判（kp_rows 全量注入）。
    arm='B'：逐题召回候选（recaller 必填，否则 fail-fast）。
    """
    arm_cfg = _ARM_CONFIG[cfg.arm]
    if arm_cfg["use_recall"] and recaller is None:
        raise ValueError("B 臂必须提供 recaller（候选召回器）")

    # Prompt 指纹校验（防漂移）+ KP 清单加载（来源仅 knowledge_points，不触 q_kp）
    registry = prompts.verify_registry()
    prompt_path = prompts.resolve_prompt_path(arm_cfg["prompt_id"], cfg.prompt_version)
    prompt_sha = registry[arm_cfg["prompt_id"]]["sha256"]

    kp_rows = _load_kp_rows(cfg.db_path)
    kp_map = {r["kp_id"]: r for r in kp_rows}
    all_kp_ids = set(kp_map)
    system_prompt_a, kp_snapshot_sha = prompts.build_system_prompt(prompt_path, kp_rows)

    conn = db.connect(cfg.db_path)
    try:
        # ---- 预检先行：全部题目必须是 processed（打标对象隔离，决策⑲）；不过关不建批次 ----
        stems = _load_processed_stems(conn, cfg.qids)
        # ---- 批次 create-or-resume（UNIQUE 幂等键）----
        batch_id = _ensure_batch(conn, cfg, arm_cfg, prompt_sha, kp_snapshot_sha)
        done_ids = {
            r["q_id"] for r in conn.execute(
                "SELECT q_id FROM q_annotation_results WHERE batch_id=? AND status IN (?,?)",
                (batch_id, *_DONE_STATUSES))
        }
    finally:
        conn.close()

    pending = deque(q_id for q_id in cfg.qids if q_id not in done_ids)
    if cfg.max_items is not None:
        pending = deque(list(pending)[: cfg.max_items])

    # 组级既有累计（决策㉖防绕过：同一 budget_group 历史批次费用全部计入初始值）
    conn = db.connect(cfg.db_path)
    group_spent = float(conn.execute(
        "SELECT COALESCE(SUM(cost_cny_est), 0) s FROM annotation_batches"
        " WHERE budget_group=?", (cfg.budget_group,)).fetchone()["s"])
    conn.close()
    ledger = BudgetLedger(cfg.budget_cap_cny, cfg.budget_warn_at_cny,
                          cfg.budget_pause_at_cny, initial_cost_cny=group_spent)
    limiter = AdaptiveLimiter(cfg.concurrency_initial, cfg.concurrency_max)

    manifest = _JsonlWriter(cfg.manifest_dir / f"{batch_id}.jsonl")
    detail = _JsonlWriter(cfg.detail_dir / f"tag_detail_{batch_id}.jsonl")
    manifest.write({
        "event": "batch_start", "batch_id": batch_id, "resumed": bool(done_ids),
        "arm": cfg.arm, "strategy": arm_cfg["strategy"], "scope": cfg.scope,
        "task_type": cfg.task_type, "model": cfg.model,
        "prompt_id": arm_cfg["prompt_id"], "prompt_version": cfg.prompt_version,
        "prompt_sha256": prompt_sha, "kp_snapshot_sha256": kp_snapshot_sha,
        "budget_group": cfg.budget_group, "budget_cap_cny": cfg.budget_cap_cny,
        "budget_warn_at_cny": ledger.warn_at, "budget_pause_at_cny": ledger.pause_at,
        "group_spent_before_cny": round(group_spent, 6),
        "worst_per_call_cny": cfg.worst_per_call_cny,
        "config": {"temperature": cfg.temperature,
                   "concurrency_initial": cfg.concurrency_initial,
                   "concurrency_max": cfg.concurrency_max,
                   "max_items": cfg.max_items, "retries": RETRY_MAX},
        "total_questions": len(cfg.qids), "pending": len(pending),
    })
    rng = random.Random(42)
    counters = {"ok": 0, "unmapped": 0, "failed": 0, "invalid": 0}
    failed_qids: list[str] = []
    invalid_qids: list[str] = []
    last_model_version = ""
    checkpoint_base = 0
    remind_event_written = False
    pause_event_written = False

    t0 = time.perf_counter()

    def _period() -> str:
        return cfg.force_period or pricing.period_at(datetime.now(timezone.utc))

    def _process_item(q_id: str) -> dict:
        """单题完整流程（含重试/纠错重试/落库/计费）。线程内独立连接。"""
        stem = stems[q_id]
        allowed = set(all_kp_ids)
        sys_prompt = system_prompt_a
        if arm_cfg["use_recall"]:
            candidates = list(recaller.recall(q_id, stem))  # type: ignore[union-attr]
            candidates = [k for k in candidates if k in kp_map]  # 防召回越界
            allowed = set(candidates)
            sys_prompt, _ = prompts.build_system_prompt(prompt_path, [kp_map[k] for k in candidates])
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompts.build_user_message(stem)},
        ]
        item = {"q_id": q_id, "status": None, "retries": 0, "calls": 0,
                "cost_cny": 0.0, "model_version": "", "error": None}

        def _call(msgs: list[dict]) -> ChatResult | None:
            """一次调用：预留→调用→计费。429 触发 AIMD 降并发；预算硬停返回 None。"""
            worst = cfg.worst_per_call_cny
            if not ledger.try_reserve(worst):
                return None
            try:
                result = client.chat_json(messages=msgs, model=cfg.model,
                                          temperature=cfg.temperature)
            finally:
                # 无论成败都释放本次预留（失败调用无 usage，不产生实际费用）
                with ledger._lock:
                    ledger.reserved = max(0.0, ledger.reserved - worst)
            actual = pricing.cost_cny(result.usage, cfg.model, _period())
            ledger.commit(actual, result.usage)
            item["calls"] += 1
            item["cost_cny"] += actual
            item["model_version"] = result.model_version
            return result

        # ---- 首次调用（指数退避重试）----
        result = None
        last_error = None
        for attempt in range(RETRY_MAX):
            if ledger.pause_requested or ledger.hard_stopped:
                last_error = ("budget pause line（暂停线已达，等待人工确认后续跑）"
                              if ledger.pause_requested else "budget hard stop")
                break
            try:
                got = _call(messages)
                if got is None:
                    last_error = "budget hard stop"
                    break
                result = got
                limiter.on_success()
                break
            except RetryableError as exc:
                if getattr(exc, "throttled", False):
                    limiter.on_throttled()
                last_error = f"retryable: {exc}"
                item["retries"] += 1
                if attempt < RETRY_MAX - 1 and not ledger.hard_stopped:
                    time.sleep(cfg.backoff_base * BACKOFF_STEPS[attempt]
                               + rng.random() * 0.1 * cfg.backoff_base)
            except FatalError as exc:
                last_error = f"fatal: {exc}"
                break

        if result is None:
            item["status"] = "failed"
            item["error"] = last_error
            return item

        # ---- 输出校验（失败→1 次带错误反馈的纠正重试）----
        parsed, err = validate_output(result.content, allowed)
        if parsed is None and not (ledger.pause_requested or ledger.hard_stopped):
            fix_messages = messages + [
                {"role": "assistant", "content": result.content},
                {"role": "user", "content":
                    f"你的输出不符合要求：{err}。"
                    "请严格按系统提示的 JSON 格式重新输出，不要包含任何其他内容。"},
            ]
            try:
                fixed = _call(fix_messages)
                if fixed is None:  # 暂停/硬停触发：不再发任何请求，按 failed 中间态记录
                    item["status"] = "failed"
                    item["error"] = "budget pause/hard stop during corrective retry"
                    return item
                limiter.on_success()
                result = fixed
                parsed, err2 = validate_output(result.content, allowed)
                if parsed is None:
                    err = f"纠错重试后仍无效：{err2}"
                else:
                    item["retries"] += 1
            except RetryableError as exc:
                err = f"纠错重试调用失败：{exc}"
                item["retries"] += 1
            except FatalError as exc:
                err = f"纠错重试调用失败：{exc}"

        if parsed is None:
            item["status"] = "invalid"
            item["error"] = err
            return item

        item["status"] = "ok" if parsed["kp_ids"] else "unmapped"
        item["parsed"] = parsed
        return item

    with ThreadPoolExecutor(max_workers=cfg.concurrency_max) as pool:
        futures: dict = {}
        stop_event_written = False
        while pending or futures:
            # AIMD：按当前 limit 补充在途任务；暂停线/硬停线触发后只排水不再提交
            while pending and len(futures) < limiter.limit \
                    and not ledger.hard_stopped and not ledger.pause_requested:
                q_id = pending.popleft()
                futures[pool.submit(_process_item, q_id)] = q_id
            if not futures:
                break
            done, _ = wait(set(futures), return_when=FIRST_COMPLETED)
            for fut in done:
                q_id = futures.pop(fut)
                try:
                    item = fut.result()
                except Exception as exc:  # 防御：单题意外异常不拖垮整批
                    item = {"q_id": q_id, "status": "failed",
                            "error": f"unexpected: {exc!r}", "retries": 0,
                            "calls": 0, "cost_cny": 0.0, "model_version": ""}
                status = item["status"]
                counters[status] += 1
                if status == "failed":
                    failed_qids.append(q_id)
                elif status == "invalid":
                    invalid_qids.append(q_id)
                if item.get("model_version"):
                    last_model_version = item["model_version"]
                detail.write(item)  # 逐题明细（含 parsed 标注结果——决策①staging 层）
                if status in ("ok", "unmapped"):
                    _write_results(cfg.db_path, batch_id, q_id, item["parsed"])
                elif status in ("failed", "invalid"):
                    # 中间态也落 results（断点可见；重跑成功后 INSERT OR REPLACE 覆盖）
                    _write_intermediate_result(cfg.db_path, batch_id, q_id, status,
                                               item.get("error"))
                processed_total = sum(counters.values())
                if processed_total - checkpoint_base >= CHECKPOINT_EVERY:
                    checkpoint_base = processed_total
                    manifest.write({
                        "event": "checkpoint", "batch_id": batch_id,
                        "done": processed_total, **counters,
                        "cost_cny": round(ledger.cost, 6),
                        "input_tokens": ledger.input_tokens,
                        "output_tokens": ledger.output_tokens,
                        "cache_hit_tokens": ledger.cache_hit_tokens,
                    })
                # 提醒线（决策㉖）：通知+记录实际支出，不停止
                if ledger.reminded and not remind_event_written:
                    remind_event_written = True
                    manifest.write({
                        "event": "budget_remind", "batch_id": batch_id,
                        "remind_line_cny": ledger.warn_at,
                        "run_cost_cny": round(ledger.cost - group_spent, 6),
                        "group_cost_cny": round(ledger.cost, 6),
                        "action": "提醒线已达：通知并记录实际支出，继续运行（不自动停止）",
                    })
                # 暂停线（决策㉖）：停止提交新请求，记录进度/费用/剩余题量/预计剩余费用
                if ledger.pause_requested and not pause_event_written:
                    pause_event_written = True
                    remaining_items = len(pending) + len(futures)
                    run_cost = ledger.cost - group_spent
                    avg = run_cost / ledger.calls if ledger.calls else None
                    est_remaining = (avg if avg is not None
                                     else cfg.worst_per_call_cny) * remaining_items
                    manifest.write({
                        "event": "budget_pause", "batch_id": batch_id,
                        "pause_line_cny": ledger.pause_at,
                        "run_cost_cny": round(run_cost, 6),
                        "group_cost_cny": round(ledger.cost, 6),
                        "remaining_items": remaining_items,
                        "est_remaining_cny": round(est_remaining, 6),
                        "action": "暂停新请求（在途排水完成）；等待人工确认后续跑，"
                                  "断点续跑跳过已完成题",
                    })
                if ledger.hard_stopped and not stop_event_written:
                    stop_event_written = True
                    manifest.write({
                        "event": "budget_stop", "batch_id": batch_id,
                        "cost_cny": round(ledger.cost, 6), "cap_cny": ledger.cap,
                        "action": "硬停止：禁止任何新请求（重试/续跑/新建批次均被组级累计拦截）",
                    })

    elapsed = round(time.perf_counter() - t0, 1)

    # ---- 批次收尾：aborted（硬停/暂停线/max_items）或 completed ----
    stop_reason = None
    if ledger.hard_stopped:
        stop_reason = "budget_hard_stop"
    elif ledger.pause_requested:
        stop_reason = "budget_pause_line"
    elif pending:
        stop_reason = "max_items" if cfg.max_items is not None else "stopped"
    status = "completed" if stop_reason is None else "aborted"

    conn = db.connect(cfg.db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        # 计数/Token/费用为**累加**语义（续跑合并到批次的既有累计值）；
        # status/total_questions/model_version 为绝对值（本 run 的终态与范围）
        conn.execute(
            "UPDATE annotation_batches SET status=?, model_version=?, total_questions=?,"
            " succeeded = succeeded + ?, unmapped = unmapped + ?,"
            " failed = failed + ?, invalid = invalid + ?,"
            " input_tokens_sum = input_tokens_sum + ?,"
            " output_tokens_sum = output_tokens_sum + ?,"
            " cache_hit_tokens_sum = cache_hit_tokens_sum + ?,"
            " cost_cny_est = cost_cny_est + ?,"
            " finished_at=?, notes=COALESCE(notes,'') || ?"
            " WHERE batch_id=?",
            (status, last_model_version, len(cfg.qids),
             counters["ok"], counters["unmapped"], counters["failed"], counters["invalid"],
             ledger.input_tokens, ledger.output_tokens, ledger.cache_hit_tokens,
             round(ledger.cost - group_spent, 6),
             _utcnow() if status == "completed" else None,
             f" [{stop_reason}]" if stop_reason else "", batch_id),
        )
        conn.commit()
    finally:
        conn.close()

    summary = {
        "batch_id": batch_id, "status": status, "stop_reason": stop_reason,
        "counters": dict(counters), "failed_qids": failed_qids,
        "invalid_qids": invalid_qids,
        "cost_cny": round(ledger.cost - group_spent, 6),   # 本 run 费用
        "group_cost_cny": round(ledger.cost, 6),           # budget_group 累计费用
        "reminded": ledger.reminded, "pause_requested": ledger.pause_requested,
        "calls": ledger.calls, "elapsed_seconds": elapsed,
        "input_tokens": ledger.input_tokens, "output_tokens": ledger.output_tokens,
        "cache_hit_tokens": ledger.cache_hit_tokens,
        "concurrency_final": limiter.limit, "throttle_events": limiter.throttle_events,
        "pending_left": len(pending),
    }
    manifest.write({"event": "batch_end", "batch_id": batch_id, "status": status,
                    "stop_reason": stop_reason, "run_scoped": True, **counters,
                    "failed_qids": failed_qids, "invalid_qids": invalid_qids,
                    "cost_cny": round(ledger.cost - group_spent, 6),
                    "group_cost_cny": round(ledger.cost, 6),
                    "calls": ledger.calls,
                    "input_tokens": ledger.input_tokens,
                    "output_tokens": ledger.output_tokens,
                    "cache_hit_tokens": ledger.cache_hit_tokens,
                    "elapsed_seconds": elapsed,
                    "concurrency_final": limiter.limit})
    log.info("批次 %s 完成：%s %s 费用 %.6f 元", batch_id, status, counters, ledger.cost)
    return summary


# ---------------------------------------------------------------- 数据访问

def _load_kp_rows(db_path: Path) -> list[dict]:
    """加载小学 82 个 KP 定义（唯一 prompt 知识来源；不触 q_kp——决策㉔）。"""
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT kp_id, name, description FROM knowledge_points"
            " WHERE stage='primary' AND source='ape210k_rule' ORDER BY kp_id"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise RuntimeError("knowledge_points 中无小学规则 KP（source='ape210k_rule'）")
    return [dict(r) for r in rows]


def _load_processed_stems(conn, qids: list[str]) -> dict[str, str]:
    """预检：打标对象必须是 processed（决策⑲）；缺失/非 processed 即拒绝运行。"""
    stems: dict[str, str] = {}
    missing: list[str] = []
    for q_id in qids:
        row = conn.execute(
            "SELECT stem, quality_state FROM questions WHERE q_id=?", (q_id,)).fetchone()
        if row is None or row["quality_state"] != "processed":
            missing.append(q_id)
        else:
            stems[q_id] = row["stem"]
    if missing:
        raise ValueError(f"{len(missing)} 个 q_id 不存在或非 processed，拒绝打标："
                         f"{missing[:5]}{'…' if len(missing) > 5 else ''}")
    return stems


def _ensure_batch(conn, cfg: ExecutorConfig, arm_cfg: dict, prompt_sha: str,
                  kp_snapshot_sha: str) -> str:
    """批次 create-or-resume：UNIQUE(scope,task_type,strategy,model,prompt_id,version)。

    已完成批次 → RuntimeError（防重复标注；重跑须用新 prompt_version 或删批）。
    其余状态 → 续跑（running/aborted/failed 均恢复为 running）。
    """
    key = (cfg.scope, cfg.task_type, arm_cfg["strategy"], cfg.model,
           arm_cfg["prompt_id"], cfg.prompt_version)
    row = conn.execute(
        "SELECT batch_id, status FROM annotation_batches WHERE scope=? AND task_type=?"
        " AND strategy=? AND model=? AND prompt_id=? AND prompt_version=?", key,
    ).fetchone()
    if row is not None:
        if row["status"] == "completed":
            raise RuntimeError(
                f"批次 {row['batch_id']} 已完成（prompt_version={cfg.prompt_version}）。"
                "如需重新标注请使用新 prompt_version（决策㉑幂等键约束）")
        conn.execute("UPDATE annotation_batches SET status='running' WHERE batch_id=?",
                     (row["batch_id"],))
        conn.commit()
        log.info("续跑批次 %s（原状态 %s）", row["batch_id"], row["status"])
        return row["batch_id"]
    seq_row = conn.execute(
        "SELECT COUNT(*) n FROM annotation_batches WHERE batch_id LIKE ?",
        (f"ann_{datetime.now(timezone.utc).strftime('%Y%m%d')}_%",)).fetchone()
    batch_id = f"ann_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{seq_row['n'] + 1:02d}_{arm_cfg['strategy'].split('_')[0].lower()}"
    conn.execute(
        "INSERT INTO annotation_batches (batch_id, task_type, scope, strategy, provider,"
        " model, prompt_id, prompt_version, prompt_sha256, kp_snapshot_sha256,"
        " budget_group, status, config_json, created_at)"
        " VALUES (?, ?, ?, ?, 'deepseek', ?, ?, ?, ?, ?, ?, 'running', ?, ?)",
        (batch_id, cfg.task_type, cfg.scope, arm_cfg["strategy"], cfg.model,
         arm_cfg["prompt_id"], cfg.prompt_version, prompt_sha, kp_snapshot_sha,
         cfg.budget_group,
         json.dumps({"temperature": cfg.temperature,
                     "concurrency_initial": cfg.concurrency_initial,
                     "concurrency_max": cfg.concurrency_max,
                     "retries": RETRY_MAX, "backoff_steps": list(BACKOFF_STEPS)},
                    ensure_ascii=False),
         _utcnow()),
    )
    conn.commit()
    log.info("创建批次 %s（%s/%s）", batch_id, cfg.arm, cfg.model)
    return batch_id


def _write_results(db_path: Path, batch_id: str, q_id: str, parsed: dict) -> None:
    """逐条事务落库：题级结果 + KP 标签 + 难度（ok/unmapped 均为有效终态）。"""
    now = _utcnow()
    conn = db.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR REPLACE INTO q_annotation_results"
            " (q_id, batch_id, status, primary_kp_id, reason, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (q_id, batch_id, "ok" if parsed["kp_ids"] else "unmapped",
             parsed["primary"], parsed["reason"], now),
        )
        if parsed["kp_ids"]:
            conn.execute("DELETE FROM q_kp_llm WHERE q_id=? AND batch_id=?",
                         (q_id, batch_id))
            for kp_id in parsed["kp_ids"]:
                conn.execute(
                    "INSERT INTO q_kp_llm (q_id, kp_id, batch_id, is_primary, confidence,"
                    " review_state, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                    (q_id, kp_id, batch_id, 1 if kp_id == parsed["primary"] else 0,
                     parsed["confidence"]["kp"], now),
                )
        conn.execute(
            "INSERT OR REPLACE INTO q_difficulty_llm"
            " (q_id, batch_id, difficulty, confidence, review_state, created_at)"
            " VALUES (?, ?, ?, ?, 'pending', ?)",
            (q_id, batch_id, parsed["difficulty"], parsed["confidence"]["difficulty"], now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _write_intermediate_result(db_path: Path, batch_id: str, q_id: str,
                               status: str, error: str | None) -> None:
    """failed/invalid 中间态落 results（无标签行；重跑成功后覆盖）。"""
    conn = db.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT OR REPLACE INTO q_annotation_results"
            " (q_id, batch_id, status, primary_kp_id, reason, created_at)"
            " VALUES (?, ?, ?, NULL, ?, ?)",
            (q_id, batch_id, status,
             (str(error)[:REASON_MAX_CHARS] if error else None), _utcnow()),
        )
        conn.execute("DELETE FROM q_kp_llm WHERE q_id=? AND batch_id=?", (q_id, batch_id))
        conn.execute("DELETE FROM q_difficulty_llm WHERE q_id=? AND batch_id=?",
                     (q_id, batch_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
