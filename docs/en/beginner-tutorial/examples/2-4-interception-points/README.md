# Example: 2-4 Interception Points

A `before_tool_call` hook blocks a shell deletion command before execution, while `after_turn` observes completed turns. The transcript demonstrates the blocked result and the still-present filenames. The complete code, configuration, prompts, inputs, and example outputs are included below.

Use Python 3.13 or later, uv, and an environment where the Flowing CLI and package are available. Model-backed runs also require your own `DEEPSEEK_API_KEY` in the shell; no real credential is included here. Create the relative files exactly as shown under a fresh working directory, and run the commands from that directory.

## Reproduce

### Replay the interception scenario

Run the following command from the working directory containing the inline files:

```console
$ uv run flowing repl . --user_name Alice < repl_input.txt
```

The two note files are listed but never read, so their bodies are not inputs to this interaction. Owner names, permissions, file sizes, and timestamps in the captured `ls -la` output are host-specific.

## Complete inline materials

Each block below contains the complete content of a relative file used by this example. Output blocks are sample transcripts; model responses and host-specific details can vary.

### `main.py`

```python
"""Entry point of the interception-points demo subproject: launch imports this file and awaits main()."""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh",
               resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # provide before mount: values that affect assembly must be supplied before mounting
    # (chain semantics: look up along the parent chain)
    runtime.provide("timezone", "Asia/Shanghai")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        # CLI --key value pairs pass through launch verbatim into main(**kwargs);
        # main then decides which parameters go to mount (→ creation pipeline → setup(**args))
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
# Tag → model entry name mapping (single value: one tag maps to exactly one entry).
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
# {{env.VAR}} is substituted at load time; when missing, it is replaced with an
# empty string and a warning is issued (warnings.warn); loading is not interrupted.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `repl_input.txt`

```text
Please use bash to delete notes/路线图.md.
Please use bash to list what's in the notes directory.
/exit
```

### `repl_output.txt`

```text
[thinking] (reasoning trace omitted)
[tool_call] bash {"command": "rm notes/路线图.md; echo \"exit_code=$?\"", "cwd": "."}
[hook] before_tool_call: bash args={'command': 'rm notes/路线图.md; echo "exit_code=$?"', 'cwd': '.'}
[tool:blocked] bash -> Deletion is not allowed: the command contains rm, so it was blocked.
Your `rm notes/路线图.md` was run but blocked by the safety hook that intercepts any `rm`, so `notes/路线图.md` was not deleted.
[thinking] (reasoning trace omitted)
[hook] after_turn: ask_count=1 aborted=False
Your `rm notes/路线图.md` was run but blocked by the safety hook that intercepts any `rm`, so `notes/路线图.md` was not deleted.
(agent-main)>>>[tool_call] bash {"command": "ls -la notes", "cwd": "."}
[hook] before_tool_call: bash args={'command': 'ls -la notes', 'cwd': '.'}
[tool:completed] bash -> exit_code: 0
--- stdout ---
total 16
drwxr-xr-x 2 user user 4096 Sep 19 00:44 .
drwxr-xr-x 5 user user 4096 Sep 21 12:47 ..
-rw-r--r-- 1 user user  286 Sep 21 12:46 使用说明.md
-rw-r--r-- 1 user user   78 Sep 21 12:46 路线图.md
--- stderr ---

`notes` contains 使用说明.md and 路线图.md — confirming the earlier `rm` never took effect.[hook] after_turn: ask_count=2 aborted=False

(agent-main)>>>
```

### `root.fya`

```text
description: "Hook demo assistant: before_tool_call interception + after_turn observation."
model_tag: default
args:
  user_name: str
tools:
  - bash     # demo interception: dangerous commands are hard-blocked by the hook before execution
---
$system_prompt:
You are the demo assistant. Current user: {{ user_name }}; absolute path of the project root: {{ env.PWD }}.
When the user asks to inspect a directory, use bash ls; when the user asks to delete a file, use bash rm (a safety hook is in place: rm will be intercepted; explain that to the user honestly). Keep every answer within one sentence.
---
$script:
from flowing import Intercepted, on


# Registration style 1: declare with @on on the class (registered during __init__, earlier than setup())
@on("after_turn")
def _(self, turn):
    self.ask_count += 1
    print(f"[hook] after_turn: ask_count={self.ask_count} aborted={turn.aborted}")
    return turn


async def setup(self, user_name: str):
    self.user_name = user_name
    self.ask_count = 0
    self.timezone = self.inject("timezone")

    # Registration style 2: register inside setup() (instance-level; registration happens during assembly)
    async def _guard(agent, tool_call):
        print(f"[hook] before_tool_call: {tool_call.name} args={tool_call.args}")
        if tool_call.name == "bash" and "rm" in tool_call.args.get("command", ""):
            # Hard block: the tool does not execute; the LLM receives a blocked result with the reason
            raise Intercepted("Deletion is not allowed: the command contains rm, so it was blocked.")
        return tool_call
    self.hooks.before_tool_call(_guard)
```

### `notes/使用说明.md` (empty fixture)

The recorded interaction uses this filename but never reads its contents. Create an empty file at this relative name.

### `notes/路线图.md` (empty fixture)

The recorded interaction uses this filename but never reads its contents. Create an empty file at this relative name.
