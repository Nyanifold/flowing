# 2-3 · 编写 ScriptTool

## 前置阅读

[0-2 使用内置工具](0-2-use-builtin-tools.md)（工具声明与使用）、[1-3 参数与
setup()](1-3-agent-args-and-setup.md)（args 声明即模型）。本篇完整内联
所需配置、prompt、工具实现、用户输入与输出；其中的 todo 工具也用于 2-4。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| `ScriptTool` | 用 Python 写工具的主通道：`execute(**kwargs)` 接收参数并返回普通值 |
| 声明即模型 | `args_model`（Pydantic 模型）同时是 LLM 可见 schema 与执行校验的来源——声明一次，两处生效 |
| error 结果 | `execute` 抛普通异常 → `ToolResult(status="error")`：LLM 可见的正常产物，不触发错误钩子（区别于编程错误直接上抛） |
| `caller` 注入 | `execute` 声明 `caller: Agent` 形参后框架自动注入调用方 Agent（get_resource / inject / 状态袋等调用方 API 经它可用） |
| 三条等价通道 | 手写 `ScriptTool` 子类 / `@flowing_tool` 打标函数 / `.fya` `callable:` 指针——两两互斥，产出同构 |

## 目标

写出生产级 ScriptTool：声明面、执行面、错误通道、等价定义通道——这个
工具正是 2-4 编程智能体的任务拆解件。

## 正文

### 类属性声明

完整的 TodoArgs 与 TodoTool 类定义见本节末尾的可复现代码。`name` 必填，
不从类名推断；`args_model` 同时定义 LLM schema 与执行校验。显式
`definition` 与类属性声明互斥；两者同时出现时告警并以显式 definition 为准。

- `name` 必填；文件通道下与文件身份不一致 → `NameMismatchError`；
- `args_model` 可省略——框架从 `execute()` 签名推导（无标注 →
  `MissingSchemaError`）；
- 类属性声明与显式 `definition` 互斥（同时存在告警，显式胜）。

### execute：零散参数进、普通值出

作者不需要知道 `ToolResult` 的存在：返回值自动包装为 `completed`；
**抛普通异常 → `error` 结果**（LLM 可见、可自我纠正，不触发错误钩子）；
只有 `raise Intercepted` 才走 blocked 通道。本篇的校验失败（空清单）
刻意走 error 通道——主线示例可见 LLM 如何据实转告。

`caller` 形参声明后框架自动注入调用方 Agent（状态袋用法 4-2 展示，
本篇保持无状态纯函数式）。

### 三条等价通道

手写 `ScriptTool` 子类、使用 `@flowing_tool` 标记函数、以及在声明中提供
`callable:` 指针是三种等价定义方式。完整实现和对应声明均在文末内联。
标记只标记、不注册（import 期无 Runtime 依赖）；指针可以指向子类或裸
函数；标记与指针互斥。三种方式产出同构的 `ToolDefinition`。每个文件至多
有一个被标记的函数；指针声明的身份名必须与所指工具的 name 一致。

### 引用方式与并行执行

文件实现的工具使用相对模块名声明，目录形态使用 TOOL.fya 声明。1-1 的
`[tool_call]` 行已见过工具在
回合内**并行执行**——`Agent._run_turn` 对每个 tool_call block 各起一个
任务（机制全景 4-5）。

## 本篇不覆盖

- 后台三形态（async generator / background=True / 返回 Task）——4-5；
- 媒体返回与结果归一五形态——4-5；
- `caller` 的状态袋用法——4-2；`description` 三级回退细节——4-5。

## 主线示例

**演示 1：Agent 使用 todo 工具（含 error 通道）**：

```console
$ DEEPSEEK_API_KEY="<your API key>" uv run flowing repl .
(agent-main)>>> 帮我把这些整理成任务清单：写方案、买咖啡（已完成）、、发邮件。
[thinking] (reasoning trace omitted)
[tool_call] todo {"tasks_text": "写方案\n[x] 买咖啡\n\n发邮件"}
[tool:completed] todo ->
已整理好：共 3 项，未结 2 项（写方案、发邮件），买咖啡已完成；其中的空项已自动去掉。
(agent-main)>>> 再用 todo 解析一个空文本：""（演示出错情形）。
[thinking] (reasoning trace omitted)
[tool_call] todo {"tasks_text": ""}
[tool:error] todo -> 任务清单为空：至少提供一条任务
工具返回错误：任务清单为空，至少需要提供一条任务，所以这次没能解析出任何任务。

请补上至少一条任务文本，我再帮你整理。
```

读这段会话：第一轮里模型先把自然语言的“（已完成）”翻译成工具约定的
`[x]` 前缀再调用（`[tool_call]` 行的参数可见）——schema 的
description 就是契约，LLM 会据此对齐；空行被工具自动去除。
第二轮空文本走 **[tool:error]**：原因对 LLM 可见、
被如实转告，且模型正确理解“这是校验提示而非程序故障”——error 通道的
设计意图完全实现。

**演示 2：三条等价定义通道**（离线运行）：

```console
$ uv run python demo_channels.py
通道A 手写子类     : TodoTool name='todo'
通道C callable 指针: TodoToolFn name='todo-fn'
通道B 打标函数     : Shout name='shout'
A 与 C 独立实现、同构声明: True
  同参 schema: True
  A: params=['tasks_text']
  B: params=['text']
  C: params=['tasks_text']
```

### 完整复现材料

以下相对文件名只是创建位置标签；每项内容都在本文中完整给出。

```python
# main.py
from flowing import Runtime

async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime()
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

```yaml
# root.fya front matter and complete system prompt
description: "ScriptTool 演示助手：用 todo 工具拆解任务清单。"
model_tag: default
tools:
  - ./tools/todo.py
---
$system_prompt:
你是任务整理助手。用户给出零散任务文本时，用 todo 工具解析成结构化
清单，并用一句话汇报“共 N 项、未结 M 项”；工具返回 error 时，把错误
原因如实转告用户。回答控制在两句话以内。
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
    """把零散任务解析为结构化清单，并返回未结任务计数。"""

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
                raise ValueError(f"任务行为空（去掉标记与符号后无内容）：{raw!r}")
            items.append({"title": title, "done": done})
        if not items:
            raise ValueError("任务清单为空：至少提供一条任务")
        open_count = sum(1 for item in items if not item["done"])
        return {"total": len(items), "open": open_count, "items": items}
```

```python
# tools/shout.py
from flowing import flowing_tool

@flowing_tool
async def shout(text: str) -> str:
    """把文本转换为大写。"""
    return text.upper()
```

```python
# tools/todo_impl.py
from pydantic import BaseModel, Field

from flowing import ScriptTool

class TodoFnArgs(BaseModel):
    tasks_text: str = Field(
        description="任务清单文本：每行一个任务；以 [x] 开头表示已完成"
    )

class TodoToolFn(ScriptTool):
    """callable 指针演示用的独立任务清单工具。"""

    name = "todo-fn"
    args_model = TodoFnArgs

    async def execute(self, *, tasks_text: str) -> dict:
        items = []
        for raw in tasks_text.splitlines():
            line = raw.strip()
            if not line:
                continue
            done = line[:3].lower() == "[x]"
            title = (line[3:] if done else line).strip().lstrip("-•").strip()
            items.append({"title": title, "done": done})
        return {
            "total": len(items),
            "open": sum(1 for item in items if not item["done"]),
            "items": items,
        }
```

```yaml
# tools/todo-fn/TOOL.fya
type: script
callable: ../todo_impl.py::TodoToolFn
```

```python
# demo_channels.py
import asyncio
import pathlib

from flowing.tool.registry import ToolRegistry

async def main() -> None:
    registry = ToolRegistry(project_root=pathlib.Path.cwd())
    root = pathlib.Path.cwd()
    handwritten = registry.get("./tools/todo.py", source_dir=root)
    pointer = registry.get("./tools/todo-fn", source_dir=root)
    marked = registry.get("./tools/shout.py", source_dir=root)
    print(f"通道A 手写子类     : {type(handwritten).__name__} name={handwritten.definition.name!r}")
    print(f"通道C callable 指针: {type(pointer).__name__} name={pointer.definition.name!r}")
    print(f"通道B 打标函数     : {type(marked).__name__} name={marked.definition.name!r}")
    print(f"A 与 C 独立实现、同构声明: {type(handwritten) is not type(pointer)}")
    print("  同参 schema: "
          f"{sorted(handwritten.definition.params_schema) == sorted(pointer.definition.params_schema)}")
    for label, tool in (("A", handwritten), ("B", marked), ("C", pointer)):
        print(f"  {label}: params={sorted(tool.definition.params_schema)}")

asyncio.run(main())
```

演示 1 的完整用户输入为：

```text
帮我把这些整理成任务清单：写方案、买咖啡（已完成）、、发邮件。
再用 todo 解析一个空文本：""（演示出错情形）。
```

在已安装 Flowing 的环境中输入以上两行，或用程序化离线示例核对三种工具
定义通道。执行命令时当前工作目录应为本文所定义项目的根目录；不需要
切换目录。

```console
$ DEEPSEEK_API_KEY="<your API key>" uv run flowing repl .
$ uv run python demo_channels.py
```

## 小结

1. `name` 必填、`args_model` 声明即模型、description 有三级回退；
2. `execute` 零散参数进普通值出；普通异常 → LLM 可见 error 结果；
3. 三条等价通道（子类 / 打标 / 指针）两两互斥、产出同构；
4. 文件实现以相对模块名声明；工具在回合内并行执行。
