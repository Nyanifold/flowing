"""工具执行机制演示（4-5）：后台三形态之一 async generator 的全链路。

编程式调用 build（等价于回合内 LLM 调用），观察：
  收据（pending）→ 分段（segment）→ 完成摘要；产物逐份经 on_tool_yields；
  后台结果以 EVENT 消息入队（触发后续回合）。
运行：uv run python demo_background.py
"""
import asyncio

from flowing import launch
from flowing.message import MessageKind
from flowing.tool import ToolCall


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    yields_log: list[tuple] = []

    async def watch(agent_, result):
        yields_log.append((result.production, result.name,
                           type(result.output).__name__))
        return result
    agent.hooks.on_tool_yields(watch, by="diag")

    receipt = await agent.tool_call(ToolCall(id="call-b1", name="build",
                                             args={"target": "demo"}))
    print(f"① 首 yield → 收据: status={receipt.status} "
          f"background_task_id={receipt.background_task_id}")
    print(f"   回合不被阻塞：tool_call 已返回，后台继续跑")

    # 等后台产物全部送达（EVENT 消息入队并被消费）
    for _ in range(100):
        await asyncio.sleep(0.3)
        done = [y for y in yields_log if y[0] == "segment"]
        if len(done) >= 4:
            break
    print("② on_tool_yields 逐份产物（production 元信息）：")
    for production, name, kind in yields_log:
        print(f"   {production:<9} name={name:<6} output={kind}")

    # 等 EVENT 结果消息进树（后台投递触发的新回合）
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.5)
    events = [m for m in agent._messages.values()
              if m.kind is MessageKind.EVENT]
    print(f"③ 后台结果投递: EVENT 消息 {len(events)} 条进树")
    for m in events:
        head = next((b.text[:30] for b in m.content
                     if getattr(b, "text", "")), "")
        print(f"   source={m.source!r} {head}")
    await runtime.shutdown()


asyncio.run(main())
