# 4-6 · Errors and Control

## Prerequisites

[1-4 Hooks basics](1-4-hooks-basics.md) (interception and observation),
[2-3 Writing a ScriptTool](2-3-write-script-tool.md) (the error result
channel), [3-1 Message tree and loop mechanics](3-1-message-tree-and-loop.md)
(checkpoint semantics). The complete demonstration program and its
configuration are included below. It injects the first two provider failures;
the successful calls use the configured provider.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| Three error channels | Tool business errors (LLM-visible error artifact) / programming errors (raised on the framework error channel) / `Intercepted` (hard block → blocked) |
| Retryable classification | `RateLimitedError` / `ServerError` / `NetworkError` / `ProviderTimeoutError` belong to the retryable class — **classification is a statement of fact; retry is a policy** |
| `can_continue` | Decision field of `on_provider_error`: `False` (default) ends the turn with an error; `True` re-sends provider_gen within the same turn |
| Cancellation semantics | `cancel` (whole agent) / `cancel_children` / `cancel_by_tag` / `abort_turn` (terminates only the current turn) / `stop` (forceful, overridable) — cooperative signals that the execution body responds to at checkpoints |
| `before_cancel` interception | Handler raises `Intercepted` → cancellation is prevented, the signal is not set, and the exception propagates to the caller of `cancel()` |

## Goals

The complete picture of error classification and control flow: each of the
three error channels takes a distinct route; provider errors have a decision
point; cancellation is a cooperative signal and can be blocked.

## Main text

### The three error channels

| Channel | Form | Destination |
|---|---|---|
| Tool business error | `ToolResult(status="error")` | Visible to the LLM and self-correctable; **triggers no error hook** (demonstrated in 2-3) |
| Programming error | Framework exception (`FlowingError` hierarchy) | Propagates directly; there is no catch-all hook (import-time typos deliberately use the built-in `ValueError`) |
| Hard block | `raise Intercepted` | The current operation is voided → a blocked result with the reason (demonstrated in 1-4) |

The registry family: all three registries (Tool / agent type / Skill) raise
`RegistryNotFoundError` on resolution misses and `RegistryConflictError` on
registration conflicts; the binding-layer alias exceptions
(`UnknownToolError` / `EntryNameConflictError`) form a separate layer from
these.

### Provider errors and on_provider_error

Errors during provider invocation are **always dispatched through
`on_provider_error`** (including `ContextLengthError` — re-sending it
unchanged necessarily reproduces the failure, but compressing history /
switching to a larger-window model / mere observation are all legitimate
handler dispositions). The framework reads exactly one field: `can_continue`
(default `False` → the turn ends with an error and the agent survives;
`True` → re-sent within the same turn). **`use_retry` is merely an optional
handler attached to this channel** — without it the channel still dispatches
as usual, only no one rewrites the decision field; `on_retry` is a pure
observation channel (an observer crashing does not affect the retry
decision, which is exactly what the code of demo ① does).

### The full cancellation semantics

Granularity family: `abort_turn()` (terminates only the current turn) <
`cancel_by_tag()` / `cancel_children()` < `cancel()` (the whole agent);
`stop()` is the forceful entry point reserved for subclass overrides (by
default equivalent to cancel); `pause()` / `resume()` (including the
`_recursive` subtree variants) suspend the work-loop checkpoints.
Cancellation is a **cooperative signal**: at a checkpoint the execution body
decides whether to stop immediately / ignore the signal and run to
completion / wrap up and return a partial result (the partial preservation
in experiment 4 of 3-1 is exactly this). **`before_cancel` can prevent
cancellation**: `Intercepted` propagates to the caller of `cancel()` and the
signal is not set — the standard usage for non-interruptible scenarios such
as an already-committed payment (demonstrated in demo ②).

## Out of scope

- Implementation details of the checkpoint sequence — Chapter 4-7;
- The provider-specific mapping from transport failures to error classes;
- The full parameter sets of `use_retry` / `use_compact` — Chapter 5-3.

## Main example

`demo_control.py`: FlakyProvider raises `RateLimitedError` on its first two
calls (the third delegates to DeepSeek); `use_retry` decides the retries;
`on_retry` observes only. This demo injects provider failures, and its
complete recorded console output is included below. The LLM-generated reply
may vary between runs:

```console
$ uv run python demo_control.py
① Rate-limit retry: status=completed
   on_retry observed: [{'attempt': 1, 'error': 'RateLimitedError'}, {'attempt': 2, 'error': 'RateLimitedError'}]
   FlakyProvider actual calls: 3 (2 failures + 1 success)
② cancel intercepted by before_cancel: Intercepted(payment already committed: this turn cannot be cancelled)
   signal not set — the cancellation never happened (standard usage for non-interruptible scenarios)
③ abort_turn: status=cancelled (turn terminated, agent alive)
   next turn as usual: status=completed final='Still here.'
```

Reading this transcript: the mechanism point of ① is "classification is
fact, retry is policy" — `RateLimitedError` is merely labeled retryable;
what actually keeps the turn alive is `can_continue=True` written by
`use_retry`; the snapshot dict received by `on_retry` is a pure observation
channel. The interception semantics of ② are "cancellation prevented", not
"rollback after cancellation" — the signal was never set. The difference
between `abort_turn` and `cancel` in ③ is granularity: only the current
turn dies, and the agent takes the next turn as usual.

### Complete runnable materials

Save each block under its displayed filename in one working directory. Python
3.13+, Flowing, and the DeepSeek provider dependencies must be installed.
Set `DEEPSEEK_API_KEY` in the environment; the key itself is deliberately not
included. Provider-generated reply text may vary from the recorded transcript.

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
description: "Errors-and-control demo assistant: equipped with bash for cancellation."
model_tag: default
tools:
  - bash
---
$system_prompt:
You are a concise demo assistant; keep answers to one sentence.
```

`providers.yaml`:

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
flaky:
  adapter: flaky
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

`demo_control.py`:

```python
import asyncio

from flowing import launch
from flowing.composables import use_retry
from flowing.errors import Intercepted, RateLimitedError
from flowing.message import TextBlock, ToolCallBlock
from flowing.model import ModelConfig
from flowing.providers import Provider, register_provider
from flowing.providers.deepseek import DeepSeekProvider


@register_provider
class FlakyProvider(Provider):
    name = "flaky"
    calls = 0

    async def generate(self, context, model):
        type(self).calls += 1
        if type(self).calls <= 2:
            raise RateLimitedError(retry_after=0)
        real = DeepSeekProvider(self.config)
        response = await real.generate(context, model)
        message = response.message
        if message is not None and not any(
                isinstance(block, ToolCallBlock) for block in message.content
        ) and not any(
                isinstance(block, TextBlock) and block.text.strip()
                for block in message.content):
            message.content.append(TextBlock("(thinking only, no body text)"))
        return response


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    agent.model = ModelConfig(model="deepseek-v4-flash", provider="flaky")

    retries: list[dict] = []
    use_retry(agent, max_retries=3, base_delay=0.1)

    async def observe(agent_, snapshot):
        retries.append({"attempt": snapshot["attempt"],
                        "error": type(snapshot["error"]).__name__})
        return snapshot
    agent.hooks.on_retry(observe, by="demo")

    result = await agent.query("Introduce yourself in one sentence.")
    print(f"① Rate-limit retry: status={result.status}")
    print(f"   on_retry observed: {retries}")
    print(f"   FlakyProvider actual calls: {FlakyProvider.calls} "
          "(2 failures + 1 success)")

    async def guard(agent_, context):
        raise Intercepted("payment already committed: this turn cannot be cancelled")
    agent.hooks.before_cancel(guard, by="guard")
    try:
        await agent.cancel()
        print("② cancel was NOT blocked (unexpected!)")
    except Intercepted as exc:
        print(f"② cancel intercepted by before_cancel: Intercepted({exc})")
        print("   signal not set — the cancellation never happened "
              "(standard usage for non-interruptible scenarios)")
    agent.hooks.before_cancel.remove_by_owner("guard")

    task = asyncio.create_task(
        agent.query("Run bash sleep 10 once more, then answer."))
    while agent.current_turn is None:
        await asyncio.sleep(0.05)
    await asyncio.sleep(3.0)
    agent.abort_turn()
    result = await task
    print(f"③ abort_turn: status={result.status} (turn terminated, agent alive)")
    result = await agent.query("Still there?")
    print(f"   next turn as usual: status={result.status} "
          f"final={result.final_text[:12]!r}")
    await runtime.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

The two query inputs, `Introduce yourself in one sentence.`,
`Run bash sleep 10 once more, then answer.`, and `Still there?`, are all
included in the program above. Run it with `uv run python demo_control.py`.

## Summary

1. Three error channels: the error artifact (LLM-visible) / programming
   errors (propagated) / Intercepted (blocked);
2. Provider errors are always dispatched through `on_provider_error`, and
   `can_continue` is the decision field; `use_retry` is a policy, not a
   mechanism;
3. Cancellation is cooperative and granularity-structured; the Intercepted
   from `before_cancel` prevents cancellation and propagates;
4. Crashes in observation channels such as `on_retry` do not affect the main
   decision.
