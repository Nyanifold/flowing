# Example: 2-1 Loops and Concurrency

Two scripts demonstrate the `message`, `steer`, and `query` entries, followed by `steer`, `INTERRUPT`, pause/resume, and cancellation experiments. The complete code, configuration, prompts, inputs, and example outputs are included below.

Use Python 3.13 or later, uv, and an environment where the Flowing CLI and package are available. Model-backed runs also require your own `DEEPSEEK_API_KEY` in the shell; no real credential is included here. Create the relative files exactly as shown under a fresh working directory, and run the commands from that directory.

## Reproduce

### Compare the three entries

Run the following command from the working directory containing the inline files:

```console
$ uv run python demo_entries.py
```

### Inspect loop mechanics

Run the following command from the working directory containing the inline files:

```console
$ uv run python demo_mechanics.py
```

The mechanics script intentionally uses asynchronous waits, a 20-second shell sleep, and cancellation. Timing and model-generated text can vary. The note filenames used by the directory-listing interaction are specified below; their bodies are not read.

## Complete inline materials

Each block below contains the complete content of a relative file used by this example. Output blocks are sample transcripts; model responses and host-specific details can vary.

### `demo_entries.py`

```python
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
```

### `demo_entries_output.txt`

```text
message() enqueued, message id=1 (the caller does not wait for the turn result)
Turn has started (current_turn is not None)
steer() delivered (STEER priority: visible to the current turn, no interruption)
Turn has ended
query() waited for TurnResult: status=completed
first 80 chars of final_text: Glob confirms there are 2 files in the notes directory: `使用说明.md` and `路线图.md`.
── message tree (head → root, reverse chronological; pri=STEER marks a steer message) ──
  9 provider  pri=NORMAL turn_end=True Glob confirms there are 2 files in t…使用说明.md` and `路线图.md`.
  8 tool      pri=NORMAL turn_end=False ./notes/路线图.md
  7 provider  pri=NORMAL turn_end=False 
  6 user      pri=NORMAL turn_end=False Use glob again to confirm how many f…nswer in one sentence.
  5 provider  pri=NORMAL turn_end=True The notes directory contains 2 files…ries. (steer received)
  3 user      pri=STEER  turn_end=False Additional requirement: end your ans…th '(steer received)'.
  4 tool      pri=NORMAL turn_end=False ./notes/路线图.md
  2 provider  pri=NORMAL turn_end=False 
  1 user      pri=NORMAL turn_end=False Use glob to look at the notes direct… many files are in it.
```

### `demo_mechanics.py`

```python
"""Queue-and-loop mechanics experiments: steer / INTERRUPT / pause-resume / cancel.

Each experiment prints "mechanics behavior + traces on the tree".
Run: uv run python demo_mechanics.py
"""
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

    # ── Experiment 1: a steer message is absorbed by the current turn (no interruption) ──
    print("== Experiment 1: steer (STEER visible to the current turn, no interruption) ==")
    t = asyncio.create_task(agent.query("Count from 1 to 5, one number per line."))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await agent.steer("Supplement: after the count, add one line saying \"(steer received)\".")
    r = await t
    print(f"Turn status={r.status} (completed = not interrupted)")
    await asyncio.sleep(0.3)   # wait for the steer message to be attached with the batch

    # ── Experiment 2: INTERRUPT aborts the current turn during tool execution ──
    print("== Experiment 2: INTERRUPT (interrupt the current turn during tool execution) ==")
    t = asyncio.create_task(agent.query(
        "First run sleep 20 with bash, then answer: why is the sky blue? (one sentence)"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(8.0)   # first generation finished, bash sleep 20 in flight
    await agent.enqueue_message(Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="Stop! First answer: what is 2+2?")],
        priority=MessagePriority.INTERRUPT))
    r = await t
    print(f"Interrupted turn status={r.status} aborted={r.turn.aborted} (cancelled = interrupted)")
    while agent.current_turn is not None:   # wait for the new INTERRUPT turn to end
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)

    # ── Experiment 3: pause / resume ──
    print("== Experiment 3: pause / resume (suspend and resume the work loop) ==")
    agent.pause()
    t = asyncio.create_task(agent.query("Introduce yourself in one sentence."))
    await asyncio.sleep(1.5)
    # pause suspends the work loop at a checkpoint: the batch is dequeued and on the tree, but provider_gen has not started
    print(f"During pause, current_turn is not None: {agent.current_turn is not None}"
          f" (turn created and stopped at the checkpoint, no LLM call)")
    agent.resume()
    r = await t
    print(f"Status after resume: {r.status}")

    # ── Experiment 4: cancel (cooperative cancellation: in-flight generation races to abort, produced output kept) ──
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

    # ── Replay the tree cursor: traces of each mechanism above ──
    print("── message tree (head → root, reverse chronological) ──")
    for line in tree_lines(agent):
        print(line)
    await runtime.shutdown()


asyncio.run(main())
```

### `demo_mechanics_output.txt`

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
 10 provider  pri=NORMAL  turn_end=True 2+2 = 4. I'm the Q&A assistant
  9 user      pri=NORMAL  turn_end=False Introduce yourself in one sent
  7 user      pri=INTERRUPT turn_end=False Stop! First answer: what is 2+
  8 tool      pri=NORMAL  turn_end=False exit_code: 0 --- stdout --- --
  6 provider  pri=NORMAL  turn_end=False 
  5 user      pri=NORMAL  turn_end=False First run sleep 20 with bash, 
  4 provider  pri=NORMAL  turn_end=True 1 2 3 4 5 (steer received)
  3 user      pri=STEER   turn_end=False Supplement: after the count, a
  2 provider  pri=NORMAL  turn_end=True 1 2 3 4 5
  1 user      pri=NORMAL  turn_end=False Count from 1 to 5, one number 
```

### `main.py`

```python
"""Subproject entry for the parameters-and-setup() demo: launch imports this file and awaits main()."""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh",
               resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # provide before mount: values that affect assembly must be provided before mounting
    # (chained semantics: look up along the parent chain)
    runtime.provide("timezone", "Asia/Shanghai")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        # CLI --key value pairs pass through launch verbatim into main(**kwargs);
        # main then decides which parameters go to mount (→ creation pipeline → setup(**args))
        kwargs: dict = {}
        if user_name is not None:
            kwargs["user_name"] = user_name
        if locale != "zh":
            kwargs["locale"] = locale
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

### `model-tags.yaml`

```yaml
# tag → model entry name mapping (single value: one tag maps to exactly one entry).
tags:
  default: deepseek-flash
```

### `models.yaml`

```yaml
# model entries: one entry = one concrete model (bound to one provider entry).
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### `providers.yaml`

```yaml
# provider entries: one entry = one API key identity.
# {{env.VAR}} is substituted at load time; a missing variable becomes an empty
# string plus a warning (warnings.warn), and loading continues.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `root.fya`

```text
description: "Loop-mechanics experiment assistant: helps observe entry semantics and control traces."
model_tag: default
tools:
  - read
  - glob
  - bash   # the mechanics experiments need an interruptible stretch of tool execution (sleep)
---
$system_prompt:
You are the Q&A assistant for this project. The absolute path of the project
root is {{ env.PWD }} (always use this absolute path when calling file tools;
do not guess other directories). Use bash when the user asks you to run
commands, and use read / glob to inspect files. Keep your answers within
five sentences.
```

### `notes/使用说明.md` (empty fixture)

The recorded interaction uses this filename but never reads its contents. Create an empty file at this relative name.

### `notes/路线图.md` (empty fixture)

The recorded interaction uses this filename but never reads its contents. Create an empty file at this relative name.
