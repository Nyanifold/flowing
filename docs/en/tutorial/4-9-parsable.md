# 4-9 · The Parsable Evaluation System

## Prerequisites

[0-1 Your First Agent](0-1-hello-agent.md) (first look at `{{ user_name }}`),
[1-5 provide and inject](1-5-provide-inject.md) (multilingual templates),
[4-4 Three-layer capability description and the binding layer](4-4-three-axes-and-tool-entry.md)
(injection expressions). The complete offline program and its configuration
are included below.

## Glossary for this chapter

| Term | One-line definition |
|---|---|
| Evaluation system | Collective name for the five Parsable forms + the render context + the PENDING sentinel |
| The five forms | `LITERAL` / `FILE_REF` (`$./`) / `EXPRESSION` (a complete `{{ }}`) / `TEMPLATE` (mixed text) / `RAW` (`$"..."`) — the type is auto-inferred from the shape of the source |
| Lazy evaluation | Construction stores only the instruction; rendering happens on use (file reads and template rendering all occur at resolve time) — the prerequisite for correctness |
| PENDING | The parse product of `field: _` in `.fya`: a sentinel for a deferred-definition promise; the only legal test is `is` |
| Render context | Instance attributes flattened + the reserved-name entries `env` / `config` / `agent` / `self` |

## Goals

Master Parsable — flowing's value-expression and evaluation system: fya
values, args defaults, the system_prompt template, and ToolEntry `specified`
/ injection expressions all share the same machinery.

## Main text

### The five forms (demo ①)

| Form | Detection | resolve result |
|---|---|---|
| `RAW` | Greedy match on `$"..."` | The text inside the quotes, **with all rendering skipped** (use it for a literal `$` or `{{ }}`) |
| `FILE_REF` | A path starting with `$` | Reads the file at resolve time and renders the content (edit the file and the next evaluation picks up the new value) |
| `EXPRESSION` | Exactly one complete `{{ }}` | **The expression's native type** (int/dict... not stringified) — the only source of non-str results |
| `TEMPLATE` | Mixed text containing Jinja2 syntax | The rendered str |
| `LITERAL` | Anything else | Returned unchanged |

### Render context and reserved names (demos ②④)

The context of `resolve(agent)` = instance attributes flattened + reserved
entries: `env` (a read-only view of os.environ) / `config` (the
configuration chain) / `agent` / `self` (the instance itself). An instance
attribute that **occupies a reserved name** → `ReservedAttributeError`
(fail fast, no silent shadowing).

### `{{ x.resolved }}`: the explicit evaluation entry (demo ③)

To reference another Parsable object inside a template: writing `{{ x }}`
shows only its template source; to get the rendered result you must write
`{{ x.resolved }}` — **every step of nested rendering is explicit**, and no
infinite-recursion channel exists. A Parsable in class-attribute form binds
to the instance automatically through the descriptor protocol; the
instance-attribute form binds explicitly via `.bind(agent)`.

### Boundaries (demo ⑤)

- Unbound + `resolve()` with no context → `MissingContextError`;
- A miss on the injection chain → `MissingProvideError` (the same channel
  as 1-5's boundary);
- An exception raised while evaluating an `EXPRESSION` propagates unchanged
  (handled by the turn-level `on_provider_error` error path).

### The PENDING sentinel and position-based semantics (demo ⑥)

`field: _` parses to the PENDING singleton; position determines semantics:

| Position | Semantics |
|---|---|
| Field position | **A promise that must be fulfilled**: still PENDING after setup → `MissingFieldError` (a checkpoint in the creation pipeline) |
| Override position (entry args, etc.) | An empty patch: the override position is declared but its content is empty, so the value is backfilled from the base |
| Resource list item | Forbidden: `FormatError` (no alias available to fulfill it) |

`$"_"` (the RAW form) is an ordinary string and does not trigger PENDING.

## Out of scope

- The compiler-side details of fya → code generation — 5-1;
- The schema bridge from parameter declarations to their validation model;
- The passing mention of how watch interacts with resolved values —
  covered in 4-7.

## Main example

`demo_parsable.py` is fully offline; the complete recorded console output is
included below:

```console
$ uv run python demo_parsable.py
== ① Five-form detection (type auto-inferred, no evaluation at construction)==
   RAW       type=RAW         → 'raw text with {{ braces }}'
   FILE_REF  type=FILE_REF    → 'Refund policy: full refund within seven days, no questions asked; past that window, manual review is required.'
   EXPRESSION type=EXPRESSION  → 42
   TEMPLATE  type=TEMPLATE    → 'Hello Alice'
   LITERAL   type=LITERAL     → 'plain text'
== ② Render context (instance attributes flattened + env / config / self entries)==
   {{ env.DEMO_VAR }}    → 'from an environment variable'
   {{ config.agent.timeout }} → 60
   {{ self.node_id }}  → 'agent-main'
== ③ {{ x.resolved }}: referencing another Parsable's rendered result inside a template ==
   'Policy: Refund policy: full refund within seven days, no questions asked; past that window, manual review is required.'
   Contrast (without .resolved, only the template source shows): 'Policy: $./refund.md'
== ④ Reserved-name conflict → ReservedAttributeError ==
   Reserved attribute name occupied: 'env'
== ⑤ Unbound + miss ==
   unbound resolve() without context → MissingContextError
   inject miss → MissingProvideError
== ⑥ PENDING position semantics: field-position _ = a promise that must be fulfilled ==
   description: _ unfulfilled → MissingFieldError: Required field 'description' of agent type '@/pending-agent.fya' is still PENDING
```

### Complete runnable materials

Save each block under its displayed filename. The program writes its refund
policy input and pending-field fixture itself, so no additional data file is
needed. Set `DEEPSEEK_API_KEY` only if your provider configuration requires it;
this offline demonstration makes no provider call.

`main.py`:

```python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

`root.fya`:

```yaml
description: "Parsable demo assistant: minimal Q&A."
model_tag: default
---
$system_prompt:
You are a concise English assistant.
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

`demo_parsable.py`:

```python
import asyncio
import os
from pathlib import Path

from flowing import Parsable, launch
from flowing.errors import (MissingContextError, MissingFieldError,
                            MissingProvideError, ReservedAttributeError)


REFUND_POLICY = (
    "Refund policy: full refund within seven days, no questions asked; "
    "past that window, manual review is required."
)


async def main() -> None:
    Path("refund.md").write_text(REFUND_POLICY, encoding="utf-8")
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    agent.user_name = "Alice"

    print("== ① Five-form detection (type auto-inferred, no evaluation at construction)==")
    for label, parsable in [
        ("RAW      ", Parsable('$"raw text with {{ braces }}"')),
        ("FILE_REF ", Parsable("$./refund.md")),
        ("EXPRESSION", Parsable("{{ 40 + 2 }}")),
        ("TEMPLATE ", Parsable("Hello {{ user_name }}")),
        ("LITERAL  ", Parsable("plain text")),
    ]:
        print(f"   {label} type={parsable.type:<11} → {parsable.resolve(agent)!r}")

    print("== ② Render context (instance attributes flattened + env / config / self entries)==")
    os.environ["DEMO_VAR"] = "from an environment variable"
    print(f"   {{{{ env.DEMO_VAR }}}}    → "
          f"{Parsable('{{ env.DEMO_VAR }}').resolve(agent)!r}")
    print(f"   {{{{ config.agent.timeout }}}} → "
          f"{Parsable('{{ config.agent.timeout }}').resolve(agent)!r}")
    print(f"   {{{{ self.node_id }}}}  → "
          f"{Parsable('{{ self.node_id }}').resolve(agent)!r}")

    print("== ③ {{ x.resolved }}: referencing another Parsable's rendered result inside a template ==")
    agent.refund_policy = Parsable("$./refund.md").bind(agent)
    rendered = Parsable("Policy: {{ refund_policy.resolved }}").resolve(agent)
    print(f"   {rendered!r}")
    raw = Parsable("Policy: {{ refund_policy }}").resolve(agent)
    print(f"   Contrast (without .resolved, only the template source shows): {raw!r}")

    print("== ④ Reserved-name conflict → ReservedAttributeError ==")
    agent.env = "occupies a reserved name"
    try:
        Parsable("{{ env.DEMO_VAR }}").resolve(agent)
    except ReservedAttributeError as exc:
        print(f"   {exc}")

    print("== ⑤ Unbound + miss ==")
    try:
        Parsable("{{ anything }}").resolve()
    except MissingContextError:
        print("   unbound resolve() without context → MissingContextError")
    try:
        agent.inject("no_such_key")
    except MissingProvideError:
        print("   inject miss → MissingProvideError")

    print("== ⑥ PENDING position semantics: field-position _ = a promise that must be fulfilled ==")
    Path("pending-agent.fya").write_text(
        "description: _\nmodel_tag: default\n---\n$system_prompt:\nplaceholder\n",
        encoding="utf-8")
    try:
        await runtime.create_agent("@/pending-agent.fya")
    except MissingFieldError as exc:
        print(f"   description: _ unfulfilled → MissingFieldError: {exc}")
    await runtime.shutdown()


asyncio.run(main())
```

Run `uv run python demo_parsable.py`. The six input cases, the refund-policy
text, the environment value, and the invalid PENDING fixture are all defined
in this program; the complete recorded output appears above.

## Summary

1. The five forms are auto-detected; EXPRESSION returns the native type,
   and RAW skips all rendering;
2. Lazy evaluation is the prerequisite for correctness; the render context
   = instance attributes + env/config/self;
3. `{{ x.resolved }}` evaluates explicitly; there is no implicit recursion;
4. PENDING dispatches by position: the field position must be fulfilled,
   the override position is an empty patch, and list items are forbidden.
