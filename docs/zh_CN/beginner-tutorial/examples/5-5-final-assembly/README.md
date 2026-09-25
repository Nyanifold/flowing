# 示例：5-5 总装

本例组合运行时装配、工作目录注入、任务规划、子 Agent 委派与文件交付。
编排者检查计算器项目、生成任务清单，再把 README 编写任务交给 coder。

## 运行

将本文内联材料按相对文件名保存。通过环境变量设置 DEEPSEEK_API_KEY；
Provider 声明只包含占位符。从项目根目录运行：

~~~console
$ uv run flowing repl . --cwd target
~~~

程序化并行示例使用单独命令：

~~~console
$ uv run python demo_fanout.py
~~~

## 运行时入口与模型配置

保存为 main.py：

~~~python
from flowing import Runtime


async def main(cwd: str | None = None, resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if cwd:
        runtime.provide("cwd", cwd)
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
~~~

~~~yaml
# providers.yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"

# models.yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash

# model-tags.yaml
tags:
  default: deepseek-flash
~~~

## 编排者与 coder 声明

将声明分别保存为 root.fya 和 agents/coder/agent.fya：

~~~yaml
# root.fya
description: 编程任务编排者：检查、拆解并委派实现任务。
model_tag: default
tools:
  - subagent-invoke
  - ./tools/todo.py
subagents:
  - explore-agent
  - ./agents/coder
---
$system_prompt:
你是编程任务编排者。工作目录：{{ cwd }}。
1. 先派 explore-agent 检查目录并汇报。
2. 用 todo 工具把实现需求拆成任务清单并展示给用户。
3. 把文件编写任务派给 coder，完成后汇总结果。
不要自己读写文件。
---
$script:
async def setup(self):
    self.cwd = self.inject("cwd")

# agents/coder/agent.fya
description: 编程智能体：在工作目录内读写文件、执行命令并交付。
model_tag: default
tools: [read, write, edit, grep, glob, bash, finish]
---
$system_prompt:
你是编程智能体。工作目录：{{ cwd }}。
文件操作使用绝对路径，不要猜测，也不要改动工作目录之外的内容。
完成后调用 finish，提供工作摘要和改动文件列表。
---
$script:
async def setup(self):
    self.cwd = self.inject("cwd")
~~~

保存为 tools/todo.py：

~~~python
from pydantic import BaseModel, Field
from flowing import ScriptTool


class TodoArgs(BaseModel):
    tasks_text: str = Field(
        description="任务清单文本，每行一项；[x] 表示已完成")


class TodoTool(ScriptTool):
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
            raise ValueError("任务清单为空")
        return {
            "total": len(items),
            "open": sum(not item["done"] for item in items),
            "items": items,
        }
~~~

## target 计算器

保存为 target/pkg/calc.py：

~~~python
"""示例项目：计算器。"""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
~~~

add 返回两数之和，div 返回两数之商。除数为 0 时，div 会抛出代码中所示
消息的 ValueError。

## 输入与可见结果

输入：

~~~text
先让 explore 检查 target 项目，再用 todo 规划 README 编写任务。让 coder 写出 README 并自检，然后汇报结果。
/exit
~~~

可见交互摘录：

~~~text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke：检查 target 项目结构。
[tool result] target 包含计算器模块和说明其工作目录用途的 README。
[tool_call] todo：规划一份介绍结构、计算器 API 与用法的 README。
[tool result] 返回任务清单。
[tool_call] subagent-invoke：编写 README、重新读取并汇报结果。
[tool result] README 已完成，API 示例介绍 add 和 div。
~~~

交付的 README 核心内容如下：

~~~~markdown
# 计算器工作目录

这是一个提供 add(a, b) 和 div(a, b) 的小型 Python 项目。模块不依赖
第三方库。运行 Python 时需将项目根目录放在导入路径中。

~~~python
from pkg.calc import add, div

print(add(2, 3))  # 5
print(div(6, 4))  # 1.5
~~~

div(1, 0) 会抛出 ValueError("除数不能为 0")。
~~~~

## 程序化并行 fan-out

保存为 demo_fanout.py：

~~~python
import asyncio
from flowing import launch


async def main() -> None:
    runtime = await launch(".", cwd="target")
    root = await runtime.get_agent("agent-main")

    async def job(name: str, topic: str) -> str:
        child = await root.create_subagent("@/agents/coder", name=name)
        try:
            result = await child.query(
                f"在 notes/{topic}.md 中用一句话定义 {topic}；"
                "先创建 notes 目录，完成后用 finish 交卷。")
            return f"{name}: status={result.status}"
        finally:
            await child.destroy()

    results = await asyncio.gather(
        job("coder-a", "adder"), job("coder-b", "divider"))
    for result in results:
        print(result)
    await runtime.shutdown()


asyncio.run(main())
~~~

两个子 Agent 完成笔记写入任务后都会报告 completed。示例笔记分别表达：
“adder 使用加法操作将两个值相加”和“divider 使用除法操作计算两个值的商”。
