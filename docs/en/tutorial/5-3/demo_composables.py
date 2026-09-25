"""Composable mechanics demo (5-3): self-written use_rate_limit end to end + built-in Composable comparison.

Run: uv run python demo_composables.py
"""
import asyncio
import sys

from flowing import launch
from flowing.tool import ToolCall

sys.path.insert(0, "composables")
from rate_limit import remove_rate_limit, use_rate_limit


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ① inject the self-written Composable: 60s window, max 3 calls
    print("== ① use_rate_limit(max_calls=3) injection ==")
    limited: list[str] = []
    use_rate_limit(agent, max_calls=3, window_seconds=60.0)

    async def observe(agent_, tool_call):
        limited.append(tool_call.name)
        return tool_call
    agent.hooks.on_rate_limited(observe, by="demo")   # observe the declared extension hook point

    results = []
    for i in range(5):   # fire 5 calls: first 3 pass, last 2 blocked
        r = await agent.tool_call(ToolCall(id=f"c{i}", name="echo",
                                           args={"text": f"shot {i}"}))
        results.append(r.status)
    print(f"   5 call results: {results}")
    print(f"   on_rate_limited observed {len(limited)} over-limit calls (the 4th and 5th)")

    # ② by-group management: after removing the whole group, calls pass again
    print("== ② remove_by_owner group removal ==")
    removed = remove_rate_limit(agent)
    r = await agent.tool_call(ToolCall(id="c9", name="echo", args={"text": "recovered"}))
    print(f"   removed {removed} handler(s); one more call: status={r.status}")

    # ③ call a built-in Composable (use_system_reminder is injected in one line)
    print("== ③ call a built-in Composable ==")
    from flowing.composables import use_system_reminder
    use_system_reminder(agent, contents=["[reminder] from use_system_reminder"])
    hits = [h for h in agent.hooks.before_turn
            if getattr(h, "by", None) == "system-reminder"]
    print(f"   after use_system_reminder, before_turn matches {len(hits)} handler(s)"
          f" (one way this built-in Composable mounts behavior)")
    await runtime.shutdown()


asyncio.run(main())
