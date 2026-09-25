# 4-1 · Core State and Message Persistence

## Prerequisites

[1-8 Runtime and Agent Directories](1-8-runtime-and-agent-dirs.md) (the
Runtime/Agent relationship) and [1-1 Turn and Loop](1-1-turn-and-loop.md)
(`TurnContext`). Flowing must be installed, and `DEEPSEEK_API_KEY` must be set
in the environment. This chapter keeps the provider credential as an
environment-variable placeholder.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| record stream | The persisted form of a message tree: records are appended as lines, and completed messages are committed to the stream. |
| write-behind | A commit returns after a record is queued; one background task writes records serially, and normal shutdown drains the queue. |
| crash window | The interval between queueing and writing tail records. Loss inside this interval is consistent with the process's in-memory authority being lost too. |
| identity fields | The Agent metadata fields `agent_type`, `parent_agent_id`, `created_at`, and `args`. |
| recovery by replay | Replaying persisted message and state records reconstructs the live state used to assemble the next turn's context. |

## Goals

This chapter describes which parts of core state persist, their persisted
forms, and which execution details deliberately do not persist. Persistence is
a core mechanism, not a policy.

## Main text

### Message persistence

The message tree is stored as a JSON Lines record stream. The first record
identifies the format version; subsequent records represent messages or
changes to message history. One line represents one record. A complete message
is appended, and later modifications are represented by change records such
as tombstones, moves, or updates. Periodic cleanup rewrites the stream without
changing the result of replay. Measured `usage` is stored on the corresponding
PROVIDER message, as described in [3-3 Provider Basics](3-3-provider-basics.md).

### Core state: identity and cursor

- **Identity fields:** `agent_type` records the declared type;
  `parent_agent_id` identifies the parent (the root's parent is `"runtime-0"`);
  `created_at` records creation time; and `args` stores creation arguments.
- **`current_head_id`:** the context-assembly cursor is persisted with core
  state. Recovery restores the cursor, so the next turn continues from the
  existing message path.
- **Runtime registry and global state:** Runtime-level core state records the
  Agent pool. A separate Runtime-global state bag is covered in
  [4-2 State Namespaces and Self-Registration](4-2-state-namespaces-and-registration.md).

### Deliberately not persisted

- **Logical turns:** `TurnContext` is temporary execution state. It is not
  persisted or inserted into the message tree, and an in-progress turn is not
  resumed after a crash.
- **Intra-turn transients:** retry counters and similar values reset with the
  turn.
- **Wait handles:** pending-turn handles exist only in the running process.

### Write-behind and the crash window

Submitting a record queues it and returns. The write queue is drained when its
owner is finalized and before a full-file rewrite. A process that exits without
normal shutdown can lose tail records that have not reached disk. The in-memory
state is authoritative while the process is alive; after a crash, both that
authority and any unflushed tail are gone, so replay reconstructs a
self-consistent earlier state.

### Recovery picture

```text
message-record replay ─┐
                       ├→ in-memory message tree + cursor + state bag → next-turn context input
state-record replay ───┘       (an in-progress turn is not resumed)
```

Each subagent has its own persisted session state. Its recovered memory is the
result of replaying that subagent's message subtree.

## Out of scope

- Business-state namespaces and registration are covered in
  [4-2 State Namespaces and Self-Registration](4-2-state-namespaces-and-registration.md).
- Advanced recovery invariants, including orphaned tool-call placeholders and
  pairing checks, are covered in
  [4-3 Message-Tree Surgery](4-3-message-tree-surgery.md).
- Torn final lines, tombstones, compaction timing, and migration chains are
  outside this chapter.
- Operational cleanup of the Agent pool is outside this chapter.

## Main example

This example uses one persistent project for three stages: a normal turn, a
second process that exits abruptly, and a restart that reads both turns. All
configuration, prompt text, program code, inputs, and sample outputs needed
for the demonstration are included below. Install Flowing and set
`DEEPSEEK_API_KEY` before running the commands. The model's exact wording can
vary; internal reasoning text is intentionally not displayed.

### Create these project files

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

`root.fya`:

```yaml
description: "Persistence demo assistant: remembers what the user tells it."
model_tag: default
---
$system_prompt:
You are a memory assistant. When asked to remember something, confirm it in
one sentence. When asked later, recall it accurately. Keep every answer to one
sentence.
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

`demo_crash.py`:

```python
import asyncio
import os

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    result = await agent.query("Also remember a color: purple.")
    print(
        f"Turn finished status={result.status}; "
        "now simulating abrupt exit with os._exit(9)",
        flush=True,
    )
    os._exit(9)


asyncio.run(main())
```

The normal-process input and representative visible output are:

```text
$ uv run flowing repl .
(agent-main)>>> Remember the number 731.
[thinking] (reasoning trace omitted)
I'll remember that the number is 731.
(agent-main)>>> /exit
```

The process leaves three records in the message stream: one metadata record,
one user message, and one PROVIDER message. Inspect the first record and
identity metadata with:

```text
$ wc -l .flowing/agent-main/tree.jsonl
3 .flowing/agent-main/tree.jsonl
$ head -1 .flowing/agent-main/tree.jsonl
{"type": "meta", "format_version": 1}
$ cat .flowing/agent-main/meta.json
{
  "agent_type": "@/root.fya",
  "parent_agent_id": "runtime-0",
  "created_at": "<generated timestamp>",
  "args": {}
}
```

The abrupt-exit process recovers the existing Agent, adds one turn, and exits
with status 9 without normal shutdown:

```text
$ uv run python demo_crash.py
Turn finished status=completed; now simulating abrupt exit with os._exit(9)
$ echo $?
9
$ wc -l .flowing/agent-main/tree.jsonl
5 .flowing/agent-main/tree.jsonl
```

The last command is a recorded result of this demonstration, not a guarantee
about every process schedule. The write-behind timing claim is a point to
verify when evaluating a specific runtime and storage environment.

Restart the REPL and ask for both remembered values:

```text
$ uv run flowing repl .
(agent-main)>>> What are the number and the color I asked you to remember?
[thinking] (reasoning trace omitted)
You asked me to remember the number 731 and the color purple.
```

The number was written by the normal process and the color by the process that
exited abruptly. After recovery, replay makes both completed turns available
to the next turn's context.

## Summary

1. The message stream stores version metadata and message/change records;
   completed messages are appended and replay reconstructs the tree.
2. Core persistence includes Agent identity, the context cursor, and Runtime
   registry state.
3. Logical turns, intra-turn transients, and wait handles are not persisted.
4. Write-behind has a tail-record crash window; recovery rebuilds state by
   replay rather than resuming an in-progress turn.
