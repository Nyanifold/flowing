# 2-1 · 循环与并发：事件驱动、入口语义与取消

> 复现条件：Python 3.13+、uv、可用的 Flowing 包、DeepSeek API key，以及终端网络连接；实验 2 还需要系统提供 `bash` 与 `sleep`。
> 本篇末尾内联完整配置、提示词、数据、两个演示脚本、脚本输入和示例输出；无需查找其他文件。

> 前置：第 1-4 篇“ReAct 循环”

## 这篇讲什么

Agent 运行时的并发模型：事件驱动的消息循环、两类消息入口的
语义差异、协作式取消，以及多 Agent 系统中的死锁问题。

## 背景知识

事件驱动是并发系统的通用模型：外部刺激（用户输入、定时事件、
异步任务完成、其他 Agent 的回执）统一表示为事件，按序进入一个
队列，由单个循环消费。这个模型避免了多线程共享状态的大部分
复杂性：所有状态变更集中在循环内按序发生。

在本框架中，循环的持有者是 **Agent 实例**：每个 Agent 拥有自己的
消息队列与常驻工作循环。工具没有自己的循环——它运行在调用它的
Agent 的回合调用栈里。下文凡是说“谁等谁”，主语都是 Agent。

Agent 系统天然适合事件驱动——第 1 章的工具回执、外部触发、
用户输入本来就该进入同一个处理管线。代价是：循环内的执行
不能无限阻塞，否则后续事件全部排队；以及跨 Agent 等待需要小心
成环。

## 核心概念

### 两类入口：等待结果与不等待

框架通常提供两个级别的消息入口：

- **同步等待**：投递消息并等待本轮求解完成，返回结果对象。
  调用方拿到终态（完成 / 失败 / 被取消），语义简单但占用
  调用方；
- **异步投递**：仅把消息放入队列，立即返回标识。结果不返回
  给调用方，由后续的钩子、订阅或轮询获取。调用方不阻塞，
  适合事件源（定时器、回调）与 fire-and-forget 场景。

运行中修正模型行为还有第三种入口：**导向**——向正在执行的
回合注入一条高优先级消息，当轮上下文可见但不打断执行。

第一个完整脚本把三种入口放进同一次运行。脚本和代表性输出均在
文末内联：

```console
message() 已入队，消息 id=1（调用方不等回合结果）
回合已开始（current_turn 非 None）
steer() 已投递（STEER 优先级：当轮可见、不打断）
回合已收尾
query() 等到 TurnResult：status=completed
final_text 前 80 字：再次 glob 确认：notes 目录仍为 2 个文件（`使用说明.md`、`路线图.md`）。（收到导向）
```

逐行看：第一行是异步投递——只拿回消息 id，调用随即继续；
第二行证明回合已经在后台开始，但没人等它的结果；第三行的
导向消息投进了**正在执行的**回合；第 5-6 行说明 `query()`
等到回合结束并返回 `status=completed`，答复确认目录中有两个文件。

导向是否被接收的证据在同一示例的消息树里：

```console
  5 provider  pri=NORMAL turn_end=True notes 目录下共有 2 个文件…（收到导向）
  3 user      pri=STEER  turn_end=False 补充要求：回答末尾请附上『（收到导向）』。
```

第 3 条是 STEER 消息，第 5 条是它到达时正在执行的回合所生成的
答复，并以“（收到导向）”结尾。这正是导向要求的内容；导向消息
进入了当轮上下文，回合没有被打断，这就是导向语义的实证。

### 协作式取消

取消不是强杀：执行中的回合与异步任务在预设的检查点检查
取消信号，自行决定立即停止、完成当前步骤后停止、或忽略
（不可中断的关键段）。取消因此是正常结束的一种，不是错误；
已产生的中间结果按设计保留或丢弃。

取消的粒度通常是分层的：只终止当前回合 < 终止某类任务 <
终止整个 Agent。执行中的 Agent 需要登记在可遍历的注册表里，
取消操作才能按粒度找到目标。

第二个完整脚本依次做四个实验：导向、打断、暂停、取消。代码和
代表性输出均在文末内联：

```console
== 实验 2：INTERRUPT（工具执行期打断当前回合）==
被打断回合 status=cancelled aborted=True（cancelled=被打断）
== 实验 4：cancel（中止执行；流式中断已产出保留落盘）==
cancel 后 status=cancelled；本回合已落树节点数=2（user + partial provider：中断不丢已产出）
```

逐行看：实验 2 在一段 `sleep 20` 的命令执行期间发来一条最高
优先级的打断消息——在途命令正常走完，回合随后以“已取消”
收尾，打断消息由下一回合处理（代表性消息树中可以看到
它回答了新问题）；实验 4 在模型正在流式输出一篇长文时取消，
状态是“已取消”而非“错误”，且中断前已输出的内容以
“部分消息”的形式保留进了历史。取消是协作式的正常终止，
这两个实验展示了它的两种典型形态。

### 死锁

死锁在 Agent 系统中最常出现的形态是循环等待：Agent A 在自己
的回合内同步等待 Agent B 的结果，而 B 又在等待 A。每个 Agent
实例只有一个工作循环，A 等 B 意味着 A 的循环被阻塞，
B 若再需要 A 处理任何消息便永远无法完成。

规避规则只有一条且必须严格执行：**禁止在回合调用栈内发起
对本 Agent（或任何已处于等待链上的 Agent）的同步等待**。

```python
# ✗ 死锁：工具内同步等待本 Agent 的下一轮 —— 当前回合等下一轮，
#   下一轮等当前回合收尾
async def execute(self, *, caller):
    result = await caller.query("继续")     # 永远等不到

# ✓ 导向：补充要求随当轮上下文可见，不打断执行
async def execute(self, *, caller):
    await caller.steer("补充要求：……")
```

需要驱动时改用导向消息，或把等待移到回合之外。

## 常见误区

1. **在钩子或工具内同步等待本 Agent 的下一轮**。这是死锁的
   标准形态；应改用导向；
2. **把取消当异常处理**。取消是正常结束路径，中间结果与
   状态的收尾应按正常结束设计；
3. **忽略取消粒度**。整个 Agent、某类任务、当前回合是三档
   不同的操作，混用会误伤并行的其他工作。

## 练习

1. 为“下载并总结五个网页”设计入口选择：主流程用什么、
   下载完成事件用什么、途中用户补充要求用什么；
2. 找出这段伪代码的阻塞点：某工具的 `execute` 在一个活跃回合
   中执行 `result = await caller.query("总结一下")`——`query`
   的语义是“向本 Agent 投递一条消息，并等待本 Agent 的答复”。
   画出这条等待边，说明答复为什么永远等不到；
3. 在文末完整的第二个演示脚本中，为实验 4 设置一个可识别的故事
   开头；取消后检查消息树输出，确认该开头是否保留。

## 完整示例：入口语义、导向、打断、暂停与取消

以下代码与数据构成两个完整演示。在新建空目录中保存所列文件，设置
`DEEPSEEK_API_KEY` 环境变量，并确保当前 Python 环境可导入 Flowing。
将 `...` 替换为自己的 API key；不要将真实凭证写入文件。演示输入直接写在脚本中；不需要单独的输入
文件。

`pyproject.toml`：

```toml
[project]
name = "flowing-chapter-2-1"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["flowing-agent"]

[tool.uv]
package = false
```

`main.py`：

```python
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

`root.fya`（工具表和完整提示词）：

```yaml
description: 循环机制实验助手：配合观察入口语义与控制痕迹。
model_tag: default
tools:
  - read
  - glob
  - bash
---
$system_prompt:
你是项目问答助手。本项目的根目录绝对路径是 {{ env.PWD }}（调用文件工具时一律使用该绝对路径，不要猜其它目录）。用户让你执行命令时用 bash，查看文件时用 read / glob。回答控制在五句话以内。
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

在保存了本文所列文件的当前目录运行：

```sh
uv sync
export DEEPSEEK_API_KEY="..."
uv run python demo_entries.py
uv run python demo_mechanics.py
```

两个脚本会读取的全部笔记数据：

`notes/使用说明.md`：

```markdown
# 使用说明

本目录是一个演示用笔记库。

## 安装

需要 Python 3.13 或更高版本。

## 常用命令

- `uv sync`：安装依赖
- `uv run pytest`：运行测试

## 注意事项

凭证一律经环境变量持有，不要写进任何文件。
```

`notes/路线图.md`：

```markdown
# 路线图

- 2026-Q3：完成核心功能
- 2026-Q4：发布 1.0 版本
```

`demo_entries.py`：

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    mid = await agent.message(
        "请用 glob 查看 notes 目录（用系统提示里的绝对路径），然后说明里面有几个文件。")
    print(f"message() 已入队，消息 id={mid}（调用方不等回合结果）")
    while agent.current_turn is None:
        await asyncio.sleep(0.2)
    print("回合已开始（current_turn 非 None）")

    await agent.steer("补充要求：回答末尾请附上『（收到导向）』。")
    print("steer() 已投递（STEER 优先级：当轮可见、不打断）")
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    print("回合已收尾")

    result = await agent.query(
        "再次用 glob 确认 notes 目录的文件数量，一句话回答。")
    print(f"query() 等到 TurnResult：status={result.status}")
    print(f"final_text 前 80 字：{result.final_text[:80]}")

    print("── 消息树（head → 根，逆时间序；pri=STEER 即导向消息）──")
    for m in agent.chain.walk(agent.current_head_id):
        head = ""
        for b in m.content:
            if getattr(b, "text", ""):
                t = b.text.replace("\n", " ")
                head = t if len(t) <= 62 else f"{t[:36]}…{t[-22:]}"
                break
        print(f"{m.id:>3} {m.kind.value:<9} pri={m.priority.name:<6} "
              f"turn_end={m.turn_end} {head}")

    await runtime.shutdown()


asyncio.run(main())
```

`demo_mechanics.py`：

```python
import asyncio

from flowing import launch
from flowing.message import Message, MessageKind, MessagePriority, TextBlock


def tree_lines(agent, limit=40):
    rows = []
    for m in agent.chain.walk(agent.current_head_id):
        head = ""
        for b in m.content:
            if getattr(b, "text", ""):
                t = b.text.replace("\n", " ")
                head = t[:30]
                break
        rows.append(f"{m.id:>3} {m.kind.value:<9} pri={m.priority.name:<7} "
                    f"turn_end={m.turn_end} {head}")
    return rows[:limit]


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    print("== 实验 1：steer（STEER 当轮可见、不打断）==")
    t = asyncio.create_task(agent.query("请数一数 1 到 5，每个数字一行。"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await agent.steer("补充：数完请附一句“（收到导向）”。")
    r = await t
    print(f"回合 status={r.status}（completed=未被打破）")
    await asyncio.sleep(0.3)

    print("== 实验 2：INTERRUPT（工具执行期打断当前回合）==")
    t = asyncio.create_task(agent.query(
        "先用 bash 运行 sleep 20，然后再回答：天空为什么是蓝色的？（一句话）"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(8.0)
    await agent.enqueue_message(Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="停下！先回答：2+2 等于几？")],
        priority=MessagePriority.INTERRUPT))
    r = await t
    print(f"被打断回合 status={r.status} aborted={r.turn.aborted}（cancelled=被打断）")
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)

    print("== 实验 3：pause / resume（挂起与恢复工作循环）==")
    agent.pause()
    t = asyncio.create_task(agent.query("用一句话介绍你自己。"))
    await asyncio.sleep(1.5)
    print(f"pause 期间 current_turn 非 None: {agent.current_turn is not None}"
          f"（回合已创建、停在检查点，无 LLM 调用）")
    agent.resume()
    r = await t
    print(f"resume 后 status={r.status}")

    print("== 实验 4：cancel（中止执行；流式中断已产出保留落盘）==")
    before = set(agent._messages)
    t = asyncio.create_task(agent.query("讲一个很长很长的故事。"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(1.0)
    await agent.cancel()
    r = await t
    new_nodes = [mid for mid in agent._messages if mid not in before]
    print(f"cancel 后 status={r.status}；本回合已落树节点数={len(new_nodes)}"
          f"（user + partial provider：中断不丢已产出）")

    print("── 消息树（head → 根，逆时间序）──")
    for line in tree_lines(agent):
        print(line)
    await runtime.shutdown()


asyncio.run(main())
```

第一个脚本的全部输入都写在脚本中：先调用 `message()` 发起目录检查，
再通过 `steer()` 要求答复带上“（收到导向）”，最后用 `query()` 再次
查询目录数量。第二个脚本的四个模型输入也都在代码中逐条列明；实验 2
发出 `sleep 20` 后等待 8 秒再发送 INTERRUPT，实验 3 暂停 1.5 秒后
恢复，实验 4 在生成开始后取消。

运行 `uv run python demo_entries.py` 的代表性完整输出如下。动态回答与
消息编号可能不同；路径统一用 `<project-root>` 占位，不包含本机路径：

```text
message() 已入队，消息 id=1（调用方不等回合结果）
回合已开始（current_turn 非 None）
steer() 已投递（STEER 优先级：当轮可见、不打断）
回合已收尾
query() 等到 TurnResult：status=completed
final_text 前 80 字：再次 glob 确认：notes 目录仍为 2 个文件（`使用说明.md`、`路线图.md`）。（收到导向）
── 消息树（head → 根，逆时间序；pri=STEER 即导向消息）──
  9 provider  pri=NORMAL turn_end=True 再次 glob 确认：notes 目录仍为 2 个文件（`使用说明.md`、`路线图.md`）。（收到导向）
  8 tool      pri=NORMAL turn_end=False <project-root>/notes/路线图.md
  7 provider  pri=NORMAL turn_end=False
  6 user      pri=NORMAL turn_end=False 再次用 glob 确认 notes 目录的文件数量，一句话回答。
  5 provider  pri=NORMAL turn_end=True notes 目录下共有 2 个文件：`使用说明.md` 和 `路线图.md`。（收到导向）
  3 user      pri=STEER  turn_end=False 补充要求：回答末尾请附上『（收到导向）』。
  4 tool      pri=NORMAL turn_end=False <project-root>/notes/路线图.md
  2 provider  pri=NORMAL turn_end=False
  1 user      pri=NORMAL turn_end=False 请用 glob 查看 notes 目录（用系统提示里的绝对路径），然后说明里面有几个文件。
```

运行 `uv run python demo_mechanics.py` 的代表性输出如下。模型返回文本、
时序与消息编号会变化；取消是否恰好保留部分 provider 内容取决于取消
到达时机：

```text
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
 10 provider  pri=NORMAL  turn_end=True 2+2=4。
  9 user      pri=NORMAL  turn_end=False 用一句话介绍你自己。
  7 user      pri=INTERRUPT turn_end=False 停下！先回答：2+2 等于几？
  8 tool      pri=NORMAL  turn_end=False exit_code: 0 --- stdout --- --
  6 provider  pri=NORMAL  turn_end=False
  5 user      pri=NORMAL  turn_end=False 先用 bash 运行 sleep 20，然后再回答：天空为什么是蓝色的？（一句话）
  4 provider  pri=NORMAL  turn_end=True 1 2 3 4 5 （收到导向）
  3 user      pri=STEER   turn_end=False 补充：数完请附一句“（收到导向）”。
  2 provider  pri=NORMAL  turn_end=True 1 2 3 4 5
  1 user      pri=NORMAL  turn_end=False 请数一数 1 到 5，每个数字一行。
```

## 小结

1. 事件驱动模型统一处理输入、回执与外部事件；
2. 同步等待与异步投递服务于不同调用方；导向解决回合内驱动；
3. 取消是协作式信号，按粒度分层，取消前可拦截；
4. 回合栈内禁止同步等待，这是死锁规避的唯一硬规则。
