# 3-1 · 消息树与循环机制

## 前置阅读

[1-1 回合与循环](1-1-turn-and-loop.md)（概念层：消息驱动回合、三入口）、
[1-2 消息模型](1-2-message-model.md)（消息字段与 kind）。下文完整列出所需
配置、prompt、程序、输入与一份记录输出；不需要查找其他示例材料。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 消息级树 | 消息 `id` + `parent_id` 亲代链构成的森林：`parent_id=None` 是根标记，一棵树允许多个根 |
| 游标（current_head_id） | “新消息挂到哪”的位置指针：新消息链到它并随即将它前移；fork = 切游标（只切视角、不建节点，手术是 4-3 的内容） |
| 消费批次 | 一个逻辑 Turn 出队消费的消息列表；默认批次 = 队首 INTERRUPT/STEER 连续段 + 其后第一条非紧急消息 |
| 检查点 | 回合内层循环的固定判定点（pause gate、urgent 吸收、abort 判定）；控制机制在检查点收口，不在执行体中途硬切 |
| urgent 吸收 | 检查点的紧急消息处理：队首有 INTERRUPT 则连 drain INTERRUPT+STEER 挂树并 abort；仅 STEER 则只 drain 挂树、不 abort |

## 目标

掌握消息树（纯内存视角）与队列调度的机制面：steer / INTERRUPT /
pause / cancel 各作用于循环的哪个环节、在树上留下什么痕迹。

## 正文

### 树（纯内存视角）

每个 Agent 持有一棵消息级树（森林）：节点 = 消息，亲代链由
`parent_id` 构成；`current_head_id` 是游标。上下文组装沿游标上溯收集
路径（3-2）；`fork(msg_id)` 只切游标、从不创建节点——同一份历史可以同时
存在多条分支视角。**手动编辑树（五 op 手术）是 4-3 的事，本篇不细讲。**

### 队列与调度

优先级五级 `INTERRUPT > STEER > HIGH > NORMAL > LOW`（数值越小越
优先，同优先级 FIFO）；PROVIDER 永不入队（回合内产生），TOOL 结果回合
内直接挂树。默认出队批次：取队首的 INTERRUPT/STEER 连续段、再取其后的
第一条非紧急消息——所以一条紧跟 INTERRUPT 的普通消息会搭 INTERRUPT
的便车进入同一回合。`_dequeue` 可覆写（drain 合并、按来源分组等调度
策略），4-7 给出实现者视角。

### 循环上的四种控制（检查点语义）

回合内层循环的固定检查点：**pause gate → urgent 吸收 → abort 判定 →
provider_gen → 工具批次**。控制机制都在检查点收口：

| 机制 | 作用点 | 树上痕迹 |
|---|---|---|
| `steer()` | 检查点 drain STEER 挂树、**不 abort** | STEER 消息进入当轮链条，当轮下一个 provider_gen 的上下文可见 |
| INTERRUPT 消息 | 检查点 drain INTERRUPT+STEER 挂树并 **abort**：当前回合以 `cancelled` 收尾 | INTERRUPT 消息挂树，下一回合上下文可见 |
| `pause()` / `resume()` | pause gate：已出队批次照常挂树，但 provider_gen 不发起 | 无新节点（挂起期间无 LLM 调用） |
| `cancel()` | `_turn_abort` 与在途 provider_gen **竞速**，立即生效 | 已开始的流式产出以 partial 消息保留落盘 |

两点精确语义（主线示例实证）：

- **INTERRUPT 不硬切在途执行**：在途的生成或工具执行会走完，回合在
  “下一个 provider_gen 之前”的检查点 abort 收口；若当前响应已是最终
  响应（finish），回合自然收尾，INTERRUPT 直接开下一回合；
- **cancel 是竞速取消**：在途的 provider 调用被立即竞速中断，已累积
  内容以 partial 定型保留——“取消是正常终止不是错误，已产出不丢”。

### 循环上的钩子点巡礼

`on_enqueue`（入队前，可拒绝/改写）→ `before_turn`（出队批次挂树前，
附加式注入）→ `on_turn_append`（逐条挂树前）→ `after_turn`（所有路径
唯一收尾观察点）。1-4 用过其中两个；全集与 pattern 过滤见 4-6。

## 本篇不覆盖

- 五 op 树手术与 `turn_end` / `partial` / `synthetic` 三标记的完整
  语义——4-3；
- `_dequeue` 覆写、drain 合并、检查点序列的实现细节——4-7；
- 取消的决策面（`before_cancel` 拦截阻止取消）——4-6。

## 主线示例

`demo_mechanics.py` 连做四个实验并回放树；完整程序、查询输入和配置见下文：

```console
$ uv run python demo_mechanics.py
== 实验 1：steer（STEER 当轮可见、不打断）==
回合 status=completed（completed=未被打破）
== 实验 2：INTERRUPT（工具执行期打断当前回合）==
被打断回合 status=cancelled aborted=True（cancelled=被打断）
== 实验 3：pause / resume（挂起与恢复工作循环）==
pause 期间 current_turn 非 None: True（回合已创建、停在检查点，无 LLM 调用）
resume 后 status=completed
== 实验 4：cancel（中止执行；流式中断已产出保留落盘）==
cancel 后 status=cancelled；本回合已落树节点数=2（user + partial provider：中断不丢已产出）
── 消息树（head → 根，逆时间序）──
 12 provider  pri=NORMAL  turn_end=True
 11 user      pri=NORMAL  turn_end=False 讲一个很长很长的故事。
 10 provider  pri=NORMAL  turn_end=True 2+2 等于 4。我是一个简洁的中文助手，用尽量少的句子回答
  9 user      pri=NORMAL  turn_end=False 用一句话介绍你自己。
  7 user      pri=INTERRUPT turn_end=False 停下！先回答：2+2 等于几？
  8 tool      pri=NORMAL  turn_end=False exit_code: 0 --- stdout ---
  6 provider  pri=NORMAL  turn_end=False
  5 user      pri=NORMAL  turn_end=False 先用 bash 运行 sleep 20，然后再回答：天空为什
  4 provider  pri=NORMAL  turn_end=True 1 2 3 4 5 （收到导向）
  3 user      pri=STEER   turn_end=False 补充：数完请附一句“（收到导向）”。
  2 provider  pri=NORMAL  turn_end=True 1 2 3 4 5
  1 user      pri=NORMAL  turn_end=False 请数一数 1 到 5，每个数字一行。
```

读这棵树，对照四个机制的痕迹：

- **id 3（STEER）** 嵌在实验 1 的回合链内部（id 2 之后、id 4 之前）——
  steer 被当轮吸收，id 4 的回答带上了导向标记；
- **id 7（INTERRUPT）** 位于实验 2 的 bash 结果（id 8）之后——bash
  执行完、回合在检查点 abort（status=cancelled），INTERRUPT 挂树后由
  下一回合消费（id 10 回答了 2+2）；
- **实验 3（pause）**：挂起期间回合已创建（current_turn 非 None）但
  停在检查点——没有任何节点产生，resume 后正常完成；
- **实验 4（cancel）**：状态 cancelled，树上留下 2 个节点——中断时
  已开始的流式产出定型保留（partial 语义，4-3 展开 `partial` 标记）。

### 完整复现材料

下面每段都是复现所需的完整内容。`main.py`、三个 YAML 配置、`root.fya`
与 `demo_mechanics.py` 均在本文内；命令使用这些相对文件名，不需要
切换目录。模型调用需要由读者自行提供 `DEEPSEEK_API_KEY`；此处不包含凭证。

```python
# main.py
"""launch 导入本模块并等待 main() 返回 Runtime。"""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

```yaml
# providers.yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

```yaml
# models.yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

```yaml
# model-tags.yaml
tags:
  default: deepseek-flash
```

```yaml
# root.fya 的 front matter
description: 机制实验助手：配合观察队列与树上的控制痕迹。
model_tag: default
tools:
  - bash
---
$system_prompt:
你是简洁的中文助手，回答控制在五句话以内。
```

```python
# demo_mechanics.py
import asyncio

from flowing import launch
from flowing.message import Message, MessageKind, MessagePriority, TextBlock


def tree_lines(agent, limit=40):
    rows = []
    for message in agent.chain.walk(agent.current_head_id):
        head = ""
        for block in message.content:
            if getattr(block, "text", ""):
                head = block.text.replace("\n", " ")[:30]
                break
        rows.append(
            f"{message.id:>3} {message.kind.value:<9} "
            f"pri={message.priority.name:<7} turn_end={message.turn_end} {head}"
        )
    return rows[:limit]


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    print("== 实验 1：steer（STEER 当轮可见、不打断）==")
    task = asyncio.create_task(agent.query("请数一数 1 到 5，每个数字一行。"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await agent.steer("补充：数完请附一句“（收到导向）”。")
    result = await task
    print(f"回合 status={result.status}（completed=未被打破）")
    await asyncio.sleep(0.3)

    print("== 实验 2：INTERRUPT（工具执行期打断当前回合）==")
    task = asyncio.create_task(agent.query(
        "先用 bash 运行 sleep 20，然后再回答：天空为什么是蓝色的？（一句话）"
    ))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(8.0)
    await agent.enqueue_message(Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="停下！先回答：2+2 等于几？")],
        priority=MessagePriority.INTERRUPT,
    ))
    result = await task
    print(f"被打断回合 status={result.status} aborted={result.turn.aborted}（cancelled=被打断）")
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)

    print("== 实验 3：pause / resume（挂起与恢复工作循环）==")
    agent.pause()
    task = asyncio.create_task(agent.query("用一句话介绍你自己。"))
    await asyncio.sleep(1.5)
    print(f"pause 期间 current_turn 非 None: {agent.current_turn is not None}（回合已创建、停在检查点，无 LLM 调用）")
    agent.resume()
    result = await task
    print(f"resume 后 status={result.status}")

    print("== 实验 4：cancel（中止执行；流式中断已产出保留落盘）==")
    before = set(agent._messages)
    task = asyncio.create_task(agent.query("讲一个很长很长的故事。"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.0)
    await agent.cancel()
    result = await task
    new_nodes = [message_id for message_id in agent._messages if message_id not in before]
    print(f"cancel 后 status={result.status}；本回合已落树节点数={len(new_nodes)}（user + partial provider：中断不丢已产出）")

    print("── 消息树（head → 根，逆时间序）──")
    for line in tree_lines(agent):
        print(line)
    await runtime.shutdown()


asyncio.run(main())
```

使用已安装 Flowing 的 Python 环境执行：

```console
$ export DEEPSEEK_API_KEY="<your-api-key>"
$ uv run python demo_mechanics.py
```

程序中的四条查询、steer 内容、INTERRUPT 消息与 pause/cancel 操作均已
完整写在上方代码中。前文的输出是一次记录；模型文本与消息 id 会随运行状态和
提供方响应变化，因此这里只将机制状态作为示例，不承诺逐字相同。

## 小结

1. 树 = 亲代链森林 + head 游标；fork 只切视角，手术在 4-3；
2. 默认批次 = 队首紧急连续段 + 第一条非紧急消息；PROVIDER 永不入队；
3. steer 当轮吸收不打断；INTERRUPT 在检查点 abort 收口（不硬切在途执行）；
4. pause 停在检查点；cancel 竞速在途生成、partial 产出保留落盘。
