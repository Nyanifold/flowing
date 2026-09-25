"""Tool execution mechanics demo (4-5): the full pipeline of the async
generator form, one of the three background forms.

Calls build programmatically (equivalent to an in-turn LLM call) and
observes: receipt (pending) → segments → completion summary; each
production passes through on_tool_yields; the background results are
enqueued as EVENT messages (triggering follow-up turns).
Run: uv run python demo_background.py
"""
import asyncio

from flowing import launch
from flowing.message import MessageKind
from flowing.tool import ToolCall


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    yields_log: list[tuple] = []

    async def watch(agent_, result):
        yields_log.append((result.production, result.name,
                           type(result.output).__name__))
        return result
    agent.hooks.on_tool_yields(watch, by="diag")

    receipt = await agent.tool_call(ToolCall(id="call-b1", name="build",
                                             args={"target": "demo"}))
    print(f"① First yield → receipt: status={receipt.status} "
          f"background_task_id={receipt.background_task_id}")
    print(f"   Turn not blocked: tool_call has returned, the background "
          f"task keeps running")

    # Wait until all background productions are delivered (EVENT messages
    # enqueued and consumed)
    for _ in range(100):
        await asyncio.sleep(0.3)
        done = [y for y in yields_log if y[0] == "segment"]
        if len(done) >= 4:
            break
    print("② on_tool_yields per production (production metadata):")
    for production, name, kind in yields_log:
        print(f"   {production:<9} name={name:<6} output={kind}")

    # Wait until the EVENT result messages are in the tree (the new turn
    # triggered by background delivery)
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.5)
    events = [m for m in agent._messages.values()
              if m.kind is MessageKind.EVENT]
    print(f"③ Background result delivery: {len(events)} EVENT messages "
          f"enqueued into the tree")
    for m in events:
        head = next((b.text[:30] for b in m.content
                     if getattr(b, "text", "")), "")
        print(f"   source={m.source!r} async tool build: {head}")
    await runtime.shutdown()


asyncio.run(main())
