# Example: 1-2 Tool Calling

This walkthrough shows how an agent uses the built-in `glob` and `read` tools to inspect a note library and answer from the files it actually reads. The complete code, configuration, prompts, inputs, and example outputs are included below.

Use Python 3.13 or later, uv, and an environment where the Flowing CLI and package are available. Model-backed runs also require your own `DEEPSEEK_API_KEY` in the shell; no real credential is included here. Create the relative files exactly as shown under a fresh working directory, and run the commands from that directory.

## Reproduce

### Replay the conversation

Run the following command from the working directory containing the inline files:

```console
$ uv run flowing repl . < repl_input.txt
```

The included interaction lists the note workspace and reads the usage note. A fresh run can show additional generated state files, so the recorded directory listing is an example rather than a fixed inventory.

## Complete inline materials

Each block below contains the complete content of a relative file used by this example. Output blocks are sample transcripts; model responses and host-specific details can vary.

### `main.py`

```python
"""Entry point of the builtin-tools demo project: launch imports this file and awaits main()."""

from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # fixed agent_id -> idempotent mounting: a second launch goes through recovery, "the same root is back"
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### `model-tags.yaml`

```yaml
# tag -> model entry name mapping (single value: one tag maps to exactly one entry).
tags:
  default: deepseek-flash
```

### `models.yaml`

```yaml
# model entry: one entry = one concrete model (bound to one provider entry).
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### `providers.yaml`

```yaml
# provider entry: one entry = one API key identity.
# {{env.VAR}} is substituted at load time; if missing, it is replaced with an
# empty string and a warning is issued (warnings.warn), and loading continues.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `repl_input.txt`

```text
What files are in this project? Please summarize the main content of notes/使用说明.md.
/exit
```

### `repl_output.txt`

```text
(agent-main)>>>I'll inspect the project structure first.
[tool_call] glob {"pattern": "**/*", "path": "."}
[tool_call] read {"path": "./notes/使用说明.md"}
[tool:completed] read -> 0	# Usage Notes
1	
2	This directory is a demo note library.
3	
4	## Installation
5	
6	Python 3.13 or higher is required.
7	
8	## Common Commands
9	
10	- `uv sync`: installs dependencies
11	- `uv run pytest`: runs the tests
12	
13	## Notes
14	
15	Credentials must always be held in environment variables; never write them
16	into any file.
[tool:completed] glob -> ./.flowing/agent-main/tree.jsonl
./.flowing/agent-main/core.jsonl
./repl_output.txt
./.flowing/core.jsonl
./.flowing/agent-main/meta.json
./__pycache__/main.cpython-313.pyc
./root.fya
./main.py
./repl_input.txt
./README.md
./model-tags.yaml
./models.yaml
./providers.yaml
./notes/使用说明.md
./notes/路线图.md
The project contains `main.py`, `root.fya`, `README.md`, plus config files (`models.yaml`, `model-tags.yaml`, `providers.yaml`), sample I/O files (`repl_input.txt`, `repl_output.txt`), a `.flowing/` state directory (core.jsonl, tree.jsonl, meta.json), a `__pycache__` cache, and two notes docs (`使用说明.md`, `路线图.md`). The file `notes/使用说明.md` ("Usage Notes") is a short demo note library: it requires Python 3.13+, lists commands `uv sync` (install dependencies) and `uv run pytest` (run tests), and warns that credentials must live in environment variables and never be written into files.
(agent-main)>>>
```

### `root.fya`

```text
description: "Project Q&A assistant: can list directories, read files, and summarize."
model_tag: default
tools:
  - read      # bare name: namespace omitted, equivalent to builtin::read
  - glob
---
$system_prompt:
You are a project Q&A assistant. The absolute path of this project's root
directory is {{ env.PWD }} (always use this absolute path in file-tool calls;
do not guess other directories). When a question involves project content,
you must first use glob to inspect the directory structure, then use read to
read the relevant files, and answer based on what you actually read; do not
make things up from memory. Keep your answer within five sentences.
```

### `notes/使用说明.md`

```markdown
# Usage Notes

This directory is a demo note library.

## Installation

Python 3.13 or higher is required.

## Common Commands

- `uv sync`: installs dependencies
- `uv run pytest`: runs the tests

## Notes

Credentials must always be held in environment variables; never write them
into any file.
```

### `notes/路线图.md`

```markdown

```
