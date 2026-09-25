# 示例：1-3 工具特化

本篇只向智能体提供 `glob`、`grep` 和 `read`，因此它可以检查和总结笔记，却不能创建文件。内联请求中的创建摘要任务用于呈现这一能力边界。下文完整内联了所需代码、配置、提示词、输入与示例输出。

运行环境需要 Python 3.13 或更高版本、uv，以及可使用 Flowing CLI 和包的 Python 环境。模型调用还需要在 shell 中设置你自己的 `DEEPSEEK_API_KEY`；本文不含真实凭证。请在全新的工作目录下按下文相对文件名创建文件，并从该目录执行命令。

## 复现

### 回放请求

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run flowing repl . < repl_input.txt
```

下方内联使用说明。留档交互还枚举并读取路线图，其完整内容也一并内联。

## 完整内联材料

下方每个代码块都包含本示例所用相对文件的完整内容。输出代码块是示例留档；模型答复和主机相关信息可能变化。

### `main.py`

```python
"""多智能体初识子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)   # 恢复既有 agent
    else:
        # 固定 agent_id → 幂等挂载：第二次启动走恢复，“同一个根回来了”
        await runtime.mount("@/root.fya", agent_id="agent-main")
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
请检查 notes 目录里有哪些文件，并如实汇报。
请帮我在 notes 目录里新建一个文件 总结.md，把 使用说明.md 的要点写进去。
/exit
```

### `repl_output.txt`

````text
(agent-main)>>>I'll check the notes directory.
[tool_call] glob {"pattern": "**/*", "path": "./notes"}
[tool:completed] glob -> ./notes/使用说明.md
./notes/路线图.md
[thinking] (reasoning trace omitted)
[tool_call] read {"path": "./notes/使用说明.md"}
[tool_call] read {"path": "./notes/路线图.md"}
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
[tool:completed] read -> 0	# 路线图
1	
2	- 2026-Q3：完成核心功能
3	- 2026-Q4：发布 1.0 版本
notes 目录里共有 **2 个文件**（均为 Markdown，无子目录）：

| 文件（绝对路径） | 内容摘要 |
|---|---|
| `.../notes/使用说明.md` | 演示用笔记库的使用说明：安装需 Python 3.13+；常用命令 `uv sync`（装依赖）、`uv run pytest`（跑测试）；注意事项为凭证一律经环境变量持有，不要写进任何文件 |
| `.../notes/路线图.md` | 路线图两条：2026-Q3 完成核心功能；2026-Q4 发布 1.0 版本 |

说明：以上是通过 glob 枚举目录、再 read 读取全文后的如实汇报，未做任何推测或补充。两个文件都很短（分别 16 行、3 行），内容已如上完整概括，无遗漏段落。
[thinking] (reasoning trace omitted)
notes 目录里共有 **2 个文件**（均为 Markdown，无子目录）：

| 文件（绝对路径） | 内容摘要 |
|---|---|
| `.../notes/使用说明.md` | 演示用笔记库的使用说明：安装需 Python 3.13+；常用命令 `uv sync`（装依赖）、`uv run pytest`（跑测试）；注意事项为凭证一律经环境变量持有，不要写进任何文件 |
| `.../notes/路线图.md` | 路线图两条：2026-Q3 完成核心功能；2026-Q4 发布 1.0 版本 |

说明：以上是通过 glob 枚举目录、再 read 读取全文后的如实汇报，未做任何推测或补充。两个文件都很短（分别 16 行、3 行），内容已如上完整概括，无遗漏段落。
(agent-main)>>>我做不到——需要如实说明原因。

**当前我可用的工具只有三个：`read`（读文件）、`grep`（正则搜索）、`glob`（按模式枚举文件）。没有任何写入、创建、编辑或执行 shell 命令的工具。** 因此我无法在 notes 目录里新建 `总结.md`，也无法把内容落到磁盘上。这不是权限或路径问题，而是能力缺口；如果我声称“已创建”，那会是假的。

（另外按你笔记里的《使用说明.md》“注意事项”，凭证不写进文件——这里不涉及凭证，我只提一句以免上下文混淆。）

我能做的是把内容准备好，由你或具备写权限的一方落盘。以下是基于 `使用说明.md` 全文的要点草稿，可直接复制为 `notes/总结.md`：

```markdown
# 总结：使用说明

- 本目录是一个演示用笔记库。
- 安装要求：Python 3.13 或更高版本。
- 常用命令：
  - `uv sync`：安装依赖
  - `uv run pytest`：运行测试
- 注意事项：凭证一律经环境变量持有，不要写进任何文件。
```

内容来源为 `notes/使用说明.md`（共 16 行，已通读），要点无删减之外的新增信息。

如果你希望我换个方式帮忙，可行的替代方案：
1. 你自行创建文件后，我用 `read` / `grep` 帮你校对内容是否与原文一致；
2. 我按你的要求输出任意格式的内容（纯文本、带表格、精简版等），你复制粘贴；
3. 若该环境本应提供写入工具，可能是工具未挂载到我这一侧，建议检查一下配置。
[thinking] (reasoning trace omitted)
我做不到——需要如实说明原因。

**当前我可用的工具只有三个：`read`（读文件）、`grep`（正则搜索）、`glob`（按模式枚举文件）。没有任何写入、创建、编辑或执行 shell 命令的工具。** 因此我无法在 notes 目录里新建 `总结.md`，也无法把内容落到磁盘上。这不是权限或路径问题，而是能力缺口；如果我声称“已创建”，那会是假的。

（另外按你笔记里的《使用说明.md》“注意事项”，凭证不写进文件——这里不涉及凭证，我只提一句以免上下文混淆。）

我能做的是把内容准备好，由你或具备写权限的一方落盘。以下是基于 `使用说明.md` 全文的要点草稿，可直接复制为 `notes/总结.md`：

```markdown
# 总结：使用说明

- 本目录是一个演示用笔记库。
- 安装要求：Python 3.13 或更高版本。
- 常用命令：
  - `uv sync`：安装依赖
  - `uv run pytest`：运行测试
- 注意事项：凭证一律经环境变量持有，不要写进任何文件。
```

内容来源为 `notes/使用说明.md`（共 16 行，已通读），要点无删减之外的新增信息。

如果你希望我换个方式帮忙，可行的替代方案：
1. 你自行创建文件后，我用 `read` / `grep` 帮你校对内容是否与原文一致；
2. 我按你的要求输出任意格式的内容（纯文本、带表格、精简版等），你复制粘贴；
3. 若该环境本应提供写入工具，可能是工具未挂载到我这一侧，建议检查一下配置。
(agent-main)>>>
````

### `root.fya`

```text
description: 笔记库助手：只读，能查看目录、搜索与读取笔记并总结。
model_tag: default
tools:
  - read      # 裸名：命名空间省略，等价于 builtin::read
  - grep
  - glob
---
$system_prompt:
你是笔记库助手。本项目的根目录绝对路径是 {{ env.PWD }}
（调用文件工具时一律使用该绝对路径，不要猜其它目录）。
笔记存放在 notes 目录。回答涉及笔记内容的问题时，先用 glob 查看
目录、按需用 grep 搜索、用 read 读取相关文件，然后根据读到的内容
回答；不要凭印象编造。
```

### `notes/使用说明.md`

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

### `notes/路线图.md`

```markdown
# 路线图

- 2026-Q3：完成核心功能
- 2026-Q4：发布 1.0 版本
```
