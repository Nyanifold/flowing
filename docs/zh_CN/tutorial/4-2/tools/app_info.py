"""应用信息工具（4-2）：读 Runtime 级全局袋（跨 Agent 共享的持久化状态）。"""
from flowing import Agent, ScriptTool


class AppInfoTool(ScriptTool):
    """读取应用级信息（如启动次数）。演示 Runtime 全局状态袋的访问面。"""

    name = "app-info"

    async def execute(self, *, caller: Agent) -> dict:
        app = caller.runtime.states["app"]   # Runtime 级命名袋
        return {"boots": app.get("boots", 0)}
