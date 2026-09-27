"""队列与循环机制实验：steer / INTERRUPT / pause-resume / cancel。

每个实验打印“机制行为 + 树上痕迹”。运行：uv run python demo_mechanics.py
"""
import asyncio

from flowing import launch
from flowing.message import Message, MessageKind, MessagePriority, TextBlock


def tree_lines(agent, limit=40):
    rows = []
    for m in agent.chain.walk(agent.current_head_id):
        head = ""
        for b in m.content:
            if getattr(b, "text", ""):
                t = b.text.replace("\n", " ")
                head = t[:30]
                break
        rows.append(f"{m.id:>3} {m.kind.value:<9} pri={m.priority.name:<7} "
                    f"turn_end={m.turn_end} {head}")
    return rows[:limit]


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ── 实验 1：steer 当轮吸收（不打断）──
    print("== 实验 1：steer（STEER 当轮可见、不打断）==")
    t = asyncio.create_task(agent.query("请数一数 1 到 5，每个数字一行。"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await agent.steer("补充：数完请附一句“（收到导向）”。")
    r = await t
    print(f"回合 status={r.status}（completed=未被打破）")
    await asyncio.sleep(0.3)   # 等 steer 消息随批次挂树

    # ── 实验 2：INTERRUPT 在工具执行期打断当前回合 ──
    print("== 实验 2：INTERRUPT（工具执行期打断当前回合）==")
    t = asyncio.create_task(agent.query(
        "先用 bash 运行 sleep 20，然后再回答：天空为什么是蓝色的？（一句话）"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(8.0)   # 首轮生成完毕、bash sleep 20 执行中
    await agent.enqueue_message(Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="停下！先回答：2+2 等于几？")],
        priority=MessagePriority.INTERRUPT))
    r = await t
    print(f"被打断回合 status={r.status} aborted={r.turn.aborted}（cancelled=被打断）")
    while agent.current_turn is not None:   # 等 INTERRUPT 新回合收尾
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)

    # ── 实验 3：pause / resume ──
    print("== 实验 3：pause / resume（挂起与恢复工作循环）==")
    agent.pause()
    t = asyncio.create_task(agent.query("用一句话介绍你自己。"))
    await asyncio.sleep(1.5)
    # pause 挂起工作循环检查点：批次已出队挂树，但 provider_gen 未发起
    print(f"pause 期间 current_turn 非 None: {agent.current_turn is not None}"
          f"（回合已创建、停在检查点，无 LLM 调用）")
    agent.resume()
    r = await t
    print(f"resume 后 status={r.status}")

    # ── 实验 4：cancel（协作式取消：在途生成被竞速中断、已产出保留）──
    print("== 实验 4：cancel（中止执行；流式中断已产出保留落盘）==")
    before = set(agent._messages)
    t = asyncio.create_task(agent.query("讲一个很长很长的故事。"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.0)
    await agent.cancel()
    r = await t
    new_nodes = [mid for mid in agent._messages if mid not in before]
    print(f"cancel 后 status={r.status}；本回合已落树节点数={len(new_nodes)}"
          f"（user + partial provider：中断不丢已产出）")

    # ── 树上游标回放：以上机制各自的痕迹 ──
    print("── 消息树（head → 根，逆时间序）──")
    for line in tree_lines(agent):
        print(line)
    await runtime.shutdown()


asyncio.run(main())
