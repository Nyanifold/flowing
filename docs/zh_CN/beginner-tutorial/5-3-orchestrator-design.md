# 5-3 · 编排者设计：目录路由与委派协议

> 自包含示例：本篇下方列出完整的运行配置、编排者与工作者定义、输入、程序化演示代码及代表性输出。创建这些相对文件后，在当前目录运行 `uv run flowing repl .`；可见性演示运行 `uv run python demo_enabled.py`。
> 对话示例需要 `DEEPSEEK_API_KEY` 环境变量；不要将真实凭证写入配置。

> 前置：第 5-2 篇“组织模式全景”

## 这篇讲什么

编排者-工作者模式的核心设计：工作者目录（catalog）如何支持
路由决策、委派协议的结构化，以及编排者自身的纪律。

## 背景知识

编排者的调度决策由模型做出（“这个任务该给谁”是语言判断），
模型需要的输入是一份结构化的工作者清单。清单的质量因此决定
路由的质量——这与传统调度系统一致：调度器的输入面决定调度
准确率。

## 核心概念

### 目录（catalog）：路由的输入面

目录描述每个可派单对象的三要素：

```
- 别名（调用时引用）
- 职责描述（一段面向模型的自然语言：做什么、什么时候用）
- 可见参数（名称、类型、说明；敏感或固定参数不展示）
```

描述字段的写法直接决定路由准确率。“万能助手”式的描述等于
没有描述；有效的描述说清适用任务类型与不适用的边界。

```yaml
# 目录条目的概念形态
- 别名: greeter
  描述: 问候类请求；只输出问候语，不处理信息查询
  参数:
    style: { 类型: string, 说明: 语气风格 }
```

目录是**现场渲染**的：每次组装上下文时按当前绑定状态重新生成。
运行期启用 / 停用工作者，下一轮即反映；目录永远与绑定状态一致。
本篇示例中的编排者声明了三个工作者，其中一个被显式停用。运行 `uv run python demo_enabled.py`：

```console
subagents 绑定表：
  coder: visible=True -> 进 catalog（LLM 可见）
  reviewer: visible=True -> 进 catalog（LLM 可见）
  auditor: visible=False -> 不进 catalog（LLM 不可见）
编程式唤起 visible=False 的 auditor -> result='审计通道已激活' status=completed
```

逐行看：绑定表三条记录里，前两条进入目录（模型能看到、能派单），
`auditor` 被停用所以不进目录——模型根本不知道它存在；但最后
一行证明它**仍可被代码直接唤起**（返回了正常结果）。可见性与
可执行性是两个独立的开关：目录控制“模型能不能看到”，不控制
“能力是否存在”。

### 委派协议：任务包的结构

一次委派的完整信息结构：

```python
dispatch = {
    "to": "greeter",              # 目录别名
    "task": "用正式语气问候用户",    # 自然语言任务描述
    "args": {"style": "formal"},   # 结构化参数（按目录可见参数）
    "context": "用户刚完成注册",     # 必要的背景（可选）
}
```

委派的质量取决于任务描述是否自足：工作者看不到编排者的完整
历史，只拿到委派包里显式携带的信息。背景不足是委派失败的
首因——“把刚才那个改一下”在工作者处没有任何意义。

在创建本篇后文列出的文件并运行 `uv run flowing repl .` 后，输入下面的复合任务：

**输入**

```text
我需要一个计算第 n 个斐波那契数的 Python 函数。定义 f(0)=0、f(1)=1；让 coder 写出来，然后让 reviewer 审查，并把审查意见汇总给我。
/exit
```

**代表性输出**

```text
(agent-main)>>> 我需要一个计算第 n 个斐波那契数的 Python 函数。定义 f(0)=0、f(1)=1；让 coder 写出来，然后让 reviewer 审查，并把审查意见汇总给我。
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"coder","name":"fib-coder","prompt":"请用 Python 写一个斐波那契数列函数。要求：1. 函数名为 fibonacci，接受整数 n，返回第 n 个斐波那契数，定义 f(0)=0、f(1)=1；2. n 为负数时抛出 ValueError；3. 代码简洁并附一段用法说明。请直接给出完整、可运行的 Python 代码。"}
[tool:completed] subagent-invoke ->
def fibonacci(n: int) -> int:
    """返回第 n 个斐波那契数，f(0)=0, f(1)=1。"""
    if n < 0:
        raise ValueError("n 不能为负数")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a

[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"reviewer","name":"fib-reviewer","prompt":"请只读审查以下代码并按严重程度列出问题，不要修改代码：\ndef fibonacci(n: int) -> int:\n    \"\"\"返回第 n 个斐波那契数，f(0)=0, f(1)=1。\"\"\"\n    if n < 0:\n        raise ValueError(\"n 不能为负数\")\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n\n请检查正确性、边界、类型校验与性能。"}
[tool:completed] subagent-invoke ->
低优先级：未对运行时类型作校验；bool 是 int 的子类，True 会被当作 1；迭代算法对非常大的 n 可能很慢；未设置输入上限。信息性：文档字符串没有写明负数会抛出 ValueError。Python int 为任意精度，不存在整数溢出问题。
汇总：核心迭代逻辑正确，n=0 与 n=1 的结果正确，负数会抛出 ValueError；没有严重级别问题。
[thinking] (reasoning trace omitted)
(agent-main)>>>
```

模型回复可能改变措辞；需核对的是第二次委派携带第一次交付的完整代码，reviewer 以只读方式审查。

逐行看委派链：两次 `subagent-invoke` 严格串行——第二次派单的
任务包必须携带第一次的产出（代码），因为 reviewer 看不到 coder
的历史；这在留档里直接可见：reviewer 的 `prompt` 原文携带了
coder 交付的代码全文。编排者在两轮之间做的正是“把上一个工作者
的产出装进下一个委派包”。

### 完整示例材料

以下是会话演示与可见性演示所需的全部文件。按相对文件名创建；对话提示、工作者完整 system prompt、配置和程序化输出均在本篇中。

```console
export DEEPSEEK_API_KEY=sk-your-key-here
```

**`providers.yaml`**

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

**`models.yaml`**

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

**`model-tags.yaml`**

```yaml
tags:
  default: deepseek-flash
```

**`main.py`**

```python
from flowing import Runtime


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

**`root.fya`**

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
你是任务编排者。开发请求先派给 coder 编写代码，再把完整代码交给 reviewer 审查，
最后汇总审查结果。你自己不要写代码或审查。auditor 不向模型展示，但仍可由代码唤起。
```

**`agents/coder/agent.fya`**

```yaml
description: 程序员：按需求写出简洁的 Python 代码。
model_tag: default
---
$system_prompt:
你是程序员。收到需求后写出简洁、带类型标注的 Python 代码，只输出代码本身与一行用法说明，不要闲聊。
```

**`agents/reviewer/agent.fya`**

```yaml
description: 代码审查员：只读审查代码并输出问题清单。
model_tag: default
---
$system_prompt:
你是严格的代码审查员。审查收到的代码，按严重程度逐行输出“[严重级别] 描述”；
没有问题时输出 LGTM。不要修改代码。
```

**`agents/auditor/agent.fya`**

```yaml
description: 审计员：内部合规审计。
model_tag: default
---
$system_prompt:
你是审计员。收到指令后只回复“审计通道已激活”。
```

**`demo_enabled.py`**

```python
import asyncio
from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")
    print("subagents 绑定表：")
    for alias, entry in root._subagent_entries.items():
        state = "进 catalog（LLM 可见）" if entry.visible else "不进 catalog（LLM 不可见）"
        print(f"  {alias}: visible={entry.visible} -> {state}")
    result = await root.invoke_subagent("auditor", prompt="激活。")
    print(
        "编程式唤起 visible=False 的 auditor -> "
        f"result={result.result!r} status={result.subagent_status}"
    )
    await runtime.shutdown()


asyncio.run(main())
```

运行可见性演示的完整预期输出：

```text
subagents 绑定表：
  coder: visible=True -> 进 catalog（LLM 可见）
  reviewer: visible=True -> 进 catalog（LLM 可见）
  auditor: visible=False -> 不进 catalog（LLM 不可见）
编程式唤起 visible=False 的 auditor -> result='审计通道已激活' status=completed
```

### 编排者的三条纪律

1. **自身工具表最小化**：编排者不持有执行类工具，否则模型倾向
   于自己完成任务而非委派——路由结构形同虚设。本篇示例的编排者
   只有唤起工作者这一个工具；
2. **结果只汇总不加工**：工作者的产物原样转达；编排者的二次
   创作会让错误归因变得不可能（用户看到的是谁的话？）；
3. **按需派单不闲聊**：每轮只向完成任务必需的工作者派单；目录
   里的每条描述都占用编排者的上下文。

## 常见误区

1. **目录描述写泛**。路由准确率直接受描述质量影响；写清任务
   类型边界；
2. **委派包不带上下文**。工作者没有共享记忆（除非按名续接，
   第 5-4 篇讨论）；自足的任务描述是委派的基本要求；
3. **编排者代劳**。编排者持执行工具时，委派结构会退化成摆设。

## 练习

1. 为“文档处理团队”写三个目录条目（格式审查 / 内容摘要 /
   翻译），各含一句“什么时候派给我”；
2. 评审这条委派包并补全缺失信息：`{"to": "translator", "task": "翻译这个"}`；
3. 在本篇示例的 `root.fya` 里把 `auditor` 的 `visible: false`
   删掉，重新运行对话并直接要求“让 auditor 审计一遍”，观察
   路由结果与之前有何不同。

## 小结

1. 目录是路由的输入面：别名、职责描述、可见参数；描述质量决定
   路由准确率；
2. 委派包必须自足：工作者只拿到显式携带的信息；
3. 编排者纪律：工具表最小、结果只汇总、按需派单；
4. 目录现场渲染，与绑定状态始终一致。
