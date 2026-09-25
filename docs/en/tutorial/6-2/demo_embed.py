"""Minimal skeleton of an embedding host (6-2): the host holds the Runtime,
subscribes hooks, gates approvals, and owns shutdown responsibility.

No web framework is involved — a FastAPI / WebSocket host just wires these
hooks into its own protocol.
Run: uv run python demo_embed.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    # Embedding = the host holds the Runtime returned by launch
    # (a thin main() may contain no interaction logic at all)
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # 1) Config integration: the set_config override layer wins
    #    (the config chain is covered in 6-3)
    runtime.set_config("agent.timeout", 90)
    print(f"1) host set_config override: agent.timeout = {runtime.get_config('agent.timeout')}")

    # 2) Output goes through hook subscription (minimal streaming to screen)
    async def on_delta(agent_, d):
        if d.kind == "text" and d.by == "_turn":
            print(d.text, end="", flush=True)
        return d
    agent.hooks.on_provider_delta["_turn"](on_delta, by="host-ui")

    # 3) Approval goes through before_tool_call (host confirmation policy,
    #    can be wired to any human-confirmation channel)
    async def approve(agent_, tool_call):
        print(f"\n[host approval] {tool_call.name} args={tool_call.args}")
        await asyncio.sleep(0.2)          # simulate the latency of human confirmation
        print("[host approval] approved")
        return tool_call
    agent.hooks.before_tool_call(approve, by="host-approval")

    # 4) The host drives the conversation
    r = await agent.query("Use bash to run `echo hello from host` and tell me the output verbatim.")
    print(f"\n[host] turn status={r.status}")

    # 5) Shutdown responsibility moves to the host: write-behind needs to be
    #    drained; exiting the process without shutdown() loses the tail records
    await runtime.shutdown()
    print("[host] shutdown complete: tail records drained")


asyncio.run(main())
