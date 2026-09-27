# 示例：5-3 编排者设计

本例将任务协调、代码编写和审查分开：编排者把实现任务派给 coder，再将
结果交给只读 reviewer。第三个子 Agent 对模型目录隐藏，但仍可通过 Python
接口调用。

## 运行

将本文内联内容按所示相对文件名保存。通过环境变量设置
`DEEPSEEK_API_KEY`；Provider 配置只含占位符。从项目根目录启动对话：

~~~console
$ uv run flowing repl .
~~~

可见性演示使用单独命令：

~~~console
$ uv run python demo_enabled.py
~~~

## 运行时入口与模型配置

保存为 `main.py`：

~~~python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
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

## 编排者与下属 Agent 提示词

将声明分别保存为 `root.fya`、`agents/coder/agent.fya`、
`agents/reviewer/agent.fya` 和 `agents/auditor/agent.fya`：

~~~yaml
# root.fya
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
你是任务编排者。
收到开发类请求时：先调 subagent-invoke 派给 coder 写代码，拿到代码后再
派给 reviewer 审查，最后把审查意见汇总交付给用户。
不要自己写代码或审查。

# agents/coder/agent.fya
description: 程序员：按需求写出简洁的 Python 代码。
model_tag: default
---
$system_prompt:
你是程序员。收到需求后写出简洁、带类型标注的 Python 代码，只输出代码
本身与一行用法说明，不要闲聊。

# agents/reviewer/agent.fya
description: 代码审查员：只读审查代码，输出问题清单。
model_tag: default
---
$system_prompt:
你是严格的代码审查员。审查收到的代码，输出问题清单：每行一个
“[严重级别] 描述”；没有问题时输出“LGTM”。不要改代码。

# agents/auditor/agent.fya
description: 审计员：内部合规审计（默认对 LLM 不可见）。
model_tag: default
---
$system_prompt:
你是审计员。收到指令后只回复“审计通道已激活”。
~~~

## 对话输入与可见结果

输入：

~~~text
我需要一个返回第 n 个斐波那契数的 Python 函数。让 coder 编写，要求 n 为负数时抛出 ValueError；再让 reviewer 审查并汇总意见。
/exit
~~~

coder 给出的实现：

~~~python
def fibonacci(n: int) -> int:
    """返回第 n 个斐波那契数，F(0)=0，F(1)=1。"""
    if n < 0:
        raise ValueError("n 不能为负数")
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


if __name__ == "__main__":
    print(fibonacci(10))  # 55
~~~

可见交互摘录：

~~~text
[thinking] (reasoning trace omitted)
[tool_call] coder：编写返回第 n 个斐波那契数的函数，并拒绝负数 n。
[tool result] fibonacci(10) 返回 55。
[tool_call] reviewer：审查实现的正确性与边界情况。
[tool result] 对非负整数，迭代逻辑正确；负数会触发 ValueError。
~~~

最终答复说明该迭代实现符合所述输入范围。运行时类型校验和大输入上限
仍可作为改进方向；Python 整数不会发生溢出。

## 可见性与可执行性演示

保存为 `demo_enabled.py`：

~~~python
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
    print(f"编程式唤起隐藏 auditor -> "
          f"result={result.result!r} status={result.subagent_status}")
    await runtime.shutdown()


asyncio.run(main())
~~~

预期输出：

~~~text
subagents 绑定表：
  coder: visible=True -> 进 catalog（LLM 可见）
  reviewer: visible=True -> 进 catalog（LLM 可见）
  auditor: visible=False -> 不进 catalog（LLM 不可见）
编程式唤起隐藏 auditor -> result='审计通道已激活' status=completed
~~~
