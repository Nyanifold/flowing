# Example: 5-1 Motivation for Splitting and Its Costs

This example separates inspection from file modification. The orchestrator
delegates inspection to the read-only explore-agent and must report its
capability boundary honestly rather than claim that it created a file.

## Run

Save the inline contents under the relative filenames shown. Set
DEEPSEEK_API_KEY in the environment; the configuration contains only a
placeholder. From the project root, run:

~~~console
$ uv run flowing repl .
~~~

## Runtime entry and model configuration

Save as main.py:

~~~python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
~~~

Save the following as providers.yaml, models.yaml, and model-tags.yaml:

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

## Orchestrator declaration and prompt

Save as root.fya:

~~~yaml
description: "Task orchestrator: delegates view/check/read tasks to the explore agent, then relays the report."
model_tag: default
tools:
  - subagent-invoke
subagents:
  - explore-agent
---
$system_prompt:
You are the task orchestrator. The project root is {{ env.PWD }}.
For any view, check, or read task, always call subagent-invoke to delegate it
to explore-agent. State the requested target clearly. Relay the report without
fabrication. If the explore agent says the task is beyond its capability
boundary, tell the user honestly and explain why. Do not read files yourself.
~~~

## Inline input material

Create `notes/Usage.md` with the following complete contents; create the
`notes/` directory first:

~~~markdown
# Usage Notes

This is a demonstration notes collection.

## Requirements

Python 3.13 or later is required.

## Common commands

- uv sync installs dependencies.
- uv run pytest runs the tests.

## Security

Credentials must be supplied through environment variables and must not be
written into files.
~~~

## Input and visible result

Input:

~~~text
Ask explore-agent to report the files in the notes collection. Then ask it to create Summary.md from the usage note. If it cannot write files, report that limitation and do not claim success.
/exit
~~~

Visible interaction excerpt:

~~~text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke: ask explore-agent to inspect the notes collection and report its contents.
[tool result] notes/Usage.md contains the usage note.
[tool_call] subagent-invoke: ask explore-agent to create Summary.md from that note.
[tool result] Writing is outside the explore-agent's read-only capability.
~~~

Expected final response: no file was created. The agent explains that it can
inspect and summarize content but cannot write files. A valid summary, if
created by a write-capable component, would state the Python requirement, the
two commands, and the environment-variable-only credential rule.

## Reproducibility boundary

The read-only boundary is intentional: this example demonstrates why splitting
work among agents has a coordination cost. The expected result is an honest
capability report, not a successful file write.
