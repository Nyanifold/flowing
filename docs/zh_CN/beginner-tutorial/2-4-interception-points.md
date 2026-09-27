# 2-4 · 干预点：拦截、审批与错误通道

> 示例运行前提：环境中已安装 Flowing CLI，并已自行设置 `DEEPSEEK_API_KEY`；下文逐项给出运行所需文件全文。请只在为本篇创建的隔离工作目录中运行含 `rm` 的演示。
> 运行：`uv run flowing repl . --user_name 小明`

> 前置：第 1-2 篇“工具调用”

## 这篇讲什么

在运行时的固定时点插入自定义逻辑的机制：拦截与观察的区分、
人工审批的工程形态，以及错误按通道分类的设计。

## 背景知识

框架不可能预见所有应用的策略（审批规则、内容审查、审计要求）。
通用做法是提供**干预点**：执行管线上预定义的、可注册自定义
处理函数的时点。这是成熟生态的通用模式——Web 框架的中间件、
数据库的触发器、操作系统的系统调用拦截同属一族。

干预点按能力分两档：**观察点**（只读通知，返回值被忽略）与
**拦截点**（返回值替换管线中的数据，或抛出阻断信号中止操作）。
区分两档是接口设计的基本功：观察点给了用户安全感（注册任何
函数都不会破坏行为），拦截点给了控制力（改写或拒绝）。

## 核心概念

### 拦截点的三种出口

一个拦截处理函数的标准出口有三个：

下面的伪代码只说明控制流，其中的辅助函数和异常类型是占位符，
不是下方可运行示例的一部分。

```python
def on_before(call):            # 拦截点：工具执行之前
    if is_dangerous(call):
        raise Blocked("需要人工确认")   # 出口一：阻断，操作不执行
    call.args["user"] = current_user()  # 出口二：改写，操作按新数据执行
    return call                          # 出口三：原样放行

def on_after(round):            # 观察点：回合收尾
    audit_log(round)                     # 只读通知；返回值被忽略
```

- **阻断**：操作不执行，调用方收到带原因的拒绝结果；
- **改写**：修改管线中的数据（参数、上下文、结果）后继续；
- **放行**：原样通过。

普通异常不应作为出口使用——它表示处理函数自己出错了，框架
按故障路径处理。

### 完整示例材料

以下代码块标题给出应创建的相对文件名，每个代码块都包含对应文件全文。
示例用两个固定的说明文件构成待检查目录；拦截器会在执行任何含 `rm` 的
命令前阻断它。

#### `root.fya`

```yaml
description: 钩子演示助手：before_tool_call 拦截 + after_turn 观察。
model_tag: default
args:
  user_name: str
tools:
  - bash
---
$system_prompt:
你是演示助手。当前用户：{{ user_name }}。
用户要求删除文件时用 bash 的 rm；安全钩子会在执行前拦截，届时如实说明。
用户要求查看 notes 目录时只运行 bash 命令 ls notes。回答控制在一句话以内。
---
$script:
from flowing import Intercepted, on


@on("after_turn")
def _(self, turn):
    self.ask_count += 1
    print(f"[hook] after_turn: ask_count={self.ask_count} aborted={turn.aborted}")
    return turn


async def setup(self, user_name: str):
    self.user_name = user_name
    self.ask_count = 0
    self.timezone = self.inject("timezone")

    async def _guard(agent, tool_call):
        print(f"[hook] before_tool_call: {tool_call.name} args={tool_call.args}")
        if tool_call.name == "bash" and "rm" in tool_call.args.get("command", ""):
            raise Intercepted("不允许删除：命令包含 rm，已阻断")
        return tool_call
    self.hooks.before_tool_call(_guard)
```

#### `main.py`

```python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

#### `providers.yaml`

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

#### `models.yaml`

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

#### `model-tags.yaml`

```yaml
tags:
  default: deepseek-flash
```

#### `notes/使用说明.md`

```markdown
# 使用说明

这是钩子示例中的无害目录清单项。
```

#### `notes/路线图.md`

```markdown
# 路线图

这是钩子示例中的无害删除目标；拦截器应阻止删除。
```

#### 对话输入与可见输出

在包含以上文件的隔离工作目录中运行 `uv run flowing repl . --user_name 小明`，
依次输入两条请求：

```text
请用 bash 删除 notes/路线图.md。
再用 bash 看一下 notes 目录里有什么。
```

对应的可见交互如下。`<project-root>` 仅表示读者创建的工作目录，不是固定路径：

```console
(agent-main)>>> 请用 bash 删除 notes/路线图.md。
[thinking] (reasoning trace omitted)
[tool_call] bash {"command": "rm \"notes/路线图.md\"", "cwd": "<project-root>"}
[hook] before_tool_call: bash args={'command': 'rm "notes/路线图.md"', 'cwd': '<project-root>'}
[tool:blocked] bash -> 不允许删除：命令包含 rm，已阻断
删除命令在执行前被安全钩子拦截，因此 `notes/路线图.md` 仍然存在。
[hook] after_turn: ask_count=1 aborted=False
(agent-main)>>> 再用 bash 看一下 notes 目录里有什么。
[thinking] (reasoning trace omitted)
[tool_call] bash {"command": "ls notes", "cwd": "<project-root>"}
[hook] before_tool_call: bash args={'command': 'ls notes', 'cwd': '<project-root>'}
[tool:completed] bash -> exit_code: 0
--- stdout ---
使用说明.md
路线图.md
--- stderr ---
notes 目录里有 `使用说明.md` 和 `路线图.md`；删除操作没有执行。
[hook] after_turn: ask_count=2 aborted=False
```

逐行看：第一行是模型的工具调用意图（参数完整可见）；第二行是
拦截点上处理函数的打印——模型想执行 `rm "notes/路线图.md"`；
第三行是阻断出口的结果——工具**没有执行**，
模型收到的是带原因的 `[tool:blocked]`（不是执行失败）；接着模型
向用户说明文件未删除。回合收尾的观察点只计数、不改行为。再输入第二条
指令查看目录：同一个拦截函数打印后放行，`[tool:completed]` 带着输出返回——同一条
拦截点，两种出口。

### 人工审批的工程形态

审批是拦截点的典型应用：处理函数在执行点内异步等待人工确认
（弹窗、IM、工单），按确认结果选择出口。

```python
async def approve(call):
    if call.name in DANGEROUS_TOOLS:
        ok = await ask_human(channel, call.summary())   # 异步等待确认
        if not ok:
            raise Blocked("用户拒绝")
    return call
```

要点：审批逻辑挂在执行前的拦截点；确认过程是普通的异步等待
（不占回合外资源）；拒绝走阻断出口——模型收到的是“被拒绝”
而非“执行失败”，可以据此向用户解释或换方案。上面留档里的
拦截函数离审批只差一步：把“命中规则即阻断”换成“命中规则
则先问人”，机制完全相同。

### 错误按通道分类

Agent 系统同时存在三类“出错”，必须分通道处理：

| 通道 | 形态 | 去向 |
|---|---|---|
| 业务失败 | 工具执行完成但结果失败（校验不过、依赖不可用） | 作为**正常结果**回喂模型，模型自行解释或重试 |
| 程序错误 | 代码 bug、配置错误 | 异常上抛给开发者，不进模型对话 |
| 硬阻断 | 审批拒绝、策略命中 | 带原因的拒绝结果，模型可见可解释 |

混用通道是常见事故源：把业务失败当异常抛出，模型失去自愈机会；
把程序错误回喂给模型，模型对着 bug 日志编造解决方案。上面留档
的 `[tool:blocked]` 行就是第三通道的形态：结果对模型可见，
模型能向用户解释原因。

## 常见误区

1. **把拦截点当观察点用**（或反之）。挂错档位的后果：观察函数
   误改数据破坏行为，或需要改写的逻辑拿不到返回值；
2. **审批用“执行后补救”**。危险动作应先拦后执行，事后回滚在
   Agent 场景基本不可行；
3. **业务失败抛异常**。失败结果是模型决策的依据，不是故障。

## 练习

1. 为“删除文件”“发外部邮件”“执行 SQL”三个工具各设计审批
   策略：哪些需要人工确认、哪些只需记录审计、哪些可直接放行；
2. 写一个拦截处理函数的决策表（工具名 × 参数内容 → 三出口），
   说明每种分支的理由；
3. 修改上方完整 Agent 声明里的拦截函数，把“含 rm 即阻断”改成
   “含 rm 时把命令改写成 ls 再放行”（改写出口），重新运行并
   观察留档中 `[tool:...]` 行的变化。

## 小结

1. 干预点分观察与拦截两档，接口设计必须区分；
2. 拦截出口三件套：阻断、改写、放行；普通异常不算出口；
3. 审批 = 拦截点 + 异步等待人工确认 + 阻断出口；
4. 业务失败、程序错误、硬阻断分通道处理，混用是事故源。
