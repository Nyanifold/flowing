"""Parsable evaluation system demo (4-9, offline): the five forms / PENDING position semantics / render context / resolved.

Run: uv run python demo_parsable.py
"""
import asyncio
import pathlib

from flowing import Parsable, launch
from flowing.errors import (MissingContextError, MissingFieldError,
                            MissingProvideError, ReservedAttributeError)


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    agent.user_name = "Alice"

    print("== ① Five-form detection (type auto-inferred, no evaluation at construction)==")
    for label, p in [
        ("RAW      ", Parsable('$"raw text with {{ braces }}"')),
        ("FILE_REF ", Parsable("$./notes/refund.md")),
        ("EXPRESSION", Parsable("{{ 40 + 2 }}")),
        ("TEMPLATE ", Parsable("Hello {{ user_name }}")),
        ("LITERAL  ", Parsable("plain text")),
    ]:
        print(f"   {label} type={p.type:<11} → {p.resolve(agent)!r}")

    print("== ② Render context (instance attributes flattened + env / config / self entries)==")
    import os
    os.environ["DEMO_VAR"] = "from an environment variable"
    print(f"   {{{{ env.DEMO_VAR }}}}    → {Parsable('{{ env.DEMO_VAR }}').resolve(agent)!r}")
    print(f"   {{{{ config.agent.timeout }}}} → {Parsable('{{ config.agent.timeout }}').resolve(agent)!r}")
    print(f"   {{{{ self.node_id }}}}  → {Parsable('{{ self.node_id }}').resolve(agent)!r}")

    print("== ③ {{ x.resolved }}: referencing another Parsable's rendered result inside a template ==")
    agent.refund_policy = Parsable("$./notes/refund.md").bind(agent)  # explicit bind (class-attribute form auto-binds via the descriptor protocol)
    out = Parsable("Policy: {{ refund_policy.resolved }}").resolve(agent)
    print(f"   {out!r}")
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
    pathlib.Path("notes/pending-agent.fya").write_text(
        "description: _\nmodel_tag: default\n---\n$system_prompt:\nplaceholder\n",
        encoding="utf-8")
    try:
        await runtime.create_agent("@/notes/pending-agent.fya")
    except MissingFieldError as exc:
        print(f"   description: _ unfulfilled → MissingFieldError: {exc}")

    await runtime.shutdown()


asyncio.run(main())
