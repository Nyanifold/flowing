"""App-info tool (4-2): reads the Runtime-global bag (persistent state shared across Agents)."""
from flowing import Agent, ScriptTool


class AppInfoTool(ScriptTool):
    """Reads application-level info (e.g. launch count). Demonstrates the access surface of the Runtime-global state bag."""

    name = "app-info"

    async def execute(self, *, caller: Agent) -> dict:
        app = caller.runtime.states["app"]   # Runtime-level named bag
        return {"boots": app.get("boots", 0)}
