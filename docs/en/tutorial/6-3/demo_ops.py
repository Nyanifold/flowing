"""Operations demo (6-3): configuration chain / source-path override / pool and archive / snapshot audit.

Run: uv run python demo_ops.py
"""
import asyncio
import os

from flowing import launch


async def main() -> None:
    # ① Configuration chain: framework default (60) < project config.yaml (45) < set_config (90)
    print("== ① Configuration chain: three layers ==")
    runtime = await launch(".")
    print(f"   project-level @/config.yaml overrides the framework default: agent.timeout = "
          f"{runtime.get_config('agent.timeout')} (framework default 60)")
    runtime.set_config("agent.timeout", 90)
    print(f"   the set_config overlay wins: agent.timeout = "
          f"{runtime.get_config('agent.timeout')}")
    print(f"   FLOWING_CONFIG_HOME user-level: "
          f"{os.environ.get('FLOWING_CONFIG_HOME', '~/.flowing (not set on this machine)')}")

    # ② Model source-path precedence: set_providers overrides programmatically (called before mount)
    print("== ② Model integration source override ==")
    print(f"   default provider candidates: "
          f"{sorted(runtime.provider_registry._candidates)}")
    print("   precedence: FLOWING_PROVIDERS_PATH environment variable > default path; "
          "set_providers overrides programmatically before mount")
    print("   (this project's main() already calls set_providers('@/providers.yaml'); "
          "switching to alt-providers.yaml yields the deepseek-alt entry — see the example code in the chapter)")

    # ③ Agent pool and the three tiers of forgetting
    print("== ③ Pool / archive (two of the three tiers of forgetting) ==")
    root = await runtime.get_agent("agent-main")
    child = await root.create_subagent("@/agents/explore-agent" if False else
                                       "builtin::explore-agent", name="exp-1")
    pool = runtime.snapshot().agents if hasattr(runtime.snapshot(), "agents") else None
    print(f"   pool registry (including child agents): {sorted(runtime._agent_pool)}")
    await runtime.archive_agent(child.node_id)
    print(f"   get_agent after archive_agent → "
          f"{await runtime.get_agent(child.node_id)} (forgotten by the runtime, files kept on disk)")
    print(f"   destroy tier (drop instance, keep records) → archive tier (remove from registry); "
          f"physical deletion is not provided by the framework, do it in the application layer")

    # ④ Snapshot audit
    print("== ④ Snapshot audit ==")
    snap = runtime.snapshot()
    print(f"   nodes={sorted(snap.nodes)}")
    print(f"   config_overrides={snap.config_overrides}")
    print("   crash drill (kill -9 → restart recovery) is covered in 4-1; "
          "the log-persistence plugin lives in flowing._unstable.logging (format not frozen, do not depend on it)")
    await runtime.shutdown()


asyncio.run(main())
