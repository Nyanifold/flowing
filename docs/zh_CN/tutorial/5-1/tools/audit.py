"""审计工具（5-1 复杂装配示例的 glob 素材之二）。"""
from flowing import ScriptTool


class AuditTool(ScriptTool):
    """写一条审计日志（示例）。"""

    name = "audit"

    async def execute(self, *, action: str) -> dict:
        return {"audited": action}
