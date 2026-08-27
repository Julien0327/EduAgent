"""执行器（Executor）。

封装工具调用与结果校验，对应技术方案 §4 / §10。D3 固定工作流中，Executor 以
"单工具调用"模式运行——由工作流显式指定要调用的工具，而非让 LLM 自主决定调用
序列（避免自由 ReAct 的跑飞 / 死循环风险）。LLM 工具内部的传输重试与结构化
校验重试已由 llm_utils.structured_completion 承担。
"""
from app.tools.registry import registry


class ToolError(Exception):
    """工具调用返回 error 时的可预期错误（消息对用户友好）。"""


def run_tool(name: str, params: dict) -> dict:
    """调用注册表工具并校验结果；结果含 error 时抛 ToolError。"""
    result = registry.execute(name, params)
    if isinstance(result, dict) and "error" in result:
        raise ToolError(result["error"])
    return result
