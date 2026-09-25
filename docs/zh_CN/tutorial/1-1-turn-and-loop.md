# 1-1 · 回合与循环

## 前置阅读

[0-2 使用内置工具](0-2-use-builtin-tools.md)（你已见过 Agent 调用 glob/read
完成一轮问答）。本篇以内联给出完整项目代码、配置、系统提示词、笔记、
程序输入和输出。请在自行创建的项目目录中保存这些材料后运行命令。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| ReAct 循环 | 模型“生成 → 发起工具调用 → 观察工具结果 → 再生成”直到产出最终答复的循环——本篇的直觉模型 |
| 常驻工作循环 | 每个 Agent 实例创建即启动的串行消费循环：出队消息、开逻辑 Turn |
| 逻辑 Turn（回合） | 消费一条（或一批）消息到 `turn_end=True` 的 PROVIDER 消息为止的执行阶段，由 `TurnContext` / `TurnResult` 表示 |
| `TurnResult` | 回合产物：`status`（completed/blocked/cancelled/error）+ `final_text` + `token_usage`；四种结局都会 resolve 给等待者 |
| 死锁禁令 | 在当前回合的调用栈内 `await query()` 必死锁；回合内驱动用 `steer()` |

## 目标

理解 ReAct 循环——智能体系统最基本的原理：消息如何驱动回合；并分清
三个消息入口 `query` / `message` / `steer` 的等待语义。

## 正文

### ReAct 直觉：消息驱动回合

0-2 的会话已经展示了循环本体：模型生成（“我先看看项目结构”）→ 发起
工具调用（`[tool_call]` 逐调用成行、参数完整）→ 观察工具结果（`[tool:completed]`）
→ 再生成（基于读到的内容回答）。在 flowing 里，这条循环不靠带外信号
驱动，全靠**消息**：用户输入、模型响应、工具结果、外部事件统一表示为
`Message`，Agent 的常驻工作循环串行消费队列，每条（批）消息驱动一个
逻辑 Turn——直到响应 `finish=True`（Provider 完成了本次响应、无待执行
工具调用）收尾。本篇只涉及两类消息（文本输入输出、工具调用），消息的
完整类型是 1-2 的主题。

### 入队三入口：按是否需要结果选择

```python
result = await agent.query("帮我查订单 4521")   # 等回合产物（TurnResult）
mid = await agent.message("稍后提醒我喝水")      # fire-and-forget，只要消息 id
await agent.steer("预算上限改为 500")            # 回合进行中导向（STEER 优先级）
```

- `query()` 打包入队并等待“包含我这条消息的回合”的 `TurnResult`；
- `message()` 散装入队立即返回消息 id，回合结果不回头找你；
- `steer()` 投一条 STEER 优先级的消息：回合进行中投递会被**当轮吸收**
  ——当轮 LLM 调用的上下文即可见、不打断当前回合（演示见主线示例）。

### TurnResult：四种结局都不挂起

`result.status` 四值：`"completed"`（自然完成）/ `"blocked"`
（被 `Intercepted` 硬阻断）/ `"cancelled"`（取消/销毁/撤回）/
`"error"`（未捕获异常终止）。**四种结局都会 resolve 给等待者**——崩溃、
取消、`destroy()` 都不会让 `query()` 的调用方挂起（错误的完整分类是
4-6 的主题）。

### 死锁禁令（新手最常踩的坑）

在当前回合的调用栈内（任何钩子、工具 `execute` 内）`await query()` 必
死锁——回合收尾要等钩子返回，钩子要等下一回合，下一回合要等当前回合
收尾。跨 Agent 等待成环（A 等 B、B 等 A）同理。**回合内需要驱动，用
`steer()`，不要 `query()`**：

```python
# ✗ 禁止：工具 / 钩子内等待本 Agent 的新回合 → 死锁
async def execute(self, *, caller: Agent):
    result = await caller.query("继续下一步")   # 死锁！

# ✓ 正确：导向消息当轮可见、不打断
async def execute(self, *, caller: Agent):
    await caller.steer("补充要求：……")
```

### priority 与 INTERRUPT（一句话版）

消息带优先级：`INTERRUPT > STEER > HIGH > NORMAL > LOW`，同优先级按
入队顺序。回合进行中到达的 INTERRUPT 消息会**打断**当前回合（当前回合
以 abort 收尾、新回合从 INTERRUPT 消息开始）；STEER 不打断、当轮吸收；
其余优先级正常排队。队列与调度的机制面是 3-1 的主题。

## 本篇不覆盖

- `_run_turn` 内层循环、`_dequeue` 覆写、drain 合并、检查点细节——4-7
  （实现者视角）；
- 消息类型全集与 ContentBlock——1-2；
- 优先级与打断在队列/树上的机制面——3-1；
- 取消语义全集——4-6。

## 主线示例

以下是可独立重建的项目材料。将 Python 入口保存为 `main.py`：

```python
from flowing import Runtime


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

把下面声明保存为 `root.fya`，三段配置依次保存为 `providers.yaml`、
`models.yaml`、`model-tags.yaml`。凭证保持环境变量占位符：

```yaml
description: 项目问答助手：能查看目录、读取文件并总结。
model_tag: default
tools:
  - read
  - glob
---
$system_prompt:
你是项目问答助手。本项目的根目录绝对路径是 {{ env.PWD }}。
文件检查必须先用 glob 查看，再用 read 读取，并依据读取到的内容回答；不要编造。
回答控制在五句话以内。
```

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

```yaml
tags:
  default: deepseek-flash
```

以下两份笔记分别保存为 `notes/使用说明.md`、`notes/路线图.md`：

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

```markdown
# 路线图

- 2026-Q3：完成核心功能
- 2026-Q4：发布 1.0 版本
```

再把下方完整脚本保存为 `demo_entries.py`。它按顺序调用 `message()`、
`steer()`、`query()` 并打印消息树：

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    mid = await agent.message(
        "请用 glob 查看 notes 目录（使用系统提示里的绝对路径），然后说明有几个文件。"
    )
    print(f"message() 已入队，消息 id={mid}（调用方不等回合结果）")
    while agent.current_turn is None:
        await asyncio.sleep(0.2)
    print("回合已开始（current_turn 非 None）")

    await agent.steer("补充要求：回答末尾请附上『（收到导向）』。")
    print("steer() 已投递（STEER 优先级：当轮可见、不打断）")
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    print("回合已收尾")

    result = await agent.query("再次用 glob 确认 notes 目录的文件数量，一句话回答。")
    print(f"query() 等到 TurnResult：status={result.status}")
    print(f"final_text 前 80 字：{result.final_text[:80]}")

    print("── 消息树（head → 根，逆时间序；pri=STEER 即导向消息）──")
    for m in agent.chain.walk(agent.current_head_id):
        head = ""
        for block in m.content:
            if getattr(block, "text", ""):
                text = block.text.replace("\n", " ")
                head = text if len(text) <= 62 else f"{text[:36]}…{text[-22:]}"
                break
        print(f"{m.id:>3} {m.kind.value:<9} pri={m.priority.name:<6} "
              f"turn_end={m.turn_end} {head}")

    await runtime.shutdown()


asyncio.run(main())
```

设置 `DEEPSEEK_API_KEY` 后，从该项目根目录运行脚本：

```console
$ uv run python demo_entries.py
message() 已入队，消息 id=1（调用方不等回合结果）
回合已开始（current_turn 非 None）
steer() 已投递（STEER 优先级：当轮可见、不打断）
回合已收尾
query() 等到 TurnResult：status=completed
final_text 前 80 字：notes 目录下依然只有 2 个文件：`使用说明.md` 和 `路线图.md`。（收到导向）
── 消息树（head → 根，逆时间序；pri=STEER 即导向消息）──
  9 provider  pri=NORMAL turn_end=True notes 目录下依然只有 2 个文件：…（收到导向）
  8 tool      pri=NORMAL turn_end=False <project-root>/notes/路线图.md
  7 provider  pri=NORMAL turn_end=False
  6 user      pri=NORMAL turn_end=False 再次用 glob 确认 notes 目录的文件数量，一句话回答。
  5 provider  pri=NORMAL turn_end=True notes 目录下共有 2 个文件：…（收到导向）
  3 user      pri=STEER  turn_end=False 补充要求：回答末尾请附上『（收到导向）』。
  4 tool      pri=NORMAL turn_end=False <project-root>/notes/路线图.md
  2 provider  pri=NORMAL turn_end=False 我先查看 notes 目录结构。
  1 user      pri=NORMAL turn_end=False 请用 glob 查看 notes 目录…
```

读这段留档，对照三个入口：

1. `message()`（id 1）：只拿回消息 id=1，调用方随后观察到
   `current_turn` 由空变非空——回合开始了，但没人等它的结果；
2. `steer()`（id 3，pri=STEER）：落在第一个回合的链条内部（id 2 的
   PROVIDER 之后、id 5 的收尾 PROVIDER 之前）——它被当轮吸收，id 5 的
   回答末尾出现了导向要求的『（收到导向）』标记，回合没有被打断；
3. `query()`（id 6）：等到 `status=completed` 的 `TurnResult`，
   `final_text` 是实查结果。注意 id 9 的回答也带了标记——STEER 消息
   进了树，后续回合的上下文里依然可见（树与上下文组装是 3-1 / 3-2
   的主题）。

树上还能读出回合边界：每个 `turn_end=True` 的 PROVIDER 消息是一个回合
的收尾（id 5 与 id 9 各收一个回合）。

本篇所需的入口、Agent 定义、模型配置、笔记、完整脚本、输入和示例输出均已
内联；创建文件时使用代码块上方标明的相对文件名即可。留档中的模型文本与
消息 id 会因运行而异；此外，这段时序示例依赖首个回合在 `steer()` 投递时
仍处于进行中，若回合更早结束，导向内容就不能保证出现在首个答复中。

## 小结

1. ReAct 循环由消息驱动：生成 → 工具调用 → 观察 → 再生成，直到 finish；
2. 三入口按等待语义选择：`query` 等结果、`message` 只要 id、`steer` 当轮导向；
3. `TurnResult` 四种结局都 resolve，调用方永不挂起；
4. 死锁禁令：回合栈内禁 `query()`，驱动用 `steer()`。
