"""Provider 调用面演示（3-3，离线结构 + 真实模型）：流式 vs 非流式。

运行：uv run python demo_stream.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    deltas: list = []

    async def collect(agent_, d):
        deltas.append(d)
        usage = "usage✓" if d.usage else "-"
        print(f"  delta#{len(deltas):<3} kind={d.kind:<8} by={d.by:<6} "
              f"{usage:<6} text={d.text[:16]!r}")
        return d

    # 只收主回合 delta（match_on="by" 过滤；副线是 "_side"）
    agent.hooks.on_provider_delta["_turn"](collect, by="diag")

    print("== 流式 provider_gen(stream=True)：delta 逐段到达 ==")
    ctx = agent._assemble_context()
    resp = await agent.provider_gen(ctx, stream=True, by="_turn")
    print(f"  → 共 {len(deltas)} 条 delta；finish={resp.finish}；"
          f"message.usage 附着={'是' if resp.message.usage else '否'}")

    deltas.clear()
    print("== 非流式 provider_gen(stream=False)：合成一条全量 delta ==")
    resp = await agent.provider_gen(ctx, stream=False, by="_turn")
    print(f"  → 共 {len(deltas)} 条 delta；finish={resp.finish}；"
          f"message.usage 附着={'是' if resp.message.usage else '否'}")
    await runtime.shutdown()


asyncio.run(main())
