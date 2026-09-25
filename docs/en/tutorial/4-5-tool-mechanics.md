# 4-5 · Tool Mechanics: The Full Picture

## Prerequisites

[2-3 Writing a ScriptTool](2-3-write-script-tool.md) introduces tool authoring. [4-4 Three-Layer Capability Description and the Binding Layer](4-4-three-axes-and-tool-entry.md) explains declarations and bindings. [1-6 MCP Tools](1-6-mcp-tools.md) describes MCP group proxies and their connection behavior.

## Glossary for this chapter

| Term | Definition |
|---|---|
| The five normalized shapes | The values that can leave `Agent.tool_call`: `None`, a plain value, a single content block, a plain-scalar list, or a mixed list. |
| Forbidden blocks | `ToolCallBlock` and `ThinkingBlock` are protocol blocks and cannot be tool results. Normalizing either raises `ValueError`. |
| Media carriers | `Image`, `File`, `Audio`, and `Video` represent rich media in tool return values. Registered converters normalize them into media blocks. |
| The three background forms | An async-generator `execute` whose first yield is a receipt, a regular async `execute` with `background = True`, or an `execute` that returns an `asyncio.Task`. Each produces a `pending` receipt. |
| `production` | The `on_tool_yields` metadata value: `sync`, `receipt`, `segment`, or `final`. |

## Goals

This chapter explains how tool results are normalized, how long-running tools continue in the background, and how hooks observe each result production.

## Main text

### Output normalization

A value returned by `execute` passes through `normalize_output` and then `output_to_blocks`. The first function normalizes the value into one of five shapes; the second converts that shape into message content blocks.

- `None` becomes an empty block list. A string becomes a `TextBlock`, and a Boolean, integer, or float becomes a `TextBlock` containing JSON text.
- A dictionary, dataclass, Pydantic model, or plain-scalar list becomes a `StructBlock`. Programs receive its structured data, while the model receives serialized text.
- A mixed list becomes one block per member, in the original order. Strings and scalars become text blocks; dictionaries and nested lists become structure blocks; supported content blocks pass through.
- A `ToolCallBlock` or `ThinkingBlock` is forbidden wherever it appears on the normalization path. It raises `ValueError` as an authoring error.
- `Image`, `File`, `Audio`, and `Video` carriers are converted to media blocks. The converter registry can be extended with `register_media_converter`.
- When `output_to_blocks` receives an error string, it appends that string as a final text block.

The shape demo below exercises each of these paths, including an image carrier and an appended error.

### The three background forms

An async-generator `execute` uses its first yield as the receipt. The remaining yields are delivered as background segments. A regular async `execute` can instead declare `background = True`; this flag is supported for script tools. A third form returns an `asyncio.Task`.

Each form returns a `pending` result with a `background_task_id`, so the logical turn does not wait for the long-running work to finish. Later productions are placed into the message tree as EVENT messages and can trigger follow-up turns. An async generator that ends naturally has no separate `final` production; the tool author can yield an explicit completion marker as its last segment.

Background cancellation is cooperative. A tool can inspect `self._execution.cancel.is_set()` and respond to the signal. The framework does not guarantee that a background task will stop at an arbitrary point.

### Production-level observation

The `on_tool_yields` hook runs once for each non-blocked production. Its `production` value is `sync` for a synchronous result, `receipt` for the initial background receipt, `segment` for each background yield, and `final` for a task's terminal value or termination notice.

The call-level `after_tool_call` hook runs once for the tool call, including a shortcut result. Attach per-production rewriting and content scanning to `on_tool_yields`. Attach disposition of the completed tool call to `after_tool_call`.

### The five dispatch duties of `Tool.__call__`

The framework owns `Tool.__call__`, which performs five duties around the author's `execute` method:

1. It detects whether `execute` is an async generator, another awaitable, or synchronous code.
2. It wraps awaited work in a task associated with the current execution so that cancellation can be coordinated.
3. It injects `caller` when the method declares that parameter.
4. It validates trusted, resolved arguments before entering the exception-conversion block. A configuration or programming error therefore remains on the framework error channel instead of becoming model-visible tool text.
5. It normalizes and wraps the result. An ordinary execution exception becomes an `error` result, `Intercepted` becomes a `blocked` result, and a background form becomes a `pending` receipt handed to the background driver.

Tool authors implement `execute`; they do not override this dispatcher.

### Related mechanics

MCP tools declared as a group proxy are invoked through their synthetic names. Calling a declared instance directly raises `RuntimeError`. Connections are opened lazily for each call and closed afterward; the proxy does not pool them. For `inputSchema`, a parameter without a default is treated as required and is supplied as `None` when absent.

Tool descriptions use this fallback order: an explicit declaration, the class docstring as a whole, and then the `execute` docstring as a whole.

## Out of scope

- Binding overrides such as aliases, specified values, and strictness are covered in 4-4.
- Approval policy and interception behavior are covered in 1-4 and 4-6.
- Provider transport for media blocks is covered in 3-3 and the provider adapter contracts.

## Main example

### Complete runnable materials

Save each block under its displayed filename in one working directory. Save the build tool block as `tools/build.py`. The entry point uses `launch(".")` to load `main.py` and mount the declarative agent. Use a Python 3.13 environment with Flowing and its dependencies installed. The background demo loads the configured DeepSeek provider and can cause provider calls when EVENT messages trigger follow-up turns, so set `DEEPSEEK_API_KEY` in the environment before running it. The provider configuration below contains only an environment-variable placeholder. The shape demo is offline and does not call a provider.

The project uses `persist_dir="@/.flowing"`. Run it in a disposable working directory if you want to preserve an existing session store. Run either command from the directory containing these files:

`main.py`:

```python
"""Entry point for the tool mechanics demo."""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh",
               resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # Provide assembly inputs before mounting the agent.
    runtime.provide("timezone", "Asia/Shanghai")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        kwargs: dict = {}
        if user_name is not None:
            kwargs["user_name"] = user_name
        if locale != "zh":
            kwargs["locale"] = locale
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

`root.fya`:

```yaml
description: "Tool mechanics demo assistant: calls the background build tool."
model_tag: default
tools:
  - ./tools/build.py
---
$system_prompt:
You are a demo assistant. When the user asks for a build, use the build
tool; the background receipt arrives first, and progress and results are
delivered as messages over time—relay them faithfully as they arrive.
Keep answers to one sentence.
```

`providers.yaml`:

```yaml
# A provider entry represents one API-key identity.
# {{env.VAR}} is substituted at load time; a missing variable is replaced
# with an empty string and a warning, without aborting the load.
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

`models.yaml`:

```yaml
# A model entry binds one concrete model to one provider entry.
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

`model-tags.yaml`:

```yaml
# A tag maps to one model entry.
tags:
  default: deepseek-flash
```

`tools/build.py`:

```python
"""Background build tool: the async-generator form."""

import asyncio

from pydantic import BaseModel

from flowing import ScriptTool


class BuildArgs(BaseModel):
    target: str = "app"


class BuildTool(ScriptTool):
    """Simulates a background build and its progress productions."""

    name = "build"
    args_model = BuildArgs

    async def execute(self, *, target: str):
        yield {"receipt": f"Build task accepted: {target}"}
        for step, label in enumerate(["compile", "package", "verify"], start=1):
            await asyncio.sleep(0.6)
            yield f"Progress {step}/3: {label} done"
        yield {"done": True, "artifact": f"dist/{target}.tar"}
```

`demo_background.py`:

```python
"""Demonstrate the async-generator background tool pipeline."""

import asyncio

from flowing import launch
from flowing.message import MessageKind
from flowing.tool import ToolCall


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    yields_log: list[tuple] = []

    async def watch(agent_, result):
        yields_log.append((result.production, result.name,
                           type(result.output).__name__))
        return result

    agent.hooks.on_tool_yields(watch, by="diag")

    # This is the demo input: one direct call with target="demo".
    receipt = await agent.tool_call(
        ToolCall(id="call-b1", name="build", args={"target": "demo"})
    )
    print(f"① First yield → receipt: status={receipt.status} "
          f"background_task_id={receipt.background_task_id}")
    print(f"   Turn not blocked: tool_call has returned, the background "
          f"task keeps running")

    # Wait until the four post-receipt productions have reached the hook.
    for _ in range(100):
        await asyncio.sleep(0.3)
        segments = [item for item in yields_log if item[0] == "segment"]
        if len(segments) >= 4:
            break

    print("② on_tool_yields per production (production metadata):")
    for production, name, kind in yields_log:
        print(f"   {production:<9} name={name:<6} output={kind}")

    # Wait for the EVENT-triggered turn to finish before counting tree messages.
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.5)
    events = [message for message in agent._messages.values()
              if message.kind is MessageKind.EVENT]
    print(f"③ Background result delivery: {len(events)} EVENT messages "
          f"enqueued into the tree")
    for message in events:
        head = next((block.text[:30] for block in message.content
                     if getattr(block, "text", "")), "")
        print(f"   source={message.source!r} async tool build: {head}")

    await runtime.shutdown()


asyncio.run(main())
```

`demo_shapes.py`:

```python
"""Demonstrate output normalization without a provider call."""

import asyncio
import base64

from flowing.media import Image
from flowing.message import ToolCallBlock
from flowing.tool import normalize_output, output_to_blocks


async def main() -> None:
    png_b64 = base64.b64encode(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c626001000000ffff030000060005"
        "57bfabd40000000049454e44ae426082")).decode()

    print("① The five normalized shapes (ordinary execute return values "
          "→ one unified exit):")
    for label, value in [("None", None), ("str", "done"),
                         ("scalar", 42), ("dict", {"ok": True}),
                         ("plain list", ["phase one", "phase two"]),
                         ("mixed list", ["phase one",
                                         Image(data=png_b64, mime_type="image/png")])]:
        out = await normalize_output(value)
        blocks = output_to_blocks(out)
        kinds = [block.type for block in blocks]
        print(f"   {label:<10} → blocks={kinds}")

    print("② Forbidden blocks (ToolCallBlock / ThinkingBlock on any path "
          "→ ValueError):")
    try:
        await normalize_output(ToolCallBlock(id="c1", name="x", args={}))
    except ValueError as exc:
        print(f"   {exc}")

    print("③ Media carriers (Image → ImageBlock, base64 inline):")
    png = base64.b64encode(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c626001000000ffff030000060005"
        "57bfabd40000000049454e44ae426082")).decode()
    out = await normalize_output(Image(data=png, mime_type="image/png"))
    blocks = output_to_blocks(out)
    print(f"   Image → {[block.type for block in blocks]} (mime={blocks[0].mime_type})")

    print("④ error appended (output_to_blocks appends an error text block "
          "at the end):")
    blocks = output_to_blocks({"ok": False}, error="build failed: missing dependencies")
    print(f"   {[block.type for block in blocks]} (last block={blocks[-1].text!r})")


asyncio.run(main())
```

### Captured output: background execution

The following is the saved English capture. Its EVENT total is discussed below.

```console
$ uv run python demo_background.py
① First yield → receipt: status=pending background_task_id=1
   Turn not blocked: tool_call has returned, the background task keeps running
② on_tool_yields per production (production metadata):
   receipt   name=build  output=dict
   segment   name=build  output=str
   segment   name=build  output=str
   segment   name=build  output=str
   segment   name=build  output=dict
③ Background result delivery: 8 EVENT messages enqueued into the tree
   source='tool_result' async tool build: async tool build: 
   source='tool_result' async tool build: async tool build: 
   source='tool_result' async tool build: async tool build: 
   source='tool_result' async tool build: async tool build: 
   source='tool_result' async tool build: async tool build: 
   source='tool_result' async tool build: async tool build: 
   source='tool_result' async tool build: async tool build: 
   source='tool_result' async tool build: async tool build: 
```

The capture records eight EVENT messages in English and four in the Chinese capture. The `main()` function in `demo_background.py` makes one direct `Agent.tool_call()` with `target="demo"`. `BuildTool.execute()` yields one receipt and four later values. The captures do not establish why their EVENT totals differ; this is an unverified captured-record discrepancy. Treat both totals as records of their respective runs, not as a shared expected count.

### Captured output: normalization

This offline capture shows the normalized shapes, forbidden-block error, media conversion, and error-text append:

```console
$ uv run python demo_shapes.py
① The five normalized shapes (ordinary execute return values → one unified exit):
   None       → blocks=[]
   str        → blocks=['text']
   scalar     → blocks=['text']
   dict       → blocks=['struct']
   plain list → blocks=['struct']
   mixed list → blocks=['text', 'image']
② Forbidden blocks (ToolCallBlock / ThinkingBlock on any path → ValueError):
   forbidden block type in tool result: ToolCallBlock
③ Media carriers (Image → ImageBlock, base64 inline):
   Image → ['image'] (mime=image/png)
④ error appended (output_to_blocks appends an error text block at the end):
   ['struct', 'text'] (last block='build failed: missing dependencies')
```

## Summary

1. `normalize_output` and `output_to_blocks` turn ordinary values into message blocks; forbidden protocol blocks raise `ValueError`, and media carriers pass through the converter registry.
2. The async-generator, `background = True`, and `asyncio.Task` forms return a pending receipt and deliver later productions as EVENT messages.
3. `on_tool_yields` observes and can rewrite individual productions. `after_tool_call` handles the completed tool-call result.
4. `Tool.__call__` owns dispatch and wrapping; tool authors provide `execute`.
