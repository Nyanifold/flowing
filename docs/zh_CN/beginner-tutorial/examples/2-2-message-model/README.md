# 示例：2-2 消息模型

本离线脚本构造 user、provider、tool、event 与多模态消息，打印内容块，并检查工具调用配对约束。脚本不调用模型。下文完整内联了所需代码、配置、提示词、输入与示例输出。

运行环境需要 Python 3.13 或更高版本、uv，以及可使用 Flowing CLI 和包的 Python 环境。模型调用还需要在 shell 中设置你自己的 `DEEPSEEK_API_KEY`；本文不含真实凭证。请在全新的工作目录下按下文相对文件名创建文件，并从该目录执行命令。

## 复现

### 运行离线演示

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run python demo_messages.py
```

单像素图像载荷由 Python 代码内联的完整字节序列生成。

## 完整内联材料

下方每个代码块都包含本示例所用相对文件的完整内容。输出代码块是示例留档；模型答复和主机相关信息可能变化。

### `demo_messages.py`

```python
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
```

### `demo_output.txt`

```text
── ① 用户消息：USER ──
kind=user  source='chat_input'  tags=['order']  priority=NORMAL
  content[0] text      text='帮我查订单 4521'

── ② 模型响应：PROVIDER（多模态块交错） ──
kind=provider  source=''  tags=[]  priority=NORMAL
  content[0] thinking  thinking='用户要查订单，先调查询工具。' signature='sig-abc'
  content[1] text      text='我来查询订单。'
  content[2] tool_call id='call_9' name='query-order' args={'order_id': '4521'}

── ③ 工具结果：TOOL（配对锚 + 结构块） ──
kind=tool  source=''  tags=[]  priority=NORMAL
  content[0] struct    data={'status': 'shipped', 'eta': '明天'}
tool_call_id=call_9  tool_status=completed

── ④ 事件消息：EVENT（source 说明来路） ──
kind=event  source='tool_result'  tags=[]  priority=NORMAL
  content[0] text      text='[后台任务 build #7 完成]'
  content[1] struct    data={'exit_code': 0}

── ⑤ 用户多模态消息：媒体块 base64 内联 ──
kind=user  source=''  tags=[]  priority=NORMAL
  content[0] text      text='这张图里是什么？'
  content[1] image     name='像素.png' mime_type=image/png data(base64 前 24 字符)=iVBORw0KGgoAAAANSUhEUgAA…

── ⑥ 配对锚强制 ──
构造孤儿 TOOL 消息 → ValueError: kind=TOOL messages must carry both tool_call_id and tool_status
非 TOOL 消息携带配对字段 → ValueError: tool_call_id / tool_status may only be carried by kind=TOOL messages
```

### `main.py`

```python
"""内置工具演示子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)   # 恢复既有 agent
    else:
        # 固定 agent_id → 幂等挂载：第二次启动走恢复，“同一个根回来了”
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### `model-tags.yaml`

```yaml
# 标签 → 模型条目名映射（单值：一个标签只映射一个条目）。
tags:
  default: deepseek-flash
```

### `models.yaml`

```yaml
# 模型条目：一个条目 = 一个具体模型（绑定一个 provider 条目）。
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### `providers.yaml`

```yaml
# provider 条目：一个条目 = 一个 API key 身份。
# {{env.VAR}} 在加载期替换；缺失时替换为空串并告警（warnings.warn），加载不中断。
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `root.fya`

```text
description: 项目问答助手：能查看目录、读取文件并总结。
model_tag: default
tools:
  - read      # 裸名：命名空间省略，等价于 builtin::read
  - glob
---
$system_prompt:
你是项目问答助手。本项目的根目录绝对路径是 {{ env.PWD }}
（调用文件工具时一律使用该绝对路径，不要猜其它目录）。
当问题涉及项目内容时，必须先用 glob 查看目录结构、再用 read 读取相关
文件，然后根据读到的内容回答；不要凭印象编造。回答控制在五句话以内。
```
