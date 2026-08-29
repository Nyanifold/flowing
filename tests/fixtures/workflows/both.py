"""类形态与顶层 run 并存（T86）：类形态优先，顶层 run 被忽略。"""

from flowing.plugins.workflow import Workflow


class BothWorkflow(Workflow):
    async def run(self, prompt: str | None = None):
        return {"form": "class"}


async def run(prompt: str | None = None):
    return {"form": "function"}
