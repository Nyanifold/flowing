# 3-3 · Provider Basics

## Prerequisites

[3-2 Context Assembly](3-2-context-assembly.md) (a Provider receives a
`Context`). Flowing must be installed in the Python environment, and the
`DEEPSEEK_API_KEY` environment variable must contain a valid credential. Keep
the `{{env.DEEPSEEK_API_KEY}}` placeholder in the configuration below; do not
put a credential directly in a document or configuration file.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| Provider | The execution endpoint of a model call. A provider adapter maps a `Context` to a vendor request and normalizes its response. |
| `provider_gen()` | The Agent entry point for a model call. It supports streaming and non-streaming modes. |
| `ProviderDelta` | A streaming update delivered to `on_provider_delta`; non-streaming calls also dispatch one complete delta. |
| `Usage` | Normalized token measurements attached to the `usage` field of a PROVIDER message. |

## Goals

This chapter explains the two call modes, the observation channel for deltas,
and where measured usage is stored.

## Main text

### Provider and adapter

A Provider performs a model call. `generate(context, model)` returns one
complete response, while `generate_stream(context, model)` yields response
deltas. In both cases, `Context` is the call input. A vendor adapter maps
message kinds and content to the vendor's request format, then maps response
fields back to Flowing's message and usage model. Changing the adapter does not
require changing Agent logic.

### Streaming and non-streaming

`provider_gen(stream=True)` is the default. Deltas arrive incrementally, and
Flowing accumulates them by `content_index`. If generation is interrupted,
content accumulated so far is finalized as a `partial=True` message and kept.
With `provider_gen(stream=False)`, the complete response arrives at once, but
Flowing still dispatches one synthesized delta. The delta format is shared by
both modes, so a subscriber can handle either mode through the same hook.
Programmatic calls made on behalf of the main turn should pass `by="_turn"`
when they need the main-turn source marker.

### Hook points

- `on_provider_delta` fires as each delta arrives. It is observational:
  changing the hook's local value does not rewrite Flowing's accumulated
  content. The `by` filter distinguishes the main turn (`"_turn"`) from a side
  channel (`"_side"`). A streaming interface can use this hook to display
  generated text.
- `before_provider_gen` can inspect or rewrite the assembled `Context` before
  a call.
- `on_provider_error` is the decision hook for call exceptions. Setting
  `ctx.can_continue = True` asks the current turn to retry; otherwise the turn
  ends with an error. [4-6 Errors and Control](4-6-errors-and-control.md) covers the
  full decision surface.

### Where usage measurements land

`Usage` contains `input`, `fresh_input`, `output`, `cache_read`, `cache_write`,
`reasoning`, `total_tokens`, and optional raw provider data. The adapter
normalizes these measurements. The authoritative stored value is the
`usage` field on the PROVIDER message, so it is persisted with that message
and remains available after recovery. The turn layer aggregates usage into
`TurnResult.token_usage` for billing. Context-budget observation also uses
the measured message usage; [3-4 Model Configuration and Context Budget](3-4-model-config-and-budget.md)
shows that calculation.

## Out of scope

- Vendor-specific adapters and their protocol details are outside this
  chapter.
- The full error taxonomy and the `on_provider_error` decision surface are
  covered in [4-6 Errors and Control](4-6-errors-and-control.md).
- Model configuration precedence and programmatic overrides are covered in
  [6-3 Operations](6-3-ops.md).

## Main example

The following self-contained example creates one context containing only the
system prompt, calls the same Provider once in each mode, and reports delta
counts and visible text. No user message is sent. Live model text, token
measurements, and delta segmentation can vary; the output below is an example
record, not a deterministic assertion. The callback counts all deltas but
prints no internal reasoning text.

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
description: "Provider demo assistant: minimal Q&A."
model_tag: default
---
$system_prompt:
You are a concise assistant. Always reply in English.
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

`demo_stream.py`:

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    deltas = []

    async def collect(agent_, delta):
        deltas.append(delta)
        return delta

    agent.hooks.on_provider_delta["_turn"](collect, by="diag")
    context = agent._assemble_context()

    for stream in (True, False):
        deltas.clear()
        print(f"== provider_gen(stream={stream}) ==")
        response = await agent.provider_gen(context, stream=stream, by="_turn")
        thinking_count = sum(delta.kind == "thinking" for delta in deltas)
        text_count = sum(delta.kind == "text" for delta in deltas)
        visible_text = "".join(
            delta.text for delta in deltas if delta.kind == "text"
        )
        print(
            f"  deltas={len(deltas)}; thinking={thinking_count}; "
            f"text={text_count}; finish={response.finish}; "
            f"message.usage attached={response.message.usage is not None}"
        )
        print(f"  visible text={visible_text!r}")

    await runtime.shutdown()


asyncio.run(main())
```

Run `uv run python demo_stream.py` from the project root after installing
Flowing and setting `DEEPSEEK_API_KEY` in the environment. The call input is
the system prompt shown above; there is no user-message input. A recorded
sample output is:

```text
== provider_gen(stream=True) ==
  deltas=68; thinking=59; text=9; finish=True; message.usage attached=True
  visible text='Understood. How can I help?'
== provider_gen(stream=False) ==
  deltas=1; thinking=0; text=1; finish=True; message.usage attached=True
  visible text='Understood. How can I help?'
```

The streaming path delivers deltas incrementally; the non-streaming path
delivers one synthesized delta. Both end with usage attached to the assembled
message. The exact counts and generated text depend on the provider response.

## Summary

1. `Context` is the Provider input; an adapter translates requests and
   normalizes responses.
2. Streaming emits incremental deltas, while non-streaming emits one complete
   delta; both use the same subscription contract.
3. `on_provider_delta` is observational, and the PROVIDER message's `usage`
   field is the durable measurement record.
4. `on_provider_error` is the decision hook for provider-call failures.
