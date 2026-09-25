# 1-3 · 工具特化：能力边界的结构性定义

> 复现条件：Python 3.13+、uv、可用的 Flowing CLI、DeepSeek API key，以及终端网络连接。
> 本篇末尾内联完整配置、提示词、数据、输入与示例输出；在新建空目录中按标注文件名保存后即可运行。

> 前置：第 1-2 篇“工具调用”

## 这篇讲什么

Agent 的能力边界由什么决定。本篇的核心论点：能力边界由工具表
**结构性地**定义，不由提示词约定；因此“这个 Agent 会做什么”
的问题应当看工具表，而不是读系统提示词。

## 背景知识

提示词对模型行为的影响是概率性的——它提高某种行为的输出概率，
但模型始终可能偏离。在需要确定性的场景（权限、安全、合规），
工程上不接受概率性约束。工具表提供了结构性的替代：模型只能
提议调用其上下文工具表中的工具，未列入的工具在协议层面不可
达。约束从“模型大概率遵守”变成“结构上不可能违反”。

这一原则与常规软件的最小权限原则一致：执行主体只持有完成
职责所需的最小能力集合。在 Agent 系统中，执行主体是 Agent，
能力载体是工具表。

## 核心概念

### 能力边界由工具表构成决定

只挂载只读工具的 Agent 无法执行写操作——这与提示词如何描述
无关，与模型是否“知道怎么写”也无关。模型可能具备写出正确
内容的全部知识，但没有相应工具时，写动作在协议层面不可能发生。

按文末完整示例启动会话，先让 Agent 检查 notes 目录，再要求它在
该目录中新建文件。下面是第二个任务的示例交互；模型措辞可能变化：

```console
(agent-main)>>> 请帮我在 notes 目录里新建一个文件 总结.md，把 使用说明.md 的要点写进去。
[tool_call] read {"path": "<project-root>/notes/使用说明.md"}
[tool:completed] read -> 0  # 使用说明
1
2  本目录是一个演示用笔记库。
3
4  ## 安装
5
6  需要 Python 3.13 或更高版本。
7
8  ## 常用命令
9
10 - `uv sync`：安装依赖
11 - `uv run pytest`：运行测试
12
13 ## 注意事项
14
15 凭证一律经环境变量持有，不要写进任何文件。
我无法创建 `总结.md`：当前可用工具只有 `read`、`grep` 和 `glob`，都没有写入能力。以下是可自行保存的内容：

# 总结：使用说明

- 本目录是一个演示用笔记库。
- 需要 Python 3.13 或更高版本。
- `uv sync` 用于安装依赖；`uv run pytest` 用于运行测试。
- 凭证只通过环境变量持有，不要写进任何文件。
```

逐行看：提示符后是你的写入请求；答复列出仅有的三个只读工具，
说明写动作无法发生，同时把能做到的部分做了——用 `read` 读取
`使用说明.md` 并把要点整理成草稿。会话
结束后，两个笔记文件保持不变，也没有新文件被创建。边界不是模型
“自觉不写”，而是它根本拿不到写工具。

由此得到两个工程推论：

1. **审查 Agent 的行为先看工具表**。预测一个 Agent 能做什么，
   读它的工具声明比读系统提示词更可靠；
2. **新增能力 = 新增工具 + 更新描述**。能力调整是结构变更，
   可审查、可回滚，而不是提示词措辞的微调。

### 工具表即公开接口

工具表同时是模型的路由依据与框架的校验依据，应按接口设计的
标准维护：

- **职责单一**：一个工具做一类事；“万能工具”会让参数 schema
  膨胀、路由准确率下降；
- **描述说明使用场景**：描述面向模型阅读，“什么时候用我”
  比“我有哪些参数”更影响行为；
- **数量从最小集起步**：每个工具占用上下文并增加选择错误率；
  权限的默认答案是否定，按需增加。

本例的完整声明如下；能力边界完全由这张表的结构决定：

```yaml
description: 笔记库助手：只读，能查看目录、搜索与读取笔记并总结。
tools:
  - read
  - grep
  - glob
```

值得对照的是，这个 Agent 的系统提示词里没有任何“你不许写
文件”的字样——上面
留档中的拒绝不是提示词教出来的，而是工具表结构的直接后果。

### 注册与可见：两个状态

工具在其生命周期中有两个状态，区分它们才能正确配置能力边界：

- **注册**：工具在框架的全局注册表中——表示这个能力存在；
- **可见**：工具出现在某个 Agent 的上下文工具表中——表示这个
  Agent 被允许提议它。

只有可见的工具可以被模型提议。高权限工具（文件写入、命令
执行）应注册在场、按 Agent 显式开启，而不是默认对全部 Agent
可见。本例中，框架可以注册写文件、执行命令等工具，
但 Agent 的 `tools:` 列表只列了三个只读工具，写入类工具
对这个 Agent 不可见——上面留档中的拒绝正是这一机制的运行
表现。

## 常见误区

1. **用提示词收回已给出的能力**。能力应在工具表层面控制；
   提示词约束是概率性的，不能作为权限机制；
2. **工具越多越好**。工具表是接口：数量增加上下文开销与
   路由错误率，应从最小集演进；
3. **描述写泛以求灵活**。“通用助手”式的描述等于没有描述；
   写清适用任务类型才能提高路由准确率。

## 练习

1. 为“订单助手”与“代码审查员”各设计一个最小工具表，说明
   两者的差异由什么决定；
2. 根据下方完整示例中的 `tools:` 列表推断 Agent 的能力边界，
   再与示例交互中的实际行为对照。

## 完整示例：只读工具表与写入请求

在新建空目录中创建 `notes/`，并保存以下内容。将 `...` 替换为自己的
API key 并仅设置为环境变量；不要将真实凭证写入配置文件。本文给出
Agent 代码、声明、模型配置、
完整提示词、可读数据、输入和示例输出。

`pyproject.toml`：

```toml
[project]
name = "flowing-chapter-1-3"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["flowing-agent"]

[tool.uv]
package = false
```

`main.py`：

```python
from flowing import Runtime


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

`root.fya`（完整工具表和系统提示词）：

```yaml
description: 笔记库助手：只读，能查看目录、搜索与读取笔记并总结。
model_tag: default
tools:
  - read
  - grep
  - glob
---
$system_prompt:
你是笔记库助手。本项目的根目录绝对路径是 {{ env.PWD }}（调用文件工具时一律使用该绝对路径，不要猜其它目录）。笔记存放在 notes 目录。回答涉及笔记内容的问题时，先用 glob 查看目录、按需用 grep 搜索、用 read 读取相关文件，然后根据读到的内容回答；不要凭印象编造。
```

`providers.yaml`：

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

`models.yaml`：

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

`model-tags.yaml`：

```yaml
tags:
  default: deepseek-flash
```

在保存了本文所列文件的当前目录运行：

```sh
uv sync
export DEEPSEEK_API_KEY="..."
uv run flowing repl .
```

全部可读数据：

`notes/使用说明.md`：

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

`notes/路线图.md`：

```markdown
# 路线图

- 2026-Q3：完成核心功能
- 2026-Q4：发布 1.0 版本
```

完整输入：

```text
请检查 notes 目录里有哪些文件，并如实汇报。
请帮我在 notes 目录里新建一个文件 总结.md，把 使用说明.md 的要点写进去。
/exit
```

在同一空目录运行 `uv run flowing repl .`。下面是行为示意；模型
措辞可能不同，内部推理轨迹不展示：

```console
(agent-main)>>>请检查 notes 目录里有哪些文件，并如实汇报。
[tool_call] glob {"pattern": "**/*", "path": "<project-root>/notes"}
[tool:completed] glob -> <project-root>/notes/使用说明.md
<project-root>/notes/路线图.md
notes 目录里共有 2 个 Markdown 文件：`使用说明.md` 和 `路线图.md`。
(agent-main)>>>请帮我在 notes 目录里新建一个文件 总结.md，把 使用说明.md 的要点写进去。
[tool_call] read {"path": "<project-root>/notes/使用说明.md"}
[tool:completed] read -> 0  # 使用说明
1
2  本目录是一个演示用笔记库。
3
4  ## 安装
5
6  需要 Python 3.13 或更高版本。
7
8  ## 常用命令
9
10 - `uv sync`：安装依赖
11 - `uv run pytest`：运行测试
12
13 ## 注意事项
14
15 凭证一律经环境变量持有，不要写进任何文件。
我无法创建 `总结.md`：当前可用工具只有 `read`、`grep` 和 `glob`，都没有写入能力。以下是可自行保存的内容：

# 总结：使用说明

- 本目录是一个演示用笔记库。
- 需要 Python 3.13 或更高版本。
- `uv sync` 用于安装依赖；`uv run pytest` 用于运行测试。
- 凭证只通过环境变量持有，不要写进任何文件。
(agent-main)>>>/exit
```

## 小结

1. 能力边界由工具表结构性定义，提示词不承担约束职责；
2. 工具表按接口设计标准维护：职责单一、描述面向路由、
   数量从最小集起步；
3. 注册与可见是两个状态，高权限工具按 Agent 显式开启；
4. 审查与调整 Agent 能力的正确位置是工具表。
