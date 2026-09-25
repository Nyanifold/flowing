# 示例：2-3 装配与参数化

本篇声明智能体参数，将 CLI 值经 `main()` 传入装配，并使用 `setup()` 与 provide-inject 初始化实例状态。下文完整内联了所需代码、配置、提示词、输入与示例输出。

运行环境需要 Python 3.13 或更高版本、uv，以及可使用 Flowing CLI 和包的 Python 环境。模型调用还需要在 shell 中设置你自己的 `DEEPSEEK_API_KEY`；本文不含真实凭证。请在全新的工作目录下按下文相对文件名创建文件，并从该目录执行命令。

## 复现

### 显式传参启动标准对话

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run flowing repl . --user_name 小红 --locale en
```

### 回放对话

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run flowing repl . --user_name 小红 --locale en < repl_input.txt
```

### 在全新状态下检查必填参数校验

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run flowing repl .
```

缺参命令是一次独立的首次启动。每条独立命令都应使用全新工作状态，因为启动失败也可能留下持久化状态。标准运行特意将 `locale` 设为 `en`。

## 完整内联材料

下方每个代码块都包含本示例所用相对文件的完整内容。输出代码块是示例留档；模型答复和主机相关信息可能变化。

### `cli_error_output.txt`

```text
launch failed: RootAgent.setup() missing 1 required positional argument: 'user_name'
```

### `main.py`

```python
"""参数与 setup() 演示子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh",
               resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # mount 之前 provide：影响装配的值在挂载前提供（链式语义：沿亲代链上溯查找）
    runtime.provide("timezone", "Asia/Shanghai")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
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
请用中文一句话汇报：当前用户是谁、locale 是什么、时区是什么。
/exit
```

### `repl_output.txt`

```text
(agent-main)>>>当前用户是小红，locale 为 en，时区为 Asia/Shanghai。
[thinking] (reasoning trace omitted)
当前用户是小红，locale 为 en，时区为 Asia/Shanghai。
(agent-main)>>>
```

### `root.fya`

```text
description: 参数演示助手：args 声明 + setup() 典型写法。
model_tag: default
args:
  user_name: str        # 糖：裸类型字符串 → 必填参数
  locale:               # 完整写法：JSON Schema 关键字多行展开
    type: string
    default: zh
    description: 回复语言（zh / en）
---
$system_prompt:
你是简洁的问答助手。当前用户：{{ user_name }}；回复语言：{{ locale }}；
时区：{{ timezone }}。回答控制在一句话以内。
---
$script:
async def setup(self, user_name: str, locale: str = "zh"):
    self.user_name = user_name               # ① 参数存 self（模板 {{ user_name }} 的取值来源）
    self.locale = locale                     # ② 参数存 self（模板 {{ locale }} 的取值来源）
    self.ask_count = 0                       # ③ 计数器初始化（实例状态随创建就位）
    self.provide("locale", locale)           # ④ 把值挂上 provide 链（子组件可沿链取用）
    self.timezone = self.inject("timezone")  # ⑤ 取上层 provide 的值（main() 在挂载前提供）
```
