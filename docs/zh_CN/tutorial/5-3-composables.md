# 5-3 · Composable：机制在核心，策略在这里

## 前置阅读

[1-9 插件与 Composable 初识](1-9-plugins-and-composables.md)（
`use_prompt_until` / `use_system_reminder` 的现象观察）、[4-6 错误与
控制](4-6-errors-and-control.md)（`use_retry` 的决策面）。本篇内联完整
可运行配置、自写 Composable、直接工具调用输入与留档结果。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| Composable | 以 Agent 为主要作用对象的应用逻辑注入函数：调用 `use_xxx(agent, ...)` 时执行其函数体 |
| 常见结构 | `use_xxx(agent, ...)` 可声明扩展钩子点并注册 handler（用 `by=`/`tags=` 归组）；也可挂载 Agent 属性、管理状态或执行其他应用逻辑 |
| 归组管理 | 挂载的 handler 可按 `by` 整组移除（`remove_by_owner`）；这是管理 handler 的方式，不限制 Composable 的其他行为 |
| 框架边界 | “框架只提供机制，不提供策略”——重试 / 压缩 / 拦截规则全在 Composable 与插件里 |

## 目标

本篇介绍内置 Composable 的用法与参数，并展示一种自写 Composable 的常见
结构。框架核心提供机制，Composable 承载应用层逻辑与策略。

`use_xxx(agent, ...)` 是普通 Python 函数，框架不限制其函数体、返回值或
应用层行为。状态存放位置按生命周期选择：只在当前运行期使用的临时状态
可以放在闭包变量或 Agent 属性中。把状态放在该次调用创建的闭包中，可
避免与 Agent 属性名冲突，但外部不能通过 Agent 直接访问；Agent 属性便于
外部读取和管理，但需避免命名冲突。
需要持久化的状态可通过 Flowing 内置的 `agent.state.register()` 登记，
也可由应用自行维护持久化与恢复；闭包变量和普通 Agent 属性本身不提供
持久化能力。

## 正文

### 内置 Composable 的用法与参数

| Composable | 挂点 | 关键参数 | 已见 |
|---|---|---|---|
| `use_retry` | `on_provider_error`（+ `on_retry` 观测） | `max_retries`（默认 3）/ `base_delay` / 可重试错误白名单 | 4-6 实证 |
| `use_system_reminder` | `before_turn`（`clean=True` 加 `after_turn` 清理） | `contents`（三态）/ `message_interval` / `time_interval` / `clean` | 1-9 实证 |
| `use_compact` / `use_auto_compact` | `after_provider_gen` / `before_provider_gen` | `threshold` / `head_tokens` / `tail_tokens` / `compact_template` | 机制底座 4-3 |

`use_prompt_until` 的 `predicate` / `message` 两大可替换件见 1-9。

### 自写示例：一种常见结构

```python
import asyncio

from flowing.errors import Intercepted


def use_rate_limit(agent, *, max_calls: int = 5, window_seconds: float = 60.0) -> None:
    agent.hooks.declare("on_rate_limited", by="rate-limit", match_on="name")  # ① 声明扩展观测点
    calls: list[float] = []
    async def _gate(agent_, tool_call):                                      # ② 策略本体
        now = asyncio.get_event_loop().time()
        calls[:] = [t for t in calls if now - t < window_seconds]
        if len(calls) >= max_calls:
            await agent_.hooks.on_rate_limited.dispatch(agent_, tool_call)   # 观测先行
            raise Intercepted(f"调用频率超限：{max_calls} 次 / {window_seconds:.0f} 秒")
        calls.append(now)
        return tool_call
    agent.hooks.before_tool_call(_gate, by="rate-limit", tags=["rate-limit"])  # ③ 挂核心点 + 归组
```

示例体现三项设计选择：

1. **Composable 的行为不受框架限制**：`use_xxx(agent, ...)` 是普通
   Python 函数，可注册 handler、挂载 Agent 属性、管理状态，也可执行其他
   应用层逻辑；本例的限流计数仅用于当前运行期，因此放在闭包中；
2. **按需管理 handler**：本例用 `by` 归组，以便 `remove_by_owner` 整组
   移除。Composable 可以用不同参数多次调用同一个 `use_xxx`；框架不对
   调用自动去重，重复调用后的组合方式由 Composable 实现决定。本包内置
   Composable 的 handler 按各自注册语义叠加；
3. **观测与决策分离**：可观测事件走自声明的 `on_*` 点（fire-and-
   forget），决策走核心点的 `Intercepted` / 改写。

### 框架边界

错误分类（4-6）是机制，`use_retry` 是策略；钩子点是机制，
`use_rate_limit` / `use_system_reminder` 是策略；
**核心只做错误分类、钩子点与消息流转**——一切策略可替换、可卸载、
可自写。

## 本篇不覆盖

- `use_compact` / `use_auto_compact` 的内部压缩算法；
- 审批 / 拦截规则的具体交互形态——1-4 机制 + 你的应用形态决定；
- 发布 Composable 为库的约定——由你的工程决定。

## 主线示例

以下留档展示了五次直接工具调用的结果：

```console
$ uv run python demo_composables.py
== ① use_rate_limit(max_calls=3) 注入 ==
   5 次调用结果: ['completed', 'completed', 'completed', 'blocked', 'blocked']
   on_rate_limited 观测到 2 次超限（第 4、5 发）
== ② remove_by_owner 整组移除 ==
   移除 1 条 handler；再调一次: status=completed
== ③ 调用内置 Composable ==
   use_system_reminder 注入后 before_turn 命中 1 条 handler（此内置 Composable 的一种挂载方式）
```

读这段留档：本例把限流 handler 通过“declare + 挂核心点 +
by 归组”接入；这是该示例的组织方式。`Intercepted` 把超限调用变成 LLM 可见的 blocked
结果（可解释、可重试）；整组移除后策略即刻失效，Agent 行为还原。

**完整可运行材料。**

将每个代码块按标题保存到同一个 Flowing 项目中。示例只使用项目根级
文件名。Provider 凭证以环境变量占位符表示，不要把凭证写进文件。

### Runtime 入口：`main.py`

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### Provider 配置：`providers.yaml`

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### 模型配置：`models.yaml`

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### 模型标签：`model-tags.yaml`

```yaml
tags:
  default: deepseek-flash
```

### Agent 定义：`root.fya`

```yaml
description: Composable 演示助手：echo 工具 + 自写策略注入。
model_tag: default
tools:
  - ./echo.py
---
$system_prompt:
你是演示助手，回答控制在一句话以内。
```

### Echo 工具：`echo.py`

```python
from pydantic import BaseModel

from flowing import ScriptTool


class EchoArgs(BaseModel):
    text: str


class EchoTool(ScriptTool):
    name = "echo"
    args_model = EchoArgs

    async def execute(self, *, text: str) -> str:
        return text
```

### 完整 Composable：`rate_limit.py`

```python
import asyncio

from flowing.errors import Intercepted


def use_rate_limit(agent, *, max_calls: int = 5,
                   window_seconds: float = 60.0) -> None:
    agent.hooks.declare("on_rate_limited", by="rate-limit", match_on="name")
    calls: list[float] = []

    async def _gate(agent_, tool_call):
        now = asyncio.get_event_loop().time()
        calls[:] = [t for t in calls if now - t < window_seconds]
        if len(calls) >= max_calls:
            await agent_.hooks.on_rate_limited.dispatch(agent_, tool_call)
            raise Intercepted(
                f"调用频率超限：{max_calls} 次 / "
                f"{window_seconds:.0f} 秒，请稍后再试")
        calls.append(now)
        return tool_call

    agent.hooks.before_tool_call(
        _gate, by="rate-limit", tags=["rate-limit"])


def remove_rate_limit(agent) -> int:
    return agent.hooks.before_tool_call.remove_by_owner("rate-limit")
```

### 演示程序与输入：`demo_composables.py`

```python
import asyncio

from flowing import launch
from flowing.tool import ToolCall
from rate_limit import remove_rate_limit, use_rate_limit


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    print("== ① use_rate_limit(max_calls=3) 注入 ==")
    limited: list[str] = []
    use_rate_limit(agent, max_calls=3, window_seconds=60.0)

    async def observe(agent_, tool_call):
        limited.append(tool_call.name)
        return tool_call
    agent.hooks.on_rate_limited(observe, by="demo")

    results = []
    for i in range(5):
        result = await agent.tool_call(
            ToolCall(id=f"c{i}", name="echo", args={"text": f"第{i}发"}))
        results.append(result.status)
    print(f"   5 次调用结果: {results}")
    print(f"   on_rate_limited 观测到 {len(limited)} 次超限（第 4、5 发）")

    print("== ② remove_by_owner 整组移除 ==")
    removed = remove_rate_limit(agent)
    result = await agent.tool_call(
        ToolCall(id="c9", name="echo", args={"text": "恢复"}))
    print(f"   移除 {removed} 条 handler；再调一次: status={result.status}")

    print("== ③ 调用内置 Composable ==")
    from flowing.composables import use_system_reminder
    use_system_reminder(agent, contents=["[提醒] 来自 use_system_reminder"])
    hits = [h for h in agent.hooks.before_turn
            if getattr(h, "by", None) == "system-reminder"]
    print(f"   use_system_reminder 注入后 before_turn 命中 {len(hits)} 条 "
          "handler（这是该内置 Composable 的一种挂载方式）")
    await runtime.shutdown()


asyncio.run(main())
```

程序化输入为 5 个名为 `c0` 至 `c4` 的 `ToolCall`，每个调用 `echo`；
移除策略后再调用 `c9`。在保存这些内联文件的工作目录运行
`uv run python demo_composables.py`。上方留档展示前三次放行、后两次
阻断，以及移除策略后再次放行。

## 小结

1. 内置 Composable 由钩子点与参数组成；
2. 自写示例：use_xxx 函数可 declare 观测点、挂核心点并按 by 归组；状态位置按是否需要持久化选择；
3. 同一个 use_xxx 可带不同参数多次调用；框架不自动去重，效果由函数实现决定；
4. 框架核心提供机制，Composable 与插件承载策略。
