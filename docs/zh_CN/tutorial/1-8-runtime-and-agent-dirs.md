# 1-8 · 运行时目录与智能体目录

## 前置阅读

[0-1 第一个智能体](0-1-hello-agent.md)（`Runtime(persist_dir="@/.flowing")`、
固定 `agent_id` 幂等挂载）。本篇使用最小 hello 形态，完整配置、提示词、输入
和可观察的目录输出均列在下文。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 持久化根（persist_dir） | Runtime 的持久化根目录；缺省 `<当前工作目录>/.flowing`，推迟到首个持久化动作才创建 |
| 智能体目录 | 每个 Agent 一个 session 目录（`persist_dir/<agent_id>/`），固定 agent_id → 固定目录 |
| 身份四键 | `meta.json` 的四个字段：`agent_type` / `parent_agent_id` / `created_at` / `args`——Agent 身份的最小记录 |
| 运行时级状态 | 持久化根直属的 `core.jsonl` / `default.jsonl`：Runtime 自己的全局状态命名空间（机制见 4-2） |

## 目标

知道东西落在哪：跑一个 flowing 子项目后，磁盘上多了什么、各自管什么；
以及“删掉目录即全新开始”这条最朴素的运维操作。

## 正文

### 持久化根：缺省行为就够好

`Runtime(persist_dir=...)` 指定持久化根；**缺省 `<当前工作目录>/.flowing`**，
且推迟到首个持久化动作才创建——零持久化的运行不落盘、不污染工作目录。
不配置任何东西即可跑：默认行为是“能持久化就持久化”。

### 智能体目录：固定 id → 固定目录

每个 Agent 一个 session 目录（`persist_dir/<agent_id>/`）。0-1 的固定
`agent_id="agent-main"` 意味着固定目录：重启后幂等挂载找回的就是“同
一个 Agent”的磁盘记录。

### 目录内容预览（表象）

跑过一次对话后（主线示例），目录布局：

```
.flowing/
├── core.jsonl              # Runtime 级核心状态（智能体池名录等，4-1 / 4-2）
└── agent-main/             # 根 Agent 的 session 目录
    ├── meta.json           # 身份四键：agent_type / parent_agent_id / created_at / args
    ├── tree.jsonl          # 消息树（一行一消息，4-1 展开）
    ├── core.jsonl          # Agent 级核心状态（head 游标、child_ids）
    └── state.jsonl         # 状态袋（4-2）
```

本篇只要“知道名字和大概分工”；字段级布局、write-behind、恢复语义
是 4-1 的主题。

### 删掉目录即全新开始

`.flowing/` 是整个运行历史的载体：删掉它，进程眼中的世界回到初始
（主线示例 run 2 实证——`/messages` 只剩新会话的一问一答）。反向亦然：
目录在，记忆就在。

## 本篇不覆盖

- 四个文件的字段级布局、write-behind 落盘机制、恢复管线——4-1；
- 状态命名空间与自注册——4-2；
- 撕裂末行 / 墓碑 / 压缩时点——6-3 按需；
- 目录的运维清理（archive / 孤儿）——6-3。

## 主线示例

**run 1**：跑一轮对话，随后观察目录：

```console
$ uv run flowing repl .
(agent-main)>>> 用一句话介绍你自己。
[thinking] (reasoning trace omitted)
我是一个简洁的中文助手，习惯用三句话以内回答你的问题。
(agent-main)>>> /exit

$ find .flowing -type f | sort
.flowing/agent-main/core.jsonl
.flowing/agent-main/meta.json
.flowing/agent-main/state.jsonl
.flowing/agent-main/tree.jsonl
.flowing/core.jsonl

$ cat .flowing/agent-main/meta.json
{
  "agent_type": "@/root.fya",
  "parent_agent_id": "runtime-0",
  "created_at": "<运行时生成的时间戳>",
  "args": {}
}
```

`meta.json` 即身份四键：`agent_type` 记录来源声明（`@/root.fya`）、
`parent_agent_id` 是 `"runtime-0"`（根的亲节点是 Runtime——多智能体
拓扑是树，3-1 展开）、`args` 是创建 kwargs（1-3 说过它“JSON 可序列化、
是身份的一部分”，此处落盘为证）。

**run 2**：把旧目录移到备份名下，再重新开始：

```console
$ mv .flowing .flowing.saved
$ uv run flowing repl .
(agent-main)>>> 用一句话介绍你自己。
[thinking] (reasoning trace omitted)
我是一个简洁的中文助手，能帮你快速解答问题。
(agent-main)>>> /messages
1  user      用一句话介绍你自己。
2  provider  我是一个简洁的中文助手，能帮你快速解答问题。
(agent-main)>>> /exit
```

`/messages` 的上溯链只有本轮的一问一答——上次会话的历史随目录一起
消失了。

## 完整示例材料

在已安装 Flowing 的项目中按相对文件名创建以下文件，并在环境变量中设置
`DEEPSEEK_API_KEY`。命令都从项目根目录运行，不需要切换目录。

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

`root.fya`：

```yaml
description: 目录观察助手：最小问答。
model_tag: default
---
$system_prompt:
你是一个简洁的中文助手，回答控制在三句话以内。
```

run 1 输入：

```text
用一句话介绍你自己。
/exit
```

run 1 命令与代表性输出：

```console
$ uv run flowing repl .
(agent-main)>>> 用一句话介绍你自己。
我是一个简洁的中文助手，习惯用三句话以内回答你的问题。
(agent-main)>>> /exit
$ find .flowing -type f | sort
.flowing/agent-main/core.jsonl
.flowing/agent-main/meta.json
.flowing/agent-main/state.jsonl
.flowing/agent-main/tree.jsonl
.flowing/core.jsonl
$ cat .flowing/agent-main/meta.json
{
  "agent_type": "@/root.fya",
  "parent_agent_id": "runtime-0",
  "created_at": "<运行时生成的时间戳>",
  "args": {}
}
```

`created_at` 在运行时生成。JSONL 状态文件属于追加式运行数据，消息 id 和
时间戳会变化；上面的目录树列出了完整的预期文件布局。

如需可逆地演示全新状态，把旧目录移到备份名下：

```text
用一句话介绍你自己。
/messages
/exit
```

```console
$ mv .flowing .flowing.saved
$ uv run flowing repl .
```

新建的 `.flowing/` 只包含第二次运行的对话；之前的状态仍保存在
`.flowing.saved/`，可以恢复。

## 小结

1. 缺省持久化根 = `<cwd>/.flowing`，推迟创建、零配置可跑；
2. 固定 agent_id → 固定智能体目录；目录在，记忆就在；
3. 四文件表象：`meta.json`（身份四键）/ `tree.jsonl`（消息树）/
   两个 `core.jsonl` + `state.jsonl`（状态，4-2）；
4. 重命名 `.flowing/` 可保留备份，同时开启一轮全新运行。
