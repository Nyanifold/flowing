# 4-1 · 核心状态与消息持久化

## 前置阅读

[1-8 运行时与 Agent 目录](1-8-runtime-and-agent-dirs.md)（Runtime 与 Agent
的关系）和[1-1 回合与循环](1-1-turn-and-loop.md)（`TurnContext`）。Python
环境中须已安装 Flowing，并设置环境变量 `DEEPSEEK_API_KEY`。本篇配置通过
环境变量占位符读取凭证。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 记录流 | 消息树的持久化形态：记录逐行追加，消息完整后提交。 |
| write-behind | 提交在记录进入队列后即返回；单个后台任务串行写入，正常关闭前排空队列。 |
| 崩溃窗口 | 尾部记录排队后、写入前的时间段；窗口内丢失与进程内存权威同时消失是一致的。 |
| 身份字段 | Agent 元数据中的 `agent_type`、`parent_agent_id`、`created_at` 与 `args`。 |
| 重放恢复 | 重放已持久化的消息与状态记录，重建运行状态，供下一回合组装上下文。 |

## 目标

介绍核心状态有哪些会持久化、以什么形式保存，以及哪些执行期细节刻意不持久化。
持久化是核心机制，不是策略。

## 正文

### 消息持久化

消息树以 JSON Lines 记录流保存。首条记录标识格式版本，其余记录表示消息或
消息历史变更；每行是一条记录。消息完成后追加提交，后续修改由墓碑、移动或
更新等变更记录表示。定期清理会重写记录流，但不改变重放结果。实测 `usage`
保存在对应 PROVIDER 消息上，详见[3-3 Provider 初识](3-3-provider-basics.md)。

### 核心状态：身份与游标

- **身份字段：**`agent_type` 记录声明类型；`parent_agent_id` 标识父节点
  （根的父节点为 `"runtime-0"`）；`created_at` 记录创建时间；`args` 保存
  创建参数。
- **`current_head_id`：**上下文组装游标随核心状态持久化。恢复时游标原样
  重建，下一回合从原消息路径继续。
- **Runtime 名录与全局状态：**Runtime 级核心状态登记 Agent 池。另有一个
  Runtime 全局状态袋，见[4-2 状态命名空间与自注册](4-2-state-namespaces-and-registration.md)。

### 刻意不持久化

- **逻辑回合：**`TurnContext` 是执行期临时状态，不持久化、不进入消息树；
  崩溃后不会续跑进行中的回合。
- **回合内瞬态：**重试计数等值随回合结束而重置。
- **等待句柄：**待处理回合句柄只存在于当前进程。

### write-behind 与崩溃窗口

提交记录后会将其排入队列并立即返回。属主收尾以及整文件重写前会排空写入队列。
若进程未正常关闭就退出，尚未落盘的尾部记录可能丢失。进程运行期间以内存
状态为权威；崩溃后该权威与未刷新的尾部同时消失，因此重放会还原出自洽的较早
状态。

### 恢复图景

```text
消息记录重放 ─┐
             ├→ 内存消息树 + 游标 + 状态袋 → 下一回合的上下文输入
状态记录重放 ─┘       （进行中的回合不会续跑）
```

每个子 Agent 都有自己的持久化会话状态。恢复后的记忆来自重放该子 Agent 的
消息子树。

## 本篇不覆盖

- 业务状态命名空间与注册见[4-2 状态命名空间与自注册](4-2-state-namespaces-and-registration.md)。
- 高阶恢复不变量，包括孤立 tool-call 占位与配对校验，见[4-3 消息树手术](4-3-message-tree-surgery.md)。
- 撕裂末行、墓碑、压缩时点与迁移链不在本篇范围内。
- Agent 池的运维清理不在本篇范围内。

## 主线示例

示例在同一持久化项目中完成三个阶段：正常回合、异常退出的第二个进程，以及
重启后读取两个回合。所需配置、提示词、程序代码、输入与样例输出都写在下方。
运行前安装 Flowing 并设置 `DEEPSEEK_API_KEY`。模型回复措辞可能变化；文中不
展示内部推理文本。

### 在同一项目根目录创建以下文件

`main.py`：

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

`root.fya`：

```yaml
description: "记忆演示助手：记住用户告知的信息。"
model_tag: default
---
$system_prompt:
你是一名记忆助手。用户让你记住信息时，请用一句话复述确认；之后用户再次
询问时，请准确回忆。每次回复都控制在一句话内。
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

`demo_crash.py`：

```python
import asyncio
import os

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    result = await agent.query("还要记住一种颜色：紫色。")
    print(
        f"回合完成 status={result.status}；现在用 os._exit(9) 模拟异常退出",
        flush=True,
    )
    os._exit(9)


asyncio.run(main())
```

正常进程的输入与代表性可见输出：

```text
$ uv run flowing repl .
(agent-main)>>> 记住数字 731。
[thinking] (reasoning trace omitted)
好的，我会记住数字 731。
(agent-main)>>> /exit
```

该进程在消息记录流中留下三条记录：一条元数据、一条用户消息和一条 PROVIDER
消息。用以下命令查看首条记录和身份元数据：

```text
$ wc -l .flowing/agent-main/tree.jsonl
3 .flowing/agent-main/tree.jsonl
$ head -1 .flowing/agent-main/tree.jsonl
{"type": "meta", "format_version": 1}
$ cat .flowing/agent-main/meta.json
{
  "agent_type": "@/root.fya",
  "parent_agent_id": "runtime-0",
  "created_at": "<生成时的时间戳>",
  "args": {}
}
```

异常退出进程恢复已有 Agent、再完成一个回合，随后不执行正常关闭便以状态码 9
退出：

```text
$ uv run python demo_crash.py
回合完成 status=completed；现在用 os._exit(9) 模拟异常退出
$ echo $?
9
$ wc -l .flowing/agent-main/tree.jsonl
5 .flowing/agent-main/tree.jsonl
```

最后一条命令是这次演示的记录结果，并不保证所有进程调度时序都得到相同结果。
write-behind 的时序行为应在具体运行环境和存储环境中验证。

重启 REPL，询问记住的两个值：

```text
$ uv run flowing repl .
(agent-main)>>> 我让你记的数字和颜色分别是什么？
[thinking] (reasoning trace omitted)
数字是 731，颜色是紫色。
```

数字由正常进程写入，颜色由异常退出的进程写入。恢复后，重放使两个已完成回合
的内容都能进入下一回合的上下文。

## 小结

1. 消息记录流保存版本元数据与消息/变更记录；完整消息追加提交，重放重建消息树。
2. 核心持久化内容包括 Agent 身份、上下文游标与 Runtime 名录状态。
3. 逻辑回合、回合内瞬态与等待句柄不持久化。
4. write-behind 存在尾部记录崩溃窗口；恢复通过重放重建状态，不会续跑进行中的回合。
