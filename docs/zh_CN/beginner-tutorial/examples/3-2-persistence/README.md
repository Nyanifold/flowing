# 示例：3-2 持久化

按顺序先记住一个数字，再由第二个进程记住颜色并在未正常关闭时退出，随后恢复并询问两个值。下文完整内联了所需代码、配置、提示词、输入与示例输出。

运行环境需要 Python 3.13 或更高版本、uv，以及可使用 Flowing CLI 和包的 Python 环境。模型调用还需要在 shell 中设置你自己的 `DEEPSEEK_API_KEY`；本文不含真实凭证。请在全新的工作目录下按下文相对文件名创建文件，并从该目录执行命令。

## 复现

### 保存数字

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run flowing repl . < repl_input.txt
```

### 模拟记住颜色后崩溃

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run python demo_crash.py
```

### 恢复并询问两个值

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run flowing repl . < repl_input_run2.txt
```

三条命令须按顺序作用于同一份全新工作状态。崩溃脚本会故意调用 `os._exit(9)` 退出进程，不执行正常关闭。记录数量和模型答复取决于实际运行。

## 完整内联材料

下方每个代码块都包含本示例所用相对文件的完整内容。输出代码块是示例留档；模型答复和主机相关信息可能变化。

### `crash_output.txt`

```text
回合完成 status=completed；现在模拟 kill -9（os._exit(9)）
```

### `crash_wc.txt`

```text
5 .flowing/agent-main/tree.jsonl
```

### `demo_crash.py`

```python
"""崩溃模拟：os._exit(9) 等价于 kill -9——不走 shutdown()。

write-behind 的排空屏障不执行，但已提交记录不重放丢失（崩溃窗口演示）。
运行：uv run python demo_crash.py
"""
import asyncio
import os

from flowing import launch


async def main() -> None:
    runtime = await launch(".")            # 恢复管线：重放 tree/state 日志
    agent = await runtime.get_agent("agent-main")
    result = await agent.query("再记住一个颜色：紫色。")
    print(f"回合完成 status={result.status}；现在模拟 kill -9（os._exit(9)）", flush=True)
    os._exit(9)                            # 无 shutdown：进程即刻死亡


asyncio.run(main())
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
记住数字 731。
/exit
```

### `repl_input_run2.txt`

```text
我让你记的数字和颜色分别是什么？
/exit
```

### `repl_output.txt`

```text
(agent-main)>>>好的，我记住了：731。
[thinking] (reasoning trace omitted)
好的，我记住了：731。
(agent-main)>>>
```

### `repl_output_run2.txt`

```text
(agent-main)>>>你让我记的数字是731，颜色是紫色。
[thinking] (reasoning trace omitted)
你让我记的数字是731，颜色是紫色。
(agent-main)>>>
```

### `root.fya`

```text
description: 持久化演示助手：记住用户告知的内容。
model_tag: default
---
$system_prompt:
你是记忆演示助手。用户让你记住信息时，用一句话复述确认；被问起时准确
复述。回答控制在一句话以内。
```
