# 3-2 · 持久化：会话状态、write-behind 与崩溃一致性

> 示例运行前提：环境中已安装 Flowing CLI，并已自行设置 `DEEPSEEK_API_KEY`；下文逐项给出运行所需文件全文。
> 正常对话：`uv run flowing repl .`；崩溃演练：完成并退出一轮后运行 `uv run python demo_crash.py`，再运行对话命令验证恢复。
> 请在新建的隔离工作目录内按顺序运行；崩溃脚本会立即终止其 Python 进程，不执行关闭流程。

> 前置：第 1-1 篇“智能体运行时”

## 这篇讲什么

会话状态的持久化设计：哪些对象需要落盘、记录流与
write-behind 两个通用机制、崩溃一致性窗口，以及会话身份的
恢复语义。

## 背景知识

进程内存是易失的：重启即失。Agent 系统的“对话记忆”“业务
进度”“组件身份”通常要求跨进程存活，这就需要持久化。持久化
设计要回答四个问题：存什么、什么形态、何时写、崩溃时保证什么。

### 完整示例材料

以下代码块标题给出应创建的相对文件名，块内均为完整文件内容。请在一个
全新的工作目录中按顺序创建这些文件并运行命令；模型凭证只通过环境变量
`DEEPSEEK_API_KEY` 提供。

#### `root.fya`

```yaml
description: 持久化演示助手：记住用户告知的内容。
model_tag: default
---
$system_prompt:
你是记忆演示助手。用户让你记住信息时，用一句话复述确认；被问起时准确
复述。回答控制在一句话以内。
```

#### `main.py`

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

#### `providers.yaml`

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

#### `models.yaml`

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

#### `model-tags.yaml`

```yaml
tags:
  default: deepseek-flash
```

#### `demo_crash.py`

```python
import asyncio
import os

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    result = await agent.query("再记住一个颜色：紫色。")
    print(f"回合完成 status={result.status}；现在模拟 kill -9（os._exit(9)）", flush=True)
    os._exit(9)


asyncio.run(main())
```

#### 三个进程的输入与可见输出

第一进程运行 `uv run flowing repl .`，输入记忆请求，然后正常退出：

```console
(agent-main)>>> 记住数字 731。
[thinking] (reasoning trace omitted)
好的，我记住了：731。
(agent-main)>>> /exit
```

第二进程运行 `uv run python demo_crash.py`。该程序直接调用 `os._exit(9)`：

```text
回合完成 status=completed；现在模拟 kill -9（os._exit(9)）
```

第三进程再次运行 `uv run flowing repl .`，提问后正常退出：

```console
(agent-main)>>> 我让你记的数字和颜色分别是什么？
[thinking] (reasoning trace omitted)
你让我记的数字是731，颜色是紫色。
(agent-main)>>> /exit
```

这次记录中的行数检查结果为：

```text
5 .flowing/agent-main/tree.jsonl
```

## 核心概念

### 存什么：消息、状态、身份

| 对象 | 内容 | 为什么存 |
|---|---|---|
| 消息历史 | 每一轮的用户输入、模型响应、工具结果 | 对话即产品；历史是下一轮请求的输入 |
| 业务状态 | 工具写入的业务数据（偏好、清单、进度） | 跨会话延续的上下文 |
| 身份 | 组件的标识、创建参数、关系（谁是谁的子级） | “找回同一个组件”依赖身份连续 |

相应地，执行期的瞬态不需要存：正在进行的回合、等待中的请求、
内存计数器。崩溃后这些按“从未发生”处理。

完成第一轮对话后，工作目录中会生成 `.flowing/` 状态目录；其布局与三对象
的划分一一对应：

```text
.flowing/
├── core.jsonl              # 运行时级核心状态（组件名录）
└── agent-main/             # 一个 Agent 一个会话目录
    ├── meta.json           # 身份：来源声明、父级、创建时间、创建参数
    ├── tree.jsonl          # 消息历史：一行一条消息
    └── …                   # 业务状态文件（本例未产生）
```

### 记录流：追加为主、定期清洗

历史类的数据以**记录流**存储：新记录追加到文件尾部，改动历史通过
追加变更记录表达（删除=墓碑行），而不是抹除原文。后端会定期做
清洗重写（墓碑行与被标记删除的记录积累到阈值后整文件重写）——
重放结果在重写前后严格一致。收益：

- 写入以顺序 I/O 为主，热路径开销恒定；
- 历史即审计日志，删除以标记表达、可回溯；
- 崩溃恢复 = 顺序重放，语义简单可证。

```text
操作顺序示意（不是可直接写入的记录内容）：追加消息 A → 追加消息 B → 追加删除 A 的墓碑记录。
```

持久化的记录文件以一行一条消息或变更记录的形式保存；首行记录格式版本。

### write-behind 与崩溃窗口

“写操作先改内存、由后台任务异步落盘”即 write-behind（数据库
缓冲区、操作系统的页缓存同属一族）。它换来热路径的低延迟，
代价是一个一致性窗口：已提交未落盘的记录在进程崩溃时丢失。

工程上的处理是明确的：窗口要足够小（毫秒级），语义上“内存
即权威”——权威随进程一起消亡时，重放到的状态自洽，如同丢失
的操作从未发生。正常退出路径必须有排空步骤（把队列写尽再关），
窗口只在异常死亡时出现。

下面的伪代码只展示概念顺序；`state` 与 `runtime` 是占位符，
不是可直接运行的完整程序。

```python
state["prefs"] = {"theme": "dark"}   # ① 内存更新 + 提交写请求（立即返回）
                                     # ② 后台任务异步落盘
await runtime.shutdown()             # ③ 正常退出：排空后关闭，窗口关闭
```

### 会话身份与恢复

“恢复”要精确成“按身份重建对象”：身份（标识、参数、关系）
持久化，内存对象可以销毁后重建。恢复后的组件应当与崩溃前
等价——历史、状态、关系都在；只有正在执行的回合按取消处理。

完整崩溃演练由三个进程依次完成；其中的输入、可见回复与崩溃脚本输出
已逐项内联在上方。三个命令依次执行：

```console
$ uv run flowing repl .          # ① 让 Agent 记住一个数字，/exit 正常退出
$ uv run python demo_crash.py    # ② 再记一个颜色，随后进程模拟 kill -9
$ uv run flowing repl .          # ③ 重启，问它两个问题验证恢复
```

请在没有既有 `.flowing/` 状态的全新工作目录中按①至③顺序复现。

逐项看：第一个进程里你让它记数字，它答“我记住了：731”；
第三个进程里你只问“数字和颜色分别是什么”，它答出
“数字是731，颜色是紫色”。数字来自第一个正常退出的进程，
颜色来自第二个**异常死亡**的进程——`os._exit(9)` 跳过了全部
正常关闭逻辑，但崩溃前完成的回合记录已经落盘；第三个进程
启动时重放两个进程留下的记录，汇成同一份历史，于是两个事实
都能复述。崩溃后的行数检查结果（上方已内联）显示记录文件有 5 行
（元数据行 + 两个回合的消息）；崩溃
窗口内的丢失没有发生，且即使发生也不破坏一致性——重放到的
状态永远自洽。

## 常见误区

1. **同步写盘保平安**。热路径延迟翻倍，收益只是缩小了本就毫秒
   级的窗口；正确做法是 write-behind + 正常退出排空；
2. **删除即抹除**。历史类数据应追加墓碑；抹除式删除破坏审计
   与重放语义；
3. **把瞬态也持久化**。进行中的回合、等待句柄属瞬态；混入持久化
   让恢复语义复杂化且毫无意义。

## 练习

1. 为“跨天续聊的陪聊 Agent”列出持久化清单：消息、用户偏好、
   当日计数器各属哪类；瞬态有哪些；
2. 写一段伪代码：正常退出时的排空序列（停止接收 → 写尽队列 →
   关闭文件），说明每步防住的故障；
3. 在另一份全新的工作目录中重复第一、第三进程但不执行第二进程，
   再问同样的问题，说明颜色为何不再能从崩溃前记录中恢复。

## 小结

1. 持久化三对象：消息、业务状态、身份；瞬态按“从未发生”处理；
2. 历史类数据以记录流存储：追加为主、后端定期清洗重写，重放恢复语义不变；
3. write-behind 以毫秒级崩溃窗口换热路径性能；正常退出必须
   排空；
4. 恢复 = 按身份重建；恢复后与崩溃前等价，除执行中回合按取消计。
