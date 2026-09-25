"""query / message / steer 三入口行为对照。

运行：uv run python demo_entries.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ── 1. message()：fire-and-forget，只拿回消息 id，不等待回合 ──
    mid = await agent.message(
        "请用 glob 查看 notes 目录（用系统提示里的绝对路径），然后说明里面有几个文件。")
    print(f"message() 已入队，消息 id={mid}（调用方不等回合结果）")
    while agent.current_turn is None:   # 等回合开始
        await asyncio.sleep(0.2)
    print("回合已开始（current_turn 非 None）")

    # ── 2. steer()：回合进行中导向——STEER 当轮上下文可见、不打断 ──
    await agent.steer("补充要求：回答末尾请附上『（收到导向）』。")
    print("steer() 已投递（STEER 优先级：当轮可见、不打断）")
    while agent.current_turn is not None:   # 等本回合收尾
        await asyncio.sleep(0.2)
    print("回合已收尾")

    # ── 3. query()：等待「包含我这条消息」的回合产物 TurnResult ──
    result = await agent.query("再次用 glob 确认 notes 目录的文件数量，一句话回答。")
    print(f"query() 等到 TurnResult：status={result.status}")
    print(f"final_text 前 80 字：{result.final_text[:80]}")

    # ── 4. 树上游标回放：看三入口各自在树上留下的痕迹 ──
    print("── 消息树（head → 根，逆时间序；pri=STEER 即导向消息）──")
    for m in agent.chain.walk(agent.current_head_id):
        head = ""
        for b in m.content:
            if getattr(b, "text", ""):
                t = b.text.replace("\n", " ")
                head = t if len(t) <= 62 else f"{t[:36]}…{t[-22:]}"
                break
        print(f"{m.id:>3} {m.kind.value:<9} pri={m.priority.name:<6} "
              f"turn_end={m.turn_end} {head}")

    await runtime.shutdown()


asyncio.run(main())
