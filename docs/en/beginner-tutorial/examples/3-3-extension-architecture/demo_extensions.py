"""Extension architecture demo: two-phase enablement / late install / dependency cycles / policy injection and unloading.

Run: uv run python demo_extensions.py
"""
import asyncio
import sys

from flowing import Runtime, launch
from flowing.errors import DependencyError, MissingProvideError, ToolNotFoundError
from flowing.plugins import Plugin
from flowing.tool import ToolCall

sys.path.insert(0, "composables")
from rate_limit import remove_rate_limit, use_rate_limit


class _P1(Plugin):
    name = "p1"
    dependencies = ("p2",)

    def install(self, runtime):
        pass


class _P2(Plugin):
    name = "p2"
    dependencies = ("p1",)

    def install(self, runtime):
        pass


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # (1) Two-phase enablement: phase 1 install registers the bodies, phase 2 use_skill in setup enables them
    print("== (1) Products of two-phase enablement ==")
    print(f"   skill-load in the global registry: "
          f"{agent.get_tool('skill-load') is not None}")
    print(f"   prompt block contains skills catalog: "
          f"{any(b.name == 'skills' for b in agent.prompt_blocks)}")

    # (2) Late install is invalid: mount first, install later -> creation-time failure
    print("== (2) Late install (install after mount) -> creation-time failure ==")
    try:
        await launch(".", main_file="@/notes-late-main.py")
        print("   unexpected success (violates the contract)")
    except (ToolNotFoundError, MissingProvideError) as exc:
        print(f"   late-install launch failed: {type(exc).__name__}"
              f" (the plugin's registered tool bodies belong to phase 1;"
              f" install must precede the first mount)")

    # (3) Dependency declaration: a cycle raises DependencyError at install time
    print("== (3) Dependency declaration: cycles fail fast ==")
    try:
        runtime.install(_P1(), _P2())
    except DependencyError:
        print("   p1 <-> p2 depend on each other -> DependencyError")

    # (4) Policy injection: self-written use_rate_limit (60s window, max 3 calls)
    print("== (4) use_rate_limit(max_calls=3) injection ==")
    limited: list[str] = []
    use_rate_limit(agent, max_calls=3, window_seconds=60.0)

    async def observe(agent_, tool_call):
        limited.append(tool_call.name)
        return tool_call
    agent.hooks.on_rate_limited(observe, by="demo")

    results = []
    for i in range(5):   # fire 5 calls in a row: first 3 allowed, last 2 blocked
        r = await agent.tool_call(ToolCall(id=f"c{i}", name="echo",
                                           args={"text": f"call {i}"}))
        results.append(r.status)
    print(f"   5 call results: {results}")
    print(f"   on_rate_limited observed {len(limited)} blocked calls (calls 4 and 5)")

    # (5) Unloadable: removing the whole by-group restores the original behavior
    print("== (5) remove_by_owner removes the whole group ==")
    removed = remove_rate_limit(agent)
    r = await agent.tool_call(ToolCall(id="c9", name="echo", args={"text": "resume"}))
    print(f"   removed {removed} handler(s); one more call: status={r.status}")

    await runtime.shutdown()


asyncio.run(main())
