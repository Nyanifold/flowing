# 4-3 · Tree Surgery and Recovery Invariants

## Prerequisites

[Chapter 3-1 Message Tree and Loop Mechanics](3-1-message-tree-and-loop.md)
(the mechanics of the tree and the cursor) and [Chapter 4-1 Core State and
Message Persistence](4-1-core-state-and-message-persistence.md) (recovery =
replay). Both demonstrations are offline: messages are constructed by hand,
without an LLM. The complete scripts and configuration are included below.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| five ops | The minimal complete write-surgery set of `MessageChain`: `insert` / `branch` / `remove` / `update` / `reparent` (plus the read-only `get` / `walk`) |
| merge semantics | Behavior of `insert` at a fork point: existing direct children are re-hung under the new node |
| adjacency preservation | Behavior of `remove`: the deleted message's direct children are automatically re-hung to its parent, keeping the chain continuous |
| `turn_end` | Boundary marker closing a logical turn (written at the agent layer; layered separately from the provider layer's `finish`) |
| `partial` | Streaming-interruption marker: accumulated content is kept and persisted, not discarded |
| `synthetic` | Recovery-synthesis placeholder marker: a deterministic-id TOOL message synthesized for an orphaned tool_call — "not a real artifact" |

## Goals

Manual editing of the message-level tree — deferred by Chapter 3-1 — plus
the recovery invariants: history can fork and can be corrected, and recovery
always yields a paired, continuous context.

## Main text

### fork = moving the cursor

`await agent.fork(msg_id)` only moves `current_head_id` and **never creates
a node** — the same history can hold multiple branch views at once (the fork
exploration in demo_surgery.py). Three caveats apply to forking within a
turn; see Chapter 4-7 for the timing details: fork only affects where newly
arriving messages are attached and never alters what is already in the tree;
the context jumps with the cursor (the next turn sees the new path
immediately); the fork landing point must not cross an unclosed
call–result segment — crossing one produces a permanently broken branch
(see below).

**Broken example (a broken branch with broken pairing)**: fork back to a
PROVIDER message that carries a tool_call and continue writing from there,
or fork such that a tool result ends up on a different branch — the call in
the shared prefix is then unpaired on the new branch, and assembly raises
`UnpairedToolCallError`; the recovery pipeline does not self-heal (synthesized
closure decides pairing globally: if the result exists on the old branch, no
placeholder is synthesized), and the broken branch stays broken permanently.
Safe landing points: fork onto a user message, or to a spot above a provider
message — never cutting the segment between a tool_call and its result.

### Five-op semantics quick reference

| op | Semantics | Common pitfall |
|---|---|---|
| `insert(after_id, msg)` | Insert after `after_id` | **Fork points merge**: existing direct children are re-hung under the new node; use `branch` to add a parallel branch instead |
| `branch(parent_id, msg)` | Attach a parallel branch; `None` = open a new root | The forest model's only entry point for persisting a new root (used by compaction chain swaps) |
| `remove(msg_id)` | Delete a single message | **Adjacency preservation**: direct children are re-hung to the parent; subtrees do not cascade |
| `update(msg_id, content)` | Change content only | Does not touch the chain or the kind |
| `reparent(msg_id, to=...)` | Reconnect a whole subtree | Cycle validation |

Each op = modify the in-memory authoritative chain + append one change row
(tombstone / move / update); the physical rewrite is deferred to the
compaction pass (Chapter 4-1). **Chain ops do not move the head
automatically** — after attaching a message, call `fork(new_id)` to advance
the cursor (the `grow` helper in the demo is exactly this pattern).

### The three markers

- `turn_end`: when a PROVIDER message carrying it is persisted, the turn
  closes — recovery locates complete turn boundaries by it; it is layered
  separately from the provider layer's `ProviderResponse.finish` (an
  interrupted stream has no finish, yet the turn still closes);
- `partial`: content already produced by an interrupted stream is **kept
  and persisted** (demonstrated by Experiment 4 of Chapter 3-1);
- `synthetic`: when recovery finds an orphaned tool_call (missing result),
  it synthesizes a placeholder TOOL message with a deterministic id
  (`synthetic-<call_id>`) and **persists** it to close the pairing — the
  tree is always paired; adapters neither need nor should patch this
  themselves; assembly only asserts on unpaired messages.

### Recovery invariants

1. **A half-finished turn is not truncated**: messages persisted after the
   last `turn_end=True` still enter the context as usual (demonstrated by
   the crash-recovery case in Chapter 4-1);
2. **tool_call / result are strictly paired (closure inside the tree)**:
   an orphaned call is closed by a persisted synthetic placeholder
   (demonstrated by demo_synthetic.py: placeholder id `synthetic-call_7`,
   `tool_status="error"`, body text stating the result is missing and the
   message was synthesized on restore — "not a real artifact"); a
   cancellation during execution is closed with `tool_status="cancelled"`
   persisted; assembly only asserts on unpaired messages.

## Out of scope

- The persistence mechanics of core state and messages (change rows /
  write-behind) — Chapter 4-1;
- The policy side of compaction chain swaps (`use_compact` /
  `use_auto_compact`) — Chapter 5-3;
- Torn last rows / tombstone compaction timing — Chapter 6-3 as needed.

## Main example

**Demo 1: fork exploration + five-op correction** (`demo_surgery.py`; the
complete recorded console output is included below):

```console
$ uv run python demo_surgery.py
── Initial linear chain（head → root, reverse chronological）──
  4 provider  parent=3 Switch to the south road.
  3 user      parent=2 The north road is blocked.
  2 provider  parent=1 Take the north road first.
  1 user      parent=None How do I get to Route A?

── After forking the parallel branch（head → root, reverse chronological）──
  6 provider  parent=5 Shorter, but waterlogged.
  5 user      parent=1 What about the south road?
  1 user      parent=None How do I get to Route A?

── Back at a: the fork point before insert（head → root, reverse chronological）──
  1 user      parent=None How do I get to Route A?

after insert: g=7, b.parent=7, e.parent=7 (both re-hung under g = merge semantics)
── Fork-point structure after insert（head → root, reverse chronological）──
  1 user      parent=None How do I get to Route A?

e's text after update: 'What about the south road? (Note: bring an umbrella)'
after remove(d): d is gone (chain.get raises KeyError); its child's parent=3 (re-hung to d's parent c)
```

Reading this transcript: after `fork(a)`, the head sits on message 6 (the
south-road exploration), and the walked view is 6 → 5 → 1 — message 5 is a
sibling of message 2 under the shared node a(1), while the north-road chain
(2 → 3 → 4) stays fully preserved in the tree. `insert(a, g)` re-hung the
existing direct children b(2) and e(5) under the new node g(7) (merge
semantics); `update` changed only the content of e; `remove("4")` deleted d
(`chain.get("4")` now raises `KeyError`) and re-hung d's direct child to
d's parent c(3) (adjacency preservation).

**Demo 2: synthetic self-healing of an orphaned tool_call**
(`demo_synthetic.py`: mount once for real → overwrite with a torn
`tree.jsonl` (provider carrying an orphan `tool_call`, result missing) →
run the recovery pipeline; the complete recorded console output is included
below):

```console
$ uv run python demo_synthetic.py
Torn session constructed: provider(id=2) carries an orphan tool_call(call_7); no result message.
All messages after recovery (id ascending):
  synthetic-call_7 tool      synthetic=True tool_status=error tool call call_7 result
                 1 user      synthetic=False tool_status=None List the directory
                 2 provider  synthetic=False tool_status=None
```

Reading this transcript: recovery replay finds that `call_7` has no paired
result, so it synthesizes the placeholder `synthetic-call_7` (a
deterministic id — identical on every replay) and **persists** it to close
the pairing — the tree is always paired, and downstream adapters never
handle orphans.

### Complete runnable materials

Save each block under the displayed filename in one working directory. These
are the complete files used by both offline demonstrations. The provider key
is an environment-variable placeholder; neither demo makes a provider call.

`main.py`:

```python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

`root.fya`:

```yaml
description: "Tree surgery demo assistant: minimal Q&A."
model_tag: default
---
$system_prompt:
You are a concise English assistant.
```

`providers.yaml`:

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

`models.yaml`:

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

`model-tags.yaml`:

```yaml
tags:
  default: deepseek-flash
```

`demo_surgery.py`:

```python
import asyncio

from flowing import launch
from flowing.message import Message, MessageKind, TextBlock


def m(kind: MessageKind, text: str) -> Message:
    return Message(kind=kind, content=[TextBlock(text=text)])


def show(agent, label: str) -> None:
    print(f"── {label} (head → root, reverse chronological) ──")
    for message in agent.chain.walk(agent.current_head_id):
        head = next((block.text[:26] for block in message.content
                     if getattr(block, "text", "")), "")
        print(f"{message.id:>3} {message.kind.value:<9} "
              f"parent={message.parent_id} {head}")
    print()


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    async def grow(parent, message):
        new_id = agent.chain.branch(parent, message)
        await agent.fork(new_id)
        return new_id

    a = await grow(None, m(MessageKind.USER, "How do I get to Route A?"))
    b = await grow(a, m(MessageKind.PROVIDER, "Take the north road first."))
    c = await grow(b, m(MessageKind.USER, "The north road is blocked."))
    await grow(c, m(MessageKind.PROVIDER, "Switch to the south road."))
    show(agent, "Initial linear chain")

    await agent.fork(a)
    e = await grow(a, m(MessageKind.USER, "What about the south road?"))
    await grow(e, m(MessageKind.PROVIDER, "Shorter, but waterlogged."))
    show(agent, "After forking the parallel branch")

    await agent.fork(a)
    show(agent, "Back at a: the fork point before insert")

    g = agent.chain.insert(a, m(MessageKind.SYSTEM, "Context: rain today."))
    print(f"after insert: g={g}, b.parent={agent.chain.get(b).parent_id}, "
          f"e.parent={agent.chain.get(e).parent_id} "
          "(both re-hung under g = merge semantics)")
    show(agent, "Fork-point structure after insert")

    agent.chain.update(
        e, [TextBlock(text="What about the south road? (Note: bring an umbrella)")])
    print(f"e's text after update: {agent.chain.get(e).content[0].text!r}")

    d = agent.chain.get("4")
    child_of_d = agent.chain.branch(
        "4", m(MessageKind.USER, "How is the south road's reputation?"))
    agent.chain.remove("4")
    print(f"after remove(d): d is gone (chain.get raises KeyError); "
          f"its child's parent={agent.chain.get(child_of_d).parent_id} "
          "(re-hung to d's parent c)")
    await runtime.shutdown()


asyncio.run(main())
```

`demo_synthetic.py`:

```python
import asyncio
import json
import pathlib

from flowing import launch
from flowing.message import (Message, MessageKind, TextBlock, ToolCallBlock,
                             to_record)

SESSION = pathlib.Path(".flowing/agent-main")


def build_torn_session() -> None:
    SESSION.mkdir(parents=True, exist_ok=True)
    (SESSION / "meta.json").write_text(json.dumps({
        "agent_type": "@/root.fya",
        "parent_agent_id": "runtime-0",
        "created_at": "2026-09-18T00:00:00+00:00",
        "args": {},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    user = Message(kind=MessageKind.USER,
                   content=[TextBlock(text="List the directory")])
    user.id, user.parent_id = "1", None
    orphan = Message(
        kind=MessageKind.PROVIDER,
        content=[ToolCallBlock(id="call_7", name="bash",
                               args={"command": "ls"})])
    orphan.id, orphan.parent_id = "2", "1"
    orphan.turn_end = True
    rows = [{"type": "meta", "format_version": 1},
            to_record(user), to_record(orphan)]
    (SESSION / "tree.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8")


async def main() -> None:
    runtime = await launch(".")
    await runtime.shutdown()
    build_torn_session()
    print("Torn session constructed: provider(id=2) carries an orphan "
          "tool_call(call_7); no result message.")

    runtime = await launch(".", resume="agent-main")
    agent = await runtime.get_agent("agent-main")
    print("All messages after recovery (id ascending):")
    for mid in sorted(agent._messages,
                      key=lambda key: int(key) if key.isdigit() else -1):
        message = agent._messages[mid]
        head = next((block.text[:24] for block in message.content
                     if getattr(block, "text", "")), "")
        print(f"{message.id:>18} {message.kind.value:<9} "
              f"synthetic={message.synthetic} "
              f"tool_status={message.tool_status} {head}")
    await runtime.shutdown()


asyncio.run(main())
```

Run either demonstration with `uv run python demo_surgery.py` or
`uv run python demo_synthetic.py`. The recorded outputs are reproduced in
the console blocks above.

## Summary

1. fork only moves the cursor; branch exploration costs nothing and the old
   chain is fully preserved;
2. The five ops are minimal and complete: insert merges, branch goes
   parallel, remove preserves adjacency, update changes content only,
   reparent reconnects a subtree (with cycle validation);
3. The three markers `turn_end` / `partial` / `synthetic` govern turn
   boundaries, streaming retention, and recovery placeholders respectively;
4. Recovery invariants: a half-finished turn is not truncated, and
   tool_calls are always paired (synthetic self-healing).
