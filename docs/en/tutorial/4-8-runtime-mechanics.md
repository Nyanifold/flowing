# 4-8 · Runtime Full Mechanics and @ Path Resolution

## Prerequisites

[0-1 First Agent](0-1-hello-agent.md) (the `launch` / `mount` surface) and
[1-8 Runtime and Agent Dirs](1-8-runtime-and-agent-dirs.md) (the
persistence root). The complete dual-Runtime demonstration creates both
project configurations from text embedded in the program below.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| Creation pipeline | The full sequence `launch` → `main()` → `mount` / `create_agent` / `recover_agent`: register the @ context → import and await `main(**kwargs)` → reset |
| `@` context | The project root registered by `launch` (a ContextVar, isolated per asyncio Task); the resolution base for the `@/` prefix |
| Three registries | The three global registries — Tool / Agent type / Skill (fully-qualified keys `ns::name`); bare-name lookup prefers `default::` over `builtin::`; **files override the registry** |
| Resource | Out-of-tree direct reference via `runtime.register_resource`: the sharing channel for heavy resources (connection pools, indexes) across Agents, not routed through the injection chain |
| Agent pool | The Runtime's agent pool registry (`agent_id → the four identity keys`); scans the roster at construction and loads lazily, never instantiating unused entries |

## Goals

The full mechanics of the Runtime class: the complete creation pipeline,
the three registries and namespaces, the @ path mechanics, the provide
endpoint and Resource, the agent pool, and the two-level snapshot
observation.

## Main text

### Creation pipeline: the full picture

```
launch(path, **kwargs)
  ├─ register the @ context (ContextVar = absolute value of path; isolated per Task)
  ├─ import <path>/main.py → await main(**kwargs) (kwargs passed through verbatim)
  │    └─ Runtime(): freeze project_root / register builtins / scan provider candidates
  │       and the agent pool (nothing instantiated) / shallow-merge the three-layer config / self-register the core+default bags
  │    └─ install / provide / register_resource / mount / recover_agent …
  └─ reset the @ context (after main() returns; already-spawned child Tasks are unaffected)
```

`mount` / `create_agent` / `recover_agent` share one pipeline (delegating
to `Runtime.create_agent`): `__new__` pre-binds identity → `__init__`
builds the synchronous skeleton → write `meta.json` → `before_create` →
`setup(**kwargs)` → PENDING check → model resolution → registration →
`after_create` → start the resident work loop. The recovery pipeline is
isomorphic (`_restore` replay plus the `before_recover` / `after_recover`
hook pair).

### @ path mechanics

- `@/` = **subproject root** (the `path` argument of `launch`); `./` and
  `../` are resolved against the referring party's source_dir;
  `file::ClassName` with `::` disambiguates classes in one file;
- Two entry points: module-level `flowing.resolve()` (only available
  inside the `main()` call stack of `launch` — it raises `RuntimeError`
  once the context is reset) and `runtime.resolve_path()` (the Runtime
  holds the frozen root and is always available);
- **Isolated per asyncio Task**: one process can run multiple Runtimes
  concurrently, and their `@/` prefixes never interfere (demo ① evidences
  this); genuine process-level strong isolation (multi-tenant / A-B)
  requires subprocesses.

### Three registries and namespaces

`ToolRegistry` / `AgentRegistry` / `SkillRegistry` are isomorphic:
fully-qualified keys `ns::name`; bare-name lookup prefers `default::` over
`builtin::` (the channel through which plugins override builtins — the
mechanistic answer to 0-2's "namespace omission"); **files override the
registry** — a bare name first goes through the source_dir file chain.
Registrations derived from files carry a path namespace (e.g.
`@/tools::demo`) and never pollute the default view.

### provide endpoint / Resource / agent pool

- The provide chain ends at the Runtime: a root provide is visible to the
  whole tree (1-5);
- A Resource is an out-of-tree direct reference: heavy resources bypass
  the injection chain and are not bound by provide semantics (demo ③);
- Agent pool: construction only scans the roster without instantiating;
  `get_agent` triggers "key present but no value → on-the-spot recovery";
  providers work the same — the candidate list is in place at
  construction, and the first get instantiates (demo ④: 1 candidate /
  0 instantiated).

### snapshot: two-level observation channels

`runtime.snapshot()` (node table / plugin manifest / pool) and
`agent.snapshot()` (model / queue / execution entries / message tree
view) — a **pull channel**: every field is JSON-serializable and provide
values never enter it; it only observes and never controls (the
prerequisite mechanism for 6-3 operations).

## Out of scope

- Two-phase plugin enablement (install for global registration → setup
  for instance-level use) — 5-2;
- Operations-side cleanup of the agent pool (`archive_orphans` / the
  three forgetting tiers) — 6-3;
- The full topic of three-layer configuration merging — 6-3;
- The Parsable evaluation system — 4-9.

## Main example

`demo_runtime.py` brings up two Runtimes in one process; the complete
recorded console output is included below:

```console
$ uv run python demo_runtime.py
== ① Two coexisting Runtimes and @ isolation ==
   A project root matches its configured root: True
   B project root matches its configured root: True
   B's @/root.fya resolves to B's own root: True
== ② Availability scope of module-level resolve() ==
   calling flowing.resolve() after launch returns → RuntimeError: @ context not registered via flowing.launch; resolve() unavailable
== ③ provide endpoint / Resource (out-of-tree direct reference) ==
   root inject('app_name') = 'demo-a' (chain endpoint = Runtime)
   get_resource('db').dsn = 'sqlite:///demo.db' (no injection chain; direct reference to the shared instance)
== ④ Pool registry / lazy providers ==
   pool entries: ['agent-main']
   provider candidates: 1 / instantiated: 0
== ⑤ snapshot (pull channel) ==
   runtime.snapshot: nodes=['agent-main', 'runtime-0'] plugins=[]
   agent.snapshot: node_id=agent-main model=deepseek-v4-flash queue=MessageQueueInfo(size=0, pending=0) tree=MessageTreeInfo (message tree view)
```

Reading this transcript: two Runtimes coexist in one process, and each
`@/` anchors to its own project root (B's root is the nested directory)
— the evidence for Task isolation; module-level `resolve()` closes once
the context resets, while the Runtime's frozen root is not subject to
this limit; lazy loading makes "1 provider configured, 0 instantiated"
the normal state.

### Complete runnable material

Save the following complete program as `demo_runtime.py`. It writes both
minimal project configurations to temporary locations, launches each Runtime,
and removes the temporary files after shutdown. The program makes no provider
call; the credential remains an environment-variable placeholder.

```python
import asyncio
import tempfile
from pathlib import Path

import flowing
from flowing import launch


def write_project(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "main.py": '''from flowing import Runtime

async def main(resume=None):
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
''',
        "root.fya": '''description: "Runtime mechanics demo assistant: minimal Q&A."
model_tag: default
---
$system_prompt:
You are a concise assistant.
''',
        "providers.yaml": '''deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
''',
        "models.yaml": '''deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
''',
        "model-tags.yaml": '''tags:
  default: deepseek-flash
''',
    }
    for filename, content in files.items():
        (root / filename).write_text(content, encoding="utf-8")


class FakeDB:
    def __init__(self, dsn: str):
        self.dsn = dsn


async def main() -> None:
    with tempfile.TemporaryDirectory() as temporary_root:
        base = Path(temporary_root)
        project_a, project_b = base / "a", base / "b"
        write_project(project_a)
        write_project(project_b)

        print("== ① Two coexisting Runtimes and @ isolation ==")
        rt_a = await launch(str(project_a))
        rt_b = await launch(str(project_b))
        print("   A project root matches its configured root: "
              f"{rt_a.project_root == project_a.resolve()}")
        print("   B project root matches its configured root: "
              f"{rt_b.project_root == project_b.resolve()}")
        print("   B's @/root.fya resolves to B's own root: "
              f"{rt_b.resolve_path('@/root.fya') == rt_b.project_root / 'root.fya'}")

        print("== ② Availability scope of module-level resolve() ==")
        try:
            flowing.resolve("@/providers.yaml")
            print("   unexpectedly available (violates the contract)")
        except RuntimeError as exc:
            print(f"   calling flowing.resolve() after launch returns → RuntimeError: {exc}")

        print("== ③ provide endpoint / Resource (out-of-tree direct reference) ==")
        rt_a.provide("app_name", "demo-a")
        rt_a.register_resource("db", FakeDB("sqlite:///demo.db"))
        root_a = await rt_a.get_agent("agent-main")
        print(f"   root inject('app_name') = {root_a.inject('app_name')!r} "
              "(chain endpoint = Runtime)")
        print(f"   get_resource('db').dsn = {root_a.get_resource('db').dsn!r} "
              "(no injection chain; direct shared reference)")

        print("== ④ Pool registry / lazy providers ==")
        print(f"   pool entries: {sorted(rt_a._agent_pool)}")
        print(f"   provider candidates: {len(rt_a.provider_registry._candidates)} / "
              f"instantiated: {len(rt_a.provider_registry._instances)}")

        print("== ⑤ snapshot (pull channel) ==")
        snapshot = rt_a.snapshot()
        print(f"   runtime.snapshot: nodes={sorted(snapshot.nodes)} "
              f"plugins={snapshot.plugins}")
        agent_snapshot = root_a.snapshot()
        print(f"   agent.snapshot: node_id={agent_snapshot.node_id} "
              f"model={agent_snapshot.model.model} "
              f"queue={agent_snapshot.message_queue} "
              f"tree={type(agent_snapshot.messages).__name__} "
              "(message tree view)")
        await rt_a.shutdown()
        await rt_b.shutdown()


asyncio.run(main())
```

Run `uv run python demo_runtime.py`. The complete recorded console output is
shown above. The only literal input is the `DEEPSEEK_API_KEY` environment
placeholder; this program does not use it to make a provider request.

## Summary

1. `launch` is the single entry point: register @ → await main() →
   reset; the whole pipeline diagram is one delegation;
2. `@/` anchors the subproject root and is isolated per Task;
   module-level `resolve()` works only inside the main() stack;
3. The three registries are isomorphic: bare names prefer `default::`
   over `builtin::`, and files override the registry;
4. The provide chain ends at the Runtime, a Resource is an out-of-tree
   direct reference, and both the pool and providers are lazy;
5. snapshot provides two-level pull observation: it only observes and
   never controls.
