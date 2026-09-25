"""Side-by-side behavior of the three entries: query / message / steer.

Run: uv run python demo_entries.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ── 1. message(): fire-and-forget — only the message id comes back, no waiting for the turn ──
    mid = await agent.message(
        "Use glob to look at the notes directory (use the absolute path from "
        "the system prompt), then say how many files are in it.")
    print(f"message() enqueued, message id={mid} (the caller does not wait for the turn result)")
    while agent.current_turn is None:   # wait for the turn to start
        await asyncio.sleep(0.2)
    print("Turn has started (current_turn is not None)")

    # ── 2. steer(): steer while the turn is running — STEER is visible to the current turn, without interrupting it ──
    await agent.steer("Additional requirement: end your answer with '(steer received)'.")
    print("steer() delivered (STEER priority: visible to the current turn, no interruption)")
    while agent.current_turn is not None:   # wait for this turn to end
        await asyncio.sleep(0.2)
    print("Turn has ended")

    # ── 3. query(): wait for the TurnResult of the turn that contains this message ──
    result = await agent.query(
        "Use glob again to confirm how many files are in the notes directory; "
        "answer in one sentence.")
    print(f"query() waited for TurnResult: status={result.status}")
    print(f"first 80 chars of final_text: {result.final_text[:80]}")

    # ── 4. Replay the tree cursor: see the traces the three entries left on the tree ──
    print("── message tree (head → root, reverse chronological; pri=STEER marks a steer message) ──")
    for m in agent.chain.walk(agent.current_head_id):
        head = ""
        for b in m.content:
            if getattr(b, "text", ""):
                t = b.text.replace("\n", " ")
                head = t if len(t) <= 62 else f"{t[:36]}…{t[-22:]}"
                break
        print(f"{m.id:>3} {m.kind.value:<9} pri={m.priority.name:<6} "
              f"turn_end={m.turn_end} {head}")

    await runtime.shutdown()


asyncio.run(main())
