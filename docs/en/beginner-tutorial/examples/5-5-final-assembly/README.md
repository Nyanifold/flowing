# Example: 5-5 Final Assembly

This example combines runtime setup, working-directory injection, planning,
subagent delegation, and file delivery. The orchestrator inspects a calculator
project, creates a task list, and delegates a README task to a coder.

## Run

Save the inline contents under the relative filenames shown. Set
DEEPSEEK_API_KEY in the environment; the provider declaration contains only a
placeholder. From the project root, run:

~~~console
$ uv run flowing repl . --cwd target
~~~

The separate programmatic parallel example runs with:

~~~console
$ uv run python demo_fanout.py
~~~

## Runtime entry and model configuration

Save as main.py:

~~~python
from flowing import Runtime


async def main(cwd: str | None = None, resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if cwd:
        runtime.provide("cwd", cwd)
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
~~~

~~~yaml
# providers.yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"

# models.yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash

# model-tags.yaml
tags:
  default: deepseek-flash
~~~

## Orchestrator and coder declarations

Save the declarations as root.fya and agents/coder/agent.fya:

~~~yaml
# root.fya
description: "Programming-task orchestrator: inspect, plan, and delegate implementation."
model_tag: default
tools:
  - subagent-invoke
  - ./tools/todo.py
subagents:
  - explore-agent
  - ./agents/coder
---
$system_prompt:
You are the programming-task orchestrator. Working directory: {{ cwd }}.
1. First dispatch explore-agent to inspect the directory and report.
2. Use the todo tool to turn implementation requests into a task list and show it.
3. Dispatch file-writing tasks to coder and summarize its result.
Do not read or write files yourself.
---
$script:
async def setup(self):
    self.cwd = self.inject("cwd")

# agents/coder/agent.fya
description: "Programming agent: reads and writes files, runs commands, and delivers."
model_tag: default
tools: [read, write, edit, grep, glob, bash, finish]
---
$system_prompt:
You are the programming agent. Working directory: {{ cwd }}.
Use absolute paths for file operations, do not guess, and do not touch anything
outside the working directory. On completion, call finish with a summary and the
list of changed files.
---
$script:
async def setup(self):
    self.cwd = self.inject("cwd")
~~~

Save as tools/todo.py:

~~~python
from pydantic import BaseModel, Field
from flowing import ScriptTool


class TodoArgs(BaseModel):
    tasks_text: str = Field(
        description="Task list text, one task per line; [x] marks a completed task")


class TodoTool(ScriptTool):
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
            raise ValueError("task list is empty")
        return {
            "total": len(items),
            "open": sum(not item["done"] for item in items),
            "items": items,
        }
~~~

## Target calculator

Save as target/pkg/calc.py:

~~~python
"""Example project: calculator."""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("divisor must not be 0")
    return a / b
~~~

The calculator returns a sum from add and a quotient from div. A zero divisor
raises ValueError with the message shown in the code.

## Input and visible result

Input:

~~~text
First ask explore to inspect the target project, then use todo to plan writing a README. Have coder write it and self-check the result before reporting back.
/exit
~~~

Visible interaction excerpt:

~~~text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke: inspect the target project structure.
[tool result] The target contains a calculator module and a README describing its working-directory role.
[tool_call] todo: plan a concise README covering the structure, calculator API, and usage.
[tool result] A task list is returned.
[tool_call] subagent-invoke: write the README, re-read it, and summarize the result.
[tool result] The README is complete and its API examples describe add and div.
~~~

The delivered README's core content is:

~~~~markdown
# Calculator working directory

This small Python project provides add(a, b) and div(a, b). The module has no
third-party dependencies. Run Python with the project root on the import path.

~~~python
from pkg.calc import add, div

print(add(2, 3))  # 5
print(div(6, 4))  # 1.5
~~~

div(1, 0) raises ValueError("divisor must not be 0").
~~~~

## Programmatic parallel fan-out

Save as demo_fanout.py:

~~~python
import asyncio
from flowing import launch


async def main() -> None:
    runtime = await launch(".", cwd="target")
    root = await runtime.get_agent("agent-main")

    async def job(name: str, topic: str) -> str:
        child = await root.create_subagent("@/agents/coder", name=name)
        try:
            result = await child.query(
                f"Write a one-sentence notes/{topic}.md defining {topic}; "
                "create the notes directory first and finish when done.")
            return f"{name}: status={result.status}"
        finally:
            await child.destroy()

    results = await asyncio.gather(
        job("coder-a", "adder"), job("coder-b", "divider"))
    for result in results:
        print(result)
    await runtime.shutdown()


asyncio.run(main())
~~~

Each child reports completed when its note-writing task finishes. Example note
contents are: “An adder adds two values using add” and “A divider divides two
values using div.”
