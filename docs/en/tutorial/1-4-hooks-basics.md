# 1-4 · Hooks Basics

## Prerequisites

[1-3 Parameters and setup()](1-3-agent-args-and-setup.md) (how `setup()` is
written). This chapter uses the same Runtime/Agent assembly pattern and
shows both class-declared and setup-registered hooks.

## Glossary for this chapter

| Term | One-sentence definition |
|---|---|
| hook point | a fixed moment in framework execution (before a tool runs, at turn wrap-up, etc.) where extension code intervenes or observes |
| handler | the processing function attached to a hook point, with the uniform signature `(agent, value)`; this chapter calls it the hook handler, and after its first appearance it may be called handler directly |
| `Intercepted` | the handler's hard-block signal: after it is raised, the current operation is voided and the LLM receives a blocked result with the reason (deliberately not attached to the framework exception root) |
| `before_tool_call` | the hook point before a tool executes: it can rewrite call arguments or hard-block — a natural point for manual approval / a safety gate |
| `after_turn` | the hook point at turn wrap-up: the only observation point covering all paths (including the exception-termination path) |

## Goals

Be able to use the two most common hook points: `before_tool_call` for
interception and `after_turn` for observation; and master the two mounting
methods (declaring with `@on` on the class and registering inside
`setup()`).

## Main text

### Instance-level hook registry

Each Agent instance holds its own hook registry (`agent.hooks`), and **all
hooks take effect only for the current instance** — there is no global hook
table. Handlers have the uniform signature `(agent, value)` (in method
form, `(self, value)`), with three exits:

1. `return value`: pass through (if you rewrite value along the way, that
   rewrite is the point, e.g. changing tool arguments);
2. `raise Intercepted`: hard block — the current operation is voided, the
   LLM receives a `blocked` result with the reason, and can explain to the
   user "the operation was intercepted" instead of "execution failed";
3. a plain exception propagates directly; no fallback hook catches it.

### The two mounting methods

**Declaring with `@on` on the class** (registration happens during
`__init__`, earlier than `setup()` — the only way to mount creation-time
hooks):

```python
@on("after_turn")
def _(self, turn):
    self.ask_count += 1
    print(f"[hook] after_turn: ask_count={self.ask_count}")
    return turn
```

**Registering inside `setup()`** (instance-level, mounted at assembly
time):

```python
async def _guard(agent, tool_call):
    if tool_call.name == "bash" and "rm" in tool_call.args.get("command", ""):
        raise Intercepted("Deletion not allowed: command contains rm; blocked")
    return tool_call
self.hooks.before_tool_call(_guard)
```

The parameters of `setup()` must correspond one-to-one with the `args:`
declaration — creation-time validation: one extra parameter fails fast (the
other half of "the args declaration is the model" from 1-3).

### before_tool_call: the interception gate

The value of `before_tool_call` is the `ToolCall` (what the LLM wants to
call). Returning the rewritten `ToolCall` is a pass; `raise Intercepted`
is a refusal — the tool **does not execute**, and the LLM receives a
`blocked` result with the interception reason. From interception to
approval is one step: the handler is `async`, so it can `await` an
external confirmation inside before deciding the exit (approval is a
policy; hooks are only the mechanism; the full approval interaction is an
advanced topic with the same mechanism).

### after_turn: the observation point

The value of `after_turn` is the `TurnContext`; it fires when **all
paths** wrap up (including intercepted, cancelled, or exceptional turns),
and the handler reads `turn.aborted` to distinguish the outcome. A handler
exception does not wedge the Agent — waiters still receive the result as
usual.

## Out of scope

- the full set of hook points and pattern filtering
  (`hook["payment-*"](...)`) — Chapter 4-6;
- `declare()` for declaring extension hook points — 5-2 / 5-3;
- the `watch` assignment event channel — 4-7;
- a manual approval flow with pass/rewrite-arguments interaction — the
  mechanism is the same as above; the application form is yours to decide.

## Main example

In the repl, first ask to delete a file (triggering the interception),
then ask to inspect a directory (allowed through):

```console
$ uv run flowing repl . --user_name Alice
(agent-main)>>> Please use bash to delete notes/路线图.md.
I'll try that now.
[tool_call] bash {"command": "rm notes/路线图.md", "cwd": "."}
[hook] before_tool_call: bash args={'command': 'rm notes/路线图.md', 'cwd': '.'}
[tool:blocked] bash -> Deletion not allowed: command contains rm; blocked
The deletion was blocked: the safety hook intercepts any command containing `rm`, so I couldn't remove `notes/路线图.md`.
[thinking] (reasoning trace omitted)
[hook] after_turn: ask_count=1 aborted=False
(agent-main)>>> Then use bash to take a look at what's in the notes directory.
[thinking] (reasoning trace omitted)
[tool_call] bash {"command": "ls notes", "cwd": "."}
[hook] before_tool_call: bash args={'command': 'ls notes', 'cwd': '.'}
[tool:completed] bash -> exit_code: 0 --- stdout --- 使用说明.md 路线图.md
The `notes` directory contains `使用说明.md` and `路线图.md`; the latter remains because the earlier `rm` was blocked.
[hook] after_turn: ask_count=2 aborted=False
(agent-main)>>> /exit
```

Reading this session: in the first turn, the model's tool-call intent
parameters are fully visible (the `rm` command line); the `[hook]` lines
come from the print in the `before_tool_call` handler (the minimal form of
an interception hook: observe + decide whether to pass); `rm` hits the
guard rule → `[tool:blocked]` carries the reason back to the LLM → the LLM
explains to the user honestly that `notes/路线图.md` was not deleted;
`[hook] after_turn: ask_count=1 aborted=False` is the observer handler
declared with `@on` firing at turn wrap-up. In the second turn, the model
runs `ls notes`, which is allowed through; `[tool:completed]` carries
the real directory listing (both files still present), and `ask_count`
accumulates to 2. Checking afterwards: `notes/路线图.md` is intact.

## Complete example materials

The following content is the whole example needed for this chapter. Create
the files with these relative names in a Flowing-enabled project, set
`DEEPSEEK_API_KEY` in the environment, and run the commands from that project
root. The deletion request is deliberately blocked; the note files remain
unchanged.

`main.py`:

```python
from flowing import Runtime


async def main(user_name: str = "Alice") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    await runtime.mount("@/root.fya", agent_id="agent-main", user_name=user_name)
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

`root.fya` (complete declaration, system prompt, and hook code):

```yaml
description: "Hooks demo assistant: before_tool_call interception + after_turn observation."
model_tag: default
args:
  user_name: str
tools:
  - bash
---
$system_prompt:
You are the demo assistant. Current user: {{ user_name }}.
When asked to inspect a directory, use bash with ls. When asked to delete a
file, use bash with rm; a safety hook blocks every command containing rm.
Explain a blocked deletion honestly. Keep each answer to one sentence.
---
$script:
from flowing import Intercepted, on


@on("after_turn")
def _(self, turn):
    self.ask_count += 1
    print(f"[hook] after_turn: ask_count={self.ask_count} aborted={turn.aborted}")
    return turn


async def setup(self, user_name: str):
    self.user_name = user_name
    self.ask_count = 0
    self.timezone = self.inject("timezone")

    async def _guard(agent, tool_call):
        print(f"[hook] before_tool_call: {tool_call.name} args={tool_call.args}")
        if tool_call.name == "bash" and "rm" in tool_call.args.get("command", ""):
            raise Intercepted("Deletion not allowed: command contains rm; blocked")
        return tool_call

    self.hooks.before_tool_call(_guard)
```

`notes/使用说明.md`:

```text
This directory contains short project notes.
The safety-hook demonstration must leave every note unchanged.
```

`notes/路线图.md`:

```text
Demo file: the hook must block deletion.
```

Input (enter each line at the REPL prompt):

```text
Please use bash to delete notes/路线图.md.
Then use bash to list the notes directory.
/exit
```

Run:

```console
$ uv run flowing repl . --user_name Alice
```

Expected observable result: the first `bash` call returns a `blocked` result
with `Deletion not allowed`; the second call lists both note files; the
`after_turn` counter reaches 2. The exact LLM wording may vary. The rule
matches any command containing the substring `rm`, so this guard is only a
small demonstration, not a complete shell-security policy.

## Summary

1. The hook registry is instance-level: all hooks take effect only for the
   current Agent;
2. a handler has three exits: return passes through (rewriting allowed),
   `Intercepted` hard-blocks, a plain exception propagates;
3. `before_tool_call` is a natural interception gate, and `after_turn` is
   the only observation point covering all paths;
4. two mounting methods: declaring with `@on` on the class (earlier than
   setup), and registering inside `setup()` (at assembly time).
