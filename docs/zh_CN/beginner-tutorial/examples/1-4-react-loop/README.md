# 示例：1-4 ReAct 循环

本篇将文件检查工具与可选的回合结束断言结合起来。`--strict done` 模式会在最终答复未单独包含 `DONE` 一行时继续导向智能体。下文完整内联了所需代码、配置、提示词、输入与示例输出。

运行环境需要 Python 3.13 或更高版本、uv，以及可使用 Flowing CLI 和包的 Python 环境。模型调用还需要在 shell 中设置你自己的 `DEEPSEEK_API_KEY`；本文不含真实凭证。请在全新的工作目录下按下文相对文件名创建文件，并从该目录执行命令。

## 复现

### 回放文件检查对话

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run flowing repl . < repl_input.txt
```

### 在全新状态下尝试回合结束断言

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run flowing repl . --strict done < repl_input_run2.txt
```

第二份留档以 `OK` 结束，没有显示 `DONE` 行。应将其视为断言流程的不完整示例；留档并未证明断言已满足。

## 完整内联材料

下方每个代码块都包含本示例所用相对文件的完整内容。输出代码块是示例留档；模型答复和主机相关信息可能变化。

### `main.py`

```python
"""ReAct 循环示例工程入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime


async def main(strict: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # --strict done 经 launch 透传 main()，再转发给 mount → setup()
    kwargs = {"strict": strict} if strict else {}
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
先用 glob 查看 notes 目录有哪些文件，再用 read 读取 使用说明.md，最后用两句话概括它讲了什么。
/messages
/exit
```

### `repl_input_run2.txt`

```text
只回复“好的”两个字，什么都不要加。
/exit
```

### `repl_output.txt`

```text
(agent-main)>>>I'll start by looking at the notes directory structure.
[tool_call] glob {"pattern": "**/*", "path": "./notes"}
[tool:completed] glob -> ./notes/使用说明.md
./notes/路线图.md
The notes directory has two files. Now reading 使用说明.md.
[thinking] (reasoning trace omitted)
[tool_call] read {"path": "./notes/使用说明.md"}
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
notes 目录下只有 使用说明.md 和 路线图.md 两个文件。使用说明.md 说明这是一个演示用笔记库，要求 Python 3.13 以上环境，并给出 `uv sync` 装依赖、`uv run pytest` 跑测试两条常用命令。它还提醒凭证只能通过环境变量持有，不要写进任何文件。
(agent-main)>>>1  user      先用 glob 查看 notes 目录有哪些文件，再用 read 读取 使用说明.md，最后用两句话概括它讲了什么。
2  provider  I'll start by looking at the notes directory structure.
3  tool      .
4  provider  The notes directory has two files. Now reading 使用说明.md.
5  tool      0 # 使用说明 1 2 本目录是一个演示用笔记库。 3 4 ## 安装 5 6 需要 Python 3.13 或更高版本。 7 8 ## 常用命令 9 10 …
6  provider  notes 目录下只有 使用说明.md 和 路线图.md 两个文件。使用说明.md 说明这是一个演示用笔记库，要求 Python 3.13 以上环境，并给出 `…
(agent-main)>>>
```

### `repl_output_run2.txt`

```text
(agent-main)>>>好的
[steer] 验收条件未满足：你的最终回复必须包含单独一行的 DONE。请重新回答刚才的问题，并在末尾附上 DONE。
好的
(agent-main)>>>
```

### `root.fya`

```text
description: 项目问答助手：能查看目录、读取文件并总结。
model_tag: default
args:
  strict:
    type: string
    default: ""
    description: 设为 done 时启用“答复必须含 DONE”的收尾断言
tools:
  - read      # 裸名：命名空间省略，等价于 builtin::read
  - glob
---
$system_prompt:
你是项目问答助手。本项目的根目录绝对路径是 {{ env.PWD }}
（调用文件工具时一律使用该绝对路径，不要猜其它目录）。
当问题涉及项目内容时，先用 glob 查看目录结构、再用 read 读取相关
文件，然后根据读到的内容回答；不要凭印象编造。回答控制在五句话以内。
---
$script:
from flowing import MessageKind, TextBlock
from flowing.composables import use_prompt_until


async def setup(self, strict: str = ""):
    if strict != "done":
        return

    # 断言型终止条件：回合收尾时检查最终答复是否含 DONE，
    # 不满足则以一条导向消息驱动下一轮
    def _done(agent, turn) -> bool:
        for mid in reversed(turn.message_ids):
            msg = agent.chain.get(mid)
            if msg.kind is MessageKind.PROVIDER:
                text = "".join(b.text for b in msg.content
                               if isinstance(b, TextBlock))
                return "DONE" in text
        return True   # 空回合不催

    use_prompt_until(
        self,
        predicate=_done,
        message=("验收条件未满足：你的最终回复必须包含单独一行的 DONE。"
                 "请重新回答刚才的问题，并在末尾附上 DONE。"),
    )
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
