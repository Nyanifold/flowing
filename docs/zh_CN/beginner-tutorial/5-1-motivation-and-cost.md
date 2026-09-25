# 5-1 · 拆分的动因与成本

> 自包含示例：本篇下方列出完整的运行配置、Agent 定义、输入数据与示例输出。先按清单在当前练习目录创建这些相对文件，再运行 `uv run flowing repl .`。
> 对话示例需要设置 `DEEPSEEK_API_KEY` 环境变量；不要把真实凭证写入配置。

> 前置：第 1-3 篇“工具特化”

## 这篇讲什么

从单 Agent 走向多智能体的判断框架：单 Agent 的三类扩展限制、
拆分引入的三类成本，以及何时值得拆分的判据。

## 背景知识

软件架构史上反复出现同一组决策：一个单元膨胀后，是否拆分为
多个职责单一的单元。每次的权衡结构相同——拆分的收益（内聚、
隔离、并行）对拆分的成本（通信、一致性、运维面）。多智能体
是这组决策在 Agent 系统上的最新形态，判断标准可以直接沿用。

## 核心概念

### 单 Agent 的三类扩展限制

| 限制 | 表现 | 根因 |
|---|---|---|
| 上下文 | 多领域知识、工具 schema、长历史竞争同一窗口 | 窗口是硬上限；内容越多，注意力越分散 |
| 权限 | 读代码的与付钱的持有同一工具表 | 能力并集对全部请求开放，无法按请求隔离 |
| 吞吐 | 一个逻辑回合顺序执行 | 事件驱动循环的串行本质 |

前两类是结构性限制——工具表与窗口无法按需分叉，撞上就必须拆；
第三类是性能问题，视吞吐量需求决定。

### 拆分引入的三类成本

| 成本 | 内容 | 量级感 |
|---|---|---|
| 通信 | 派单与汇总的额外模型调用、目录占用的上下文 | 每轮数十到数千 token |
| 一致性 | 多个上下文的视图可能不一致；结果合并需校验 | 随协作链长度增长 |
| 调试 | 一次失败要跨组件定位责任 | 排查路径 = 协作链长度 |

这三类成本不是缺陷，是结构的固有属性；问题只在收益是否覆盖。

下面是一个已经拆分的最小系统：一个编排者加一个只读探索 Agent。两项输入连续发生在同一会话中：先列出笔记条目，再要求基于其中一篇创建摘要。先按后文的文件清单在当前练习目录创建文件，然后运行 `uv run flowing repl .` 并逐行输入以下内容：

输入：
```text
请检查 notes 目录里有哪些文件，并如实汇报。
请帮我在 notes 目录里新建一个文件 总结.md，把 使用说明.md 的要点写进去。
/exit
```

代表性输出：
```text
(agent-main)>>> 请检查 notes 目录里有哪些文件，并如实汇报。
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"explore-agent","prompt":"请列出 <practice-root>/notes 下的全部文件与子目录；只报告实际枚举到的名称，不要读取或猜测内容。"}
[tool:completed] subagent-invoke ->
<practice-root>/notes 下有两个普通文件、没有子目录：使用说明.md、路线图.md。
[thinking] (reasoning trace omitted)
(agent-main)>>> 请帮我在 notes 目录里新建一个文件 总结.md，把 使用说明.md 的要点写进去。
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type":"explore-agent","prompt":"读取 <practice-root>/notes/使用说明.md，提炼要点，并尝试在同一目录创建 总结.md。若没有写入能力，请明确说明，不能声称文件已创建。"}
[tool:completed] subagent-invoke ->
使用说明.md 的要点是：本目录为演示笔记库；需要 Python 3.13 或更高版本；常用命令为 uv sync 和 uv run pytest；凭证只经环境变量提供，不写入文件。explore-agent 只有只读能力，无法创建 总结.md。
[thinking] (reasoning trace omitted)
(agent-main)>>>
```

运行时的自然语言可能不同；可核对的结果是目录中有两个指定文件，以及只读 Agent 拒绝写入。

逐行看这段会话里的收益与成本同时在场：查看类任务由一个工具表
极小的只读 Agent 执行（权限隔离的收益——它结构上不可能写文件）；
写入请求被同一个结构拒绝（隔离的代价——这个系统确实做不了写）；
两个委派包的全文在 `[tool_call]` 行可见——任务、路径、回报要求
都要显式携带，因为工作者看不到编排者的历史；而编排者收到的两段
汇报都很长，它们原文进入了编排者的上下文
（通信成本的直观形态）。判据演示：“三个问题”中“工具表差异
显著”与“能力必须隔离”两条成立，所以这个系统值得拆。

### 完整示例材料

以下文件全部属于本篇示例，内容在此完整列出。请在一个空练习目录中创建；`路线图.md` 只作为枚举样本，因此可以为空。`explore-agent` 是 Flowing 提供的只读内置子智能体。

```console
touch notes/路线图.md
export DEEPSEEK_API_KEY=sk-your-key-here
uv run flowing repl .
```

**`providers.yaml`**

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

**`models.yaml`**

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

**`model-tags.yaml`**

```yaml
tags:
  default: deepseek-flash
```

**`main.py`**

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

**`root.fya`**

```yaml
description: 只读任务编排者：将查看、检查和读取任务委派给探索智能体。
model_tag: default
tools:
  - subagent-invoke
subagents:
  - builtin::explore-agent
---
$system_prompt:
你是任务编排者。本练习目录的绝对路径是 {{ env.PWD }}。
凡是查看、检查或读取类任务，一律调用 subagent-invoke 委派给 explore-agent，
并在任务中给出目标的绝对路径。收到结果后如实汇报，不得自行读取或编造内容。
explore-agent 只有只读能力；若用户要求写入文件，应报告能力边界，不得声称已写入。
```

**`notes/使用说明.md`**

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

完成文件创建后，在交互提示符中输入上面的完整“输入”块。输出中的自然语言由模型生成；固定验收点是目录枚举结果与只读 Agent 对写入请求的拒绝。

### 判据

拆分的充要条件可以压成一句话：**职责异构或权限必须隔离，且
收益大于上述成本**。展开为三个具体问题：

1. 不同请求类型的工具表差异是否显著？（不显著 → 单 Agent 换
   提示词即可）
2. 是否存在“绝不该由同一主体持有”的能力组合？（存在 → 必须拆）
3. 任务是否可并行到需要多回路？（是 → 考虑程序化并行，未必
   需要常驻协作结构）

三个问题都不满足时，多智能体是过度设计。

## 常见误区

1. **为拆而拆**。多智能体不提升单任务质量；错误派单只是更快地
   产生错误结果；
2. **忽略通信成本**。每个下属的回执都要过编排者的上下文；链路
   越深，重复携带的历史越多；
3. **把并行当默认收益**。同构批量任务用程序化并行即可（第 5-2
   篇），常驻协作结构解决的是异构分工。

## 练习

1. 对一个“客服系统”列出请求类型，判断：哪些类型工具表差异
   显著、哪些能力组合必须隔离；
2. 给出该系统的两种方案（单 Agent 多提示词 / 编排者 + 三个下属），
   各估一项成本与一项收益，说明取舍；
3. 用本篇判据评审本篇示例工程：写出三个问题的答案，指出哪条
   判据在这个系统里最弱。

## 小结

1. 单 Agent 的扩展限制：上下文、权限、吞吐；前两类是结构性的；
2. 拆分的成本：通信、一致性、调试——结构的固有属性；
3. 判据：职责异构或权限必须隔离，且收益覆盖成本；
4. 判据不满足时，多智能体是过度设计。
