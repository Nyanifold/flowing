# Example: 1-1 Agent Runtime

This walkthrough demonstrates how a minimal Runtime creates an agent, mounts its declarative prompt, and passes a command-line parameter into the agent. The complete example-specific code, configuration, prompts, inputs, and example transcripts are included below.

Use Python 3.13 or later, uv, and an environment where the Flowing CLI is available. Before a run with the default DeepSeek model, set `DEEPSEEK_API_KEY` in the current shell to your own credential. To try OpenRouter's GPT-6 Luna, change `model_tag` to `luna` and also set `OPENROUTER_API_KEY`. No credential is included in this page. Create the relative files exactly as shown under a fresh working directory, and run the commands from that directory.

## Run

Start a standard conversation:

```console
$ uv run flowing repl .
```

To pass a user name into the prompt and ask the agent to recall it, use:

```console
$ uv run flowing repl . --user_name Alice < repl_input_run2.txt
```

To replay the ordinary exchange from the included input, use:

```console
$ uv run flowing repl . < repl_input.txt
```

The model's wording can vary between runs.

## Complete inline materials

The following blocks contain the complete project-specific files. The transcript blocks show example runs; model responses may vary.

### `main.py`

```python
"""Entry point of the hello subproject: launch imports this file and awaits main()."""

from flowing import Runtime


async def main(user_name: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # Fixed agent_id -> idempotent mount: a later launch goes through recovery, "the same root is back"
    root = await runtime.mount("@/root.fya", agent_id="agent-main")
    if user_name is not None:
        # Runtime assignment: the {{ user_name }} template is evaluated on the spot at the next context assembly
        root.user_name = user_name
    return runtime
```

### `model-tags.yaml`

```yaml
# Tag -> model entry name mapping (single value: one tag maps to exactly one entry).
tags:
  default: deepseek-flash
  luna: openrouter-gpt-6-luna
```

### `models.yaml`

```yaml
# Model entry: one entry = one concrete model (bound to one provider entry).
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
openrouter-gpt-6-luna:
  provider: openrouter
  model: openai/gpt-6-luna
  "reasoning.effort": high
```

### `providers.yaml`

```yaml
# Provider entry: one entry = one API key identity.
# {{env.VAR}} is substituted at load time; if missing, it is replaced with an empty string and a warning is issued (warnings.warn), and loading continues.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
openrouter:
  adapter: openrouter
  base_url: https://openrouter.ai/api/v1
  api_key: "{{env.OPENROUTER_API_KEY}}"
```

If `OPENROUTER_API_KEY` is unset, configuration loading warns and the `default`
tag can still use DeepSeek. The key is required when you change `model_tag` in
`root.fya` to `luna`.

### `repl_input.txt`

```text
Tell me about yourself in one sentence.
What did I just ask you?
/exit
```

### `repl_input_run2.txt`

```text
What is my name? Answer with just the name itself.
/exit
```

### `repl_output.txt`

```text
(agent-main)>>>I'm a concise English assistant designed to give helpful, clear answers within three sentences.
[thinking] (reasoning trace omitted)
I'm a concise English assistant designed to give helpful, clear answers within three sentences.
(agent-main)>>>You asked me to tell you about myself in one sentence.
[thinking] (reasoning trace omitted)
You asked me to tell you about myself in one sentence.
(agent-main)>>>
```

### `repl_output_run2.txt`

```text
(agent-main)>>>Alice
[thinking] (reasoning trace omitted)
Alice
(agent-main)>>>
```

### `root.fya`

```text
description: Minimal Q&A assistant
model_tag: default
---
$system_prompt:
You are a concise English assistant. Keep your answers within three sentences.{% if user_name %}The user is named {{ user_name }}; you may address them by name in your answers.{% endif %}
```
