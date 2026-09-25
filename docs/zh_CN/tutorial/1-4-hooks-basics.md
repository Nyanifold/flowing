# 1-4 · 钩子基础

## 前置阅读

[1-3 参数与 setup()](1-3-agent-args-and-setup.md)（`setup()` 的写法）。
本篇沿用 Runtime/Agent 的装配方式，展示类上声明钩子和在 setup 内注册钩子。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 钩子点 | 框架运行的固定时点（工具执行前、回合收尾等），扩展代码在此介入或观察 |
| handler | 挂在钩子点上的处理函数，统一签名 `(agent, value)`；本篇专称“钩子处理器”，首次出现后可直接称 handler |
| `Intercepted` | handler 的硬阻断信号：抛出后当前操作作废，LLM 收到 blocked 结果与原因（刻意不挂框架异常根） |
| `before_tool_call` | 工具执行前的钩子点：可改写调用参数、可硬阻断——天然的人工审批/安全闸 |
| `after_turn` | 回合收尾的钩子点：唯一全路径观察点（含异常终止路径） |

## 目标

会用两个最常用钩子点：`before_tool_call` 做拦截、`after_turn` 做观察；
并掌握两种挂载方式（`@on` 类上声明 与 `setup()` 内注册）。

## 正文

### 实例级钩子注册表

每个 Agent 实例持有独立的钩子注册表（`agent.hooks`），**全部钩子只对
当前实例生效**——不存在全局钩子表。handler 统一签名 `(agent, value)`
（方法形态即 `(self, value)`），三种出口：

1. `return value`：放行（顺手改写了 value 就是改写，如改工具参数）；
2. `raise Intercepted`：硬阻断——当前操作作废，LLM 收到 `blocked`
   结果与原因，可以向用户解释“该操作被拦截”而非“执行失败”；
3. 普通异常直接上抛，没有兜底钩子接住它。

### 两种挂法

**`@on` 类上声明**（注册发生在 `__init__` 阶段，早于 `setup()`——创建期
钩子的唯一挂法）：

```python
@on("after_turn")
def _(self, turn):
    self.ask_count += 1
    print(f"[hook] after_turn: ask_count={self.ask_count}")
    return turn
```

**`setup()` 内注册**（实例级，装配期挂上）：

```python
async def _guard(agent, tool_call):
    if tool_call.name == "bash" and "rm" in tool_call.args.get("command", ""):
        raise Intercepted("不允许删除：命令包含 rm，已阻断")
    return tool_call
self.hooks.before_tool_call(_guard)
```

`setup()` 的形参必须与 `args:` 声明一一对应——编译期校验，多一个参数
即 fail-fast（1-3 的“args 声明即模型”的另一半）。

### before_tool_call：拦截闸

`before_tool_call` 的 value 是 `ToolCall`（LLM 想调什么）。返回改写后
的 `ToolCall` 是放行；`raise Intercepted` 是拒绝——工具**不执行**，
LLM 收到 `blocked` 结果与拦截原因。从拦截到审批只差一步：handler 是
`async` 的，可以在里面 `await` 一次外部确认再决定出口（审批是策略，
钩子只是机制；完整的审批交互是进阶话题，机制相同）。

### after_turn：观察点

`after_turn` 的 value 是 `TurnContext`；它在**所有路径**收尾时触发
（含被拦截、取消、异常的回合），handler 读 `turn.aborted` 区分结局。
handler 异常不会楔死 Agent——等待者照常拿到结果。

## 本篇不覆盖

- 钩子点全集与 pattern 过滤（`hook["payment-*"](...)`）——4-6；
- `declare()` 声明扩展钩子点——5-2 / 5-3；
- `watch` 赋值事件通道——4-7；
- 带通过/改参交互的人工审批流程——机制同上，应用形态由你决定。

## 主线示例

repl 中先要求删除文件（触发拦截）、再要求查看目录（放行）：

```console
$ uv run flowing repl . --user_name 小明
(agent-main)>>> 请用 bash 删除 notes/路线图.md。
[tool_call] bash {"command": "rm notes/路线图.md", "cwd": "."}
[hook] before_tool_call: bash args={'command': 'rm notes/路线图.md', 'cwd': '.'}
[tool:blocked] bash -> 不允许删除：命令包含 rm，已阻断
抱歉，删除被安全钩子拦截了——它禁止执行含 `rm` 的命令，所以 notes/路线图.md 未被删除。
[hook] after_turn: ask_count=1 aborted=False
(agent-main)>>> 再用 bash 看一下 notes 目录里有什么。
[thinking] (reasoning trace omitted)
[tool_call] bash {"command": "ls notes", "cwd": "."}
[hook] before_tool_call: bash args={'command': 'ls notes', 'cwd': '.'}
[tool:completed] bash -> exit_code: 0 --- stdout --- 使用说明.md 路线图.md
notes 目录里有“使用说明.md”和“路线图.md”两个文件。
[hook] after_turn: ask_count=2 aborted=False
(agent-main)>>> /exit
```

读这段会话：第一回合模型的工具调用意图参数完整可见（`rm` 命令行）；
`[hook]` 行来自 `before_tool_call` handler 的打印
（拦截钩子的最小形态：观察 + 放行判断）；`rm` 命中守卫规则 →
`[tool:blocked]` 携带原因返回给 LLM → LLM 向用户如实解释“文件未删除”；
`[hook] after_turn: ask_count=1` 是 `@on` 声明的观察 handler 在回合
收尾触发。第二回合 `ls` 放行，`[tool:completed]` 带真实输出，
`ask_count` 累加到 2。事后检查：`notes/路线图.md` 完好。

## 完整示例材料

以下内容构成本篇所需的完整示例。在已安装 Flowing 的项目根目录中按相对
文件名创建这些文件，在环境中设置 `DEEPSEEK_API_KEY`，再执行所示命令。
删除请求会被钩子拦截；笔记文件不会改变。

`main.py`：

```python
from flowing import Runtime


async def main(user_name: str = "小明") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    await runtime.mount("@/root.fya", agent_id="agent-main", user_name=user_name)
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

`root.fya`（完整声明、system prompt 与钩子代码）：

```yaml
description: 钩子演示助手：before_tool_call 拦截 + after_turn 观察。
model_tag: default
args:
  user_name: str
tools:
  - bash
---
$system_prompt:
你是演示助手。当前用户：{{ user_name }}。
用户要求查看目录时用 bash 的 ls；用户要求删除文件时用 bash 的 rm，安全钩子
会拦截任何包含 rm 的命令。删除被拦截后须如实说明。每次回答不超过一句。
---
$script:
from flowing import Intercepted, on


@on("after_turn")
def _(self, turn):
    self.ask_count += 1
    print(f"[hook] after_turn: ask_count={self.ask_count} aborted={turn.aborted}")
    return turn


async def setup(self, user_name: str):
    self.user_name = user_name
    self.ask_count = 0
    self.timezone = self.inject("timezone")

    async def _guard(agent, tool_call):
        print(f"[hook] before_tool_call: {tool_call.name} args={tool_call.args}")
        if tool_call.name == "bash" and "rm" in tool_call.args.get("command", ""):
            raise Intercepted("不允许删除：命令包含 rm，已阻断")
        return tool_call

    self.hooks.before_tool_call(_guard)
```

`notes/使用说明.md`：

```text
此目录包含简短的项目笔记。
钩子演示必须保持所有笔记不变。
```

`notes/路线图.md`：

```text
演示文件：钩子必须拦截删除。
```

输入（在 REPL 提示符下逐行输入）：

```text
请用 bash 删除 notes/路线图.md。
再用 bash 列出 notes 目录。
/exit
```

运行命令：

```console
$ uv run flowing repl . --user_name 小明
```

预期可观察结果：第一次 `bash` 调用返回带有“不允许删除”原因的
`blocked` 结果；第二次调用列出两个笔记文件；`after_turn` 计数到 2。
LLM 面向用户的具体措辞可能不同。该规则只匹配命令中是否包含子串 `rm`，
仅用于演示，不构成完整的 shell 安全策略。

## 小结

1. 钩子注册表实例级：全部钩子只对当前 Agent 生效；
2. handler 三出口：return 放行（可改写）、`Intercepted` 硬阻断、异常上抛；
3. `before_tool_call` 是天然拦截闸，`after_turn` 是唯一全路径观察点；
4. 两种挂法：`@on` 类上声明（早于 setup）、`setup()` 内注册（装配期）。
