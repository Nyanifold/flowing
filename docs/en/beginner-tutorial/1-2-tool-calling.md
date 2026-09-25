# 1-2 · Tool Calling: Separation of Intent and Execution

> Reproduction requirements: Python 3.13+, uv, an available Flowing CLI, a DeepSeek API key, and network access from the terminal.
> Complete configuration, prompt, data, input, and sample output are included at the end of this chapter. Save each block under its stated filename in a new empty directory before running it.

> Prerequisites: Chapter 1-1 "The Agent Runtime"

## What this chapter covers

The core design of tool calling: the model does not execute actions directly;
instead, it emits structured call intents, and the framework executes them.
This chapter covers the structure of this separation, its benefits, and the
full course of one call round.

## Background

The industry-standard function calling convention: the request carries a
tool catalog — the name, natural-language description, and parameter
definition (JSON Schema) of each tool; the model's response may contain one
or more structured call intents; the application side (framework) executes
them, appends the results to the history, and calls the model again.

This convention resolves the tension between "the model can only output
text" and "applications need the model to drive actions": instead of parsing
the model's natural-language output to guess the action (fragile, exploitable
by prompt injection, and unauditable), the model states its intent in a
constrained output channel.

## Core concepts

### The three-part structure of one call round

Take "query an order" as the example:

```python
# ① Before the call: the tool catalog is sent with the request
tools = [{"name": "query-order",
          "description": "Query order status by order ID",
          "parameters": {"type": "object",
                         "properties": {"order_id": {"type": "string"}},
                         "required": ["order_id"]}}]

# ② Model response: a structured call intent
{"name": "query-order", "arguments": {"order_id": "4521"}}

# ③ The application side executes and feeds the result back as a message
result = query_order(order_id="4521")
messages.append(tool_result(result))
text = llm(messages)
```

The responsibility of each part: writing and sending the catalog is on the
application side; generating the intent is on the model; execution and
feedback are on the framework. What the model sees is "I propose to call X";
what the user sees is "the system completed X" — the validation, approval,
and logging in between are all completed by the framework in the execution
stage.

Start a session using the complete example at the end of this chapter and
enter: "What files are in the notes/ directory? Please summarize the main
content of notes/使用说明.md." The three-part structure is explained below;
model wording may vary, and all tool inputs and results are included at the end:

Line by line, these map onto the three parts: the two `[tool_call]` lines are
part ② — the structured intents output by the model; the two
`[tool:completed]` lines are part ③ — the results returned by the framework;
the final text is the model's answer based on those results. Part ① happens
before the request is sent: this agent's tool catalog has only two entries,
glob and read, as specified by its declared tool table.

### Benefits of the framework holding execution power

With execution on the framework side, three capabilities follow directly:

1. **Interception before execution**: inspect, rewrite, or reject before the
   action lands (the hook point for approval and security policies);
2. **Parameter validation**: validate the arguments the model filled in
   against the schema; illegal calls never enter execution;
3. **Audit and billing**: the tool name, arguments, result, and duration of
   every call can be recorded completely.

Conversely, any design that "lets the model execute directly" (for example,
teaching the model to write code in the prompt and having the application
eval it) loses all three capabilities at once.

The conceptual form of pre-execution interception:

```python
def before_execute(call):                     # the framework always passes this point before executing
    if is_dangerous(call) and not approved(call):
        return Deny(reason="Needs human confirmation")   # rejected: does not enter execution
    return call                               # allowed: enters execution
```

In the transcript above, the two `[tool:completed]` entries are also
evidence of the third capability: the name and full result of every call
remain in the session record and can be audited afterward.

### Tool descriptions are part of the contract

The description fields in the tool catalog are written for the model to
read. They serve two functions at once: helping the model judge "when should
this tool be used", and constraining the semantics of argument filling. Vague
descriptions directly cause routing errors and argument errors. The tool
catalog should therefore be maintained to the standard of interface design:
single responsibility, usage scenarios stated, and the smallest possible
starting set.

## Common misconceptions

1. **Treating model output as executable code.** The text output channel is
   untrustworthy; actions must go through the path of structured intent plus
   framework execution;
2. **Writing tool descriptions carelessly.** Description quality determines
   the accuracy of routing and argument filling, and is the focus of tool
   catalog maintenance;
3. **Loose parameter schemas.** The schema is the contract between the model
   and the execution layer; state the types, constraints, and requiredness
   explicitly, so both ends have a basis for validation.

## Exercises

1. Write the complete three-part sequence for "query an order" in
   pseudocode, marking the responsible party of each part;
2. Write a description and a parameter schema for a "send email" tool; the
   description must state the applicable scenarios, and the schema must
   constrain the recipient format and the body length;
3. In the complete example below, ask a different question (for example,
   ask only about the roadmap notes), find the model's intent lines and the
   framework's executed feedback in the sample output, and identify which
   part of the three-part structure each belongs to.

## Complete example: tool table, prompt, input, and result

Run this example in a new empty directory and create the `notes/` child
directory. Replace `...` with your API key and set it only as an environment
variable, then save the following files. All paths are relative to the
current directory; no other file needs to be found or copied.

`pyproject.toml`:

```toml
[project]
name = "flowing-chapter-1-2"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["flowing-agent"]

[tool.uv]
package = false
```

`main.py`:

```python
from flowing import Runtime


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

`root.fya` (the complete prompt and visible tool catalog):

```yaml
description: "Project Q&A assistant: can list directories, read files, and summarize."
model_tag: default
tools:
  - read
  - glob
---
$system_prompt:
You are a project Q&A assistant. The absolute path of this project's root is {{ env.PWD }} (always use this absolute path in file-tool calls; do not guess other directories). When a question concerns notes/, first use glob to inspect the files in notes/, then use read to read the relevant files, and answer based on what you actually read; do not make things up from memory. Keep your answer within five sentences.
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

Run these commands from the directory containing the files:

```sh
uv sync
export DEEPSEEK_API_KEY="..."
uv run flowing repl .
```

All note data used by this example:

`notes/使用说明.md`:

```markdown
# Usage Notes

This directory is a demo notes library.

## Installation

Python 3.13 or later is required.

## Common commands

- `uv sync`: install dependencies
- `uv run pytest`: run tests

## Notes

Credentials must always be held in environment variables; never write them into any file.
```

`notes/路线图.md`:

```markdown
# Roadmap

- 2026-Q3: complete core functionality
- 2026-Q4: release version 1.0
```

Input:

```text
What files are in the notes/ directory? Please summarize the main content of notes/使用说明.md.
/exit
```

After running `uv run flowing repl .`, an illustrative interaction is:
`<project-root>` means the current working directory. Model wording and file
enumeration order may vary:

```console
(agent-main)>>>What files are in the notes/ directory? Please summarize the main content of notes/使用说明.md.
[tool_call] glob {"pattern": "notes/**/*", "path": "<project-root>"}
[tool_call] read {"path": "<project-root>/notes/使用说明.md"}
[tool:completed] glob -> notes/使用说明.md
notes/路线图.md
[tool:completed] read -> 0  # Usage Notes
1
2  This directory is a demo notes library.
3
4  ## Installation
5
6  Python 3.13 or later is required.
7
8  ## Common commands
9
10 - `uv sync`: install dependencies
11 - `uv run pytest`: run tests
12
13 ## Notes
14
15 Credentials must always be held in environment variables; never write them into any file.
The project contains the notes `notes/使用说明.md` and `notes/路线图.md`. The usage note requires Python 3.13 or later, lists commands for installing dependencies and running tests, and says to keep credentials in environment variables only.
(agent-main)>>>/exit
```

## Summary

1. Tool calling separates intent from execution: the model proposes, the
   framework executes;
2. Three-part structure: catalog delivery -> intent generation -> execution
   feedback;
3. Execution power on the framework side brings interception, validation,
   and audit;
4. Tool descriptions and schemas are the contract between the model and the
   execution layer.
