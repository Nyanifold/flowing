# 2-4 · Multi-Agent Application Final Assembly

## Prerequisites

Readers should understand the basic concepts from Chapters 0-0 through 2-3. This
chapter combines an orchestrator, explore, todo, and coder into a multi-agent
application. All required configuration, prompts, code, inputs, and outputs are
included here; no other example material is needed.

## Glossary for this chapter

| Term | One-sentence definition |
|---|---|
| Orchestration mode | An organization pattern for multi-agent collaboration; this chapter covers LLM routing and programmatic fan-out. |
| Programmatic fan-out | Code creates multiple child Agents directly, runs their tasks in parallel, and awaits all results with gather. |
| cwd injection chain | main() passes cwd through runtime.provide(), each Agent's inject(), and {{ cwd }} in the system_prompt. |
| kwargs are identity | Creation arguments are persisted with Agent metadata and passed through on recovery, so they must be JSON-serializable. |

## Goals

Assemble the earlier mechanisms into a complete workflow. The orchestrator first
asks explore to inspect the current working directory, uses todo to break down
the request, and then delegates writing to coder. The cwd value reaches the
orchestrator and coder through provide-inject.

## Main text

### Components and execution order

The orchestrator reads the request, selects subagents, and reports the result.
The builtin explore-agent inspects the working directory without writing;
todo turns requirements into a structured checklist; and coder creates or
edits files inside that working directory. Agent configuration and system
prompts define these responsibilities.

### The cwd injection chain

Before mounting the orchestrator, the entry point calls
runtime.provide("cwd", cwd). The orchestrator and coder each call
self.inject("cwd") in setup() and use the value in a system_prompt template.
The task sent to explore-agent includes the working directory it should inspect.
This design keeps the shared value at the Runtime level instead of repeating it
in each Agent's creation arguments.

### Two orchestration modes

1. **LLM routing.** The orchestrator reads the catalog and selects explore,
   todo, and coder as needed. The current request and each result determine
   whether it proceeds.
2. **Programmatic fan-out.** Python code creates two coder subagents directly
   and awaits them in parallel with asyncio.gather. The wait graph must remain
   acyclic; the script uses an application-root anchor for the Agent type rather
   than catalog routing.

## Out of scope

- Chapter 4-1 explains persistence and recovery in full.
- Chapter 4-4 explains how to consume the structured payload returned by finish.
- Chapters 4-6 and 5-2 cover other orchestration forms, including Workflow and
  message-layer collaboration.

## Main example

### Run 1: survey and break down the task

User input:

```text
First ask explore to inspect the current working directory and report its structure, then use todo to break down "write a README.md for this project" into a task list.
```

The orchestrator's reasoning is represented by a placeholder before it calls
explore-agent. The complete prompt follows:

```text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"explore-agent","name":"explore-target","prompt":"full prompt below"}
```

```text
Inspect the current working directory and all of its contents without modifying anything. Report:
1. The complete file and subdirectory structure.
2. The language, framework, package-management approach, and entry point.
3. The purpose of important code, configuration, tests, and existing documentation.
4. The available APIs, dependencies, and runtime limitations.
5. Any facts a README author should state explicitly.
Do not create, edit, or delete anything. Return a text report only.
```

One exploration result is:

```text
This is a minimal calculator example written in Python 3.
It provides addition and division and uses only the Python standard library.
It has no command-line entry point, installation configuration, test suite, or license statement.
Division by zero raises ValueError("divisor must not be 0").
"adder" and "divider" are informal names for the two functions, not separate features.
```

After exploration, the orchestrator calls todo. The non-user-visible reasoning
is omitted; the complete tool input is:

```text
[thinking] (reasoning trace omitted)
[tool_call] todo {"tasks_text":"full task text below"}
[x] Inspect the project structure, technology, and key behavior in the current working directory
[ ] Choose the README title, overview, and section order
[ ] Describe the parameters, return values, and division-by-zero exception
[ ] Include reproducible import, call, and output examples
[ ] Clarify that adder and divider are informal names
[ ] State the Python version, dependencies, and project limitations
[ ] Draft the README and self-check it against the function behavior shown here
[ ] Report the result and any unresolved issue to the user
```

Tool result:

```text
8 tasks total; 1 completed and 7 open.
```

The orchestrator presents the checklist to the user and pauses before dispatching
a writing task.

### Run 2: dispatch coder

User input:

```text
Use the structure and checklist from before to write README.md; then self-check every item and report back.
```

After the user asks it to continue, the orchestrator calls coder. The complete
task prompt follows:

```text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"coder","name":"readme-writer","prompt":"full task prompt below"}
```

```text
Write a README.md for the minimal Python calculator in the current working directory. Modify README.md only.

Project facts:
- The callable functions are add(a: float, b: float) -> float and div(a: float, b: float) -> float.
- add returns the sum of its two arguments.
- div returns a / b and raises ValueError("divisor must not be 0") when b == 0.
- The project uses only the Python standard library. It has no third-party dependencies, CLI, installer, or test suite.
- "adder" and "divider" are informal names for add and div.

The README must include an overview, the complete function definitions, API behavior,
reproducible inputs and expected outputs, terminology, and limitations. Include
normal division and handling of a zero-divisor exception.

After writing the README, read it and self-check the function names, signatures,
return values, and exact exception message against the facts above. Verify that
all inputs and outputs are included, code fences are balanced, and no dependency
or feature has been invented. Report only the check results. Do not run programs
or tests.
```

A recorded visible tool exchange and delivery summary follows. Model wording can
vary, but the code facts and expected outputs should match the inline example.

```text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"coder","name":"readme-writer","prompt":"Write a README.md for the minimal Python calculator in the current working directory. Modify README.md only. Project facts, required sections, and self-check steps are given in the complete prompt above."}
[tool:completed] subagent-invoke ->
README.md was written and self-checked. It includes the overview, complete module,
API, normal calls, zero-divisor handling, terminology, and environment limits.
No dependency or extra feature was added.
```

The README's complete module is:

```python
"""Example project: a calculator."""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("divisor must not be 0")
    return a / b
```

The README's complete usage input is:

```python
from pkg.calc import add, div

print(add(1, 2))
print(add(1.5, 2))
print(div(1, 2))

try:
    print(div(1, 0))
except ValueError as exc:
    print(f"ValueError: {exc}")
```

Expected output:

```text
3
3.5
0.5
ValueError: divisor must not be 0
```

The complete README delivered by coder follows. Its code, call inputs, and
expected outputs are included here so readers can understand the deliverable
without opening another Markdown page.

~~~~markdown
# Example project: a calculator

This is a minimal Python calculator with two public functions: `add` performs
addition, and `div` performs division. This page includes the complete
module, inputs, outputs, and error behavior.

## Complete module

Save the following code under the relative filename `pkg/calc.py`:

```python
"""Example project: a calculator."""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("divisor must not be 0")
    return a / b
```

## API and reproduction

| Function | Parameters | Return value | Exception |
| --- | --- | --- | --- |
| `add(a, b)` | `a: float`, `b: float` | `a + b`, as a `float` | None |
| `div(a, b)` | `a: float`, `b: float` | `a / b`, as a `float` | Raises `ValueError("divisor must not be 0")` when `b == 0` |

The complete signatures are `add(a: float, b: float) -> float` and
`div(a: float, b: float) -> float`.

```python
from pkg.calc import add, div

print(add(1, 2))
print(add(1.5, 2))
print(div(1, 2))
```

Output:

```text
3
3.5
0.5
```

Input for the zero-divisor case:

```python
from pkg.calc import div

try:
    print(div(1, 0))
except ValueError as exc:
    print(f"ValueError: {exc}")
```

Output:

```text
ValueError: divisor must not be 0
```

## Terminology and limitations

`adder` is an informal name for `add`, and `divider` is an informal name
for `div`; neither is a function, class, or configuration entry. The example
uses Python 3 and the standard library. It provides no CLI, installer, or test
suite. Keep the project root (the directory containing `pkg`) as the current
working directory for imports; this page does not ask you to change directories.
~~~~

### Demo 3: programmatic fan-out

This complete offline program creates two coder subagents in parallel and waits
for each to write a terminology note. Its entry point, configuration, Agent
prompts, and target data are included at the end of the chapter.

```python
# demo_fanout.py
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".", cwd=".")
    root = await runtime.get_agent("agent-main")

    async def job(name: str, term: str) -> str:
        child = await root.create_subagent("@/agents/coder", name=name)
        try:
            result = await child.query(
                f"Create notes/{term}.md in the current working directory. "
                f"In one sentence, explain what {term} means in this project; "
                "then submit with finish."
            )
            return f"{name}: status={result.status}"
        finally:
            await child.destroy()

    results = await asyncio.gather(
        job("coder-a", "adder"),
        job("coder-b", "divider"),
    )
    for line in results:
        print(line)
    await runtime.shutdown()


asyncio.run(main())
```

One recorded output:

```text
coder-a: status=completed
coder-b: status=completed
```

## Complete reproduction material

The following blocks provide all required content for the multi-agent application
and target project. Each relative filename labels the complete content that
follows it. The commands do not require a directory change. An interactive model
call requires a reader-supplied DEEPSEEK_API_KEY; only an environment-variable
placeholder is included here.

```python
# main.py
from flowing import Runtime


async def main(cwd: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if cwd:
        runtime.provide("cwd", cwd)
    await runtime.mount("@/root.fya", agent_id="agent-main")
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
# root.fya
description: "Programming task orchestrator: inspect, break down, and delegate implementation."
model_tag: default
tools:
  - subagent-invoke
  - ./tools/todo.py
subagents:
  - explore-agent
  - ./agents/coder
---
$system_prompt:
You are the programming task orchestrator. The current working directory is {{ cwd }}.
First dispatch explore-agent for a read-only survey and report; then use todo to
turn implementation requests into a checklist and show it to the user. Wait for
the user's instruction before dispatching writes to coder. Summarize the result
to the user. Do not read, write, or execute files yourself.
---
$script:
async def setup(self):
    self.cwd = self.inject("cwd")
```

```yaml
# agents/coder/agent.fya
description: "A programming agent that edits files and delivers tasks within the current working directory."
model_tag: default
tools:
  - read
  - write
  - edit
  - grep
  - glob
  - bash
  - finish
---
$system_prompt:
You are the programming agent. The current working directory is {{ cwd }}.
Work only inside it. Use explicit relative filenames for file operations; do not
guess other locations. Call finish when done: summary describes the work and
files lists the changed files. Do not modify anything outside the working directory.
---
$script:
async def setup(self):
    self.cwd = self.inject("cwd")
```

```python
# tools/todo.py
from pydantic import BaseModel, Field

from flowing import ScriptTool


class TodoArgs(BaseModel):
    tasks_text: str = Field(
        description="Task-list text: one task per line; a leading [x] marks a completed task"
    )


class TodoTool(ScriptTool):
    """Parse task text into a list and return the number of open tasks."""

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
                raise ValueError(f"task line is empty: {raw!r}")
            items.append({"title": title, "done": done})
        if not items:
            raise ValueError("task list is empty: provide at least one task")
        return {
            "total": len(items),
            "open": sum(1 for item in items if not item["done"]),
            "items": items,
        }
```

```python
# pkg/calc.py
"""Example project: a calculator."""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("divisor must not be 0")
    return a / b
```

```text
# notes/adder.md
# The role of adder in this project

There is no separate feature named adder. It is an informal name for the add
function, which takes two numbers and returns their sum; for example,
add(1, 2) returns 3.
```

```text
# notes/divider.md
# The role of divider in this project

Divider is not a separate feature. It is an informal name for the div function.
div returns a / b and raises ValueError("divisor must not be 0") when b is zero.
```

To run the interactive routing example, let the current working directory contain
the inline content above. This command uses the application entry point and a
relative working-directory value and does not include a directory-change command:

```console
$ export DEEPSEEK_API_KEY="<your-api-key>"
$ uv run flowing repl . --cwd .
```

## Summary

1. The assembled application combines entry-point policy, orchestrator
   configuration, subagent configuration, and a task tool.
2. cwd travels through Runtime provide, Agent inject, and prompt templates to
   each layer that needs it.
3. LLM routing delegates step by step from task results; programmatic fan-out
   creates subagents in parallel from code.
4. All prompts and target data are inline, so readers can reconstruct the
   example from the relative filenames without looking up other materials.
