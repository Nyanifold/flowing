# 4-3 · 树手术与恢复不变量

## 前置阅读

[3-1 消息树与循环机制](3-1-message-tree-and-loop.md)（树与游标的机制面）、
[4-1 核心状态与消息持久化](4-1-core-state-and-message-persistence.md)
（恢复 = 重放）。两个演示均为离线（消息手工构造，不经 LLM）；完整脚本
与配置均内嵌如下。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 五 op | `MessageChain` 的写手术最小完备集：`insert` / `branch` / `remove` / `update` / `reparent`（+ 只读 `get` / `walk`） |
| 合并语义 | `insert` 在分叉点的行为：既有直接子消息重挂到新节点之下 |
| 邻接保持 | `remove` 的行为：被删消息的直接子自动重挂到其亲节点，链保持连续 |
| `turn_end` | 逻辑回合关闭的边界标记（agent 层写入，与 provider 层的 `finish` 分层） |
| `partial` | 流式中断标记：已累积内容保留落盘，不丢弃 |
| `synthetic` | 恢复合成占位标记：为孤立 tool_call 合成的确定性 id TOOL 消息，“不是真实产物” |

## 目标

消息级树的手术（3-1 刻意不讲的“手动编辑”在此全开）与恢复不变量：
历史可分叉、可修正，恢复永远得到成对、连续的上下文。

## 正文

### fork = 切游标

`await agent.fork(msg_id)` 只切 `current_head_id`、**从不创建节点**——
同一份历史可以同时存在多条分支视角（demo_surgery.py 的分叉探索）。
回合内 fork 有三件须知（伏笔提示，时序细节属 4-7）：fork 只影响“之后
新消息挂到哪”，不改已挂树内容；上下文随游标瞬移（下一回合即见新路径）；
fork 落点必须避开未闭合的调用-结果段——越过即产生永久坏分支（见下）。

**错误示例（配对断裂的坏分支）**：fork 回一条带 tool_call 的 PROVIDER
消息再续写，或 fork 把工具结果甩到另一分支——共享前缀里的调用在新分支
上未配对，组装即抛 `UnpairedToolCallError`；恢复管线不会自愈（合成封闭
按全局配对判定，结果在老分支上存在即不合成），坏分支永久坏。安全落点：
fork 到 user 消息上、或 provider 消息上方——不切开 tool_call 与其结果
之间的链段。

### 五 op 语义速记

| op | 语义 | 易错点 |
|---|---|---|
| `insert(after_id, msg)` | 在 `after_id` 之后插入 | **分叉点会合并**：既有直接子重挂到新节点下；只想加平行分支用 `branch` |
| `branch(parent_id, msg)` | 挂平行分支；`None` = 开新根 | 森林模型唯一持久化开根入口（压缩换链用） |
| `remove(msg_id)` | 单条删 | **邻接保持**：直接子重挂亲节点；子树不级联 |
| `update(msg_id, content)` | 只改内容 | 不动链、不改 kind |
| `reparent(msg_id, to=...)` | 子树整体重连 | 防环校验 |

每个 op = 改内存权威链 + append 一条变更行（tombstone/move/update），
物理重写延迟到压缩期（4-1）。**chain op 不自动移动 head**——挂树后
要 `fork(new_id)` 前移游标（demo 的 `grow` 助手即此定式）。

### 三标记

- `turn_end`：本条 PROVIDER 消息落盘时回合随之关闭——恢复时据此
  定位完整回合边界；与 provider 层的 `ProviderResponse.finish` 分层
  （中断的流式没有 finish，但回合照样关闭）；
- `partial`：流式中断的已产出内容**保留落盘**（3-1 实验 4 实证）；
- `synthetic`：恢复发现孤立 tool_call（结果缺失）时，合成一条确定性
  id（`synthetic-<call_id>`）的占位 TOOL 消息**落盘**封闭配对——树内永远
  成对，adapter 不需要也不应该自行修补；装配对未配对只做断言。

### 恢复不变量

1. **半截 turn 不截断**：最后一条 `turn_end=True` 之后的已落盘消息照常
   进入上下文（4-1 的崩溃恢复实证）；
2. **tool_call / 结果严格成对（树内封闭）**：孤儿调用由 synthetic 占位
   落盘封闭（demo_synthetic.py 实证：占位 id `synthetic-call_7`、
   `tool_status="error"`、正文说明“不是真实结果”）；执行期取消则以
   `tool_status="cancelled"` 落盘封闭；装配对未配对只做断言。

## 本篇不覆盖

- 核心状态与消息的落盘机制（变更行 / write-behind）——4-1；
- 压缩换链的策略面（`use_compact` / `use_auto_compact`）——5-3；
- 撕裂末行 / 墓碑压缩时点——6-3 按需。

## 主线示例

**演示 1：分叉探索 + 五 op 修正**（`demo_surgery.py`；完整终端输出
内嵌于下文）：

```console
$ uv run python demo_surgery.py
── 初始线性链（head → 根，逆时间序）──
  4 provider  parent=3 改走南路。
  3 user      parent=2 北路被堵了。
  2 provider  parent=1 先沿北路探一段。
  1 user      parent=None 路线 A 怎么走？
── fork 出平行分支后（head 在南路探索上；北路链完整保留）──
  6 provider  parent=5 南路更近，但有积水。
  5 user      parent=1 如果直接走南路呢？
  1 user      parent=None 路线 A 怎么走？
insert 后：g=7，b.parent=7，e.parent=7（都重挂到 g 下 = 合并语义）
update 后 e 的文本：'如果直接走南路呢？（补充：带伞）'
remove(d) 后：d 已消失（chain.get 抛 KeyError），其子 parent=3（重挂到 d 的亲节点 c）
```

**演示 2：孤立 tool_call 的 synthetic 自愈**（`demo_synthetic.py`：
先真实挂载一次 → 用撕裂的 `tree.jsonl` 覆盖（provider 带孤儿
`tool_call`、结果缺失）→ 走恢复管线；完整终端输出内嵌于下文）：

```console
$ uv run python demo_synthetic.py
已构造撕裂 session：provider(id=2) 带孤儿 tool_call(call_7)，无结果消息。
恢复后的全部消息（id 升序）：
  synthetic-call_7 tool      synthetic=True tool_status=error tool call call_7 result
                 1 user      synthetic=False tool_status=None 查一下目录
                 2 provider  synthetic=False tool_status=None
```

读这份留档：恢复重放发现 `call_7` 无配对结果，合成占位
`synthetic-call_7`（确定性 id，重放多少次都一样）**落盘**封闭配对——
树内永远成对，下游 adapter 无需处理孤儿。

### 完整可运行材料

将每个代码块分别保存为其标题所示文件名，并放在同一工作目录。以下是
两个离线演示所需的全部文件。Provider key 使用环境变量占位符；演示不会
调用 Provider。

`main.py`：

```python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh",
               resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
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

`root.fya`：

```yaml
description: 树手术演示助手：最小问答。
model_tag: default
---
$system_prompt:
你是一个简洁的中文助手。
```

`providers.yaml`：

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

`models.yaml`：

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

`model-tags.yaml`：

```yaml
tags:
  default: deepseek-flash
```

`demo_surgery.py`：

```python
import asyncio

from flowing import launch
from flowing.message import Message, MessageKind, TextBlock


def m(kind: MessageKind, text: str) -> Message:
    return Message(kind=kind, content=[TextBlock(text=text)])


def show(agent, label: str) -> None:
    print(f"── {label}（head → 根，逆时间序）──")
    for message in agent.chain.walk(agent.current_head_id):
        head = next((block.text[:26] for block in message.content
                     if getattr(block, "text", "")), "")
        print(f"{message.id:>3} {message.kind.value:<9} "
              f"parent={message.parent_id} {head}")
    print()


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    async def grow(parent, message):
        new_id = agent.chain.branch(parent, message)
        await agent.fork(new_id)
        return new_id

    a = await grow(None, m(MessageKind.USER, "路线 A 怎么走？"))
    b = await grow(a, m(MessageKind.PROVIDER, "先沿北路探一段。"))
    c = await grow(b, m(MessageKind.USER, "北路被堵了。"))
    await grow(c, m(MessageKind.PROVIDER, "改走南路。"))
    show(agent, "初始线性链")

    await agent.fork(a)
    e = await grow(a, m(MessageKind.USER, "如果直接走南路呢？"))
    await grow(e, m(MessageKind.PROVIDER, "南路更近，但有积水。"))
    show(agent, "fork 出平行分支后（head 在南路探索上；北路链完整保留）")

    await agent.fork(a)
    show(agent, "切回 a：insert 前的分叉点")

    g = agent.chain.insert(a, m(MessageKind.SYSTEM, "背景：今日有雨。"))
    print(f"insert 后：g={g}，b.parent={agent.chain.get(b).parent_id}，"
          f"e.parent={agent.chain.get(e).parent_id}（都重挂到 g 下 = 合并语义）")
    show(agent, "insert 后的分叉点结构")

    agent.chain.update(e, [TextBlock(text="如果直接走南路呢？（补充：带伞）")])
    print(f"update 后 e 的文本：{agent.chain.get(e).content[0].text!r}")

    d = agent.chain.get("4")
    child_of_d = agent.chain.branch("4", m(MessageKind.USER, "南路口碑如何？"))
    agent.chain.remove("4")
    print(f"remove(d) 后：d 已消失（chain.get 抛 KeyError），"
          f"其子 parent={agent.chain.get(child_of_d).parent_id}（重挂到 d 的亲节点 c）")
    await runtime.shutdown()


asyncio.run(main())
```

`demo_synthetic.py`：

```python
import asyncio
import json
import pathlib

from flowing import launch
from flowing.message import (Message, MessageKind, TextBlock, ToolCallBlock,
                             to_record)

SESSION = pathlib.Path(".flowing/agent-main")


def build_torn_session() -> None:
    SESSION.mkdir(parents=True, exist_ok=True)
    (SESSION / "meta.json").write_text(json.dumps({
        "agent_type": "@/root.fya",
        "parent_agent_id": "runtime-0",
        "created_at": "2026-09-18T00:00:00+00:00",
        "args": {},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    user = Message(kind=MessageKind.USER, content=[TextBlock(text="查一下目录")])
    user.id, user.parent_id = "1", None
    orphan = Message(
        kind=MessageKind.PROVIDER,
        content=[ToolCallBlock(id="call_7", name="bash",
                               args={"command": "ls"})])
    orphan.id, orphan.parent_id = "2", "1"
    orphan.turn_end = True
    rows = [{"type": "meta", "format_version": 1},
            to_record(user), to_record(orphan)]
    (SESSION / "tree.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8")


async def main() -> None:
    runtime = await launch(".")
    await runtime.shutdown()
    build_torn_session()
    print("已构造撕裂 session：provider(id=2) 带孤儿 tool_call(call_7)，无结果消息。")

    runtime = await launch(".", resume="agent-main")
    agent = await runtime.get_agent("agent-main")
    print("恢复后的全部消息（id 升序）：")
    for mid in sorted(agent._messages,
                      key=lambda key: int(key) if key.isdigit() else -1):
        message = agent._messages[mid]
        head = next((block.text[:24] for block in message.content
                     if getattr(block, "text", "")), "")
        print(f"{message.id:>18} {message.kind.value:<9} "
              f"synthetic={message.synthetic} "
              f"tool_status={message.tool_status} {head}")
    await runtime.shutdown()


asyncio.run(main())
```

分别运行 `uv run python demo_surgery.py` 或
`uv run python demo_synthetic.py`。对应输出已完整列在上方终端块中。

## 小结

1. fork 只切游标；分支探索零成本，旧链完整保留；
2. 五 op 最小完备：insert 合并、branch 平行、remove 邻接保持、update
   只改内容、reparent 子树重连（防环）；
3. `turn_end` / `partial` / `synthetic` 三标记各管回合边界、流式保留、
   恢复占位；
4. 恢复不变量：半截 turn 不截断、tool_call 永远成对（synthetic 自愈）。
