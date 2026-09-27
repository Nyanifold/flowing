# 1-3 · Tool Specialization: The Structural Definition of Capability Boundaries

> Reproduction requirements: Python 3.13+, uv, an available Flowing CLI, a DeepSeek API key, and network access from the terminal.
> Complete configuration, prompt, data, input, and sample output are included at the end of this chapter. Save each block under its stated filename in a new empty directory before running it.

> Prerequisites: Chapter 1-2 "Tool Calling"

## What this chapter covers

This chapter examines what determines an agent's capability boundary. The
central claim: the capability boundary is defined **structurally** by the
tool table, not by prompt conventions. Therefore, the question "what can
this agent do?" should be answered by reading the tool table, not the
system prompt.

## Background

A prompt's influence on model behavior is probabilistic — it raises the
probability of a behavior, but the model can always deviate. Where
determinism is required (permissions, security, compliance), engineering
does not accept probabilistic constraints. The tool table provides a
structural alternative: the model can only propose calls to tools that
appear in the tool table of its context; tools not listed are unreachable
at the protocol level. The constraint changes from "the model very likely
complies" to "violation is structurally impossible".

This principle is consistent with the principle of least privilege in
conventional software: an execution principal holds only the minimum set
of capabilities required to carry out its duties. In an agent system, the
execution principal is the agent, and the capability carrier is the tool
table.

## Core concepts

### The capability boundary is determined by the composition of the tool table

An agent mounted with read-only tools cannot perform write operations —
this holds no matter how the prompt describes it, and no matter whether the
model "knows how to write". The model may possess all the knowledge needed
to produce correct content, but without the corresponding tool the write
action cannot occur at the protocol level.

Start a session using the complete example at the end of this chapter and
issue two tasks: first ask the agent to inspect the notes directory, then
ask it to create a file there. The interaction below illustrates the second
task; all tool inputs and file contents are included at the end, and model
wording may vary:

```console
(agent-main)>>> Please create a new file summary.md in the notes directory and write the key points of 使用说明.md into it.
[tool_call] read {"path": "<project-root>/notes/使用说明.md"}
[tool:completed] read -> 0  # Usage Notes
1
2  This directory is a demo notes repository.
3
4  ## Installation
5
6  Python 3.13 or later is required.
7
8  ## Common Commands
9
10 - `uv sync`: install dependencies
11 - `uv run pytest`: run the tests
12
13 ## Cautions
14
15 Credentials are always held via environment variables; never write them into any file.
I can't create `summary.md`: my available tools are `read`, `grep`, and `glob`, and none of them can write. Here is content you can save yourself:

# Summary: Usage Notes

- This directory is a demo notes repository.
- Python 3.13 or later is required.
- Use `uv sync` to install dependencies and `uv run pytest` to run tests.
- Keep credentials in environment variables; never write them into a file.
```

Line by line: after the prompt comes your write request; the model states
that it has no write/create tool and that its toolset is read-only. The
answer explains why the write cannot happen, then does the part it can do —
it reads `使用说明.md` and organizes the key points into a draft for you to
save yourself. After the session, both note files remain unchanged and no
new file has been created. The boundary is not the model "refraining from writing" — it
simply has no write tool to reach.

This yields two engineering corollaries:

1. **Review an agent's behavior by looking at the tool table first.** To
   predict what an agent can do, reading its tool declarations is more
   reliable than reading its system prompt;
2. **New capability = new tool + updated description.** A capability
   change is a structural change — reviewable and reversible — not a tweak
   of prompt wording.

### The tool table is the public interface

The tool table is both the model's routing basis and the framework's
validation basis, and it should be maintained to the standard of interface
design:

- **Single responsibility**: one tool does one kind of thing; a
  "universal tool" inflates the parameter schema and lowers routing
  accuracy;
- **The description states the usage scenario**: the description is read
  by the model; "when to use me" influences behavior more than "what
  parameters I have";
- **Start from a minimal set**: every tool consumes context and increases
  the selection error rate; the default answer for permissions is no —
  grant on demand.

The complete declaration used in this example — the capability boundary is
determined entirely by the structure of this table:

```yaml
description: "Note-library assistant: read-only; it can list directories, search and read notes, and summarize."
tools:
  - read
  - grep
  - glob
```

For comparison: this agent's system prompt contains no phrase like "you must
not write files" — the refusal in the example above is not taught by the
prompt; it follows from the structure of the tool table.

### Registered vs. visible: two states

A tool has two states in its lifecycle, and distinguishing them is
necessary for configuring the capability boundary correctly:

- **Registered**: the tool is in the framework's global registry — the
  capability exists;
- **Visible**: the tool appears in an agent's context tool table — this
  agent is allowed to propose it.

Only visible tools can be proposed by the model. High-privilege tools
(file writing, command execution) should be registered and explicitly
enabled per agent, rather than visible to all agents by default. In this
example, the framework can register tools such as file writing and command
execution, but the agent's `tools:` list names only the three read-only tools, so write tools are not
visible to this agent — the refusal in the transcript above is exactly
this mechanism at work.

## Common misconceptions

1. **Using the prompt to take back a capability that has already been
   granted.** Capabilities should be controlled at the tool-table level;
   prompt constraints are probabilistic and cannot serve as a permission
   mechanism;
2. **The more tools, the better.** The tool table is an interface: more
   tools mean more context overhead and a higher routing error rate;
   evolve it from a minimal set;
3. **Writing a vague description in the name of flexibility.** A "generic
   assistant" description is equivalent to no description; stating the
   applicable task types clearly is what raises routing accuracy.

## Exercises

1. Design a minimal tool table each for an "order assistant" and a "code
   reviewer", and explain what determines the difference between the two;
2. Infer the agent's capability boundary from the `tools:` list in the
   complete example below, then compare your inference with its behavior in
   the sample interaction.

## Complete example: a read-only tool table and a write request

Create a `notes/` child directory in a new empty directory and save the
following content. Replace `...` with your API key and set it only as an
environment variable; do not put a real credential in a configuration file.
This chapter includes the agent code, declaration, model settings,
complete prompt, readable data, input, and sample output.

`pyproject.toml`:

```toml
[project]
name = "flowing-chapter-1-3"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["flowing-agent"]

[tool.uv]
package = false
```

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

`root.fya` (complete tool table and system prompt):

```yaml
description: "Note-library assistant: read-only; it can list directories, search and read notes, and summarize."
model_tag: default
tools:
  - read
  - grep
  - glob
---
$system_prompt:
You are a note-library assistant. The absolute path of this project's root is {{ env.PWD }} (always use this absolute path when calling file tools; do not guess other directories). The notes are in the notes directory. When answering questions about notes, first use glob to inspect the directory, use grep to search as needed, and use read to fetch relevant files; then answer based on what you actually read. Do not make things up from memory.
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

All readable data:

`notes/使用说明.md`:

```markdown
# Usage Notes

This directory is a demo notes repository.

## Installation

Python 3.13 or later is required.

## Common Commands

- `uv sync`: install dependencies
- `uv run pytest`: run the tests

## Cautions

Credentials are always held via environment variables; never write them into any file.
```

`notes/路线图.md`:

```markdown
# Roadmap

- 2026-Q3: complete core functionality
- 2026-Q4: release version 1.0
```

Complete input:

```text
Please check what files are in the notes directory and report back faithfully.
Please create a new file summary.md in the notes directory and write the key points of 使用说明.md into it.
/exit
```

Run `uv run flowing repl .` in the same empty directory. This interaction
illustrates the behavior; model wording may vary, and internal reasoning is
not shown:

```console
(agent-main)>>>Please check what files are in the notes directory and report back faithfully.
[tool_call] glob {"pattern": "**/*", "path": "<project-root>/notes"}
[tool:completed] glob -> <project-root>/notes/使用说明.md
<project-root>/notes/路线图.md
The notes directory contains two Markdown files: `使用说明.md` and `路线图.md`.
(agent-main)>>>Please create a new file summary.md in the notes directory and write the key points of 使用说明.md into it.
[tool_call] read {"path": "<project-root>/notes/使用说明.md"}
[tool:completed] read -> 0  # Usage Notes
1
2  This directory is a demo notes repository.
3
4  ## Installation
5
6  Python 3.13 or later is required.
7
8  ## Common Commands
9
10 - `uv sync`: install dependencies
11 - `uv run pytest`: run the tests
12
13 ## Cautions
14
15 Credentials are always held via environment variables; never write them into any file.
I can't create `summary.md`: my available tools are `read`, `grep`, and `glob`, and none of them can write. Here is content you can save yourself:

# Summary: Usage Notes

- This directory is a demo notes repository.
- Python 3.13 or later is required.
- Use `uv sync` to install dependencies and `uv run pytest` to run tests.
- Keep credentials in environment variables; never write them into a file.
(agent-main)>>>/exit
```

## Summary

1. The capability boundary is defined structurally by the tool table; the
   prompt carries no constraint responsibility;
2. Maintain the tool table to the standard of interface design: single
   responsibility, descriptions aimed at routing, and a minimal starting
   set;
3. Registered and visible are two states; high-privilege tools are enabled
   explicitly per agent;
4. The correct place to review and adjust an agent's capabilities is the
   tool table.
