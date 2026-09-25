# Flowing

**English** | [中文](README.zh.md)

Flowing is a lightweight, extensible, descriptive agent runtime framework for complex interactions (Python ≥ 3.13). In Flowing, an agent's entire definition — its role and prompt, the LLM it uses, its tools and subagents, composable extensions, and hook code — lives in a single `.fya` file. The framework opens its execution pipeline to extensions at key points, and its runtime can be embedded into any Python host application as an ordinary object. The core does only three things: message flow, error classification, and hook dispatch; policies such as retries, compaction, and approvals are mounted on demand as composables or plugins.

## Who it's for

- You want every layer of the framework to be readable, modifiable, and auditable, rather than a black box of policy configuration;
- You need fine-grained control over conversation history — branching off from any message, rewriting or pruning the past, instead of append-only dialogue;
- You need the same capability to present different model views on different agents, with an explicit safety boundary: being registered does not mean the model can see it.

These needs don't point to any single application shape: in Flowing, orchestration logic is plain Python code, with sequencing, branching, and concurrency expressed by the language itself. As a foundational runtime framework, it can be used to build coding, education, e-commerce, or companion agents as well as multi-agent systems, and to run multi-agent interaction experiments.

## Highlights

- **Message-driven runtime model**: each agent instance owns a priority message queue and a persistent work loop; user input, model responses, tool results, external events, and subagent receipts are all represented as messages, each driving a turn. `query()` waits for the turn result, `message()` is fire-and-forget, `steer()` redirects an in-flight turn.
- **A message-level tree for history**: conversation history is a forest of messages, not a linear list. `fork()` moves a cursor to open a parallel branch while old branches stay intact; history itself supports five surgical operations — insert, branch, remove, update, reparent — all persisted as usual.
- **Three-layer capability description**: the executable object, the LLM-visible declaration, and the agent-level binding evolve independently — the same tool can appear under different names, descriptions, and parameter views on different agents. Built-in tools ship with the runtime, but they must be explicitly declared to become visible to the model.
- **Declarative `.fya`**: an agent's description, model intent, capability bindings, system prompt, and hook code live in one self-contained file; the compiled output is fully equivalent to a hand-written `Agent` subclass.
- **Instance-level hooks**: every instance has its own hook registry, with hook points spanning the lifecycle, turns, messages, model calls, tool execution, and subagents. A handler can rewrite data, asynchronously wait for external confirmation, or raise `Intercepted` to hard-block the operation — a natural foundation for human approval gates.
- **Automatic persistence and crash recovery**: messages and state are persisted write-behind; after a crash, replay rebuilds the state. Unpaired tool calls are sealed during recovery, so the model always sees a complete, paired history.
- **Zero-code tool access**: MCP servers (stdio / SSE / HTTP), shell command templates (arguments auto-escaped), and HTTP endpoints can all become tools from a single declaration file.
- **Complete exposure options**: eight subcommands — REPL, one-shot CLI, plain HTTP API, a built-in web frontend, CI smoke tests, and more — run the same project unchanged in any form.

## Installation

```bash
pip install flowing-agent
```

## Quick start

Create a directory with five files.

`root.fya`:

```yaml
description: Minimal Q&A assistant
model_tag: default
---
$system_prompt:
You are a concise assistant. Answer in at most three sentences.
```

`main.py`:

```python
from flowing import Runtime

async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

Model access is split into three files, declaring the access identity, the model entries, and the tag mapping:

```yaml
# providers.yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
openrouter:
  adapter: openrouter
  base_url: https://openrouter.ai/api/v1
  api_key: "{{env.OPENROUTER_API_KEY}}"
```

```yaml
# models.yaml
luna:
  provider: openrouter
  model: openai/gpt-6-luna
  "reasoning.effort": high
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

```yaml
# model-tags.yaml
tags:
  default: luna
```

Run:

```console
$ export OPENROUTER_API_KEY='<your key>'
$ flowing repl .
(agent-main)>>> Introduce yourself in one sentence.
I'm a concise assistant, keeping answers to three sentences or fewer.
(agent-main)>>> /exit
```

The conversation doesn't vanish on exit: the messages it produced are persisted under `.flowing/` in the project directory. A fixed `agent_id` means that running `flowing repl .` again brings back the same agent with its full history — no recovery code required.

## Going further

- **Capability access**: declare built-in tools, MCP servers, or shell commands and HTTP endpoints as tools via `tools:`; implement custom logic with `ScriptTool`.
- **Multi-agent**: declare subagent types under `subagents:` and the orchestrator routes work using the auto-generated catalog; you can also create subagents programmatically and run them in parallel.
- **Intervention**: attach handlers at hook points — intercept a tool call pending approval, audit at turn completion, switch models and retry on provider errors. The built-in `use_retry` / `use_compact` are implemented in exactly this way and can serve as references.
- **Embedding**: the host application holds the Runtime returned by `launch()`; input goes through `query` / `message` / `steer`, output through hook subscriptions (streaming output, completion notices, call interception).

## Documentation

- [Beginner tutorial](https://flowing-agent.readthedocs.io/en/beginner-tutorial/): for readers new to agent systems development;
- [Detailed tutorial](https://flowing-agent.readthedocs.io/en/tutorial/): from quick start to core mechanics and operations;
- [Concise reference](https://flowing-agent.readthedocs.io/en/flowing-ref/): the whole framework in four parts, for quick lookup;
- [API reference](https://flowing-agent.readthedocs.io/en/api.html): the public API organized by module.

## Project status

Current version 0.1.0. The framework core is complete (700+ test cases passing) and the project is in the example-scenarios phase. Breaking changes are still possible during 0.x; every such release ships with a detailed changelog explaining how to migrate.

This project was developed with heavy reliance on AI assistance, using models from different providers at different capability levels. Limited by the author's available time, not every line of code has been individually reviewed; if you find any divergence between the implementation and the documentation (docstrings, tutorials), an issue is greatly appreciated.

## License

[MIT](LICENSE) © 2026 Nyanifold

---

<p align="center">
  <img src="assets/oh-wishes.svg" alt="Oh wishes... I beg you coalesce!">
</p>
