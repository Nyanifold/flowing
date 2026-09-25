"""Crash simulation (4-1): os._exit(9) is equivalent to kill -9 — no shutdown().

The write-behind drain barrier never runs, yet committed records are not lost
on replay (crash window demo).
Run: uv run python demo_crash.py
"""
import asyncio
import os

from flowing import launch


async def main() -> None:
    runtime = await launch(".")            # recovery pipeline: replay tree/state logs
    agent = await runtime.get_agent("agent-main")
    result = await agent.query("Also remember a color: purple.")
    print(f"Turn finished status={result.status}; now simulating kill -9 (os._exit(9))", flush=True)
    os._exit(9)                            # no shutdown: the process dies instantly


asyncio.run(main())
