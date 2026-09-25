# Example: 3-3 Extension Architecture

The offline script demonstrates two-phase plugin enablement, a late-install failure, dependency-cycle validation, a rate-limit Composable, and unloading a handler group. The complete code, configuration, prompts, inputs, and example outputs are included below.

Use Python 3.13 or later, uv, and an environment where the Flowing CLI and package are available. Model-backed runs also require your own `DEEPSEEK_API_KEY` in the shell; no real credential is included here. Create the relative files exactly as shown under a fresh working directory, and run the commands from that directory.

## Reproduce

### Run the extension demonstration

Run the following command from the working directory containing the inline files:

```console
$ uv run python demo_extensions.py
```

This script makes no model calls. Run it once in a fresh working state; the late-install failure can leave state behind, so use a fresh state for another attempt.

## Complete inline materials

Each block below contains the complete content of a relative file used by this example. Output blocks are sample transcripts; model responses and host-specific details can vary.

### `composables/rate_limit.py`

```python
"""Self-written Composable example: use_rate_limit -- rate limiting for tool calls.

Example structure (isomorphic to the hook-mounting form of built-in Composables):
  use_xxx(agent, ...) -> optionally declare an extension hook point
  -> optionally attach a handler to a core hook point.
The same function can be called with different parameters; its implementation
determines the effect of repeated calls. This example's rate-limit counter is
needed only during the current runtime, so it lives in a closure. Persistent
state can use agent.state.register() or an application-managed persistence and
recovery mechanism.
"""
import asyncio

from flowing.errors import Intercepted


def use_rate_limit(agent, *, max_calls: int = 5, window_seconds: float = 60.0) -> None:
    """Tool calls beyond the limit within the window are blocked with an Intercepted (blocked) result.

    Declares the observation hook point ``on_rate_limited`` (match_on="name", filtered by tool name);
    the main logic attaches to ``before_tool_call`` (managed as one group via by="rate-limit").
    """
    agent.hooks.declare("on_rate_limited", by="rate-limit", match_on="name")

    calls: list[float] = []

    async def _gate(agent_, tool_call):
        now = asyncio.get_event_loop().time()
        calls[:] = [t for t in calls if now - t < window_seconds]   # sliding-window cleanup
        if len(calls) >= max_calls:
            await agent_.hooks.on_rate_limited.dispatch(agent_, tool_call)  # observation channel
            raise Intercepted(
                f"Rate limit exceeded: {max_calls} calls / {window_seconds:.0f} seconds,"
                f" please try again later")
        calls.append(now)
        return tool_call

    agent.hooks.before_tool_call(_gate, by="rate-limit", tags=["rate-limit"])


def remove_rate_limit(agent) -> int:
    """Remove the whole group (the standard pattern for by-group management)."""
    return agent.hooks.before_tool_call.remove_by_owner("rate-limit")
```

### `demo_extensions.py`

```python
"""Extension architecture demo: two-phase enablement / late install / dependency cycles / policy injection and unloading.

Run: uv run python demo_extensions.py
"""
import asyncio
import sys

from flowing import Runtime, launch
from flowing.errors import DependencyError, MissingProvideError, ToolNotFoundError
from flowing.plugins import Plugin
from flowing.tool import ToolCall

sys.path.insert(0, "composables")
from rate_limit import remove_rate_limit, use_rate_limit


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

    # (1) Two-phase enablement: phase 1 install registers the bodies, phase 2 use_skill in setup enables them
    print("== (1) Products of two-phase enablement ==")
    print(f"   skill-load in the global registry: "
          f"{agent.get_tool('skill-load') is not None}")
    print(f"   prompt block contains skills catalog: "
          f"{any(b.name == 'skills' for b in agent.prompt_blocks)}")

    # (2) Late install is invalid: mount first, install later -> creation-time failure
    print("== (2) Late install (install after mount) -> creation-time failure ==")
    try:
        await launch(".", main_file="@/notes-late-main.py")
        print("   unexpected success (violates the contract)")
    except (ToolNotFoundError, MissingProvideError) as exc:
        print(f"   late-install launch failed: {type(exc).__name__}"
              f" (the plugin's registered tool bodies belong to phase 1;"
              f" install must precede the first mount)")

    # (3) Dependency declaration: a cycle raises DependencyError at install time
    print("== (3) Dependency declaration: cycles fail fast ==")
    try:
        runtime.install(_P1(), _P2())
    except DependencyError:
        print("   p1 <-> p2 depend on each other -> DependencyError")

    # (4) Policy injection: self-written use_rate_limit (60s window, max 3 calls)
    print("== (4) use_rate_limit(max_calls=3) injection ==")
    limited: list[str] = []
    use_rate_limit(agent, max_calls=3, window_seconds=60.0)

    async def observe(agent_, tool_call):
        limited.append(tool_call.name)
        return tool_call
    agent.hooks.on_rate_limited(observe, by="demo")

    results = []
    for i in range(5):   # fire 5 calls in a row: first 3 allowed, last 2 blocked
        r = await agent.tool_call(ToolCall(id=f"c{i}", name="echo",
                                           args={"text": f"call {i}"}))
        results.append(r.status)
    print(f"   5 call results: {results}")
    print(f"   on_rate_limited observed {len(limited)} blocked calls (calls 4 and 5)")

    # (5) Unloadable: removing the whole by-group restores the original behavior
    print("== (5) remove_by_owner removes the whole group ==")
    removed = remove_rate_limit(agent)
    r = await agent.tool_call(ToolCall(id="c9", name="echo", args={"text": "resume"}))
    print(f"   removed {removed} handler(s); one more call: status={r.status}")

    await runtime.shutdown()


asyncio.run(main())
```

### `demo_output.txt`

```text
== (1) Products of two-phase enablement ==
   skill-load in the global registry: True
   prompt block contains skills catalog: True
== (2) Late install (install after mount) -> creation-time failure ==
   late-install launch failed: ToolNotFoundError (the plugin's registered tool bodies belong to phase 1; install must precede the first mount)
== (3) Dependency declaration: cycles fail fast ==
   p1 <-> p2 depend on each other -> DependencyError
== (4) use_rate_limit(max_calls=3) injection ==
   5 call results: ['completed', 'completed', 'completed', 'blocked', 'blocked']
   on_rate_limited observed 2 blocked calls (calls 4 and 5)
== (5) remove_by_owner removes the whole group ==
   removed 1 handler(s); one more call: status=completed
```

### `main.py`

```python
"""Entry point of the extension example project: launch imports this file and awaits main()."""

from flowing import Runtime
from flowing.plugins.skills import SkillPlugin


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # Phase 1: install performs global registration (tool bodies / registries) -- must precede the first mount
    runtime.install(SkillPlugin())
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### `model-tags.yaml`

```yaml
# tag -> model entry name mapping (single value: one tag maps to one entry).
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

### `notes-late-main.py`

```python
"""Entry point for the late-install negative example: no plugin install before mount."""

from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing-late")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # Deliberately no install(SkillPlugin()) -- use_skill in root.fya's setup will find no registry to inject
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### `providers.yaml`

```yaml
# provider entry: one entry = one API key identity.
# {{env.VAR}} is substituted at load time; if missing, it becomes an empty string with a warnings.warn, and loading continues.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `root.fya`

```text
description: "Extension example assistant: skill-load + echo tool + policy injection demo."
model_tag: default
tools:
  - skill-load       # the tool body registered by the plugin's install; visible to the LLM only when declared
  - ./tools/echo.py
skills:
  - ./skills/daily-tip.md
---
$system_prompt:
You are a demo assistant. Use skill-load when the user wants to load a skill. Keep answers to one sentence.
---
$script:
from flowing.plugins.skills import use_skill


async def setup(self):
    # Phase 2: instance-level enablement (declare hook point + register handler + inject the skills catalog)
    use_skill(self)
```

### `tools/echo.py`

```python
"""Echo tool (call material for the policy injection demo)."""
from pydantic import BaseModel

from flowing import ScriptTool


class EchoArgs(BaseModel):
    text: str


class EchoTool(ScriptTool):
    """Returns the input text unchanged."""

    name = "echo"
    args_model = EchoArgs

    async def execute(self, *, text: str) -> str:
        return text
```

### `skills/daily-tip.md`

```markdown
---
description: "Daily tip: recommend one practical trick for the user's work today."
---
# Daily Tip

Based on the work the user mentions for today, give one concrete, immediately
actionable tip (a command, a shortcut, or a process improvement), stated in one
sentence.
```
