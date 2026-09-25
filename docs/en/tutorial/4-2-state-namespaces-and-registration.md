# 4-2 · State Namespaces and Self-Registration

## Prerequisites

[1-8 Runtime and Agent Directories](1-8-runtime-and-agent-dirs.md) (the
Runtime/Agent relationship), [4-1 Core State and Message Persistence](4-1-core-state-and-message-persistence.md)
(the persistence model), and [2-3 Writing a ScriptTool](2-3-write-script-tool.md)
(a stateless todo tool). Flowing must be installed, and `DEEPSEEK_API_KEY` must
be set in the environment. The configuration below retains its environment
placeholder and contains no credential.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| state bag | The key space exposed by `agent.state`; values are read from memory and writes are persisted. |
| named bag | An independent state namespace opened by `register_state(name)`, at either Agent or Runtime scope. |
| default-on-register | `state.register(key, default)` persists the default only when no persisted value exists; otherwise, the persisted value wins. |
| write-through | Setting or deleting a key submits a persistence operation immediately; reads use the in-memory authority. |
| Runtime-global bag | A named bag registered on the Runtime and shared by its Agents. |

## Goals

Register persistent state at Agent and Runtime scope. Keep values that must
survive a restart and recompute values that can be derived again. The example
also makes the earlier stateless todo tool persistent.

## Main text

### `agent.state`: the default bag

```python
async def setup(self):
    self.state.register("user_prefs", {})
    self.state.register("todo_items", [])
    self.state.register("visit_count", 0)

# Inside a tool or hook:
preferences = caller.state.get("user_prefs") or {}
preferences[key] = value
caller.state["user_prefs"] = preferences
```

- **Write-through:** assignment submits the persistence operation immediately.
  Values must be JSON-compatible. Reads use in-memory state.
- **Default-on-register, persisted value wins:** recovery replay completes
  before `setup()` runs. `register` does not overwrite an existing persisted
  value; later changes to the default in code do not replace that value.
- **No schema is required:** assigning a key writes it without a prior call to
  `register`; deleting the key removes its persisted value.

### `register_state`: named bags

```python
agent.register_state("audit")
runtime.register_state("app")
```

Named bags follow the same state-view contract as the default bag; they split
key spaces into independent namespaces. A plugin can prefix business keys
with its name, such as `cron_jobs`. Framework-private state is kept separate
from application data.

### What to register

| Register | Do not register |
|---|---|
| Preferences, progress, and task lists that must survive sessions. | High-frequency telemetry values, except counters whose persistence is intentional. |
| Business state needed after recovery. | Values that can be derived again during context assembly. |
| Small configuration data. | Large objects; state values must be JSON-compatible. |

Frequently changing values should use the message channel or in-memory
attributes when persistence is not required. The state bag is a persistence
channel, not a per-turn context-injection channel: it never enters the LLM
context. Values pass into and out of the bag through tools; the model sees
tool arguments and results rather than the bag itself.

## Out of scope

- The detailed timing of write-through persistence is outside this chapter.
- Core-state and message persistence are covered in
  [4-1 Core State and Message Persistence](4-1-core-state-and-message-persistence.md).
- Compaction timing and migration chains are outside this chapter.

## Main example

The example records a preference and two tasks, increments an Agent-level turn
counter, and increments a Runtime-global launch counter. It then restarts and
reads the persisted values. Every declaration, prompt, tool implementation,
input, and representative output is included below. Install Flowing and set
`DEEPSEEK_API_KEY` before running the commands. Internal model reasoning is
not displayed.

### Create these project files

`main.py`:

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")

    app_state = runtime.register_state("app")
    app_state["boots"] = app_state.get("boots", 0) + 1

    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

`root.fya`:

```yaml
description: "Assistant with persistent preferences, todos, and visit count."
model_tag: default
tools:
  - ./prefs.py
  - ./todo.py
  - ./app_info.py
---
$system_prompt:
You are a state-demo assistant. Users save preferences with prefs, manage
tasks with todo, and ask for launch counts with app-info. Keep each answer to
one sentence.
---
$script:
async def setup(self):
    self.state.register("user_prefs", {})
    self.state.register("todo_items", [])
    self.state.register("visit_count", 0)

    async def count_completed_turns(agent, turn):
        if turn.aborted:
            return turn
        agent.state["visit_count"] = agent.state["visit_count"] + 1
        return turn

    self.hooks.after_turn(count_completed_turns, by="counter")
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

`prefs.py`:

```python
from typing import Literal

from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class PrefsArgs(BaseModel):
    action: Literal["remember", "list"] = Field(
        description="remember=store one preference; list=list all"
    )
    key: str | None = Field(default=None, description="preference name")
    value: str | None = Field(default=None, description="preference value")


class PrefsTool(ScriptTool):
    """Remember and list preferences stored in the Agent state bag."""

    name = "prefs"
    args_model = PrefsArgs

    async def execute(
        self, *, action: str, key: str | None, value: str | None,
        caller: Agent,
    ) -> dict:
        preferences = caller.state.get("user_prefs") or {}
        if action == "remember":
            if not key or value is None:
                raise ValueError("remember requires key and value")
            preferences[key] = value
            caller.state["user_prefs"] = preferences
            return {"remembered": {key: value}}
        return {"prefs": preferences}
```

`todo.py`:

```python
from typing import Literal

from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class TodoArgs(BaseModel):
    action: Literal["add", "done", "list"] = Field(
        description="add=add a task; done=complete; list=list"
    )
    title: str | None = Field(default=None, description="task title for add")
    index: int | None = Field(default=None, description="1-based task index for done")


class TodoStateTool(ScriptTool):
    """Add, complete, and list tasks persisted in the Agent state bag."""

    name = "todo"
    args_model = TodoArgs

    async def execute(
        self, *, action: str, title: str | None, index: int | None,
        caller: Agent,
    ) -> dict:
        items: list[dict] = caller.state.get("todo_items") or []
        if action == "add":
            if not title:
                raise ValueError("add requires title")
            items.append({"title": title, "done": False})
        elif action == "done":
            if index is None or not 1 <= index <= len(items):
                raise ValueError(f"index out of range: {index!r} ({len(items)} items)")
            items[index - 1]["done"] = True
        caller.state["todo_items"] = items
        return {
            "items": items,
            "open": sum(1 for item in items if not item["done"]),
        }
```

`app_info.py`:

```python
from flowing import Agent, ScriptTool


class AppInfoTool(ScriptTool):
    """Read application-level information from the Runtime-global bag."""

    name = "app-info"

    async def execute(self, *, caller: Agent) -> dict:
        app_state = caller.runtime.states["app"]
        return {"boots": app_state.get("boots", 0)}
```

Run 1 input:

```text
Remember a preference: theme=dark. Add two todos: write the weekly report, buy milk. Which launch is this, and which turn of this session am I on?
```

Start the REPL with `uv run flowing repl .`, enter the input above, and then
enter `/exit`. A representative visible transcript is:

```text
(agent-main)>>> Remember a preference: theme=dark. Add two todos: write the weekly report, buy milk. Which launch is this, and which turn of this session am I on?
[thinking] (reasoning trace omitted)
[tool_call] prefs {"action": "remember", "key": "theme", "value": "dark"}
[tool_call] todo {"action": "add", "title": "write the weekly report"}
[tool_call] todo {"action": "add", "title": "buy milk"}
[tool_call] app-info
[tool:completed] prefs
[tool:completed] todo
[tool:completed] todo
[tool:completed] app-info
Preference theme=dark saved and both todos added, and this is launch #1 — but I don't track a session turn counter, so I can't tell you which turn you're on.
(agent-main)>>> /exit
```

The tool results supplied to the assistant in Run 1 are:

```json
{
  "prefs": {"remembered": {"theme": "dark"}},
  "todo_add_1": {
    "items": [{"title": "write the weekly report", "done": false}],
    "open": 1
  },
  "todo_add_2": {
    "items": [
      {"title": "write the weekly report", "done": false},
      {"title": "buy milk", "done": false}
    ],
    "open": 2
  },
  "app-info": {"boots": 1}
}
```

After restarting the REPL, Run 2 input is:

```text
What are the preferences and todos I saved? And what about the visit count and the launch count?
```

Start `uv run flowing repl .` again, enter that input, and then enter `/exit`.
A representative visible transcript is:

```text
(agent-main)>>> What are the preferences and todos I saved? And what about the visit count and the launch count?
[thinking] (reasoning trace omitted)
[tool_call] prefs {"action": "list"}
[tool_call] todo {"action": "list"}
[tool_call] app-info
[tool:completed] prefs
[tool:completed] todo
[tool:completed] app-info
You have one preference (theme=dark) and two open todos (write the weekly report, buy milk), and the launch count is now 2 — but there's no visit count anywhere in my tools.
(agent-main)>>> /exit
```

The tool results supplied to the assistant in Run 2 are:

```json
{
  "prefs": {"prefs": {"theme": "dark"}},
  "todo": {
    "items": [
      {"title": "write the weekly report", "done": false},
      {"title": "buy milk", "done": false}
    ],
    "open": 2
  },
  "app-info": {"boots": 2}
}
```

The persisted state after these two turns is represented by the following
records. The first three belong to the Agent's default bag; the final record
belongs to the Runtime-global `app` bag:

```jsonl
{"op": "set", "key": "user_prefs", "value": {"theme": "dark"}}
{"op": "set", "key": "todo_items", "value": [{"title": "write the weekly report", "done": false}, {"title": "buy milk", "done": false}]}
{"op": "set", "key": "visit_count", "value": 2}
{"op": "set", "key": "boots", "value": 2}
```

The preference and task tools write to the Agent bag. The after-turn hook
increments `visit_count` once per completed turn; the Runtime increments
`boots` once per process launch. The `app-info` tool exposes `boots`, not
`visit_count`, so the model cannot report the latter through its available
tools. State bags are persistence channels and are not inserted into the LLM
context.

## Summary

1. A state bag is a key namespace with write-through persistence; registering a
   default does not overwrite an existing persisted value.
2. `register_state` opens an independent namespace, and a Runtime-global bag
   can be shared across Agents.
3. Register values that must survive a restart; avoid high-frequency or
   recomputable values unless persistence is intentional.
4. Tools can expose selected state operations to an Agent without placing the
   entire state bag in the LLM context.
