"""Provider call-surface demo (3-3, offline structure + a real model):
streaming vs non-streaming.

Run: uv run python demo_stream.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    deltas: list = []

    async def collect(agent_, d):
        deltas.append(d)
        usage = "usage✓" if d.usage else "-"
        print(f"  delta#{len(deltas):<3} kind={d.kind:<8} by={d.by:<6} "
              f"{usage:<6} text={d.text[:16]!r}")
        return d

    # Collect only main-turn deltas (match_on="by" filter; the side channel
    # is "_side")
    agent.hooks.on_provider_delta["_turn"](collect, by="diag")

    print("== streaming provider_gen(stream=True): deltas arrive piece by piece ==")
    ctx = agent._assemble_context()
    resp = await agent.provider_gen(ctx, stream=True, by="_turn")
    print(f"  → {len(deltas)} deltas total; finish={resp.finish}; "
          f"message.usage attached={'yes' if resp.message.usage else 'no'}")

    deltas.clear()
    print("== non-streaming provider_gen(stream=False): one synthesized full delta ==")
    resp = await agent.provider_gen(ctx, stream=False, by="_turn")
    print(f"  → {len(deltas)} delta total; finish={resp.finish}; "
          f"message.usage attached={'yes' if resp.message.usage else 'no'}")
    await runtime.shutdown()


asyncio.run(main())
