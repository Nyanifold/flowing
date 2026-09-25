# 2-2 · 消息模型：对话历史的表示与协议完整性

> 本篇的完整离线演示代码与输出均内联在文末；代码只构造消息对象并打印结构，不调用模型，也不读写持久化状态。

> 前置：第 2-1 篇“循环与并发”

## 这篇讲什么

对话历史在系统内部的表示方式：通用的 role 体系、富文本内容的
结构化建模，以及保证调用与结果严格配对的协议设计。

## 背景知识

各模型 API 对历史消息的表示不同，但共享同一套抽象：每条消息有
一个角色（role），表明它来自对话的哪一方。理解这套抽象是理解
一切消息处理（持久化、编辑、映射到具体 API）的前提。

## 核心概念

### role 体系

主流 API 的角色划分：

| role | 来源 | 说明 |
|---|---|---|
| `system` | 应用 | 每轮请求携带的指令，声明角色与工作方式 |
| `user` | 用户 | 人类输入，也承载工具结果回喂（部分 API） |
| `assistant` | 模型 | 模型的响应，可含推理文本与工具调用意图 |
| `tool` | 工具 | 工具执行结果（部分 API 单设此角色，部分并入 user） |

同一条历史在不同 API 上映射为不同的 role 序列——映射规则属于
适配层的职责，应用侧的对象模型不应绑定某一家的表示。flowing
的内部模型按“来源”而非 API role 命名（用户 / 模型 / 工具 /
事件等八种），role 映射完全交给适配层。

### 内容与结构的分离

消息内容不是纯字符串，而是**分块列表**：文本块、推理块、工具
调用块、结构化数据块、媒体块按产生顺序排列。分块模型的收益：

- 多模态（图、文、工具调用交错）有统一的承载方式；
- 结构化数据（工具返回的 dict 等）对程序保持结构、对模型序列
  化为文本——一个载体服务两类读者；
- 流式响应按块增量到达，上屏与落盘都以块为单位。

运行文末完整代码即可看到以下构造。推理块内容按规约省略：

```console
── ② 模型响应：PROVIDER（多模态块交错） ──
kind=provider  source=''  tags=[]  priority=NORMAL
  content[0] thinking  thinking='[thinking] (reasoning trace omitted)' signature='sig-abc'
  content[1] text      text='我来查询订单。'
  content[2] tool_call id='call_9' name='query-order' args={'order_id': '4521'}

── ③ 工具结果：TOOL（配对锚 + 结构块） ──
kind=tool  source=''  tags=[]  priority=NORMAL
  content[0] struct    data={'status': 'shipped', 'eta': '明天'}
tool_call_id=call_9  tool_status=completed
```

逐行看：第②组的 content 列表里，推理块、文本块、工具调用块
按产生顺序交错排列——一个消息的“内容”是块列表而不是字符串；
第③组的 `struct` 块装着一个 dict，它对程序保持结构、发给模型
时由适配层序列化为文本，一个载体服务两类读者。

### 协议完整性：调用与结果配对

一轮工具调用展开为两类消息：前者的调用意图（assistant 侧）与
后者的执行结果（tool 侧），以调用 ID 一一配对。完整性要求：

- 每条调用意图必须有恰好一条结果进入历史，否则下一轮请求对
  模型而言是一个未闭合的括号——多数 API 直接拒绝；
- 完整性分三层保障：构造层强制格式（配对字段放错位置在消息
  构造时即报错）；历史层树内封闭——执行中取消以“cancelled”
  结果落盘封闭、崩溃经恢复管线合成占位结果落盘封闭；装配层
  只做断言，发现未配对即报错，不留到发送请求时才被服务端拒绝。

```python
# 配对的完整形态：意图（id=call_1）→ 结果（tool_call_id=call_1）
assistant_msg = {"role": "assistant",
                 "tool_calls": [{"id": "call_1", "name": "query-order",
                                 "args": {"order_id": "4521"}}]}
tool_msg = {"role": "tool", "tool_call_id": "call_1",
            "content": {"status": "shipped"}}
```

上面留档的第②组里调用块的 `id='call_9'`，第③组结果的
`tool_call_id=call_9`——两个字段的值严格相等，这就是配对。
完整输出的第⑥组演示了构造层强制：

```console
── ⑥ 配对锚强制 ──
构造孤儿 TOOL 消息 → ValueError: kind=TOOL messages must carry both tool_call_id and tool_status
非 TOOL 消息携带配对字段 → ValueError: tool_call_id / tool_status may only be carried by kind=TOOL messages
```

逐行看：第一行故意构造一条没有配对字段的工具结果消息，构造
当场抛出 ValueError；第二行反过来，给普通消息带上配对字段，
同样在构造点被拒绝。两个方向的违规都过不了构造——孤儿结果
在进入历史之前就被消灭了。

## 常见误区

1. **把历史当字符串拼**。字符串拼接丢失了结构，工具调用、多模态
   都无法正确承载；历史应是结构化对象序列；
2. **由应用层“修补”孤儿调用**。配对完整性应在构造层强制；发送
   前才修补意味着损坏数据已经流过半条管线；
3. **按某家 API 的 role 设计内部模型**。内部模型按来源建模，API
   差异收敛在适配层。

## 练习

1. 给出三条消息（工具调用意图、工具结果、模型最终答复），标出
   配对字段；再故意制造一条孤儿结果，说明哪一层应该拒绝它；
2. 为一个“返回表格数据的查询工具”设计结果消息：结构化块与
   文本块各放什么、模型读到什么；
3. 在文末完整代码中增加一条同时含图片块与文本块的用户消息，
   再从打印结果确认媒体以 base64 内联在内容块里。

## 完整离线示例：消息构造与配对校验

在新建空目录中保存以下配置和完整代码，然后运行 `uv sync` 与
`uv run python demo_messages.py`。程序不需要
API key，不发起模型请求，也不读取图片文件；一像素 PNG 数据直接在
代码中以内联十六进制字节构造。

`pyproject.toml`：

```toml
[project]
name = "flowing-chapter-2-2"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["flowing-agent"]

[tool.uv]
package = false
```

`demo_messages.py`：

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
                     f"data(base64 前 24 字符)={b.data[:24]}…")
        print(f"  content[{i}] {b.type:<9} {extra}")
    if m.kind is MessageKind.TOOL:
        print(f"tool_call_id={m.tool_call_id}  tool_status={m.tool_status}")
    print()


user = Message(kind=MessageKind.USER,
               content=[TextBlock(text="帮我查订单 4521")],
               source="chat_input", tags=["order"])
show(user, "① 用户消息：USER")

provider = Message(kind=MessageKind.PROVIDER, content=[
    ThinkingBlock(thinking="[thinking] (reasoning trace omitted)",
                  signature="sig-abc"),
    TextBlock(text="我来查询订单。"),
    ToolCallBlock(id="call_9", name="query-order",
                  args={"order_id": "4521"}),
])
show(provider, "② 模型响应：PROVIDER（内容块交错）")

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
img = Message(kind=MessageKind.USER, content=[
    TextBlock(text="这张图里是什么？"),
    ImageBlock(data=png_1px, mime_type="image/png", name="像素.png"),
])
show(img, "⑤ 用户多模态消息：媒体块 base64 内联")

print("── ⑥ 配对锚强制 ──")
try:
    Message(kind=MessageKind.TOOL, content=[TextBlock(text="孤儿结果")])
except ValueError as exc:
    print(f"构造孤儿 TOOL 消息 → ValueError: {exc}")
try:
    Message(kind=MessageKind.USER,
            content=[TextBlock(text="带配对字段的用户消息")],
            tool_call_id="call_x", tool_status="completed")
except ValueError as exc:
    print(f"非 TOOL 消息携带配对字段 → ValueError: {exc}")
```

代码中已列出全部输入值。对应输出如下；推理块仅显示省略标记，
不包含任何推理内容：

```text
── ① 用户消息：USER ──
kind=user  source='chat_input'  tags=['order']  priority=NORMAL
  content[0] text      text='帮我查订单 4521'

── ② 模型响应：PROVIDER（内容块交错） ──
kind=provider  source=''  tags=[]  priority=NORMAL
  content[0] thinking  thinking='[thinking] (reasoning trace omitted)' signature='sig-abc'
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

## 小结

1. role 体系按消息来源划分，API 差异由适配层吸收；
2. 内容为分块列表：多模态、结构化数据、流式增量统一承载；
3. 调用与结果以 ID 严格配对，约束应在构造层强制；
4. 内部模型按来源建模，不绑定任何一家的表示。
