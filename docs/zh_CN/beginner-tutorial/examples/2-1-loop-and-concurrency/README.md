# 示例：2-1 循环与并发

两个脚本先对照 `message`、`steer`、`query` 三种入口，再演示 `steer`、`INTERRUPT`、暂停/恢复与取消。下文完整内联了所需代码、配置、提示词、输入与示例输出。

运行环境需要 Python 3.13 或更高版本、uv，以及可使用 Flowing CLI 和包的 Python 环境。模型调用还需要在 shell 中设置你自己的 `DEEPSEEK_API_KEY`；本文不含真实凭证。请在全新的工作目录下按下文相对文件名创建文件，并从该目录执行命令。

## 复现

### 对照三种入口

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run python demo_entries.py
```

### 观察循环控制机制

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run python demo_mechanics.py
```

机制脚本会使用异步等待、20 秒 shell sleep 和取消操作，运行时序及模型生成文本可能变化。目录枚举交互所需的笔记文件名在下方给出；交互不会读取文件正文。

## 完整内联材料

下方每个代码块都包含本示例所用相对文件的完整内容。输出代码块是示例留档；模型答复和主机相关信息可能变化。

### `demo_entries.py`

```python
"""query / message / steer 三入口行为对照。

运行：uv run python demo_entries.py
"""
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    # ── 1. message()：fire-and-forget，只拿回消息 id，不等待回合 ──
    mid = await agent.message(
        "请用 glob 查看 notes 目录（用系统提示里的绝对路径），然后说明里面有几个文件。")
    print(f"message() 已入队，消息 id={mid}（调用方不等回合结果）")
    while agent.current_turn is None:   # 等回合开始
        await asyncio.sleep(0.2)
    print("回合已开始（current_turn 非 None）")

    # ── 2. steer()：回合进行中导向——STEER 当轮上下文可见、不打断 ──
    await agent.steer("补充要求：回答末尾请附上『（收到导向）』。")
    print("steer() 已投递（STEER 优先级：当轮可见、不打断）")
    while agent.current_turn is not None:   # 等本回合收尾
        await asyncio.sleep(0.2)
    print("回合已收尾")

    # ── 3. query()：等待“包含我这条消息”的回合产物 TurnResult ──
    result = await agent.query("再次用 glob 确认 notes 目录的文件数量，一句话回答。")
    print(f"query() 等到 TurnResult：status={result.status}")
    print(f"final_text 前 80 字：{result.final_text[:80]}")

    # ── 4. 树上游标回放：看三入口各自在树上留下的痕迹 ──
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

### `demo_entries_output.txt`

```text
message() 已入队，消息 id=1（调用方不等回合结果）
回合已开始（current_turn 非 None）
steer() 已投递（STEER 优先级：当轮可见、不打断）
回合已收尾
query() 等到 TurnResult：status=completed
final_text 前 80 字：再次 glob 确认：notes 目录仍为 2 个文件（`使用说明.md`、`路线图.md`）。（收到导向）
── 消息树（head → 根，逆时间序；pri=STEER 即导向消息）──
  9 provider  pri=NORMAL turn_end=True 再次 glob 确认：notes 目录仍为 2 个文件（`使用说明.md`、`路线图.md`）。（收到导向）
  8 tool      pri=NORMAL turn_end=False ./notes/路线图.md
  7 provider  pri=NORMAL turn_end=False 
  6 user      pri=NORMAL turn_end=False 再次用 glob 确认 notes 目录的文件数量，一句话回答。
  5 provider  pri=NORMAL turn_end=True notes 目录下共有 2 个文件：`使用说明.md` 和 `路线图.m…文档，没有子目录或其他类型文件。（收到导向）
  3 user      pri=STEER  turn_end=False 补充要求：回答末尾请附上『（收到导向）』。
  4 tool      pri=NORMAL turn_end=False ./notes/路线图.md
  2 provider  pri=NORMAL turn_end=False 
  1 user      pri=NORMAL turn_end=False 请用 glob 查看 notes 目录（用系统提示里的绝对路径），然后说明里面有几个文件。
```

### `demo_mechanics.py`

```python
"""队列与循环机制实验：steer / INTERRUPT / pause-resume / cancel。

每个实验打印“机制行为 + 树上痕迹”。运行：uv run python demo_mechanics.py
"""
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

    # ── 实验 1：steer 当轮吸收（不打断）──
    print("== 实验 1：steer（STEER 当轮可见、不打断）==")
    t = asyncio.create_task(agent.query("请数一数 1 到 5，每个数字一行。"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await agent.steer("补充：数完请附一句“（收到导向）”。")
    r = await t
    print(f"回合 status={r.status}（completed=未被打破）")
    await asyncio.sleep(0.3)   # 等 steer 消息随批次挂树

    # ── 实验 2：INTERRUPT 在工具执行期打断当前回合 ──
    print("== 实验 2：INTERRUPT（工具执行期打断当前回合）==")
    t = asyncio.create_task(agent.query(
        "先用 bash 运行 sleep 20，然后再回答：天空为什么是蓝色的？（一句话）"))
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    await asyncio.sleep(8.0)   # 首轮生成完毕、bash sleep 20 执行中
    await agent.enqueue_message(Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="停下！先回答：2+2 等于几？")],
        priority=MessagePriority.INTERRUPT))
    r = await t
    print(f"被打断回合 status={r.status} aborted={r.turn.aborted}（cancelled=被打断）")
    while agent.current_turn is not None:   # 等 INTERRUPT 新回合收尾
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)

    # ── 实验 3：pause / resume ──
    print("== 实验 3：pause / resume（挂起与恢复工作循环）==")
    agent.pause()
    t = asyncio.create_task(agent.query("用一句话介绍你自己。"))
    await asyncio.sleep(1.5)
    # pause 挂起工作循环检查点：批次已出队挂树，但 provider_gen 未发起
    print(f"pause 期间 current_turn 非 None: {agent.current_turn is not None}"
          f"（回合已创建、停在检查点，无 LLM 调用）")
    agent.resume()
    r = await t
    print(f"resume 后 status={r.status}")

    # ── 实验 4：cancel（协作式取消：在途生成被竞速中断、已产出保留）──
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

    # ── 树上游标回放：以上机制各自的痕迹 ──
    print("── 消息树（head → 根，逆时间序）──")
    for line in tree_lines(agent):
        print(line)
    await runtime.shutdown()


asyncio.run(main())
```

### `demo_mechanics_output.txt`

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
 10 provider  pri=NORMAL  turn_end=True 2+2=4。  我是本项目的问答助手，负责在需要时查阅文件、
  9 user      pri=NORMAL  turn_end=False 用一句话介绍你自己。
  7 user      pri=INTERRUPT turn_end=False 停下！先回答：2+2 等于几？
  8 tool      pri=NORMAL  turn_end=False exit_code: 0 --- stdout --- --
  6 provider  pri=NORMAL  turn_end=False 
  5 user      pri=NORMAL  turn_end=False 先用 bash 运行 sleep 20，然后再回答：天空为什
  4 provider  pri=NORMAL  turn_end=True 1 2 3 4 5  （收到导向）
  3 user      pri=STEER   turn_end=False 补充：数完请附一句“（收到导向）”。
  2 provider  pri=NORMAL  turn_end=True 1 2 3 4 5  一共 5 行，每行一个数字。
  1 user      pri=NORMAL  turn_end=False 请数一数 1 到 5，每个数字一行。
```

### `main.py`

```python
"""参数与 setup() 演示子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh",
               resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # mount 之前 provide：影响装配的值在挂载前提供（链式语义：沿亲代链上溯查找）
    runtime.provide("timezone", "Asia/Shanghai")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        # CLI 的 --key value 经 launch 原样透传 main(**kwargs)，
        # 再由 main 决定哪些参数交给 mount（→ 创建管线 → setup(**args)）
        kwargs: dict = {}
        if user_name is not None:
            kwargs["user_name"] = user_name
        if locale != "zh":
            kwargs["locale"] = locale
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
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
description: 循环机制实验助手：配合观察入口语义与控制痕迹。
model_tag: default
tools:
  - read
  - glob
  - bash   # 机制实验需要一段可打断的工具执行（sleep）
---
$system_prompt:
你是项目问答助手。本项目的根目录绝对路径是 {{ env.PWD }}
（调用文件工具时一律使用该绝对路径，不要猜其它目录）。
用户让你执行命令时用 bash，查看文件时用 read / glob。
回答控制在五句话以内。
```

### `notes/使用说明.md`（空文件占位）

该交互会用到这个文件名，但不会读取文件内容。在此相对路径创建空文件即可。

### `notes/路线图.md`（空文件占位）

该交互会用到这个文件名，但不会读取文件内容。在此相对路径创建空文件即可。
