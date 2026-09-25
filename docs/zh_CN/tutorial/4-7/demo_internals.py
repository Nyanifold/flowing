"""Agent 机制演示（4-7）：自定义 _dequeue 的 drain 合并 / watch 联动 / side_query 副线。

运行：uv run python demo_internals.py
"""
import asyncio
import types

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ── ① 覆写 _dequeue：drain 合并（一批消费队列里全部消息）──
    print("== ① 自定义 _dequeue：drain 合并 ==")
    original = agent._dequeue

    async def drain_all(self):
        await self._message_queue.wait_not_empty()
        batch = await self._message_queue.drain_all()
        print(f"   [_dequeue] drain 合并：本批 {len(batch)} 条消息进一个回合")
        return batch

    agent._dequeue = types.MethodType(drain_all, agent)
    for text in ["第一句", "第二句", "第三句"]:
        await agent.message(text)
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)
    # 注意：restore 不撤回「已在途的出队调用」——工作循环正阻塞在旧方法
    # 的 wait_not_empty 里；② 的那次出队仍走 drain 合并（本批 1 条）。

    # ── ② 回合收尾结构：TurnResult 的聚合口径 ──
    print("== ② 回合收尾：TurnResult 构建 ==")
    r = await agent.query("用一句话介绍你自己。")
    print(f"   status={r.status} turn.message_ids={r.turn.message_ids}")
    print(f"   token_usage 聚合={r.token_usage.total_tokens if r.token_usage else None} "
          f"（TurnContext.usages 逐字段求和）")
    agent._dequeue = original   # 还原默认批次语义（下一次出队起生效）

    # ── ③ watch：赋值事件联动（fire-and-forget）──
    print("== ③ watch 属性联动 ==")
    seen: list[tuple] = []
    agent.watch("locale", lambda new, old: seen.append((old, new)))
    agent.locale = "zh"      # 首次赋值（old=None）
    agent.locale = "en"      # 改写
    await asyncio.sleep(0.5)   # watcher 异步执行
    print(f"   watch(locale) 收到赋值事件: {seen}")

    # ── ④ side_query：副线不进树不落盘 ──
    print("== ④ side_query 副线 ==")
    before = len(agent._messages)
    side = await agent.side_query("只回复：ok")
    after = len(agent._messages)
    print(f"   side_query 返回={side!r}；树节点数 {before} → {after}（不变 = 副线零痕迹）")
    await runtime.shutdown()


asyncio.run(main())
