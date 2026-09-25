"""Context assembly mechanics demo (3-2, offline): Context's three fields /
common Parsable forms / hook rewriting.

Run: uv run python demo_context.py
"""
import asyncio

from flowing import Parsable, launch
from flowing.context import PromptSegment


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ① Context: three orthogonal fields
    ctx = agent._assemble_context()
    print("① Context: three orthogonal fields:")
    print(f"  system_prompt: {[ (s.name, s.cache) for s in ctx.system_prompt ]}")
    print(f"  tools: {[ t.name for t in ctx.tools ]}")
    print(f"  messages: {len(ctx.messages)} (root → head path)")

    # ② Three common Parsable forms + live evaluation
    print("② Common Parsable forms (literal / template / file reference):")
    print(f"  literal        : {Parsable('hello').resolve(agent)!r}")
    print(f"  template       : {Parsable('Current mode: {{ current_mode }}').resolve(agent)!r}")
    print(f"  file reference : {Parsable('$./notes/motto.md').resolve(agent)!r}")
    agent.current_mode = "experiment mode"   # change the instance attribute…
    print(f"  live evaluation: same template after changing current_mode → "
          f"{Parsable('Current mode: {{ current_mode }}').resolve(agent)!r} (latest value)")

    # ③ before_provider_gen rewriting the whole Context (example: append a marker segment)
    async def _mark(agent_, context):
        context.system_prompt.append(
            PromptSegment(content="[rewrite marker]", cache="dynamic", name="marker"))
        return context
    agent.hooks.before_provider_gen(_mark, by="demo")
    rewritten = await agent.hooks.before_provider_gen.dispatch(
        agent, agent._assemble_context())
    print("③ before_provider_gen rewriting Context:")
    print(f"  last segment after rewrite: {rewritten.system_prompt[-1].name} = "
          f"{rewritten.system_prompt[-1].content!r}")
    print(f"  [0] core reference block still present: {rewritten.system_prompt[0].name}")
    await runtime.shutdown()


asyncio.run(main())
