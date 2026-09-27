# 4-5 · 工具机制全景

## 前置阅读

[2-3 编写 ScriptTool](2-3-write-script-tool.md)介绍工具编写；[4-4 三层能力描述与绑定层](4-4-three-axes-and-tool-entry.md)介绍声明与绑定；[1-6 MCP 工具](1-6-mcp-tools.md)说明 MCP 组代理及其连接行为。

## 本篇名词表

| 名词 | 定义 |
|---|---|
| 归一五形态 | `Agent.tool_call` 出口可能包含的五种值：`None`、基础值、单个内容块、纯基础值列表、混合列表。 |
| 违禁块 | `ToolCallBlock` 与 `ThinkingBlock` 是协议块，不能作为工具结果。归一化遇到它们会抛出 `ValueError`。 |
| 媒体载体 | `Image`、`File`、`Audio` 与 `Video` 是工具返回值中富媒体的统一表示。注册的转换器会将它们归一为媒体块。 |
| 后台三形态 | 长任务的三种写法：首个 yield 作为收据的 async-generator `execute`、设置 `background = True` 的普通 async `execute`，或返回 `asyncio.Task` 的 `execute`。三者都会产生 `pending` 收据。 |
| `production` | `on_tool_yields` 的产物元信息，取值为 `sync`、`receipt`、`segment` 或 `final`。 |

## 目标

本篇说明工具结果如何归一、长任务如何在后台继续，以及钩子如何观察每份产物。

## 正文

### 结果归一

`execute` 返回的值先经过 `normalize_output`，再由 `output_to_blocks` 转成消息内容块。前者把值归为五种形态之一，后者把该形态塑形成内容块列表。

- `None` 变为空块列表。字符串变为 `TextBlock`；布尔值、整数与浮点数变为包含 JSON 文本的 `TextBlock`。
- 字典、dataclass、Pydantic 模型或纯基础值列表变为 `StructBlock`。程序得到结构化数据，模型得到序列化文本。
- 混合列表按原顺序逐项变为块。字符串与标量变为文本块；字典与嵌套列表变为结构块；受支持的内容块原样通过。
- `ToolCallBlock` 与 `ThinkingBlock` 在归一化路径的任何位置都属违禁块。命中时会以作者错误抛出 `ValueError`。
- `Image`、`File`、`Audio` 与 `Video` 载体会转换为媒体块。转换器注册表可通过 `register_media_converter` 扩展。
- `output_to_blocks` 收到错误文本时，会将其作为最后一个文本块追加。

下方的形态演示覆盖这些路径，包括 Image 载体与末尾错误文本。

### 后台三形态

async-generator `execute` 把首个 yield 作为收据，后续 yield 作为后台产物逐段投递。普通 async `execute` 也可声明 `background = True`；此标记仅由脚本工具支持。第三种写法是返回 `asyncio.Task`。

三种写法都会返回带有 `background_task_id` 的 `pending` 结果，因此逻辑回合不会等待长任务完成。后续产物作为 EVENT 消息进入消息树，并可能触发后续回合。async generator 自然结束时不会另发 `final` 产物；作者可以自行 yield 一个明确的完成标记作为最后一段。

后台任务的取消是协作式的。工具可以检查 `self._execution.cancel.is_set()` 并响应信号。框架不保证后台任务会在任意位置立即停止。

### 产物级观察

每份非 blocked 产物都会触发一次 `on_tool_yields`。同步结果的 `production` 为 `sync`；后台初始收据为 `receipt`；每个后台 yield 为 `segment`；Task 的终值或终止通知为 `final`。

调用级钩子 `after_tool_call` 针对一次工具调用运行一次，也包括 shortcut 结果。逐份改写产物和扫描内容应挂到 `on_tool_yields`；处置完整工具调用的结果应挂到 `after_tool_call`。

### `Tool.__call__` 的五项调度职责

框架负责 `Tool.__call__`，它围绕作者实现的 `execute` 完成五项工作：

1. 它检查 `execute` 是 async generator、其他 awaitable，还是同步代码。
2. 它将 awaitable 工作包装为与当前 execution 关联的 Task，以协调取消。
3. 它会在方法声明了 `caller` 参数时注入调用方。
4. 它在异常转换区块之外校验可信的最终参数。配置或编程错误因此留在框架错误通道，不会变成模型可见的工具文本。
5. 它归一并包装结果。普通执行异常变成 `error` 结果，`Intercepted` 变成 `blocked` 结果，后台形态变成 `pending` 收据并交给后台驱动器。

工具作者实现 `execute`，不覆写这个调度器。

### 相关机制

声明为组代理的 MCP 工具应通过合成名调用。直接调用声明实例会抛出 `RuntimeError`。连接按需建立，每次调用后关闭；代理不做连接池。对于 `inputSchema`，无默认值的参数按 required 处理，缺省时补入 `None`。

工具描述的回退顺序是：显式声明、类 docstring 整体、`execute` docstring 整体。

## 本篇不覆盖

- 绑定覆写，例如别名、specified 值与 strict，见 4-4。
- 审批策略与拦截行为，见 1-4 和 4-6。
- 媒体块的 Provider 传输方式，见 3-3 和 Provider adapter 契约。

## 主线示例

### 完整可运行材料

将每个代码块保存为所示文件名，并放在同一工作目录。构建工具代码保存为 `tools/build.py`。入口通过 `launch(".")` 加载 `main.py` 并挂载声明式 Agent。使用安装了 Flowing 及其依赖的 Python 3.13 环境。后台演示会加载配置中的 DeepSeek Provider；EVENT 消息触发后续回合时可能产生 Provider 调用，因此运行前请在环境中设置 `DEEPSEEK_API_KEY`。Provider 配置中只保留环境变量占位符。形态演示离线运行，不调用 Provider。

项目将持久化目录设为 `persist_dir="@/.flowing"`。如需保留已有会话状态，请在一次性工作目录中运行。以下命令均从这些文件所在目录运行：

`main.py`：

```python
"""工具机制演示入口。"""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # 挂载 Agent 之前提供影响装配的输入。
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

`root.fya`：

```yaml
description: 工具机制演示助手：调用后台构建工具。
model_tag: default
tools:
  - ./tools/build.py
---
$system_prompt:
你是演示助手。用户要求构建时使用 build 工具；后台收据会先到，
进度与结果会随时间以消息送达，请如实转达。
回答控制在一句话以内。
```

`providers.yaml`：

```yaml
# Provider 条目代表一个 API key 身份。
# {{env.VAR}} 在加载时替换；变量缺失会替换为空串并告警，加载不中断。
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

`models.yaml`：

```yaml
# 模型条目将一个具体模型绑定到一个 Provider 条目。
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

`model-tags.yaml`：

```yaml
# 标签映射到一个模型条目。
tags:
  default: deepseek-flash
```

`tools/build.py`：

```python
"""后台构建工具：async-generator 形态。"""

import asyncio

from pydantic import BaseModel

from flowing import ScriptTool


class BuildArgs(BaseModel):
    target: str = "app"


class BuildTool(ScriptTool):
    """模拟后台构建及其进度产物。"""

    name = "build"
    args_model = BuildArgs

    async def execute(self, *, target: str):
        yield {"receipt": f"构建任务已受理：{target}"}
        for step, label in enumerate(["编译", "打包", "校验"], start=1):
            await asyncio.sleep(0.6)
            yield f"进度 {step}/3：{label}完成"
        yield {"done": True, "artifact": f"dist/{target}.tar"}
```

`demo_background.py`：

```python
"""演示 async-generator 工具的后台执行全链路。"""

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

    # 本演示输入：直接调用一次，target="demo"。
    receipt = await agent.tool_call(ToolCall(id="call-b1", name="build",
                                             args={"target": "demo"}))
    print(f"① 首 yield → 收据: status={receipt.status} "
          f"background_task_id={receipt.background_task_id}")
    print(f"   回合不被阻塞：tool_call 已返回，后台继续跑")

    # 等后台产物全部送达（EVENT 消息入队并被消费）
    for _ in range(100):
        await asyncio.sleep(0.3)
        done = [item for item in yields_log if item[0] == "segment"]
        if len(done) >= 4:
            break
    print("② on_tool_yields 逐份产物（production 元信息）：")
    for production, name, kind in yields_log:
        print(f"   {production:<9} name={name:<6} output={kind}")

    # 等 EVENT 结果消息进树（后台投递触发的新回合）
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.5)
    events = [message for message in agent._messages.values()
              if message.kind is MessageKind.EVENT]
    print(f"③ 后台结果投递: EVENT 消息 {len(events)} 条进树")
    for message in events:
        head = next((block.text[:30] for block in message.content
                     if getattr(block, "text", "")), "")
        print(f"   source={message.source!r} async tool build: {head}")
    await runtime.shutdown()


asyncio.run(main())
```

`demo_shapes.py`：

```python
"""离线演示结果归一：五形态、违禁块与媒体载体。"""

import asyncio
import base64

from flowing.media import Image
from flowing.message import StructBlock, TextBlock, ToolCallBlock
from flowing.tool import normalize_output, output_to_blocks


async def main() -> None:
    png_b64 = base64.b64encode(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c626001000000ffff030000060005"
        "57bfabd40000000049454e44ae426082")).decode()
    print("① 归一五形态（execute 普通值出 → 统一出口）：")
    for label, value in [("None", None), ("str", "完成"),
                         ("标量", 42), ("dict", {"ok": True}),
                         ("纯基础 list", ["阶段一", "阶段二"]),
                         ("混合 list", ["阶段一",
                                        Image(data=png_b64, mime_type="image/png")])]:
        out = await normalize_output(value)
        blocks = output_to_blocks(out)
        kinds = [block.type for block in blocks]
        print(f"   {label:<10} → blocks={kinds}")

    print("② 违禁块（ToolCallBlock / ThinkingBlock 任何路径 → ValueError）：")
    try:
        await normalize_output(ToolCallBlock(id="c1", name="x", args={}))
    except ValueError as exc:
        print(f"   {exc}")

    print("③ 媒体载体（Image → ImageBlock，base64 内联）：")
    png = base64.b64encode(bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c626001000000ffff030000060005"
        "57bfabd40000000049454e44ae426082")).decode()
    out = await normalize_output(Image(data=png, mime_type="image/png"))
    blocks = output_to_blocks(out)
    print(f"   Image → {[block.type for block in blocks]}（mime={blocks[0].mime_type}）")

    print("④ error 追加（output_to_blocks 末尾补错误文本块）：")
    blocks = output_to_blocks({"ok": False}, error="构建失败：缺依赖")
    print(f"   {[block.type for block in blocks]}（末块={blocks[-1].text!r}）")


asyncio.run(main())
```

### 后台执行留档

以下是保存的中文运行记录。EVENT 数量差异见下文。

```console
$ uv run python demo_background.py
① 首 yield → 收据: status=pending background_task_id=1
   回合不被阻塞：tool_call 已返回，后台继续跑
② on_tool_yields 逐份产物（production 元信息）：
   receipt   name=build  output=dict
   segment   name=build  output=str
   segment   name=build  output=str
   segment   name=build  output=str
   segment   name=build  output=dict
③ 后台结果投递: EVENT 消息 4 条进树
   source='tool_result' async tool build: 
   source='tool_result' async tool build: 
   source='tool_result' async tool build: 
   source='tool_result' async tool build: 
```

英文留档记录 8 条 EVENT 消息，中文留档记录 4 条。内嵌的 `demo_background.py` 中，`main()` 只直接调用一次 `Agent.tool_call()`，参数为 `target="demo"`；`BuildTool.execute()` 产出一份收据和后续四份值。两份留档无法说明 EVENT 总数不同的原因；这是尚未核实的留档差异。应将两个数值视为各自运行记录，不视为跨语言共享的预期数量。

### 归一化留档

离线留档展示五种形态、违禁块错误、媒体转换与追加错误文本：

```console
$ uv run python demo_shapes.py
① 归一五形态（execute 普通值出 → 统一出口）：
   None       → blocks=[]
   str        → blocks=['text']
   标量         → blocks=['text']
   dict       → blocks=['struct']
   纯基础 list   → blocks=['struct']
   混合 list    → blocks=['text', 'image']
② 违禁块（ToolCallBlock / ThinkingBlock 任何路径 → ValueError）：
   forbidden block type in tool result: ToolCallBlock
③ 媒体载体（Image → ImageBlock，base64 内联）：
   Image → ['image']（mime=image/png）
④ error 追加（output_to_blocks 末尾补错误文本块）：
   ['struct', 'text']（末块='构建失败：缺依赖'）
```

## 小结

1. `normalize_output` 与 `output_to_blocks` 将普通值塑形成消息块；违禁协议块会抛出 `ValueError`，媒体载体经转换器注册表处理。
2. async-generator、`background = True` 与 `asyncio.Task` 三种形式先返回 pending 收据，后续产物作为 EVENT 消息投递。
3. `on_tool_yields` 观察并可改写逐份产物；`after_tool_call` 处理完整工具调用的结果。
4. `Tool.__call__` 负责调度与包装；工具作者实现 `execute`。

