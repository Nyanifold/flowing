# 2-2 · Message Model: Representing Conversation History and Protocol Completeness

> The complete offline demo code and output are included at the end of this chapter. The code constructs message objects and prints their structure; it does not call a model or read/write persisted state.

> Prerequisites: Chapter 2-1 "Loop and Concurrency"

## What this chapter covers

How conversation history is represented inside the system: the general
role system, structured modeling of rich-text content, and the protocol
design that keeps calls and results strictly paired.

## Background

Model APIs represent history messages differently, but they share one
abstraction: every message has a role stating which side of the conversation
it comes from. Understanding this abstraction is the prerequisite for all
message handling (persistence, editing, mapping to a concrete API).

## Core concepts

### The role system

Role divisions in mainstream APIs:

| role | Source | Description |
|---|---|---|
| `system` | Application | Instructions carried with every request, declaring the role and working mode |
| `user` | User | Human input; also carries tool results back (some APIs) |
| `assistant` | Model | The model's response; may contain reasoning text and tool-call intent |
| `tool` | Tool | Tool execution results (some APIs give it its own role; others fold it into `user`) |

The same history maps to different role sequences on different APIs —
mapping rules are the adapter layer's responsibility, and the
application-side object model should not bind to any single vendor's
representation. flowing's internal model names message kinds by origin
instead of by API role (seven kinds, including user, model, tool, and
event); role mapping is left entirely to the adapter layer.

### Separation of content and structure

Message content is not a plain string but a **list of blocks**: text blocks,
reasoning blocks, tool-call blocks, structured-data blocks, and media blocks,
in the order they were produced. Benefits of the chunked model:

- Multimodality (interleaved images, text, and tool calls) has a single
  unified carrier;
- Structured data (e.g. dicts returned by tools) stays structured for
  programs and is serialized to text for the model — one carrier serving
  two kinds of readers;
- Streaming responses arrive incrementally block by block; both screen
  rendering and persistence work in units of blocks.

Run the complete code at the end of this chapter to see these constructions.
The reasoning block is represented only by the required omission marker:

```console
── ② Model response: PROVIDER (interleaved multimodal blocks) ──
kind=provider  source=''  tags=[]  priority=NORMAL
  content[0] thinking  thinking='[thinking] (reasoning trace omitted)' signature='sig-abc'
  content[1] text      text="I'll look up the order."
  content[2] tool_call id='call_9' name='query-order' args={'order_id': '4521'}

── ③ Tool result: TOOL (pairing anchor + struct block) ──
kind=tool  source=''  tags=[]  priority=NORMAL
  content[0] struct    data={'status': 'shipped', 'eta': 'tomorrow'}
tool_call_id=call_9  tool_status=completed
```

Line by line: in construction ② the content list interleaves a reasoning
block, a text block, and a tool-call block in the order they were produced —
a message's "content" is a list of blocks, not a string; the `struct` block
in construction ③ holds a dict that stays structured for programs and is
serialized to text by the adapter layer when sent to the model — one carrier
serving two kinds of readers.

### Protocol completeness: pairing calls and results

One round of tool calling unfolds into two message kinds: the call intent
(assistant side) and the execution result (tool side), paired one-to-one by
call ID. The completeness requirements:

- Every call intent must have exactly one result enter history; otherwise
  the next request reaches the model with an unpaired call, and most APIs
  simply reject it;
- Completeness is enforced at three layers: the construction layer enforces
  format (pairing fields placed on the wrong kind error out at message
  construction); the history layer closes pairs inside the tree — a
  cancelled execution is closed by persisting a "cancelled" result, and a
  crash is closed by the recovery pipeline synthesizing a placeholder
  result; the assembly layer only asserts, reporting any unpaired call
  instead of leaving it to be rejected by the server when the request is
  sent.

```python
# The full paired shape: intent (id=call_1) → result (tool_call_id=call_1)
assistant_msg = {"role": "assistant",
                 "tool_calls": [{"id": "call_1", "name": "query-order",
                                 "args": {"order_id": "4521"}}]}
tool_msg = {"role": "tool", "tool_call_id": "call_1",
            "content": {"status": "shipped"}}
```

In construction ② the call block has `id='call_9'`, and in construction ③
the result has `tool_call_id=call_9`. The values match exactly, which is the
pairing. Construction ⑥ demonstrates construction-layer enforcement:

```console
── ⑥ Pairing-anchor enforcement ──
constructing an orphan TOOL message → ValueError: kind=TOOL messages must carry both tool_call_id and tool_status
non-TOOL message carrying pairing fields → ValueError: tool_call_id / tool_status may only be carried by kind=TOOL messages
```

Line by line: the first line deliberately constructs a tool-result message
without pairing fields, and construction raises ValueError on the spot; the
second line does the reverse, attaching pairing fields to an ordinary
message, and is likewise rejected at the construction point. Violations in
both directions fail at construction — orphan results are eliminated before
they ever enter history.

## Common misconceptions

1. **Treating history as string concatenation**. String concatenation loses
   structure: tool calls and multimodality cannot be carried correctly.
   History should be a sequence of structured objects;
2. **Having the application layer "patch" orphan calls**. Pairing
   completeness should be enforced at the construction layer; patching right
   before sending means corrupted data has already flowed halfway down the
   pipeline;
3. **Designing the internal model around one API's roles**. The internal
   model is shaped by origin; API differences are absorbed by the adapter
   layer.

## Exercises

1. Write three messages (a tool-call intent, a tool result, and the model's
   final answer) and mark the pairing fields; then deliberately create an
   orphan result and state which layer should reject it;
2. Design the result message for a query tool that returns tabular data:
   what goes into the structured block and the text block respectively, and
   what the model reads;
3. Add one more construction to the complete demo code below: a user message
   containing both an image block and a text block, then inspect the printed
   representation to confirm that media is inlined as base64.

## Complete offline example: message construction and pairing validation

In a new empty directory, save the project configuration and complete code
below, then run `uv sync` and `uv run python demo_messages.py`. This program
needs no API key, makes no model request, and reads no image file; it
constructs a one-pixel PNG from inline hexadecimal bytes.

`pyproject.toml`:

```toml
[project]
name = "flowing-chapter-2-2"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["flowing-agent"]

[tool.uv]
package = false
```

`demo_messages.py`:

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


user = Message(kind=MessageKind.USER,
               content=[TextBlock(text="Look up order 4521 for me")],
               source="chat_input", tags=["order"])
show(user, "① User message: USER")

provider = Message(kind=MessageKind.PROVIDER, content=[
    ThinkingBlock(thinking="[thinking] (reasoning trace omitted)",
                  signature="sig-abc"),
    TextBlock(text="I'll look up the order."),
    ToolCallBlock(id="call_9", name="query-order",
                  args={"order_id": "4521"}),
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
img = Message(kind=MessageKind.USER, content=[
    TextBlock(text="What is in this image?"),
    ImageBlock(data=png_1px, mime_type="image/png", name="pixel.png"),
])
show(img, "⑤ User multimodal message: media block inlined as base64")

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

All input values are in the code above. The output is shown below; the
reasoning block contains only the omission marker and exposes no reasoning:

```text
── ① User message: USER ──
kind=user  source='chat_input'  tags=['order']  priority=NORMAL
  content[0] text      text='Look up order 4521 for me'

── ② Model response: PROVIDER (interleaved content blocks) ──
kind=provider  source=''  tags=[]  priority=NORMAL
  content[0] thinking  thinking='[thinking] (reasoning trace omitted)' signature='sig-abc'
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

## Summary

1. The role system divides messages by origin, and API differences are
   absorbed by the adapter layer;
2. Content is a list of blocks: multimodality, structured data, and
   streaming increments are carried uniformly;
3. Calls and results are strictly paired by ID, and the constraint should be
   enforced at the construction layer;
4. The internal model is shaped by origin and binds to no single vendor's
   representation.
