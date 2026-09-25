"""Probe tool (4-4): the strict=False "parameter-gate passthrough" form.

strict exists only at the ToolDefinition layer: the ScriptTool class-attribute
channel does not read it, so definition must be declared explicitly
(mutually exclusive with the args_model class attribute; definition wins).
"""
from flowing import ScriptTool
from flowing.tool import ToolDefinition


class ProbeTool(ScriptTool):
    """strict=False: undefined parameters the LLM adds do not trigger the gate error; execute handles them as it sees fit."""

    definition = ToolDefinition(
        name="probe",
        description="strict=False probe: undefined parameters pass through.",
        params_schema={"known": {"type": "string", "description": "Known parameter"}},
        strict=False,
    )

    async def execute(self, *, known: str, **kwargs) -> dict:
        return {"known": known, "extra_seen": sorted(kwargs)}
