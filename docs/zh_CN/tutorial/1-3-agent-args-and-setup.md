# 1-3 · 参数与 setup()

## 前置阅读

[0-1 第一个智能体](0-1-hello-agent.md)（`--key value` 经 `launch` 透传
`main(**kwargs)`；0-1 用运行期赋值 `root.user_name = ...`）。本篇以内联给出
完整入口、Agent 声明、提示词、用户输入和示例输出；请在自行创建的
项目目录中保存后运行。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| `args:` 声明 | `.fya` 头部的实例化参数声明（JSON Schema 展开式或其糖），同时是 LLM 可见的参数 schema 与执行校验的来源 |
| `setup()` | Agent 就绪时自动调用的装配方法：`async def setup(self, ...)` 形参对应 args 声明 |
| 创建管线 | Agent 实例的唯一诞生路径：`before_create` → `setup(**kwargs)` → 检查 → 注册 → 启动工作循环（时序细节见 4-7） |

## 目标

会写 Agent 参数（args）与 `setup()` 的表象写法——只讲“长什么样、怎么写”。
本篇是 1-4（钩子篇）与 1-5（provide-inject 篇）的直接前置：下一篇的
拒绝 handler 就挂在同一个 `setup()` 里。

## 正文

### args 声明：两种写法

`.fya` 头部的 `args:` 块声明参数名 / 类型 / 默认值。两种写法等价出现在
同一声明里：

```yaml
args:
  user_name: str        # 糖：裸类型字符串 → 必填参数
  locale:               # 完整写法：JSON Schema 关键字多行展开
    type: string
    default: zh
    description: 回复语言（zh / en）
```

保存下方完整 Agent 声明为 `root.fya`。它包含 args、系统提示词和 `setup()`；
系统提示词会在求值后显示当前用户、回复语言和时区：

```yaml
description: 参数演示助手：args 声明 + setup() 典型写法。
model_tag: default
args:
  user_name: str
  locale:
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
    self.user_name = user_name
    self.locale = locale
    self.ask_count = 0
    self.provide("locale", locale)
    self.timezone = self.inject("timezone")
```

必填性恒由“有无 `default`”派生：`user_name` 没有默认值 → 必填；
`locale` 有默认值 → 可选。args 声明**声明即模型**：它同时是 LLM 可见的
参数 schema（子智能体 catalog 渲染用）与执行层校验的来源——缺必填参数
在创建期就报错（主线示例的演示 1），不会留到运行期。

### setup() 的表象：典型四件事

`setup()` 的形参对应 args 声明（名字一致、带默认值的也要对应），Agent
就绪时由创建管线自动调用，每个实例恰好一次。典型写法四件事：

```python
async def setup(self, user_name: str, locale: str = "zh"):
    self.user_name = user_name               # ① 参数存 self（模板 {{ user_name }} 的取值来源）
    self.locale = locale                     # ② 参数存 self（1-5 的多语言模板用）
    self.ask_count = 0                       # ③ 计数器初始化（1-4 的钩子会用到）
    self.provide("locale", locale)           # ④ 把值挂上 provide 链（链式语义见 1-5）
    self.timezone = self.inject("timezone")  # ⑤ 取上层 provide 的值（main() 在 mount 前提供）
```

（④⑤ 与前三件的并列编号是排版需要——大纲的“四件事”即“存 self、
初始化、provide、inject”。）

与 0-1 呼应：0-1 的运行时 `root.user_name = ...` 是运行期直接赋值，
模板同样生效；args + setup 是“声明 + 初始化”的正路——值在创建期就
就位，且进入持久化身份信息，恢复时原样透传（4-1）。

约束：`setup()` 要轻量（网络/重 IO 放工具里）；`**kwargs` 必须 JSON 可
序列化。手写子类（`args_model` + `setup`）与 `.fya` 声明完全等价，
5-1 展开。

### main() 的转发

CLI 的 `--key value` 经 `launch` 原样透传 `main(**kwargs)`；`main()` 自行
决定哪些参数交给 `mount(**kwargs)` → 创建管线 → `setup(**args)`：

```python
from flowing import Runtime

async def main(user_name: str | None = None, locale: str = "zh") -> Runtime:
    runtime = Runtime()
    runtime.provide("timezone", "Asia/Shanghai")   # mount 前 provide
    kwargs: dict = {}
    if user_name is not None:
        kwargs["user_name"] = user_name
    if locale != "zh":
        kwargs["locale"] = locale
    await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
```

## 本篇不覆盖

- 创建管线与装配时点的完整原理（`before_create` 钩子等）——2-4 / 4-7；
- provide/inject 的链式上溯语义与敏感信息边界——1-5；
- 参数的类型桥接（`schema_to_model`）与覆写端规则——4-4 / 5-1。

## 主线示例

**演示 1：缺必填参数 → 创建期 fail-fast**。输入中未传 `user_name`：

```console
$ DEEPSEEK_API_KEY="<your API key>" uv run flowing repl .
launch failed: RootAgent.setup() missing 1 required positional argument: 'user_name'
```

进程直接退出（退出码 1）——必填参数缺失是**创建期错误**，不是运行期
surprise。

**演示 2：参数经 setup 各就各位**。输入传入 `user_name` 和 `locale`：

```console
$ DEEPSEEK_API_KEY="<your API key>" uv run flowing repl . --user_name 小红 --locale en
(agent-main)>>> 请用中文一句话汇报：当前用户是谁、locale 是什么、时区是什么。
[thinking]（推理痕迹已省略）
当前用户是小红，locale 为 en，时区为 Asia/Shanghai。
(agent-main)>>> /exit
```

读这段会话：`小红` 来自 `--user_name`（args → setup ① → 模板
`{{ user_name }}`）；`en` 来自 `--locale`；`Asia/Shanghai` 来自
`main()` 的 `runtime.provide("timezone", ...)` → setup ⑤ 的
`inject`。模板里的三个占位符各有来路。虽然系统提示词把回复语言设为 `en`，
用户输入明确要求中文，因此示例最终输出为中文；推理痕迹已省略。1-5 会用
`{% if %}` 把语言切换写成确定行为。

`main.py`、`root.fya`、两条演示输入和对应的示例输出都已内联；
把各代码块保存为对应的相对文件名即可重建该示例。推理内容只以省略标记表示。

## 小结

1. `args:` 两种写法（糖 / JSON Schema 展开），必填性由 default 有无派生；
2. 缺必填参数创建期 fail-fast，args 声明即校验模型；
3. `setup()` 典型四件事：存 self、初始化、provide、inject，每实例恰好一次；
4. CLI 参数经 `main(**kwargs)` 由 `main()` 转发给 `mount(**kwargs)`。
