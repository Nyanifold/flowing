# Example: 2-2 Message Model

This offline script constructs user, provider, tool, event, and multimodal messages, prints their content blocks, and checks the tool-call pairing invariant. It makes no model calls. The complete code, configuration, prompts, inputs, and example outputs are included below.

Use Python 3.13 or later, uv, and an environment where the Flowing CLI and package are available. Model-backed runs also require your own `DEEPSEEK_API_KEY` in the shell; no real credential is included here. Create the relative files exactly as shown under a fresh working directory, and run the commands from that directory.

## Reproduce

### Run the offline demonstration

Run the following command from the working directory containing the inline files:

```console
$ uv run python demo_messages.py
```

The one-pixel image payload is generated from the complete byte sequence embedded in the Python code.

## Complete inline materials

Each block below contains the complete content of a relative file used by this example. Output blocks are sample transcripts; model responses and host-specific details can vary.

### `demo_messages.py`

```python
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
```

### `demo_output.txt`

```text
── ① User message: USER ──
kind=user  source='chat_input'  tags=['order']  priority=NORMAL
  content[0] text      text='Look up order 4521 for me'

── ② Model response: PROVIDER (interleaved multimodal blocks) ──
kind=provider  source=''  tags=[]  priority=NORMAL
  content[0] thinking  thinking='The user wants to check an order; query the order tool first.' signature='sig-abc'
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

### `main.py`

```python
"""Entry point of the builtin-tools demo subproject: launch imports this file
and awaits main()."""

from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # Fixed agent_id → idempotent mount: the second startup goes through
    # recovery — "the same root is back"
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### `model-tags.yaml`

```yaml
# Tag → model entry name mapping (single value: one tag maps to one entry).
tags:
  default: deepseek-flash
```

### `models.yaml`

```yaml
# Model entry: one entry = one concrete model (bound to one provider entry).
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### `providers.yaml`

```yaml
# Provider entry: one entry = one API key identity.
# {{env.VAR}} is substituted at load time; when missing, it is replaced with
# an empty string and a warning is issued (warnings.warn); loading continues.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `root.fya`

```text
description: "Project Q&A assistant: can list directories, read files, and summarize."
model_tag: default
tools:
  - read      # Bare name: namespace omitted, equivalent to builtin::read
  - glob
---
$system_prompt:
You are a project Q&A assistant. The absolute path of this project's root
directory is {{ env.PWD }} (always use this absolute path when calling file
tools; do not guess other directories). When a question concerns project
content, you must first use glob to inspect the directory structure and then
use read to read the relevant files before answering based on what you read;
do not make things up from memory. Keep your answers within five sentences.
```
