"""Plugin mechanics demo (5-2): two-phase enablement / dependency declaration /
late install is invalid / cron real-time trigger.

Run: uv run python demo_plugins.py   (the cron part waits up to ~70 seconds)
"""
import asyncio

from flowing import Runtime, launch
from flowing.errors import (DependencyError, MissingProvideError,
                            ToolNotFoundError)
from flowing.message import MessageKind
from flowing.plugins import Plugin
from flowing.plugins.cron import schedule


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

    # ① Dependency declaration: a cycle raises DependencyError (raised at the install call site)
    print("== ① Dependency declaration: cycle fails fast ==")
    try:
        runtime.install(_P1(), _P2())
    except DependencyError:
        print("   p1 ↔ p2 depend on each other -> DependencyError")

    # ② Late install is invalid: mount first, install later -> at creation time use_skill has no registry to inject from
    print("== ② Late install (install after mount) -> creation-time failure ==")
    try:
        await launch(".", main_file="@/notes-late-main.py")
        print("   unexpected success (contradicts the contract)")
    except (ToolNotFoundError, MissingProvideError) as exc:
        print(f"   late-install launch failed: {type(exc).__name__}"
              f" (tool bodies/registries a plugin registers are phase-one products;"
              f" install must precede the first mount)")

    # ③ cron real-time trigger: schedule an every-minute job programmatically, wait for the EVENT delivery on schedule
    print("== ③ cron real-time trigger (programmatic schedule, wait up to 70s) ==")
    fired: list[str] = []

    async def on_fire(agent_, ctx):
        fired.append(ctx.content)
        return ctx
    agent.hooks.on_cron_trigger(on_fire, by="demo")

    schedule(agent, "*/1 * * * *", "[Reminder] Time to get up and move around.")   # module-level synchronous API
    print("   every-minute job scheduled, waiting for the trigger...")
    for _ in range(140):
        await asyncio.sleep(0.5)
        if fired:
            break
    for _ in range(40):   # The EVENT is enqueued after the hook and attaches to the tree with the turn; give it a moment to land
        await asyncio.sleep(0.5)
        events = [m for m in agent._messages.values()
                  if m.kind is MessageKind.EVENT and m.source in ("cron", "scheduled_task")]
        if events:
            break
    print(f"   on_cron_trigger fired {len(fired)} time(s); content={fired!r}")
    print(f"   EVENT messages in tree: {len(events)} (delivery source: 'cron' or custom)")

    # ④ Two-phase evidence: the body registered by install sits in the registry; undeclared means invisible to the LLM
    print("== ④ Stage-one artifacts check ==")
    print(f"   skill-load in global registry: "
          f"{agent.get_tool('skill-load') is not None}")
    print(f"   prompt block contains skills catalog: "
          f"{any(b.name == 'skills' for b in agent.prompt_blocks)}")
    await runtime.shutdown()


asyncio.run(main())
