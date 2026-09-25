# 4-2 · 状态命名空间与自注册

## 前置阅读

[1-8 运行时与 Agent 目录](1-8-runtime-and-agent-dirs.md)（Runtime 与 Agent
的关系）、[4-1 核心状态与消息持久化](4-1-core-state-and-message-persistence.md)
（持久化模型）和[2-3 编写 ScriptTool](2-3-write-script-tool.md)（无状态
todo 工具）。Python 环境中须已安装 Flowing，并设置环境变量
`DEEPSEEK_API_KEY`。下方配置保留环境变量占位符，不含凭证。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 状态袋 | `agent.state` 暴露的键空间；读从内存读取，写入会持久化。 |
| 命名袋 | 通过 `register_state(name)` 开启的独立状态命名空间，可属于 Agent 或 Runtime。 |
| 缺省即写 | `state.register(key, default)` 仅在持久化值不存在时写入默认值；已有值优先。 |
| 写透 | 设置或删除键时立即提交持久化操作；读取以内存权威为准。 |
| Runtime 全局袋 | 在 Runtime 上注册、由其 Agent 共享的命名袋。 |

## 目标

在 Agent 和 Runtime 两个层级注册持久化状态：需要跨重启保留的值予以保存，
可以重新推导的值每次重算。示例也会让先前的无状态 todo 工具具备持久化能力。

## 正文

### `agent.state`：默认状态袋

```python
async def setup(self):
    self.state.register("user_prefs", {})
    self.state.register("todo_items", [])
    self.state.register("visit_count", 0)

# 工具或钩子内：
preferences = caller.state.get("user_prefs") or {}
preferences[key] = value
caller.state["user_prefs"] = preferences
```

- **写透：**赋值会立即提交持久化操作。值必须与 JSON 兼容；读取从内存中进行。
- **缺省即写，持久值优先：**恢复重放先于 `setup()` 完成。`register` 不会
  覆盖已有持久值；之后修改代码中的默认值也不会替换已保存的值。
- **无需 schema：**无需先调用 `register` 就能通过赋值写入键；删除键会移除其
  持久值。

### `register_state`：命名袋

```python
agent.register_state("audit")
runtime.register_state("app")
```

命名袋与默认袋遵循相同的状态视图契约，只是把键空间分到独立命名空间中。插件
可以给业务键加自身名称前缀，例如 `cron_jobs`。框架私有状态与应用数据分开保存。

### 哪些值应该注册

| 应注册 | 不应注册 |
|---|---|
| 跨会话要保留的偏好、进度和任务清单。 | 高频遥测值，除非明确需要保留计数器。 |
| 恢复后仍要使用的业务状态。 | 可在上下文组装时重新推导的值。 |
| 小型配置数据。 | 大对象；状态值必须与 JSON 兼容。 |

如果不需要持久化，高频变化值应使用消息通道或内存属性。状态袋是持久化通道，
不是每回合上下文注入通道：它不会进入 LLM 上下文。值通过工具写入或读出状态袋；
模型看到的是工具参数和结果，而不是状态袋本身。

## 本篇不覆盖

- 写透持久化的详细时序不在本篇范围内。
- 核心状态与消息持久化见[4-1 核心状态与消息持久化](4-1-core-state-and-message-persistence.md)。
- 压缩时点与迁移链不在本篇范围内。

## 主线示例

示例记录一项偏好、两个待办；Agent 级回合计数器与 Runtime 全局启动计数器也会
递增。随后重启并读取持久化值。下方内联全部声明、提示词、工具实现、输入与代表
性输出。运行前安装 Flowing 并设置 `DEEPSEEK_API_KEY`。不展示模型内部推理过程。

### 在同一项目根目录创建以下文件

`main.py`：

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")

    app_state = runtime.register_state("app")
    app_state["boots"] = app_state.get("boots", 0) + 1

    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

`root.fya`：

```yaml
description: "持久化偏好、待办与访问计数的助手。"
model_tag: default
tools:
  - ./prefs.py
  - ./todo.py
  - ./app_info.py
---
$system_prompt:
你是一名状态演示助手。用户通过 prefs 记录偏好、通过 todo 管理待办，并通过
app-info 查询启动次数。每次回复控制在一句话内。
---
$script:
async def setup(self):
    self.state.register("user_prefs", {})
    self.state.register("todo_items", [])
    self.state.register("visit_count", 0)

    async def count_completed_turns(agent, turn):
        if turn.aborted:
            return turn
        agent.state["visit_count"] = agent.state["visit_count"] + 1
        return turn

    self.hooks.after_turn(count_completed_turns, by="counter")
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

`prefs.py`：

```python
from typing import Literal

from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class PrefsArgs(BaseModel):
    action: Literal["remember", "list"] = Field(
        description="remember=记录一项偏好；list=列出全部偏好"
    )
    key: str | None = Field(default=None, description="偏好名称")
    value: str | None = Field(default=None, description="偏好值")


class PrefsTool(ScriptTool):
    """记录和列出保存在 Agent 状态袋中的偏好。"""

    name = "prefs"
    args_model = PrefsArgs

    async def execute(
        self, *, action: str, key: str | None, value: str | None,
        caller: Agent,
    ) -> dict:
        preferences = caller.state.get("user_prefs") or {}
        if action == "remember":
            if not key or value is None:
                raise ValueError("remember requires key and value")
            preferences[key] = value
            caller.state["user_prefs"] = preferences
            return {"remembered": {key: value}}
        return {"prefs": preferences}
```

`todo.py`：

```python
from typing import Literal

from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class TodoArgs(BaseModel):
    action: Literal["add", "done", "list"] = Field(
        description="add=新增待办；done=完成待办；list=列出待办"
    )
    title: str | None = Field(default=None, description="新增待办的标题")
    index: int | None = Field(default=None, description="完成待办时使用的序号，从 1 开始")


class TodoStateTool(ScriptTool):
    """新增、完成和列出保存在 Agent 状态袋中的待办。"""

    name = "todo"
    args_model = TodoArgs

    async def execute(
        self, *, action: str, title: str | None, index: int | None,
        caller: Agent,
    ) -> dict:
        items: list[dict] = caller.state.get("todo_items") or []
        if action == "add":
            if not title:
                raise ValueError("add requires title")
            items.append({"title": title, "done": False})
        elif action == "done":
            if index is None or not 1 <= index <= len(items):
                raise ValueError(f"index out of range: {index!r} ({len(items)} items)")
            items[index - 1]["done"] = True
        caller.state["todo_items"] = items
        return {
            "items": items,
            "open": sum(1 for item in items if not item["done"]),
        }
```

`app_info.py`：

```python
from flowing import Agent, ScriptTool


class AppInfoTool(ScriptTool):
    """读取 Runtime 全局状态袋中的应用信息。"""

    name = "app-info"

    async def execute(self, *, caller: Agent) -> dict:
        app_state = caller.runtime.states["app"]
        return {"boots": app_state.get("boots", 0)}
```

第一次运行的输入：

```text
记住偏好：主题=深色。加两个待办：写周报、买牛奶。这次启动是第几次、我这个会话是第几轮？
```

启动 `uv run flowing repl .`，输入上面的内容，再输入 `/exit`。代表性可见输出：

```text
(agent-main)>>> 记住偏好：主题=深色。加两个待办：写周报、买牛奶。这次启动是第几次、我这个会话是第几轮？
[thinking] (reasoning trace omitted)
[tool_call] prefs {"action": "remember", "key": "主题", "value": "深色"}
[tool_call] todo {"action": "add", "title": "写周报"}
[tool_call] todo {"action": "add", "title": "买牛奶"}
[tool_call] app-info
[tool:completed] prefs
[tool:completed] todo
[tool:completed] todo
[tool:completed] app-info
已记住偏好“主题=深色”、加入待办“写周报”和“买牛奶”，本次启动是第 1 次；不过会话轮次没有对应工具可查，我无法得知。
(agent-main)>>> /exit
```

第一次运行中提供给助手的工具返回值：

```json
{
  "prefs": {"remembered": {"主题": "深色"}},
  "todo_add_1": {
    "items": [{"title": "写周报", "done": false}],
    "open": 1
  },
  "todo_add_2": {
    "items": [
      {"title": "写周报", "done": false},
      {"title": "买牛奶", "done": false}
    ],
    "open": 2
  },
  "app-info": {"boots": 1}
}
```

重启 REPL 后，第二次运行的输入为：

```text
我记住的偏好和待办分别是什么？访问计数和启动次数呢？
```

再次启动 `uv run flowing repl .`，输入上面的内容，再输入 `/exit`。代表性可见
输出：

```text
(agent-main)>>> 我记住的偏好和待办分别是什么？访问计数和启动次数呢？
[thinking] (reasoning trace omitted)
[tool_call] prefs {"action": "list"}
[tool_call] todo {"action": "list"}
[tool_call] app-info
[tool:completed] prefs
[tool:completed] todo
[tool:completed] app-info
偏好是“主题=深色”，有两条未完成待办“写周报”“买牛奶”，启动次数为 2；但工具中没有访问计数，我查不到。
(agent-main)>>> /exit
```

第二次运行中提供给助手的工具返回值：

```json
{
  "prefs": {"prefs": {"主题": "深色"}},
  "todo": {
    "items": [
      {"title": "写周报", "done": false},
      {"title": "买牛奶", "done": false}
    ],
    "open": 2
  },
  "app-info": {"boots": 2}
}
```

两次回合后的持久化状态可表示为以下记录。前三条属于 Agent 默认状态袋，最后
一条属于 Runtime 全局 `app` 状态袋：

```jsonl
{"op": "set", "key": "user_prefs", "value": {"主题": "深色"}}
{"op": "set", "key": "todo_items", "value": [{"title": "写周报", "done": false}, {"title": "买牛奶", "done": false}]}
{"op": "set", "key": "visit_count", "value": 2}
{"op": "set", "key": "boots", "value": 2}
```

偏好与待办工具会写入 Agent 状态袋。after-turn 钩子每完成一轮就递增
`visit_count`；Runtime 每启动一次就递增 `boots`。`app-info` 只暴露 `boots`，
不暴露 `visit_count`，所以模型无法通过现有工具报告访问计数。状态袋用于持久化，
不会整体注入 LLM 上下文。

## 小结

1. 状态袋是带写透持久化的键空间；注册默认值不会覆盖已存在的持久值。
2. `register_state` 会开启独立命名空间，Runtime 全局袋可由多个 Agent 共享。
3. 注册需要跨重启保留的值；除非确有持久化需求，否则避免保存高频或可重算值。
4. 工具可以向 Agent 暴露选定的状态操作，而不把整个状态袋放入 LLM 上下文。
