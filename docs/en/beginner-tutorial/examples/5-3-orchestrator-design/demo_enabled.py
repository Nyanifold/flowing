"""Demo of the visibility/executability split for visible=False subagents.

Run: uv run python demo_enabled.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")
    print("subagent binding table:")
    for alias, entry in root._subagent_entries.items():
        print(f"  {alias}: visible={entry.visible} -> "
              f"{'in catalog (LLM-visible)' if entry.visible else 'not in catalog (LLM-invisible)'}")
    result = await root.invoke_subagent("auditor", prompt="Activate.")
    print(f"programmatically invoking visible=False auditor -> "
          f"result={result.result!r} status={result.subagent_status}")
    await runtime.shutdown()


asyncio.run(main())
