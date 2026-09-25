# 6-2 · Embedding into a Host Application

## Prerequisites

[4-8 Runtime Mechanics](4-8-runtime-mechanics.md) (`launch` and `@`
isolation) and [1-4 Hook Basics](1-4-hooks-basics.md) (the
`before_tool_call` approval gate). This chapter includes the complete runtime
configuration, host program, prompt, inputs, and recorded output inline.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| Embedding | The host process itself holds the Runtime returned by `launch`: input goes through `query` / `message` / `steer`, output goes through hook subscription, and config is wired via `set_config` / `provide` |
| Shutdown responsibility | Write-behind needs draining — exiting the process without `shutdown()` loses the tail records; CLI subcommands close it for you, but in embedding the responsibility moves to the host |
| Thin `main()` | In the embedding shape, main() may only assemble without interacting (it may even skip mounting, and the host calls `create_agent` on demand) |

## Goals

This chapter shows how to embed Flowing in an existing application through
four integration surfaces (configuration, shared objects, output, and
approval) and the host's shutdown responsibility.

## Main text

### Embedding = holding the Runtime + four integration points

```python
import asyncio

from flowing import launch


async def host_integration() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    runtime.set_config("agent.timeout", 90)

    async def on_delta(agent_, delta):
        return delta
    agent.hooks.on_provider_delta["_turn"](on_delta, by="host-ui")

    connection_pool = object()
    runtime.provide("workspace_root", "<workspace-root>")
    runtime.register_resource("db", connection_pool)

    async def approve(agent_, tool_call):
        return tool_call
    agent.hooks.before_tool_call(approve, by="host-approval")

    await runtime.shutdown()


asyncio.run(host_integration())
```

This setup registers the four integration surfaces. The host-driven example
below also sends a query, so the callbacks receive streaming and approval
events.

Coexisting runtimes rely on per-Task isolation of the `@` context
(demonstrated in 4-8); for strong isolation (multi-tenant / A-B testing),
use subprocesses instead of multiple runtimes.

### Three boundaries

1. **After `launch` returns, module-level `resolve()` is unavailable.**
   Use `runtime.resolve_path` instead, as described in Chapter 4-8.
2. **The host is responsible for calling `shutdown()`.** CLI subcommands
   close the Runtime automatically, but an embedding host must close it.
3. **Synchronous framework hosts** such as Django need a dedicated thread
   to run the event loop and submit coroutines through
   `run_coroutine_threadsafe`. Do not call `await` directly on the
   framework's synchronous thread.

## Out of scope

- Each application determines its own WebSocket or SSE protocol design.
- This tutorial does not cover multi-process deployment or load balancing.
- The framework does not define permission tiers; the host implements
  permissions at the hook layer.

## Main example

The transcript below records one host-driven run:

```console
$ uv run python demo_embed.py
1) host set_config override: agent.timeout = 90

[host approval] bash args={'command': 'echo hello from host'}
[host approval] approved
The output was: `hello from host`
[host] turn status=completed
[host] shutdown complete: tail records drained
```

The configuration override took effect (1). The tool call reached the host
approval handler before execution; the 0.2-second delay represents the time
needed for human confirmation. The streamed text reached the screen through
`on_provider_delta`. After the turn completed, the host called `shutdown()`
to drain the write-behind records. This demonstrates the four integration
points and the host's shutdown responsibility.

**Complete runnable materials.**

Save each block under its heading in one Flowing project. The API credential
is represented only by an environment-variable placeholder. The transcript
is one recorded model run; natural-language wording may vary while the bash
command output and host lifecycle remain the same.

### Runtime entry: `main.py`

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
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

### Agent definition and host prompt: `root.fya`

```yaml
description: "Embedding demo assistant: a minimal agent driven by the host."
model_tag: default
tools:
  - bash
---
$system_prompt:
You are an assistant inside a host application. Answer in one sentence.
```

### Host program, input, and shutdown: `demo_embed.py`

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    runtime.set_config("agent.timeout", 90)
    print(f"1) host set_config override: agent.timeout = "
          f"{runtime.get_config('agent.timeout')}")

    async def on_delta(agent_, delta):
        if delta.kind == "text" and delta.by == "_turn":
            print(delta.text, end="", flush=True)
        return delta
    agent.hooks.on_provider_delta["_turn"](on_delta, by="host-ui")

    async def approve(agent_, tool_call):
        print(f"\n[host approval] {tool_call.name} args={tool_call.args}")
        await asyncio.sleep(0.2)
        print("[host approval] approved")
        return tool_call
    agent.hooks.before_tool_call(approve, by="host-approval")

    result = await agent.query(
        "Use bash to run `echo hello from host` and tell me the output verbatim.")
    print(f"\n[host] turn status={result.status}")
    await runtime.shutdown()
    print("[host] shutdown complete: tail records drained")


asyncio.run(main())
```

The host input is the `agent.query` string in this program. Run
`uv run python demo_embed.py` from the working directory where the inline
files were saved. Supply `DEEPSEEK_API_KEY` through the environment; do not
place a credential in the configuration block.

## Summary

1. Embedding means holding the Runtime returned by `launch`; a thin
   `main()` can assemble the application without performing interactions.
2. The four integration points are `set_config`, `provide` and resources,
   hook subscriptions, and approval through `before_tool_call`.
3. The `@` context isolates coexisting runtimes per Task, while subprocesses
   provide stronger isolation.
4. The host owns shutdown because it must drain write-behind records.
