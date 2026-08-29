"""kimi 式 caller 反向调用样例（T89 异步防死锁回归）：run 中
``await self.caller.query(...)`` 等待 caller 的回合产物，完成后经
PLUGIN 消息交付完成信号（文本携带回合 final_text）。
"""

from flowing.message import Message, MessageKind, TextBlock
from flowing.plugins.workflow import Workflow


class CallerQueryWorkflow(Workflow):
    async def run(self, prompt: str | None = None) -> None:
        answer = await self.caller.query(prompt or "?")
        await self.caller.enqueue_message(Message(
            kind=MessageKind.PLUGIN,
            source="workflow:caller-query",
            content=[TextBlock(text=f"got:{answer.final_text}")],
        ))
        return None
