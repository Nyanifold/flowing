"""Demo of the visibility/executability separation for visible=False (2-1).

Run: uv run python demo_enabled.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")
    print("subagents binding table:")
    for alias, entry in root._subagent_entries.items():
        print(f"  {alias}: visible={entry.visible} -> "
              f"{'in catalog (visible to the LLM)' if entry.visible else 'not in catalog (invisible to the LLM)'}")
    result = await root.invoke_subagent("auditor", prompt="Activate.")
    print(f"programmatic invocation of the visible=False auditor -> "
          f"result={result.result!r} status={result.subagent_status}")
    await runtime.shutdown()


asyncio.run(main())
