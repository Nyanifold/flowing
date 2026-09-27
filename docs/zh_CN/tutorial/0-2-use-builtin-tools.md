# 0-2 · 使用内置工具

## 前置阅读

[0-1 第一个智能体](0-1-hello-agent.md)（子项目三件套、`launch`、repl 用法）。
本篇在下文内联完整的 Python 入口、Agent 定义、模型配置、系统提示词、
演示笔记、用户输入和示例交互。请把这些内容保存到你自行创建的项目中，
并在该项目根目录运行命令。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 内置工具 | `builtin::` 命名空间的出厂工具：`read` / `write` / `bash` / `edit` / `grep` / `glob` / `finish` / `subagent-invoke` |
| 命名空间省略 | 裸名查找命中 `builtin::` 命名空间的规则：`read` 与 `builtin::read` 等价 |
| 注册 ≠ 可见 | builtins 随 Runtime 注册，但必须经 Agent 显式声明才进 LLM 可见面——危险工具不会被静默附加 |

## 目标

不写一行工具代码，让 Agent 获得文件能力：声明即用，并理解“注册 ≠ 可见”
这条安全铁律。

## 正文

### tools: 声明文件工具

`.fya` 头部的 `tools:` 列表声明这个 Agent 可以使用哪些工具。内置工具包括
`builtin::read` / `write` / `bash` / `edit` / `grep` / `glob` / `finish` /
`subagent-invoke`。本篇用两个只读工具：

```yaml
description: 项目问答助手：能查看目录、读取文件并总结。
model_tag: default
tools:
  - read      # 读文件（行窗偏移可选）
  - glob      # 按模式枚举文件
---
$system_prompt:
你是项目问答助手。本项目的根目录绝对路径是 {{ env.PWD }}
（调用文件工具时一律使用该绝对路径，不要猜其它目录）。
当问题涉及项目内容时，必须先用 glob 查看目录结构、再用 read 读取相关
文件，然后根据读到的内容回答；不要凭印象编造。回答控制在五句话以内。
```

### 命名空间省略

`builtin::` 是内置工具的命名空间。**裸名查找会命中 `builtin::` 命名空间**，
所以 `read` 与 `builtin::read` 完全等价——教程中一律写裸名；全限定写法
留给需要消歧的场景（如你自己的工具与内置工具同名，命名空间机制见 4-8）。

### 注册 ≠ 可见

Runtime 构造时注册了全部内置工具，但 LLM 只看得到 Agent 显式声明
（`tools:` / `add_tool`）的条目——**注册 ≠ 可见**。可写文件、执行命令等
危险工具因此不会被静默附加：你的 Agent 能做什么，完全由你的声明决定
（这条铁律的再现见 0-3 的 `subagent-invoke`、1-6 的 MCP 工具）。

### 给 Agent 一个路径基准

内置文件工具只收**绝对路径**（相对路径须显式传 `cwd` 参数），而 Agent
不知道进程的工作目录在哪。所以要让它真正用上文件工具，提示词里必须
给出绝对路径基准。本篇用现场求值拿到它——`{{ env.PWD }}` 在每次组装
上下文时求值（求值体系见 4-9）：

```yaml
$system_prompt:
你是项目问答助手。本项目的根目录绝对路径是 {{ env.PWD }}
（调用文件工具时一律使用该绝对路径，不要猜其它目录）。
当问题涉及项目内容时，必须先用 glob 查看目录结构、再用 read 读取相关
文件，然后根据读到的内容回答；不要凭印象编造。回答控制在五句话以内。
```

## 本篇不覆盖

- ToolEntry（别名、参数覆写）与参数聚合——4-4；
- 工具怎么写出来的（ScriptTool 写作）——2-3；执行机制全景——4-5；
- `finish` 结构化交卷（它在绑定层覆写下才有意义）——4-4；
- MCP 与 cli / request 零代码工具——1-6 / 1-7。

## 主线示例

下面先给出复现本例所需的完整项目材料。保存时，Python 代码块作为 `main.py`，
Agent 声明作为 `root.fya`，三段模型配置依次保存为 `providers.yaml`、
`models.yaml` 和 `model-tags.yaml`。API key 继续由环境变量提供：

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

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

```yaml
tags:
  default: deepseek-flash
```

将以下两份笔记分别保存为 `notes/使用说明.md` 和 `notes/路线图.md`：

```markdown
# 使用说明

本目录是一个演示用笔记库。

## 安装

需要 Python 3.13 或更高版本。

## 常用命令

- `uv sync`：安装依赖
- `uv run pytest`：运行测试

## 注意事项

凭证一律经环境变量持有，不要写进任何文件。
```

```markdown
# 路线图

- 2026-Q3：完成核心功能
- 2026-Q4：发布 1.0 版本
```

现在，在保存这些内联材料的项目根目录中启动 repl，并输入下列问题：

```console
$ uv run flowing repl .
(agent-main)>>> notes 目录里有哪些文件？请概括 notes/使用说明.md 的主要内容。
[tool_call] glob {"path": "<project-root>/notes", "pattern": "**/*"}
[tool_call] read {"path": "<project-root>/notes/使用说明.md"}
[tool:completed] glob -> <project-root>/notes/使用说明.md; <project-root>/notes/路线图.md
[tool:completed] read -> 0	# 使用说明
1	
2	本目录是一个演示用笔记库。
3	
4	## 安装
5	
6	需要 Python 3.13 或更高版本。
7	
8	## 常用命令
9	
10	- `uv sync`：安装依赖
11	- `uv run pytest`：运行测试
12	
13	## 注意事项
14	
15	凭证一律经环境变量持有，不要写进任何文件。
目录中有 `使用说明.md` 和 `路线图.md` 两份笔记。使用说明要求 Python 3.13 或更高版本，列出 `uv sync` 和 `uv run pytest` 两个常用命令，并提醒不要把凭证写进文件。
(agent-main)>>> /exit
```

读这段会话：模型并行发起 `glob` 和 `read` 两个调用；完整参数、两份笔记的
文件名、被读取文件的逐行内容以及基于这些内容生成的示例答复都展示在本文中。
工具调用如何嵌进回合循环，是 1-1 的主题。

留档中的 `<project-root>` 表示运行时项目目录的绝对路径；提示词通过
`{{ env.PWD }}` 取得该值。阅读或重放工具调用时，应将占位符替换为你自己的
项目路径。

以上文件块、用户输入和示例交互构成本篇完整演示；理解或复现它不需要其他材料。

## 小结

1. `tools:` 声明即用，内置工具零代码获得文件能力；
2. `builtin::` 命名空间可省略，裸名 `read` 即 `builtin::read`；
3. **注册 ≠ 可见**：LLM 只看得到显式声明的条目，危险工具不会被静默附加；
4. 文件工具只收绝对路径——提示词里用 `{{ env.PWD }}` 给 Agent 路径基准。
