"""Queue and loop mechanics experiments (3-1): steer / INTERRUPT / pause-resume / cancel.

Each experiment prints "mechanism behavior + traces on the tree".
Run: uv run python demo_mechanics.py
"""
import asyncio

from flowing import launch
from flowing.message import Message, MessageKind, MessagePriority, TextBlock


def tree_lines(agent, limit=40):
    rows = []
    for m in agent.chain.walk(agent.current_head_id):
        head = ""
        for b in m.content:
            if getattr(b, "text", ""):
                t = b.text.replace("\n", " ")
                head = t[:30]
                break
        rows.append(f"{m.id:>3} {m.kind.value:<9} pri={m.priority.name:<7} "
                    f"turn_end={m.turn_end} {head}")
    return rows[:limit]


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ── Experiment 1: steer is absorbed in-turn (no abort) ──
    print("== Experiment 1: steer (STEER visible in-turn, no abort) ==")
    t = asyncio.create_task(agent.query("Count from 1 to 5, one number per line."))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await agent.steer('Addendum: after the count, append the sentence "(steer received)".')
    r = await t
    print(f"turn status={r.status} (completed = unbroken)")
    await asyncio.sleep(0.3)   # let the steer message be appended with the batch

    # ── Experiment 2: INTERRUPT aborts the current turn during tool execution ──
    print("== Experiment 2: INTERRUPT (abort the current turn during tool execution) ==")
    t = asyncio.create_task(agent.query(
        "First use bash to run sleep 20, then answer: why is the sky blue? (one sentence)"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(8.0)   # first generation done, bash sleep 20 in flight
    await agent.enqueue_message(Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="Stop! First answer: what is 2+2?")],
        priority=MessagePriority.INTERRUPT))
    r = await t
    print(f"aborted turn status={r.status} aborted={r.turn.aborted} (cancelled = aborted)")
    while agent.current_turn is not None:   # wait for the INTERRUPT's new turn to finish
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)

    # ── Experiment 3: pause / resume ──
    print("== Experiment 3: pause / resume (suspend and resume the work loop) ==")
    agent.pause()
    t = asyncio.create_task(agent.query("Introduce yourself in one sentence."))
    await asyncio.sleep(1.5)
    # pause parks the work loop at the checkpoint: the batch is dequeued and
    # appended, but provider_gen is never issued
    print(f"during pause current_turn is not None: {agent.current_turn is not None}"
          f" (turn created, parked at the checkpoint, no LLM call)")
    agent.resume()
    r = await t
    print(f"after resume status={r.status}")

    # ── Experiment 4: cancel (cooperative: in-flight generation is raced down,
    #    already-produced output is kept) ──
    print("== Experiment 4: cancel (abort execution; interrupted streamed output "
          "is kept and persisted) ==")
    before = set(agent._messages)
    t = asyncio.create_task(agent.query("Tell a very, very long story."))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.0)
    await agent.cancel()
    r = await t
    new_nodes = [mid for mid in agent._messages if mid not in before]
    print(f"after cancel status={r.status}; nodes appended this turn: {len(new_nodes)}"
          f" (user + partial provider: an interrupted turn keeps what was produced)")

    # ── Cursor walk over the tree: the trace left by each mechanism above ──
    print("── message tree (head -> root, reverse chronological order) ──")
    for line in tree_lines(agent):
        print(line)
    await runtime.shutdown()


asyncio.run(main())
