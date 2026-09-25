"""树手术演示（4-3，离线）：fork 分支探索 + 五 op 历史修正。

消息全部手工构造（不经 LLM），手术痕迹经 walk 回放呈现。
运行：uv run python demo_surgery.py
"""
import asyncio

from flowing import launch
from flowing.message import Message, MessageKind, TextBlock


def m(kind: MessageKind, text: str) -> Message:
    return Message(kind=kind, content=[TextBlock(text=text)])


def show(agent, label: str) -> None:
    print(f"── {label}（head → 根，逆时间序）──")
    for x in agent.chain.walk(agent.current_head_id):
        head = next((b.text[:26] for b in x.content if getattr(b, "text", "")), "")
        print(f"{x.id:>3} {x.kind.value:<9} parent={x.parent_id} {head}")
    print()


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    async def grow(parent, message):
        """branch 挂树 + fork 前移游标（chain op 不自动移动 head）。"""
        new_id = agent.chain.branch(parent, message)
        await agent.fork(new_id)
        return new_id

    # 造历史：1 user → 2 provider → 3 user → 4 provider（线性链）
    a = await grow(None, m(MessageKind.USER, "路线 A 怎么走？"))
    b = await grow(a, m(MessageKind.PROVIDER, "先沿北路探一段。"))
    c = await grow(b, m(MessageKind.USER, "北路被堵了。"))
    await grow(c, m(MessageKind.PROVIDER, "改走南路。"))
    show(agent, "初始线性链")

    # fork = 切游标（不建节点）：回到公共节点 a，开平行探索分支
    await agent.fork(a)
    e = await grow(a, m(MessageKind.USER, "如果直接走南路呢？"))
    await grow(e, m(MessageKind.PROVIDER, "南路更近，但有积水。"))
    show(agent, "fork 出平行分支后（head 在南路探索上；北路链完整保留）")

    # 回到公共根视角
    await agent.fork(a)
    show(agent, "切回 a：insert 前的分叉点")

    # insert 的合并语义：在分叉点 a 下插入——a 的既有直接子（b 与 e）重挂到新节点下
    g = agent.chain.insert(a, m(MessageKind.SYSTEM, "背景：今日有雨。"))
    print(f"insert 后：g={g}，b.parent={agent.chain.get(b).parent_id}，"
          f"e.parent={agent.chain.get(e).parent_id}（都重挂到 g 下 = 合并语义）")
    show(agent, "insert 后的分叉点结构")

    # update：只改内容不动链
    agent.chain.update(e, [TextBlock(text="如果直接走南路呢？（补充：带伞）")])
    print(f"update 后 e 的文本：{agent.chain.get(e).content[0].text!r}")

    # remove 的邻接保持：给 d（id 4）下加个子再删 d——直接子重挂到 d 的亲节点
    d = agent.chain.get("4")
    child_of_d = agent.chain.branch("4", m(MessageKind.USER, "南路口碑如何？"))
    agent.chain.remove("4")
    print(f"remove(d) 后：d 已消失（chain.get 抛 KeyError），"
          f"其子 parent={agent.chain.get(child_of_d).parent_id}（重挂到 d 的亲节点 c）")
    await runtime.shutdown()


asyncio.run(main())
