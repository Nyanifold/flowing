"""三条等价定义通道演示（2-3，离线）：手写子类 / @flowing_tool / callable 指针。

运行：uv run python demo_channels.py
"""
import asyncio
import pathlib

from flowing.tool.registry import ToolRegistry


async def main() -> None:
    reg = ToolRegistry(project_root=pathlib.Path.cwd())
    root = pathlib.Path.cwd()
    # 通道 A：手写 ScriptTool 子类（tools/todo.py，显式文件路径引用）
    a = reg.get("./tools/todo.py", source_dir=root)
    # 通道 C：.fya callable 指针（tools/todo-fn/TOOL.fya 指向独立实现文件）
    c = reg.get("./tools/todo-fn", source_dir=root)
    # 通道 B：@flowing_tool 打标函数（tools/shout.py，get 时提升为子类）
    b = reg.get("./tools/shout.py", source_dir=root)
    print(f"通道A 手写子类     : {type(a).__name__} name={a.definition.name!r}")
    print(f"通道C callable 指针: {type(c).__name__} name={c.definition.name!r}")
    print(f"通道B 打标函数     : {type(b).__name__} name={b.definition.name!r}")
    print(f"A 与 C 独立实现、同构声明: {type(a) is not type(c)}")
    print(f"  同参 schema: "
          f"{sorted(a.definition.params_schema) == sorted(c.definition.params_schema)}")
    for label, t in (("A", a), ("B", b), ("C", c)):
        print(f"  {label}: params={sorted(t.definition.params_schema)}")


asyncio.run(main())
