"""Model selection demo (3-4, offline): two-hop resolution recap / runtime model switch / a subagent on a different tag.

Run: uv run python demo_model.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")

    # (1) two-hop resolution: model_tag -> tag mapping -> model entry
    print(f"(1) Root agent: model_tag={root.model_tag!r} -> model={root.model.model!r}")
    print(f"    provider entry binding: {root.model.provider!r}")

    # (2) runtime model switch: assigning model_tag re-resolves immediately
    root.model_tag = "chat"
    print(f"(2) Runtime root.model_tag = 'chat' -> model={root.model.model!r}")

    # (3) declaration-layer difference: the subagent declares model_tag in .fya
    child = await root.create_subagent("@/agents/greeter")
    print(f"(3) Subagent greeter: declared model_tag={child.model_tag!r} "
          f"-> model={child.model.model!r}")
    await child.destroy()
    await runtime.shutdown()


asyncio.run(main())
