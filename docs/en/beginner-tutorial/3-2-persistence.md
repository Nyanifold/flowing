# 3-2 · Persistence: Session State, Write-Behind, and Crash Consistency

> Prerequisites: Flowing CLI is installed and `DEEPSEEK_API_KEY` is set in the environment. Every file needed to run the example is included below.
> Normal conversation: `uv run flowing repl .`; crash drill: finish and exit one turn, run `uv run python demo_crash.py`, then run the conversation command again to verify recovery.
> Run the sequence in a new isolated working directory. The crash script terminates its Python process immediately without running shutdown.

> Prerequisites: Chapter 1-1 "The Agent Runtime"

## What this chapter covers

This chapter covers the persistence design of session state: which objects
need to be persisted, the two general mechanisms of record streams and
write-behind, the crash-consistency window, and the recovery semantics of
session identity.

## Background

Process memory is volatile: a restart loses it. An agent system's
conversation memory, business progress, and component identity usually must
survive across processes, which calls for persistence. A persistence design
must answer four questions: what to store, in what form, when to write, and
what to guarantee during a crash.

### Complete example materials

Each block heading gives the relative filename to create, and every block
contains the full file contents. Create these files in a fresh working
directory and run the commands in order. The provider credential is
supplied only through the `DEEPSEEK_API_KEY` environment variable.

#### `root.fya`

```yaml
description: "Memory demo assistant: remembers whatever the user tells it."
model_tag: default
---
$system_prompt:
You are a memory demo assistant. When the user asks you to remember something,
confirm it with a one-sentence restatement; when asked about it later, recall
it accurately. Keep every answer to one sentence.
```

#### `main.py`

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

#### `providers.yaml`

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

#### `models.yaml`

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

#### `model-tags.yaml`

```yaml
tags:
  default: deepseek-flash
```

#### `demo_crash.py`

```python
import asyncio
import os

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    result = await agent.query("Also remember a color: purple.")
    print(f"Turn completed status={result.status}; now simulating kill -9 (os._exit(9))", flush=True)
    os._exit(9)


asyncio.run(main())
```

#### Inputs and visible output from the three processes

In the first process, run `uv run flowing repl .`, enter the memory request,
then exit normally:

```console
(agent-main)>>> Remember the number 731.
[thinking] (reasoning trace omitted)
I'll remember that the number is 731.
(agent-main)>>> /exit
```

In the second process, run `uv run python demo_crash.py`. The script calls
`os._exit(9)` directly:

```text
Turn completed status=completed; now simulating kill -9 (os._exit(9))
```

In the third process, run `uv run flowing repl .`, ask the recovery question,
then exit normally:

```console
(agent-main)>>> What number and color did I ask you to remember?
[thinking] (reasoning trace omitted)
You asked me to remember the number 731 and the color purple.
(agent-main)>>> /exit
```

The recorded line-count check is:

```text
5 .flowing/agent-main/tree.jsonl
```

## Core concepts

### What to persist: messages, state, identity

| Object | Contents | Why persist it |
|---|---|---|
| Message history | Each turn's user input, model responses, and tool results | The conversation is the product; history is the input to the next request |
| Business state | Business data written by tools (preferences, lists, progress) | Context that continues across sessions |
| Identity | A component's identifier, creation parameters, and relationships (which component is whose child) | "Getting the same component back" depends on identity continuity |

Correspondingly, execution-time transients do not need to be persisted:
in-progress turns, pending requests, and in-memory counters. After a crash
these are treated as "never happened".

After the first conversation turn, a `.flowing/` state directory appears in
the working directory, and its layout maps one-to-one to the three objects:

```text
.flowing/
├── core.jsonl              # runtime-level core state (component registry)
└── agent-main/             # one session directory per agent
    ├── meta.json           # identity: source declaration, parent, creation time, creation parameters
    ├── tree.jsonl          # message history: one message per line
    └── …                   # business state files (none produced in this example)
```

### Record streams: append-mostly, compacted periodically

Historical data is stored as a **record stream**: new records are appended to
the end of the file, and changes to history are expressed by appending change
records (delete = a tombstone row) rather than erasing the original text. The
backend periodically compacts and rewrites (once tombstones and
deletion-marked records accumulate to a threshold, the whole file is
rewritten) — the replay result is strictly identical before and after the
rewrite. The gains:

- Writes are dominated by sequential I/O, so hot-path overhead stays constant;
- History is an audit log: deletions are expressed as markers and remain traceable;
- Crash recovery = sequential replay, with simple and provable semantics.

```text
Operation sequence (not literal record syntax): append message A → append message B → append a tombstone for A.
```

The persistent record file stores one message or change record per line; the
first line records the format version.

### Write-behind and the crash window

"Writes modify memory first and are flushed to disk asynchronously by a
background task" is write-behind (database buffers and operating-system page
caches belong to the same family). It buys low latency on the hot path at the
cost of a consistency window: records that have been submitted but not yet
written to disk are lost when the process crashes.

The engineering treatment is clear: the window must be small enough (on the
order of milliseconds), and semantically "memory is authoritative" — when the
authority dies along with the process, the state reached by replay is
self-consistent, as if the lost operations never happened. The normal-exit
path must include a drain step (write the queue empty before closing); the
window only appears on abnormal death.

The following pseudocode shows the sequence conceptually; `state` and
`runtime` are placeholders, not a complete executable program.

```python
state["prefs"] = {"theme": "dark"}   # ① memory update + submit write request (returns immediately)
                                     # ② background task flushes to disk asynchronously
await runtime.shutdown()             # ③ normal exit: drain, then close; the window closes
```

### Session identity and recovery

"Recovery" must be made precise as "rebuild objects by identity": identity
(identifier, parameters, relationships) is persisted, so in-memory objects
can be destroyed and rebuilt. A recovered component should be equivalent to
the one before the crash — history, state, and relationships all present;
only a turn that was executing is treated as cancelled.

The complete crash drill uses three processes in sequence. The input,
visible replies, and crash-script output are all included above. Run these
three commands in order:

```console
$ uv run flowing repl .          # ① have the agent remember a number, then /exit normally
$ uv run python demo_crash.py    # ② remember a color too, then the process simulates kill -9
$ uv run flowing repl .          # ③ restart and ask for both facts to verify recovery
```

Run the sequence from a fresh working directory with no existing `.flowing/`
state. In the first process you had it remember the number, and it
answered "I'll remember that the number is 731"; in the third process you
asked only "what number and color did I ask you to remember," and it answered
"You asked me to remember the number 731 and the color purple." The number
came from the first process, which exited normally; the color came from the
second process, which **died abnormally** — `os._exit(9)` skipped all normal
shutdown logic, but the records of turns completed before the crash had
already reached disk. When the third process starts, it replays the records
left by both processes into a single shared history, so it can restate both
facts. The line-count check included above shows 5 lines after the crash
(metadata plus the messages from two turns). No loss inside the crash window occurred — and even a
loss there would not break consistency: the state reached by replay is always
self-consistent.

## Common misconceptions

1. **Sync writes for safety.** Hot-path latency doubles, and the gain is only
   a smaller version of a window that was already milliseconds; the right
   approach is write-behind plus draining on normal exit;
2. **Deletion means erasure.** Historical data should receive an appended
   tombstone; erasing deletes breaks audit and replay semantics;
3. **Persisting transients too.** In-progress turns and pending handles are
   transients; mixing them into persistence complicates recovery semantics
   for no benefit.

## Exercises

1. For a companion agent that continues chats across days, write a
   persistence inventory: which category above do messages, user preferences,
   and the daily counter each belong to, and what is transient;
2. Write pseudocode for the drain sequence on normal exit (stop accepting
   input → drain the queue → close the file), and state which failure each
   step guards against;
3. In a separate fresh working directory, repeat the first and third
   processes without running the crash process, then ask the same questions;
   explain why the color is no longer present in the recovered history.

## Summary

1. The three persistence objects: messages, business state, and identity;
   transients are treated as "never happened";
2. Historical data is stored as a record stream: append-mostly, with the
   backend compacting and rewriting periodically; replay-based recovery
   semantics do not change;
3. Write-behind trades hot-path performance against a millisecond-scale crash
   window; normal exit must drain;
4. Recovery = rebuilding by identity; the recovered component is equivalent
   to the pre-crash one, except that an in-progress turn counts as cancelled.
