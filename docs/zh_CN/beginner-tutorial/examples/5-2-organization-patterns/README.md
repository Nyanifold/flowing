# 示例：5-2 组织模式全景

本例结合模型驱动的任务委派与代码驱动的 fan-out：编排者检查极简计算器，
把需求拆成任务清单，再委派文档编写；另一个脚本则并发创建两个子 Agent。

## 运行

将本文内联材料按相对文件名保存。通过环境变量设置 DEEPSEEK_API_KEY；
Provider 声明只包含占位符。从项目根目录运行编排者：

~~~console
$ uv run flowing repl . --cwd target
~~~

程序化 fan-out 演示命令：

~~~console
$ uv run python demo_fanout.py
~~~

## 运行时入口与模型配置

保存为 main.py：

~~~python
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

## 编排者、coder 与任务清单工具

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
1. 先派 explore-agent 检查目录结构并汇报。
2. 用 todo 工具把实现需求拆成任务清单并展示给用户。
3. 把文件编写任务派给 coder，完成后汇总结果。
不要自己读写文件。
---
$script:
async def setup(self):
    self.cwd = self.inject("cwd")

# agents/coder/agent.fya
description: 编程智能体：读写文件、执行命令并交付。
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

## target 项目数据

计算器模块的完整代码如下：

~~~python
"""示例项目：计算器。"""


def add(a: float, b: float) -> float:
    return a + b


def div(a: float, b: float) -> float:
    if b == 0:
        raise ValueError("除数不能为 0")
    return a / b
~~~

初始 target README 的完整说明见
[target 项目介绍](target/README.md)，其中也包含可复制的调用示例及输出。

## 对话输入与可见结果

输入：

~~~text
先让 explore 检查 target 项目结构，再用 todo 拆解 README 编写任务，随后派 coder 写 README 并汇报结果。
/exit
~~~

可见交互摘录：

~~~text
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke：检查 target 项目。
[tool result] 目标是一个包含 add 和 div 操作的极简 Python 计算器。
[tool_call] todo：列出 README 编写计划。
[tool result] 计划包含项目结构、API 行为、用法和限制。
[tool_call] subagent-invoke：编写 README 并汇总。
[tool result] README 介绍计算器、API 和可运行示例。
~~~

完成后的说明记录 add(2, 3) 得到 5、div(6, 4) 得到 1.5。

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

预期控制台输出：

~~~text
coder-a: status=completed
coder-b: status=completed
~~~

示例笔记内容：

~~~markdown
# adder

adder 使用加法操作将两个值相加。

# divider

divider 使用除法操作计算两个值的商。
~~~
