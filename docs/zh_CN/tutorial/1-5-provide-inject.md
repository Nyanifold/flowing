# 1-5 · provide 与 inject

## 前置阅读

[1-3 参数与 setup()](1-3-agent-args-and-setup.md)（`setup()` 里的
`provide` / `inject` 调用）。本篇将它们用于父 Agent 与问候员子智能体。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| `provide(key, value)` | 在本节点注册一个跨层共享的运行期值 |
| `inject(key)` | 沿亲代链先近后远上溯查找 provide 值，终点 Runtime；未命中抛 `MissingProvideError` |
| provide 链 | 由 `_parent_id` 亲代链构成的查找路径：子 Agent 可见祖先的值（多智能体拓扑见 3-1） |
| `MissingProvideError` | inject 上溯到链顶仍未命中时抛出的异常 |

## 目标

掌握跨层共享值的正确通道：什么时候 provide、什么时候 inject、值沿什么
路径流动、边界在哪里。

## 正文

### 链式上溯：先近后远，终点 Runtime

`provide(key, value)` 在本节点注册；`inject(key)` 从当前节点沿亲代链
逐级向根查找，找到即返回。Runtime 是链的终点——根 Agent 的
`provide` 对全树所有后代可见。这带来两条直接推论：

- **跨层共享用 inject**：根 provide 一次，任意深度的子 Agent inject 可得；
  凭证等敏感值应在**深层节点** provide，收窄可见范围；
- **同 key 覆盖写、实时查找**：重复 provide 是覆盖更新，inject 每次实时
  上溯——运行期切换配置（如 locale）的正规通道。

### 四条边界

1. **敏感信息边界**：注入值不进消息流、不进 LLM 上下文、不落盘——
   API key / 用户身份等走这条通道；但沿链对后代可见，所以凭证要放
   深层节点；
2. **未命中即抛**：`inject` 找不到 key → `MissingProvideError`
   （主线示例演示 3）；
3. **影响装配的值在 `mount()` 前 provide**（如 1-3 的 timezone）；
4. 跨 Agent 的重资源（连接池、索引）不走注入链，走 Resource
   （`runtime.register_resource`，4-8 一带而过）。

### 与模板求值的关系

provide 的值不是模板的直接上下文；模板要消费它，须在 `setup()` 里
`self.xxx = self.inject(key)` 落到实例属性（模板上下文 = 实例属性摊平，
求值体系见 4-9）。本篇问候员的 `{{ locale }}` 就是这么来的。

## 本篇不覆盖

- `InjectionKey[T]` 类型安全键——4-4；
- Resource 注册与树外直引——4-8；
- 运行期动态切换值的完整场景（钩子内 provide 改写行为）——4-6 一带而过；
- 声明处注入表达式（绑定层 `specified` 的注入值）——4-4。

## 主线示例

工程结构：根 Agent（编排者）在 `setup()` 里
`self.provide("locale", locale)`；问候员在
自己的 `setup()` 里 `self.locale = self.inject("locale")`，模板按 locale
分支选语言。`--locale` 换值，问候语言随之切换。

**演示 1：`--locale en` → 英文问候**：

```console
$ uv run flowing repl . --locale en
(agent-main)>>> 请派 greeter 问候我。
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "greeter", "prompt": "问候用户（无额外参数）。"}
[tool:completed] subagent-invoke -> Hello! How can I help you today?
Hello! How can I help you today?
(agent-main)>>> /exit
```

**演示 2：默认 `zh` → 中文问候**：

```console
$ uv run flowing repl .
(agent-main)>>> 请派 greeter 问候我。
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "greeter", "prompt": "请问候用户。"}
[tool:completed] subagent-invoke -> 你好！
你好！
(agent-main)>>> /exit
```

读这两段交互：问候员两次都是被唤起时才创建的新实例，它的 `locale` 不
来自参数（`args:` 是空的），而是创建时沿亲代链上溯 inject 到的——
provide 换值（`--locale en` vs 默认 `zh`），子智能体行为随之切换。
`prompt` 是必填字段且不能为空；传入空字符串会返回
`[tool:error] prompt is required and must be non-empty: the task the subagent should work on`。
此校验失败会反馈给模型以便自我修正，不会变成回合异常。

**演示 3：inject 未命中**（下方给出 `demo_boundary.py` 完整内容）：

```console
$ uv run python demo_boundary.py
inject 未命中 → MissingProvideError: Missing provide value for key: 'no_such_key'
```

## 完整示例材料

在已安装 Flowing 的项目中按以下相对文件名创建内容。运行 REPL 前，在环境中
设置 `DEEPSEEK_API_KEY`。声明、提示词、输入与输出均列在本文，不需要另找
示例文件。

`main.py`：

```python
from flowing import Runtime


async def main(locale: str = "zh", resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        kwargs = {"locale": locale} if locale != "zh" else {}
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
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
description: 编排者：provide locale，把问候任务派给问候员。
model_tag: default
args:
  locale:
    type: string
    default: zh
tools:
  - subagent-invoke
subagents:
  - ./agents/greeter
---
$system_prompt:
你是编排者。收到问候请求时，用非空 prompt 调用 subagent-invoke 派给
问候员 greeter（不传其他参数），并把问候语原样转达给用户。
---
$script:
async def setup(self, locale: str = "zh"):
    self.locale = locale
    self.provide("locale", locale)
```

`agents/greeter/agent.fya`：

```yaml
description: 问候员：按链上提供的 locale 使用对应语言问候。
model_tag: default
---
$system_prompt:
你是问候员。locale = {{ locale }}。
{% if locale == 'zh' %}请用中文问候用户。{% else %}请用英文问候用户。{% endif %}
只输出问候语本身。
---
$script:
async def setup(self):
    self.locale = self.inject("locale")
```

英文运行的输入：

```text
请派 greeter 问候我。
/exit
```

```console
$ uv run flowing repl . --locale en
```

预期行为：父 Agent 派发一个新建的问候员；问候员注入到的 locale 为 `en`，
因此返回英文问候，例如 `Hello! How can I help you today?`。

默认中文运行的输入：

```text
请派 greeter 问候我。
/exit
```

```console
$ uv run flowing repl .
```

预期行为：缺省 `locale: zh`，问候员返回中文问候；具体措辞由配置的模型生成。

以下完整 Python 片段单独演示键缺失：

```python
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

预期输出：

```text
inject 未命中 → MissingProvideError: Missing provide value for key: 'no_such_key'
```

## 小结

1. provide 注册、inject 沿亲代链先近后远上溯，终点 Runtime；
2. 同 key 覆盖写、实时查找——运行期切换配置的正规通道；
3. 敏感值走注入链（不进消息/上下文/落盘），凭证放深层节点收窄可见范围；
4. 未命中抛 `MissingProvideError`；模板消费注入值须经实例属性中转。
