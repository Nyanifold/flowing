"""Programmatic fan-out demo (2-4, offline script): create_subagent + asyncio.gather.

The second orchestration mode alongside LLM routing: code creates multiple
child Agents directly and lets them work in parallel.
Run: uv run python demo_fanout.py
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
                f"In the working directory, write a notes/{topic}.md whose body is one sentence "
                f"explaining the role of '{topic}' in this project "
                "(create the directory first, then write); "
                "submit with finish when done.")
            return f"{name}: status={result.status}"
        finally:
            await child.destroy()

    # Parallel fan-out: two coders work at the same time; gather waits for all (the wait graph must not form a cycle!)
    results = await asyncio.gather(job("coder-a", "adder"), job("coder-b", "divider"))
    for line in results:
        print(line)
    await runtime.shutdown()


asyncio.run(main())
