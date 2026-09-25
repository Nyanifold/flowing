"""Audit tool (glob material #2 of the 5-1 complex assembly demo)."""
from flowing import ScriptTool


class AuditTool(ScriptTool):
    """Write an audit log entry (demo)."""

    name = "audit"

    async def execute(self, *, action: str) -> dict:
        return {"audited": action}
