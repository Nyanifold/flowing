"""Demo of the three equivalent definition channels (2-3, offline): handwritten subclass / @flowing_tool / callable pointer.

Run: uv run python demo_channels.py
"""
import asyncio
import pathlib

from flowing.tool.registry import ToolRegistry


async def main() -> None:
    reg = ToolRegistry(project_root=pathlib.Path.cwd())
    root = pathlib.Path.cwd()
    # Channel A: handwritten ScriptTool subclass (tools/todo.py, referenced by explicit file path)
    a = reg.get("./tools/todo.py", source_dir=root)
    # Channel C: .fya callable pointer (tools/todo-fn/TOOL.fya points at an independent implementation file)
    c = reg.get("./tools/todo-fn", source_dir=root)
    # Channel B: @flowing_tool-marked function (tools/shout.py, promoted to a subclass on get)
    b = reg.get("./tools/shout.py", source_dir=root)
    print(f"Channel A handwritten subclass  : {type(a).__name__} name={a.definition.name!r}")
    print(f"Channel C callable pointer      : {type(c).__name__} name={c.definition.name!r}")
    print(f"Channel B @flowing_tool function: {type(b).__name__} name={b.definition.name!r}")
    print(f"A and C are independent implementations with isomorphic declarations: {type(a) is not type(c)}")
    print(f"  same params schema: "
          f"{sorted(a.definition.params_schema) == sorted(c.definition.params_schema)}")
    for label, t in (("A", a), ("B", b), ("C", c)):
        print(f"  {label}: params={sorted(t.definition.params_schema)}")


asyncio.run(main())
