"""Tree surgery demo (4-3, offline): fork branch exploration + five-op history correction.

All messages are constructed by hand (no LLM involved); the surgery traces
are replayed via walk.
Run: uv run python demo_surgery.py
"""
import asyncio

from flowing import launch
from flowing.message import Message, MessageKind, TextBlock


def m(kind: MessageKind, text: str) -> Message:
    return Message(kind=kind, content=[TextBlock(text=text)])


def show(agent, label: str) -> None:
    print(f"── {label}（head → root, reverse chronological）──")
    for x in agent.chain.walk(agent.current_head_id):
        head = next((b.text[:26] for b in x.content if getattr(b, "text", "")), "")
        print(f"{x.id:>3} {x.kind.value:<9} parent={x.parent_id} {head}")
    print()


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    async def grow(parent, message):
        """branch to attach + fork to advance the cursor (chain ops do not move the head)."""
        new_id = agent.chain.branch(parent, message)
        await agent.fork(new_id)
        return new_id

    # Build history: 1 user -> 2 provider -> 3 user -> 4 provider (linear chain)
    a = await grow(None, m(MessageKind.USER, "How do I get to Route A?"))
    b = await grow(a, m(MessageKind.PROVIDER, "Take the north road first."))
    c = await grow(b, m(MessageKind.USER, "The north road is blocked."))
    await grow(c, m(MessageKind.PROVIDER, "Switch to the south road."))
    show(agent, "Initial linear chain")

    # fork = move the cursor (no node created): back to the shared node a
    # and open a parallel exploration branch
    await agent.fork(a)
    e = await grow(a, m(MessageKind.USER, "What about the south road?"))
    await grow(e, m(MessageKind.PROVIDER, "Shorter, but waterlogged."))
    show(agent, "After forking the parallel branch")

    # Back to the shared-root view
    await agent.fork(a)
    show(agent, "Back at a: the fork point before insert")

    # insert merge semantics: inserting under fork point a -- a's existing
    # direct children (b and e) are re-hung under the new node
    g = agent.chain.insert(a, m(MessageKind.SYSTEM, "Context: rain today."))
    print(f"after insert: g={g}, b.parent={agent.chain.get(b).parent_id}, "
          f"e.parent={agent.chain.get(e).parent_id} (both re-hung under g = merge semantics)")
    show(agent, "Fork-point structure after insert")

    # update: content only, the chain is untouched
    agent.chain.update(e, [TextBlock(text="What about the south road? (Note: bring an umbrella)")])
    print(f"e's text after update: {agent.chain.get(e).content[0].text!r}")

    # remove adjacency preservation: attach a child under d (id 4), then
    # remove d -- the direct child is re-hung to d's parent
    d = agent.chain.get("4")
    child_of_d = agent.chain.branch("4", m(MessageKind.USER, "How is the south road's reputation?"))
    agent.chain.remove("4")
    print(f"after remove(d): d is gone (chain.get raises KeyError); "
          f"its child's parent={agent.chain.get(child_of_d).parent_id} (re-hung to d's parent c)")
    await runtime.shutdown()


asyncio.run(main())
