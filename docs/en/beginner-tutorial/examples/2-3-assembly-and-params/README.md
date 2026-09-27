# Example: 2-3 Assembly and Parameterization

This walkthrough declares agent parameters, passes CLI values through `main()` into assembly, and uses `setup()` plus provide-inject to initialize instance state. The complete code, configuration, prompts, inputs, and example outputs are included below.

Use Python 3.13 or later, uv, and an environment where the Flowing CLI and package are available. Model-backed runs also require your own `DEEPSEEK_API_KEY` in the shell; no real credential is included here. Create the relative files exactly as shown under a fresh working directory, and run the commands from that directory.

## Reproduce

### Start a normal conversation with explicit values

Run the following command from the working directory containing the inline files:

```console
$ uv run flowing repl . --user_name Alice --locale en
```

### Replay the conversation

Run the following command from the working directory containing the inline files:

```console
$ uv run flowing repl . --user_name Alice --locale en < repl_input.txt
```

### Check required-parameter validation in a fresh state

Run the following command from the working directory containing the inline files:

```console
$ uv run flowing repl .
```

The missing-parameter command is a separate first launch. Use a fresh working state for each independent command because a failed launch can leave persisted state. The `locale` argument is deliberately set to `en` in the normal run.

## Complete inline materials

Each block below contains the complete content of a relative file used by this example. Output blocks are sample transcripts; model responses and host-specific details can vary.

### `cli_error_output.txt`

```text
launch failed: RootAgent.setup() missing 1 required positional argument: 'user_name'
```

### `main.py`

```python
"""Entry point of the parameter & setup() demo subproject: launch imports this file and awaits main()."""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # provide before mount: assembly-affecting values are provided before mounting
    # (chained semantics: look up along the parent chain)
    runtime.provide("timezone", "Asia/Shanghai")
    # CLI --key value passes through launch verbatim into main(**kwargs);
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
# tag → model entry name mapping (single value: one tag maps to exactly one entry).
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
# {{env.VAR}} is substituted at load time; when missing, replaced with an
# empty string plus a warning (warnings.warn), and loading continues.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `repl_input.txt`

```text
Please report in one sentence: who is the current user, what is the locale, and what is the timezone?
/exit
```

### `repl_output.txt`

```text
(agent-main)>>>The current user is Alice, the locale is English (en), and the timezone is Asia/Shanghai.
[thinking] (reasoning trace omitted)
The current user is Alice, the locale is English (en), and the timezone is Asia/Shanghai.
(agent-main)>>>
```

### `root.fya`

```text
description: "Parameter demo assistant: args declaration + typical setup() pattern."
model_tag: default
args:
  user_name: str        # sugar: bare type string → required parameter
  locale:               # full form: JSON Schema keywords expanded over lines
    type: string
    default: zh
    description: "Reply language (zh / en)"
---
$system_prompt:
You are a concise Q&A assistant. Current user: {{ user_name }}; reply language: {{ locale }}; timezone: {{ timezone }}. Keep your answer within one sentence.
---
$script:
async def setup(self, user_name: str, locale: str = "zh"):
    self.user_name = user_name               # ① store the param on self (source for the {{ user_name }} template)
    self.locale = locale                     # ② store the param on self (source for the {{ locale }} template)
    self.ask_count = 0                       # ③ initialize the counter (instance state in place at creation)
    self.provide("locale", locale)           # ④ attach the value to the provide chain (child components can fetch along the chain)
    self.timezone = self.inject("timezone")  # ⑤ fetch the upper-level provided value (main() provides it before mounting)
```
