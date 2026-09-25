# 2-1 · Declaratively Assembling a Multi-Agent Team

## Prerequisites

[0-3 Hello Multi-Agent](0-3-hello-multi-agent.md) (first contact with the
orchestrator + `explore-agent`). This chapter includes a complete team
declaration, each agent's prompt, the test input, and representative outputs.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| Type binding | The essence of the `subagents:` declaration: it declares "which subagent type this agent may use, and how", not "create an instance" |
| `<available_subagents>` | The subagent catalog rendered into the system prompt at every context assembly; the basis for LLM routing |
| Catalog rendering | The process that precomputes a view (alias / description / parameters-section XML) for every binding entry with `visible=True` and injects it into the system prompt |
| `visible=False` | The switch on a binding entry: it stays out of the catalog (invisible to the LLM) yet remains programmatically invocable via `invoke_subagent` — visibility is separated from executability |

## Goals

Declaratively assemble a multi-agent team: the orchestrator declares coder /
reviewer as its two subordinates, and a single query triggers the full
"write code → review → summarize" collaboration.

## Main text

### subagents: a declaration binds types, not instances

```yaml
# root.fya
tools:
  - subagent-invoke        # the invocation tool must still be declared explicitly (registration != visibility)
subagents:
  - ./agents/coder         # path form: a directory containing agent.fya
  - ./agents/reviewer
  - ./agents/auditor:
      visible: false        # entry form with overrides (body starts on the indented next line)
```

A `subagents:` entry references a **subagent type**. Each entry is one binding:
no instance is created at declaration time — only a declaration of "type +
usage". An instance is born at invocation time (creation and resume are
covered in 2-2).

### catalog: rendered at every assembly

At every logical turn's context assembly, the framework precomputes a catalog
view (alias, description, parameters-section XML) for every `visible=True`
entry and injects it as an `<available_subagents>` block into the system
prompt. The LLM uses it to decide "who should get this task, and with what
parameters". The catalog always reflects the binding table's **current**
state — change bindings mid-run and the next turn sees the change.

### LLM invocation and the visibility semantics of visible

The LLM invokes subagents through `subagent-invoke` (whichever types are in
the catalog can be invoked); the orchestrator prompt determines the routing
policy (the "coder first, then reviewer" order in the main example is prompt
engineering). Entries with `visible=False` stay out of the catalog — the LLM
cannot see or invoke them; programmatic `invoke_subagent("auditor", prompt=...)`
is unaffected (demonstrated by the main example's demo). This visibility /
executability separation belongs to the same design family as the
"registration != visibility" rule from 0-2.

## Out of scope

- `description` overrides and aliases (changes to catalog presentation) — 4-4;
- `specified` / injection expressions, parameter overrides, catalog template
  overrides — 4-4;
- the invocation pipeline internals (`on_subagent_invoke` /
  `on_subagent_returns`) — 2-2 / 4-6;
- programmatic fan-out (`create_subagent` + gather) — 2-4.

## Main example

**Demo 1: the orchestrator drives "write → review → summarize"** (complete
representative interaction):

````text
$ uv run flowing repl .
(agent-main)>>> I need a Python function for the Fibonacci sequence. Have coder write it, then have reviewer review it, and summarize the review findings for me.
I'll start by having the coder write the Fibonacci function.
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "coder", "prompt": "Write a Python function for the Fibonacci sequence. Accept a non-negative integer n and raise ValueError when n is negative. Return the complete function code."}
[tool:completed] subagent-invoke ->
```python
def fibonacci(n: int) -> int:
    # F(0) = 0 and F(1) = 1.
    if n < 0:
        raise ValueError("n must be non-negative")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
```
Now I'll have the reviewer review this code.
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "reviewer", "prompt": "Review this code for bugs, edge cases, style, and performance; report one issue per line or say LGTM if there are none:\n\ndef fibonacci(n: int) -> int:\n    # F(0) = 0 and F(1) = 1.\n    if n < 0:\n        raise ValueError(\"n must be non-negative\")\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a"}
[tool:completed] subagent-invoke -> LGTM
The coder wrote the function, and the reviewer found no issues.
````

Read this session: the two `subagent-invoke` calls are strictly sequential —
the orchestrator copies the coder's complete code into the reviewer's prompt.
The LLM decides the order of multi-agent collaboration under the
constraints of the orchestrator prompt; what you declare is only "which
subordinates are available".

**Demo 2: the visibility / executability separation of visible=False**:

```console
$ uv run python demo_enabled.py
subagents binding table:
  coder: visible=True -> in catalog (visible to the LLM)
  reviewer: visible=True -> in catalog (visible to the LLM)
  auditor: visible=False -> not in catalog (invisible to the LLM)
programmatic invocation of the visible=False auditor -> result='Audit channel activated' status=completed
```

## Complete example materials

Create these relative files in a Flowing-enabled project and set
`DEEPSEEK_API_KEY` in the environment. The prompts and expected observable
results are included here. The Fibonacci response is representative; an LLM
may produce different correct code or review findings.

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
description: "Orchestrator: dispatch coding tasks to coder and review tasks to reviewer."
model_tag: default
tools:
  - subagent-invoke
subagents:
  - ./agents/coder
  - ./agents/reviewer
  - ./agents/auditor:
      visible: false
---
$system_prompt:
You are the task orchestrator. For a development request, dispatch it to
coder to write code, pass the returned code to reviewer, then summarize the
review findings. Do not write or review code yourself.
```

`agents/coder/agent.fya`:

```yaml
description: "Programmer: writes concise Python code on demand."
model_tag: default
---
$system_prompt:
You are a programmer. Write concise Python code with type annotations. Output
only the complete function code. Do not add usage notes or chit-chat.
```

`agents/reviewer/agent.fya`:

```yaml
description: "Code reviewer: reviews code read-only and outputs a problem list."
model_tag: default
---
$system_prompt:
You are a strict code reviewer. Review the supplied code and output one
"[severity] description" per line, or output "LGTM" if there are no issues.
Do not modify the code.
```

`agents/auditor/agent.fya`:

```yaml
description: "Auditor: an internal compliance audit."
model_tag: default
---
$system_prompt:
You are an auditor. Reply only "Audit channel activated" to every instruction.
```

Input and run command:

```text
I need a Python function for the Fibonacci sequence. Have coder write it, then have reviewer review it, and summarize the review findings for me.
/exit
```

```console
$ uv run flowing repl .
```

Expected interaction: `coder` returns a complete `fibonacci` function; the
orchestrator includes that returned code in the reviewer's prompt; the
reviewer returns a problem list or `LGTM`; the orchestrator summarizes both.

The hidden `auditor` remains directly invocable by application code. The
complete contents of `demo_enabled.py` follow:

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")
    print("subagents binding table:")
    for alias, entry in root._subagent_entries.items():
        visibility = "in catalog (LLM-visible)" if entry.visible else "not in catalog (LLM-invisible)"
        print(f"  {alias}: visible={entry.visible} -> {visibility}")
    result = await root.invoke_subagent("auditor", prompt="Activate.")
    print(f"programmatic invocation of hidden auditor -> result={result.result!r} status={result.subagent_status}")
    await runtime.shutdown()


asyncio.run(main())
```

Expected output:

```text
subagents binding table:
  coder: visible=True -> in catalog (LLM-visible)
  reviewer: visible=True -> in catalog (LLM-visible)
  auditor: visible=False -> not in catalog (LLM-invisible)
programmatic invocation of hidden auditor -> result='Audit channel activated' status=completed
```

## Summary

1. `subagents:` is a type-binding declaration: type + usage; instances are
   born at invocation time;
2. the catalog is rendered at every assembly and is the basis for LLM routing;
3. `subagent-invoke` must still be declared explicitly under `tools:`
   (registration != visibility);
4. `visible=False`: invisible to the LLM, programmatically invocable —
   visibility is separated from executability.
