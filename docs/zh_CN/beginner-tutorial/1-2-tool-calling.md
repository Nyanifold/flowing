# 1-2 · 工具调用：意图与执行的分离

> 复现条件：Python 3.13+、uv、可用的 Flowing CLI、DeepSeek API key，以及终端网络连接。
> 本篇末尾内联完整配置、提示词、数据、输入与示例输出；在新建空目录中按标注文件名保存后即可运行。

> 前置：第 1-1 篇“智能体运行时”

## 这篇讲什么

工具调用（Tool Calling）的核心设计：模型不直接执行动作，而是
输出结构化的调用意图，由框架执行。本篇讲这一分离的结构、收益
与一轮调用的完整过程。

## 背景知识

行业通用的 function calling 约定：请求中附带工具目录——每个
工具的名称、自然语言描述、参数定义（JSON Schema 格式）；模型
的响应中可以包含一个或多个结构化调用意图；应用侧（框架）执行
后，把结果追加进历史并再次调用模型。

这个约定解决的是“模型只会输出文本”与“应用需要模型驱动动作”
之间的矛盾：不靠解析模型的自然语言输出来猜测动作（脆弱、易被
提示注入利用、无法审计），而是让模型在受约束的输出通道里给出
意图。

## 核心概念

### 一轮调用的三段结构

以“查订单”为例：

```python
# ① 调用前：工具目录随请求发送
tools = [{"name": "query-order",
          "description": "按订单号查询订单状态",
          "parameters": {"type": "object",
                         "properties": {"order_id": {"type": "string"}},
                         "required": ["order_id"]}}]

# ② 模型响应：结构化调用意图
{"name": "query-order", "arguments": {"order_id": "4521"}}

# ③ 应用侧执行并把结果回喂为消息
result = query_order(order_id="4521")
messages.append(tool_result(result))
text = llm(messages)
```

三段各自的责任：目录的编写与下发在应用侧；意图的生成在模型；
执行与回喂在框架。模型看到的是“我提议调用 X”，用户看到的是
“系统完成了 X”——中间的校验、审批、日志都由框架在执行段完成。

按文末的完整示例启动会话，输入“notes/ 目录里有哪些文件？请概括
notes/使用说明.md 的主要内容”。下面说明三段结构；
模型措辞可能变化，所有工具输入和结果均在文末完整列出：

逐行对应三段结构：两行 `[tool_call]` 是第②段——模型输出的
结构化意图；两段 `[tool:completed]` 是第③段——框架执行后回喂的
结果；最后的文字是模型基于结果生成的回答。第①段发生在请求发出
前：本例的工具目录只有 glob 与 read 两条，由 Agent 声明的工具表决定。

### 执行权在框架的收益

执行权归属框架一侧，直接带来三项能力：

1. **执行前拦截**：在动作落地前检查、改写或拒绝（审批、安全
   策略的挂点）；
2. **参数校验**：按 schema 校验模型填入的参数，不合法的调用
   不进入执行；
3. **审计与计费**：每次调用的工具名、参数、结果、耗时可完整
   记录。

反过来，任何“让模型直接执行”的设计（例如在提示词里教模型写
代码再由应用 eval）都会同时失去这三项能力。

执行前的拦截在概念上的形态：

```python
def before_execute(call):                     # 框架在执行前必经此点
    if is_dangerous(call) and not approved(call):
        return Deny(reason="需要人工确认")     # 拒绝：不进入执行
    return call                               # 放行：进入执行
```

上面的两段 `[tool:completed]` 同时是第 3 项能力的证据：
每次调用的名称与结果全文都留在会话记录里，可以事后审计。

### 工具描述是合同的一部分

工具目录中的描述字段面向模型阅读。它同时承担两个职能：帮助
模型判断“什么时候该用这个工具”，以及约束参数填写的语义。
描述含糊的直接后果是路由错误与参数错误。工具目录因此应按
接口设计的标准维护：职责单一、说明使用场景、数量从最小集起步。

## 常见误区

1. **把模型输出当可执行代码**。文本输出通道不可信；动作必须走
   结构化意图 + 框架执行的路径；
2. **工具描述随手写**。描述质量决定路由与参数填写的准确率，
   是工具表维护的重点；
3. **参数 schema 从宽**。schema 是模型与执行层之间的合同，
   应明确类型、约束与必填性，给两端的校验提供依据。

## 练习

1. 用伪代码写出“查订单”的完整三段序列，标出每段的责任方；
2. 为一个“发送邮件”工具撰写描述与参数 schema，要求描述说清
   适用场景，schema 约束收件人格式与正文长度；
3. 在下方完整示例中换一个提问（例如只问路线图文件的内容），
   从示例输出中找出模型的意图行与框架的回执行，指出它们各自
   属于三段结构中的哪一段。

## 完整示例：工具表、提示词、输入与结果

本例在一个新建空目录中运行。创建 `notes/` 子目录，将 `...` 替换为
自己的 API key 并仅设置在环境变量中，再保存以下文件。所有路径均
相对于当前目录，不需要查找或复制其他文件。

`pyproject.toml`：

```toml
[project]
name = "flowing-chapter-1-2"
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

`root.fya`（完整提示词与可见工具目录）：

```yaml
description: 项目问答助手：能查看目录、读取文件并总结。
model_tag: default
tools:
  - read
  - glob
---
$system_prompt:
你是项目问答助手。本项目的根目录绝对路径是 {{ env.PWD }}（调用文件工具时一律使用该绝对路径，不要猜其它目录）。当问题涉及 notes/ 时，必须先用 glob 查看 notes/ 中的文件，再用 read 读取相关文件，然后根据读到的内容回答；不要凭印象编造。回答控制在五句话以内。
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

本例使用的全部笔记数据：

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

输入：

```text
notes/ 目录里有哪些文件？请概括 notes/使用说明.md 的主要内容。
/exit
```

运行 `uv run flowing repl .` 后，示例交互如下。`<project-root>` 表示
当前工作目录；模型表述和枚举顺序可能变化：

```console
(agent-main)>>>notes/ 目录里有哪些文件？请概括 notes/使用说明.md 的主要内容。
[tool_call] glob {"pattern": "notes/**/*", "path": "<project-root>"}
[tool_call] read {"path": "<project-root>/notes/使用说明.md"}
[tool:completed] glob -> notes/使用说明.md
notes/路线图.md
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
项目根目录下有 `notes/使用说明.md` 和 `notes/路线图.md`。使用说明要求 Python 3.13 或更高版本，列出安装依赖与运行测试的命令，并要求凭证仅通过环境变量保存。
(agent-main)>>>/exit
```

## 小结

1. 工具调用分离意图与执行：模型提议、框架执行；
2. 三段结构：目录下发 → 意图生成 → 执行回喂；
3. 执行权在框架带来拦截、校验、审计三项能力；
4. 工具描述与 schema 是模型与执行层之间的合同。
