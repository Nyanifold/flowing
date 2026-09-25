# 0-3 · 初识多智能体

## 前置阅读

[0-1 第一个智能体](0-1-hello-agent.md)、[0-2 使用内置工具](0-2-use-builtin-tools.md)
（`tools:` 声明、命名空间省略、注册 ≠ 可见）。本篇以内联方式给出完整项目
材料与示例交互；请在自行创建的项目目录中保存代码、提示词、配置和笔记，
然后从该目录运行命令。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 编排者 | 根 Agent 扮演的角色：把任务派给子智能体、汇总交付 |
| 子智能体（子 Agent） | 由另一个 Agent 唤起的 Agent 实例；本篇使用出厂的只读探索类型 `explore-agent` |
| `subagents:` 声明 | `.fya` 头部的“这个 Agent 可以使用哪个子智能体**类型**”的声明——是类型绑定，不是创建实例（机制深化见 2-1） |
| `subagent-invoke` | LLM 唤起子智能体的内置工具；与 0-2 同一规则——注册 ≠ 可见，须显式声明 |
| 类型绑定 | 子智能体声明的本质：声明的是“类型 + 用法”，实例在唤起时才诞生（2-2 展开创建与续接） |

## 目标

第一次见多智能体协作：编排者 + 出厂探索智能体。用户下两个要求——
“检查目录”与“写一个文件”——看探索智能体如何按自己的能力边界给出
不同响应，编排者又如何按各自结局分别汇总交付。

## 正文

### subagents: 一句话版

`.fya` 头部的 `subagents:` 列表声明这个 Agent 可以使用哪些子智能体
**类型**：

```yaml
description: 任务编排者：派发只读检查任务，并如实汇总结果。
model_tag: default
tools:
  - subagent-invoke
subagents:
  - explore-agent   # 裸名：命名空间省略，等价于 builtin::explore-agent
---
$system_prompt:
你是任务编排者。本项目的根目录绝对路径是 {{ env.PWD }}。
对于查看、检查或读取任务，调用 subagent-invoke 派给 explore-agent，并在 prompt
中写明目标绝对路径；拿到结果后如实汇报。对于创建或修改文件的请求，可以先向
explore-agent 确认其能力；如果它只有只读工具，不要声称已经写入，应明确说明边界。
```

上面是完整的 `root.fya` 内容。`subagent-invoke` 是 Agent 显式声明的唯一工具；
运行时会提供出厂的只读探索子智能体。

与 0-2 完全相同的两个规则再现：`explore-agent` 是 `builtin::` 命名空间的
出厂标准子智能体，裸名即可引用（全限定名 `builtin::explore-agent` 留给
消歧场景）；`subagent-invoke` 是唤起子智能体的唯一工具入口，同样
**注册 ≠ 可见**，必须在 `tools:` 显式声明。

`subagents:` 声明的是**类型绑定，不是实例**：此刻还没有任何子智能体被
创建；实例在唤起时才诞生（创建-续接是 2-2 的主题）。

### 一条 query 触发的自动路由

用户只发一条普通消息，LLM 看到编排者提示词与 `<available_subagents>`
目录后自动完成派单：

1. 编排者判断任务类型 → 调用 `subagent-invoke` 指定 `explore-agent`；
2. 子智能体此刻被创建，独立跑自己的回合（用自己的工具集干活）；
3. 子智能体的最终产出经 `SubagentResult` 回到编排者（`result` 与
   `subagent_status` 分离，2-2 展开）；
4. 编排者把结果汇总交付。

**能力边界由工具集构成保证**：`explore-agent` 的工具集固定为只读的
`read` / `grep` / `glob`——“只读”不是靠提示词自律，而是它根本没有
写工具。所以写文件的请求只会得到诚实的能力边界汇报，而不是越权写入
或伪造结果。

## 本篇不覆盖

- `name=` / `resume=` 续接同一实例（有记忆）——2-2；
- 参数覆写与 specified、`visible=False`、catalog 渲染细节——2-1 / 4-4；
- 手写自己的子智能体类型——2-1；
- `SubagentResult` 字段级契约与 `on_subagent_invoke` / `on_subagent_returns`
  钩子——2-2 / 4-6。

## 主线示例

先以内联内容准备独立项目。Agent 定义与完整系统提示词已在上文给出；入口
和模型配置如下，凭证只通过环境变量注入：

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

依次将配置保存为 `providers.yaml`、`models.yaml` 和 `model-tags.yaml`。
为探索智能体准备以下两份完整笔记，分别保存为 `notes/使用说明.md` 和
`notes/路线图.md`：

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

设置 `DEEPSEEK_API_KEY` 后，从保存这些文件的项目根目录启动 repl，并输入
下方两条用户消息。以下工具路径用 `<project-root>` 表示运行时项目根，以免
把本机绝对路径写入文档：

```console
$ uv run flowing repl .
(agent-main)>>> 请检查 notes 目录里有哪些文件，并如实汇报。
我会把只读检查任务交给 explore-agent。
[thinking]（推理痕迹已省略）
[tool_call] subagent-invoke {"agent_type": "explore-agent", "prompt": "请检查 <project-root>/notes 中有哪些文件，列出文件名并概括可确认的内容。只读，不要修改文件。"}
[tool:completed] subagent-invoke -> notes 中有 `使用说明.md` 和 `路线图.md` 两个 Markdown 文件。使用说明要求 Python 3.13 或更高版本，列出 `uv sync`、`uv run pytest`，并要求凭证保存在环境变量中；路线图列出 2026-Q3 完成核心功能、2026-Q4 发布 1.0。
我检查了两份笔记：使用说明列出环境要求、常用命令和凭证规则；路线图列出两个阶段目标。
(agent-main)>>> 请帮我在 notes 目录里新建一个文件 总结.md，把 使用说明.md 的要点写进去。
写入请求超出只读探索智能体的能力；我会如实说明是否能够完成。
[thinking]（推理痕迹已省略）
[tool_call] subagent-invoke {"agent_type": "explore-agent", "prompt": "请在 <project-root>/notes 中创建 总结.md，概括 使用说明.md 的要点。如果你没有文件写入能力，请明确说明，不要声称创建成功。"}
[tool:completed] subagent-invoke -> 无法创建文件：可用工具只有只读的 `read`、`grep`、`glob`，没有创建或写入能力；`总结.md` 未创建。
我无法创建该文件：探索智能体没有写入工具，因此 `总结.md` 并未创建。
(agent-main)>>> /exit
```

第一条消息演示只读探索，派单 prompt 和完整结果都已展示。第二条消息演示写入
请求的能力边界：探索智能体没有写工具，因此明确报告文件未创建。上面的两段
`[thinking]` 仅标记省略的推理痕迹；用户输入、工具交互和可见结果均保留在本文。

## 小结

1. `subagents:` 是类型绑定声明，不是创建实例；实例在唤起时诞生；
2. `subagent-invoke` 与 0-2 同一规则：注册 ≠ 可见，须显式声明；
3. 一条 query 即可触发自动路由：编排者派单 → 子智能体独立干活 → 结果回传汇总；
4. 能力边界由工具集构成保证：只读探索智能体对写请求只会诚实拒绝。
