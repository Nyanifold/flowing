# 5-3 · Composable: Mechanics Stay in the Core, Policies Live Here

## Prerequisites

Read [1-9 Introduction to Plugins and Composables](1-9-plugins-and-composables.md)
for an initial look at `use_prompt_until` and `use_system_reminder`, and
[4-6 Errors and Control](4-6-errors-and-control.md) for how `use_retry`
handles retry decisions. This chapter includes the complete runnable setup,
self-written Composable, direct tool-call inputs, and recorded results inline.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| Composable | An application-logic injection function that primarily targets an Agent: calling `use_xxx(agent, ...)` executes its function body |
| Common form | It may declare an extension hook point and register handlers (grouped with `by=`/`tags=`), attach Agent attributes, manage state, or perform other application logic |
| by-group management | Attached handlers can be removed as a group with the same `by` (`remove_by_owner`); this manages handlers and does not limit other Composable behavior |
| Framework boundary | "The framework provides mechanisms, not policies" — retry / compaction / interception rules all live in Composables and plugins |

## Goals

This chapter explains the usage and parameters of built-in Composables and
shows one common structure for writing your own. The core provides mechanisms,
while Composables carry application logic and policies.

`use_xxx(agent, ...)` is an ordinary Python function. The framework does not
restrict its function body, return value, or application-layer behavior. Choose
where to keep state based on its lifetime: transient state may live in a
closure or an Agent attribute. Keeping state in a closure created by that call
avoids collisions with Agent attribute names, but external code cannot access
it through the Agent. Agent attributes are available for external access and
management, but their names must avoid collisions. State that must persist can use Flowing's built-in
`agent.state.register()` or application-managed persistence and recovery;
closure variables and ordinary Agent attributes do not provide persistence by
themselves.

## Main text

### Built-in Composables: usage and parameters

| Composable | Hook point | Key parameters | Seen in |
|---|---|---|---|
| `use_retry` | `on_provider_error` (+ `on_retry` observation) | `max_retries` (default 3) / `base_delay` / whitelist of retryable errors | demonstrated in 4-6 |
| `use_system_reminder` | `before_turn` (`clean=True` adds `after_turn` cleanup) | `contents` (three states) / `message_interval` / `time_interval` / `clean` | demonstrated in 1-9 |
| `use_compact` / `use_auto_compact` | `after_provider_gen` / `before_provider_gen` | `threshold` / `head_tokens` / `tail_tokens` / `compact_template` | mechanics foundation in 4-3 |

The two replaceable parts of `use_prompt_until` — `predicate` and `message` —
are covered in 1-9.

### A common form for a self-written Composable

```python
import asyncio

from flowing.errors import Intercepted


def use_rate_limit(agent, *, max_calls: int = 5, window_seconds: float = 60.0) -> None:
    agent.hooks.declare("on_rate_limited", by="rate-limit", match_on="name")  # ① declare an extension observation point
    calls: list[float] = []
    async def _gate(agent_, tool_call):                                      # ② the policy itself
        now = asyncio.get_event_loop().time()
        calls[:] = [t for t in calls if now - t < window_seconds]
        if len(calls) >= max_calls:
            await agent_.hooks.on_rate_limited.dispatch(agent_, tool_call)   # observation first
            raise Intercepted(f"Rate limit exceeded: {max_calls} calls / {window_seconds:.0f} seconds")
        calls.append(now)
        return tool_call
    agent.hooks.before_tool_call(_gate, by="rate-limit", tags=["rate-limit"])  # ③ attach to a core point + group by
```

The example illustrates three design choices:

1. **Composable behavior is not restricted by the framework**: `use_xxx(agent, ...)`
   is an ordinary Python function. It can register handlers, attach Agent
   attributes, manage state, or perform other application-layer logic. This
   example keeps its rate-limit counter, which is only needed for
   the current runtime, in a closure.
2. **Manage handlers when needed**: this example groups handlers by `by` so
   `remove_by_owner` can remove them as a group. The same `use_xxx` can be
   called with different parameters; the framework does not deduplicate calls,
   and the Composable implementation determines how repeated calls combine.
   Built-in Composable handlers stack according to their registration semantics.
3. **Observation separated from decision**: observable events go through
   self-declared `on_*` points as fire-and-forget notifications. Decisions
   use `Intercepted` or rewriting at core points.

### Framework boundary

Error classification is a framework mechanism; `use_retry` is an application
policy. Hook points are mechanisms; `use_rate_limit` and `use_system_reminder`
are policies. **The core only does error classification, hook points, and
message flow** — every policy is replaceable, removable, and writable by you.

## Out of scope

- This chapter does not detail the internal compaction algorithm of
  `use_compact` / `use_auto_compact`.
- This chapter does not define the concrete interaction for approval and
  interception rules; Chapter 1-4 and the application design determine it.
- Each project determines its own conventions for publishing a Composable as
  a library.

## Main example

The recorded transcript below shows the results of five direct tool calls:

```console
$ uv run python demo_composables.py
== ① use_rate_limit(max_calls=3) injection ==
   5 call results: ['completed', 'completed', 'completed', 'blocked', 'blocked']
   on_rate_limited observed 2 over-limit calls (the 4th and 5th)
== ② remove_by_owner group removal ==
   removed 1 handler(s); one more call: status=completed
== ③ call a built-in Composable ==
   after use_system_reminder, before_turn matches 1 handler(s) (one way this built-in Composable mounts behavior)
```

Reading this transcript: this example installs its rate-limit handlers by
declaring an observation point, attaching to a core point, and grouping by
`by`; this is the structure of this example. `Intercepted` turns an over-limit call into a blocked result visible to
the LLM (explainable and retryable); once the whole group is removed, the
policy stops immediately and the Agent's behavior reverts.

**Complete runnable materials.**

Save each block under its heading in one Flowing project. The example uses
root-level filenames only. The provider key remains an environment-variable
placeholder; do not put credentials in the files.

### Runtime entry: `main.py`

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### Provider configuration: `providers.yaml`

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### Model configuration: `models.yaml`

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### Model tag: `model-tags.yaml`

```yaml
tags:
  default: deepseek-flash
```

### Agent definition: `root.fya`

```yaml
description: "Composable demo assistant: echo tool + self-written policy injection."
model_tag: default
tools:
  - ./echo.py
---
$system_prompt:
You are a demo assistant. Answer in one sentence.
```

### Echo tool: `echo.py`

```python
from pydantic import BaseModel

from flowing import ScriptTool


class EchoArgs(BaseModel):
    text: str


class EchoTool(ScriptTool):
    name = "echo"
    args_model = EchoArgs

    async def execute(self, *, text: str) -> str:
        return text
```

### Complete Composable: `rate_limit.py`

```python
import asyncio

from flowing.errors import Intercepted


def use_rate_limit(agent, *, max_calls: int = 5,
                   window_seconds: float = 60.0) -> None:
    agent.hooks.declare("on_rate_limited", by="rate-limit", match_on="name")
    calls: list[float] = []

    async def _gate(agent_, tool_call):
        now = asyncio.get_event_loop().time()
        calls[:] = [t for t in calls if now - t < window_seconds]
        if len(calls) >= max_calls:
            await agent_.hooks.on_rate_limited.dispatch(agent_, tool_call)
            raise Intercepted(
                f"Rate limit exceeded: {max_calls} calls / "
                f"{window_seconds:.0f} seconds. Please try again later.")
        calls.append(now)
        return tool_call

    agent.hooks.before_tool_call(
        _gate, by="rate-limit", tags=["rate-limit"])


def remove_rate_limit(agent) -> int:
    return agent.hooks.before_tool_call.remove_by_owner("rate-limit")
```

### Demonstration program and inputs: `demo_composables.py`

```python
import asyncio

from flowing import launch
from flowing.tool import ToolCall
from rate_limit import remove_rate_limit, use_rate_limit


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    print("== ① use_rate_limit(max_calls=3) injection ==")
    limited: list[str] = []
    use_rate_limit(agent, max_calls=3, window_seconds=60.0)

    async def observe(agent_, tool_call):
        limited.append(tool_call.name)
        return tool_call
    agent.hooks.on_rate_limited(observe, by="demo")

    results = []
    for i in range(5):
        result = await agent.tool_call(
            ToolCall(id=f"c{i}", name="echo", args={"text": f"shot {i}"}))
        results.append(result.status)
    print(f"   5 call results: {results}")
    print(f"   on_rate_limited observed {len(limited)} over-limit calls "
          "(the 4th and 5th)")

    print("== ② remove_by_owner group removal ==")
    removed = remove_rate_limit(agent)
    result = await agent.tool_call(
        ToolCall(id="c9", name="echo", args={"text": "recovered"}))
    print(f"   removed {removed} handler(s); "
          f"one more call: status={result.status}")

    print("== ③ call a built-in Composable ==")
    from flowing.composables import use_system_reminder
    use_system_reminder(agent, contents=["[reminder] from use_system_reminder"])
    hits = [h for h in agent.hooks.before_turn
            if getattr(h, "by", None) == "system-reminder"]
    print(f"   after use_system_reminder, before_turn matches {len(hits)} "
          "handler(s) (one way this built-in Composable mounts behavior)")
    await runtime.shutdown()


asyncio.run(main())
```

The programmatic inputs are five `ToolCall` objects named `c0` through
`c4`, each invoking `echo`, followed by `c9` after policy removal. Run
`uv run python demo_composables.py` from the working directory where these
inline files were saved. The recorded output above shows three calls
completing, two being blocked, and one completing after removal.

## Summary

1. A built-in Composable combines a hook point with parameters.
2. A custom Composable is a `use_xxx` function whose application-layer behavior
   is implementation-defined; declaring an observation point and attaching
   grouped handlers to core hook points is one common form. State placement
   depends on whether it must persist.
3. The same `use_xxx` can be called with different parameters. The framework
   does not deduplicate calls; their effects are determined by the function.
4. The framework keeps mechanisms in the core and leaves policies to
   Composables and plugins.
