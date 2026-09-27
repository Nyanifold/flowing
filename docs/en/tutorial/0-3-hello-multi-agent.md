# 0-3 · Hello Multi-Agent

## Prerequisites

Chapters [0-1 The First Agent](0-1-hello-agent.md) and
[0-2 Using Builtin Tools](0-2-use-builtin-tools.md) introduce the project files,
`tools:` declarations, namespace omission, and registration-versus-visibility rule.
This chapter embeds all project code, prompts, configuration, sample notes, inputs, and example
interaction. Save them in a project directory you create, then run the command there.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| orchestrator | The root agent acts as an orchestrator by dispatching tasks to subagents and aggregating their results. |
| subagent | A subagent is an agent instance invoked by another agent; this chapter uses the stock, read-only `explore-agent`. |
| `subagents:` declaration | A `.fya` header uses `subagents:` to declare which subagent **types** an agent may use; this binds a type rather than creating an instance, as chapter 2-1 explains. |
| `subagent-invoke` | The LLM uses the builtin `subagent-invoke` tool to invoke a subagent; as in chapter 0-2, an Agent must declare the tool explicitly because registration does not imply visibility. |
| type binding | A subagent declaration binds a type and its usage, while invocation creates the instance; chapter 2-2 explains creation and resume. |

## Goals

This chapter introduces multi-agent collaboration through an orchestrator and the stock
explore agent. The user issues two requests — "inspect the directory" and "write a file" —
and the example shows how the explore agent responds within its capability boundary and
how the orchestrator aggregates and delivers each outcome.

## Main text

### `subagents:` in one sentence

The `subagents:` list in the `.fya` header declares which subagent **types** this agent
may use:

```yaml
description: "Task orchestrator: dispatches read-only inspection tasks and reports results faithfully."
model_tag: default
tools:
  - subagent-invoke
subagents:
  - explore-agent   # This bare name omits the namespace and resolves to builtin::explore-agent.
---
$system_prompt:
You are the task orchestrator. The project root is {{ env.PWD }}.
For every inspect, check, or read request, call subagent-invoke with explore-agent and
include the absolute target path in the prompt. Report its result faithfully. For a
request to create or modify files, you may ask explore-agent to confirm its capability;
if it only has read tools, do not claim success and explain this boundary honestly.
```

This is the complete `root.fya` declaration and prompt. The Runtime provides the stock
read-only explore subagent; `subagent-invoke` is the only tool declared for the root agent.

Builtin namespace lookup lets the agent refer to the stock `explore-agent` by its bare name;
the fully qualified name `builtin::explore-agent` is useful when disambiguation is needed.
The agent invokes subagents through `subagent-invoke`, which must be declared in `tools:`
because **registration ≠ visibility**.

What `subagents:` declares is a **type binding, not an instance**: no subagent has been
created at this point; the instance is born at invocation time (creation and resume are
the topic of 2-2).

### Automatic routing triggered by a single query

The user sends a single ordinary message, and the LLM completes the dispatch automatically
after seeing the orchestrator prompt and the `<available_subagents>` catalog:

1. The orchestrator judges the task type and calls `subagent-invoke` with `explore-agent`.
2. Invocation creates the subagent, which runs its own turn independently with its own tool set.
3. The subagent's final output returns to the orchestrator in a `SubagentResult`, which separates `result` from `subagent_status`; chapter 2-2 explains this structure.
4. The orchestrator aggregates the result and delivers it to the user.

**The capability boundary is guaranteed by the composition of the tool set**: the tool set
of `explore-agent` is fixed to the read-only `read` / `grep` / `glob` — "read-only" is not a
matter of prompt self-discipline; it simply has no write tools at all. So a file-writing
request only gets an honest report of the capability boundary, rather than an overstepping
write or a fabricated result.

## Out of scope

- This chapter does not cover resuming the same instance with `name=` or `resume=`; chapter 2-2 explains resume with memory.
- This chapter does not cover parameter overrides, `specified`, `visible=False`, or catalog rendering; chapters 2-1 and 4-4 cover those topics.
- This chapter does not explain how to write custom subagent types; chapter 2-1 does.
- This chapter does not detail the `SubagentResult` fields or the `on_subagent_invoke` and `on_subagent_returns` hooks; chapters 2-2 and 4-6 cover them.

## Main example

Prepare the project from the following inline materials. The full agent declaration and
prompt appear above. Save this complete Python entry point as `main.py`:

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

Save these complete model configurations as `providers.yaml`, `models.yaml`, and
`model-tags.yaml`. Keep the credential as an environment-variable placeholder:

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

Create the two note files with the following complete contents:

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

Save the notes as `notes/使用说明.md` and `notes/路线图.md`, set
`DEEPSEEK_API_KEY`, start the REPL from the project root, and enter the two messages
shown below. `<project-root>` in the displayed tool interaction is a placeholder for
the runtime project path, not a machine-specific path.

```console
$ uv run flowing repl .
(agent-main)>>> Please check what files are in the notes directory and report back faithfully.
I'll delegate this read-only inspection to the explore agent.
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "explore-agent", "prompt": "Inspect <project-root>/notes and report the filenames and content you can confirm. Read only; do not modify anything."}
[tool:completed] subagent-invoke -> The notes directory contains `使用说明.md` and `路线图.md`. The usage guide requires Python 3.13 or later, lists `uv sync` and `uv run pytest`, and says to keep credentials in environment variables. The roadmap lists finishing core features in 2026-Q3 and releasing version 1.0 in 2026-Q4.
The notes contain two files: the usage guide summarizes the environment, common commands, and credential rule; the roadmap lists two milestones.
(agent-main)>>> Please create a new file 总结.md in the notes directory and write the key points of 使用说明.md into it.
I cannot claim a file was written; the explore agent is read-only, so I will check and report that capability limit honestly.
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "explore-agent", "prompt": "Create 总结.md under <project-root>/notes with a summary of 使用说明.md. If you do not have file-writing capability, state that clearly and do not claim success."}
[tool:completed] subagent-invoke -> I cannot create the file: my available tools are read-only (`read`, `grep`, and `glob`) and do not include file creation or writing. `总结.md` was not created.
I cannot create the file: the explore agent has no write tool, so `总结.md` was not created.
(agent-main)>>> /exit
```

The first request demonstrates read-only exploration. The second demonstrates the
write boundary: the explore agent has no write tool and reports that no file was created.
Both `[thinking]` placeholders mark omitted reasoning; the user messages, complete tool
prompts, tool results, and visible responses remain in the example.

## Summary

1. The `subagents:` declaration binds a type rather than creating an instance, which is born only when invoked.
2. Registration does not imply visibility for `subagent-invoke`, so the Agent must declare the tool explicitly.
3. A single query triggers automatic routing: the orchestrator dispatches the task, the subagent works independently, and its result returns for aggregation.
4. The tool set enforces the capability boundary, so a read-only explore agent can only report honestly that it cannot write files.
