""".fya complex assembly demo (5-1, mostly offline): compilation equivalence / glob expansion / deep blocks / failure assertions.

Run: uv run python demo_fya.py
"""
import asyncio
import pathlib

from flowing import launch
from flowing.errors import NameMismatchError, UnknownHookPointError


async def main() -> None:
    import shutil
    shutil.rmtree("tools/__pycache__", ignore_errors=True)   # keep the glob material directory clean
    runtime = await launch(".", user_id="u-7")
    agent = await runtime.get_agent("agent-main")

    print("== ① compiled product equals handwritten subclass (item by item) ==")
    import sys
    sys.path.insert(0, str(pathlib.Path("notes").resolve()))
    from handwritten import RootAgent
    desc_a = getattr(agent.description, "source", agent.description)
    desc_b = getattr(RootAgent.description, "source", RootAgent.description)
    print(f"   description equal: {desc_a == desc_b}")
    print(f"   system_prompt template equal: "
          f"{agent.system_prompt.source.strip() == RootAgent.system_prompt.source.strip()}"
          f" (block content carries a trailing newline; equal after strip)")
    fya_props = agent.args_model.model_json_schema()["properties"]
    py_props = RootAgent.args_model.model_json_schema()["properties"]
    print(f"   args schema equal (properties key set): "
          f"{sorted(fya_props) == sorted(py_props)}")

    print("== ② glob expansion and alias entry ==")
    print(f"   tools (glob expansion): {sorted(agent._tool_entries)}")
    greet = agent._subagent_entries["greet"]
    print(f"   subagent alias greet: name_ori={greet.name_ori!r} "
          f"specified={ {k: v.source for k, v in greet.specified.items()} }")
    view = greet.catalog_view(agent)
    print(f"   catalog view: name={view['name']!r} "
          f"(tone removed from the parameter table by specified: no tone in '<params>')")

    print("== ③ failure assertions (negative cases) ==")
    pathlib.Path("notes/bad-name.fya").write_text(
        """name: wrong-name
model_tag: default
---
$system_prompt:
placeholder
""", encoding="utf-8")
    try:
        runtime.get_agent_class("@/notes/bad-name.fya")
    except NameMismatchError as exc:
        print(f"   name consistency assertion: NameMismatchError")
    pathlib.Path("notes/bad-hook.fya").write_text(
        """---
$system_prompt:
placeholder
---
$script:
from flowing import on

@on('no_such_hook')
def _(self, turn):
    return turn

async def setup(self):
    pass
""", encoding="utf-8")
    try:
        await runtime.create_agent("@/notes/bad-hook.fya")
    except UnknownHookPointError as exc:
        print(f"   @on undeclared hook point: UnknownHookPointError: {exc}")
    await runtime.shutdown()


asyncio.run(main())
