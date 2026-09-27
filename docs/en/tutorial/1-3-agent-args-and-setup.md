# 1-3 · Parameters and setup()

## Prerequisites

Chapter [0-1 The First Agent](0-1-hello-agent.md) explains how `--key value` passes
through `launch` to `main(**kwargs)` and demonstrates the run-time assignment
`root.user_name = ...`. This chapter embeds the complete entry point, Agent
declaration, model configuration, prompt, user inputs, and example outputs.
Save them in a project directory you create before running the commands.

## Glossary for this chapter

| Term | One-sentence definition |
|---|---|
| `args:` declaration | The `args:` declaration in a `.fya` header defines instantiation parameters in expanded JSON Schema form or shorthand, and it supplies both the LLM-visible schema and execution-time validation. |
| `setup()` | The creation pipeline calls `setup()` automatically when the agent is ready, and its parameters correspond to the declared args. |
| creation pipeline | The creation pipeline is the only path for creating an agent instance: it runs `before_create`, calls `setup(**kwargs)`, performs checks, registers the agent, and starts its work loop; chapter 4-7 explains the timing. |

## Goals

You can write agent parameters (args) and the surface form of `setup()` —
this chapter covers only what they look like and how to write them. This
chapter is the direct prerequisite for 1-4 (hooks) and 1-5 (provide-inject):
the rejection handler of the next chapter hooks into this same `setup()`.

## Main text

### The args declaration: two forms

The `args:` block in the `.fya` header declares parameter names / types /
default values. The two forms may appear in the same declaration:

```yaml
args:
  user_name: str        # The bare type string is shorthand for a required parameter.
  locale:               # This form expands JSON Schema keywords across multiple lines.
    type: string
    default: zh
    description: "Reply language (zh / en)"
```

Requiredness is always derived from the presence or absence of a `default`:
`user_name` has no default → required; `locale` has a default → optional.
For the args declaration, **the declaration is the model**: it is at once
the LLM-visible parameter schema (used to render the subagent catalog) and
the source of execution-time validation — a missing required parameter is
an error at creation time (demo 1 in the main example), never deferred to
run time.

### The surface of setup(): the typical four things

The parameters of `setup()` correspond to the args declaration (same names;
parameters with defaults correspond too). The creation pipeline calls it
automatically when the agent is ready, exactly once per instance. The
typical pattern does four things:

```python
async def setup(self, user_name: str, locale: str = "zh"):
    self.user_name = user_name               # ① Store the parameter for the {{ user_name }} template value.
    self.locale = locale                     # ② Store the parameter for the multilingual template in chapter 1-5.
    self.ask_count = 0                       # ③ Initialize the counter used by the hooks in chapter 1-4.
    self.provide("locale", locale)           # ④ Add the value to the provide chain, which chapter 1-5 explains.
    self.timezone = self.inject("timezone")  # ⑤ Read the value provided by main() before mount.
```

(Items ④⑤ share the flat numbering with the first three for typographic
reasons — the outline's "four things" are "store on self, initialize,
provide, inject".)

Comparison with 0-1: the run-time assignment `root.user_name = ...` in 0-1
also takes effect in templates; args + setup is the proper route of
"declaration + initialization" — the values are in place at creation time,
and they enter the persisted identity and pass through unchanged on recovery
(4-1).

Constraints: `setup()` should be lightweight (network / heavy IO belongs in
tools); `**kwargs` must be JSON-serializable. A handwritten subclass
(`args_model` + `setup`) is fully equivalent to the `.fya` declaration,
expanded in 5-1.

### main() forwarding

The CLI's `--key value` passes through `launch` unchanged to
`main(**kwargs)`; `main()` itself decides which parameters go to
`mount(**kwargs)` → creation pipeline → `setup(**args)`:

```python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")   # Provide this value before mounting the agent.
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

Save the following complete Agent declaration as `root.fya`. It contains the args,
system prompt, and `setup()` implementation:

```yaml
description: "Parameter demo assistant: args declaration + typical setup() pattern."
model_tag: default
args:
  user_name: str
  locale:
    type: string
    default: zh
    description: "Reply language (zh / en)"
---
$system_prompt:
You are a concise Q&A assistant. Current user: {{ user_name }}; reply language:
{{ locale }}; timezone: {{ timezone }}. Keep your answer within one sentence.
---
$script:
async def setup(self, user_name: str, locale: str = "zh"):
    self.user_name = user_name
    self.locale = locale
    self.ask_count = 0
    self.provide("locale", locale)
    self.timezone = self.inject("timezone")
```

Save the following complete model configurations as `providers.yaml`, `models.yaml`,
and `model-tags.yaml`. Keep the credential as an environment-variable placeholder:

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

```yaml
tags:
  default: deepseek-flash
```

## Out of scope

- This chapter does not cover creation-pipeline mechanics or assembly timing, including `before_create` hooks; chapters 2-4 and 4-7 do.
- This chapter does not cover provide/inject chain semantics or the sensitive-data boundary; chapter 1-5 does.
- This chapter does not cover parameter type bridging through `schema_to_model` or override-side rules; chapters 4-4 and 5-1 do.

## Main example

**Demo 1: a missing required parameter → fail-fast at creation time**
(the input omits `user_name`):

```console
$ uv run flowing repl .
launch failed: RootAgent.setup() missing 1 required positional argument: 'user_name'
```

The process exits directly (exit code 1) — a missing required parameter is
a **creation-time error**, not a run-time surprise.

**Demo 2: parameters take their places via setup** (the input supplies both
`user_name` and `locale`):

```console
$ uv run flowing repl . --user_name Alice --locale en
(agent-main)>>> Report in one Chinese sentence: who the current user is, what the locale is, and what the timezone is.
[thinking] (reasoning trace omitted)
当前用户是 Alice，区域设置为 en，时区为 Asia/Shanghai。
(agent-main)>>> /exit
```

Reading this session: `Alice` comes from `--user_name` (args → setup ① →
template `{{ user_name }}`); `en` comes from `--locale`; `Asia/Shanghai`
comes from `runtime.provide("timezone", ...)` in `main()` → `inject` in
setup ⑤. Each of the three placeholders in the template has its own source.
Although the system prompt sets the reply language to `en`, the user explicitly
requests a Chinese sentence, so the example answer is Chinese. The reasoning trace
is omitted. Chapter 1-5 makes language switching deterministic with `{% if %}`.

The complete `main.py`, `root.fya`, and three model configurations are embedded
above. Both command inputs and their observable outputs are shown here; the only
omitted transcript material is the model's private reasoning trace.

## Summary

1. The `args:` declaration has two forms (sugar / JSON Schema expansion),
   and requiredness derives from the presence or absence of a default.
2. A missing required parameter fails fast at creation time, and the args
   declaration is the validation model.
3. `setup()` typically does four things — store on self, initialize,
   provide, inject — exactly once per instance.
4. CLI parameters pass through `main(**kwargs)`, and `main()` forwards them
   to `mount(**kwargs)`.
