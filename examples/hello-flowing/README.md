# Hello Flowing: parameters, setup, and persistence

This first example builds one chat agent. It shows the shortest useful Flowing project: a Python entry point assembles a Runtime, a single-file `.fya` declaration defines the agent, and a command-line argument becomes part of the agent's system prompt.

You will learn how to:

- pass `--name Alice` from the CLI through `main()` and `mount()` into `setup()`;
- store a value on a live Agent and interpolate it with `{{name}}`;
- attach provider-call retries with the `use_retry()` composable;
- restart the REPL and recover the same conversation through a fixed `agent_id`.

## 1. Prerequisites

Use Python 3.13 or later and install `flowing-agent`:

```console
pip install flowing-agent
```

The commands below use `uv run` from a source checkout. If you installed the CLI in another environment, omit `uv run` and use `flowing`, `flowing-config`, or `python` directly. Run the example commands from this directory:

```console
cd examples/hello-flowing
```

### Configure the `luna` Model

This walkthrough uses OpenRouter and the API model ID `openai/gpt-6-luna`. If your shared configuration already contains a Model entry named `luna`, you can skip the two `flowing-config` commands, but you still need the credential required by its Provider.

**Step 1: make the credential available.** Set the environment variable in every shell where you run the example:

```console
export OPENROUTER_API_KEY='<your OpenRouter API key>'
```

Do not write the real key into `root.fya`, `main.py`, or `model-tags.yaml`.

**Step 2: add the OpenRouter Provider.** Run the interactive command and answer the prompts as shown:

```console
$ uv run flowing-config providers add
# Press Enter at the configuration path prompt to use the Runtime default.
Provider entry name (identity): openrouter
Provider adapter: openrouter
API endpoint (base_url; leave blank to use 'https://openrouter.ai/api/v1'): [Enter]
API key (api_key; leave blank to skip): env.OPENROUTER_API_KEY
```

This creates a Provider entry named `openrouter` in the shared `providers.yaml`. By default, Flowing looks for shared configuration under `~/.flowing`; `FLOWING_CONFIG_HOME` can select another directory. The `env.OPENROUTER_API_KEY` input stores an environment-variable reference rather than embedding your credential in this example.

**Step 3: add the `luna` Model entry.** Run:

```console
$ uv run flowing-config models add
# Press Enter at the configuration path prompt to use the Runtime default.
Model entry name (identity): luna
Known Provider entries: openrouter
Provider entry name (identity): openrouter
API model ID: openai/gpt-6-luna
```

Accept the defaults for the remaining optional parameters unless you intentionally want different model settings. The resulting entry in shared `models.yaml` is equivalent to:

```yaml
luna:
  provider: openrouter
  model: openai/gpt-6-luna
```

**Step 4: follow the two-hop lookup.** No Provider credential belongs in the project tag file. At run time, Flowing resolves the model as follows:

```text
root.fya: model_tag: default
    -> project model-tags.yaml: default: luna
    -> shared models.yaml: luna uses Provider openrouter
       and API model ID openai/gpt-6-luna
    -> shared providers.yaml: openrouter reads OPENROUTER_API_KEY
```

The Provider and Model are shared across projects. The included `model-tags.yaml` is project-local and only maps the Agent's lookup label to the Model entry name.

## 2. The project files

The project contains three source files:

```text
hello-flowing/
├── main.py
├── model-tags.yaml
└── root.fya
```

### `root.fya`: declare the agent

```python
description: Minimal agent with a parameter.
model_tag: default
---
$system_prompt:
Your name is Flowing, and you are chatting with {{name}}.
Keep your tone relaxed and natural. Answer in English.
---
$script:
from flowing.composables import use_retry


async def setup(self, name: str):
    use_retry(self)
    self.name = name
```

The YAML header selects the `default` model tag. The `$system_prompt` block contains a Jinja expression, `{{name}}`. The `$script` block runs `setup()` when the Agent is created:

1. `use_retry(self)` attaches a reusable retry policy for retryable Provider exceptions.
2. `self.name = name` stores the constructor argument on this Agent instance.
3. During context assembly, Flowing evaluates `{{name}}` against that instance and renders the actual name into the system prompt.

A composable adds behavior to one Agent; it is not a global Runtime policy.

### `main.py`: assemble the project

```python
from flowing import Runtime


async def main(name: str) -> Runtime:
    runtime = Runtime()
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id=f"agent-{name.lower()}", name=name)
    return runtime
```

`flowing repl` launches this project by importing `main.py` and awaiting `main()`. The CLI forwards `--name Alice` as `name="Alice"`.

`runtime.mount()` then performs three relevant steps:

- `@/` resolves paths relative to this project directory;
- `name=name` forwards the value to `setup(self, name)`;
- `agent_id=f"agent-{name.lower()}"` gives the Agent a stable identity, such as `agent-alice`.

### `model-tags.yaml`: select the Model entry

```yaml
tags:
  default: luna
```

A model tag is only a lookup label. `default` resolves to the Model entry named `luna`; that Model entry separately selects a Provider and an API model ID from your shared configuration.

## 3. Start a conversation

Set your Provider credential in the shell, then run:

```console
uv run flowing repl . --name Alice
```

Try these two messages:

```text
Hello! What is my name?
What were we just talking about?
```

A session will look like this, although the model's exact wording will vary:

```text
(agent-alice)>>> Hello! What is my name?
Your name is Alice.
(agent-alice)>>> What were we just talking about?
You asked what your name is, and I said Alice.
(agent-alice)>>> /exit
```

The first answer demonstrates parameter interpolation: `Alice` was never written into the prompt file. The second demonstrates ordinary conversation history within one run.

## 4. Restart and verify recovery

After `/exit`, start exactly the same command again:

```console
uv run flowing repl . --name Alice
```

Ask:

```text
What is my name, and what did I ask you earlier?
```

The Agent can recover both the name and the earlier exchange. Flowing persists messages under `.flowing/` in this project directory. Because the Agent ID remains `agent-alice`, mounting the same declaration restores its existing message history; no manual load or restore call is needed.

Using a different name creates a different ID and therefore a separate conversation. For example, `--name Bob` uses `agent-bob`.

## 5. Reset and experiment

To repeat the walkthrough from an empty history, stop the REPL and remove this example's local `.flowing/` directory:

```console
rm -rf .flowing
```

Then try changing the prompt, adding another instance attribute in `setup()`, or mounting the same declaration with a different name. The next example builds on this project structure by hosting two Agents that communicate through a plugin.
