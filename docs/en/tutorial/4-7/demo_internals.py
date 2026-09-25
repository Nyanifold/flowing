"""Agent mechanics demo (4-7): custom _dequeue drain merge / watch linkage / side_query side channel.

Run: uv run python demo_internals.py
"""
import asyncio
import types

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ── ① Override _dequeue: drain merge (one batch consumes every message in the queue) ──
    print("== ① Custom _dequeue: drain merge ==")
    original = agent._dequeue

    async def drain_all(self):
        await self._message_queue.wait_not_empty()
        batch = await self._message_queue.drain_all()
        print(f"   [_dequeue] drain merge: batch of {len(batch)} messages into one turn")
        return batch

    agent._dequeue = types.MethodType(drain_all, agent)
    for text in ["First sentence", "Second sentence", "Third sentence"]:
        await agent.message(text)
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)
    # Note: restore does not recall a dequeue call already in flight — the work
    # loop is still blocked in the old method's wait_not_empty; the dequeue in
    # ② still takes the drain-merge path (batch of 1).

    # ── ② Turn finalization structure: TurnResult aggregation rules ──
    print("== ② Turn finalization: TurnResult construction ==")
    r = await agent.query("Introduce yourself in one sentence.")
    print(f"   status={r.status} turn.message_ids={r.turn.message_ids}")
    print(f"   token_usage aggregate={r.token_usage.total_tokens if r.token_usage else None} "
          f"(sum over TurnContext.usages field by field)")
    agent._dequeue = original   # restore default batch semantics (takes effect from the next dequeue)

    # ── ③ watch: assignment event linkage (fire-and-forget) ──
    print("== ③ watch attribute linkage ==")
    seen: list[tuple] = []
    agent.watch("locale", lambda new, old: seen.append((old, new)))
    agent.locale = "zh"      # first assignment (old=None)
    agent.locale = "en"      # overwrite
    await asyncio.sleep(0.5)   # watcher executes asynchronously
    print(f"   watch(locale) received assignment events: {seen}")

    # ── ④ side_query: the side channel bypasses the tree and persistence ──
    print("== ④ side_query side channel ==")
    before = len(agent._messages)
    side = await agent.side_query("Reply with only: ok")
    after = len(agent._messages)
    print(f"   side_query returned={side!r}; tree nodes {before} → {after} (unchanged = zero trace from the side channel)")
    await runtime.shutdown()


asyncio.run(main())
