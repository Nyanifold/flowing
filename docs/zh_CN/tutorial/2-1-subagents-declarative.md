# 2-1 · 声明式组建多智能体团队

## 前置阅读

[0-3 初识多智能体](0-3-hello-multi-agent.md)（编排者 + `explore-agent` 的
首次接触）。本篇列出完整团队声明、每个智能体的提示词、示例输入与代表性输出。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 类型绑定 | `subagents:` 声明的本质：声明“这个 Agent 可以使用哪个子智能体类型 + 用法”，不是创建实例 |
| `<available_subagents>` | 每次组装上下文时现场渲染进 system prompt 的子智能体目录（catalog）；LLM 的路由依据 |
| catalog 渲染 | 对每个 `visible=True` 的绑定条目预计算视图（别名 / 描述 / 参数段 XML）注入 system prompt 的过程 |
| `visible=False` | 绑定条目的开关：不进 catalog（LLM 不可见）但仍可编程式 `invoke_subagent`——可见性与可执行性分离 |

## 目标

声明式组建多 Agent 团队：编排者声明 coder / reviewer 两个下属，一条
query 触发“写代码 → 审查 → 汇总”的完整协作。

## 正文

### subagents: 声明 = 绑定类型而非实例

```yaml
# root.fya
tools:
  - subagent-invoke        # 唤起工具仍须显式声明（注册 ≠ 可见）
subagents:
  - ./agents/coder         # 路径形态：目录内含 agent.fya
  - ./agents/reviewer
  - ./agents/auditor:
      visible: false        # 带覆写的条目形态（body 从换行缩进开始）
```

`subagents:` 条目引用**子智能体类型**。每个条目是一份绑定：声明时不会创建
实例，只有“类型 + 用法”的声明。实例在唤起时才诞生（2-2 讲创建-续接）。

### catalog：每次组装现场渲染

每个逻辑 Turn 组装上下文时，框架对每个 `visible=True` 的条目预计算
catalog 视图（别名、描述、参数段 XML），渲染成 `<available_subagents>`
块注入 system prompt。LLM 据此决定“这个任务该派给谁、传什么参数”。
catalog 永远反映绑定表的**当前**状态——运行中改绑定，下一个回合即变。

### LLM 唤起与 visible 的可见性语义

LLM 经 `subagent-invoke` 唤起（catalog 里有哪些类型就唤起哪些）；编排
提示词决定路由策略（主线示例的“先 coder 后 reviewer”即提示词工程）。
`visible=False` 的条目不进 catalog——LLM 看不到也唤不起；但编程式
`invoke_subagent("auditor", prompt=...)` 不受限（主线示例 demo 实证）。
这条可见性 / 可执行性分离，与 0-2 的“注册 ≠ 可见”是同一族设计。

## 本篇不覆盖

- `description` 覆写与别名（catalog 呈现的变化）——4-4；
- `specified` / 注入表达式、参数覆写、catalog 模板覆写——4-4；
- 唤起管线内部（`on_subagent_invoke` / `on_subagent_returns`）——2-2 / 4-6；
- 程序化 fan-out（`create_subagent` + gather）——2-4。

## 主线示例

**演示 1：编排者驱动“写 → 审 → 汇总”**（完整代表性交互）：

````text
$ uv run flowing repl .
(agent-main)>>> 我需要一个计算斐波那契数列的 Python 函数。让 coder 写出来，再让 reviewer 审查，并汇总审查结果。
我先派 coder 编写斐波那契函数。
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "coder", "prompt": "请编写一个计算斐波那契数列的 Python 函数。接收非负整数 n；当 n 为负数时抛出 ValueError。只返回完整函数代码。"}
[tool:completed] subagent-invoke ->
```python
def fibonacci(n: int) -> int:
    # F(0) = 0 and F(1) = 1.
    if n < 0:
        raise ValueError("n must be non-negative")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
```
现在让 reviewer 检查这段代码。
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "reviewer", "prompt": "请检查下面代码的错误、边界情况、风格和性能；每行列出一个问题，如果没有问题则回答 LGTM：\n\ndef fibonacci(n: int) -> int:\n    # F(0) = 0 and F(1) = 1.\n    if n < 0:\n        raise ValueError(\"n must be non-negative\")\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a"}
[tool:completed] subagent-invoke -> LGTM
coder 写出了函数，reviewer 未发现问题。
````

读这段会话：两次 `subagent-invoke` 严格串行，编排者把 coder 的完整代码
放入 reviewer 的 prompt——多智能体协作的顺序由 LLM 在编排
提示词的约束下自行决定；你声明的只是“有哪些下属可用”。

**演示 2：visible=False 的可见性 / 可执行性分离**：

```console
$ uv run python demo_enabled.py
subagents 绑定表：
  coder: visible=True -> 进 catalog（LLM 可见）
  reviewer: visible=True -> 进 catalog（LLM 可见）
  auditor: visible=False -> 不进 catalog（LLM 不可见）
编程式唤起 visible=False 的 auditor -> result='审计通道已激活' status=completed
```

## 完整示例材料

在已安装 Flowing 的项目中按相对文件名创建以下内容，并在环境变量中设置
`DEEPSEEK_API_KEY`。模型生成的代码和审查意见可能不同；完整输入与代表性工具
交互均已内联。

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
description: 编排者：把编码任务派给 coder、审查任务派给 reviewer。
model_tag: default
tools:
  - subagent-invoke
subagents:
  - ./agents/coder
  - ./agents/reviewer
  - ./agents/auditor:
      visible: false
---
$system_prompt:
你是任务编排者。收到开发请求时，先派给 coder 写代码，再把收到的代码交给
reviewer 审查，最后汇总审查意见交付用户。不要自行编写或审查代码。
```

`agents/coder/agent.fya`：

```yaml
description: 程序员：按需求编写简洁的 Python 代码。
model_tag: default
---
$system_prompt:
你是程序员。收到需求后写出简洁、带类型标注的 Python 代码，只输出完整函数
代码，不附用法说明或闲聊。
```

`agents/reviewer/agent.fya`：

```yaml
description: 代码审查员：只读审查代码并输出问题清单。
model_tag: default
---
$system_prompt:
你是严格的代码审查员。审查收到的代码，每行输出一个“[严重级别] 描述”；
没有问题时输出“LGTM”。不要修改代码。
```

`agents/auditor/agent.fya`：

```yaml
description: 审计员：内部合规审计。
model_tag: default
---
$system_prompt:
你是审计员。收到指令后只回复“审计通道已激活”。
```

主线输入与运行命令：

```text
我需要一个计算斐波那契数列的 Python 函数。让 coder 写出来，再让 reviewer 审查，并汇总审查结果。
/exit
```

```console
$ uv run flowing repl .
```

程序化唤起隐藏审计员的 `demo_enabled.py` 完整内容：

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")
    print("subagents 绑定表：")
    for alias, entry in root._subagent_entries.items():
        visibility = "进 catalog（LLM 可见）" if entry.visible else "不进 catalog（LLM 不可见）"
        print(f"  {alias}: visible={entry.visible} -> {visibility}")
    result = await root.invoke_subagent("auditor", prompt="激活。")
    print(f"编程式唤起隐藏 auditor -> result={result.result!r} status={result.subagent_status}")
    await runtime.shutdown()


asyncio.run(main())
```

预期输出：

```text
subagents 绑定表：
  coder: visible=True -> 进 catalog（LLM 可见）
  reviewer: visible=True -> 进 catalog（LLM 可见）
  auditor: visible=False -> 不进 catalog（LLM 不可见）
编程式唤起隐藏 auditor -> result='审计通道已激活' status=completed
```

## 小结

1. `subagents:` 是类型绑定声明：类型 + 用法，实例在唤起时诞生；
2. catalog 每次组装现场渲染，是 LLM 的路由依据；
3. `subagent-invoke` 仍须 `tools:` 显式声明（注册 ≠ 可见）；
4. `visible=False`：LLM 不可见，编程式可唤起——可见性与可执行性分离。
