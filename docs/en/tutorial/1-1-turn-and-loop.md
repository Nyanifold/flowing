# 1-1 · Turn and Loop

## Prerequisites

Chapter [0-2 Using Builtin Tools](0-2-use-builtin-tools.md) demonstrates an
Agent calling `glob` and `read` to complete a question-answering turn. This chapter
embeds the complete project code, configuration, system prompt, notes, program
inputs, and outputs. Save them in a project directory you create before running
the command.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| ReAct loop | The ReAct loop describes how the model generates a response, issues tool calls, observes their results, and generates again until it reaches a final answer. |
| resident work loop | Every Agent instance starts a resident work loop when it is created, and that loop consumes messages serially to open logical Turns. |
| logical Turn | A logical Turn consumes one or more messages until a PROVIDER message has `turn_end=True`, and `TurnContext` and `TurnResult` represent the execution phase. |
| `TurnResult` | A `TurnResult` contains `status`, `final_text`, and `token_usage`, and all four status outcomes resolve to the waiting caller. |
| deadlock prohibition | Awaiting `query()` inside the current turn's call stack always deadlocks, so use `steer()` to drive the current turn. |

## Goals

Understand the ReAct loop — the most basic principle of an agent system: how
messages drive turns; and distinguish the waiting semantics of the three
message entries `query` / `message` / `steer`.

## Main text

### The ReAct intuition: messages drive turns

The 0-2 session already demonstrated the loop itself: the model generates
("Let me look at the project structure first") → issues tool calls
(`[tool_call]`, one call per line with complete arguments) → observes tool
results (`[tool:completed]`) → generates again (answering based on what was
read). In flowing, this loop is driven by no out-of-band signal; it runs
entirely on **messages**: user input, model responses, tool results, and
external events are all represented as `Message`, and the agent's resident
work loop consumes the queue serially, with each (batch of) message(s)
driving one logical Turn — until a response with `finish=True` or a hook sets
`turn.finish` / a tool sets `finish_output`. The current tool batch completes
before a hook-requested end; `turn.finish` controls completion while result
production follows the turn's result rules. This chapter covers only two
message kinds (text input/output and tool calls); the full
message type system is the topic of 1-2.

### Three enqueue entries: choose by whether you need the result

```python
result = await agent.query("Look up order 4521 for me")   # This call waits for the turn product (TurnResult).
mid = await agent.message("Remind me to drink water later")   # This call returns only the message id without waiting.
await agent.steer("Change the budget cap to 500")   # This call steers an active turn with STEER priority.
```

- `query()` packs the message into the queue and waits for the `TurnResult` of the turn that contains that message.
- `message()` enqueues a message and immediately returns its id, so the caller does not receive the turn result.
- `steer()` posts a message with STEER priority; when delivered during an active turn, it is **absorbed into the current turn** and becomes visible to the LLM without interrupting the turn, as the main example shows.

### TurnResult: none of the four outcomes hangs

`result.status` has four values: `"completed"` (finished naturally) /
`"blocked"` (hard-blocked by `Intercepted`) / `"cancelled"`
(cancelled/destroyed/revoked) / `"error"` (terminated by an uncaught
exception). **All four outcomes resolve to the waiter** — a crash, a
cancellation, or `destroy()` will never hang the caller of `query()` (the
full error taxonomy is the topic of 4-6).

### The deadlock prohibition (the most common beginner pitfall)

Inside the current turn's call stack (any hook, any tool `execute`),
`await query()` always deadlocks — the turn's wrap-up waits for the hook to
return, the hook waits for the next turn, and the next turn waits for the
current turn to wrap up. Cross-agent wait cycles (A waits for B, B waits for
A) behave the same. **To drive within a turn, use `steer()`, not `query()`**:

```python
# ✗ This is forbidden because waiting inside a tool or hook for a new turn of the same agent causes a deadlock.
async def execute(self, *, caller: Agent):
    result = await caller.query("Continue to the next step")   # This call deadlocks.

# ✓ This is correct because the steer message is visible during this turn and does not interrupt it.
async def execute(self, *, caller: Agent):
    await caller.steer("Additional requirement: ...")
```

### priority and INTERRUPT (one-sentence version)

Messages carry priorities: `INTERRUPT > STEER > HIGH > NORMAL > LOW`, and
same-priority messages are dequeued in enqueue order. An INTERRUPT message
arriving while a turn is in flight **interrupts** the current turn (the
current turn aborts, and the new turn starts from the INTERRUPT message);
STEER does not interrupt and is absorbed into the current turn; all other
priorities queue normally. The queue and scheduling mechanics are the topic
of 3-1.

## Out of scope

- This chapter does not cover the `_run_turn` inner loop, `_dequeue` overrides, drain merging, or checkpoint details; chapter 4-7 presents the implementer's view.
- This chapter does not cover the complete message type system or ContentBlock; chapter 1-2 does.
- This chapter does not cover queue and tree mechanics for priority and interruption; chapter 3-1 does.
- This chapter does not cover cancellation semantics in full; chapter 4-6 does.

## Main example

The following inline contents form a complete project. Save this entry point as
`main.py`:

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

Save the complete Agent declaration as `root.fya`, and save the three model
configurations as `providers.yaml`, `models.yaml`, and `model-tags.yaml`:

```yaml
description: "Project Q&A assistant: it can list directories, read files, and summarize."
model_tag: default
tools:
  - read
  - glob
---
$system_prompt:
You are a project Q&A assistant. The absolute path of this project's root is {{ env.PWD }}.
For file checks, use glob first and read the relevant files next. Answer from their
contents; do not invent facts. Keep each answer within five sentences.
```

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

```yaml
tags:
  default: deepseek-flash
```

Create the two complete note files below as `notes/使用说明.md` and
`notes/路线图.md`:

```markdown
# Usage Guide

This directory is a demo notebook library.

## Installation

Python 3.13 or higher is required.

## Common commands

- Run `uv sync` to install dependencies.
- Run `uv run pytest` to run the tests.

## Cautions

Credentials are always held in environment variables; never write them into any file.
```

```markdown
# Roadmap

- The team plans to finish the core features in 2026-Q3.
- The team plans to release version 1.0 in 2026-Q4.
```

Save this complete script as `demo_entries.py`. It calls `message()`, `steer()`,
and `query()` in sequence, then prints the message tree:

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    mid = await agent.message(
        "Use glob to inspect the notes directory (use the absolute path from "
        "the system prompt) and say how many files it contains.")
    print(f"message() enqueued, message id={mid} (caller does not wait for the turn result)")
    while agent.current_turn is None:
        await asyncio.sleep(0.2)
    print("turn started (current_turn is not None)")

    await agent.steer("Additional requirement: append '(steer received)' at the end of your answer.")
    print("steer() delivered (STEER priority: visible this turn, no interruption)")
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    print("turn ended")

    result = await agent.query("Use glob again to confirm how many files the notes directory has; answer in one sentence.")
    print(f"query() got TurnResult: status={result.status}")
    print(f"final_text (first 80 chars): {result.final_text[:80]}")

    print("── Message tree (head → root, reverse chronological order; pri=STEER marks a steer message) ──")
    for message in agent.chain.walk(agent.current_head_id):
        head = ""
        for block in message.content:
            if getattr(block, "text", ""):
                text = block.text.replace("\n", " ")
                head = text if len(text) <= 62 else f"{text[:36]}…{text[-22:]}"
                break
        print(f"{message.id:>3} {message.kind.value:<9} pri={message.priority.name:<6} "
              f"turn_end={message.turn_end} {head}")

    await runtime.shutdown()


asyncio.run(main())
```

Set `DEEPSEEK_API_KEY`, start the REPL from the project root, then save and run
`demo_entries.py` with the command below. Tool paths in the transcript use the
placeholder `<project-root>`; no machine-specific absolute path is included.

```console
$ uv run python demo_entries.py
message() enqueued, message id=1 (caller does not wait for the turn result)
turn started (current_turn is not None)
steer() delivered (STEER priority: visible this turn, no interruption)
turn ended
query() got TurnResult: status=completed
final_text (first 80 chars): The notes directory contains exactly 2 files: `使用说明.md` and `路线图.md`.
── Message tree (head → root, reverse chronological order; pri=STEER marks a steer message) ──
 10 provider  pri=NORMAL turn_end=True The notes directory contains exactly…使用说明.md` and `路线图.md`.
  9 tool      pri=NORMAL turn_end=False <project-root>/notes/路线图.md
  8 provider  pri=NORMAL turn_end=False 
  7 user      pri=NORMAL turn_end=False Use glob again to confirm how many f…nswer in one sentence.
  6 provider  pri=NORMAL turn_end=True The notes directory contains 2 files….md`. (steer received)
  3 user      pri=STEER  turn_end=False Additional requirement: append '(ste…he end of your answer.
  5 tool      pri=NORMAL turn_end=False <project-root>/notes/路线图.md
  4 tool      pri=NORMAL turn_end=False <project-root>/root.fya
  2 provider  pri=NORMAL turn_end=False I'll inspect the notes directory.
  1 user      pri=NORMAL turn_end=False Use glob to inspect the notes direct…any files it contains.
```

Reading this transcript against the three entries:

1. `message()` (id 1) returns only the message id. The caller then observes
   `current_turn` change from None to not-None, which means the turn has started,
   but the caller does not wait for its result.
2. `steer()` (id 3, pri=STEER) lands inside the first turn's chain. Because
   the tree is printed newest-to-oldest, id 3 appears immediately after id 6
   in the listing, which means it precedes the closing PROVIDER message
   chronologically; the id-6 answer ends with the requested "(steer received)"
   marker, and the turn was not interrupted.
3. `query()` (id 7) waits for a `TurnResult` with `status=completed`, and
   `final_text` contains the lookup result. The STEER message remains in the
   tree and stays visible in subsequent-turn context; chapters 3-1 and 3-2
   cover the tree and context assembly.

Turn boundaries can also be read off the tree: each PROVIDER message with
`turn_end=True` closes one turn (ids 6 and 10 each close one turn).

The full entry point, agent definition, model configuration, notes, script, user inputs,
and illustrative output are embedded in this chapter. Save the blocks under their
stated relative filenames; no other example material is needed. Model-generated text
and message IDs vary between runs. This timing example also depends on the first turn
still being active when `steer()` is delivered; if it ends earlier, the steer content
is not guaranteed to appear in the first answer.

## Summary

1. The ReAct loop is message-driven: the model generates, calls a tool, observes the result, and generates again until it finishes.
2. Choose among the three entries by their waiting semantics: `query` waits for the result, `message` returns only the id, and `steer` guides the current turn.
3. All four `TurnResult` outcomes resolve, so the caller never hangs.
4. Never call `query()` inside the turn stack because it deadlocks; use `steer()` to drive the current turn.
