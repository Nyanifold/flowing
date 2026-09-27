"""Recovery invariants demo (4-3, offline): synthetic placeholder synthesis for an orphaned tool_call.

Construct a "torn" tree by hand (a provider carrying a tool_call whose
result is missing); after replay through the recovery pipeline, the pairing
anchor is closed by a synthesized placeholder.
Run: uv run python demo_synthetic.py
"""
import asyncio
import json
import pathlib

from flowing import launch
from flowing.message import (Message, MessageKind, TextBlock, ToolCallBlock,
                             to_record)

SESSION = pathlib.Path(".flowing/agent-main")


def build_torn_session() -> None:
    """Write a torn session: user + a provider carrying an orphaned tool_call."""
    SESSION.mkdir(parents=True, exist_ok=True)
    (SESSION / "meta.json").write_text(json.dumps({
        "agent_type": "@/root.fya",
        "parent_agent_id": "runtime-0",
        "created_at": "2026-09-18T00:00:00+00:00",
        "args": {},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    user = Message(kind=MessageKind.USER, content=[TextBlock(text="List the directory")])
    user.id, user.parent_id = "1", None
    orphan = Message(
        kind=MessageKind.PROVIDER,
        content=[ToolCallBlock(id="call_7", name="bash", args={"command": "ls"})])
    orphan.id, orphan.parent_id = "2", "1"
    orphan.turn_end = True   # the turn closed but the result is missing = torn shape
    rows = [{"type": "meta", "format_version": 1},
            to_record(user), to_record(orphan)]
    (SESSION / "tree.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")


async def main() -> None:
    # (1) Mount once for real: the registry (core.jsonl) and meta are in
    #     place -- equivalent to this Agent having actually existed
    #     (hand-writing the registry is an internal format and not the way)
    runtime = await launch(".")
    await runtime.shutdown()
    # (2) Overwrite with a torn tree.jsonl: provider(id=2) carries an
    #     orphan tool_call and no result message
    build_torn_session()
    print("Torn session constructed: provider(id=2) carries an orphan tool_call(call_7); no result message.")
    # (3) Replay through the recovery pipeline (idempotent mount with a fixed
    #     agent_id — the second launch goes through recovery)
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    print("All messages after recovery (id ascending):")
    for mid in sorted(agent._messages, key=lambda k: int(k) if k.isdigit() else -1):
        x = agent._messages[mid]
        head = next((b.text[:24] for b in x.content if getattr(b, "text", "")), "")
        print(f"{x.id:>18} {x.kind.value:<9} synthetic={x.synthetic} "
              f"tool_status={x.tool_status} {head}")
    await runtime.shutdown()


asyncio.run(main())
