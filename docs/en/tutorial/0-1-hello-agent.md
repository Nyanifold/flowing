# 0-1 · The First Agent

## Prerequisites

Chapter [0-0 Environment Setup](0-0-env-setup.md) covers the prerequisites: Python ≥ 3.13
and an installed `flowing-agent` package. Set `DEEPSEEK_API_KEY` in the current shell if
`api_key` uses the `{{env.DEEPSEEK_API_KEY}}` placeholder. If you put the credential
directly in the user-level configuration described below, the environment variable is
not needed. Create a fresh project directory and save the project files there; put the
provider and model configuration in the user-level directory as described in this chapter.
Set `OPENROUTER_API_KEY` if you want to try the OpenRouter model below.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| flowing subproject | A flowing subproject consists of a directory, an entry point named `main.py`, and declarative agent definitions in `.fya` files. |
| `.fya` file | A `.fya` file is flowing's declarative definition format: its YAML header declares metadata, and named blocks separated by `---` hold prompts or scripts such as `$system_prompt:`. |
| The three model-access files | `providers.yaml` defines provider entries, `models.yaml` defines concrete models, and `model-tags.yaml` maps tags to model-entry names. |
| `model_tag` | The `model_tag` field points to a model tag, which resolves to a concrete API call through two lookup steps. |
| `launch` | `flowing.launch(path, **kwargs)` is the only entry point that creates a Runtime. |
| Logical turn | A logical turn runs from message consumption through the model's final reply, and `TurnResult` represents its outcome. |

## Goals

This chapter takes you from an empty directory to one complete conversation: you wire
up model access, bring up a flowing subproject, complete one Q&A through each of the
four access methods, and learn how messages drive turns.

## Main text

### What a flowing subproject looks like

The unit of a flowing application is a **project directory**. This example keeps
`providers.yaml` and `models.yaml` in the user-level configuration directory, while
`main.py`, the agent declaration, and `model-tags.yaml` remain in the project. The
complete configuration contents appear below; you can also keep the provider and model
files in the project by enabling the corresponding calls in the entry point.

`root.fya` is the declarative agent definition: the YAML header declares metadata,
and the named block after the `---` separator — `$system_prompt:` — is the system
prompt (the only required content):

```yaml
description: Minimal Q&A assistant
model_tag: default
---
$system_prompt:
You are a concise assistant and keep every answer within three sentences. {% if user_name %}The user's name is {{ user_name }}; you may address them by name in your replies.{% endif %}
```

There is no Python code at all — this is already an agent with full behavior. Ignore
the `{{ user_name }}` clause for now; demo 2 of the main example uses it (the
principle of on-the-spot template evaluation is covered in 4-9).

Save the following complete entry point as `main.py`. It is responsible for
**assembly**: it constructs the Runtime, registers the model configurations, and
mounts the root agent.

```python
from flowing import Runtime


async def main(user_name: str | None = None, resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)   # Recover an existing agent.
    else:
        # A fixed agent_id makes mounting idempotent, so a second launch recovers the same root.
        root = await runtime.mount("@/root.fya", agent_id="agent-main")
        if user_name is not None:
            root.user_name = user_name
    return runtime
```

The `set_providers()` and `set_models()` calls are commented out, so Runtime uses the
default configuration under `~/.flowing`. `set_model_tags()` remains active, so this
example reads `model-tags.yaml` from the project root. If you put that file in
`~/.flowing` too, comment out `set_model_tags()` as well. To use project-level provider
and model files, uncomment the first two calls and place those files in the project root.

Note: **whether to create or to recover is a policy of `main()`, not a parameter of
the framework**. The framework does not know `--resume`; your `main()` decides on
its own (here expressed with the `resume` parameter). The root agent is mounted with
`mount()`; with a fixed `agent_id`, the second launch automatically goes through the
recovery pipeline (idempotent mount).

### Model access: the three YAML files and the two-hop resolution of model_tag

The most common reason an agent system fails to start is that the model is not wired
up. flowing splits model access into three files, each with a single responsibility
and independently replaceable. Save the following complete blocks as
`providers.yaml`, `models.yaml`, and `model-tags.yaml`, respectively. In this example,
put the first two in `~/.flowing` and the tag file in the project root. To use
project-level provider and model configuration, uncomment the first two calls in `main.py`:

```yaml
# Each provider entry represents one API key identity.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"   # Credentials are injected through environment variables and are never hard-coded.
openrouter:
  adapter: openrouter
  base_url: https://openrouter.ai/api/v1
  api_key: "{{env.OPENROUTER_API_KEY}}"
```

```yaml
# Each model entry represents one concrete model and selects a provider entry.
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
openrouter-gpt-6-luna:
  provider: openrouter
  model: openai/gpt-6-luna
  "reasoning.effort": high
```

```yaml
# This file maps each tag to one model-entry name without a fallback chain.
tags:
  default: deepseek-flash
  luna: openrouter-gpt-6-luna
```

How does `model_tag: default` in the agent declaration become one real API call?
**Two-hop resolution**:

```
model_tag("default") ──① model-tags.yaml──▶ entry name ("deepseek-flash")
                     ──② models.yaml──────▶ ModelConfig(provider=deepseek,
                                             model=deepseek-v4-flash)
model_tag("luna")    ──① model-tags.yaml──▶ entry name ("openrouter-gpt-6-luna")
                     ──② models.yaml──────▶ ModelConfig(provider=openrouter,
                                             model=openai/gpt-6-luna,
                                             extra={"reasoning.effort": "high"})
```

The three files declare provider identities, models, and tag mappings separately.
The `provider` field in `models.yaml` selects an entry from `providers.yaml`, while
`model` is the model ID sent to that provider. A tag points only to a model entry.
The `default` tag still selects DeepSeek. Set an agent's `model_tag` to `luna` to
call `openai/gpt-6-luna` through OpenRouter. The literal extension key
`"reasoning.effort": high` belongs to that model entry and is sent to OpenRouter
with GPT-6 Luna. The DeepSeek entry has no such setting. If `OPENROUTER_API_KEY` is
unset, configuration loading warns and the `default` tag can still use DeepSeek;
the `luna` entry requires that key.

Key rules: when a tag is undefined, resolution falls back to the `default` tag; if
`default` is also undefined → an error is raised — **no silent fallback**. `{{env.X}}`
is substituted at entry load time; a missing environment variable becomes an empty
string with a `warnings.warn` warning, and **loading is not interrupted** (the actual
consequence of missing credentials — such as a 401 — surfaces on the first call
through that entry).

#### Shared user-level model configuration

By default, `Runtime` reads `providers.yaml`, `models.yaml`, and `model-tags.yaml` from
`$FLOWING_CONFIG_HOME`; when that variable is unset, the directory is `~/.flowing`.
This default does not scan the current project directory. In this chapter's `main.py`,
`set_providers()` and `set_models()` are commented out, so those two files come from the
user-level directory; `set_model_tags()` remains active, so the tag mapping comes from
the project. Later tutorials can reuse shared provider and model configuration without
declaring `providers.yaml` and `models.yaml` in every example directory; keep their
`set_providers()` and `set_models()` calls commented out. To share the tag mapping too,
comment out `set_model_tags()` and put `model-tags.yaml` in the user-level directory.

The user-level `models.yaml` and `model-tags.yaml` can use the contents shown above.
In `providers.yaml`, you can keep the environment-variable placeholder or write the
credential directly in a private local configuration:

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "YOUR_DEEPSEEK_API_KEY"
```

With the default directory, create `~/.flowing` and put `providers.yaml` and
`models.yaml` there. You may also put `model-tags.yaml` there if you want to share the
tag mapping. Restrict access to the credential file:

```console
$ mkdir -p ~/.flowing
$ chmod 600 ~/.flowing/providers.yaml
```

Keeping the credential in user-level configuration prevents it from being committed
or shared with project source files. It is still stored as plain text locally, so only
the current user should be able to read it. If `FLOWING_CONFIG_HOME` is set, place the
files in that directory and apply the same permissions to its `providers.yaml`.

### launch, Runtime, and the "message-driven turn"

`flowing.launch(path, **kwargs)` is the **only entry point** that creates a Runtime.
It does very little: register the `@`
project-root context → import the subproject `main.py` → `await main(**kwargs)` →
reset the context. It does not parse configuration, does not know about plugins, and
does not open any port — all of that lives in `main()` or further out.

The Runtime is the **root of the object graph**: it holds all agent nodes, the global
tool registry, and the agent pool. Two commonly used semantics in one sentence each:
`await runtime` blocks until `shutdown()` (the CLI / embedder uses it to keep the
process alive); `shutdown()` destroys all agents recursively and drains persistence —
write-behind persistence (commits are only queued), so exiting the process directly
without `shutdown()` loses the tail records (details in 1-8 / 4-1).

How does a turn happen? An interactive input that is not `/` is delivered by `query()`
as a message and enqueued; the agent's resident work loop dequeues and consumes it,
opening a logical turn: assemble context → call the model → attach the response to
the tree and persist → wrap up. What `query()` waits for is a `TurnResult`:
`result.status` (`"completed"` / `"blocked"` / `"cancelled"` / `"error"`) and
`result.final_text` (the text of the last PROVIDER message of this turn). All four
outcomes **do** resolve to the waiter — the caller never hangs (the full loop is in
1-1).

### The four access methods at a glance

The same subproject can be exposed in four ways, all built on `launch(path)`; the
only difference is the exposure method (the full comparison is in 6-1):

| Method | Command | Form |
|---|---|---|
| `flowing cli` | `flowing cli . "one sentence"` | This command delivers one input, prints the result, and exits, which suits scripts and pipelines. |
| `flowing repl` | `flowing repl .` | This command opens an interactive REPL bound to the agent; **this tutorial uses `repl` for its interactive examples**. |
| `flowing web` | `flowing web .` | This command serves an HTTP API and a built-in frontend that opens in a browser. |
| `flowing serve` | `flowing serve .` | This command serves only the HTTP API for custom user interfaces or service integration. |

## Out of scope

- This chapter does not explain the mechanics of the `@` project-root context; chapter 4-8 covers them.
- This chapter does not cover the recovery pipeline's internal timing or idempotent mounting; chapter 1-8 shows the surface behavior, and chapter 4-1 explains the mechanics.
- This chapter does not cover the three-layer `config.yaml` merge, the search precedence for the three configuration files, or programmatic overrides through `set_providers`, `set_models`, and `set_model_tags`; chapter 6-3 covers these topics.
- This chapter does not explain model selection in full, including multiple tags and changing `agent.model_tag` at runtime; chapter 3-4 covers those topics.
- This chapter does not cover the HTTP details of `serve` or embedded exposure; chapters 6-1 and 6-2 do.

## Main example

**Demo 1: the same inline project, run once through each of the four methods.**
Model replies can vary between calls; the following blocks show example inputs and
outputs. The complete files required by these commands appear above.

```console
# ① Run a one-shot conversation with `cli`.
$ uv run flowing cli . "Introduce yourself in one sentence."
I am a concise AI assistant that provides clear, helpful answers within three sentences.

# ② Run an interactive REPL; the sample input follows the prompt, and the response is illustrative.
$ uv run flowing repl .
(agent-main)>>> Introduce yourself in one sentence.
[thinking] (reasoning trace omitted)
I am a concise AI assistant who provides clear, helpful answers while keeping every response to three sentences or fewer.
(agent-main)>>> /exit

# ③ Start `web` to serve an HTTP API and its built-in frontend; `-a` sets the host and `-p` sets the port.
# Terminal A starts the server and leaves it running.
$ uv run flowing web . -a 127.0.0.1 -p 8411
# Terminal B sends requests and inspects their responses.
$ curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8411/
200
$ curl -s -X POST http://127.0.0.1:8411/agents/agent-main/message \
    -H "Content-Type: application/json" \
    -d '{"text": "Introduce yourself in one sentence."}'
{"message_id": "7", "final_text": "I'm a concise AI assistant that answers questions clearly and helpfully in no more than three sentences."}

# ④ Start `serve` to provide only an HTTP API, without a frontend; `/healthz` checks health and the message endpoint accepts input.
# Terminal A starts the server and leaves it running.
$ uv run flowing serve . -a 127.0.0.1 -p 8412
# Terminal B sends requests and inspects their responses.
$ curl -s http://127.0.0.1:8412/healthz
{"status": "ok"}
$ curl -s -X POST http://127.0.0.1:8412/agents/agent-main/message \
    -H "Content-Type: application/json" \
    -d '{"text": "Introduce yourself in one sentence."}'
{"message_id": "9", "final_text": "I am a concise AI assistant designed to give clear, helpful answers within three sentences."}
```

How to read this demo: `cli` prints `final_text` and exits; `repl` streams the reply
as it is generated, and `/exit` triggers `shutdown()` before exiting; `web` and `serve` share
the same set of HTTP endpoints (`POST /agents/<agent-id>/message` delivers one
message and waits for the turn result), and the only difference is that web
additionally serves a built-in frontend page (which is why the demo runs web before
serve).

**Demo 2: runtime parameter injection** (the phenomenon only; the mechanics are in
4-9). The inline entry point declares the `user_name` parameter — the CLI's `--key value` passes
through `launch` verbatim to `main(**kwargs)`; after the mount,
`root.user_name = user_name`, and the `{% if user_name %}` in the `root.fya` template
is evaluated on the spot at the next context assembly. Run this after demo 1; those
earlier inputs did not mention a name, so the conversation history does not contain it:

```console
$ uv run flowing repl . --user_name Alice
(agent-main)>>> What is my name? Reply with the name itself only.
[thinking] (reasoning trace omitted)
Alice
(agent-main)>>> /exit
```

The agent answers on the spot with the injected name "Alice" — that name has never
appeared in the conversation history before; it can only have come from the
on-the-spot evaluation of the system prompt.

**Inline project materials**:

| File | Description |
|---|---|
| `main.py` | The complete entry point appears above. |
| `root.fya` | The complete agent declaration appears above. |
| `providers.yaml`, `models.yaml`, `model-tags.yaml` | The complete model configurations appear above. |
| Demo commands and displayed responses | The inputs, HTTP request bodies, and observable outputs appear above. |

## Summary

1. A subproject combines `main.py` for assembly policy, `root.fya` for the agent declaration, and the three model-configuration files.
2. `model_tag` resolves in two steps from a tag to an entry name and then to `ModelConfig`, with no silent fallback.
3. `launch` is the only entry point, and Runtime is the root of the object graph; `query` waits for a `TurnResult`, and all four outcomes resolve.
4. One subproject supports `cli`, `repl`, `web`, and `serve`, and this tutorial uses `repl` for its interactive examples.
