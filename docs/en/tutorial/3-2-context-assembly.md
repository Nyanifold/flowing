# 3-2 · Context Assembly

## Prerequisites

Read [3-1 Message Tree and Loop](3-1-message-tree-and-loop.md) (the tree and
the cursor) and [1-5 provide and inject](1-5-provide-inject.md) first. This
page includes the complete configuration, prompts, programs, Parsable content,
inputs, and recorded outputs. The context inspection is offline; the mode
switching conversation calls a model to show different answer forms.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| `Context` | The sole input to `Provider.generate()`: three orthogonal fields — system_prompt / tools / messages |
| On-the-spot assembly | Re-assembled and re-evaluated before every model call — lazy evaluation is a correctness requirement, not a performance choice |
| Prompt block | A layered fragment of the system prompt: a `PromptBlock` registration declaration is evaluated into a `PromptSegment` at assembly time |
| Intent channel | The three `cache` values (static/dynamic/session) are cache-intent markers for the adapter only — the framework does not read them locally and does not cache |
| Mode switching | The idiomatic technique of registering two or more prompt-block sets and toggling them by tag (disable/enable_by_tag) |

## Goals

Master the mechanics side of context assembly: how the tree from 3-1 becomes
the provider's sole input, plus prompt layering and mode switching.

## Main text

### Context: three orthogonal fields

| Field | Contents |
|---|---|
| `system_prompt` | An ordered list of `PromptSegment` objects that preserves cache marks. |
| `tools` | Declarations for currently enabled tools. |
| `messages` | The message path from the root to the current head. |

The adapter's mapping is a mechanical, field-by-field conversion (never merge
and then split back apart). **Fresh assembly, zero caching**: every assembly
re-evaluates all Parsables, re-collects the message path, and regenerates tool
definitions — environment variables and instance attributes always take their
latest values.

### prompt_blocks: layered registration

`agent.prompt_blocks` is a layered list concatenated in registration order:

- **`[0]` is a lazy reference block injected by the framework** (its content is
  the template `{{ self.system_prompt }}`, `by="core"`): change the class
  attribute `system_prompt` in `setup()` and the next assembly reflects it
  automatically; **do not remove it** (the system prompt would vanish, and the
  framework provides no fallback).
- `append("example", "Prompt text", cache="static", by="example", tags=["mode"])`
  registers a block; strings are normalized to Parsables and are not evaluated
  at registration time.
- Management operations include `disable_by_tag`, `enable_by_tag`, and
  `remove_by_owner`. **The idiomatic mode switch is to register two sets of
  blocks and toggle them by tag** (as in the main example).
- Dynamic content goes into Parsable template references (e.g.
  `{{ current_mode }}`) and is evaluated at assembly time. **Anti-patterns**:
  adding or removing blocks every turn (breaks prefix caching); high-frequency
  values (the current time) should go through the message channel (append-style
  injection in `before_turn` — the reminder in 1-9 does exactly this).

### `cache` is only an intent channel

`cache="static" / "dynamic" / "session"` are cache-optimization hints for the
provider adapter (the Anthropic family, for example, sets prefix-cache marks
only on `static` segments); the framework does not read them locally and does
not skip evaluation based on them.

### before_provider_gen: rewriting the whole Context

The assembled product passes through the `before_provider_gen` hook before it
is sent to the provider — a handler can rewrite all three fields, as the complete
offline demo below shows. Rewriting `messages` is not recommended
(the change is not persisted and is lost at the next assembly).

## Out of scope

- Chapter 4-9 covers the five Parsable forms and the full render-context evaluation system.
- Chapter 3-4 covers budget observation with `ContextUsageEstimate`.
- Chapter 3-3 explains how the adapter consumes the three fields.

## Main example

**Demo 1: Context structure, Parsable, and hook rewriting** (offline; no model call):

```console
$ uv run python demo_context.py
① Context: three orthogonal fields:
  system_prompt: [('system_prompt', 'dynamic'), ('mode-explain', 'static')]
  tools: ['switch-mode']
  messages: 0 (root → head path)
② Common Parsable forms (literal / template / file reference):
  literal        : 'hello'
  template       : 'Current mode: explain'
  file reference : 'Assemble first, then ask; context is a view, not a warehouse.'
  live evaluation: same template after changing current_mode → 'Current mode: experiment mode' (latest value)
③ before_provider_gen rewriting Context:
  last segment after rewrite: marker = '[rewrite marker]'
  [0] core reference block still present: system_prompt
```

When the persistent state is empty, Demo 1 reports zero messages. Reusing
existing session state changes that count, so zero applies only to an empty
state; with persisted messages, the assembled path determines the result.

**Demo 2: Mode switching (prompt blocks toggled by tag)**:

```console
(agent-main)>>> What is a message tree? Explain with bullet points.
[thinking] (reasoning trace omitted)
A message tree is a hierarchical structure in which each message is linked to a parent message.
- The original message is the root; replies become child nodes, so the conversation branches instead of forming a flat list.
- Explicit metadata, such as email reply headers or a chat thread ID, can establish the links.
- Threaded views use the structure to follow topics and expand or collapse branches.
(agent-main)>>> Switch to poem mode and answer the same question.
[thinking] (reasoning trace omitted)
[tool_call] switch-mode {"mode": "poem"}
[tool:completed] switch-mode ->
On hills, a signal tree falls to warn;
on screens, each reply grows a branch.
Words follow the links that bind them,
four lines map the shape they form.
(agent-main)>>> Switch back to explain mode and answer the same question.
[thinking] (reasoning trace omitted)
[tool_call] switch-mode {"mode": "explain"}
[tool:completed] switch-mode ->
A message tree is a hierarchical structure of messages where each message is attached to the one it replies to or derives from.
- Structure: The original message is the root, and each reply becomes a child node, so a conversation branches rather than sitting in a flat list.
- How it is built: Links can come from explicit metadata, such as email reply headers or a chat thread ID.
- Why it matters: It supports threaded views in email, forums, and chat by making branches easy to follow, expand, or collapse.
```

Reading this session: the same question receives three answer shapes, and the
shape difference comes entirely from enabling/disabling prompt blocks
(`switch-mode` internally performs `disable_by_tag("mode")` +
`enable_by_tag(mode)`). A mode is the state of which block is enabled, not a
collage of yet another prompt.

### Complete reproduction material

The following blocks include all configuration, prompts, programs, and Parsable
content needed to reconstruct both demos. The relative filenames are labels for
creating these inline contents; no directory change is required. The live model
conversation requires a reader-supplied `DEEPSEEK_API_KEY`; no credential is
included here.

```python
# main.py
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

```yaml
# providers.yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

```yaml
# models.yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

```yaml
# model-tags.yaml
tags:
  default: deepseek-flash
```

```yaml
# root.fya front matter and body
description: "Mode-switching answer assistant: layered prompt blocks + mode switching."
model_tag: default
tools:
  - ./tools/switch_mode.py
---
$system_prompt:
You are a mode-switching answer assistant. Current mode: {{ current_mode }}.
When the user asks to switch modes, use switch-mode to complete the switch,
then answer the question again in the new mode.
---
$script:
from flowing import Parsable


async def setup(self):
    self.current_mode = "explain"
    self.prompt_blocks.append(
        "mode-explain",
        Parsable("[Explain mode] Answer with clear bullet points, at most three."),
        cache="static", by="mode-switcher", tags=["mode", "explain"],
    )
    self.prompt_blocks.append(
        "mode-poem",
        Parsable("[Poem mode] Answer in the form of a modern poem, at most four lines."),
        cache="static", by="mode-switcher", tags=["mode", "poem"],
    )
    self.prompt_blocks.disable_by_tag("poem")
```

```python
# tools/switch_mode.py
from typing import Literal

from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class SwitchModeArgs(BaseModel):
    mode: Literal["explain", "poem"] = Field(
        description="Target mode: explain=bullet-point explanation; poem=modern poem"
    )


class SwitchMode(ScriptTool):
    """Switch answer mode and update current_mode."""

    name = "switch-mode"
    args_model = SwitchModeArgs

    async def execute(self, *, mode: str, caller: Agent) -> dict:
        caller.prompt_blocks.disable_by_tag("mode")
        caller.prompt_blocks.enable_by_tag(mode)
        caller.current_mode = mode
        return {"mode": mode, "current_mode": caller.current_mode}
```

```text
# notes/motto.md
Assemble first, then ask; context is a view, not a warehouse.
```

```python
# demo_context.py
import asyncio

from flowing import Parsable, launch
from flowing.context import PromptSegment


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    context = agent._assemble_context()
    print("① Context: three orthogonal fields:")
    print(f"  system_prompt: {[ (segment.name, segment.cache) for segment in context.system_prompt ]}")
    print(f"  tools: {[ tool.name for tool in context.tools ]}")
    print(f"  messages: {len(context.messages)} (root → head path)")

    print("② Common Parsable forms (literal / template / file reference):")
    print(f"  literal        : {Parsable('hello').resolve(agent)!r}")
    print(f"  template       : {Parsable('Current mode: {{ current_mode }}').resolve(agent)!r}")
    print(f"  file reference : {Parsable('$./notes/motto.md').resolve(agent)!r}")
    agent.current_mode = "experiment mode"
    print("  live evaluation: same template after changing current_mode → "
          f"{Parsable('Current mode: {{ current_mode }}').resolve(agent)!r} (latest value)")

    async def mark(agent_, context_):
        context_.system_prompt.append(
            PromptSegment(content="[rewrite marker]", cache="dynamic", name="marker")
        )
        return context_

    agent.hooks.before_provider_gen(mark, by="demo")
    rewritten = await agent.hooks.before_provider_gen.dispatch(
        agent, agent._assemble_context()
    )
    print("③ before_provider_gen rewriting Context:")
    print(f"  last segment after rewrite: {rewritten.system_prompt[-1].name} = "
          f"{rewritten.system_prompt[-1].content!r}")
    print(f"  [0] core reference block still present: {rewritten.system_prompt[0].name}")
    await runtime.shutdown()


asyncio.run(main())
```

The complete live-demo input is:

```text
What is a message tree? Explain with bullet points.
Switch to poem mode and answer the same question.
Switch back to explain mode and answer the same question.
```

Set a credential placeholder and start the interactive entry point. Replace the
placeholder with your own credential; do not put credentials in this page.

```console
$ export DEEPSEEK_API_KEY="<your-api-key>"
$ uv run flowing repl .
```

The conversation above shows the input, tool calls, and resulting answers.
Model-generated prose may vary; the stable behavior is that each tool call
enables the requested mode tag.

## Summary

1. The three orthogonal fields of `Context` form the provider's sole input, and fresh assembly without caching is a correctness requirement.
2. Prompt blocks are registered in layers, the `[0]` core reference block must remain, and mode switching toggles blocks by tag.
3. `cache` is only an intent channel for the adapter, while dynamic content goes through Parsable template references.
4. `before_provider_gen` can rewrite the whole `Context`, although rewriting `messages` is not recommended.
