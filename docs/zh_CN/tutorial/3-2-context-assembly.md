# 3-2 · 上下文组装

## 前置阅读

[3-1 消息树与循环机制](3-1-message-tree-and-loop.md)（树与游标）、[1-5
provide 与 inject](1-5-provide-inject.md)。本文内联完整配置、prompt、
程序、Parsable 文件内容、输入与记录输出；离线演示不调用模型，模式切换
示例则调用模型以展示不同回答形态。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| `Context` | Provider.generate() 的唯一输入：三正交字段 system_prompt / tools / messages |
| 现场组装 | 每次调模型前重新组装、重新求值——惰性求值是功能正确性前提，不是性能选择 |
| prompt 块 | system prompt 的分层片段：`PromptBlock` 注册声明 → 组装时求值为 `PromptSegment` |
| 意图通道 | `cache` 三值（static/dynamic/session）只是给 adapter 的缓存意图标记——框架本地不读、不缓存 |
| 模式切换 | 注册两套以上 prompt 块、按 tag 启停的正规手法（disable/enable_by_tag） |

## 目标

掌握上下文组装的机制面：3-1 的树如何变成 Provider 的唯一输入，以及
prompt 分层与模式切换。

## 正文

### Context：三个正交字段

| 字段 | 内容 |
|---|---|
| `system_prompt` | 按序排列的 `PromptSegment` 列表，保留 cache 标记。 |
| `tools` | 当前启用的工具声明。 |
| `messages` | 从根节点到当前 head 的消息路径。 |

adapter 的映射是机械逐字段转换（不得合并后再拆）。**现场组装、零缓存**：
每次组装重新求值所有 Parsable、重新收集消息路径、重新生成工具定义——
环境变量、实例属性永远取最新值。

### prompt_blocks：分层注册

`agent.prompt_blocks` 是按注册顺序拼接的分层列表：

- **`[0]` 是框架注入的惰性引用块**（内容 `{{ self.system_prompt }}` 模板，
  `by="core"`）：`setup()` 里改类属性 `system_prompt`，下次组装自动反映；
  **不得删除**（system prompt 会消失，框架不兜底）；
- `append("example", "Prompt text", cache="static", by="example", tags=["mode"])`
  用于注册一块；字符串会归一为 Parsable，但注册时不求值；
- 管理操作包括 `disable_by_tag`、`enable_by_tag` 和 `remove_by_owner`。
  **模式切换的正规手法是注册两套块并按 tag 启停**（主线示例）；
- 动态内容放进 Parsable 模板引用（如 `{{ current_mode }}`），随组装
  现场求值——**反模式**：每 Turn 增删块（破坏前缀缓存）；高频值（当前
  时间）应走消息通道（`before_turn` 附加式注入，1-9 的 reminder 即此）。

### `cache` 只是意图通道

`cache="static" / "dynamic" / "session"` 是给 Provider adapter 的缓存
优化提示（如 Anthropic 家族只给 `static` 段设前缀缓存标记）；框架本地
不读、不据此跳过求值。

### before_provider_gen：整体改写 Context

组装产物经 `before_provider_gen` 钩子后才发给 Provider——handler 可
改写三个字段（文末完整离线演示展示了这一点）。不建议改 `messages`（改动不
落盘，下次组装即丢）。

## 本篇不覆盖

- Parsable 五种形式与渲染上下文的完整求值体系见 4-9。
- `ContextUsageEstimate` 预算观测见 3-4。
- adapter 侧如何消费三个字段见 3-3。

## 主线示例

**演示 1：Context 结构、Parsable 与钩子改写**（离线运行，不调用模型）：

```console
$ uv run python demo_context.py
① Context 三正交字段：
  system_prompt: [('system_prompt', 'dynamic'), ('mode-explain', 'static')]
  tools: ['switch-mode']
  messages: 0 条（根 → head 路径）
② Parsable 常用形式（字面量 / 模板 / 文件引用）：
  字面量   : '你好'
  模板     : '当前模式：讲解'
  文件引用 : '先组装，再提问；上下文是视图，不是仓库。'
  现场求值 : 改 current_mode 后同一模板 → '当前模式：实验模式'（取最新值）
③ before_provider_gen 改写 Context：
  改写后最后一段: marker = '[改写标记]'
  [0] 核心引用块仍在: system_prompt
```

空持久化状态下，演示 1 的 messages 数量为 0；复用已有会话状态时，数量会
随消息树改变。因此，0 只适用于空状态，已有持久化消息时应以实际组装出的
消息路径为准。

**演示 2：模式切换（prompt 块按 tag 启停）**：

```console
(agent-main)>>> 什么是消息树？用要点式讲解。
[thinking] (reasoning trace omitted)
消息树是由父子关系连接起来的分层结构。
- 原始消息是根节点，回复成为子节点，因此对话可以分支，而不只是平铺成列表。
- 邮件回复标头或聊天线程 ID 等显式元数据可以建立消息之间的连接。
- 邮件、论坛和聊天中的线程视图可以沿着结构追踪主题，并展开或折叠分支。
(agent-main)>>> 切换到诗歌模式，回答同样的问题。
[thinking] (reasoning trace omitted)
[tool_call] switch-mode {"mode": "poem"}
[tool:completed] switch-mode ->
山间，信号树倒下，传来警报；
屏幕上，每条回复长成枝桠。
文字沿着连接它们的脉络延展，
四行诗勾勒出树的形状。
(agent-main)>>> 再切回讲解模式，回答同样的问题。
[thinking] (reasoning trace omitted)
[tool_call] switch-mode {"mode": "explain"}
[tool:completed] switch-mode ->
消息树是由消息组成的层级结构，每条消息都连接到它回复或派生的消息。
- 结构：原始消息是根节点，每条回复成为子节点，因此对话形成分支，而非平铺列表。
- 建立方式：邮件回复标头或聊天线程 ID 等显式元数据可以建立这些连接。
- 作用：它为邮件、论坛和聊天提供分线程视图，便于追踪、展开或折叠分支。
```

读这段会话：同一问题、三种回答形态——形态差异完全来自 prompt 块的
启停（`switch-mode` 工具内部就是 `disable_by_tag("mode")` +
`enable_by_tag(mode)`）。模式是“哪块启用”的状态，不是另一段提示词
的拼贴。

### 完整复现材料

以下各块包含重建两个演示所需的完整配置、prompt、程序与 Parsable 文件
内容。注释中的相对文件名是创建位置标签；命令不要求切换目录。实时模式
切换需要由读者提供 `DEEPSEEK_API_KEY`；本文不包含真实凭证。

```python
# main.py
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh",
               resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        kwargs: dict = {}
        if user_name is not None:
            kwargs["user_name"] = user_name
        if locale != "zh":
            kwargs["locale"] = locale
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

```yaml
# providers.yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

```yaml
# models.yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

```yaml
# model-tags.yaml
tags:
  default: deepseek-flash
```

```yaml
# root.fya front matter and body
description: 模式化回答助手：prompt 块分层 + 模式切换。
model_tag: default
tools:
  - ./tools/switch_mode.py
---
$system_prompt:
你是模式化回答助手。当前模式：{{ current_mode }}。
用户要求切换模式时，用 switch-mode 工具完成切换，然后按新模式回答。
---
$script:
from flowing import Parsable


async def setup(self):
    self.current_mode = "讲解"
    self.prompt_blocks.append(
        "mode-explain",
        Parsable("【讲解模式】请用条理清晰的要点式回答，控制在三点以内。"),
        cache="static", by="mode-switcher", tags=["mode", "explain"],
    )
    self.prompt_blocks.append(
        "mode-poem",
        Parsable("【诗歌模式】请用现代诗的形式回答，四行以内。"),
        cache="static", by="mode-switcher", tags=["mode", "poem"],
    )
    self.prompt_blocks.disable_by_tag("poem")
```

```python
# tools/switch_mode.py
from typing import Literal

from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class SwitchModeArgs(BaseModel):
    mode: Literal["explain", "poem"] = Field(
        description="目标模式：explain=要点式讲解；poem=现代诗"
    )


class SwitchMode(ScriptTool):
    """切换回答模式并更新 current_mode。"""

    name = "switch-mode"
    args_model = SwitchModeArgs

    async def execute(self, *, mode: str, caller: Agent) -> dict:
        caller.prompt_blocks.disable_by_tag("mode")
        caller.prompt_blocks.enable_by_tag(mode)
        caller.current_mode = {"explain": "讲解", "poem": "诗歌"}[mode]
        return {"mode": mode, "current_mode": caller.current_mode}
```

```text
# notes/motto.md
先组装，再提问；上下文是视图，不是仓库。
```

```python
# demo_context.py
import asyncio

from flowing import Parsable, launch
from flowing.context import PromptSegment


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    context = agent._assemble_context()
    print("① Context 三正交字段：")
    print(f"  system_prompt: {[ (s.name, s.cache) for s in context.system_prompt ]}")
    print(f"  tools: {[ tool.name for tool in context.tools ]}")
    print(f"  messages: {len(context.messages)} 条（根 → head 路径）")

    print("② Parsable 常用形式（字面量 / 模板 / 文件引用）：")
    print(f"  字面量   : {Parsable('你好').resolve(agent)!r}")
    print(f"  模板     : {Parsable('当前模式：{{ current_mode }}').resolve(agent)!r}")
    print(f"  文件引用 : {Parsable('$./notes/motto.md').resolve(agent)!r}")
    agent.current_mode = "实验模式"
    print("  现场求值 : 改 current_mode 后同一模板 → "
          f"{Parsable('当前模式：{{ current_mode }}').resolve(agent)!r}（取最新值）")

    async def mark(agent_, context_):
        context_.system_prompt.append(
            PromptSegment(content="[改写标记]", cache="dynamic", name="marker")
        )
        return context_

    agent.hooks.before_provider_gen(mark, by="demo")
    rewritten = await agent.hooks.before_provider_gen.dispatch(
        agent, agent._assemble_context()
    )
    print("③ before_provider_gen 改写 Context：")
    print(f"  改写后最后一段: {rewritten.system_prompt[-1].name} = "
          f"{rewritten.system_prompt[-1].content!r}")
    print(f"  [0] 核心引用块仍在: {rewritten.system_prompt[0].name}")
    await runtime.shutdown()


asyncio.run(main())
```

演示 1 的预期结构化输出如下。空持久化状态下消息数为 0；已有会话状态会
改变这个数值。

```text
① Context 三正交字段：
  system_prompt: [('system_prompt', 'dynamic'), ('mode-explain', 'static')]
  tools: ['switch-mode']
  messages: 0 条（根 → head 路径）
② Parsable 常用形式（字面量 / 模板 / 文件引用）：
  字面量   : '你好'
  模板     : '当前模式：讲解'
  文件引用 : '先组装，再提问；上下文是视图，不是仓库。'
  现场求值 : 改 current_mode 后同一模板 → '当前模式：实验模式'（取最新值）
③ before_provider_gen 改写 Context：
  改写后最后一段: marker = '[改写标记]'
  [0] 核心引用块仍在: system_prompt
```

实时模式切换的完整用户输入为：

```text
什么是消息树？用要点式讲解。
切换到诗歌模式，回答同样的问题。
再切回讲解模式，回答同样的问题。
```

执行实时演示前设置凭证占位符并启动交互入口；将占位符替换为自己的凭证，
不要把凭证写入文档：

```console
$ export DEEPSEEK_API_KEY="<your-api-key>"
$ uv run flowing repl .
```

上方“演示 2”逐轮列出了输入、工具调用与回答。模型自然语言回答可能不同；
稳定机制是两次 `switch-mode` 调用分别启用 poem 与 explain 标签。

## 小结

1. `Context` 的三个正交字段是 Provider 的唯一输入，现场组装且不缓存是正确性前提。
2. prompt 块按层注册，必须保留 `[0]` 核心引用块，模式切换则通过 tag 启停。
3. `cache` 只是给 adapter 的意图通道，动态内容则通过 Parsable 模板引用。
4. `before_provider_gen` 可以整体改写 `Context`，但不建议改写 `messages`。
