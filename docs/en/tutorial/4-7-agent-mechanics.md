# 4-7 · Agent Full Mechanics

## Prerequisites

[3-1 Message Tree and Loop Mechanics](3-1-message-tree-and-loop.md) (checkpoint
semantics), [4-6 Errors and Control](4-6-errors-and-control.md) (the
cancellation decision surface). The complete four-experiment program and its
configuration are included below.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| `_dequeue` override point | The dequeue extension point: it builds the consumption batch for one logical turn (default = the leading urgent run at the queue head + the first non-urgent message) — the landing point for scheduling policies such as drain merge or grouping by source |
| `_run_turn` inner layer | The turn execution body: checkpoint sequence → provider_gen → parallel tool batch → final assembly of `TurnResult` |
| watch channel | The observation mechanism in which `Agent.__setattr__` intercepts assignments and notifies with a `FieldUpdate(new, old)` snapshot on a fire-and-forget basis (observation only; rewriting is ineffective) |
| side channel (side_query) | An independent call that bypasses the queue, the tree, and persistence — context is assembled on the spot, and the result goes only to the caller |

## Goals

The full mechanics of the Agent class: within the standard loop, which other
mechanisms this class still holds — a complete inventory from the
implementer's perspective.

## Main text

### The work loop and the dequeue extension point

Each iteration of the resident work loop: `msgs = await self._dequeue()` →
`_run_turn(msgs, waiters)`. `_dequeue` is an **explicit extension point**
(overriding handles "multiple / policy", while the `on_dequeue` hook handles
"observation / transformation"). Note the override semantics: the loop reads
the attribute once per iteration — a patch takes effect from the next
dequeue; **restore does not recall a dequeue call already in flight** (the
loop is still blocked in the old method's `wait_not_empty`; the "batch of 1"
in demo ② is the empirical evidence of this phenomenon).

### The _run_turn inner layer (order is the invariant)

Create `TurnContext` → set `current_turn` → `before_turn` (if intercepted, the
batch is discarded without entering the tree) → append each message of the
batch to the tree → inner loop (checkpoints: pause gate → urgent absorption →
abort determination → `provider_gen` → **all tool_calls within the same
response execute in parallel** (asyncio.gather; results are appended to the
tree in response order) → finish determination) → `finally`: release
`current_turn` → `after_turn` (the only finalization point on all paths) →
`build_turn_result` assembly → write `last_result` → resolve all waiters (a
batch merged by drain shares the same `TurnResult`).

`TurnResult` construction rules (demo ②): `token_usage` sums
`TurnContext.usages` field by field (raw usage is not aggregated), and is
`None` when there are no successful calls; `turn.message_ids` traces back to
the messages appended to the tree in this turn.

### The __setattr__ / watch mechanism in full

Every instance attribute assignment: build a `FieldUpdate(name, old, new)`
snapshot → fire-and-forget notification (not awaited; exceptions do not
affect the assignment) → write. Key points: the watcher receives **a
self-consistent snapshot taken at the moment of assignment**, and execution
order is not guaranteed; it is observation only (rewriting `new` is
ineffective); state writes do not trigger it; `_`-prefixed skeleton fields do
not pass through it; assigning `model_tag` additionally triggers
re-resolution (and does not fire the model watcher a second time).

### Tour of all entry points (mechanism foundations)

| Entry | Foundation |
|---|---|
| `query` | packaging + enqueue + binding a waiting Future (`_pending_turns`) |
| `message` / `enqueue_message` | pure enqueue (`on_enqueue` may reject or rewrite) |
| `steer` | STEER-priority message (absorbed at checkpoints; 3-1) |
| `side_query` | bypasses the queue: on-the-spot assembly + non-streaming call + `by="_side"`, **zero tree trace** (demo ④) |
| `invoke_subagent` | two-phase pipeline (2-2) + parent hooks |

### Instance mechanics of the state bag

Every set/delete on `agent.state` / a named bag: JSON validation (fail fast) →
update the `_persisted` in-memory authority → `submit` write-through
(write-behind queueing) → appending rows past the threshold triggers a
whole-file compaction request (the drain barrier from 4-1 is pinned before
compaction).

### Runtime visibility of the snapshot projection

`current_turn` is non-None only while a turn is executing (visible to the
snapshot layer during the same period); turn finalization releases the
identity token before running `after_turn` — during the hook, this field is
already None in the snapshot. The boundary between observation and control:
**the snapshot is an observation channel, not a basis for control**.

## Out of scope

- The cancellation decision surface and the full semantics of cancellation — 4-6;
- The subagent catalog / binding layer — 4-4;
- The recovery pipeline — 4-1.

## Main example

`demo_internals.py` runs four experiments; the complete recorded console
output is included below:

```console
$ uv run python demo_internals.py
== ① Custom _dequeue: drain merge ==
   [_dequeue] drain merge: batch of 3 messages into one turn
== ② Turn finalization: TurnResult construction ==
   [_dequeue] drain merge: batch of 1 messages into one turn
   status=completed turn.message_ids=['5', '6']
   token_usage aggregate=224 (sum over TurnContext.usages field by field)
== ③ watch attribute linkage ==
   watch(locale) received assignment events: [(None, 'zh'), ('zh', 'en')]
== ④ side_query side channel ==
   side_query returned='ok'; tree nodes 6 → 6 (unchanged = zero trace from the side channel)
```

Reading the four experiments: ① one dequeue consumes three messages (a
minimal implementation of the drain-merge policy); ② the "batch of 1" is
phenomenological evidence of the restore semantics — the work loop is still
blocked in the wait of the old dequeue method; the message_ids and the
aggregated usage of the same turn show the construction rules of
`TurnResult`; ③ the `(old, new)` snapshot sequence is the watch contract
(old=None on the first assignment); ④ demonstrates the zero trace of the
side channel — the total node count is unchanged, nothing is appended to the
tree, and nothing is persisted.

### Complete runnable materials

Save each block under its displayed filename in one working directory. The
program makes provider calls for its query and side-query experiments, so set
`DEEPSEEK_API_KEY` in the environment; the credential value is not included.
Provider-generated token counts and responses can vary.

`main.py`:

```python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh",
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
        if locale != "zh":
            kwargs["locale"] = locale
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

`root.fya`:

```yaml
description: "Agent mechanics demo assistant: minimal Q&A."
model_tag: default
---
$system_prompt:
You are a concise English assistant. Answer in one sentence.
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

`demo_internals.py`:

```python
import asyncio
import types

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    print("== ① Custom _dequeue: drain merge ==")
    original = agent._dequeue

    async def drain_all(self):
        await self._message_queue.wait_not_empty()
        batch = await self._message_queue.drain_all()
        print(f"   [_dequeue] drain merge: batch of {len(batch)} messages into one turn")
        return batch

    agent._dequeue = types.MethodType(drain_all, agent)
    for text in ["First sentence", "Second sentence", "Third sentence"]:
        await agent.message(text)
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)

    print("== ② Turn finalization: TurnResult construction ==")
    result = await agent.query("Introduce yourself in one sentence.")
    print(f"   status={result.status} turn.message_ids={result.turn.message_ids}")
    print(f"   token_usage aggregate="
          f"{result.token_usage.total_tokens if result.token_usage else None} "
          "(sum over TurnContext.usages field by field)")
    agent._dequeue = original

    print("== ③ watch attribute linkage ==")
    seen: list[tuple] = []
    agent.watch("locale", lambda new, old: seen.append((old, new)))
    agent.locale = "zh"
    agent.locale = "en"
    await asyncio.sleep(0.5)
    print(f"   watch(locale) received assignment events: {seen}")

    print("== ④ side_query side channel ==")
    before = len(agent._messages)
    side = await agent.side_query("Reply with only: ok")
    after = len(agent._messages)
    print(f"   side_query returned={side!r}; tree nodes {before} → {after} "
          "(unchanged = zero trace from the side channel)")
    await runtime.shutdown()


asyncio.run(main())
```

Run it with `uv run python demo_internals.py`. The recorded output is shown
above; the full user inputs are the three messages in the program.

## Summary

1. `_dequeue` is the extension point for scheduling policies; both override and restore take effect from "the next dequeue";
2. The `_run_turn` inner layer: checkpoint sequence + parallel tool batch + finally finalization assembly;
3. watch is snapshot observation of assignment events; side_query is a zero-trace side channel;
4. state writes go through write-through + threshold compaction; snapshots observe only and never control.
