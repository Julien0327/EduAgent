"""Online 模式适配（S2-B）：真实/Mock LLM 注入 + 成本聚合。

**约束（S2-B 范围）**：不发起任何真实 LLM/API 请求。真实调用在用户批准
（决策㊴/㊲③）后才启用；本阶段默认 `MockLLM`（模拟 DeepSeek 返回真实 usage），
用于成本统计与预算保护的单元/集成测试。

复用 S2-A 统一执行接口：dispatch_case → run_workflow_case 以 `mode` 参数切换
provider 注入；执行后从 tracing 聚合真实成本（tokens/cost/usage_source/cost_known）。
"""
from __future__ import annotations

from pathlib import Path

from backend.app.agent.llm_client import LLMResponse


def install_online_env(llm_provider, workdir: Path) -> None:
    """把 LLM / 检索 / 产物路径改写为 online 语义。

    llm_provider：真实 DeepSeekProvider（批准后）或 MockLLM（本阶段）。
    检索保持 FakeKpService（S2 聚焦链路与成本；真实检索属于 S3+ 增强）。
    """
    import backend.app.agent.llm_utils as llmu
    import backend.app.tools.knowledge_tools as kt
    import backend.app.storage.file_store as fstore
    from backend.app.eval.runner import FakeKpService

    llmu.get_llm_service = lambda: llm_provider
    kt.get_retrieval_service = lambda: FakeKpService()
    (workdir / "outputs").mkdir(parents=True, exist_ok=True)
    fstore.OUTPUTS_ROOT = workdir / "outputs"


class MockLLM:
    """确定性 Mock：模拟 DeepSeek 返回（含 usage 上报），不发起网络请求。

    用于成本统计/预算保护测试：给每次调用分配 tokens，cost 由 pricing 计算；
    planner 与出题逻辑复用 FakeLLM（保持 workflow/结构 与 fake 档一致），
    usage/model 走真实 provider 上报路径（tracing.record_llm_call）。
    两类均不发起真实调用。
    """

    def __init__(self, seed: int = 0, prompt_tokens: int = 500,
                 completion_tokens: int = 300, cache_hit_tokens: int = 100,
                 model: str = "deepseek-flash"):
        self.seed = seed
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cache_hit_tokens = cache_hit_tokens
        self.model = model
        self.calls: list[dict] = []
        from backend.app.eval.fake_llm import FakeLLM
        self._fake = FakeLLM(seed=seed)

    def chat_with_tools(self, messages, tools=None, tool_choice="auto"):
        name = self._tool_name(tools)
        self.calls.append({"tool": name})
        args = self._arguments_for(name, messages)
        # 模拟真实供应商上报路径：DeepSeekProvider._request 会调用 tracing
        # record_llm_call（usage → pricing → llm_calls 明细）。Mock 走同一路径，
        # 使"成本从 traces 聚合"的闭环与线上一致。
        self._record(name, messages)
        return LLMResponse(
            content="", model=self.model,
            tool_calls=[{"id": f"mock-{self.seed}", "name": name,
                         "arguments": args}],
            usage={"prompt_tokens": self.prompt_tokens,
                   "completion_tokens": self.completion_tokens,
                   "cache_hit_tokens": self.cache_hit_tokens},
        )

    def _record(self, tool: str, messages: list) -> None:
        try:
            from backend.app.tracing import collector as tracing
            tracing.record_llm_call(
                model=self.model, tool=tool,
                usage={"prompt_tokens": self.prompt_tokens,
                       "completion_tokens": self.completion_tokens,
                       "cache_hit_tokens": self.cache_hit_tokens},
                latency_ms=0, error=None)
        except Exception:
            pass  # 采集失败不阻断（同线上语义）

    def _arguments_for(self, name: str, messages: list) -> dict:
        """按工具返回合法结构化参数。

        planner 复用 FakeLLM 的复刻规则（submit_plan 按 prompt 推 workflow/params）——
        保证 mock 冒烟的 workflow/参数结构与 fake 一致（否则 planner 准确率失真），
        同时带 usage 上报（成本统计/预算验证）。"""
        user = "\n".join(m.get("content", "") for m in messages
                         if m.get("role") == "user")
        if name == "submit_plan":
            from backend.app.eval.fake_llm import _find_plan_workflow, plan_arguments
            return {"workflow": _find_plan_workflow(user)["workflow"],
                    "params": plan_arguments(user)}
        if name == "submit_questions":
            # 复用 FakeLLM 的"按请求 count 出题 + 高量≥20 上限"逻辑（结构与 fake 一致）
            raw = self._fake._resp_submit_questions(user)
            return raw.tool_calls[0]["arguments"]
        if name == "submit_diagnosis":
            return {"summary": "班级整体良好", "diagnosis": "个别知识点薄弱",
                    "suggestions": ["加强练习", "分层教学"]}
        if name == "submit_lesson_plan":
            return {"objectives": ["目标1", "目标2", "目标3"],
                    "key_points": "重点", "difficult_points": "难点",
                    "process": [{"step": "导入", "detail": "复习"},
                                {"step": "新授", "detail": "推导"}],
                    "homework": "课后作业"}
        if name == "submit_judge_scores":
            return {"content_correctness": 4, "difficulty_fit": 4,
                    "coverage_completeness": 4, "expression": 4,
                    "citation_compliance": 4, "reason": "Mock 判分（确定性）"}
        return {"mock": True}

    @staticmethod
    def _tool_name(tools):
        if tools:
            first = tools[0] if isinstance(tools, (list, tuple)) else tools
            if isinstance(first, dict):
                fn = first.get("function") or first
                return fn.get("name", "") if isinstance(fn, dict) else ""
        return ""


def aggregate_cost_from_ctx(ctx) -> dict:
    """从 TaskContext.steps 聚合成本（online 真实语义）。

    返回 {cost, cost_known, tokens_in, tokens_out, llm_calls, usage_sources}。
    cost=None 且 cost_known=False 当 steps 无 provider usage（如模板兜底/纯确定性）。
    """
    cost_total = 0.0
    tokens_in = tokens_out = 0
    llm_calls = 0
    any_provider = False
    for s in getattr(ctx, "steps", []) or []:
        c = getattr(s, "cost", None)
        ti = getattr(s, "tokens_in", None)
        to = getattr(s, "tokens_out", None)
        if ti is not None:
            tokens_in += ti
        if to is not None:
            tokens_out += to
        details = getattr(s, "llm_calls", None) or []
        if details:
            llm_calls += sum(1 for d in details if d.get("kind") == "llm")
            for d in details:
                dc = d.get("cost_cny")
                if dc is not None:
                    cost_total += dc
                    any_provider = True
                elif d.get("usage_source") == "provider":
                    any_provider = True
        elif getattr(s, "cost", None) is not None:
            cost_total += c or 0.0
            if getattr(s, "usage_source") == "provider":
                any_provider = True
    if not any_provider and not tokens_in and not tokens_out:
        return {"cost": None, "cost_known": False, "tokens_in": 0,
                "tokens_out": 0, "llm_calls": 0, "usage_sources": []}
    return {"cost": round(cost_total, 6), "cost_known": True,
            "tokens_in": tokens_in, "tokens_out": tokens_out,
            "llm_calls": llm_calls,
            "usage_sources": sorted({getattr(s, "usage_source", "") or "none"
                                     for s in getattr(ctx, "steps", []) or []})}