# Example: 3-1 Cross-Layer Sharing

The root agent provides a locale and delegates a greeting to a child greeter, which injects the value from the parent chain. A separate script demonstrates the missing-key boundary. The complete code, configuration, prompts, inputs, and example outputs are included below.

Use Python 3.13 or later, uv, and an environment where the Flowing CLI and package are available. Model-backed runs also require your own `DEEPSEEK_API_KEY` in the shell; no real credential is included here. Create the relative files exactly as shown under a fresh working directory, and run the commands from that directory.

## Reproduce

### Request an English greeting

Run the following command from the working directory containing the inline files:

```console
$ uv run flowing repl . --locale en < repl_input.txt
```

### Request the default-language greeting in a fresh state

Run the following command from the working directory containing the inline files:

```console
$ uv run flowing repl . < repl_input_run2.txt
```

### Demonstrate a missing provide value

Run the following command from the working directory containing the inline files:

```console
$ uv run python demo_boundary.py
```

Use an independent fresh working state for each locale comparison because the runtime persists parameters between launches.

## Complete inline materials

Each block below contains the complete content of a relative file used by this example. Output blocks are sample transcripts; model responses and host-specific details can vary.

### `agents/greeter/agent.fya`

```text
description: "Greeter: greets the user in the language of the locale provided up the chain."
model_tag: default
---
$system_prompt:
You are the greeter. locale = {{ locale }}.
{% if locale == 'zh' %}Greet the user in Chinese.{% else %}Greet the user in English.{% endif %}
Output only the greeting itself.
---
$script:
async def setup(self):
    # Walk up the parent chain (ends at Runtime); a miss raises MissingProvideError
    self.locale = self.inject("locale")
```

### `demo_boundary.py`

```python
"""provide-inject boundary demo: an inject miss → MissingProvideError.

Run: uv run python demo_boundary.py
"""
import asyncio

from flowing import launch
from flowing.errors import MissingProvideError


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    try:
        agent.inject("no_such_key")
    except MissingProvideError as exc:
        print(f"inject miss → MissingProvideError: {exc}")
    await runtime.shutdown()


asyncio.run(main())
```

### `demo_output.txt`

```text
inject miss → MissingProvideError: Missing provide value for key: 'no_such_key'
```

### `main.py`

```python
"""Entry point for the parameter & setup() demo subproject: launch imports this file and awaits main()."""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # provide before mount: assembly-affecting values are provided before mounting
    # (chain semantics: lookup walks up the parent chain)
    runtime.provide("timezone", "Asia/Shanghai")
    # CLI --key value passes through launch into main(**kwargs) verbatim;
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
# tag → model entry name mapping (single-valued: one tag maps to one entry).
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
# {{env.VAR}} is substituted at load time; a missing variable becomes an empty
# string with a warning (warnings.warn) and does not interrupt loading.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `repl_input.txt`

```text
Please send the greeter to greet me.
/exit
```

### `repl_input_run2.txt`

```text
Please send the greeter to greet me.
/exit
```

### `repl_output.txt`

```text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"prompt": "Greet the user.", "agent_type": "greeter"}
[tool:completed] subagent-invoke -> 
Hello! Welcome — how can I help you today?
(agent-main)>>>
```

### `repl_output_run2.txt`

```text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "greeter", "prompt": "Greet the user."}
[tool:completed] subagent-invoke -> 
你好！
[thinking] (reasoning trace omitted)
你好！
(agent-main)>>>
```

### `root.fya`

```text
description: "Orchestrator: provides locale and dispatches greeting tasks to the greeter."
model_tag: default
args:
  locale:                # root Agent parameter → provided onto the chain
    type: string
    default: zh
tools:
  - subagent-invoke
subagents:
  - ./agents/greeter
---
$system_prompt:
You are the orchestrator. When a greeting request arrives, call
subagent-invoke to dispatch it to the greeter (agent_type "greeter",
pass no parameters), and relay its greeting to the user verbatim.
---
$script:
async def setup(self, locale: str = "zh"):
    self.locale = locale
    self.provide("locale", locale)   # attached to the provide chain: descendants get it via inject
```
