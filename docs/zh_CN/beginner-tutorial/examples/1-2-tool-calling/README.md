# 示例：1-2 工具调用

本篇展示智能体如何使用内置 `glob` 与 `read` 工具检查笔记库，并依据实际读取的内容作答。下文完整内联了所需代码、配置、提示词、输入与示例输出。

运行环境需要 Python 3.13 或更高版本、uv，以及可使用 Flowing CLI 和包的 Python 环境。模型调用还需要在 shell 中设置你自己的 `DEEPSEEK_API_KEY`；本文不含真实凭证。请在全新的工作目录下按下文相对文件名创建文件，并从该目录执行命令。

## 复现

### 回放对话

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run flowing repl . < repl_input.txt
```

内联交互会枚举笔记目录并读取使用说明。全新运行时生成的状态文件可能不同，因此留档中的目录清单是示例，不是固定清单。

## 完整内联材料

下方每个代码块都包含本示例所用相对文件的完整内容。输出代码块是示例留档；模型答复和主机相关信息可能变化。

### `main.py`

```python
"""内置工具演示子项目入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
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
这个项目里有哪些文件？请概括 notes/使用说明.md 的主要内容。
/exit
```

### `repl_output.txt`

```text
(agent-main)>>>I'll look at the project structure first, then read that file.
[tool_call] glob {"pattern": "**/*", "path": "."}
[tool_call] read {"path": "./notes/使用说明.md"}
[tool:completed] glob -> ./.flowing/agent-main/tree.jsonl
./.flowing/agent-main/core.jsonl
./repl_output.txt
./.flowing/core.jsonl
./.flowing/agent-main/meta.json
./__pycache__/main.cpython-313.pyc
./repl_input.txt
./README.md
./model-tags.yaml
./models.yaml
./providers.yaml
./notes/使用说明.md
./notes/路线图.md
./root.fya
./main.py
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
项目根目录下主要有：`main.py`、`root.fya`、`README.md`、`repl_input.txt`、`repl_output.txt`、`model-tags.yaml`、`models.yaml`、`providers.yaml`，`notes/` 下有 `使用说明.md` 和 `路线图.md`，另有 `.flowing/`（含 `agent-main/` 的 meta.json、core.jsonl、tree.jsonl）和 `__pycache__/`。

`notes/使用说明.md` 说明本目录是演示用笔记库，要求 Python 3.13 或更高版本；常用命令为 `uv sync`（安装依赖）和 `uv run pytest`（运行测试）；并强调凭证一律通过环境变量持有，不要写进任何文件。
[thinking] (reasoning trace omitted)
项目根目录下主要有：`main.py`、`root.fya`、`README.md`、`repl_input.txt`、`repl_output.txt`、`model-tags.yaml`、`models.yaml`、`providers.yaml`，`notes/` 下有 `使用说明.md` 和 `路线图.md`，另有 `.flowing/`（含 `agent-main/` 的 meta.json、core.jsonl、tree.jsonl）和 `__pycache__/`。

`notes/使用说明.md` 说明本目录是演示用笔记库，要求 Python 3.13 或更高版本；常用命令为 `uv sync`（安装依赖）和 `uv run pytest`（运行测试）；并强调凭证一律通过环境变量持有，不要写进任何文件。
(agent-main)>>>
```

### `root.fya`

```text
description: 项目问答助手：能查看目录、读取文件并总结。
model_tag: default
tools:
  - read      # 裸名：命名空间省略，等价于 builtin::read
  - glob
---
$system_prompt:
你是项目问答助手。本项目的根目录绝对路径是 {{ env.PWD }}
（调用文件工具时一律使用该绝对路径，不要猜其它目录）。
当问题涉及项目内容时，必须先用 glob 查看目录结构、再用 read 读取相关
文件，然后根据读到的内容回答；不要凭印象编造。回答控制在五句话以内。
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

```
