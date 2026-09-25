# 2-3 · Writing a ScriptTool

## Prerequisites

[0-2 Using Builtin Tools](0-2-use-builtin-tools.md) (tool declaration and usage)
and [1-3 · Parameters and setup()](1-3-agent-args-and-setup.md) (args declaration
is the model). This page includes the complete configuration, prompts, tool
implementations, user inputs, and outputs. The todo tool is also used by the
2-4 programming agent.

## Glossary for this chapter

| Term | One-sentence definition |
|---|---|
| `ScriptTool` | The main channel for writing tools in Python: `execute(**kwargs)` takes parameters and returns plain values |
| Declaration is the model | The `args_model` (Pydantic model) is the source of both the LLM-visible schema and execution validation — declare once, effective in both places |
| `error` result | A plain exception raised by `execute` → `ToolResult(status="error")`: a normal product visible to the LLM that does not trigger error hooks (unlike programming errors, which propagate directly) |
| `caller` injection | Once `execute` declares a `caller: Agent` parameter, the framework injects the calling agent automatically (caller APIs such as get_resource / inject / the state bag become available through it) |
| Three equivalent channels | Handwritten `ScriptTool` subclass / a function marked with `@flowing_tool` / a `.fya` `callable:` pointer — pairwise mutually exclusive, producing isomorphic definitions |

## Goals

Write a production-grade ScriptTool: the declaration surface, the execution
surface, the error channel, and the equivalent definition channels — this tool
is exactly the task-breakdown piece of the 2-4 coding agent.

## Main text

### Class-attribute declaration

The complete TodoArgs and TodoTool definitions appear in the reproduction
material at the end of this chapter. `name` is required and is not inferred
from the class name. `args_model` defines both the LLM schema and execution
validation. An explicit `definition` and class-attribute declaration are
mutually exclusive; if both are present, a warning is issued and the explicit
definition wins.

- `name` is required; under the file channel, a mismatch with the file identity
  raises `NameMismatchError`.
- `args_model` is optional — the framework infers it from the `execute()`
  signature (no type annotation → `MissingSchemaError`).
- Class-attribute declaration and an explicit `definition` are mutually
  exclusive (if both are present, a warning is issued and the explicit one wins).

### `execute`: scattered parameters in, plain values out

The author does not need to know that `ToolResult` exists: the return value is
wrapped as `completed` automatically; **a plain exception becomes an `error`
result** (visible to the LLM and self-correctable, and it does not trigger
error hooks); only `raise Intercepted` takes the blocked channel. This
chapter's validation failure (an empty list) deliberately takes the error
channel — the main example shows how the LLM relays it faithfully.

Once a `caller` parameter is declared, the framework injects the calling agent
automatically (the state-bag usage is shown in 4-2; this chapter stays
stateless and purely functional).

### Three equivalent channels

A handwritten `ScriptTool` subclass, a function marked with
`@flowing_tool`, and a declaration with a `callable:` pointer are three
equivalent definition forms. Their full implementations and declaration are
included below. Marking does not register a function and adds no Runtime
dependency at import time. A pointer can target a subclass or a bare function;
marking and pointers are mutually exclusive. All three forms produce
isomorphic `ToolDefinition` objects. Each file may contain at most one marked
function, and the identity name in a pointer declaration must match the
referenced tool's name.

### Referencing and parallel execution

File-defined tools are declared by a relative module name, and the directory
form uses a TOOL.fya declaration. The `[tool_call]` lines in 1-1 already
showed tools executing **in parallel** within a turn — `Agent._run_turn`
starts one task per tool_call
block (full mechanics in 4-5).

## Out of scope

- Chapter 4-5 covers the three background forms: async generators, `background=True`, and returning a `Task`.
- Chapter 4-5 also covers media returns and the five forms of result normalization.
- Chapter 4-2 covers the state-bag usage of `caller`, and Chapter 4-5 explains the three-level description fallback.

## Main example

**Demo 1: the agent uses the todo tool (including the error channel)**:

```console
$ uv run flowing repl .
(agent-main)>>> Please turn these into a task list: draft the proposal, buy coffee (already done),, send the email.
[thinking] (reasoning trace omitted)
[tool_call] todo {"tasks_text": "draft the proposal\n[x] buy coffee (already done)\n\nsend the email"}
[tool:completed] todo -> 
3 items total, 2 open: draft the proposal and send the email are pending, while buy coffee is done.
(agent-main)>>> Now use todo to parse an empty text: "" (demonstrating the error case).
[thinking] (reasoning trace omitted)
[tool_call] todo {"tasks_text": ""}
[tool:error] todo -> Task list is empty: provide at least one task
The tool returned the error: "Task list is empty: provide at least one task." No items were parsed, so there is nothing to report as total or open.
(agent-main)>>> 
```

Reading this session: in the first turn, the model translates the
natural-language "(already done)" into the tool's `[x]` prefix convention
before calling (visible in the `[tool_call]` line's arguments) — the schema's
description is the contract, and the LLM aligns with it; the empty item is
removed by the tool automatically. In the second turn, the empty text takes
the **[tool:error]** channel: the reason is visible to the LLM and relayed
faithfully, and the model treats it as a validation hint rather than a
program failure — the design intent of the error channel is fully realized.

**Demo 2: the three equivalent definition channels** (offline):

```console
$ uv run python demo_channels.py
Channel A handwritten subclass  : TodoTool name='todo'
Channel C callable pointer      : TodoToolFn name='todo-fn'
Channel B @flowing_tool function: Shout name='shout'
A and C are independent implementations with isomorphic declarations: True
  same params schema: True
  A: params=['tasks_text']
  B: params=['text']
  C: params=['tasks_text']
```

### Complete reproduction material

The relative filenames below are labels for creating each inline item; their
complete contents are provided here. The interactive demo requires a reader-
supplied `DEEPSEEK_API_KEY`. This page contains only the environment-variable
placeholder, not a credential.

```python
# main.py
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "en",
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
# root.fya front matter and complete system prompt
description: "ScriptTool demo assistant: breaks scattered task text into a task list with the todo tool."
model_tag: default
tools:
  - ./tools/todo.py
---
$system_prompt:
You are a task-tidying assistant. When the user gives scattered task text, use the
todo tool to parse it into a structured list, and report in one sentence "N items
total, M open"; when the tool returns an error, relay the error reason to the user
faithfully. Keep your answers within two sentences.
```

```python
# tools/todo.py
from pydantic import BaseModel, Field

from flowing import ScriptTool


class TodoArgs(BaseModel):
    tasks_text: str = Field(
        description="Task-list text: one task per line; a leading [x] marks it completed"
    )


class TodoTool(ScriptTool):
    """Parse task text into a structured list and return the open-task count."""

    name = "todo"
    args_model = TodoArgs

    async def execute(self, *, tasks_text: str) -> dict:
        items = []
        for raw in tasks_text.splitlines():
            line = raw.strip()
            if not line:
                continue
            done = line[:3].lower() == "[x]"
            title = (line[3:] if done else line).strip().lstrip("-•").strip()
            if not title:
                raise ValueError(
                    f"task line is empty after removing markers and bullets: {raw!r}"
                )
            items.append({"title": title, "done": done})
        if not items:
            raise ValueError("Task list is empty: provide at least one task")
        open_count = sum(1 for item in items if not item["done"])
        return {"total": len(items), "open": open_count, "items": items}
```

```python
# tools/shout.py
from flowing import flowing_tool


@flowing_tool
async def shout(text: str) -> str:
    """Convert text to uppercase."""
    return text.upper()
```

```python
# tools/todo_impl.py
from pydantic import BaseModel, Field

from flowing import ScriptTool


class TodoFnArgs(BaseModel):
    tasks_text: str = Field(
        description="Task-list text: one task per line; a leading [x] marks it completed"
    )


class TodoToolFn(ScriptTool):
    """Independent task-list tool for the callable-pointer example."""

    name = "todo-fn"
    args_model = TodoFnArgs

    async def execute(self, *, tasks_text: str) -> dict:
        items = []
        for raw in tasks_text.splitlines():
            line = raw.strip()
            if not line:
                continue
            done = line[:3].lower() == "[x]"
            title = (line[3:] if done else line).strip().lstrip("-•").strip()
            items.append({"title": title, "done": done})
        return {
            "total": len(items),
            "open": sum(1 for item in items if not item["done"]),
            "items": items,
        }
```

```yaml
# tools/todo-fn/TOOL.fya
type: script
callable: ../todo_impl.py::TodoToolFn
```

```python
# demo_channels.py
import asyncio
import pathlib

from flowing.tool.registry import ToolRegistry


async def main() -> None:
    registry = ToolRegistry(project_root=pathlib.Path.cwd())
    root = pathlib.Path.cwd()
    handwritten = registry.get("./tools/todo.py", source_dir=root)
    pointer = registry.get("./tools/todo-fn", source_dir=root)
    marked = registry.get("./tools/shout.py", source_dir=root)
    print(f"Channel A handwritten subclass  : {type(handwritten).__name__} name={handwritten.definition.name!r}")
    print(f"Channel C callable pointer      : {type(pointer).__name__} name={pointer.definition.name!r}")
    print(f"Channel B @flowing_tool function: {type(marked).__name__} name={marked.definition.name!r}")
    print(f"A and C are independent implementations with isomorphic declarations: {type(handwritten) is not type(pointer)}")
    print("  same params schema: "
          f"{sorted(handwritten.definition.params_schema) == sorted(pointer.definition.params_schema)}")
    for label, tool in (("A", handwritten), ("B", marked), ("C", pointer)):
        print(f"  {label}: params={sorted(tool.definition.params_schema)}")


asyncio.run(main())
```

The complete interactive input is:

```text
Please turn these into a task list: draft the proposal, buy coffee (already done),, send the email.
Now use todo to parse an empty text: "" (demonstrating the error case).
```

Enter those two lines in the interactive session to reproduce Demo 1. The
recorded tool calls and final replies above show the expected visible behavior.
The offline registry comparison uses the following commands; the current
working directory should be the project root represented by the relative
filenames above, and no directory change is required.

```console
$ export DEEPSEEK_API_KEY="<your-api-key>"
$ uv run flowing repl .
$ uv run python demo_channels.py
```

## Summary

1. `name` is required, `args_model` serves as both declaration and model, and the description has a three-level fallback.
2. `execute` takes scattered parameters and returns plain values, while a plain exception becomes an LLM-visible error result.
3. The three equivalent channels (subclass, marked function, and pointer) are pairwise mutually exclusive and produce isomorphic definitions.
4. File-implemented tools are declared by relative module names, and tools execute in parallel within a turn.
