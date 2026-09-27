# 0-2 · Using Builtin Tools

## Prerequisites

Chapter [0-1 Your First Agent](0-1-hello-agent.md) introduces the project files, `launch`,
and REPL usage. This chapter embeds the complete Python entry point, agent definition, model
configuration, system prompt, sample notes, user input, and example interaction.
Save these materials in a project directory you create, and run the command there.

## Glossary for this chapter

| Term | One-sentence definition |
|---|---|
| Builtin tools | Builtin tools are factory tools in the `builtin::` namespace, including `read`, `write`, `bash`, `edit`, `grep`, `glob`, `finish`, and `subagent-invoke`. |
| Namespace omission | Namespace omission means that a bare name resolves in the `builtin::` namespace, so `read` is equivalent to `builtin::read`. |
| Registration ≠ visibility | The Runtime registers builtins, but an Agent exposes them to the LLM only when it declares them explicitly; dangerous tools are never attached silently. |

## Goals

This chapter gives the agent file capabilities without requiring tool code, and explains
the hard rule that registration does not imply visibility.

## Main text

### tools: declaring file tools

The `tools:` list at the head of a `.fya` declares which tools this Agent may use.
Builtin tools include `builtin::read` / `write` / `bash` / `edit` / `grep` / `glob` /
`finish` / `subagent-invoke`. This chapter uses two read-only tools:

```yaml
description: "Project Q&A assistant: can list directories, read files, and summarize."
model_tag: default
tools:
  - read      # Read a file; the line-window offset is optional.
  - glob      # Enumerate files by pattern.
---
$system_prompt:
You are a project Q&A assistant. The absolute path of this project's root directory is {{ env.PWD }}
(Always use this absolute path when calling file tools; do not guess any other directory).
When a question involves project content, you must first use glob to inspect the directory structure, then use read to read the relevant files, and then answer based on what you read; do not make things up from memory. Keep your answer within five sentences.
```

### Namespace omission

`builtin::` is the namespace of builtin tools. **Bare-name lookup hits the `builtin::` namespace**, so `read` is fully equivalent to `builtin::read` — the tutorial always writes bare names; the fully qualified form is reserved for scenarios that need disambiguation (for example, when your own tool has the same name as a builtin; the namespace mechanism is covered in 4-8).

### Registration ≠ visibility

The Runtime registers all builtin tools at construction time, but the LLM only sees entries explicitly declared by the Agent (`tools:` / `add_tool`) — **registration ≠ visibility**. Dangerous tools such as writing files or executing commands are therefore never silently attached: what your Agent can do is fully determined by your declarations. The `subagent-invoke` tool in chapter 0-3 and MCP tools in chapter 1-6 follow this rule as well.

### Give the agent a path anchor

The builtin file tools accept **absolute paths only** (a relative path requires an explicit `cwd` parameter), and the Agent does not know the working directory of the process. So for the agent to actually use file tools, the prompt must give an absolute-path anchor. This chapter obtains it via on-the-spot evaluation — `{{ env.PWD }}` is evaluated at every context assembly (the evaluation system is covered in 4-9):

```yaml
$system_prompt:
You are a project Q&A assistant. The absolute path of this project's root directory is {{ env.PWD }}
(Always use this absolute path when calling file tools; do not guess any other directory).
When a question involves project content, you must first use glob to inspect the directory structure, then use read to read the relevant files, and then answer based on what you read; do not make things up from memory. Keep your answer within five sentences.
```

## Out of scope

- This chapter does not cover ToolEntry aliases, parameter overrides, or parameter aggregation; chapter 4-4 covers them.
- This chapter does not explain how to write a ScriptTool; chapter 2-3 covers that topic, and chapter 4-5 explains execution mechanics.
- This chapter does not cover structured submission through `finish`, which is relevant only with binding-layer overrides; chapter 4-4 explains it.
- This chapter does not cover MCP or the zero-code `cli` and `request` tools; chapters 1-6 and 1-7 do.

## Main example

Save the following complete Python entry point and model configurations in the
project directory. Keep the credential as an environment-variable placeholder.

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

Save those configuration blocks as `providers.yaml`, `models.yaml`, and
`model-tags.yaml`. Save the following two complete note contents as
`notes/使用说明.md` and `notes/路线图.md`:

```markdown
# Usage Guide

This directory is a demo notebook library.

## Installation

Python 3.13 or higher is required.

## Common commands

- Run `uv sync` to install dependencies.
- Run `uv run pytest` to run the tests.

## Cautions

Credentials are always held in environment variables; never write them into any file.
```

```markdown
# Roadmap

- The team plans to finish the core features in 2026-Q3.
- The team plans to release version 1.0 in 2026-Q4.
```

The `root.fya` block above is the complete Agent definition and prompt. After
setting `DEEPSEEK_API_KEY`, start the REPL and enter the question shown below.

```console
$ uv run flowing repl .
(agent-main)>>> Which files are in the notes folder? Summarize the main points of notes/使用说明.md.
[tool_call] glob {"path": "<project-root>/notes", "pattern": "**/*"}
[tool_call] read {"path": "<project-root>/notes/使用说明.md"}
[tool:completed] glob -> <project-root>/notes/使用说明.md; <project-root>/notes/路线图.md
[tool:completed] read -> 0\t# Usage Guide
1\t
2\tThis directory is a demo notebook library.
3\t
4\t## Installation
5\t
6\tPython 3.13 or higher is required.
7\t
8\t## Common commands
9\t
10\t- Run `uv sync` to install dependencies.
11\t- Run `uv run pytest` to run the tests.
12\t
13\t## Cautions
14\t
15\tCredentials are always held in environment variables; never write them into any file.
The notes folder contains `使用说明.md` and `路线图.md`. The usage guide says that Python 3.13 or later is required, lists `uv sync` and `uv run pytest` as common commands, and says to keep credentials in environment variables rather than files.
(agent-main)>>> /exit
```

The interaction includes the complete user input, both tool-call payloads, the two note names, every line returned by `read`, and an example answer grounded in that text. Wording may vary with the model response. How tool calls fit into the turn loop is the topic of 1-1.

In this transcript, `<project-root>` stands for the absolute path of the project
directory at runtime; the prompt obtains that value from `{{ env.PWD }}`. Replace
the placeholder with your own project path when interpreting or replaying the
displayed tool calls.

All files, prompts, inputs, and illustrative outputs required by this example are
included above; no external example material is needed.

## Summary

1. The `tools:` declaration enables builtin file capabilities without requiring tool code.
2. An Agent can omit the `builtin::` namespace because bare `read` resolves to `builtin::read`.
3. **Registration ≠ visibility**: the LLM sees only explicitly declared entries, and dangerous tools are never attached silently.
4. File tools accept only absolute paths, so the prompt must provide a path anchor through `{{ env.PWD }}`.
