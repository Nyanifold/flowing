# 1-1 · 智能体运行时：LLM 接口的性质与框架职责

> 复现条件：Python 3.13+、uv、可用的 Flowing CLI、DeepSeek API key，以及终端网络连接。若要切换到 OpenRouter 的 GPT-6 Luna，还需 `OPENROUTER_API_KEY`。
> 本篇末尾提供运行所需的完整配置、提示词、输入和示例输出；新建空目录后按代码块标注的文件名保存即可复现。

## 这篇讲什么

建立“智能体”这一概念的基础：大语言模型的对外接口有什么性质，
以及为什么在模型之上需要一个运行时层、这个层负责什么。

## 背景知识

大语言模型（LLM）对外暴露的调用形式可以概括为：

```python
def llm(system_prompt: str, messages: list[Message]) -> str:
    ...
```

输入指令与对话历史，输出文本。这个接口有三个在工程上重要的性质：

1. **只接受文本、只产出文本**。模型不直接访问文件系统、网络或
   时钟；参数内化的知识有截止时间。
2. **无服务端会话状态**。每次调用需要把相关历史整体发送；可用
   上下文长度受模型上下文窗口限制，超出即调用失败。
3. **输出是概率性的**。多数情况下正确，但会以确定的语气输出
   错误内容（幻觉）。工程上必须按“输出可能错误”设计校验与
   约束。

## 核心概念

### 为什么需要运行时层

直接使用裸接口做产品会遇到四个问题，每个都对应一层框架职责：

| 问题 | 运行时职责 |
|---|---|
| 每轮要手工拼历史、提示词、工具定义 | **上下文装配**：按统一规则组装请求 |
| 模型输出的是意图，动作要有人执行 | **执行权**：代模型执行工具调用，执行前后可拦截 |
| 对话要跨请求、跨进程持续 | **状态管理**：历史与业务状态的持久化与恢复 |
| 输出可能错误、动作可能危险 | **拦截扩展**：在固定时点插入校验、审批、改写 |

这四个职责构成 Agent 框架的定义。一次最小调用在概念上的形态：

```python
result = agent_runtime.query(
    "查订单 4521",
    system="你是订单助手",        # 上下文装配
    tools=[query_order],          # 执行权归属框架
    history=conversation,         # 状态：历史由客户端维护
)
```

一次问答在运行时内部的链路：

```mermaid
flowchart LR
    U[用户输入] --> R[运行时：装配上下文]
    R --> M[模型服务]
    M --> R2[运行时：响应写入历史]
    R2 --> U2[用户看到答复]
```

其他框架的职责划分大同小异，差别在边界与默认值。

### 对话的历史由谁维护

模型服务端不保存会话。用户感受到的“对话”是框架在本地维护
历史、每轮整体重放出来的结果。

按文末完整示例启动会话，在同一会话里连问两句。下面是便于讲解的
示例输出；模型措辞可能变化，推理轨迹不展示：

```console
(agent-main)>>> 用一句话介绍你自己。
[thinking] (reasoning trace omitted)
我是简洁的中文助手，乐于用简短回答帮你解决问题。
(agent-main)>>> 我刚才问了你什么？
[thinking] (reasoning trace omitted)
你刚才问的是：“用一句话介绍你自己。”
```

逐行看：提示符 `(agent-main)>>>` 之后是你键入的问题，随后是模型
给出的答复；推理轨迹不在本文展示。第一轮你问
“用一句话介绍你自己”，模型答了一句话；第二轮你的新问题只有
“我刚才问了你什么”七个字，模型却准确复述了第一个问题。模型
服务端在两轮之间什么都没保存——第一轮的问题能出现在第二轮的
回答里，唯一的解释是运行时把第一轮的消息历史整体装进了第二轮
的请求。这就是“历史由客户端维护”的直接证据。

这一事实有两个直接推论：

- 对话越长，每轮请求越大，成本与延迟随之上升，上下文预算因此
  是 Agent 系统的常规观测项；
- 历史在谁手里，谁就能改写、裁剪、分支它，“上下文工程”因此
  是 Agent 开发的主要工作内容之一。

### 系统提示词的位置

每轮请求携带的指令文本声明 Agent 的角色与工作方式。它对模型
行为的影响是概率性的：提高遵从的可能性，不构成保证。约束性
行为由工具表（第 1-3 篇）与拦截机制（第 2-4 篇）承担；系统
提示词负责引导。

系统提示词在每次请求前现场装配，因此可以承载运行期注入的值。
文末完整示例的第二个启动命令演示这一点：

```console
$ uv run flowing repl . --user_name 小明
```

示例对话如下；模型措辞可能变化，内部推理内容不展示：

```console
(agent-main)>>> 我叫什么名字？请只回答名字本身。
[thinking] (reasoning trace omitted)
小明
```

逐行看：你问“我叫什么名字？”，模型答出“小明”。这段会话
的历史里从未出现过这个名字——它是启动参数 `--user_name 小明`
经入口装配进系统提示词模板的，模型在请求里读到提示词才知道
这个名字。系统提示词不是静态文件，而是每轮请求前由运行时现场
组装的产品。

## 常见误区

1. **把系统提示词当约束机制**。提示词提高遵从概率；能力边界
   与危险动作控制应落在工具表与拦截点上；
2. **认为模型服务端记得会话**。跨轮记忆完全由客户端维护——
   “模型忘了”优先检查本地历史与恢复逻辑。

## 练习

1. 列出你使用过的一款对话产品，指出其中哪些功能属于运行时
   职责（提示词模板、工具、记忆、审核），哪些属于模型本身；
2. 在下方完整示例中新开会话，问模型“我们这是第几轮对话”，再在
   同一会话里重问一次；比较两次回答，并说明历史重放如何影响回答。

## 完整示例：运行时、提示词与对话

以下内容构成一个完整的最小会话。在空目录中创建所列文件。将 `...`
替换为自己的凭证并仅设置在环境变量中，
不要把真实凭证写入文件。

`pyproject.toml`：

```toml
[project]
name = "flowing-chapter-1-1"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["flowing-agent"]

[tool.uv]
package = false
```

`main.py`：

```python
from flowing import Runtime


async def main(user_name: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    root = await runtime.mount("@/root.fya", agent_id="agent-main")
    if user_name is not None:
        root.user_name = user_name
    return runtime
```

`root.fya`（其中的 `$system_prompt` 是本例完整提示词）：

```yaml
description: 最小问答助手
model_tag: default
---
$system_prompt:
你是一个简洁的中文助手，回答控制在三句话以内。{% if user_name %}用户叫做 {{ user_name }}，回答时可以直接以名字相称。{% endif %}
```

`providers.yaml`：

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
openrouter:
  adapter: openrouter
  base_url: https://openrouter.ai/api/v1
  api_key: "{{env.OPENROUTER_API_KEY}}"
```

`models.yaml`：

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
openrouter-gpt-6-luna:
  provider: openrouter
  model: openai/gpt-6-luna
  "reasoning.effort": high
```

`model-tags.yaml`：

```yaml
tags:
  default: deepseek-flash
  luna: openrouter-gpt-6-luna
```

当前 `default` 标签仍选择 DeepSeek。将 `root.fya` 中的 `model_tag` 改为 `luna`，
即可经 OpenRouter 调用 `openai/gpt-6-luna`。`"reasoning.effort": high` 是 GPT-6 Luna
模型条目的字面扩展键，只随该模型传给 OpenRouter；DeepSeek 条目没有该设置。未设置
`OPENROUTER_API_KEY` 时加载配置会告警，`default` 仍可使用 DeepSeek；`luna` 需要该密钥。

从包含这些文件的当前目录执行：

```sh
uv sync
export DEEPSEEK_API_KEY="..."
uv run flowing repl .
```

第一轮的完整输入（逐行输入，最后输入 `/exit` 结束）：

```text
用一句话介绍你自己。
我刚才问了你什么？
/exit
```

对应的示例输出（回答会因模型而异；内部推理轨迹以占位符表示）：

```console
(agent-main)>>>用一句话介绍你自己。
[thinking] (reasoning trace omitted)
我是简洁的中文助手，乐于用简短回答帮你解决问题。
(agent-main)>>>我刚才问了你什么？
[thinking] (reasoning trace omitted)
你刚才问的是：“用一句话介绍你自己。”
(agent-main)>>>/exit
```

第二种启动方式与输入如下。`--user_name` 的值在运行期注入提示词：

```console
$ uv run flowing repl . --user_name 小明
```

```text
我叫什么名字？请只回答名字本身。
/exit
```

```console
(agent-main)>>>我叫什么名字？请只回答名字本身。
[thinking] (reasoning trace omitted)
小明
(agent-main)>>>/exit
```

## 小结

1. LLM 接口只进文本、无服务端状态、输出概率性；
2. 运行时层承担上下文装配、执行权、状态管理、拦截扩展四项职责；
3. 对话历史由客户端维护并重放，上下文工程是主要开发内容；
4. 系统提示词负责引导，每轮请求前现场装配。
