"""long-task 的 callable 实现——普通 async def + background: true（B6，非 generator）。

background: true 使其经 ensure_future 落既有 Task/pending 分支（B6），
完成时投递标注块 + 结果块 EVENT。
"""

import asyncio


async def long_task(seconds: float = 0.05) -> dict:
    """长任务：单次返回（后台化由 background: true 触发）。"""
    await asyncio.sleep(seconds)
    return {"status": "done"}
