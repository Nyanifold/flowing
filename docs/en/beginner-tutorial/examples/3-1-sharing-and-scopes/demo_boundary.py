"""provide-inject boundary demo: an inject miss → MissingProvideError.

Run: uv run python demo_boundary.py
"""
import asyncio

from flowing import launch
from flowing.errors import MissingProvideError


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    try:
        agent.inject("no_such_key")
    except MissingProvideError as exc:
        print(f"inject miss → MissingProvideError: {exc}")
    await runtime.shutdown()


asyncio.run(main())
