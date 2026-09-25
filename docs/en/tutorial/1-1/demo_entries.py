"""Three-entry comparison demo (main example for 1-1): query / message / steer.

Run: uv run python demo_entries.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ── 1. message(): fire-and-forget — only the message id comes back,
    #      the caller does not wait for the turn ──
    mid = await agent.message(
        "Use glob to inspect the notes directory (use the absolute path from "
        "the system prompt) and say how many files it contains.")
    print(f"message() enqueued, message id={mid} (caller does not wait for the turn result)")
    while agent.current_turn is None:   # wait for the turn to start
        await asyncio.sleep(0.2)
    print("turn started (current_turn is not None)")

    # ── 2. steer(): steer a running turn — STEER is visible to the current
    #      turn's context and does not interrupt it ──
    await agent.steer("Additional requirement: append '(steer received)' at the end of your answer.")
    print("steer() delivered (STEER priority: visible this turn, no interruption)")
    while agent.current_turn is not None:   # wait for this turn to end
        await asyncio.sleep(0.2)
    print("turn ended")

    # ── 3. query(): wait for the TurnResult of the turn that contains my message ──
    result = await agent.query("Use glob again to confirm how many files the notes directory has; answer in one sentence.")
    print(f"query() got TurnResult: status={result.status}")
    print(f"final_text (first 80 chars): {result.final_text[:80]}")

    # ── 4. Tree cursor replay: see what trace each of the three entries left ──
    print("── Message tree (head → root, reverse chronological order; pri=STEER marks a steer message) ──")
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
