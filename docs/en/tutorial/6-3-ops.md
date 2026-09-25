# 6-3 · Operations

## Prerequisites

[1-8 Runtime and Agent Directories](1-8-runtime-and-agent-dirs.md) (directory
layout), [4-1 Core State and Message Persistence](4-1-core-state-and-message-persistence.md)
(crash recovery — this chapter's crash drill references it directly),
[4-8 Runtime Mechanics in Full](4-8-runtime-mechanics.md) (the pool and lazy
loading). This chapter includes the complete relevant configuration, program,
recorded inputs, and output inline.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| configuration chain | framework defaults < project-level `@/config.yaml` < user-level `$FLOWING_CONFIG_HOME/config.yaml`; the `set_config` overlay wins (not persisted) |
| configuration-file precedence | the resolution order of the three model files: `FLOWING_PROVIDERS_PATH` / `FLOWING_MODELS_PATH` / `FLOWING_MODEL_TAGS` environment variables > the default user configuration home; `set_providers` etc. override programmatically before mount |
| three tiers of forgetting | destroy (drop the instance, keep the records) → archive (remove from the registry, keep the files) → physical deletion (not provided by the framework) |
| `archive_orphans()` | archives pool entries whose parent is dangling (orphans left behind by crashes) |
| `_unstable` | experimental namespace: the feature is settled but the interface / format is not frozen; it may change or be removed in any version, and downstream projects must not depend on it |

## Goals

This chapter explains configuration and model-integration operations, agent
pool recovery, and runtime auditing through snapshots.

## Main text

### The configuration chain

```
set_config overlay (process-level, wins)
  > user-level  $FLOWING_CONFIG_HOME/config.yaml
  > project-level  @/config.yaml
  > framework defaults (e.g. agent.timeout=60)
```

`FLOWING_*` environment variables are read directly at their respective
consumption points (paths / switches); they do not enter the configuration
chain. Calling `get_config` before the construction-time shallow merge has
completed → `ConfigNotReadyError` (the classic failure mode of a typo at
import time).

### The operations surface of model integration

The two-hop resolution from 0-1 and the tag design from 3-4 are the "usage
surface"; the operations surface is configuration management: by default the
three files live under the user configuration home and can be
redirected via `FLOWING_PROVIDERS_PATH` / `FLOWING_MODELS_PATH` /
`FLOWING_MODEL_TAGS`; `set_providers` / `set_models` / `set_model_tags`
override them programmatically before mount. The complete default and
alternate configurations are included below.

### Pool, recovery, and forgetting

- The pool registry (`core.jsonl`) is the single source of truth for agent
  pool keys (4-1). The construction-time scan does not instantiate agents.
- `recover_agent(id)` automatically recurses up the parent chain, and
  descendants are recovered lazily when requested.
- The framework provides three tiers of forgetting: destroy (drop the
  instance but keep records), `archive_agent` (remove the entry from the
  registry but keep files), and physical deletion (handled by the application
  layer; archiving first is recommended).
- `archive_orphans()` cleans up orphan entries left behind by crashes.

### Observation and logging

`snapshot()` pulls in two levels (4-8): it is the most stable entry point
for runtime observation. Structured log persistence remains experimental —
**boundary statement**: `_unstable` is not part
of the cross-version stable contract; the format may change, and published
downstream artifacts must not depend on it.

## Out of scope

- The framework does not export metrics such as Prometheus metrics; the host
  can attach its own hooks.
- Operations teams choose the backup strategy for `persist_dir`.
- Flowing is a lightweight framework; applications handle multi-machine
  deployment at the application layer.

## Main example

The transcript below records one operations run:

```console
$ uv run python demo_ops.py
== ① Configuration chain: three layers ==
   project-level @/config.yaml overrides the framework default: agent.timeout = 45 (framework default 60)
   the set_config overlay wins: agent.timeout = 90
   FLOWING_CONFIG_HOME user-level: default user configuration home (not set)
== ② Model integration source override ==
   default provider candidates: ['deepseek']
   precedence: FLOWING_PROVIDERS_PATH environment variable > default path; set_providers overrides programmatically before mount
   the inline alternate configuration names its entry deepseek-alt; select it by calling set_providers('@/alt-providers.yaml') before mount
== ③ Pool / archive (two of the three tiers of forgetting) ==
   pool registry (including child agents): ['<generated-child-agent-id>', 'agent-main']
   get_agent after archive_agent → None (forgotten by the runtime, files kept on disk)
   destroy tier (drop instance, keep records) → archive tier (remove from registry); physical deletion is not provided by the framework, do it in the application layer
== ④ Snapshot audit ==
   nodes=['agent-main', 'runtime-0']
   config_overrides={'agent.timeout': 90}
   crash drill (kill -9 → restart recovery) is covered in 4-1; structured logging remains experimental and its format is not frozen
```

The crash drill (kill -9 → restart recovery → replay of a half-finished
turn) is covered in [4-1](4-1-core-state-and-message-persistence.md) —
recovery correctness is guaranteed by "replay = ordered replay of results",
so operations only needs to keep `persist_dir` safe.

**Complete runnable materials.**

Save each block under its heading in one Flowing project. All filenames are
root-level relative names, and the provider credential remains an environment
variable placeholder. The child-agent ID in the transcript is normalized
because it is generated at runtime.

### Runtime entry: `main.py`

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### Project configuration: `config.yaml`

```yaml
agent:
  timeout: 45
```

### Default provider configuration: `providers.yaml`

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### Alternate provider configuration: `alt-providers.yaml`

```yaml
deepseek-alt:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

To select the alternate provider entry, use this call instead of the default
provider call in the Runtime entry, before mounting the Agent:

```python
runtime.set_providers("@/alt-providers.yaml")
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
description: "Operations demo assistant: minimal Q&A."
model_tag: default
---
$system_prompt:
You are a concise English assistant.
```

### Operations program: `demo_ops.py`

```python
import asyncio
import os

from flowing import launch


async def main() -> None:
    print("== ① Configuration chain: three layers ==")
    runtime = await launch(".")
    print(f"   project-level @/config.yaml overrides the framework default: "
          f"agent.timeout = {runtime.get_config('agent.timeout')} "
          "(framework default 60)")
    runtime.set_config("agent.timeout", 90)
    print(f"   the set_config overlay wins: agent.timeout = "
          f"{runtime.get_config('agent.timeout')}")
    print(f"   FLOWING_CONFIG_HOME user-level: "
          f"{os.environ.get('FLOWING_CONFIG_HOME', 'default user configuration home (not set)')}")

    print("== ② Model integration source override ==")
    print(f"   default provider candidates: "
          f"{sorted(runtime.provider_registry._candidates)}")
    print("   precedence: FLOWING_PROVIDERS_PATH environment variable > "
          "default path; set_providers overrides programmatically before mount")
    print("   the inline alternate configuration names its entry deepseek-alt; "
          "select it by calling set_providers('@/alt-providers.yaml') before mount")

    print("== ③ Pool / archive (two of the three tiers of forgetting) ==")
    root = await runtime.get_agent("agent-main")
    child = await root.create_subagent("builtin::explore-agent", name="exp-1")
    print(f"   pool registry (including child agents): "
          f"{sorted(runtime._agent_pool)}")
    await runtime.archive_agent(child.node_id)
    print(f"   get_agent after archive_agent → "
          f"{await runtime.get_agent(child.node_id)} "
          "(forgotten by the runtime, files kept on disk)")
    print("   destroy tier (drop instance, keep records) → archive tier "
          "(remove from registry); physical deletion is not provided by the "
          "framework, do it in the application layer")

    print("== ④ Snapshot audit ==")
    snapshot = runtime.snapshot()
    print(f"   nodes={sorted(snapshot.nodes)}")
    print(f"   config_overrides={snapshot.config_overrides}")
    print("   crash drill (kill -9 → restart recovery) is covered in 4-1; "
          "structured logging remains experimental and its format is not frozen")
    await runtime.shutdown()


asyncio.run(main())
```

Run `uv run python demo_ops.py` from the working directory where these
inline files were saved. The provider credential is read through
`DEEPSEEK_API_KEY`; no credential value or machine-specific path is included.
The demonstration does not call the model, so it does not need model-generated
text as input or output.

## Summary

1. The configuration chain orders defaults, project configuration, user
   configuration, and the process-level `set_config` override.
2. Environment variables can redirect the three model-file locations, and
   application code can override them programmatically.
3. Forgetting has three tiers: destroy, archive, and application-managed
   physical deletion; `archive_orphans()` handles orphan entries.
4. `snapshot()` is the primary observation channel, and applications must
   respect the unstable boundary of `_unstable`.
