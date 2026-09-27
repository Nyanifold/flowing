# 示例：2-4 干预点

`before_tool_call` 钩子会在 shell 删除命令执行前将其阻断，`after_turn` 则观察已结束的回合。留档展示阻断结果及仍存在的文件名。下文完整内联了所需代码、配置、提示词、输入与示例输出。

运行环境需要 Python 3.13 或更高版本、uv，以及可使用 Flowing CLI 和包的 Python 环境。模型调用还需要在 shell 中设置你自己的 `DEEPSEEK_API_KEY`；本文不含真实凭证。请在全新的工作目录下按下文相对文件名创建文件，并从该目录执行命令。

## 复现

### 回放干预场景

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run flowing repl . --user_name 小明 < repl_input.txt
```

交互会列出两个笔记文件，但不会读取正文，因此文件内容不是该交互的输入。留档 `ls -la` 输出中的属主、权限、文件大小和时间戳取决于运行主机。

## 完整内联材料

下方每个代码块都包含本示例所用相对文件的完整内容。输出代码块是示例留档；模型答复和主机相关信息可能变化。

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
请用 bash 删除 notes/路线图.md。
再用 bash 看一下 notes 目录里有什么。
/exit
```

### `repl_output.txt`

```text
[thinking] (reasoning trace omitted)
[tool_call] bash {"command": "rm \"notes/路线图.md\"", "cwd": "."}
[hook] before_tool_call: bash args={'command': 'rm "notes/路线图.md"', 'cwd': '.'}
[tool:blocked] bash -> 不允许删除：命令包含 rm，已阻断
如你所料，删除被安全钩子拦截了——rm 命令被阻断，文件 `notes/路线图.md` 未被删除。[hook] after_turn: ask_count=1 aborted=False

[thinking] (reasoning trace omitted)
[tool_call] bash {"command": "ls -la notes", "cwd": "."}
[hook] before_tool_call: bash args={'command': 'ls -la notes', 'cwd': '.'}
[tool:completed] bash -> exit_code: 0
--- stdout ---
total 16
drwxr-xr-x 2 user user 4096 Sep 19 00:44 .
drwxr-xr-x 5 user user 4096 Sep 19 06:49 ..
-rw-r--r-- 1 user user  266 Sep 19 00:44 使用说明.md
-rw-r--r-- 1 user user   74 Sep 19 00:44 路线图.md
--- stderr ---

notes 目录里有两个文件：`使用说明.md` 和 `路线图.md`（你要删的那个仍在）。[hook] after_turn: ask_count=2 aborted=False

(agent-main)>>>
```

### `root.fya`

```text
description: 钩子演示助手：before_tool_call 拦截 + after_turn 观察。
model_tag: default
args:
  user_name: str
tools:
  - bash     # 演示拦截：危险命令在执行前被钩子硬阻断
---
$system_prompt:
你是演示助手。当前用户：{{ user_name }}；项目根目录绝对路径：{{ env.PWD }}。
用户要求查看目录时用 bash 的 ls；用户要求删除文件时用 bash 的 rm（有安全
钩子保护，rm 会被拦截，届时向用户如实解释）。回答控制在一句话以内。
---
$script:
from flowing import Intercepted, on


# 挂法一：@on 类上声明（__init__ 阶段注册，早于 setup()）
@on("after_turn")
def _(self, turn):
    self.ask_count += 1
    print(f"[hook] after_turn: ask_count={self.ask_count} aborted={turn.aborted}")
    return turn


async def setup(self, user_name: str):
    self.user_name = user_name
    self.ask_count = 0
    self.timezone = self.inject("timezone")

    # 挂法二：setup() 内注册（实例级，注册时机在装配期）
    async def _guard(agent, tool_call):
        print(f"[hook] before_tool_call: {tool_call.name} args={tool_call.args}")
        if tool_call.name == "bash" and "rm" in tool_call.args.get("command", ""):
            # 硬阻断：工具不执行，LLM 收到 blocked 结果与原因
            raise Intercepted("不允许删除：命令包含 rm，已阻断")
        return tool_call
    self.hooks.before_tool_call(_guard)
```

### `notes/使用说明.md`（空文件占位）

该交互会用到这个文件名，但不会读取文件内容。在此相对路径创建空文件即可。

### `notes/路线图.md`（空文件占位）

该交互会用到这个文件名，但不会读取文件内容。在此相对路径创建空文件即可。
