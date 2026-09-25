# 6-1 · Choosing Interfaces

## Prerequisites

[0-1 The First Agent](0-1-hello-agent.md) (the first run of the four modes
cli / repl / web / serve). This chapter includes the complete minimal
configuration, declarative input, commands, and relevant output inline. It
demonstrates `test` / `repl-debug` / `compile`; `run` shares the mechanism
of `web` / `serve` and is not demonstrated again.

## Glossary for this chapter

| Term | One-sentence definition |
|---|---|
| Closed subcommand set | The CLI's 8 subcommands (run / repl / cli / repl-debug / serve / web / test / compile): 8 names, 8 real commands, no aliases |
| `test` | Smoke subcommand: launch + snapshot assertion + shutdown — boots and stops (CI-friendly) |
| `repl-debug` | Debug-extended repl: extra debug commands such as `/eval` / `/watch` (live Jinja2 expression evaluation) |
| `compile` | Explicitly compiles `.fya` into `.py` artifacts (hash gate against overwriting; artifacts are statically analyzable) |

## Goals

Choose among the remaining commands by applying the interface-selection
criteria.

## Main text

### The closed subcommand set

| Subcommand | One-sentence responsibility | Form |
|---|---|---|
| `repl` | Interactive REPL for direct conversation and exploration | interactive |
| `cli` | One-shot conversation: a single INPUT delivery, then exit | script / pipeline |
| `repl-debug` | repl + `/eval` `/watch` and other debug commands | interactive / debugging |
| `run` | launch then long-running process (`await runtime`) | service |
| `test` | Smoke: launch + snapshot assertion + shutdown | CI |
| `serve` | Pure HTTP API (24 closed endpoints) | service |
| `web` | serve + built-in default frontend | service |
| `compile` | `.fya` → `.py` explicit compilation | build |

All eight share `launch(path)`; the interfaces layer only does "launch and
get a Runtime" — it parses no configuration, knows no plugins, and is
unaware of `@`. Exit codes 0 / 1 / 2 = success / runtime error / usage
error.

### Selection criteria

- Use `repl` when a person needs interactive development or debugging, and
  choose `repl-debug` when live runtime evaluation is required.
- Use `cli` when a script or CI job needs one response, and use `test` when
  CI needs to launch and stop the Runtime.
- Use `serve` when another program needs an API for a custom UI, and use
  `web` when a browser-ready interface is appropriate.
- Embed programmatically when the host process needs to hold the Runtime
  itself; Chapter 6-2 covers that approach.
- Use `compile` when a team needs a statically analyzable artifact or
  mypy-friendly generated types.

## Out of scope

- This chapter does not document each of serve's 24 endpoints.
- Chapter 6-2 covers the details of programming an embedding host.
- This chapter does not cover server-side WebSocket or SSE implementation;
  Chapter 6-2 uses HTTP polling to demonstrate the same integration idea.

## Main example

```console
$ uv run flowing test .
exit=0                    ← smoke: launch + snapshot + shutdown

$ echo "/eval agent.node_id" | uv run flowing repl-debug .
(agent-main)>>>agent-main ← /eval evaluates a Jinja2 expression in the Agent context

$ uv run flowing compile .
exit=0
```

Reading this recorded transcript: `test` is the boot-and-stop smoke for
CI; the `/eval` of `repl-debug` evaluates `agent.node_id` in the Agent
rendering context (the context of 4-9 enters here); the `.py` produced by
`compile` carries a hash-gate header comment — once the artifact is
modified externally, compile refuses to overwrite it
(`ArtifactModifiedError`).

**Complete inline materials.**

Save each block under its heading in one Flowing project. The configuration
keeps the provider credential as an environment-variable placeholder. The
compilation input is a root-level file; the artifact excerpt omits only its
location-dependent metadata.

### Runtime entry: `main.py`

```python
from flowing import Runtime


async def main(locale: str = "en") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs = {} if locale == "en" else {"locale": locale}
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

### Provider configuration: `providers.yaml`

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### Model configuration: `models.yaml`

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### Model tag: `model-tags.yaml`

```yaml
tags:
  default: deepseek-flash
```

### Agent definition: `root.fya`

```yaml
description: "Interfaces demo assistant: minimal Q&A."
model_tag: default
---
$system_prompt:
You are a concise English assistant.
```

### Compilation input: `scratch.fya`

```yaml
description: "Sample agent to be compiled."
model_tag: default
---
$system_prompt:
You are a compilation sample.
```

### Commands and recorded output

```console
$ uv run flowing test .
exit=0

$ echo "/eval agent.node_id" | uv run flowing repl-debug .
(agent-main)>>>agent-main
(agent-main)>>>
exit=0

$ uv run flowing compile .
exit=0
```

The relevant generated class definition is shown below. It contains the
statically analyzable description, system prompt, and model tag.

```python
from flowing import Agent, PENDING, Parsable


class ScratchAgent(Agent):
    description = Parsable("Sample agent to be compiled.")
    system_prompt = Parsable("You are a compilation sample.")
    model_tag = "default"
```

Run the commands from the working directory where these inline files were
saved. The compiled artifact is generated by the final command; no
location-specific path is needed to follow the example.

## Summary

1. The eight subcommands form a closed set, share `launch`, and should be
   selected according to who will use them.
2. The `test` command boots and stops, `repl-debug` evaluates expressions at
   runtime, and `compile` produces static artifacts.
3. The `serve` and `web` commands expose integration surfaces, while embedding
   means the host holds the Runtime itself, as Chapter 6-2 explains.
