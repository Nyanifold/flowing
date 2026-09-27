# 2-1 · Loops and Concurrency: Event-Driven, Entry Semantics, and Cancellation

> Reproduction requirements: Python 3.13+, uv, the Flowing package, a DeepSeek API key, and network access from the terminal; Experiment 2 also requires `bash` and `sleep`.
> Complete configuration, prompt, data, both demo scripts, their inputs, and sample outputs are included at the end of this chapter; no other file is needed.

> Prerequisites: Chapter 1-4 "The ReAct Loop"

## What this chapter covers

The concurrency model of an agent runtime: the event-driven message loop,
the semantic difference between the two kinds of message entry, cooperative
cancellation, and the deadlock problem in multi-agent systems.

## Background

Event-driven is a general model for concurrent systems: external stimuli
(user input, timer events, completion of asynchronous tasks, receipts from
other agents) are uniformly represented as events, enter a queue in order,
and are consumed by a single loop. This model avoids most of the complexity
of shared state across threads: every state change happens inside the loop,
in order.

In this framework, the owner of the loop is the **agent instance**: each
agent has its own message queue and a resident work loop. A tool has no
loop of its own — it runs inside the turn call stack of the agent that
invoked it. Whenever the text below says "who waits for whom", the subject
is always an agent.

Agent systems are a natural fit for event-driven processing: the tool
receipts, external triggers, and user input from Chapter 1 all belong in
the same processing pipeline. The costs are: execution inside the loop
must not block indefinitely, or every subsequent event queues up; and
cross-agent waiting must be careful not to form cycles.

## Core concepts

### Two kinds of entry: waiting for a result vs. not waiting

A framework typically provides two levels of message entry:

- **Synchronous wait**: deliver a message and wait for the current round of
  problem-solving to finish, then return the result object. The caller gets
  the final state (completed / failed / cancelled); the semantics are simple,
  but the caller is occupied;
- **Asynchronous delivery**: only place the message into the queue and return
  an identifier immediately. The result does not go back to the caller; it is
  picked up by later hooks, subscriptions, or polling. The caller does not
  block, which suits event sources (timers, callbacks) and fire-and-forget
  scenarios.

There is a third entry for correcting model behavior while it runs:
**steer** — inject a high-priority message into the turn that is currently
executing; it becomes visible to the current turn's context without
interrupting execution.

The first complete script below puts all three entries into a
single run. Its code and a representative output are included at the end of
this chapter:

```console
message() enqueued, message id=1 (the caller does not wait for the turn result)
Turn has started (current_turn is not None)
steer() delivered (STEER priority: visible to the current turn, no interruption)
Turn has ended
query() waited for TurnResult: status=completed
first 80 chars of final_text: Glob confirms there are 2 files in the notes directory: `使用说明.md` and `路线图.md`.
```

Line by line: the first line is asynchronous delivery — only the message id
comes back, and the call moves on immediately. The second line shows the turn
has already started in the background, but nobody waits for its result. The
third line is a steer message injected into the turn that is **currently
executing**. Lines 5-6 show `query()` blocking until the turn finished and
returning `status=completed`, with the model confirming in one sentence that
the notes directory still has 2 files.

Where is the evidence that the steer was actually received? It sits in the
tree replay at the end of the same transcript:

```console
  5 provider  pri=NORMAL turn_end=True The notes directory contains 2 files…ries. (steer received)
  3 user      pri=STEER  turn_end=False Additional requirement: end your ans…th '(steer received)'.
```

Message 3 is the STEER message, and message 5 — the answer produced by the
turn that was executing when the steer arrived — ends with "(steer received)".
That is exactly what the steer asked for ("end your answer with '(steer
received)'"). The steer requirement entered the current turn's context and
the turn was not interrupted (`status=completed` on line 5 of the excerpt);
this is the empirical demonstration of steer semantics.

### Cooperative cancellation

Cancellation is not a kill signal: the executing turn and asynchronous tasks
check the cancellation signal at designated checkpoints and decide for
themselves whether to stop immediately, stop after finishing the current
step, or ignore it (uninterruptible critical sections). Cancellation is
therefore one form of normal termination, not an error; intermediate results
produced so far are kept or discarded by design.

The granularity of cancellation is usually layered: terminating only the
current turn < terminating a class of tasks < terminating the whole agent.
An executing agent must be registered in a traversable registry so that a
cancellation operation can find its target by granularity.

The second complete script below runs four experiments in a row: steer,
interrupt, pause, and cancel. Its code and representative output are
included at the end of this chapter:

```console
== Experiment 2: INTERRUPT (interrupt the current turn during tool execution) ==
Interrupted turn status=cancelled aborted=True (cancelled = interrupted)
== Experiment 4: cancel (abort execution; streamed output produced before interruption is persisted) ==
Status after cancel: cancelled; nodes persisted by this turn: 2 (user + partial provider: an interruption does not lose produced output)
```

Line by line: in Experiment 2, a highest-priority INTERRUPT message arrives
while a `sleep 20` command is executing — the in-flight command runs to
completion, the turn then ends as "cancelled", and the interrupt message is
handled by the next turn (in the representative message-tree output,
message 10 answers the new question: "2+2 = 4."). In Experiment 4, the
cancellation lands while the model is streaming a long story: the status is
"cancelled" rather than "error", and the content produced before the
interruption is preserved in history as a partial message. Cancellation is a
cooperative normal termination, and these two experiments show its two
typical forms.

### Deadlock

The most common form of deadlock in agent systems is circular waiting:
agent A synchronously waits for a result from agent B inside its own turn,
while B in turn waits for A. Each agent instance has only one work loop, so
A waiting for B means A's loop is blocked; if B ever needs A to process any
message, B can never finish.

There is exactly one avoidance rule, and it must be enforced strictly:
**never initiate a synchronous wait inside a turn call stack on this agent
itself (or on any agent already on the waiting chain)**.

```python
# ✗ deadlock: synchronous wait inside a tool for this agent's next turn —
#   the current turn waits for the next turn, and the next turn waits for
#   the current turn to finish
async def execute(self, *, caller):
    result = await caller.query("continue")     # never arrives

# ✓ steer: supplementary requirements are visible to the current turn's
#   context without interrupting execution
async def execute(self, *, caller):
    await caller.steer("supplementary requirement: ...")
```

When driving is needed, use a steer message instead, or move the wait
outside the turn.

## Common misconceptions

1. **Synchronously waiting inside a hook or tool for this agent's next
   turn**. This is the standard form of deadlock; use a steer instead;
2. **Handling cancellation as an exception**. Cancellation is a normal
   termination path; the wrap-up of intermediate results and state should be
   designed as normal termination;
3. **Ignoring cancellation granularity**. The whole agent, a class of tasks,
   and the current turn are three distinct levels of operation, and mixing
   them up hurts other work running in parallel.

## Exercises

1. Design the entry choice for "download and summarize five web pages": what
   does the main flow use, what do the download-completion events use, and
   what does a mid-way user supplement use;
2. Find the blocking point in this pseudocode: a tool's `execute` runs
   `result = await caller.query("summarize")` inside an active turn — the
   semantics of `query` are "deliver a message to this agent and wait for
   this agent's reply". Draw this waiting edge and explain why the reply can
   never arrive;
3. Modify Experiment 4 in the complete second demo script below so the model
   first outputs a recognizable opening before cancellation, then inspect
   the message-tree output to confirm whether the opening is preserved.

## Complete example: entry semantics, steering, interruption, pause, and cancellation

The following code and data form two complete demos. Save the listed files
in a new empty directory, replace `...` with your API key, and set it only
as the `DEEPSEEK_API_KEY` environment variable. Never write a real credential
to a file. The demo inputs are embedded in the scripts; no separate input
file is needed.

`pyproject.toml`:

```toml
[project]
name = "flowing-chapter-2-1"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["flowing-agent"]

[tool.uv]
package = false
```

`main.py`:

```python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "en") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "en":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

`root.fya` (tool table and complete prompt):

```yaml
description: "Loop-mechanics experiment assistant: helps observe entry semantics and control traces."
model_tag: default
tools:
  - read
  - glob
  - bash
---
$system_prompt:
You are the Q&A assistant for this project. The absolute path of the project root is {{ env.PWD }} (always use this absolute path when calling file tools; do not guess other directories). Use bash when the user asks you to run commands, and use read / glob to inspect files. Keep your answers within five sentences.
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

Run these commands from the directory containing the files:

```sh
uv sync
export DEEPSEEK_API_KEY="..."
uv run python demo_entries.py
uv run python demo_mechanics.py
```

All note data read by the demos:

`notes/使用说明.md`:

```markdown
# Usage

This directory is a demo notes library.

## Installation

Python 3.13 or later is required.

## Common commands

- `uv sync`: install dependencies
- `uv run pytest`: run tests

## Notes

Credentials must always be held in environment variables; never write them into any file.
```

`notes/路线图.md`:

```markdown
# Roadmap

- 2026-Q3: complete core functionality
- 2026-Q4: release version 1.0
```

`demo_entries.py`:

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    mid = await agent.message(
        "Use glob to look at the notes directory (use the absolute path from "
        "the system prompt), then say how many files are in it.")
    print(f"message() enqueued, message id={mid} (the caller does not wait for the turn result)")
    while agent.current_turn is None:
        await asyncio.sleep(0.2)
    print("Turn has started (current_turn is not None)")

    await agent.steer("Additional requirement: end your answer with '(steer received)'.")
    print("steer() delivered (STEER priority: visible to the current turn, no interruption)")
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    print("Turn has ended")

    result = await agent.query(
        "Use glob again to confirm how many files are in the notes directory; "
        "answer in one sentence.")
    print(f"query() waited for TurnResult: status={result.status}")
    print(f"first 80 chars of final_text: {result.final_text[:80]}")

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
```

`demo_mechanics.py`:

```python
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

    print("== Experiment 1: steer (STEER visible to the current turn, no interruption) ==")
    t = asyncio.create_task(agent.query("Count from 1 to 5, one number per line."))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await agent.steer("Supplement: after the count, add one line saying \"(steer received)\".")
    r = await t
    print(f"Turn status={r.status} (completed = not interrupted)")
    await asyncio.sleep(0.3)

    print("== Experiment 2: INTERRUPT (interrupt the current turn during tool execution) ==")
    t = asyncio.create_task(agent.query(
        "First run sleep 20 with bash, then answer: why is the sky blue? (one sentence)"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(8.0)
    await agent.enqueue_message(Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="Stop! First answer: what is 2+2?")],
        priority=MessagePriority.INTERRUPT))
    r = await t
    print(f"Interrupted turn status={r.status} aborted={r.turn.aborted} (cancelled = interrupted)")
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)

    print("== Experiment 3: pause / resume (suspend and resume the work loop) ==")
    agent.pause()
    t = asyncio.create_task(agent.query("Introduce yourself in one sentence."))
    await asyncio.sleep(1.5)
    print(f"During pause, current_turn is not None: {agent.current_turn is not None}"
          f" (turn created and stopped at the checkpoint, no LLM call)")
    agent.resume()
    r = await t
    print(f"Status after resume: {r.status}")

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

    print("── message tree (head → root, reverse chronological) ──")
    for line in tree_lines(agent):
        print(line)
    await runtime.shutdown()


asyncio.run(main())
```

The first script embeds all its inputs: `message()` starts a directory
inspection, `steer()` asks for the suffix `(steer received)`, and `query()`
checks the file count again. The second script likewise includes every
prompt. Experiment 2 waits eight seconds after requesting `sleep 20` before
sending INTERRUPT; Experiment 3 resumes after a 1.5-second pause; Experiment
4 cancels one second after the turn starts.

Representative full output from `uv run python demo_entries.py` follows.
Model text and message IDs can vary. Paths use the machine-agnostic
`<project-root>` placeholder:

```text
message() enqueued, message id=1 (the caller does not wait for the turn result)
Turn has started (current_turn is not None)
steer() delivered (STEER priority: visible to the current turn, no interruption)
Turn has ended
query() waited for TurnResult: status=completed
first 80 chars of final_text: Glob confirms there are 2 files in the notes directory: `使用说明.md` and `路线图.md`.
── message tree (head → root, reverse chronological; pri=STEER marks a steer message) ──
  9 provider  pri=NORMAL turn_end=True Glob confirms there are 2 files in the notes directory: `使用说明.md` and `路线图.md`.
  8 tool      pri=NORMAL turn_end=False <project-root>/notes/路线图.md
  7 provider  pri=NORMAL turn_end=False
  6 user      pri=NORMAL turn_end=False Use glob again to confirm how many files are in the notes directory; answer in one sentence.
  5 provider  pri=NORMAL turn_end=True The notes directory contains 2 files: `使用说明.md` and `路线图.md`. (steer received)
  3 user      pri=STEER  turn_end=False Additional requirement: end your answer with '(steer received)'.
  4 tool      pri=NORMAL turn_end=False <project-root>/notes/路线图.md
  2 provider  pri=NORMAL turn_end=False
  1 user      pri=NORMAL turn_end=False Use glob to look at the notes directory (use the absolute path from the system prompt), then say how many files are in it.
```

Representative output from `uv run python demo_mechanics.py` follows.
Model text, timing, and message IDs vary; whether cancellation catches a
partially streamed provider message depends on when the signal arrives:

```text
== Experiment 1: steer (STEER visible to the current turn, no interruption) ==
Turn status=completed (completed = not interrupted)
== Experiment 2: INTERRUPT (interrupt the current turn during tool execution) ==
Interrupted turn status=cancelled aborted=True (cancelled = interrupted)
== Experiment 3: pause / resume (suspend and resume the work loop) ==
During pause, current_turn is not None: True (turn created and stopped at the checkpoint, no LLM call)
Status after resume: completed
== Experiment 4: cancel (abort execution; streamed output produced before interruption is persisted) ==
Status after cancel: cancelled; nodes persisted by this turn: 2 (user + partial provider: an interruption does not lose produced output)
── message tree (head → root, reverse chronological) ──
 12 provider  pri=NORMAL  turn_end=True
 11 user      pri=NORMAL  turn_end=False Tell a very, very long story.
 10 provider  pri=NORMAL  turn_end=True 2+2 = 4.
  9 user      pri=NORMAL  turn_end=False Introduce yourself in one sentence.
  7 user      pri=INTERRUPT turn_end=False Stop! First answer: what is 2+2?
  8 tool      pri=NORMAL  turn_end=False exit_code: 0 --- stdout --- --
  6 provider  pri=NORMAL  turn_end=False
  5 user      pri=NORMAL  turn_end=False First run sleep 20 with bash, then answer: why is the sky blue? (one sentence)
  4 provider  pri=NORMAL  turn_end=True 1 2 3 4 5 (steer received)
  3 user      pri=STEER   turn_end=False Supplement: after the count, add one line saying "(steer received)".
  2 provider  pri=NORMAL  turn_end=True 1 2 3 4 5
  1 user      pri=NORMAL  turn_end=False Count from 1 to 5, one number per line.
```

## Summary

1. The event-driven model handles input, receipts, and external events
   uniformly;
2. Synchronous wait and asynchronous delivery serve different callers; steer
   solves in-turn driving;
3. Cancellation is a cooperative signal, layered by granularity, and can be
   intercepted before it lands;
4. Synchronous waiting inside a turn call stack is forbidden — it is the one
   hard rule of deadlock avoidance.
