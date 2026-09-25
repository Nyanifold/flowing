"""visible=False 的可见性/可执行性分离演示（2-1）。

运行：uv run python demo_enabled.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")
    print("subagents 绑定表：")
    for alias, entry in root._subagent_entries.items():
        print(f"  {alias}: visible={entry.visible} -> "
              f"{'进 catalog（LLM 可见）' if entry.visible else '不进 catalog（LLM 不可见）'}")
    result = await root.invoke_subagent("auditor", prompt="激活。")
    print(f"编程式唤起 visible=False 的 auditor -> "
          f"result={result.result!r} status={result.subagent_status}")
    await runtime.shutdown()


asyncio.run(main())
