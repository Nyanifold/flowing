# 3-4 · 模型配置与上下文预算

## 前置阅读

[0-1 第一个智能体](0-1-hello-agent.md)（模型标签与模型条目）和
[3-3 Provider 初识](3-3-provider-basics.md)（`Usage` 的保存位置）。Python
环境中须已安装 Flowing，并在环境变量 `DEEPSEEK_API_KEY` 中设置凭证。下方
配置保留环境变量占位符，不内嵌凭证。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| `estimate_context_tokens()` | 纯观测 API：结合最近的实测用量锚点与其后新内容的启发式估算。 |
| 实测锚点 | 当前消息路径上最近一条带 `usage` 的 PROVIDER 消息；该消息及之前的内容计为实测，之后计为估算。 |
| `usage_ratio` | `tokens / context_window`。不截断；未声明上下文窗口时为 `None`。 |
| 观察窗口 | 只供观察、不自动处置的数值。比率逼近 1 时，应用可以考虑压缩或清理。 |

## 目标

展示模型标签设计、运行期切换、按 Agent 指定模型，以及如何把上下文预算
估算作为观察值使用。

## 正文

### 模型选择：三种操作

模型解析分两跳：`model_tag` 先选择标签映射，再由映射选择模型条目。在此基础
上有三种操作：

1. **定义条目和标签。** 每个标签映射到一个模型条目。多个标签可以指向同一
   条目，也可以用 `fast`、`chat` 等标签区分用途。
2. **声明期指定模型。** Agent 声明可以设置 `model_tag`；子 Agent 可以用此
   方式指定专用模型。
3. **运行期切换模型。** 赋值 `agent.model_tag = "chat"` 会重新解析模型，
   并作用于下一次 `provider_gen` 调用。也可以给 `agent.model` 赋一个完整的
   `ModelConfig`，替换整份模型规格。

`context_window`、`max_output_tokens` 等字段是模型元数据。Flowing 不根据
这些字段直接施加策略；观测 API 与 composable 可以消费这些数据。

### 上下文预算：实测锚点加尾部估算

```python
estimate = agent.estimate_context_tokens()
estimate.tokens       # 实测值 + 估算值
estimate.measured    # 最近 usage 锚点及之前部分的实测值
estimate.estimated   # 锚点之后内容的启发式估算
estimate.usage_ratio  # tokens / context_window；不截断，或为 None
```

估算只供观察，不用于计费；计费使用 `TurnResult.token_usage`。`usage_ratio`
大于 1 是合法的溢出信号。如果模型条目没有声明 `context_window`，ratio 为
`None`。REPL 的 `/status` 与 `/snapshot` 命令也会报告相关健康信息。

### 预算的定位：观察窗口

阈值与压缩属于策略；Flowing 提供观测值，但不替应用决定何时采取行动。比率
逼近 1 时，应用可以考虑压缩或清理。通过 fork 替换消息链并以摘要开启新根是
另一类机制，`use_compact` 与 `use_auto_compact` 则是应用策略；本篇只观察
估算值。

## 本篇不覆盖

- 基于 fork 的链替换与树手术见[4-3 消息树手术](4-3-message-tree-surgery.md)。
- 压缩策略见[5-3 Composables](5-3-composables.md)。
- 配置优先级与编程覆盖见[6-3 运维](6-3-ops.md)。

## 主线示例

下方示例所需的声明、配置、程序、提示词与样例输出均在本文中给出。运行前
安装 Flowing，并在环境变量 `DEEPSEEK_API_KEY` 中设置凭证；配置中的
`{{env.DEEPSEEK_API_KEY}}` 占位符保持不变。

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
description: "模型配置与上下文预算演示助手。"
model_tag: default
---
$system_prompt:
你是一名简洁的中文助手。每次回复控制在一句话内。
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
  context_window: 32000
deepseek-chat:
  provider: deepseek
  model: deepseek-chat
  context_window: 32000
```

`model-tags.yaml`：

```yaml
tags:
  default: deepseek-flash
  fast: deepseek-flash
  chat: deepseek-chat
```

`greeter.fya`：

```yaml
description: "使用 chat 标签所选模型的问候 Agent。"
model_tag: chat
---
$system_prompt:
你是一名问候助手。请用一句话问候用户。
```

`demo_model.py`：

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")

    print(f"(1) 根 Agent：model_tag={root.model_tag!r} -> model={root.model.model!r}")
    print(f"    provider 条目绑定：{root.model.provider!r}")

    root.model_tag = "chat"
    print(f"(2) 运行期 root.model_tag = 'chat' -> model={root.model.model!r}")

    child = await root.create_subagent("@/greeter.fya")
    print(f"(3) 子 Agent greeter：声明 model_tag={child.model_tag!r} "
          f"-> model={child.model.model!r}")
    await child.destroy()
    await runtime.shutdown()


asyncio.run(main())
```

运行 `uv run python demo_model.py`。该示例不调用模型，因此输出是确定的：

```text
(1) 根 Agent：model_tag='default' -> model='deepseek-v4-flash'
    provider 条目绑定：'deepseek'
(2) 运行期 root.model_tag = 'chat' -> model='deepseek-chat'
(3) 子 Agent greeter：声明 model_tag='chat' -> model='deepseek-chat'
```

`demo_budget.py`：

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    estimate = agent.estimate_context_tokens()
    print(f"初始（空树）：tokens={estimate.tokens} "
          f"measured={estimate.measured} estimated={estimate.estimated} "
          f"ratio={estimate.usage_ratio}")

    for turn in range(1, 7):
        question = f"第 {turn} 问：换一个角度，用一句话谈谈『上下文窗口』。"
        await agent.query(question)
        estimate = agent.estimate_context_tokens()
        print(f"第 {turn} 轮后：tokens={estimate.tokens} "
              f"measured={estimate.measured} estimated={estimate.estimated} "
              f"ratio={estimate.usage_ratio:.4f}")

    await runtime.shutdown()


asyncio.run(main())
```

运行 `uv run python demo_budget.py`。六条用户输入由上面的循环生成，依次为
“第 1 问：换一个角度，用一句话谈谈『上下文窗口』。”至“第 6 问：换一个
角度，用一句话谈谈『上下文窗口』。”一次记录样例如下：

```text
初始（空树）：tokens=21 measured=None estimated=21 ratio=0.00065625
第 1 轮后：tokens=204 measured=204 estimated=0 ratio=0.0064
第 2 轮后：tokens=507 measured=507 estimated=0 ratio=0.0158
第 3 轮后：tokens=565 measured=565 estimated=0 ratio=0.0177
第 4 轮后：tokens=612 measured=612 estimated=0 ratio=0.0191
第 5 轮后：tokens=706 measured=706 estimated=0 ratio=0.0221
第 6 轮后：tokens=835 measured=835 estimated=0 ratio=0.0261
```

空树没有 usage 锚点，因此内容由启发式估算。首轮完成后，PROVIDER 消息提供
实测用量并成为锚点。上面是一次模型调用记录；prompt token 数与回复长度会
改变具体读数。声明的上下文窗口为 32,000 token，因此这段短对话的比率远低于
1。

## 小结

1. 模型选择采用两跳解析，并支持标签设计、声明期指定和运行期切换。
2. `estimate_context_tokens()` 结合实测用量与尾部估算；`usage_ratio` 不
   截断、可能为 `None`，且不用于计费。
3. 预算是观察窗口。比率逼近 1 是应用考虑压缩的信号，不是自动执行的策略。
