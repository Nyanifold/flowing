"""恢复不变量演示（4-3，离线）：孤立 tool_call 的 synthetic 占位合成。

手工构造一棵“撕裂”的树（provider 带 tool_call 但结果缺失），
经恢复管线重放后，配对锚由合成占位封闭。
运行：uv run python demo_synthetic.py
"""
import asyncio
import json
import pathlib

from flowing import launch
from flowing.message import (Message, MessageKind, TextBlock, ToolCallBlock,
                             to_record)

SESSION = pathlib.Path(".flowing/agent-main")


def build_torn_session() -> None:
    """写一份撕裂的 session：user + 带孤儿 tool_call 的 provider。"""
    SESSION.mkdir(parents=True, exist_ok=True)
    (SESSION / "meta.json").write_text(json.dumps({
        "agent_type": "@/root.fya",
        "parent_agent_id": "runtime-0",
        "created_at": "2026-09-18T00:00:00+00:00",
        "args": {},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    user = Message(kind=MessageKind.USER, content=[TextBlock(text="查一下目录")])
    user.id, user.parent_id = "1", None
    orphan = Message(
        kind=MessageKind.PROVIDER,
        content=[ToolCallBlock(id="call_7", name="bash", args={"command": "ls"})])
    orphan.id, orphan.parent_id = "2", "1"
    orphan.turn_end = True   # 回合已收口但结果缺失 = 撕裂形态
    rows = [{"type": "meta", "format_version": 1},
            to_record(user), to_record(orphan)]
    (SESSION / "tree.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")


async def main() -> None:
    # ① 先正常挂载一次：名录（core.jsonl）与 meta 就位——等价于该 Agent
    #    曾经真实存在过（手工造名录属内部格式，不走这条路）
    runtime = await launch(".")
    await runtime.shutdown()
    # ② 用撕裂的 tree.jsonl 覆盖：provider(id=2) 带孤儿 tool_call、无结果消息
    build_torn_session()
    print("已构造撕裂 session：provider(id=2) 带孤儿 tool_call(call_7)，无结果消息。")
    # ③ 恢复管线重放
    runtime = await launch(".", resume="agent-main")
    agent = await runtime.get_agent("agent-main")
    print("恢复后的全部消息（id 升序）：")
    for mid in sorted(agent._messages, key=lambda k: int(k) if k.isdigit() else -1):
        x = agent._messages[mid]
        head = next((b.text[:24] for b in x.content if getattr(b, "text", "")), "")
        print(f"{x.id:>18} {x.kind.value:<9} synthetic={x.synthetic} "
              f"tool_status={x.tool_status} {head}")
    await runtime.shutdown()


asyncio.run(main())
