# 3-4 · Model Configuration and Context Budget

## Prerequisites

[0-1 First Agent](0-1-hello-agent.md) (model tags and model entries) and
[3-3 Provider Basics](3-3-provider-basics.md) (where measured `Usage` is
stored). Flowing must be installed, and `DEEPSEEK_API_KEY` must be set in the
environment. The configuration below keeps the environment-variable
placeholder instead of embedding a credential.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| `estimate_context_tokens()` | An observation API that combines the latest measured usage anchor with a heuristic estimate of newer content. |
| Measured anchor | The nearest PROVIDER message with `usage` on the current message path. Content through that message is measured; later content is estimated. |
| `usage_ratio` | `tokens / context_window`. It is not clamped, and it is `None` when no context window is declared. |
| Observation window | A value to inspect, not an automatic action. A ratio approaching 1 signals that an application may consider compaction or cleanup. |

## Goals

This chapter shows model-tag design, runtime model switching, per-agent model
assignment, and how to use a context-budget estimate as an observation value.

## Main text

### Model selection: three operations

Model resolution has two hops: `model_tag` selects a tag mapping, and that
mapping selects a model entry. Three operations build on this:

1. **Define entries and tags.** Each tag maps to one model entry. Multiple tags
   can select the same entry, or different tags such as `fast` and `chat` can
   select entries for different purposes.
2. **Assign a model at declaration time.** An agent declaration can set
   `model_tag`; a subagent can use a dedicated model this way.
3. **Switch a model at runtime.** Assigning `agent.model_tag = "chat"`
   re-resolves the model and affects the next `provider_gen` call. Assigning a
   complete `ModelConfig` to `agent.model` is another way to replace the
   selected specification.

Fields such as `context_window` and `max_output_tokens` are model metadata.
Flowing does not impose a policy based on them; observation APIs and
composables can consume them.

### Context budget: measured anchor plus tail estimate

```python
estimate = agent.estimate_context_tokens()
estimate.tokens       # measured + estimated
estimate.measured    # measured portion through the latest usage anchor
estimate.estimated   # heuristic estimate for content after that anchor
estimate.usage_ratio  # tokens / context_window; unclamped, or None
```

The estimate is for observation, not billing; billing uses
`TurnResult.token_usage`. A `usage_ratio` above 1 is a valid overflow signal.
If the selected model entry has no `context_window`, the ratio is `None`. The
REPL `/status` and `/snapshot` commands report related health information.

### Positioning the budget: an observation window

Thresholds and compaction are policies; Flowing exposes measurements without
choosing when to act. A ratio approaching 1 can prompt an application to
consider compaction or cleanup. Forking a message tree and opening a summarized
root is a separate mechanism, while `use_compact` and `use_auto_compact` are
application policies; this chapter only observes the estimate.

## Out of scope

- Fork-based chain replacement and tree surgery are covered in
  [4-3 Message-Tree Surgery](4-3-message-tree-surgery.md).
- Compaction policies are covered in [5-3 Composables](5-3-composables.md).
- Configuration precedence and programmatic overrides are covered in
  [6-3 Operations](6-3-ops.md).

## Main example

The examples below are fully defined by the inline declarations, configuration,
programs, prompts, and sample outputs. Install Flowing and set
`DEEPSEEK_API_KEY` in the environment before running them. Keep the
`{{env.DEEPSEEK_API_KEY}}` configuration placeholder unchanged.

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
description: "Model configuration and context-budget demo."
model_tag: default
---
$system_prompt:
You are a concise English assistant. Keep answers within one sentence.
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
  context_window: 32000
deepseek-chat:
  provider: deepseek
  model: deepseek-chat
  context_window: 32000
```

`model-tags.yaml`:

```yaml
tags:
  default: deepseek-flash
  fast: deepseek-flash
  chat: deepseek-chat
```

`greeter.fya`:

```yaml
description: "Greeter that uses the model selected by the chat tag."
model_tag: chat
---
$system_prompt:
You are a greeter. Greet in one sentence.
```

`demo_model.py`:

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")

    print(f"(1) Root agent: model_tag={root.model_tag!r} -> model={root.model.model!r}")
    print(f"    provider entry binding: {root.model.provider!r}")

    root.model_tag = "chat"
    print(f"(2) Runtime root.model_tag = 'chat' -> model={root.model.model!r}")

    child = await root.create_subagent("@/greeter.fya")
    print(f"(3) Subagent greeter: declared model_tag={child.model_tag!r} "
          f"-> model={child.model.model!r}")
    await child.destroy()
    await runtime.shutdown()


asyncio.run(main())
```

Run `uv run python demo_model.py`. This example does not make a model call, so
its output is deterministic:

```text
(1) Root agent: model_tag='default' -> model='deepseek-v4-flash'
    provider entry binding: 'deepseek'
(2) Runtime root.model_tag = 'chat' -> model='deepseek-chat'
(3) Subagent greeter: declared model_tag='chat' -> model='deepseek-chat'
```

`demo_budget.py`:

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    estimate = agent.estimate_context_tokens()
    print(f"Initial (empty tree): tokens={estimate.tokens} "
          f"measured={estimate.measured} estimated={estimate.estimated} "
          f"ratio={estimate.usage_ratio}")

    for turn in range(1, 7):
        question = (
            f"Question {turn}: from another angle, describe the "
            "'context window' in one sentence."
        )
        await agent.query(question)
        estimate = agent.estimate_context_tokens()
        print(f"After round {turn}: tokens={estimate.tokens} "
              f"measured={estimate.measured} estimated={estimate.estimated} "
              f"ratio={estimate.usage_ratio:.4f}")

    await runtime.shutdown()


asyncio.run(main())
```

Run `uv run python demo_budget.py`. The six user inputs are generated by the
inline loop: “Question 1: from another angle, describe the 'context window' in
one sentence.”, continuing with the number incremented through 6. A recorded
sample output is:

```text
Initial (empty tree): tokens=18 measured=None estimated=18 ratio=0.0005625
After round 1: tokens=198 measured=198 estimated=0 ratio=0.0062
After round 2: tokens=344 measured=344 estimated=0 ratio=0.0107
After round 3: tokens=382 measured=382 estimated=0 ratio=0.0119
After round 4: tokens=428 measured=428 estimated=0 ratio=0.0134
After round 5: tokens=788 measured=788 estimated=0 ratio=0.0246
After round 6: tokens=799 measured=799 estimated=0 ratio=0.0250
```

The empty tree has no usage anchor, so its content is estimated. Once a turn
completes, its PROVIDER message provides measured usage and becomes the anchor.
The values above are one recorded live-model sample; prompt tokens and replies
can change the measurements. The declared context window is 32,000 tokens, so
this short sample remains far below a ratio of 1.

## Summary

1. Model selection uses two-hop resolution and supports tag design,
   declaration-time assignment, and runtime switching.
2. `estimate_context_tokens()` combines measured usage with a tail estimate;
   `usage_ratio` is unclamped, can be `None`, and is not a billing value.
3. The budget is an observation window. A ratio approaching 1 is a signal for
   an application to consider compaction, not an automatic policy.
