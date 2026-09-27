# 5-2 · Plugins

## Prerequisites

[1-9 First Look at Plugins and Composables](1-9-plugins-and-composables.md)
(usage only). This chapter includes the complete configuration, agent
definition, skill prompt, demonstration program, and recorded output inline.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| Two-phase enablement | The fixed two steps for enabling a plugin: phase one `runtime.install(...)` (Runtime-level global registration) → phase two `use_xxx(self)` in the Agent's `setup()` (instance-level enablement) |
| Plugin base class | The unified plugin contract exposes `name` / `namespace` / `dependencies` / `install(runtime)` / `shutdown()` |
| Dependency declaration | `dependencies` only declares: a cycle in the installed dependency graph → `DependencyError` (raised at the install call site); a missing entry only triggers `warnings.warn`, never raises |
| Install timing | Must happen before the first `mount()` — a late install has no effect on already-created Agents (their setup has already run and their registries are finalized) |

## Goals

This chapter explains two-phase enablement, dependency declarations, and
installation timing. Its recorded examples demonstrate skill loading and a
cron trigger.

## Main text

### Two-phase enablement

```python
# main.py — phase one: Runtime-level global registration (tool bodies / provide values / config namespace)
runtime.install(SkillPlugin(), CronPlugin())   # must precede the first mount

# root.fya $script — phase two: instance-level enablement (declare hook points + register handlers + bind instance methods)
async def setup(self):
    use_skill(self)
    use_cron(self)
```

install only registers, it does not assemble; for an Agent that skips phase
two, the extension "never existed" (zero overhead rather than being skipped).
**Late install is invalid**: an install that comes after mount finds that the
already-created Agent's setup has long finished — in demo ② the `skill-load`
tool body is invisible at creation time (`ToolNotFoundError`).

### Dependency declaration

`dependencies` is a declaration, not a request: a cycle → immediate
`DependencyError` (raised at exactly the install that introduced the cycle);
a missing entry → only a warning (declaring a dependency you do not use is
legal, so installs can be batched).

### Tour of builtin plugins (what + when)

| Plugin | What it is | When to use |
|---|---|---|
| skills | "Load a prompt snippet by name": catalog lazy injection + the `skill-load` tool | Give an Agent assemblable specialties (proven in 1-9) |
| comm | In-process message bus: point-to-point signals + publish-subscribe | Out-of-band interaction between Agents (bypasses LLM context, not persisted) |
| cron | Per-Agent scheduled tasks (`schedule-cron` / `manage-cron` tools + module API) | Timed reminders, periodic patrol checks |
| workflow | Explicitly orchestrated branches / loops / parallelism in Python code (non-Agent nodes) | Regular processes, deterministic control flow |
| clipboard | File-range cut / copy / paste | LLM moving code snippets across files |

This table is an orientation to the built-in extensions; the runnable example
below focuses on skills and cron.

## Out of scope

- This chapter does not provide complete parameter references for every
  builtin plugin.
- This chapter does not define the full cron-expression grammar.
- Chapter 5-3 shows a common structure for writing a Composable.

## Main example

The recorded transcript below shows one run; ③ is a real-time trigger:

```console
$ uv run python demo_plugins.py
== ① Dependency declaration: cycle fails fast ==
   p1 ↔ p2 depend on each other -> DependencyError
== ② Late install (install after mount) -> creation-time failure ==
   late-install launch failed: ToolNotFoundError (tool bodies/registries a plugin registers are phase-one products; install must precede the first mount)
== ③ cron real-time trigger (programmatic schedule, wait up to 70s) ==
   every-minute job scheduled, waiting for the trigger...
   on_cron_trigger fired 1 time(s); content=['[Reminder] Time to get up and move around.']
   EVENT messages in tree: 1 (delivery source: 'cron' or custom)
== ④ Stage-one artifacts check ==
   skill-load in global registry: True
   prompt block contains skills catalog: True
```

The cycle in ① raises an error at the install call site. Case ② shows that a
late install causes a creation-time error. Case ③ shows the cron chain:
module API `schedule(agent, cron,
content)` registration (a synchronous call), then at trigger time the
`on_cron_trigger` hook (the handler may rewrite the content or set
`shortcut=True` to skip) → the EVENT message is enqueued with STEER priority,
enters the tree, and starts a new turn. Case ④ confirms that phase one
registered the expected artifacts. A registered tool body remains invisible
to the LLM until the Agent explicitly declares `skill-load`:
**registration does not imply visibility**.

**Complete runnable materials.**

Save each block under its heading in one Flowing project. The provider
configuration preserves an environment-variable placeholder; set
`DEEPSEEK_API_KEY` in the environment before a run that contacts the provider.
The cron demonstration waits for a scheduled minute boundary, for at most
about 70 seconds. The negative case deliberately omits plugin installation.
Store every listed item in the same working directory; the skill prompt is a
single root-level file.

### Runtime entry: `main.py`

```python
from flowing import Runtime
from flowing.plugins.cron import CronPlugin
from flowing.plugins.skills import SkillPlugin


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.install(SkillPlugin(), CronPlugin())
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

### Agent definition: `root.fya`

```yaml
description: "Plugin tour assistant: skills + cron dual plugins."
model_tag: default
tools:
  - skill-load
  - schedule-cron
  - manage-cron
skills:
  - ./daily-tip.md
---
$system_prompt:
You are a plugin demo assistant. Use skill-load when the user wants to load a
skill; use schedule-cron for scheduled-reminder needs. When a cron trigger
fires, tell the user exactly what was received.
---
$script:
from flowing.plugins.cron import use_cron
from flowing.plugins.skills import use_skill


async def setup(self):
    use_skill(self)
    use_cron(self)
```

### Skill prompt: `daily-tip.md`

```markdown
---
description: "Daily tip: recommend one practical trick for the user's work context today."
---
# Daily Tip

Based on the work the user mentions for today, give one concrete,
immediately actionable tip (a command, a shortcut, or a process
improvement), stated in a single sentence.

## Example

Input: "Today I need to review pull requests for several hours."

Output: "Use your editor's diff-navigation shortcuts to jump between changed
files and hunks instead of scrolling through the full patch."
```

### Late-install negative entry: `notes-late-main.py`

This entry intentionally mounts the same agent without installing either
plugin. It is used only by the negative case in the demonstration.

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing-late")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### Demonstration program: `demo_plugins.py`

```python
import asyncio

from flowing import launch
from flowing.errors import DependencyError, MissingProvideError, ToolNotFoundError
from flowing.message import MessageKind
from flowing.plugins import Plugin
from flowing.plugins.cron import schedule


class _P1(Plugin):
    name = "p1"
    dependencies = ("p2",)

    def install(self, runtime):
        pass


class _P2(Plugin):
    name = "p2"
    dependencies = ("p1",)

    def install(self, runtime):
        pass


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    print("== ① Dependency declaration: cycle fails fast ==")
    try:
        runtime.install(_P1(), _P2())
    except DependencyError:
        print("   p1 ↔ p2 depend on each other -> DependencyError")

    print("== ② Late install (install after mount) -> creation-time failure ==")
    try:
        await launch(".", main_file="@/notes-late-main.py")
        print("   unexpected success (contradicts the contract)")
    except (ToolNotFoundError, MissingProvideError) as exc:
        print(f"   late-install launch failed: {type(exc).__name__}"
              f" (tool bodies/registries a plugin registers are phase-one products;"
              f" install must precede the first mount)")

    print("== ③ cron real-time trigger (programmatic schedule, wait up to 70s) ==")
    fired: list[str] = []

    async def on_fire(agent_, ctx):
        fired.append(ctx.content)
        return ctx
    agent.hooks.on_cron_trigger(on_fire, by="demo")

    schedule(agent, "*/1 * * * *", "[Reminder] Time to get up and move around.")
    print("   every-minute job scheduled, waiting for the trigger...")
    for _ in range(140):
        await asyncio.sleep(0.5)
        if fired:
            break
    for _ in range(40):
        await asyncio.sleep(0.5)
        events = [m for m in agent._messages.values()
                  if m.kind is MessageKind.EVENT
                  and m.source in ("cron", "scheduled_task")]
        if events:
            break
    print(f"   on_cron_trigger fired {len(fired)} time(s); content={fired!r}")
    print(f"   EVENT messages in tree: {len(events)} "
          "(delivery source: 'cron' or custom)")

    print("== ④ Stage-one artifacts check ==")
    print(f"   skill-load in global registry: "
          f"{agent.get_tool('skill-load') is not None}")
    print(f"   prompt block contains skills catalog: "
          f"{any(b.name == 'skills' for b in agent.prompt_blocks)}")
    await runtime.shutdown()


asyncio.run(main())
```

Run `uv run python demo_plugins.py` from the working directory in which you
saved these blocks. The provider key must be supplied through the
`DEEPSEEK_API_KEY` environment variable. All material required by the example
is included above.

## Summary

1. Two-phase enablement registers plugins globally before mount and enables
   each extension on an Agent through `use_xxx` in `setup()`.
2. Dependencies are declarations: cycles raise an error, missing entries
   produce warnings, and installs can be batched.
3. Late installation fails during Agent creation rather than causing a
   runtime surprise.
4. The builtin plugin tour covers skills, comm, cron, workflow, and clipboard.
