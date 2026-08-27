"""Tool 注册表。

统一管理 Agent 可用的工具：提供 OpenAI Function Calling 所需的 schema，
以及带校验的执行入口。工具分为两类（对应技术方案 §5 / §9）：
    - LLM 工具（search_knowledge_points / generate_questions / generate_diagnosis）：
      注册进注册表，可被 Executor 的工具循环调用；
    - 确定性文档服务（exam_docx / answer_sheet / report_pdf / lesson_plan）：
      不进注册表，由 Workflow 确定性调用，LLM 永远拿不到它们的 schema。

参数校验用 Pydantic 模型作为单一来源：既通过 model_json_schema() 生成给
LLM 的 tools 参数，又在执行前用 model_validate() 做严格校验，不新增 jsonschema 依赖。
"""
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ValidationError


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict              # JSON Schema（发给 LLM）
    params_model: type[BaseModel]
    handler: Callable             # 接收 params_model 实例，返回 dict


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(
        self, name: str, description: str, params_model: type[BaseModel], handler: Callable
    ) -> None:
        self._tools[name] = Tool(
            name=name,
            description=description,
            parameters=params_model.model_json_schema(),
            params_model=params_model,
            handler=handler,
        )

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_schemas(self) -> list[dict]:
        """返回全部工具的 OpenAI tools 格式 schema 列表。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    def execute(self, name: str, params: dict) -> dict:
        """校验入参并执行工具；任何失败都返回 {"error": ...}，不向外抛异常。"""
        tool = self._tools.get(name)
        if tool is None:
            return {"error": f"未知工具：{name}"}
        try:
            validated = tool.params_model.model_validate(params)
        except ValidationError as exc:
            return {"error": f"参数校验失败：{exc.errors()}"}
        try:
            return tool.handler(validated)
        except Exception as exc:  # 工具异常统一转结构化错误，回喂 LLM 自纠
            return {"error": f"工具执行失败：{exc}"}


registry = ToolRegistry()


def tool(name: str, description: str, params_model: type[BaseModel]):
    """装饰器：把函数注册为工具（入参经 params_model 校验后传给函数）。"""
    def decorator(fn: Callable):
        registry.register(name, description, params_model, fn)
        return fn
    return decorator
