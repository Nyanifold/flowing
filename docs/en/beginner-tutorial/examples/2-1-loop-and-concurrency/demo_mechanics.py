"""Queue-and-loop mechanics experiments: steer / INTERRUPT / pause-resume / cancel.

Each experiment prints "mechanics behavior + traces on the tree".
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

    # ── Experiment 1: a steer message is absorbed by the current turn (no interruption) ──
    print("== Experiment 1: steer (STEER visible to the current turn, no interruption) ==")
    t = asyncio.create_task(agent.query("Count from 1 to 5, one number per line."))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await agent.steer("Supplement: after the count, add one line saying \"(steer received)\".")
    r = await t
    print(f"Turn status={r.status} (completed = not interrupted)")
    await asyncio.sleep(0.3)   # wait for the steer message to be attached with the batch

    # ── Experiment 2: INTERRUPT aborts the current turn during tool execution ──
    print("== Experiment 2: INTERRUPT (interrupt the current turn during tool execution) ==")
    t = asyncio.create_task(agent.query(
        "First run sleep 20 with bash, then answer: why is the sky blue? (one sentence)"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(8.0)   # first generation finished, bash sleep 20 in flight
    await agent.enqueue_message(Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="Stop! First answer: what is 2+2?")],
        priority=MessagePriority.INTERRUPT))
    r = await t
    print(f"Interrupted turn status={r.status} aborted={r.turn.aborted} (cancelled = interrupted)")
    while agent.current_turn is not None:   # wait for the new INTERRUPT turn to end
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)

    # ── Experiment 3: pause / resume ──
    print("== Experiment 3: pause / resume (suspend and resume the work loop) ==")
    agent.pause()
    t = asyncio.create_task(agent.query("Introduce yourself in one sentence."))
    await asyncio.sleep(1.5)
    # pause suspends the work loop at a checkpoint: the batch is dequeued and on the tree, but provider_gen has not started
    print(f"During pause, current_turn is not None: {agent.current_turn is not None}"
          f" (turn created and stopped at the checkpoint, no LLM call)")
    agent.resume()
    r = await t
    print(f"Status after resume: {r.status}")

    # ── Experiment 4: cancel (cooperative cancellation: in-flight generation races to abort, produced output kept) ──
    print("== Experiment 4: cancel (abort execution; streamed output produced before interruption is persisted) ==")
    before = set(agent._messages)
    t = asyncio.create_task(agent.query("Tell a very, very long story."))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.0)
    await agent.cancel()
    r = await t
    new_nodes = [mid for mid in agent._messages if mid not in before]
    print(f"Status after cancel: {r.status}; nodes persisted by this turn: {len(new_nodes)}"
          f" (user + partial provider: an interruption does not lose produced output)")

    # ── Replay the tree cursor: traces of each mechanism above ──
    print("── message tree (head → root, reverse chronological) ──")
    for line in tree_lines(agent):
        print(line)
    await runtime.shutdown()


asyncio.run(main())
