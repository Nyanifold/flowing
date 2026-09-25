"""消息模型演示（1-2）：构造几种 Message，打印结构，验证配对锚。

运行：uv run python demo_messages.py
"""
import base64

from flowing.message import (
    ImageBlock,
    Message,
    MessageKind,
    MessagePriority,
    StructBlock,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
)


def show(m: Message, note: str) -> None:
    print(f"── {note} ──")
    print(f"kind={m.kind.value}  source={m.source!r}  tags={m.tags}  "
          f"priority={m.priority.name}")
    for i, b in enumerate(m.content):
        extra = ""
        if isinstance(b, TextBlock):
            extra = f"text={b.text!r}"
        elif isinstance(b, ThinkingBlock):
            extra = f"thinking={b.thinking!r} signature={b.signature!r}"
        elif isinstance(b, ToolCallBlock):
            extra = f"id={b.id!r} name={b.name!r} args={b.args}"
        elif isinstance(b, StructBlock):
            extra = f"data={b.data}"
        elif isinstance(b, ImageBlock):
            extra = (f"name={b.name!r} mime_type={b.mime_type} "
                     f"data(base64 前 24 字符)={b.data[:24]}…")
        print(f"  content[{i}] {b.type:<9} {extra}")
    if m.kind is MessageKind.TOOL:
        print(f"tool_call_id={m.tool_call_id}  tool_status={m.tool_status}")
    print()


# ① 用户文本消息（进队列的来源之一）
user = Message(kind=MessageKind.USER,
               content=[TextBlock(text="帮我查订单 4521")],
               source="chat_input", tags=["order"])
show(user, "① 用户消息：USER")

# ② PROVIDER 消息：思考块 / 文本块 / 工具调用块交错
provider = Message(kind=MessageKind.PROVIDER, content=[
    ThinkingBlock(thinking="用户要查订单，先调查询工具。", signature="sig-abc"),
    TextBlock(text="我来查询订单。"),
    ToolCallBlock(id="call_9", name="query-order", args={"order_id": "4521"}),
])
show(provider, "② 模型响应：PROVIDER（多模态块交错）")

# ③ TOOL 消息：与 ToolCallBlock.id 严格 1:1 配对
tool = Message(kind=MessageKind.TOOL,
               content=[StructBlock(data={"status": "shipped", "eta": "明天"})],
               tool_call_id="call_9", tool_status="completed")
show(tool, "③ 工具结果：TOOL（配对锚 + 结构块）")

# ④ EVENT 消息：异步工具最终结果以 EVENT 入队（标注块 + 结果块）
event = Message(kind=MessageKind.EVENT, source="tool_result",
                priority=MessagePriority.NORMAL, content=[
                    TextBlock(text="[后台任务 build #7 完成]"),
                    StructBlock(data={"exit_code": 0}),
                ])
show(event, "④ 事件消息：EVENT（source 说明来路）")

# ⑤ 媒体块：一律 base64 内联，data 是权威表示
png_1px = base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000"
                  "01f15c4890000000d49444154789c626001000000ffff030000060005"
                  "57bfabd40000000049454e44ae426082")).decode()
img = Message(kind=MessageKind.USER, content=[
    TextBlock(text="这张图里是什么？"),
    ImageBlock(data=png_1px, mime_type="image/png", name="像素.png"),
])
show(img, "⑤ 用户多模态消息：媒体块 base64 内联")

# ⑥ 配对锚双向强制：孤儿结果消灭在构造点
print("── ⑥ 配对锚强制 ──")
try:
    Message(kind=MessageKind.TOOL, content=[TextBlock(text="孤儿结果")])
except ValueError as exc:
    print(f"构造孤儿 TOOL 消息 → ValueError: {exc}")
try:
    Message(kind=MessageKind.USER, content=[TextBlock(text="带配对字段的用户消息")],
            tool_call_id="call_x", tool_status="completed")
except ValueError as exc:
    print(f"非 TOOL 消息携带配对字段 → ValueError: {exc}")
