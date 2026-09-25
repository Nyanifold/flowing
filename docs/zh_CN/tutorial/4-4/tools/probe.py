"""探针工具（4-4）：strict=False 的「参数门禁放行」形态。

strict 只存在于 ToolDefinition 层：ScriptTool 的类属性通道不读它，
须显式声明 definition（与 args_model 类属性互斥，definition 胜）。
"""
from flowing import ScriptTool
from flowing.tool import ToolDefinition


class ProbeTool(ScriptTool):
    """strict=False：LLM 多给的未定义参数不触发门禁错误，由 execute 自行处置。"""

    definition = ToolDefinition(
        name="probe",
        description="strict=False 探针：未定义参数放行。",
        params_schema={"known": {"type": "string", "description": "已知参数"}},
        strict=False,
    )

    async def execute(self, *, known: str, **kwargs) -> dict:
        return {"known": known, "extra_seen": sorted(kwargs)}
