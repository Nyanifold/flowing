"""崩溃模拟（4-1）：os._exit(9) 等价于 kill -9——不走 shutdown()。

write-behind 的排空屏障不执行，但已提交记录不重放丢失（崩溃窗口演示）。
运行：uv run python demo_crash.py
"""
import asyncio
import os

from flowing import launch


async def main() -> None:
    runtime = await launch(".")            # 恢复管线：重放 tree/state 日志
    agent = await runtime.get_agent("agent-main")
    result = await agent.query("再记住一个颜色：紫色。")
    print(f"回合完成 status={result.status}；现在模拟 kill -9（os._exit(9)）", flush=True)
    os._exit(9)                            # 无 shutdown：进程即刻死亡


asyncio.run(main())
