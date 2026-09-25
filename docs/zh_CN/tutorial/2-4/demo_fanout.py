"""程序化 fan-out 演示（2-4，离线脚本）：create_subagent + asyncio.gather。

与 LLM 路由并列的第二种编排模式：代码直接创建多个子 Agent 并行干活。
运行：uv run python demo_fanout.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".", cwd="target")
    root = await runtime.get_agent("agent-main")

    async def job(name: str, topic: str) -> str:
        child = await root.create_subagent("@/agents/coder", name=name)
        try:
            result = await child.query(
                f"在工作目录里写一个 notes/{topic}.md，正文一句话说明"
                f"「{topic}」在这个项目中的作用（先建目录再写），"
                "写完用 finish 交卷。")
            return f"{name}: status={result.status}"
        finally:
            await child.destroy()

    # 并行 fan-out：两个 coder 同时干活，gather 等全部完成（等待图不成环！）
    results = await asyncio.gather(job("coder-a", "adder"), job("coder-b", "divider"))
    for line in results:
        print(line)
    await runtime.shutdown()


asyncio.run(main())
