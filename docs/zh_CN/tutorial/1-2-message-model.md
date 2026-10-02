# 1-2 · 消息模型

## 前置阅读

[1-1 回合与循环](1-1-turn-and-loop.md)（消息如何驱动回合；本篇补齐消息的
常用类型与结构）。本篇在下方完整内联一个离线演示脚本；保存为
`demo_messages.py` 后即可构造消息对象并打印，不需要启动 Runtime 或调用模型。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| `Message` | 一切信息的统一表示：用户输入、模型响应、工具结果、外部事件、子智能体回执都是它 |
| `MessageKind` | 七值枚举，描述“这条消息来自谁”——不是“扮演什么角色” |
| ContentBlock | 消息内容的片段，不同 type 可交错排列（text / thinking / tool_call / struct / image 等） |
| 配对锚 | PROVIDER 消息的 `ToolCallBlock.id` 与 TOOL 消息的 `tool_call_id` 严格 1:1 成对，`__post_init__` 在构造点双向强制 |
| 亲代链 | `Message.parent_id` 链：消息级树的唯一结构依据（树结构见 3-1） |
| 消息级树 | 消息 id + 亲代链构成的森林；每个 Agent 持有一棵（1-1 的“树回放”已见过） |

## 目标

建立“一切皆是消息”的模型面：1-1 已用文本与工具调用两类消息跑通循环，
本篇补齐七值 `MessageKind`、常用 ContentBlock 与配对锚规则。

## 正文

### Message：字段速览

本例会用到的 `Message` 核心字段如下：

| 字段 | 要点 |
|---|---|
| `kind` | 七值 `MessageKind`，对象层唯一角色判别——**消息没有 `role` 属性** |
| `content` | ContentBlock 列表，不同 type 可交错 |
| `id` / `parent_id` | 消息级树的节点 id 与亲代链；`parent_id=None` 是根标记 |
| `source` / `tags` | 自由二级分类与标签（投递方填写，框架不枚举） |
| `turn_end` / `partial` / `synthetic` | 回合边界 / 流式中断 / 恢复占位标记——本篇不展开（4-3） |

### 七值 MessageKind：来自谁，而非扮演谁

| kind | 来自谁 | 排队? |
|---|---|---|
| `USER` | 用户 | 进队列 |
| `PROVIDER` | 模型响应（含多模态输出） | **永不进队列**（回合内产生） |
| `TOOL` | 工具执行结果 | 不入队（回合内直接挂树） |
| `SYSTEM` | 框架 / Composable 的上下文注入 | 双通道（队列 或 组装内注入） |
| `PEER` | 另一个 Agent 有意图地发送 | 进队列 |
| `EVENT` | 外部事件或扩展交付内容（cron、异步工具终值、Skill 正文） | 进队列 |
| `SUBAGENT` | 子 Agent 返回结果 | 进队列 |

命名原则是“来自谁”：`PROVIDER` 不取“assistant”，因为它还覆盖文生图 /
语音等多模态输出。**kind 无行为含义**——不存在“纯系统消息不触发 LLM”
之类的短路；kind 只决定 adapter 的呈现映射。

### kind→role：adapter 的事

各家 API 的 role 由 Provider adapter 从 kind 映射。这里只举两个例子：
Anthropic 会把 `SYSTEM` 映射到 `user` 并用 XML 包裹；OpenAI 会把 `TOOL`
映射到 `tool`。消息层与任何 Provider API 解耦：换模型不换消息代码。

### 常用 ContentBlock

- `TextBlock(text)`：纯文本；
- `ThinkingBlock(thinking, signature)`：模型推理摘要（signature 是
  Anthropic 家族的思考块签名，多轮回放必须原样带回）；
- `ToolCallBlock(id, name, args)`：LLM 发起的工具调用；
- `StructBlock(data)`：对程序是结构（dict / dataclass 序列化形态）、对
  LLM 是 dumps 文本；
- 媒体块 `ImageBlock` 等：一律 **base64 内联**，`data` 是权威表示。

### 配对锚：孤儿结果消灭在构造点

PROVIDER 消息里 `ToolCallBlock.id` 与后续 TOOL 消息的 `tool_call_id`
严格 1:1 成对；`Message.__post_init__` 双向强制
（`kind=TOOL ⟺ 配对字段非 None`），违反抛 `ValueError`——孤儿结果在
构造点就被消灭，不会流进上下文。

## 本篇不覆盖

- `priority` 细节与队列调度——3-1；
- 消息级树的手术（五 op）与 `turn_end` / `partial` / `synthetic`
  三标记的完整语义——4-3；
- `MessageQueue` 的排序实现——3-1 / 4-7；
- `to_record` / `from_record` 落盘行格式——4-1。

## 主线示例

下面是完整的 `demo_messages.py`。脚本内直接构造六组示例输入并打印消息结构；
其中的图片字节也在代码中生成，不依赖外部文件。将代码保存为
`demo_messages.py`，在已安装 `flowing-agent` 的 Python 环境中运行：

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
                     f"data(base64 前 24 字符)={block.data[:24]}…")
        print(f"  content[{index}] {block.type:<9} {extra}")
    if message.kind is MessageKind.TOOL:
        print(f"tool_call_id={message.tool_call_id}  tool_status={message.tool_status}")
    print()

user = Message(kind=MessageKind.USER,
               content=[TextBlock(text="帮我查订单 4521")],
               source="chat_input", tags=["order"])
show(user, "① 用户消息：USER")

provider = Message(kind=MessageKind.PROVIDER, content=[
    ThinkingBlock(thinking="示例推理块内容（非真实推理记录）", signature="sig-abc"),
    TextBlock(text="我来查询订单。"),
    ToolCallBlock(id="call_9", name="query-order", args={"order_id": "4521"}),
])
show(provider, "② 模型响应：PROVIDER（多种内容块交错）")

tool = Message(kind=MessageKind.TOOL,
               content=[StructBlock(data={"status": "shipped", "eta": "明天"})],
               tool_call_id="call_9", tool_status="completed")
show(tool, "③ 工具结果：TOOL（配对锚 + 结构块）")

event = Message(kind=MessageKind.EVENT, source="tool_result",
                priority=MessagePriority.NORMAL, content=[
                    TextBlock(text="[后台任务 build #7 完成]"),
                    StructBlock(data={"exit_code": 0}),
                ])
show(event, "④ 事件消息：EVENT（source 说明来路）")

png_1px = base64.b64encode(
    bytes.fromhex("89504e470d0a1a0a0000000d494844520000000100000001080600000"
                  "01f15c4890000000d49444154789c626001000000ffff030000060005"
                  "57bfabd40000000049454e44ae426082")).decode()
image = Message(kind=MessageKind.USER, content=[
    TextBlock(text="这张图里是什么？"),
    ImageBlock(data=png_1px, mime_type="image/png", name="像素.png"),
])
show(image, "⑤ 用户多模态消息：媒体块 base64 内联")

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

保存后运行 `uv run python demo_messages.py`。下面给出脚本的完整示例输出：

```console
$ uv run python demo_messages.py
── ① 用户消息：USER ──
kind=user  source='chat_input'  tags=['order']  priority=NORMAL
  content[0] text      text='帮我查订单 4521'
── ② 模型响应：PROVIDER（多种内容块交错） ──
kind=provider  source=''  tags=[]  priority=NORMAL
  content[0] thinking  thinking='示例推理块内容（非真实推理记录）' signature='sig-abc'
  content[1] text      text='我来查询订单。'
  content[2] tool_call id='call_9' name='query-order' args={'order_id': '4521'}
── ③ 工具结果：TOOL（配对锚 + 结构块）──
kind=tool  source=''  tags=[]  priority=NORMAL
  content[0] struct    data={'status': 'shipped', 'eta': '明天'}
tool_call_id=call_9  tool_status=completed
── ④ 事件消息：EVENT（source 说明来路）──
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

读这段留档：② 里思考块 / 文本块 / 工具调用块按产生顺序交错；③ 的结构块
对程序是 dict、喂给 LLM 时由 adapter dumps 成文本；⑤ 的 1×1 PNG 以
base64 内联（`data` 即权威表示，消息层不保留文件路径语义）；⑥ 的两条
`ValueError` 是配对锚的双向强制——正向缺配对字段、反向错带配对字段，
都过不了构造点。

本例全部输入、构造代码和输出都以内联方式给出；图片数据由脚本生成，不需要
读取其他文件。

## 小结

1. 一切信息统一表示为 `Message`；kind 描述“来自谁”，消息没有 `role`；
2. 七值 `MessageKind`：PROVIDER 永不入队、TOOL 回合内挂树、SYSTEM 双通道；
3. ContentBlock 交错排列；媒体一律 base64 内联；
4. 配对锚在构造点双向强制，孤儿结果无法存在。
