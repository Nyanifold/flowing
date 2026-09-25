"""Message model demo (1-2): construct several Message objects, print their
structure, and verify the pairing anchor.

Run: uv run python demo_messages.py
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
                     f"data(base64 first 24 chars)={b.data[:24]}…")
        print(f"  content[{i}] {b.type:<9} {extra}")
    if m.kind is MessageKind.TOOL:
        print(f"tool_call_id={m.tool_call_id}  tool_status={m.tool_status}")
    print()


# ① User text message (one source of queued input)
user = Message(kind=MessageKind.USER,
               content=[TextBlock(text="Look up order 4521 for me")],
               source="chat_input", tags=["order"])
show(user, "① User message: USER")

# ② PROVIDER message: reasoning / text / tool-call blocks interleaved
provider = Message(kind=MessageKind.PROVIDER, content=[
    ThinkingBlock(thinking="The user wants to check an order; query the order tool first.",
                  signature="sig-abc"),
    TextBlock(text="I'll look up the order."),
    ToolCallBlock(id="call_9", name="query-order", args={"order_id": "4521"}),
])
show(provider, "② Model response: PROVIDER (interleaved multimodal blocks)")

# ③ TOOL message: strictly 1:1 paired with ToolCallBlock.id
tool = Message(kind=MessageKind.TOOL,
               content=[StructBlock(data={"status": "shipped", "eta": "tomorrow"})],
               tool_call_id="call_9", tool_status="completed")
show(tool, "③ Tool result: TOOL (pairing anchor + struct block)")

# ④ EVENT message: async tool results enter the queue as EVENT
#    (note block + result block)
event = Message(kind=MessageKind.EVENT, source="tool_result",
                priority=MessagePriority.NORMAL, content=[
                    TextBlock(text="[background task build #7 finished]"),
                    StructBlock(data={"exit_code": 0}),
                ])
show(event, "④ Event message: EVENT (source states the origin)")

# ⑤ Media blocks: always inlined as base64; data is the authoritative form
png_1px = base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000"
                  "01f15c4890000000d49444154789c626001000000ffff030000060005"
                  "57bfabd40000000049454e44ae426082")).decode()
img = Message(kind=MessageKind.USER, content=[
    TextBlock(text="What is in this image?"),
    ImageBlock(data=png_1px, mime_type="image/png", name="pixel.png"),
])
show(img, "⑤ User multimodal message: media block inlined as base64")

# ⑥ Pairing-anchor enforcement in both directions: orphan results die at
#    the construction point
print("── ⑥ Pairing-anchor enforcement ──")
try:
    Message(kind=MessageKind.TOOL, content=[TextBlock(text="orphan result")])
except ValueError as exc:
    print(f"constructing an orphan TOOL message → ValueError: {exc}")
try:
    Message(kind=MessageKind.USER,
            content=[TextBlock(text="user message carrying pairing fields")],
            tool_call_id="call_x", tool_status="completed")
except ValueError as exc:
    print(f"non-TOOL message carrying pairing fields → ValueError: {exc}")
