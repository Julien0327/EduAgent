"""评测执行记录模型（S2-A，要求 4：完整失败归因）。

每个 case 的执行结果是可持久化的结构化记录，字段覆盖：
- case 标识 / golden 版本与 SHA256 / workflow / 档位（fake/online）
- 预期行为 vs 实际行为 / Planner 参数预期 vs 实际
- 任务状态 / 产物校验结果（三层） / 耗时 / 成本与可信度
- 错误类型（7 类归因）与错误信息
- 代码 commit

错误类型（fail 归因 7 类，S2-A 定稿）：
0  OK                    —— 无错误（通过）
1  planner_params        —— Planner 参数错误（明确参数错误）
2  workflow_choice       —— Workflow 选择错误
3  api_behavior          —— API 行为错误（clarify/reject/http 不符）
4  workflow_failed       —— Workflow 执行失败（任务 run_workflow 抛错）
5  artifact_generation   —— 产物生成失败（文件缺失/损坏，非内容问题）
6  artifact_content      —— 产物内容不合格（能打开但内容断言失败）
7  infra                 —— 评测基础设施自身异常（断言逻辑/解析错误等）

说明：fail 的 case 若预期行为就是 fail（如 gc_b24）→ 归因不算错误（passed=True）；
若预期别的行为却 fail → 记为 workflow_failed（passed=False）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict


# ---- 行为与归因常量 ----
BEHAVIORS = ("execute", "fallback", "fail", "reject", "reject_scope",
             "reject_400", "reject_404", "reject_422", "clarify")
ERR_OK = "ok"
ERR_PLANNER_PARAMS = "planner_params"
ERR_WORKFLOW_CHOICE = "workflow_choice"
ERR_API_BEHAVIOR = "api_behavior"
ERR_WORKFLOW_FAILED = "workflow_failed"
ERR_ARTIFACT_GENERATION = "artifact_generation"
ERR_ARTIFACT_CONTENT = "artifact_content"
ERR_INFRA = "infra"

ERROR_TYPES = (
    ERR_OK, ERR_PLANNER_PARAMS, ERR_WORKFLOW_CHOICE, ERR_API_BEHAVIOR,
    ERR_WORKFLOW_FAILED, ERR_ARTIFACT_GENERATION, ERR_ARTIFACT_CONTENT, ERR_INFRA,
)


@dataclass
class ArtifactCheck:
    """产物校验单条结果（三层：存在 / 有效 / 内容断言）。"""
    name: str = ""
    exists: bool = False
    valid: bool = False
    content_assertions: dict = field(default_factory=dict)   # {断言名: 是否通过}
    error: str = ""
    path: str = ""                                            # 制品路径（judge 读取文本用）


@dataclass
class CaseRecord:
    """单条 case 的完整执行记录。"""
    case_id: str = ""
    golden_sha256: str = ""          # 冻结版 JSON 完整 sha256
    workflow: str = ""
    execution_mode: str = "fake"     # fake / online
    # 行为
    expected_behavior: str = ""
    actual_behavior: str = ""
    behavior_matched: bool = False
    # planner 参数
    expected_params: dict = field(default_factory=dict)
    actual_params: dict = field(default_factory=dict)
    params_matched: bool = False     # 整体字段匹配
    param_field_matches: dict = field(default_factory=dict)  # {字段: 命中bool}
    # 任务
    task_status: str = ""            # completed / failed / running / pending / 未创建
    task_error: str = ""
    # 产物（三层）
    artifacts: list = field(default_factory=list)  # [ArtifactCheck]
    artifacts_ok: bool = False
    # 表现
    latency_ms: int = 0
    cost: float | None = None        # fake 档 = 0；成本未知时 None（不冒充）
    cost_known: bool = True
    # 归因
    error_type: str = ERR_OK
    error_message: str = ""
    details: str = ""
    rule_scores: dict | None = None   # judge 未采信时规则评分（五维），S4/S6 展示
    quality: dict | None = None       # Phase 5 / S3：question_gen 质检指标（原始统计字段）
    commit: str = ""
    # 汇总判定
    passed: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["artifacts"] = [asdict(a) if isinstance(a, ArtifactCheck) else a for a in self.artifacts]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)