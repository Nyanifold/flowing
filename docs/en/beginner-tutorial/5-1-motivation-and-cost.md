# 5-1 · Motivation for Splitting and Its Costs

> Self-contained example: the complete runtime configuration, agent definition, input data, and representative output appear below. Create the listed relative files in your current practice directory, then run `uv run flowing repl .`.
> The conversation example requires the `DEEPSEEK_API_KEY` environment variable. Do not put a real credential in a configuration file.

> Prerequisites: Chapter 1-3 "Tool Specialization"

## What this chapter covers

A decision framework for moving from a single agent to multi-agent: the three
types of extension limits of a single agent, the three types of costs
introduced by splitting, and the criteria for when splitting is worthwhile.

## Background

The same decision recurs throughout the history of software architecture:
when a unit grows too large, whether to split it into multiple
single-responsibility units. The trade-off structure is identical every
time — the benefits of splitting (cohesion, isolation, parallelism) versus
its costs (communication, consistency, operational surface). Multi-agent is
the latest form of this same decision applied to agent systems, and the
criteria carry over directly.

## Core concepts

### The three types of extension limits of a single agent

| Limit | Symptom | Root cause |
|---|---|---|
| Context | Knowledge from multiple domains, tool schemas, and long history compete for the same window | The window is a hard cap; the more content it holds, the more diluted the attention |
| Permissions | The party that reads code and the party that pays hold the same tool table | The union of capabilities is open to every request and cannot be isolated per request |
| Throughput | One logical turn executes sequentially | The serial nature of the event-driven loop |

The first two are structural limits — the tool table and the window cannot
fork on demand, and hitting either forces a split; the third is a
performance problem and depends on throughput requirements.

### The three types of costs introduced by splitting

| Cost | Content | Order of magnitude |
|---|---|---|
| Communication | Extra model calls for dispatch and rollup, plus the catalog space they occupy in context | Tens to thousands of tokens per turn |
| Consistency | Views across multiple contexts may diverge; merging results requires validation | Grows with the length of the collaboration chain |
| Debugging | A single failure must be traced across components to locate responsibility | Investigation path = length of the collaboration chain |

These three costs are not defects; they are inherent properties of the
structure. The only question is whether the benefits outweigh them.

The example below is a minimal system that has already been split: one orchestrator plus one read-only explore agent. The two inputs happen in the same session: first list the notes, then request a summary file based on one note. Create the files listed later in your current practice directory, run `uv run flowing repl .`, and enter these lines:

**Input**

```text
Please check what files are in the notes directory and report back honestly.
Please create a new file 总结.md in the notes directory with the key points of 使用说明.md.
/exit
```

**Representative output**

```text
(agent-main)>>> Please check what files are in the notes directory and report back honestly.
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"explore-agent","prompt":"List every file and subdirectory under <practice-root>/notes. Report only names actually found; do not read or guess their contents."}
[tool:completed] subagent-invoke ->
There are two regular files and no subdirectories under <practice-root>/notes: 使用说明.md and 路线图.md.
[thinking] (reasoning trace omitted)
(agent-main)>>> Please create a new file 总结.md in the notes directory with the key points of 使用说明.md.
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"explore-agent","prompt":"Read <practice-root>/notes/使用说明.md, summarize its key points, and try to create 总结.md in the same directory. If you cannot write files, say so clearly and do not claim the file was created."}
[tool:completed] subagent-invoke ->
The key points are: this is a demo notes library; Python 3.13 or later is required; common commands are uv sync and uv run pytest; credentials must be supplied through environment variables and must not be written to files. The explore agent is read-only and cannot create 总结.md.
[thinking] (reasoning trace omitted)
(agent-main)>>>
```

Natural-language wording may differ at runtime. The checkable outcomes are the two listed names and the read-only agent's refusal to write.

Looking line by line at this session, the benefits and the costs are both
present at the same time: the view-type task is executed by a read-only
agent with a very small tool table (the benefit of permission isolation —
it is structurally incapable of writing files); the write request is
rejected by that same structure (the cost of isolation — this system
genuinely cannot write); the full text of every delegation package is
visible in the `[tool_call]` lines — the task, the paths, and the
reporting requirements must all be carried explicitly, because the worker
cannot see the orchestrator's history; and the subagent's report is long —
the orchestrator relays it verbatim, so it all enters the orchestrator's
context (the visible form of the communication cost). Criteria
demonstration: of the "three questions," "tool tables differ
significantly" and "capabilities must be isolated" both hold, so this
system is worth splitting.

### Complete example materials

All files below belong to this example and are reproduced in full. Create them in an empty practice directory. `路线图.md` is used only as a directory-enumeration sample, so it may be empty. `explore-agent` is Flowing's built-in read-only subagent.

```console
touch notes/路线图.md
export DEEPSEEK_API_KEY=sk-your-key-here
uv run flowing repl .
```

**`providers.yaml`**

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

**`models.yaml`**

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

**`model-tags.yaml`**

```yaml
tags:
  default: deepseek-flash
```

**`main.py`**

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

**`root.fya`**

```yaml
description: "Read-only task orchestrator: delegates view, check, and read requests to the exploration agent."
model_tag: default
tools:
  - subagent-invoke
subagents:
  - builtin::explore-agent
---
$system_prompt:
You are the task orchestrator. The absolute path of this practice directory is {{ env.PWD }}.
For every view, check, or read request, use subagent-invoke to delegate it to explore-agent
and give the target's absolute path in the task. Report the result honestly; do not read
files yourself or fabricate contents. The explore agent is read-only; if a user requests
a file write, explain this capability boundary and do not claim that a file was written.
```

**`notes/使用说明.md`**

```markdown
# Usage Notes

This directory is a demo notes library.

## Installation

Python 3.13 or later is required.

## Common Commands

- `uv sync`: install dependencies
- `uv run pytest`: run tests

## Caution

Credentials must be supplied through environment variables and must not be written to any file.
```

After creating the files, enter the complete input block above at the prompt. Model-generated wording can vary; the fixed acceptance points are the directory enumeration and the read-only agent's refusal to write.

### Criteria

The necessary and sufficient condition for splitting compresses into one
sentence: **responsibilities are heterogeneous or permissions must be
isolated, and the benefits exceed the costs above**. Expanded into three
concrete questions:

1. Do different request types differ significantly in their tool tables?
   (If not → a single agent with different prompts is enough)
2. Is there a combination of capabilities that "must never be held by the
   same party"? (If yes → splitting is mandatory)
3. Can the task be parallelized to the point of requiring multiple loops?
   (If yes → consider programmatic parallelism; a standing collaboration
   structure is not necessarily required)

When none of the three questions is satisfied, multi-agent is
over-engineering.

## Common misconceptions

1. **Splitting for its own sake.** Multi-agent does not improve the quality
   of a single task; incorrect dispatch just produces incorrect results
   faster;
2. **Ignoring communication costs.** Every subordinate's receipt passes
   through the orchestrator's context; the deeper the chain, the more
history is carried redundantly;
3. **Treating parallelism as a default benefit.** Homogeneous batch tasks
   only need programmatic parallelism (Chapter 5-2); standing collaboration
   structures solve heterogeneous division of labor.

## Exercises

1. For a "customer support system," list the request types and judge: which
   types differ significantly in their tool tables, and which capability
   combinations must be isolated;
2. Give two designs for that system (a single agent with multiple prompts /
   an orchestrator plus three subordinates), estimate one cost and one
   benefit for each, and explain the trade-off;
3. Review this chapter's example project with the criteria: write the
   answers to the three questions, and point out which criterion is the
   weakest in this system.

## Summary

1. A single agent's extension limits: context, permissions, throughput;
   the first two are structural;
2. The costs of splitting: communication, consistency, debugging —
   inherent properties of the structure;
3. Criterion: responsibilities are heterogeneous or permissions must be
   isolated, and the benefits cover the costs;
4. When the criterion is not satisfied, multi-agent is over-engineering.
