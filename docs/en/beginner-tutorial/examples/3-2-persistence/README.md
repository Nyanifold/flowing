# Example: 3-2 Persistence

The ordered sequence stores a number, starts a second process that stores a color and exits without normal shutdown, then resumes and asks for both values. The complete code, configuration, prompts, inputs, and example outputs are included below.

Use Python 3.13 or later, uv, and an environment where the Flowing CLI and package are available. Model-backed runs also require your own `DEEPSEEK_API_KEY` in the shell; no real credential is included here. Create the relative files exactly as shown under a fresh working directory, and run the commands from that directory.

## Reproduce

### Store the number

Run the following command from the working directory containing the inline files:

```console
$ uv run flowing repl . < repl_input.txt
```

### Simulate the crash while storing the color

Run the following command from the working directory containing the inline files:

```console
$ uv run python demo_crash.py
```

### Resume and ask for both values

Run the following command from the working directory containing the inline files:

```console
$ uv run flowing repl . < repl_input_run2.txt
```

Run all three commands in order against the same fresh working state. The crash script deliberately exits with `os._exit(9)`, so the process does not perform normal shutdown. The saved record count and model responses depend on the actual run.

## Complete inline materials

Each block below contains the complete content of a relative file used by this example. Output blocks are sample transcripts; model responses and host-specific details can vary.

### `crash_output.txt`

```text
Turn completed status=completed; now simulating kill -9 (os._exit(9))
```

### `crash_wc.txt`

```text
5 .flowing/agent-main/tree.jsonl
```

### `demo_crash.py`

```python
"""Crash simulation: os._exit(9) is equivalent to kill -9 — it bypasses shutdown().

The write-behind drain barrier never runs, but already-submitted records are not
lost and are replayed on the next start (crash-window demonstration).
Run: uv run python demo_crash.py
"""
import asyncio
import os

from flowing import launch


async def main() -> None:
    runtime = await launch(".")            # recovery pipeline: replay the tree/state logs
    agent = await runtime.get_agent("agent-main")
    result = await agent.query("Also remember a color: purple.")
    print(f"Turn completed status={result.status}; now simulating kill -9 (os._exit(9))", flush=True)
    os._exit(9)                            # no shutdown: the process dies immediately


asyncio.run(main())
```

### `main.py`

```python
"""Entry point of the parameter-and-setup() demo subproject: launch imports this module and awaits main()."""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # provide before mount: values that affect assembly must be provided before
    # mounting (chain semantics: look up along the parent chain)
    runtime.provide("timezone", "Asia/Shanghai")
    # the CLI's --key value passes through launch into main(**kwargs) unchanged,
    # and main decides which parameters go to mount (→ creation pipeline → setup(**args))
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
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
# {{env.VAR}} is substituted at load time; if the variable is missing, it is replaced
# with an empty string and a warning is issued (warnings.warn); loading continues.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `repl_input.txt`

```text
Remember the number 731.
/exit
```

### `repl_input_run2.txt`

```text
What number and color did I ask you to remember?
/exit
```

### `repl_output.txt`

```text
(agent-main)>>>I'll remember that the number is 731.
[thinking] (reasoning trace omitted)
I'll remember that the number is 731.
(agent-main)>>>
```

### `repl_output_run2.txt`

```text
(agent-main)>>>You asked me to remember the number 731 and the color purple.
[thinking] (reasoning trace omitted)
You asked me to remember the number 731 and the color purple.
(agent-main)>>>
```

### `root.fya`

```text
description: "Memory demo assistant: remembers whatever the user tells it."
model_tag: default
---
$system_prompt:
You are a memory demo assistant. When the user asks you to remember something, confirm it with a one-sentence restatement; when asked about it later, recall it accurately. Keep every answer to one sentence.
```
