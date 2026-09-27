# 示例：3-1 跨层共享

根智能体提供 locale 并将问候任务委派给 greeter 子智能体；子智能体从父级链注入该值。另一个脚本演示缺少键时的边界。下文完整内联了所需代码、配置、提示词、输入与示例输出。

运行环境需要 Python 3.13 或更高版本、uv，以及可使用 Flowing CLI 和包的 Python 环境。模型调用还需要在 shell 中设置你自己的 `DEEPSEEK_API_KEY`；本文不含真实凭证。请在全新的工作目录下按下文相对文件名创建文件，并从该目录执行命令。

## 复现

### 请求英文问候

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run flowing repl . --locale en < repl_input.txt
```

### 在全新状态下请求默认语言问候

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run flowing repl . < repl_input_run2.txt
```

### 演示 provide 值缺失

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run python demo_boundary.py
```

语言对照的每次运行都应使用独立的全新工作状态，因为运行时会持久化参数。

## 完整内联材料

下方每个代码块都包含本示例所用相对文件的完整内容。输出代码块是示例留档；模型答复和主机相关信息可能变化。

### `agents/greeter/agent.fya`

```text
description: 问候员：按链上提供的 locale 用对应语言问候。
model_tag: default
---
$system_prompt:
你是问候员。locale = {{ locale }}。
{% if locale == 'zh' %}请用中文问候用户。{% else %}请用英文问候用户。{% endif %}
只输出问候语本身。
---
$script:
async def setup(self):
    # 沿亲代链上溯查找（终点 Runtime）；未命中抛 MissingProvideError
    self.locale = self.inject("locale")
```

### `demo_boundary.py`

```python
"""provide-inject 边界演示：inject 未命中 → MissingProvideError。

运行：uv run python demo_boundary.py
"""
import asyncio

from flowing import launch
from flowing.errors import MissingProvideError


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    try:
        agent.inject("no_such_key")
    except MissingProvideError as exc:
        print(f"inject 未命中 → MissingProvideError: {exc}")
    await runtime.shutdown()


asyncio.run(main())
```

### `demo_output.txt`

```text
inject 未命中 → MissingProvideError: Missing provide value for key: 'no_such_key'
```

### `main.py`

```python
"""参数与 setup() 演示子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # mount 之前 provide：影响装配的值在挂载前提供（链式语义：沿亲代链上溯查找）
    runtime.provide("timezone", "Asia/Shanghai")
    # CLI 的 --key value 经 launch 原样透传 main(**kwargs)，
    # 再由 main 决定哪些参数交给 mount（→ 创建管线 → setup(**args)）
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

### `model-tags.yaml`

```yaml
# 标签 → 模型条目名映射（单值：一个标签只映射一个条目）。
tags:
  default: deepseek-flash
```

### `models.yaml`

```yaml
# 模型条目：一个条目 = 一个具体模型（绑定一个 provider 条目）。
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### `providers.yaml`

```yaml
# provider 条目：一个条目 = 一个 API key 身份。
# {{env.VAR}} 在加载期替换；缺失时替换为空串并告警（warnings.warn），加载不中断。
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `repl_input.txt`

```text
请派 greeter 问候我。
/exit
```

### `repl_input_run2.txt`

```text
请派 greeter 问候我。
/exit
```

### `repl_output.txt`

```text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "greeter", "prompt": "问候用户。"}
[tool:completed] subagent-invoke -> 
问候员 greeter 的问候语如下：

**Hello!**
(agent-main)>>>
```

### `repl_output_run2.txt`

```text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "greeter", "prompt": "请向用户问好。"}
[tool:completed] subagent-invoke -> 
问候员 greeter 的问候语如下：

> 你好！很高兴见到你，有什么我可以帮你的吗？
(agent-main)>>>
```

### `root.fya`

```text
description: 编排者：provide locale，把问候任务派给问候员。
model_tag: default
args:
  locale:                # 根 Agent 的参数 → provide 到链上
    type: string
    default: zh
tools:
  - subagent-invoke
subagents:
  - ./agents/greeter
---
$system_prompt:
你是编排者。收到问候请求时，调用 subagent-invoke 派给问候员 greeter
（不传参数），把它的问候语原样转达给用户。
---
$script:
async def setup(self, locale: str = "zh"):
    self.locale = locale
    self.provide("locale", locale)   # 挂上 provide 链：后代 Agent 经 inject 可得
```
