# 3-1 · Message Tree and Loop Mechanics

## Prerequisites

[1-1 Turn and Loop](1-1-turn-and-loop.md) (concept layer: message-driven turns, the three entry points) and
[1-2 Message Model](1-2-message-model.md) (message fields and kind). The complete configuration, prompt, program, inputs, and a recorded output are included below; no other example material is needed.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| message-level tree | A forest of messages chained by `id` + `parent_id`: `parent_id=None` marks a root, and one tree may have multiple roots |
| cursor (current_head_id) | The position pointer for "where the next message attaches": a new message links to it and then advances it; fork = switching the cursor (switches the view only, creates no node; surgery is Chapter 4-3) |
| consumption batch | The list of messages dequeued and consumed by one logical Turn; the default batch = the leading INTERRUPT/STEER run + the first non-urgent message after it |
| checkpoint | Fixed decision points in the turn's inner loop (pause gate, urgent absorption, abort decision); control mechanisms close at checkpoints instead of hard-switching mid-execution |
| urgent absorption | Checkpoint-time handling of urgent messages: if the queue head holds an INTERRUPT, drain INTERRUPT+STEER onto the tree and abort; if only STEER, drain onto the tree without aborting |

## Goals

Master the mechanics of the message tree (pure in-memory view) and queue scheduling: which stage of the loop each of steer / INTERRUPT / pause / cancel acts on, and what traces each leaves on the tree.

## Main text

### Tree (in-memory view)

Each agent holds a message-level tree (a forest): nodes are messages, and the parent chain is formed by `parent_id`; `current_head_id` is the cursor. Context assembly walks up from the cursor to collect the path (3-2); `fork(msg_id)` only switches the cursor and never creates a node — the same history can carry multiple branch views at once. **Manually editing the tree (the five-op surgery) is Chapter 4-3's topic and is not detailed here.**

### Queue and scheduling

Five priority levels: `INTERRUPT > STEER > HIGH > NORMAL > LOW` (the smaller the number, the higher the priority; FIFO within the same priority). PROVIDER messages never enter the queue (they are produced inside a turn); TOOL results attach to the tree directly within the turn. The default dequeue batch takes the leading INTERRUPT/STEER run at the queue head plus the first non-urgent message after it — so a normal message right behind an INTERRUPT rides its coattails into the same turn. `_dequeue` can be overridden (drain merging, grouping by source, and other scheduling policies); Chapter 4-7 gives the implementer's view.

### Four controls on the loop (checkpoint semantics)

Fixed checkpoints of the turn's inner loop: **pause gate → urgent absorption → abort decision → provider_gen → tool batch**. All control mechanisms close at checkpoints:

| Mechanism | Where it acts | Traces on the tree |
|---|---|---|
| `steer()` | Checkpoint drains STEER onto the tree, **no abort** | The STEER message enters the current turn's chain and is visible to the next provider_gen's context in the same turn |
| INTERRUPT message | Checkpoint drains INTERRUPT+STEER onto the tree and **aborts**: the current turn ends with `cancelled` | The INTERRUPT message is on the tree and visible to the next turn's context |
| `pause()` / `resume()` | Pause gate: the dequeued batch attaches as usual, but provider_gen is not issued | No new nodes (no LLM calls while suspended) |
| `cancel()` | Races with `_turn_abort` and the in-flight provider_gen; takes effect immediately | Already-started streamed output is kept as a partial message on disk |

Two precise semantics (proven by the main example):

- **INTERRUPT does not hard-switch in-flight execution**: the in-flight generation or tool call runs to completion, and the turn closes with an abort at the checkpoint "before the next provider_gen"; if the current response is already the final response (finish), the turn ends naturally and INTERRUPT simply opens the next turn.
- **cancel is a racing cancellation**: the in-flight provider call is raced down immediately, and the already-accumulated content is kept as a finalized partial — "cancellation is normal termination, not an error; nothing already produced is lost".

### Tour of hook points on the loop

`on_enqueue` (before enqueue, may reject/rewrite) → `before_turn` (before the dequeued batch attaches, additive injection) → `on_turn_append` (before each message attaches) → `after_turn` (the single closing observation point on all paths). Chapter 1-4 used two of them; the full set and pattern filtering are in Chapter 4-6.

## Out of scope

- Chapter 4-3 covers the five-operation tree surgery and the full semantics of the `turn_end`, `partial`, and `synthetic` markers.
- Chapter 4-7 covers `_dequeue` overrides, drain merging, and the implementation details of the checkpoint sequence.
- Chapter 4-6 covers cancellation decisions, including `before_cancel` interception that blocks a cancellation.

## Main example

`demo_mechanics.py` runs four experiments and replays the tree; the complete program, query inputs, and configuration follow:

```console
$ uv run python demo_mechanics.py
== Experiment 1: steer (STEER visible in-turn, no abort) ==
turn status=completed (completed = unbroken)
== Experiment 2: INTERRUPT (abort the current turn during tool execution) ==
aborted turn status=cancelled aborted=True (cancelled = aborted)
== Experiment 3: pause / resume (suspend and resume the work loop) ==
during pause current_turn is not None: True (turn created, parked at the checkpoint, no LLM call)
after resume status=completed
== Experiment 4: cancel (abort execution; interrupted streamed output is kept and persisted) ==
after cancel status=cancelled; nodes appended this turn: 2 (user + partial provider: an interrupted turn keeps what was produced)
── message tree (head -> root, reverse chronological order) ──
 12 provider  pri=NORMAL  turn_end=True 
 11 user      pri=NORMAL  turn_end=False Tell a very, very long story.
 10 provider  pri=NORMAL  turn_end=True 2 + 2 = 4. I'm a concise Engli
  9 user      pri=NORMAL  turn_end=False Introduce yourself in one sent
  7 user      pri=INTERRUPT turn_end=False Stop! First answer: what is 2+
  8 tool      pri=NORMAL  turn_end=False exit_code: 0 --- stdout --- --
  6 provider  pri=NORMAL  turn_end=False 
  5 user      pri=NORMAL  turn_end=False First use bash to run sleep 20
  4 provider  pri=NORMAL  turn_end=True 1 2 3 4 5 (steer received)
  3 user      pri=STEER   turn_end=False Addendum: after the count, app
  2 provider  pri=NORMAL  turn_end=True 1 2 3 4 5
  1 user      pri=NORMAL  turn_end=False Count from 1 to 5, one number 
```

Read this tree against the traces of the four mechanisms:

- **id 3 (STEER)** sits inside Experiment 1's turn chain (after id 2, before id 4) — steer is absorbed in-turn, and id 4's answer carries the "(steer received)" mark.
- **id 7 (INTERRUPT)** comes after Experiment 2's bash result (id 8) — the bash call ran to completion, the turn aborted at the checkpoint (status=cancelled), and the INTERRUPT attached to the tree was consumed by the next turn (id 10 answers 2+2).
- **Experiment 3 (pause)**: while suspended, the turn was already created (current_turn is not None) but parked at the checkpoint — no nodes were produced, and after resume it completed normally.
- **Experiment 4 (cancel)**: status cancelled, 2 nodes left on the tree — the already-started streamed output was kept as a finalized partial (the `partial` semantics; Chapter 4-3 expands the `partial` marker).

### Complete reproduction material

The following blocks contain everything required for this example. The relative
filenames are included with their complete contents, and the command uses one
of those filenames without requiring a directory change. A model call requires
the reader to provide `DEEPSEEK_API_KEY`; no credential is included here.

```python
# main.py
"""launch imports this module and awaits main() to obtain a Runtime."""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "en",
               resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        kwargs: dict = {}
        if user_name is not None:
            kwargs["user_name"] = user_name
        if locale != "en":
            kwargs["locale"] = locale
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

```yaml
# providers.yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

```yaml
# models.yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

```yaml
# model-tags.yaml
tags:
  default: deepseek-flash
```

```yaml
# root.fya front matter
description: "Mechanics experiment assistant: observes control traces on the queue and the tree."
model_tag: default
tools:
  - bash
---
$system_prompt:
You are a concise English assistant. Keep answers within five sentences.
```

```python
# demo_mechanics.py
import asyncio

from flowing import launch
from flowing.message import Message, MessageKind, MessagePriority, TextBlock


def tree_lines(agent, limit=40):
    rows = []
    for message in agent.chain.walk(agent.current_head_id):
        head = ""
        for block in message.content:
            if getattr(block, "text", ""):
                head = block.text.replace("\n", " ")[:30]
                break
        rows.append(
            f"{message.id:>3} {message.kind.value:<9} "
            f"pri={message.priority.name:<7} turn_end={message.turn_end} {head}"
        )
    return rows[:limit]


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    print("== Experiment 1: steer (STEER visible in-turn, no abort) ==")
    task = asyncio.create_task(agent.query("Count from 1 to 5, one number per line."))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await agent.steer('Addendum: after the count, append the sentence "(steer received)".')
    result = await task
    print(f"turn status={result.status} (completed = unbroken)")
    await asyncio.sleep(0.3)

    print("== Experiment 2: INTERRUPT (abort the current turn during tool execution) ==")
    task = asyncio.create_task(agent.query(
        "First use bash to run sleep 20, then answer: why is the sky blue? (one sentence)"
    ))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(8.0)
    await agent.enqueue_message(Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="Stop! First answer: what is 2+2?")],
        priority=MessagePriority.INTERRUPT,
    ))
    result = await task
    print(f"aborted turn status={result.status} aborted={result.turn.aborted} (cancelled = aborted)")
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)

    print("== Experiment 3: pause / resume (suspend and resume the work loop) ==")
    agent.pause()
    task = asyncio.create_task(agent.query("Introduce yourself in one sentence."))
    await asyncio.sleep(1.5)
    print(f"during pause current_turn is not None: {agent.current_turn is not None} (turn created, parked at the checkpoint, no LLM call)")
    agent.resume()
    result = await task
    print(f"after resume status={result.status}")

    print("== Experiment 4: cancel (abort execution; interrupted streamed output is kept and persisted) ==")
    before = set(agent._messages)
    task = asyncio.create_task(agent.query("Tell a very, very long story."))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.0)
    await agent.cancel()
    result = await task
    new_nodes = [message_id for message_id in agent._messages if message_id not in before]
    print(f"after cancel status={result.status}; nodes appended this turn: {len(new_nodes)} (user + partial provider: an interrupted turn keeps what was produced)")

    print("── message tree (head -> root, reverse chronological order) ──")
    for line in tree_lines(agent):
        print(line)
    await runtime.shutdown()


asyncio.run(main())
```

Run in an environment where Flowing is installed:

```console
$ export DEEPSEEK_API_KEY="<your-api-key>"
$ uv run python demo_mechanics.py
```

The four queries, steer text, INTERRUPT message, and pause/cancel operations
are all explicit in the code above. The output earlier in this section is one
recorded run; model text and message IDs can vary with provider responses and
runtime state, so the status lines illustrate the mechanics rather than promise
byte-for-byte identical output.

## Summary

1. The message tree is a parent-chain forest with a head cursor, and `fork` switches only the view; Chapter 4-3 covers tree surgery.
2. The default batch contains the leading urgent run and the first non-urgent message, while PROVIDER messages never enter the queue.
3. `steer` is absorbed in-turn without aborting, while INTERRUPT closes the turn with an abort at a checkpoint and does not hard-switch in-flight execution.
4. `pause` parks the work loop at a checkpoint, while `cancel` races in-flight generation and preserves partial output on disk.
