""".fya 复杂装配演示（5-1，离线为主）：编译等价性 / glob 展开 / 深层块 / 失败断言。

运行：uv run python demo_fya.py
"""
import asyncio
import pathlib

from flowing import launch
from flowing.errors import NameMismatchError, UnknownHookPointError


async def main() -> None:
    import shutil
    shutil.rmtree("tools/__pycache__", ignore_errors=True)   # glob 素材目录保持干净
    runtime = await launch(".", user_id="u-7")
    agent = await runtime.get_agent("agent-main")

    print("== ① 编译产物等价手写子类（逐项比对）==")
    import sys
    sys.path.insert(0, str(pathlib.Path("notes").resolve()))
    from handwritten import RootAgent
    desc_a = getattr(agent.description, "source", agent.description)
    desc_b = getattr(RootAgent.description, "source", RootAgent.description)
    print(f"   description 等价: {desc_a == desc_b}")
    print(f"   system_prompt 模板等价: "
          f"{agent.system_prompt.source.strip() == RootAgent.system_prompt.source.strip()}"
          f"（块内容带末尾换行，strip 后同）")
    fya_props = agent.args_model.model_json_schema()["properties"]
    py_props = RootAgent.args_model.model_json_schema()["properties"]
    print(f"   args schema 等价（properties 键集）: "
          f"{sorted(fya_props) == sorted(py_props)}")

    print("== ② glob 展开与别名条目 ==")
    print(f"   tools（glob 展开）: {sorted(agent._tool_entries)}")
    greet = agent._subagent_entries["greet"]
    print(f"   subagent 别名 greet: name_ori={greet.name_ori!r} "
          f"specified={ {k: v.source for k, v in greet.specified.items()} }")
    view = greet.catalog_view(agent)
    print(f"   catalog 视图: name={view['name']!r} "
          f"（tone 已被 specified 移出参数表：'<params>' 中无 tone）")

    print("== ③ 失败断言（负例）==")
    pathlib.Path("notes/bad-name.fya").write_text(
        """name: wrong-name
model_tag: default
---
$system_prompt:
占位
""", encoding="utf-8")
    try:
        runtime.get_agent_class("@/notes/bad-name.fya")
    except NameMismatchError as exc:
        print(f"   name 一致性断言: NameMismatchError")
    pathlib.Path("notes/bad-hook.fya").write_text(
        """---
$system_prompt:
占位
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
        print(f"   @on 未声明钩子点: UnknownHookPointError: {exc}")
    await runtime.shutdown()


asyncio.run(main())
