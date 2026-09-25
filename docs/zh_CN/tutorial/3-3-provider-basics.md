# 3-3 · Provider 初识

## 前置阅读

[3-2 上下文组装](3-2-context-assembly.md)（Provider 接收一个 `Context`）。
Python 环境中须已安装 Flowing，并通过 `DEEPSEEK_API_KEY` 环境变量提供
有效凭证。配置中的 `{{env.DEEPSEEK_API_KEY}}` 占位符应原样保留，不要把
凭证直接写入文档或配置。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| Provider | 模型调用的执行端。Provider adapter 将 `Context` 映射为服务商请求，并将响应归一化。 |
| `provider_gen()` | Agent 发起模型调用的入口，支持流式和非流式模式。 |
| `ProviderDelta` | 通过 `on_provider_delta` 分发的流式增量；非流式调用也会分发一条完整 delta。 |
| `Usage` | 归一化后的 token 用量，附着在 PROVIDER 消息的 `usage` 字段上。 |

## 目标

理解两种调用形态、delta 的观察通道，以及实测用量保存的位置。

## 正文

### Provider 与 adapter

Provider 执行一次模型调用。`generate(context, model)` 返回一份完整响应，
`generate_stream(context, model)` 则逐段产出响应增量；两种调用的输入都是
`Context`。服务商 adapter 将消息类型与内容映射为对应请求格式，再把响应
字段映射回 Flowing 的消息与用量模型。切换 adapter 不要求修改 Agent 逻辑。

### 流式与非流式

`provider_gen(stream=True)` 是默认模式。delta 逐段到达，Flowing 按
`content_index` 累积内容。若生成被中断，已累积内容会定型为
`partial=True` 消息并保留。使用 `provider_gen(stream=False)` 时，完整响应
一次返回，但 Flowing 仍会合成并分发一条完整 delta。两种模式共用 delta
格式，因此订阅者可以用同一个钩子处理它们。代表主回合发起编程调用时，若
需要主回合来源标记，应传 `by="_turn"`。

### 钩子点

- `on_provider_delta` 在每条 delta 到达时触发，属于观察钩子：修改钩子收到
  的局部值不会改写 Flowing 的累积内容。`by` 过滤器可区分主回合
  （`"_turn"`）与副线（`"_side"`）。流式界面可以用它显示生成文本。
- `before_provider_gen` 可在调用前检查或改写已组装的 `Context`。
- `on_provider_error` 是调用异常的决策钩子。handler 设置
  `ctx.can_continue = True` 时，请求当前回合重试；否则回合以错误结束。完整
  决策面见[4-6 错误与控制](4-6-errors-and-control.md)。

### 实测用量保存在哪里

`Usage` 包含 `input`、`fresh_input`、`output`、`cache_read`、
`cache_write`、`reasoning`、`total_tokens` 及可选的服务商原始数据。adapter
负责归一化。权威持久值是 PROVIDER 消息的 `usage` 字段，因此它随消息一同
落盘，恢复后仍可读取。回合层将其聚合为 `TurnResult.token_usage`，作为计费
口径。上下文预算观测也使用消息中的实测用量；[3-4 模型配置与上下文预算](3-4-model-config-and-budget.md)
会展示该计算。

## 本篇不覆盖

- 本篇不展开具体服务商 adapter 及其协议细节。
- 完整错误分类与 `on_provider_error` 决策面见[4-6 错误与控制](4-6-errors-and-control.md)。
- 模型配置优先级与编程覆盖见[6-3 运维](6-3-ops.md)。

## 主线示例

下面的自包含示例创建一个只含系统提示词的上下文，并分别用两种模式调用
Provider，统计 delta 数量并报告是否收到可见文本。示例不发送用户消息。模型
实时文本、token 用量和 delta 切分都可能变化；下方输出是一次记录样例，不是
确定性断言。回调会统计所有 delta，但不会打印模型内部推理文本。

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
description: "Provider 演示助手：最简问答。"
model_tag: default
---
$system_prompt:
你是一名简洁的中文助手。请始终使用中文回复。
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

`demo_stream.py`：

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    deltas = []

    async def collect(agent_, delta):
        deltas.append(delta)
        return delta

    agent.hooks.on_provider_delta["_turn"](collect, by="diag")
    context = agent._assemble_context()

    for stream in (True, False):
        deltas.clear()
        print(f"== provider_gen(stream={stream}) ==")
        response = await agent.provider_gen(context, stream=stream, by="_turn")
        print(
            f"  delta 数量={len(deltas)}；finish={response.finish}；"
            f"message.usage 已附着={response.message.usage is not None}"
        )
        print(
            "  已收到可见文本="
            f"{any(delta.kind == 'text' and delta.text for delta in deltas)}"
        )

    await runtime.shutdown()


asyncio.run(main())
```

安装 Flowing 并在环境中设置 `DEEPSEEK_API_KEY` 后，在项目根目录运行
`uv run python demo_stream.py`。调用输入只有上方的系统提示词，没有用户消息。
一次记录的结构如下：

```text
== provider_gen(stream=True) ==
  delta 数量=40；finish=True；message.usage 已附着=True
  已收到可见文本=True
== provider_gen(stream=False) ==
  delta 数量=1；finish=True；message.usage 已附着=True
  已收到可见文本=True
```

流式模式逐段发送 delta，非流式模式发送一条合成 delta；两种模式结束时，
组装后的消息都附有 usage。具体数量和模型生成文本取决于服务商响应。

## 小结

1. Provider 接收 `Context`；adapter 负责请求映射与响应归一。
2. 流式模式逐段发送 delta，非流式模式发送一条完整 delta；两者遵循相同的
   订阅契约。
3. `on_provider_delta` 是观察钩子，PROVIDER 消息的 `usage` 字段是持久化的
   用量记录。
4. `on_provider_error` 是处理 Provider 调用失败的决策钩子。
