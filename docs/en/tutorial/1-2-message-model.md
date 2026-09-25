# 1-2 · The Message Model

## Prerequisites

Chapter [1-1 Turn and Loop](1-1-turn-and-loop.md) explains how messages drive turns.
This chapter introduces common message types and structure, and embeds a complete
offline script as `demo_messages.py`. The script constructs message objects and
prints them without starting a Runtime or calling a model.

## Glossary for this chapter

| Term | One-sentence definition |
|---|---|
| `Message` | A `Message` uniformly represents user input, model responses, tool results, external events, and subagent returns. |
| `MessageKind` | The seven-value `MessageKind` enum describes who sent a message rather than the role it plays. |
| ContentBlock | A ContentBlock is one fragment of message content, and blocks of different types can appear in an interleaved sequence. |
| Pairing anchor | The `ToolCallBlock.id` in a PROVIDER message must match the `tool_call_id` in its TOOL message, and `__post_init__` enforces this pairing in both directions during construction. |
| Parent chain | The `Message.parent_id` chain is the sole structural basis of the message-level tree, which chapter 3-1 explains. |
| Message-level tree | Each Agent holds a message-level tree, which is a forest built from message ids and parent chains; chapter 1-1 demonstrates tree replay. |

## Goals

Build the model surface of "everything is a message": chapter 1-1 demonstrates the
loop with two message types (text and tool calls), and this chapter completes
the seven-value `MessageKind`, the common ContentBlocks, and the pairing
anchor rule.

## Main text

### Message: field overview

The core `Message` fields used in this example are:

| Field | Key points |
|---|---|
| `kind` | The seven-value `MessageKind` is the only role discriminator at the object level, and **a message has no `role` attribute**. |
| `content` | The `content` field contains a list of ContentBlocks, and blocks of different types can be interleaved. |
| `id` / `parent_id` | These fields identify a message-level tree node and its parent chain, and `parent_id=None` marks a root. |
| `source` / `tags` | The deliverer fills these free-form secondary classifications and tags, which the framework does not enumerate. |
| `turn_end` / `partial` / `synthetic` | These fields mark turn boundaries, streaming interruptions, and recovery placeholders; chapter 4-3 explains their semantics. |

### The seven-value MessageKind: origin, not role

| kind | Origin and queue behavior |
|---|---|---|
| `USER` | A user sends this message, and it enters the queue. |
| `PROVIDER` | The model produces this response, including multimodal output, inside a turn, so it **never enters the queue**. |
| `TOOL` | A tool produces this execution result, which attaches directly to the tree inside a turn rather than entering the queue. |
| `SYSTEM` | The framework or a Composable injects this context through the queue or during assembly. |
| `PEER` | Another Agent intentionally sends this message, and it enters the queue. |
| `EVENT` | An external event or extension-delivered content, such as a cron job, an async tool's final result, or a Skill body; it enters the queue. |
| `SUBAGENT` | A subagent returns this result, and it enters the queue. |

The naming principle is "origin": `PROVIDER` is not called "assistant"
because it also covers multimodal outputs such as text-to-image and
speech. **A kind carries no behavioral meaning** — there is no shortcut
such as "a pure system message does not trigger the LLM"; the kind only
determines the adapter's presentation mapping.

### kind→role: the adapter's job

Each vendor API's role is mapped from the kind by the Provider adapter
(Anthropic routes `SYSTEM` into `user` wrapped in XML; OpenAI routes
`TOOL` into `tool`). The message layer is decoupled from any Provider API:
switching models does not require changing message code.

### Common ContentBlocks

- `TextBlock(text)` represents plain text.
- `ThinkingBlock(thinking, signature)` represents a model-provided reasoning summary; its signature belongs to the Anthropic family and must be carried back verbatim during multi-turn replay.
- `ToolCallBlock(id, name, args)` represents a tool call initiated by the LLM.
- `StructBlock(data)` stores a structure for programs, such as a dict or serialized dataclass, and the adapter dumps it as text for the LLM.
- Media blocks such as `ImageBlock` are always **inlined as base64**, and `data` is the authoritative representation.

### Pairing anchor: orphan results eliminated at construction

The `ToolCallBlock.id` in a PROVIDER message and the `tool_call_id` of the
subsequent TOOL message are strictly paired 1:1; `Message.__post_init__`
enforces this in both directions (`kind=TOOL ⟺ the pairing fields are not
None`), raising `ValueError` on violation — orphan results are eliminated
at the construction point and never flow into context.

## Out of scope

- This chapter does not cover `priority` or queue scheduling; chapter 3-1 does.
- This chapter does not cover the five message-tree operations or the full semantics of `turn_end`, `partial`, and `synthetic`; chapter 4-3 does.
- This chapter does not cover the ordering implementation of `MessageQueue`; chapters 3-1 and 4-7 do.
- This chapter does not cover the on-disk line format used by `to_record` and `from_record`; chapter 4-1 does.

## Main example

The complete `demo_messages.py` script is shown below. It constructs six groups of
sample input and prints each structure; the image bytes are generated inline, so no
external data file is needed.

```python
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


def show(message: Message, note: str) -> None:
    print(f"── {note} ──")
    print(f"kind={message.kind.value}  source={message.source!r}  tags={message.tags}  "
          f"priority={message.priority.name}")
    for index, block in enumerate(message.content):
        extra = ""
        if isinstance(block, TextBlock):
            extra = f"text={block.text!r}"
        elif isinstance(block, ThinkingBlock):
            extra = f"thinking={block.thinking!r} signature={block.signature!r}"
        elif isinstance(block, ToolCallBlock):
            extra = f"id={block.id!r} name={block.name!r} args={block.args}"
        elif isinstance(block, StructBlock):
            extra = f"data={block.data}"
        elif isinstance(block, ImageBlock):
            extra = (f"name={block.name!r} mime_type={block.mime_type} "
                     f"data(base64 first 24 chars)={block.data[:24]}…")
        print(f"  content[{index}] {block.type:<9} {extra}")
    if message.kind is MessageKind.TOOL:
        print(f"tool_call_id={message.tool_call_id}  tool_status={message.tool_status}")
    print()


user = Message(kind=MessageKind.USER,
               content=[TextBlock(text="Look up order 4521 for me")],
               source="chat_input", tags=["order"])
show(user, "① User message: USER")

provider = Message(kind=MessageKind.PROVIDER, content=[
    ThinkingBlock(thinking="sample thinking-block content (not a reasoning trace)",
                  signature="sig-abc"),
    TextBlock(text="I'll look up the order."),
    ToolCallBlock(id="call_9", name="query-order", args={"order_id": "4521"}),
])
show(provider, "② Model response: PROVIDER (interleaved content blocks)")

tool = Message(kind=MessageKind.TOOL,
               content=[StructBlock(data={"status": "shipped", "eta": "tomorrow"})],
               tool_call_id="call_9", tool_status="completed")
show(tool, "③ Tool result: TOOL (pairing anchor + struct block)")

event = Message(kind=MessageKind.EVENT, source="tool_result",
                priority=MessagePriority.NORMAL, content=[
                    TextBlock(text="[background task build #7 finished]"),
                    StructBlock(data={"exit_code": 0}),
                ])
show(event, "④ Event message: EVENT (source states the origin)")

png_1px = base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000"
                  "01f15c4890000000d49444154789c626001000000ffff030000060005"
                  "57bfabd40000000049454e44ae426082")).decode()
image = Message(kind=MessageKind.USER, content=[
    TextBlock(text="What is in this image?"),
    ImageBlock(data=png_1px, mime_type="image/png", name="pixel.png"),
])
show(image, "⑤ User multimodal message: media block inlined as base64")

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
```

Save the script above as `demo_messages.py` in a directory where
`flowing-agent` is installed, then run `uv run python demo_messages.py`. The
complete example output is:

```console
$ uv run python demo_messages.py
── ① User message: USER ──
kind=user  source='chat_input'  tags=['order']  priority=NORMAL
  content[0] text      text='Look up order 4521 for me'
── ② Model response: PROVIDER (interleaved content blocks) ──
kind=provider  source=''  tags=[]  priority=NORMAL
  content[0] thinking  thinking='sample thinking-block content (not a reasoning trace)' signature='sig-abc'
  content[1] text      text="I'll look up the order."
  content[2] tool_call id='call_9' name='query-order' args={'order_id': '4521'}
── ③ Tool result: TOOL (pairing anchor + struct block) ──
kind=tool  source=''  tags=[]  priority=NORMAL
  content[0] struct    data={'status': 'shipped', 'eta': 'tomorrow'}
tool_call_id=call_9  tool_status=completed
── ④ Event message: EVENT (source states the origin) ──
kind=event  source='tool_result'  tags=[]  priority=NORMAL
  content[0] text      text='[background task build #7 finished]'
  content[1] struct    data={'exit_code': 0}
── ⑤ User multimodal message: media block inlined as base64 ──
kind=user  source=''  tags=[]  priority=NORMAL
  content[0] text      text='What is in this image?'
  content[1] image     name='pixel.png' mime_type=image/png data(base64 first 24 chars)=iVBORw0KGgoAAAANSUhEUgAA…
── ⑥ Pairing-anchor enforcement ──
constructing an orphan TOOL message → ValueError: kind=TOOL messages must carry both tool_call_id and tool_status
non-TOOL message carrying pairing fields → ValueError: tool_call_id / tool_status may only be carried by kind=TOOL messages
```

Reading this transcript: in construction ② the thinking block, text block,
and tool-call block are interleaved in the order they were produced; the
struct block in ③ is a dict for programs and is dumped to text by the
adapter when fed to the LLM; the 1×1 PNG in ⑤ is inlined as base64 (`data`
is the authoritative representation — the message layer keeps no file-path
semantics); the two `ValueError` lines in ⑥ are the pairing anchor's
bidirectional enforcement — a forward violation (missing pairing fields)
and a reverse violation (pairing fields carried by the wrong kind) both
fail at the construction point.

The complete script, all constructed input data, and the example output are embedded
above. The script creates its image bytes in memory and reads no external files.

## Summary

1. `Message` uniformly represents all information, and its kind describes who sent it while the object has no `role` field.
2. The seven-value `MessageKind` defines queue behavior: PROVIDER never enters the queue, TOOL attaches to the tree within a turn, and SYSTEM uses a dual channel.
3. ContentBlocks can be interleaved, and media data is always inlined as base64.
4. Construction enforces the pairing anchor in both directions, so orphan results cannot exist.
