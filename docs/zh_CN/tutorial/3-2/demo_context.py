"""上下文组装机制演示（3-2，离线）：Context 三字段 / Parsable 常用形式 / 钩子改写。

运行：uv run python demo_context.py
"""
import asyncio

from flowing import Parsable, launch
from flowing.context import PromptSegment


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ① Context 三正交字段
    ctx = agent._assemble_context()
    print("① Context 三正交字段：")
    print(f"  system_prompt: {[ (s.name, s.cache) for s in ctx.system_prompt ]}")
    print(f"  tools: {[ t.name for t in ctx.tools ]}")
    print(f"  messages: {len(ctx.messages)} 条（根 → head 路径）")

    # ② Parsable 三种常用形式 + 现场求值
    print("② Parsable 常用形式（字面量 / 模板 / 文件引用）：")
    print(f"  字面量   : {Parsable('你好').resolve(agent)!r}")
    print(f"  模板     : {Parsable('当前模式：{{ current_mode }}').resolve(agent)!r}")
    print(f"  文件引用 : {Parsable('$./notes/motto.md').resolve(agent)!r}")
    agent.current_mode = "实验模式"   # 改实例属性…
    print(f"  现场求值 : 改 current_mode 后同一模板 → "
          f"{Parsable('当前模式：{{ current_mode }}').resolve(agent)!r}（取最新值）")

    # ③ before_provider_gen 整体改写 Context（示例：追加一段标记）
    async def _mark(agent_, context):
        context.system_prompt.append(
            PromptSegment(content="[改写标记]", cache="dynamic", name="marker"))
        return context
    agent.hooks.before_provider_gen(_mark, by="demo")
    rewritten = await agent.hooks.before_provider_gen.dispatch(
        agent, agent._assemble_context())
    print("③ before_provider_gen 改写 Context：")
    print(f"  改写后最后一段: {rewritten.system_prompt[-1].name} = "
          f"{rewritten.system_prompt[-1].content!r}")
    print(f"  [0] 核心引用块仍在: {rewritten.system_prompt[0].name}")
    await runtime.shutdown()


asyncio.run(main())
