# Example: 5-2 Organization Patterns

This example combines model-directed delegation with code-directed fan-out.
The orchestrator inspects a small calculator project, turns a request into a
task list, and delegates documentation work. A second script creates two
subagents concurrently.

## Run

Save the inline content under the relative filenames shown. Set
DEEPSEEK_API_KEY in the environment; the provider declaration contains only a
placeholder. From the project root, run the orchestrator:

~~~console
$ uv run flowing repl . --cwd target
~~~

The Python fan-out demonstration is:

~~~console
$ uv run python demo_fanout.py
~~~

## Runtime entry and model configuration

Save as main.py:

~~~python
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

## Orchestrator, coder, and task-list tool

Save the declarations as root.fya and agents/coder/agent.fya:

~~~yaml
# root.fya
description: "Programming task orchestrator: inspect, plan, and delegate implementation."
model_tag: default
tools:
  - subagent-invoke
  - ./tools/todo.py
subagents:
  - explore-agent
  - ./agents/coder
---
$system_prompt:
You are the programming task orchestrator. Working directory: {{ cwd }}.
1. First dispatch explore-agent to inspect the directory structure and report.
2. Use the todo tool to turn implementation requests into a task list and show it.
3. Dispatch file-writing work to coder and summarize its result.
Do not read or write files yourself.
---
$script:
async def setup(self):
    self.cwd = self.inject("cwd")

# agents/coder/agent.fya
description: "Programming agent: reads and writes files, executes commands, and delivers."
model_tag: default
tools: [read, write, edit, grep, glob, bash, finish]
---
$system_prompt:
You are the programming agent. Working directory: {{ cwd }}.
Use absolute paths for file operations, do not guess, and do not touch anything
outside the working directory. When done, call finish with a summary and the
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

## Target project data

The calculator module is the complete target code:

~~~python
"""Example project: calculator."""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("divisor must not be 0")
    return a / b
~~~

The initial target README is fully included in the
[target project guide](target/README.md), which also gives copyable calls and
their output.

## Conversation input and visible result

Input:

~~~text
First ask explore to inspect the target project's structure. Use todo to plan writing a README, then dispatch coder to write it and summarize the result.
/exit
~~~

Visible interaction excerpt:

~~~text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke: inspect the target project.
[tool result] The target is a small Python calculator with add and div operations.
[tool_call] todo: outline the README task.
[tool result] The plan covers project structure, API behavior, usage, and limitations.
[tool_call] subagent-invoke: write the README and return a summary.
[tool result] The README describes the calculator, its API, and runnable examples.
~~~

The completed guide documents add(2, 3) as 5 and div(6, 4) as 1.5.

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

Expected console output:

~~~text
coder-a: status=completed
coder-b: status=completed
~~~

Example note contents:

~~~markdown
# adder

An adder adds two values using the add operation.

# divider

A divider divides two values using the div operation.
~~~
