"""上下文预算观测演示（3-4）：estimate_context_tokens 的锚点实测 + 尾部估算。

每跑一轮打印一次估算，观察 ratio 随对话增长。运行：uv run python demo_budget.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    est = agent.estimate_context_tokens()
    print(f"初始（空树）：tokens={est.tokens} measured={est.measured} "
          f"estimated={est.estimated} ratio={est.usage_ratio}")
    for i in range(6):
        await agent.query(
            f"第 {i + 1} 问：换一个角度，用一句话谈谈『上下文窗口』。")
        est = agent.estimate_context_tokens()
        print(f"第 {i + 1} 轮后：tokens={est.tokens} measured={est.measured} "
              f"estimated={est.estimated} ratio={est.usage_ratio:.4f}")
    await runtime.shutdown()


asyncio.run(main())
