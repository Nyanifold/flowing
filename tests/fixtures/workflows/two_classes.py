"""歧义负例（T86）：两个 Workflow 子类——一个文件一个编排，属形态歧义。"""

from flowing.plugins.workflow import Workflow


class FirstWorkflow(Workflow):
    async def run(self, prompt: str | None = None):
        return {"form": "first"}


class SecondWorkflow(Workflow):
    async def run(self, prompt: str | None = None):
        return {"form": "second"}
