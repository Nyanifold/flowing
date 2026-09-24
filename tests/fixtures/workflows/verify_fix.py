"""类形态 workflow 样例（T84/T88）：恰好一个 Workflow 子类。

观测通道（测试驱动用）：caller 实例上若有 ``wf_gate``（asyncio.Event）
属性则 run 先等待其置位（证明 run-workflow 工具在 run 完成前已返回
收据）；完成时经 ``caller.enqueue_message`` 交付一条 EVENT 消息
（source="workflow:verify-fix"，文本为运行参数的 JSON 回显）。
"""

import json

from flowing.message import Message, MessageKind, TextBlock
from flowing.plugins.workflow import Workflow


class VerifyFixWorkflow(Workflow):
    """验证-修复循环编排样例（流程本体不在样例范围内）。"""

    async def run(self, prompt: str | None = None, max_rounds: int = 3) -> dict:
        gate = getattr(self.caller, "wf_gate", None)
        if gate is not None:
            await gate.wait()
        echo = json.dumps(
            {"caller": self.caller.node_id, "prompt": prompt,
             "max_rounds": max_rounds},
            ensure_ascii=False,
        )
        await self.caller.enqueue_message(Message(
            kind=MessageKind.EVENT,
            source="workflow:verify-fix",
            content=[TextBlock(text=echo)],
        ))
        return {"status": "passed", "rounds": 1}
