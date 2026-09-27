# 2-4 · 多智能体应用总装

## 前置阅读

需要理解 0-0 至 2-3 的基本概念。本篇把编排者、explore、todo 与 coder
组合为一个多智能体应用。所需配置、prompt、代码、输入和输出均在本文中
内联，不要求读者查找其他示例材料。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 编排模式 | 多智能体的协作组织方式；本篇展示 LLM 路由与程序化 fan-out。 |
| 程序化 fan-out | 代码直接创建多个子 Agent 并行执行任务，再用 gather 等待全部结果。 |
| cwd 注入链 | main() 的 cwd 参数经 runtime.provide()、各 Agent 的 inject() 与 system_prompt 中的 {{ cwd }} 传递到各层。 |
| kwargs 即身份 | 创建参数会随 Agent 元数据持久化并在恢复时传递，因此必须可 JSON 序列化。 |

## 目标

把前篇机制组合成完整工作流：编排者先派 explore 阅读当前工作目录，再用
todo 拆解任务，最后把写作工作派给 coder。cwd 通过 provide-inject 链传给
编排者与 coder。

## 正文

### 组件与执行顺序

编排者负责读取任务、选择下属并汇总结果。内置 explore-agent 只读检查
工作目录；todo 将需求变成结构化清单；coder 在该工作目录内创建或修改文件。
这些职责由 Agent 配置和 system prompt 明确约束。

### cwd 注入链

入口在挂载编排者之前执行 runtime.provide("cwd", cwd)。编排者和 coder
各自的 setup() 调用 self.inject("cwd")，并将值用于 system_prompt 模板。
派给 explore-agent 的任务文本则直接包含它要检查的工作目录。这个设计把
共享值的来源放在 Runtime，而不是重复写入每个 Agent 的创建参数。

### 两种编排模式

1. **LLM 路由。** 编排者读取 catalog，自主选择 explore、todo 和 coder；
   每一步是否继续由当前用户请求与任务结果决定。
2. **程序化 fan-out。** Python 代码直接创建两个 coder 子 Agent，并用
   asyncio.gather 并行等待。等待关系不能形成环；脚本中使用应用根锚点
   创建类型，避免依赖 catalog 路由。

## 本篇不覆盖

- 持久化与恢复的完整语义由 4-1 说明。
- finish 结构化载荷的消费方式由 4-4 说明。
- Workflow 与消息层协作等其他编排形态由 4-6、5-2 说明。

## 主线示例

### Run 1：探查与拆解

用户输入：

```text
先派 explore 查看当前工作目录的代码结构并汇报，再用 todo 把“为这个项目写一个 README.md”拆成任务清单。
```

编排者调用 explore-agent 前的推理痕迹用占位符表示；完整 prompt 随后内联：

```text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"explore-agent","name":"explore-target","prompt":"下方完整 prompt"}
```

```text
请只读检查当前工作目录及其全部子项，并汇报：
1. 完整列出文件与子目录结构。
2. 判断项目使用的语言、框架、包管理方式和入口。
3. 汇总关键代码、配置、测试与现有文档。
4. 说明项目用途、可调用 API、依赖和运行限制。
5. 对 README 作者有用的事实请明确列出。
不要创建、修改或删除任何内容；只返回文字报告。
```

一份探查结果如下。此处的项目由本文末尾内联的计算器函数和术语说明组成。

```text
项目是一个使用 Python 3 的极简计算器示例。
它提供加法与除法两个函数，仅依赖 Python 标准库。
没有命令行入口、安装配置、测试套件或许可证说明。
除法在除数为零时抛出 ValueError("除数不能为 0")。
adder 和 divider 只是对两个函数的非正式称呼，不是独立功能。
```

探查返回后，编排者调用 todo。此处省略非用户可见推理，完整工具输入为：

```text
[thinking] (reasoning trace omitted)
[tool_call] todo {"tasks_text":"见下方完整任务文本"}
[x] 探查当前工作目录中的项目结构、技术栈与关键功能
[ ] 确定 README 的标题、简介和章节顺序
[ ] 描述加法与除法的参数、返回值和除零异常
[ ] 给出可直接复现的导入、调用和输出示例
[ ] 说明 adder 与 divider 只是非正式称呼
[ ] 写明 Python 版本、依赖与项目限制
[ ] 起草 README 并对照本文给出的函数行为自检
[ ] 向用户汇报结果与未解决事项
```

工具结果：

```text
8 项任务，1 项已完成，7 项待处理。
```

编排者随后向用户展示清单，并在获得下一条请求前暂停派发写入任务。

### Run 2：派发 coder

用户输入：

```text
按刚才的结构和任务清单写出 README.md；完成后请逐条自检并汇报。
```

编排者在收到用户继续指示后调用 coder；工具使用的完整任务 prompt 为：

```text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"coder","name":"readme-writer","prompt":"下方完整任务 prompt"}
```

```text
请为当前工作目录中的极简 Python 计算器编写 README.md。只修改 README.md。

项目事实：
- 可调用函数为 add(a: float, b: float) -> float 和 div(a: float, b: float) -> float。
- add 返回两个参数之和。
- div 返回 a / b；当 b == 0 时抛出 ValueError("除数不能为 0")。
- 项目只使用 Python 标准库，没有第三方依赖、CLI、安装器或测试套件。
- adder 和 divider 只是 add 与 div 的非正式称呼。

README 必须包含项目简介、完整函数代码、API 行为、可执行输入与预期输出、
术语说明及限制。示例应包括正常除法和除数为零的异常处理。

完成后读取 README.md 并自检：函数名、签名、返回值和异常消息是否与上面的
项目事实一致；所有输入和输出是否在 README 中；代码围栏是否闭合；是否
编造依赖或不存在的功能。只报告检查结果，不要运行程序或测试。
```

一次记录中的可见工具交互与交付内容如下。模型生成的措辞可不同，但代码事实
和预期输出应与内联示例一致。

```text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"coder","name":"readme-writer","prompt":"请为当前工作目录中的极简 Python 计算器编写 README.md。只修改 README.md。项目事实、章节要求与自检步骤见上方完整 prompt。"}
[tool:completed] subagent-invoke ->
README.md 已写入并自检。包含项目简介、完整模块、API、正常调用、除零处理、
术语说明和环境限制；未添加依赖或额外功能。
```

README 中内联的完整模块：

```python
"""示例项目：计算器。"""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
```

README 中的完整使用输入：

```python
from pkg.calc import add, div

print(add(1, 2))
print(add(1.5, 2))
print(div(1, 2))

try:
    print(div(1, 0))
except ValueError as exc:
    print(f"ValueError: {exc}")
```

对应输出：

```text
3
3.5
0.5
ValueError: 除数不能为 0
```

coder 交付的完整 README 内容如下；其中的代码、调用输入与预期输出均在本页
内联，可直接据此理解交付物，不必打开其他 Markdown 页面。

~~~~markdown
# 示例项目：计算器

这是一个极简 Python 计算器，只公开两个函数：`add` 做加法，`div` 做除法。
本页包含完整模块、输入、输出与错误行为。

## 完整模块

将以下内容保存为相对文件名 `pkg/calc.py`：

```python
"""示例项目：计算器。"""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
```

## API 与复现

| 函数 | 参数 | 返回值 | 异常 |
| --- | --- | --- | --- |
| `add(a, b)` | `a: float`、`b: float` | `a + b`，类型为 `float` | 无 |
| `div(a, b)` | `a: float`、`b: float` | `a / b`，类型为 `float` | `b == 0` 时抛出 `ValueError("除数不能为 0")` |

完整签名为 `add(a: float, b: float) -> float` 与 `div(a: float, b: float) -> float`。

```python
from pkg.calc import add, div

print(add(1, 2))
print(add(1.5, 2))
print(div(1, 2))
```

输出：

```text
3
3.5
0.5
```

除数为零时的输入：

```python
from pkg.calc import div

try:
    print(div(1, 0))
except ValueError as exc:
    print(f"ValueError: {exc}")
```

输出：

```text
ValueError: 除数不能为 0
```

## 术语与限制

`adder` 是 `add` 的非正式称呼，`divider` 是 `div` 的非正式称呼；它们
不是函数、类或配置项。示例使用 Python 3 和标准库，不提供 CLI、安装器
或测试套件。运行导入示例时，当前工作目录应是包含 `pkg` 的项目根目录；
本文不要求切换目录。
~~~~

### 演示 3：程序化 fan-out

以下完整离线程序并行创建两个 coder，并等待各自写出一条术语说明。入口、
配置、Agent prompt 和目标数据也都在本文末尾内联。

```python
# demo_fanout.py
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".", cwd=".")
    root = await runtime.get_agent("agent-main")

    async def job(name: str, term: str) -> str:
        child = await root.create_subagent("@/agents/coder", name=name)
        try:
            result = await child.query(
                f"在当前工作目录中创建 notes/{term}.md。正文用一句话说明 "
                f"{term} 在当前项目里的含义；完成后调用 finish。"
            )
            return f"{name}: status={result.status}"
        finally:
            await child.destroy()

    results = await asyncio.gather(
        job("coder-a", "adder"),
        job("coder-b", "divider"),
    )
    for line in results:
        print(line)
    await runtime.shutdown()


asyncio.run(main())
```

一次记录的输出：

```text
coder-a: status=completed
coder-b: status=completed
```

## 完整复现材料

以下代码块给出多智能体应用与目标项目的全部必要内容。注释中的相对文件名
对应紧随其后的完整内容；命令不要求切换目录。交互式模型调用需要读者自行
提供 DEEPSEEK_API_KEY，本文只保留占位符。

```python
# main.py
from flowing import Runtime


async def main(cwd: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if cwd:
        runtime.provide("cwd", cwd)
    await runtime.mount("@/root.fya", agent_id="agent-main")
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
# root.fya
description: "编程任务编排者：探查、拆解任务并派发实现。"
model_tag: default
tools:
  - subagent-invoke
  - ./tools/todo.py
subagents:
  - explore-agent
  - ./agents/coder
---
$system_prompt:
你是编程任务编排者。当前工作目录为 {{ cwd }}，所有子任务都围绕它进行。
先派 explore-agent 只读探查并汇报；再用 todo 将实现需求拆成清单并向用户展示；
收到继续指示后，把文件写入任务派给 coder；最后向用户总结结果。
不得自行读写或执行文件。
---
$script:
async def setup(self):
    self.cwd = self.inject("cwd")
```

```yaml
# agents/coder/agent.fya
description: "在当前工作目录内读写文件并交付任务的编程智能体。"
model_tag: default
tools:
  - read
  - write
  - edit
  - grep
  - glob
  - bash
  - finish
---
$system_prompt:
你是编程智能体。当前工作目录为 {{ cwd }}。只在此目录内处理任务。
文件操作使用明确的相对文件名；不要猜测其他目录。完成后调用 finish，
summary 说明完成内容，files 列出改动文件。不得修改工作目录之外的内容。
---
$script:
async def setup(self):
    self.cwd = self.inject("cwd")
```

```python
# tools/todo.py
from pydantic import BaseModel, Field

from flowing import ScriptTool


class TodoArgs(BaseModel):
    tasks_text: str = Field(
        description="任务清单文本：每行一个任务；以 [x] 开头表示已完成"
    )


class TodoTool(ScriptTool):
    """把任务文本解析成清单，并返回未结任务计数。"""

    name = "todo"
    args_model = TodoArgs

    async def execute(self, *, tasks_text: str) -> dict:
        items = []
        for raw in tasks_text.splitlines():
            line = raw.strip()
            if not line:
                continue
            done = line[:3].lower() == "[x]"
            title = (line[3:] if done else line).strip().lstrip("-•").strip()
            if not title:
                raise ValueError(f"任务行为空：{raw!r}")
            items.append({"title": title, "done": done})
        if not items:
            raise ValueError("任务清单为空：至少提供一条任务")
        return {
            "total": len(items),
            "open": sum(1 for item in items if not item["done"]),
            "items": items,
        }
```

```python
# pkg/calc.py
"""示例项目：计算器。"""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
```

```text
# notes/adder.md
# adder 在本项目中的作用

本项目里并不存在名为 adder 的独立功能。它只是加法函数 add 的非正式称呼；
add 接收两个数并返回它们的和，例如 add(1, 2) 返回 3。
```

```text
# notes/divider.md
# divider 在本项目中的作用

divider 不是独立功能，而是除法函数 div 的非正式称呼。div 返回 a / b；
当 b 为零时，它抛出 ValueError("除数不能为 0")。
```

启动 LLM 路由示例时，当前目录应包含上述内联内容；命令仅使用项目入口和
相对工作目录值，不包含切换目录命令：

```console
$ export DEEPSEEK_API_KEY="<your-api-key>"
$ uv run flowing repl . --cwd .
```

## 小结

1. 总装应用由入口策略、编排者配置、子 Agent 配置和任务工具组成。
2. cwd 经 Runtime provide、Agent inject 与 prompt 模板传递到需要它的层级。
3. LLM 路由按任务结果逐步派单；程序化 fan-out 由代码并行创建子 Agent。
4. prompt 与目标数据均在本文内联，读者可据相对文件名重建示例，不需要查找其他材料。
