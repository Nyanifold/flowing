"""后台构建工具（4-5）：async generator 形态——首 yield 收据、逐段进度。

后台三形态之一：execute 写成 async generator。首 yield = pending 收据
（回合不阻塞）；后续每个 yield = 一段产物（EVENT 消息投递）；自然耗尽
不产生 final production（收尾标记由作者自行 yield）。
"""
import asyncio

from pydantic import BaseModel

from flowing import ScriptTool


class BuildArgs(BaseModel):
    target: str = "app"


class BuildTool(ScriptTool):
    """模拟一次构建：收据 + 三段进度 + 完成摘要（后台执行，不阻塞回合）。"""

    name = "build"
    args_model = BuildArgs

    async def execute(self, *, target: str):
        yield {"receipt": f"构建任务已受理：{target}"}   # 首 yield = pending 收据
        for step, label in enumerate(["编译", "打包", "校验"], start=1):
            await asyncio.sleep(0.6)                    # 长任务：后台慢慢跑
            yield f"进度 {step}/3：{label}完成"
        yield {"done": True, "artifact": f"dist/{target}.tar"}   # 完成摘要（自定收尾段）
