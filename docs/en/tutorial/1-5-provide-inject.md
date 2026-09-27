# 1-5 · provide and inject

## Prerequisites

[1-3 Parameters and setup()](1-3-agent-args-and-setup.md) (the `provide` /
`inject` calls in `setup()`). This chapter applies them to a parent Agent and
a greeter subagent.

## Glossary for this chapter

| Term | One-sentence definition |
|---|---|
| `provide(key, value)` | Registers a cross-layer shared runtime value on this node |
| `inject(key)` | Looks up a provided value along the parent chain, nearest first, ending at Runtime; raises `MissingProvideError` on a miss |
| provide chain | The lookup path formed by the `_parent_id` parent chain: a child Agent sees the values of its ancestors (multi-agent topology is covered in 3-1) |
| `MissingProvideError` | The exception raised when an inject walks to the top of the chain without a hit |

## Goals

Master the correct channel for cross-layer shared values: when to provide,
when to inject, along which path a value flows, and where the boundaries are.

## Main text

### Chained lookup: nearest first, ending at Runtime

`provide(key, value)` registers on this node; `inject(key)` starts from the
current node and walks up the parent chain level by level toward the root,
returning the first hit. Runtime is the end of the chain — a `provide` on the
root Agent is visible to every descendant in the tree. This gives two direct
corollaries:

- **Use inject for cross-layer sharing**: provide once at the root, and any
  subagent at any depth can inject it; sensitive values such as credentials
  should be provided at a **deep node** to narrow their visibility;
- **Same-key overwrite, live lookup**: re-providing a key overwrites it, and
  every inject walks the chain live — the regular channel for switching
  configuration at runtime (such as locale).

### Four boundaries

1. **Sensitive-data boundary**: injected values do not enter the message
   stream, the LLM context, or the disk — API keys / user identity go through
   this channel; but they are visible to descendants along the chain, so
   credentials belong at a deep node;
2. **A miss raises**: `inject` on a missing key → `MissingProvideError`
   (demonstration 3 in the main example);
3. **Values that affect assembly must be provided before `mount()`**
   (such as the timezone in 1-3);
4. Heavy resources shared across Agents (connection pools, indexes) do not go
   through the injection chain; they go through Resource
   (`runtime.register_resource`, mentioned briefly in Chapter 4-8).

### Relation to template evaluation

A provided value is not directly part of a template's context; to consume it,
a template must go through `self.xxx = self.inject(key)` in `setup()` to land
it on an instance attribute (template context = flattened instance
attributes; the evaluation system is covered in Chapter 4-9). The greeter's
`{{ locale }}` in this chapter comes from exactly this route.

## Out of scope

- Type-safe keys with `InjectionKey[T]` — Chapter 4-4;
- Resource registration and direct references outside the tree — Chapter 4-8;
- Full scenarios for switching values dynamically at runtime (providing inside
  hooks to rewrite behavior) — mentioned briefly in Chapter 4-6;
- Injection expressions at declaration sites (the binding layer's `specified`
  injected values) — Chapter 4-4.

## Main example

Project structure: the root Agent (orchestrator) calls
`self.provide("locale", locale)` in `setup()`; the greeter calls
`self.locale = self.inject("locale")` in
its own `setup()`, and its template branches by locale to pick the language.
Change the value with `--locale`, and the greeting language switches with it.

**Demonstration 1: `--locale en` → English greeting**:

```console
$ uv run flowing repl . --locale en
(agent-main)>>> Please dispatch the greeter to greet me.
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "greeter", "prompt": "Greet the user."}
[tool:completed] subagent-invoke -> Hello! How can I help you today?
Hello! How can I help you today?
(agent-main)>>> /exit
```

**Demonstration 2: default `zh` → Chinese greeting**:

```console
$ uv run flowing repl .
(agent-main)>>> Please dispatch the greeter to greet me.
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "greeter", "prompt": "Greet the user."}
[tool:completed] subagent-invoke -> 你好！
你好！
(agent-main)>>> /exit
```

Read these two interactions: each time, the greeter is a fresh instance created
only when invoked; its `locale` does not come from parameters (`args:` is
empty for it) but from an inject that walked up the parent chain at creation
time — change the provided value (`--locale en` vs the default `zh`), and the
subagent's behavior switches with it. The `prompt` field is required and must
be non-empty; an empty string returns `[tool:error] prompt is required and
must be non-empty: the task the subagent should work on`. This validation
failure is model-visible self-correction feedback, not a turn exception.

**Demonstration 3: inject miss** (the complete contents of `demo_boundary.py` follow):

```console
$ uv run python demo_boundary.py
inject miss -> MissingProvideError: Missing provide value for key: 'no_such_key'
```

## Complete example materials

Create the following relative files in a Flowing-enabled project. Set
`DEEPSEEK_API_KEY` in the environment before using the REPL. These declarations,
prompts, inputs, and outputs are all shown here; no separate example files are
needed to understand the data flow.

`main.py`:

```python
from flowing import Runtime


async def main(locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs = {"locale": locale} if locale != "zh" else {}
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
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

`root.fya`:

```yaml
description: "Orchestrator: provides locale and dispatches greeting tasks to the greeter."
model_tag: default
args:
  locale:
    type: string
    default: zh
tools:
  - subagent-invoke
subagents:
  - ./agents/greeter
---
$system_prompt:
You are the orchestrator. For a greeting request, call subagent-invoke for
greeter with a non-empty prompt and no extra parameters. Relay the greeting
verbatim.
---
$script:
async def setup(self, locale: str = "zh"):
    self.locale = locale
    self.provide("locale", locale)
```

`agents/greeter/agent.fya`:

```yaml
description: "Greeter: greets the user in the language matching the provided locale."
model_tag: default
---
$system_prompt:
You are the greeter. locale = {{ locale }}.
{% if locale == 'zh' %}Greet the user in Chinese.{% else %}Greet the user in English.{% endif %}
Output only the greeting itself.
---
$script:
async def setup(self):
    self.locale = self.inject("locale")
```

Input for the English run:

```text
Please dispatch the greeter to greet me.
/exit
```

```console
$ uv run flowing repl . --locale en
```

Expected behavior: the parent dispatches a fresh greeter; its injected locale
is `en`, so it returns an English greeting. A representative response is
`Hello! How can I help you today?`.

Input for the default Chinese run:

```text
请派 greeter 问候我。
/exit
```

```console
$ uv run flowing repl .
```

Expected behavior: with the default `locale: zh`, the greeter returns a
Chinese greeting. The exact wording is generated by the configured model.

The missing-key behavior is independently reproducible with this complete
Python snippet:

```python
import asyncio

from flowing import launch
from flowing.errors import MissingProvideError


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    try:
        agent.inject("no_such_key")
    except MissingProvideError as exc:
        print(f"inject miss -> MissingProvideError: {exc}")
    await runtime.shutdown()


asyncio.run(main())
```

Expected output:

```text
inject miss -> MissingProvideError: Missing provide value for key: 'no_such_key'
```

## Summary

1. provide registers; inject walks up the parent chain nearest first, ending at Runtime;
2. Same-key overwrite, live lookup — the regular channel for switching configuration at runtime;
3. Sensitive values go through the injection chain (not into messages / context / disk); credentials belong at a deep node to narrow visibility;
4. A miss raises `MissingProvideError`; templates consume injected values via an instance-attribute detour.
