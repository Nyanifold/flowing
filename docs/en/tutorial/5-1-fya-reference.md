# 5-1 · .fya Full-Field Reference and Complex Assembly

## Prerequisites

[4-9 The Parsable Evaluation System](4-9-parsable.md) (evaluation semantics of
`.fya` values), [4-4 Three-Layer Capability Description and the Binding
Layer](4-4-three-axes-and-tool-entry.md) (entry overrides), and [2-1 Building
a Team Declaratively](2-1-subagents-declarative.md) (subagents entries).
The complete declarative example, handwritten equivalent, and execution
materials are included below.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| Named block | A `$<dotted.path>:` block separated by `---`: system_prompt / description / script / deep-path blocks |
| Deep named block | A block whose path points inside a resource-list entry (e.g. `$subagents.greet.args.style.description:`) — addressed **by alias** |
| glob expansion | Resource-list entries support globs (`./tools/*.py`): explicit entries first, then globs; duplicate resources are skipped |
| Compilation equivalence | The `.fya` compiled product and a handwritten subclass produce the exact same class model (identical down to behavioral points) |

## Goals

The `.fya` full-field reference and complex assembly: one file to understand
the full field set, entry forms, deep blocks, and compilation equivalence.

## Main text

### The full field set (root.fya as a living reference)

```yaml
name: root                    # optional: consistency assertion (mismatch with inferred name → NameMismatchError)
description: …                # one-line intro (Parsable)
model_tag: default            # model tag (two-hop resolution, see 0-1)
args:                         # instance parameters: sugar (user_id: str) and JSON Schema expansion coexist
tools:                        # tool entries: path / bare name / glob / as alias / override body
subagents:                    # subagent entries: same structure (2-1 / 4-4)
metadata: …                   # arbitrary static key-values (not interpreted by the framework)
```

Legacy fields such as `model:` do not exist — the model is resolved only
through `model_tag` (see 0-1). Fields the framework does not recognize land
in `_extra` (extensions read them via `__getattr__`).

### Named blocks and deep blocks

- `$system_prompt:` / `$description:` / `$script:` are regular blocks
  (multi-line raw content);
- **Deep blocks are addressed by alias**: `$subagents.greet.args.style.description:`
  points at the empty slot of greet's override body — "list segments match by
  `EntryRef.alias`", and a miss raises `FormatError`;
- Writing into an empty slot: `style: {}` (an explicit empty patch).
- Layout discipline: a `---` must be followed by a block header (comment
  lines cannot sit between blocks; they can only live in the YAML header);
- A line starting with `#` inside raw block content is treated as body text.

### glob expansion and `as`

`tools: [./tools/*.py]` declares every `.py` tool in the directory in one
shot (**explicit entries first, then globs; duplicate resources are
skipped**); `as` gives an entry a different LLM-visible alias. Glob hits go
through form filtering (miscellany such as `__pycache__` is skipped — writing
`*.py` is the steadier choice).

### $script and compilation equivalence

`$script` is a Python module segment: `@on` hooks (declared on the class and
registered during `__init__`), `setup()`, and instance methods are all
allowed. `.fya` and a handwritten subclass are **compilation-equivalent**
(demo ① compares item by item: description / system_prompt template / args
schema key set are all equal). The failure assertions are part of fail-fast:
a mismatched `name` → `NameMismatchError`; an `@on` pointing at an
undeclared hook point → `UnknownHookPointError`.

## Out of scope

- The compiler pipeline internals, including generated-file overwrite
  protection and on-disk representation;
- Parameter bridging from field shorthand to validation schemas;
- The complete semantics of runtime evaluation — 4-9.

## Main example

**Demo 1: compilation equivalence + glob + negative cases** (`demo_fya.py`;
the complete recorded console output is included below):

```console
$ uv run python demo_fya.py
== ① compiled product equals handwritten subclass (item by item) ==
   description equal: True
   system_prompt template equal: True (block content carries a trailing newline; equal after strip)
   args schema equal (properties key set): True
== ② glob expansion and alias entry ==
   tools (glob expansion): ['audit', 'payment', 'subagent-invoke']
   subagent alias greet: source class='greeter' specified={'tone': 'casual'}
   catalog view: name='greet' (tone removed from the parameter table by specified: no tone in '<params>')
== ③ failure assertions (negative cases) ==
   name consistency assertion: NameMismatchError
   @on undeclared hook point: UnknownHookPointError: Unknown hook point: '@on markers found no owning hook point: no_such_hook (method _) — hook point name misspelled, or the corresponding plugin/Composable is not enabled in setup()' (not declared)
```

**Demo 2: end to end**: pass `--user_id u-7` and the inline input below.
The complete recorded exchange demonstrates the `subagent-invoke` dispatch,
the `before_tool_call` hook, and the user ID reaching the subagent. Generated
greeting text may differ on another run.

`repl_input.txt`:

```text
Have greet greet me in a playful style, and report the current user.
/exit
```

Run `uv run flowing repl . --user_id u-7 < repl_input.txt`. The full recorded
transcript follows; the reasoning trace is omitted.

```text
$ uv run flowing repl . --user_id u-7 < repl_input.txt
(agent-main)>>>I'll dispatch the greeting subagent now.
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "greet", "name": "greet", "prompt": "Greet the user in a playful style."}
[hook] before_tool_call: subagent-invoke
[tool:completed] subagent-invoke -> 
Here you go:

**Playful greeting** (from the `greet` subagent, agent-6abb57):

> "Well hello there, you wonderful human—ready to jump into something fun, maybe a little silly, and definitely awesome?"

**Current user:** `u-7`
(agent-main)>>> 
```

### Complete runnable materials

Save each block under its displayed relative filename. These are all files
needed for the two demonstrations. Set `DEEPSEEK_API_KEY` in the environment
before making a model call; no credential value or absolute machine path is
included.

`main.py`:

```python
from flowing import Runtime


async def main(user_id: str | None = None, resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        kwargs = {"user_id": user_id} if user_id else {}
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

`providers.yaml`:

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

`models.yaml`:

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

`model-tags.yaml`:

```yaml
tags:
  default: deepseek-flash
```

`root.fya`:

```yaml
name: root
description: "Complex assembly demo assistant: glob tools + aliased subagent + deep block + $script hook."
model_tag: default
args:
  user_id: str
  locale:
    type: string
    default: en
    description: Reply language
tools:
  - subagent-invoke
  - ./tools/*.py
subagents:
  - ./agents/greeter as greet:
      description: Greeting subagent (alias greet)
      args:
        tone: casual
        style: {}
---
$system_prompt:
You are the assembly demo assistant ({{ locale }} mode), current user {{ user_id }}.
---
$subagents.greet.args.style.description:
  Wording style of the greeting (formal / playful / classical).
---
$script:
from flowing import on

@on("before_tool_call")
def _(self, tool_call):
    print(f"[hook] before_tool_call: {tool_call.name}")
    return tool_call

async def setup(self, user_id: str, locale: str = "en"):
    self.user_id = user_id
    self.locale = locale
```

`agents/greeter/agent.fya`:

```yaml
description: Greeting subagent.
model_tag: default
args:
  tone:
    type: string
    default: neutral
    description: Tone
  style:
    type: string
    default: friendly
    description: Wording style
---
$system_prompt:
You are the greeter. Greet the user in a {{ tone }} tone, in one sentence.
```

`tools/payment.py`:

```python
from pydantic import BaseModel, Field

from flowing import ScriptTool


class PayArgs(BaseModel):
    order_id: str = Field(description="Order ID")
    amount: float = Field(ge=0.01, description="Amount")


class PaymentTool(ScriptTool):
    """Initiate a payment for the example order."""

    name = "payment"
    args_model = PayArgs

    async def execute(self, *, order_id: str, amount: float) -> dict:
        return {"paid": order_id, "amount": amount}
```

`tools/audit.py`:

```python
from flowing import ScriptTool


class AuditTool(ScriptTool):
    """Write an example audit log entry."""

    name = "audit"

    async def execute(self, *, action: str) -> dict:
        return {"audited": action}
```

`handwritten.py`:

```python
from pydantic import BaseModel, Field

from flowing import Agent, on
from flowing.parsable import Parsable


class RootArgs(BaseModel):
    user_id: str = Field(description="user_id")
    locale: str = Field(default="en", description="Reply language")


class RootAgent(Agent):
    description = (
        "Complex assembly demo assistant: glob tools + aliased subagent + "
        "deep block + $script hook."
    )
    system_prompt = Parsable(
        "You are the assembly demo assistant ({{ locale }} mode), "
        "current user {{ user_id }}."
    )
    model_tag = "default"
    args_model = RootArgs

    @on("before_tool_call")
    def _log(self, tool_call):
        print(f"[hook] before_tool_call: {tool_call.name}")
        return tool_call

    async def setup(self, user_id: str, locale: str = "en"):
        self.user_id = user_id
        self.locale = locale
```

`demo_fya.py`:

```python
import asyncio
from pathlib import Path

from flowing import launch
from flowing.errors import NameMismatchError, UnknownHookPointError
from handwritten import RootAgent


async def main() -> None:
    runtime = await launch(".", user_id="u-7")
    agent = await runtime.get_agent("agent-main")

    print("== ① compiled product equals handwritten subclass (item by item) ==")
    description_a = getattr(agent.description, "source", agent.description)
    description_b = getattr(RootAgent.description, "source", RootAgent.description)
    print(f"   description equal: {description_a == description_b}")
    print(f"   system_prompt template equal: "
          f"{agent.system_prompt.source.strip() == RootAgent.system_prompt.source.strip()} "
          "(block content carries a trailing newline; equal after strip)")
    fya_props = agent.args_model.model_json_schema()["properties"]
    py_props = RootAgent.args_model.model_json_schema()["properties"]
    print(f"   args schema equal (properties key set): "
          f"{sorted(fya_props) == sorted(py_props)}")

    print("== ② glob expansion and alias entry ==")
    print(f"   tools (glob expansion): {sorted(agent._tool_entries)}")
    greet = agent._subagent_entries["greet"]
    source_class = greet.name_ori.rsplit("::", 1)[-1]
    specified = {key: value.source for key, value in greet.specified.items()}
    print(f"   subagent alias greet: source class={source_class!r} "
          f"specified={specified}")
    view = greet.catalog_view(agent)
    print(f"   catalog view: name={view['name']!r} "
          "(tone removed from the parameter table by specified: no tone in '<params>')")

    print("== ③ failure assertions (negative cases) ==")
    Path("bad-name.fya").write_text(
        "name: wrong-name\nmodel_tag: default\n---\n$system_prompt:\nplaceholder\n",
        encoding="utf-8")
    try:
        runtime.get_agent_class("@/bad-name.fya")
    except NameMismatchError:
        print("   name consistency assertion: NameMismatchError")

    Path("bad-hook.fya").write_text(
        "---\n$system_prompt:\nplaceholder\n---\n$script:\n"
        "from flowing import on\n\n@on('no_such_hook')\n"
        "def _(self, turn):\n    return turn\n\n"
        "async def setup(self):\n    pass\n",
        encoding="utf-8")
    try:
        await runtime.create_agent("@/bad-hook.fya")
    except UnknownHookPointError as exc:
        print(f"   @on undeclared hook point: UnknownHookPointError: {exc}")
    await runtime.shutdown()


asyncio.run(main())
```

Run `uv run python demo_fya.py` for the assembly checks. Run
`uv run flowing repl . --user_id u-7 < repl_input.txt` for the provider-backed
dialogue; its input and recorded output are included earlier in this chapter.

## Summary

1. The `.fya` header field set: name assertion / description / model_tag /
   args (sugar + expansion) / tools / subagents / metadata;
2. Named blocks address deep paths by alias; empty slots take `{}`; no
   comment lines between blocks;
3. glob declares in one shot + `as` alias; explicit entries take precedence
   over globs;
4. The compiled product equals the handwritten subclass; the name / @on
   failure assertions are fail-fast.
