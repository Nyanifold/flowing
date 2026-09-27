# Example: 1-3 Tool Specialization

This walkthrough gives the agent only `glob`, `grep`, and `read`, so it can inspect and summarize notes but cannot create a file. The included request to create a summary demonstrates that capability boundary. The complete code, configuration, prompts, inputs, and example outputs are included below.

Use Python 3.13 or later, uv, and an environment where the Flowing CLI and package are available. Model-backed runs also require your own `DEEPSEEK_API_KEY` in the shell; no real credential is included here. Create the relative files exactly as shown under a fresh working directory, and run the commands from that directory.

## Reproduce

### Replay the request

Run the following command from the working directory containing the inline files:

```console
$ uv run flowing repl . < repl_input.txt
```

The usage note is reproduced below. The recorded exchange also enumerates and reads the roadmap note, whose complete contents are included below.

## Complete inline materials

Each block below contains the complete content of a relative file used by this example. Output blocks are sample transcripts; model responses and host-specific details can vary.

### `main.py`

```python
"""Entry point of the tool-specialization demo subproject: launch imports this file and awaits main()."""

from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # Fixed agent_id → idempotent mount: the second startup goes through
    # recovery — "the same root is back"
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### `model-tags.yaml`

```yaml
# tag → model entry name mapping (single value: one tag maps to exactly one entry).
tags:
  default: deepseek-flash
```

### `models.yaml`

```yaml
# model entries: one entry = one concrete model (bound to one provider entry).
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### `providers.yaml`

```yaml
# provider entries: one entry = one API key identity.
# {{env.VAR}} is substituted at load time; if missing, it is replaced with an
# empty string and a warning is issued (warnings.warn); loading is not interrupted.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `repl_input.txt`

```text
Please check what files are in the notes directory and report back faithfully.
Please create a new file summary.md in the notes directory and write the key points of 使用说明.md into it.
/exit
```

### `repl_output.txt`

````text
(agent-main)>>>I'll inspect the notes directory.
[tool_call] glob {"pattern": "**/*", "path": "./notes"}
[tool:completed] glob -> ./notes/使用说明.md
./notes/路线图.md
The notes directory contains exactly 2 files:

| # | File name | Path |
|---|-----------|------|
| 1 | `使用说明.md` | `notes/使用说明.md` |
| 2 | `路线图.md` | `notes/路线图.md` |

Both are Markdown (`.md`) files, located directly in the `notes` directory — there are no subdirectories or other file types present.

Note: I only enumerated the directory, so I can't yet tell you what's *inside* these files. If you'd like, I can read either (or both) and summarize their contents.
[thinking] (reasoning trace omitted)
The notes directory contains exactly 2 files:

| # | File name | Path |
|---|-----------|------|
| 1 | `使用说明.md` | `notes/使用说明.md` |
| 2 | `路线图.md` | `notes/路线图.md` |

Both are Markdown (`.md`) files, located directly in the `notes` directory — there are no subdirectories or other file types present.

Note: I only enumerated the directory, so I can't yet tell you what's *inside* these files. If you'd like, I can read either (or both) and summarize their contents.
(agent-main)>>>I don't have a write/create tool available — my toolset is read-only (`read`, `grep`, `glob`), so I can't create `summary.md` myself. Let me read the source file so I can at least give you the content to save.
[thinking] (reasoning trace omitted)
[tool_call] read {"path": "./notes/使用说明.md"}
[tool:completed] read -> 0	# Usage Notes
1	
2	This directory is a demo notes repository.
3	
4	## Installation
5	
6	Python 3.13 or later is required.
7	
8	## Common Commands
9	
10	- `uv sync`: install dependencies
11	- `uv run pytest`: run the tests
12	
13	## Cautions
14	
15	Credentials are always held via environment variables; never write them into
16	any file.
**I could not create `summary.md`** — I have no write capability in this session (only `read`, `grep`, `glob`). No file was created, and nothing in the notes directory was modified.

Here is the faithful content of `使用说明.md` (the file is written in English despite its Chinese filename), which you can save yourself as `notes/summary.md`:

```markdown
# 使用说明 — Key Points

- This directory is a demo notes repository.
- **Installation:** Python 3.13 or later is required.
- **Common commands:**
  - `uv sync` — install dependencies
  - `uv run pytest` — run the tests
- **Cautions:** Credentials are always held via environment variables; never write them into any file.
```

The file is 17 lines, with a title plus three sections (Installation, Common Commands, Cautions) — that's all the substantive content it contains, so the summary above covers it in full rather than selectively.
[thinking] (reasoning trace omitted)
**I could not create `summary.md`** — I have no write capability in this session (only `read`, `grep`, `glob`). No file was created, and nothing in the notes directory was modified.

Here is the faithful content of `使用说明.md` (the file is written in English despite its Chinese filename), which you can save yourself as `notes/summary.md`:

```markdown
# 使用说明 — Key Points

- This directory is a demo notes repository.
- **Installation:** Python 3.13 or later is required.
- **Common commands:**
  - `uv sync` — install dependencies
  - `uv run pytest` — run the tests
- **Cautions:** Credentials are always held via environment variables; never write them into any file.
```

The file is 17 lines, with a title plus three sections (Installation, Common Commands, Cautions) — that's all the substantive content it contains, so the summary above covers it in full rather than selectively.
(agent-main)>>>
````

### `root.fya`

```text
description: "Note-library assistant: read-only; it can list directories, search and read notes, and summarize."
model_tag: default
tools:
  - read      # bare name: namespace omitted, equivalent to builtin::read
  - grep
  - glob
---
$system_prompt:
You are a note-library assistant. The absolute path of this project's root is {{ env.PWD }}
(always use this absolute path when calling file tools; do not guess other directories).
The notes live in the notes directory. When answering questions about note content,
first use glob to inspect the directory, use grep to search as needed, and use read
to fetch the relevant files, then answer based on what you actually read; do not
make things up from memory.
```

### `notes/使用说明.md`

```markdown
# Usage Notes

This directory is a demo notes repository.

## Installation

Python 3.13 or later is required.

## Common Commands

- `uv sync`: install dependencies
- `uv run pytest`: run the tests

## Cautions

Credentials are always held via environment variables; never write them into
any file.
```

### `notes/路线图.md`

```markdown
# Roadmap

- 2026-Q3: Complete core features.
- 2026-Q4: Release version 1.0.
```
