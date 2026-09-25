"""嵌入宿主的最小骨架（6-2）：宿主持有 Runtime + 钩子订阅 + 审批 + shutdown 责任。

不引入 Web 框架——FastAPI / WebSocket 形态只是把这里的钩子接到你的协议上。
运行：uv run python demo_embed.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    # 嵌入 = 宿主持有 launch 返回的 Runtime（薄 main() 可不含交互逻辑）
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ① 配置对接：set_config 覆盖层最优先（配置链见 6-3）
    runtime.set_config("agent.timeout", 90)
    print(f"① 宿主 set_config 覆盖: agent.timeout = {runtime.get_config('agent.timeout')}")

    # ② 输出走钩子订阅（流式上屏的最小实现）
    async def on_delta(agent_, d):
        if d.kind == "text" and d.by == "_turn":
            print(d.text, end="", flush=True)
        return d
    agent.hooks.on_provider_delta["_turn"](on_delta, by="host-ui")

    # ③ 审批经 before_tool_call（宿主的确认策略，可接任意人工通道）
    async def approve(agent_, tool_call):
        print(f"\n[宿主审批] {tool_call.name} args={tool_call.args}")
        await asyncio.sleep(0.2)          # 模拟人工确认的耗时
        print("[宿主审批] 通过")
        return tool_call
    agent.hooks.before_tool_call(approve, by="host-approval")

    # ④ 宿主驱动对话
    r = await agent.query("用 bash 运行 echo 宿主你好，把输出原样告诉我。")
    print(f"\n[宿主] 回合 status={r.status}")

    # ⑤ shutdown 责任转移给宿主：write-behind 需要排空，不 shutdown 退进程丢尾部
    await runtime.shutdown()
    print("[宿主] shutdown 完成：尾部记录已排空")


asyncio.run(main())
