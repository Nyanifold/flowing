"""Composable 机制演示（5-3）：自写 use_rate_limit 全链路 + 内置 Composable 对照。

运行：uv run python demo_composables.py
"""
import asyncio
import sys

from flowing import launch
from flowing.tool import ToolCall

sys.path.insert(0, "composables")
from rate_limit import remove_rate_limit, use_rate_limit


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ① 注入自写 Composable：窗口 60s、上限 3 次
    print("== ① use_rate_limit(max_calls=3) 注入 ==")
    limited: list[str] = []
    use_rate_limit(agent, max_calls=3, window_seconds=60.0)

    async def observe(agent_, tool_call):
        limited.append(tool_call.name)
        return tool_call
    agent.hooks.on_rate_limited(observe, by="demo")   # 观测声明的扩展钩子点

    results = []
    for i in range(5):   # 连发 5 次：前 3 放行、后 2 阻断
        r = await agent.tool_call(ToolCall(id=f"c{i}", name="echo",
                                           args={"text": f"第{i}发"}))
        results.append(r.status)
    print(f"   5 次调用结果: {results}")
    print(f"   on_rate_limited 观测到 {len(limited)} 次超限（第 4、5 发）")

    # ② by 归组管理：整组移除后恢复放行
    print("== ② remove_by_owner 整组移除 ==")
    removed = remove_rate_limit(agent)
    r = await agent.tool_call(ToolCall(id="c9", name="echo", args={"text": "恢复"}))
    print(f"   移除 {removed} 条 handler；再调一次: status={r.status}")

    # ③ 调用内置 Composable（use_system_reminder 一行注入）
    print("== ③ 调用内置 Composable ==")
    from flowing.composables import use_system_reminder
    use_system_reminder(agent, contents=["[提醒] 来自 use_system_reminder"])
    hits = [h for h in agent.hooks.before_turn
            if getattr(h, "by", None) == "system-reminder"]
    print(f"   use_system_reminder 注入后 before_turn 命中 {len(hits)} 条 handler"
          f"（这是该内置 Composable 的一种挂载方式）")
    await runtime.shutdown()


asyncio.run(main())
