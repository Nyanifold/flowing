# Example: 1-4 The ReAct Loop

This walkthrough combines file-inspection tools with an optional end-of-turn assertion. The `--strict done` mode steers the agent again unless its final reply contains `DONE` on its own line. The complete code, configuration, prompts, inputs, and example outputs are included below.

Use Python 3.13 or later, uv, and an environment where the Flowing CLI and package are available. Model-backed runs also require your own `DEEPSEEK_API_KEY` in the shell; no real credential is included here. Create the relative files exactly as shown under a fresh working directory, and run the commands from that directory.

## Reproduce

### Replay the file-inspection conversation

Run the following command from the working directory containing the inline files:

```console
$ uv run flowing repl . < repl_input.txt
```

### Try the end-of-turn assertion in a fresh state

Run the following command from the working directory containing the inline files:

```console
$ uv run flowing repl . --strict done < repl_input_run2.txt
```

The second recorded transcript ends with `OK` and does not show a `DONE` line. Treat it as an incomplete example of the assertion path; its success is not established by the transcript.

## Complete inline materials

Each block below contains the complete content of a relative file used by this example. Output blocks are sample transcripts; model responses and host-specific details can vary.

### `main.py`

```python
"""Entry point of the ReAct loop example project: launch imports this file and awaits main()."""

from flowing import Runtime


async def main(strict: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # --strict done passes through launch to main(), then forwards to mount → setup()
    kwargs = {"strict": strict} if strict else {}
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

### `model-tags.yaml`

```yaml
# Tag → model entry name mapping (single value: one tag maps to one entry).
tags:
  default: deepseek-flash
```

### `models.yaml`

```yaml
# Model entry: one entry = one concrete model (bound to one provider entry).
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### `providers.yaml`

```yaml
# Provider entry: one entry = one API key identity.
# {{env.VAR}} is substituted at load time; if missing, it is replaced with an
# empty string and a warning is issued (warnings.warn); loading continues.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `repl_input.txt`

```text
First use glob to see what files are in the notes directory, then use read to read 使用说明.md, and finally summarize what it says in two sentences.
/messages
/exit
```

### `repl_input_run2.txt`

```text
Reply with only the word "OK" and do not add anything else.
/exit
```

### `repl_output.txt`

```text
(agent-main)>>>I'll start by exploring the notes directory structure.
[tool_call] glob {"pattern": "notes/**/*", "path": "."}
[tool:completed] glob -> ./notes/使用说明.md
./notes/路线图.md
The notes directory contains two files. Now let me read the one you asked about.
[thinking] (reasoning trace omitted)
[tool_call] read {"path": "./notes/使用说明.md"}
[tool:completed] read -> 0	# Usage
1	
2	This directory is a demo notes library.
3	
4	## Installation
5	
6	Python 3.13 or later is required.
7	
8	## Common commands
9	
10	- `uv sync`: install dependencies
11	- `uv run pytest`: run tests
12	
13	## Notes
14	
15	Credentials must always be held in environment variables; never write them
16	into any file.
`notes/` holds two files: `使用说明.md` and `路线图.md`. `使用说明.md` describes a demo notes library requiring Python 3.13+, listing `uv sync` for installing dependencies and `uv run pytest` for running tests. It ends with the warning that credentials must always live in environment variables and never be written into any file.
(agent-main)>>>1  user      First use glob to see what files are in the notes directory, then use read to re…
2  provider  I'll start by exploring the notes directory structure.
3  tool      .
4  provider  The notes directory contains two files. Now let me read the one you asked about.
5  tool      0 # Usage 1 2 This directory is a demo notes library. 3 4 ## Installation 5 6 Py…
6  provider  `notes/` holds two files: `使用说明.md` and `路线图.md`. `使用说明.md` describes a demo not…
(agent-main)>>>
```

### `repl_output_run2.txt`

```text
(agent-main)>>>OK
[steer] Acceptance condition not met: your final reply must contain DONE on its own line. Please answer the previous question again and append DONE at the end.
OK
(agent-main)>>>
```

### `root.fya`

```text
description: "Project Q&A assistant: can list directories, read files, and summarize."
model_tag: default
args:
  strict:
    type: string
    default: ""
    description: 'When set to "done", enables the end-of-turn assertion that the reply must contain DONE'
tools:
  - read      # bare name: namespace omitted, equivalent to builtin::read
  - glob
---
$system_prompt:
You are a project Q&A assistant. The absolute path of this project's root
directory is {{ env.PWD }} (always use this absolute path when calling file
tools; do not guess other directories). When a question concerns project
content, first use glob to inspect the directory structure, then use read to
read the relevant files, and answer based on what you read; do not make
things up from memory. Keep answers within five sentences.
---
$script:
from flowing import MessageKind, TextBlock
from flowing.composables import use_prompt_until


async def setup(self, strict: str = ""):
    if strict != "done":
        return

    # Assertion-based termination: at turn end, check whether the final
    # reply contains DONE; if not, drive the next round with a steering message
    def _done(agent, turn) -> bool:
        for mid in reversed(turn.message_ids):
            msg = agent.chain.get(mid)
            if msg.kind is MessageKind.PROVIDER:
                text = "".join(b.text for b in msg.content
                               if isinstance(b, TextBlock))
                return "DONE" in text
        return True   # empty turn: no nudge

    use_prompt_until(
        self,
        predicate=_done,
        message=("Acceptance condition not met: your final reply must contain "
                 "DONE on its own line. Please answer the previous question "
                 "again and append DONE at the end."),
    )
```

### `notes/使用说明.md`

```markdown
# Usage

This directory is a demo notes library.

## Installation

Python 3.13 or later is required.

## Common commands

- `uv sync`: install dependencies
- `uv run pytest`: run tests

## Notes

Credentials must always be held in environment variables; never write them
into any file.
```

### `notes/路线图.md`

```markdown

```
