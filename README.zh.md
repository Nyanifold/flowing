# Flowing

[English](README.md) | **中文**

[📖 文档](https://flowing-agent.readthedocs.io/zh_CN/index.html)

Flowing 是一个为复杂交互设计的轻量级、可扩展的描述式 Agent 运行时框架（Python ≥ 3.13）。在 Flowing 中，一个 Agent 的全部定义，包括角色与提示词、大语言模型、工具与子智能体、 Composable 扩展、钩子代码等，都写在同一个 `.fya` 文件里；框架在执行管线的关键时点向扩展开放，它的运行时亦可作为普通对象嵌入任意 Python 宿主应用。框架核心仅承担消息流转、错误分类与钩子点分发三项职责；重试、压缩、审批等策略均以 Composable 或插件形式按需挂载。

## 它适合谁

- 希望框架的每一层行为都可读、可改、可审计，而不是面对一份黑盒的策略配置；
- 需要对会话历史做细粒度操作——在任意消息上开平行分支、改写或裁剪历史，而不只是追加式地对话；
- 需要同一种能力在不同 Agent 上呈现不同的模型视图，并要求“注册了不等于模型能看见”的显式安全边界。

这些需求并不指向某一种特定的应用形态：在 Flowing 中，编排逻辑就是普通的 Python 代码，顺序、分支与并发都由语言本身表达。因此，作为一个基础运行时框架，它可以用来构建编程、教育、电商、陪伴等各类智能体或多智能体系统，也可以用来开展多智能体交互实验。

## 核心特性

- **消息驱动的运行模型**：每个 Agent 实例持有一条优先级消息队列和一个常驻工作循环；用户输入、模型响应、工具结果、外部事件、子 Agent 回执统一表示为消息，逐条驱动回合。`query()` 等待回合结果，`message()` 投递即返回，`steer()` 在回合进行中导向。
- **消息级树的历史**：对话历史是由消息构成的森林，而不是线性列表。`fork()` 只切换游标就能开出平行分支，旧分支完整保留；历史本身支持插入、分支、删除、改写、重挂五种手术操作，改动照常落盘。
- **三层能力描述**：可执行对象、LLM 可见声明、Agent 级绑定三个维度独立演化——同一个工具可以在不同 Agent 上呈现不同的名称、描述和参数视图。内置工具随运行时就绪，但必须显式声明才对模型可见。
- **声明式 `.fya`**：Agent 的描述、模型意图、能力绑定、系统提示词与钩子代码收进同一个自包含文件；编译产物与手写的 Agent 子类完全等价。
- **实例级钩子系统**：每个实例独立持有钩子注册表，钩子点覆盖生命周期、回合、消息、模型调用、工具执行与子 Agent 各族；handler 可以改写数据、异步等待外部确认，或抛出 `Intercepted` 硬阻断当前操作——人工审批闸由此自然实现。
- **自动持久化与崩溃恢复**：消息与状态以 write-behind 方式落盘，进程崩溃后重放重建；缺失配对的 tool_call 在恢复时自动封闭，模型看到的历史永远完整成对。
- **零代码工具接入**：MCP 服务（stdio / SSE / HTTP）、shell 命令模板（参数自动转义）、HTTP 接口，都可以用一份声明文件直接变成工具。
- **完整的暴露方式**：REPL、一次性 CLI、纯 HTTP API、内置 Web 前端、CI 冒烟测试等八个子命令，同一个项目无需改动即可用任意方式运行。

## 安装

```bash
pip install flowing-agent
```

## 快速上手

这个示例会搭建一个双智能体猜词游戏：裁判 `oracle` 私下保管谜底，猜词者 `guesser` 通过定向消息逐步提问并猜出答案。它展示如何在一个 Runtime 中承载多个 Agent、由 `PeersPlugin` 注册 Peers 工具，并用声明式工具绑定控制 Agent 的工具可见性。工具按 `peer_id` 通过 Runtime 查询目标 Agent。两个 Composable 各司其职：`use_peers()` 读取 `peers` 目录并将其注入系统提示词，`use_retry()` 为模型调用配置重试，裁判使用默认设置而猜词者最多重试五次。

新建一个项目目录，并创建以下四个文件：

`main.py` 安装 Peers 插件，并以稳定 ID 挂载两个 Agent：

```python
from flowing import Runtime
from flowing.plugins.peers import PeersPlugin

async def main() -> Runtime:
    runtime = Runtime()
    runtime.set_model_tags("@/model-tags.yaml")
    # 注册 Peers 工具；Agent 是否能调用工具仍由各自的 tools: 声明决定。
    runtime.install(PeersPlugin())
    await runtime.mount("@/oracle.fya", agent_id="oracle")
    await runtime.mount("@/guesser.fya", agent_id="guesser")
    return runtime
```

`oracle.fya` 描述知道谜底的裁判。它只允许向 `guesser` 发送消息：

```python
description: "私下保管谜底，并判断猜词者的问题。"
model_tag: default
tools:
  - message-peer
peers:
  guesser: "猜词者；通过私聊向你提问。请回复其答案。"

---
$system_prompt:
你是猜词游戏的裁判。用户会私下告诉你谜底。记住谜底，但不要向猜词者透露。
猜词者发来问题时，根据谜底判断答案是肯定、否定还是无法判断。你必须调用
message-peer 工具，将 peer_id 设为 "guesser"，并发送包含原问题及以下三种
内容之一的消息：是、否、不确定。不要添加解释或其他文字，也不要直接回复猜词者。
用户私下告诉你谜底时，只需简短确认设置完成，不要联系猜词者。

---
$script:
from flowing.composables import use_retry
from flowing.plugins.peers import use_peers


async def setup(self):
    # 注入 peers 目录提示，并启用默认的 Provider 调用重试策略。
    use_peers(self)
    use_retry(self)

    # peer 消息发出后结束当前回合，等待对方的下一条消息。
    def _end_turn(agent, result):
        if result.status == "completed" and agent.current_turn is not None:
            agent.current_turn.finish = True
        return result

    self.hooks.after_tool_call["message-peer"](_end_turn, by="guessing-game")
```

`guesser.fya` 描述通过提问推断谜底的猜词者：

```python
description: "通过向裁判提问来猜出谜底。"
model_tag: default
tools:
  - message-peer
peers:
  oracle: "知道谜底并回答是、否或不确定的裁判。请向其询问问题。"

---
$system_prompt:
你正在玩猜词游戏。当用户说明开始后，请每次向 oracle 智能体（裁判）提出一个可以用是或否回答的问题。
不要要求裁判直接说出谜底。收到裁判回复后，根据游戏规则继续向该智能体提问；得到最终答案时，向用户
给出你的最佳猜测。

---
$script:
from flowing.composables import use_retry
from flowing.plugins.peers import use_peers


async def setup(self):
    # 注入 peers 目录提示，并将单回合重试上限设为五次。
    use_peers(self)
    use_retry(self, max_retries=5)

    # peer 消息发出后结束当前回合，等待裁判的回复。
    def _end_turn(agent, result):
        if result.status == "completed" and agent.current_turn is not None:
            agent.current_turn.finish = True
        return result

    self.hooks.after_tool_call["message-peer"](_end_turn, by="guessing-game")
```

`model-tags.yaml` 将查询标签 `default` 映射到 Model 条目 `luna`：

```yaml
tags:
  default: luna
```

要让这两个 Agent 调用模型，先添加 OpenRouter Provider 并配置连接信息：

```console
$ flowing-config providers add
# Press Enter at the configuration path prompt to use the Runtime default.
Provider entry name (identity): openrouter
Provider adapter: openrouter
API endpoint (base_url; leave blank to use 'https://openrouter.ai/api/v1'): [Enter]
API key (api_key; leave blank to skip): env.OPENROUTER_API_KEY
```

Provider 配置完成后，再登记引用它的 Model 条目。示例将条目命名为 `luna`，并指定 API model ID `openai/gpt-6-luna`：

```console
$ flowing-config models add
# Press Enter at the configuration path prompt to use the Runtime default.
Model entry name (identity): luna
Known Provider entries: openrouter
Provider entry name (identity): openrouter
API model ID: openai/gpt-6-luna
```

`default` model-tag 是查询标签，不携带 Provider 信息；它映射到 `luna` Model 条目，而条目再单独指定 `openrouter` Provider 和 API 模型 ID `openai/gpt-6-luna`。adapter 参数提示依赖 API 模型 ID，不保证远端模型支持对应参数。

这里有两种不同的声明：`tools:` 显式绑定 Agent 可调用的 `message-peer` 工具；`peers:` 列出可联系 Agent 的 ID 与说明。`PeersPlugin` 注册 Peers 工具；工具调用时按声明的目标 ID 通过 Runtime 查询目标 Agent。`use_peers(self)` 这个 Composable 只读取当前 Agent 的 `peers`，将目录动态注入系统提示词，不负责工具绑定。两个 Agent 都通过 `use_retry()` 启用 Provider 调用重试；裁判使用默认设置，猜词者使用 `max_retries=5`。

运行：

```console
$ export OPENROUTER_API_KEY='<your key>'
$ flowing repl .
(new agent)>>> /agent oracle
(oracle)>>> 谜底是梨。请记住谜底，不要告诉猜词者。
(oracle)>>> /agent guesser
(guesser)>>> 开始游戏。谜底是梨、苹果或香蕉之一。你最多可以提出三个是非问题，然后给出你的猜测。
```

`guesser` 通过 `message-peer` 向 `oracle` 发送问题，裁判只返回“是”“否”或“不确定”；谜底留在裁判自己的会话中。两个 Agent 的消息记录都保存在项目目录下的 `.flowing/` 中，固定的 `agent_id` 让再次启动时可以恢复各自的历史。

## 进一步

- **能力接入**：`tools:` 声明内置工具、MCP 服务，或将 shell 命令与 HTTP 接口声明为工具；自定义逻辑经 `ScriptTool` 实现。
- **多智能体**：`subagents:` 声明子智能体类型，编排者依据自动生成的目录路由派单；也可以在代码中直接创建子 Agent 并行执行。`PeersPlugin` 则让同一 Runtime 中的 Agent 通过 `peers:` 目录和消息队列定向交互。
- **运行介入**：在钩子点挂载 handler——工具执行前拦截待审批、回合收尾时审计、Provider 出错时换模型重试。内置的 `use_retry` / `use_compact` 即按此模式实现，可直接参考改写。
- **嵌入宿主**：`launch()` 返回的 Runtime 由宿主持有，输入走 `query` / `message` / `steer`，输出经钩子订阅（流式输出、完成通知、调用拦截）。

## 文档

- [入门教程](https://flowing-agent.readthedocs.io/zh_CN/beginner-tutorial/)：面向 Agent 系统开发的初学者；
- [详细教程](https://flowing-agent.readthedocs.io/zh_CN/tutorial/)：从快速上手到核心机制与运维；
- [精简参考](https://flowing-agent.readthedocs.io/zh_CN/flowing-ref/)：四篇覆盖整个框架，适合查阅；
- [API 参考](https://flowing-agent.readthedocs.io/zh_CN/api.html)：按模块组织的公开 API。

## 项目说明

0.x 阶段仍可能有非兼容更新；每次此类更新都会附带详尽的更新日志并说明迁移方式，详见 [changelogs/](changelogs/) 目录。

本项目在开发过程中高度依赖 AI 辅助生成，使用了来自不同提供商、不同能力强度的模型。受个人精力所限，作者未能逐行检查全部代码；若您发现实现与文档（docstring、教程）之间存在分歧，欢迎提出 issue 指正。

## License

[MIT](LICENSE) © 2026 Nyanifold

---

<p align="center">
  <img src="assets/oh-wishes.svg" alt="Oh wishes... I beg you coalesce!">
</p>
