"""Crash simulation: os._exit(9) is equivalent to kill -9 — it bypasses shutdown().

The write-behind drain barrier never runs, but already-submitted records are not
lost and are replayed on the next start (crash-window demonstration).
Run: uv run python demo_crash.py
"""
import asyncio
import os

from flowing import launch


async def main() -> None:
    runtime = await launch(".")            # recovery pipeline: replay the tree/state logs
    agent = await runtime.get_agent("agent-main")
    result = await agent.query("Also remember a color: purple.")
    print(f"Turn completed status={result.status}; now simulating kill -9 (os._exit(9))", flush=True)
    os._exit(9)                            # no shutdown: the process dies immediately


asyncio.run(main())
