# 5-1 · .fya 全字段参考与复杂装配

## 前置阅读

[4-9 Parsable 求值体系](4-9-parsable.md)（`.fya` 值的求值语义）、[4-4
三层能力描述与绑定层](4-4-three-axes-and-tool-entry.md)（条目覆写）、
[2-1 声明式组建团队](2-1-subagents-declarative.md)（subagents 条目）。
下方内嵌完整声明式示例、手写等价实现与运行材料。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 具名块 | `---` 分隔的 `$<点分路径>:` 块：system_prompt / description / script / 深层路径块 |
| 深层具名块 | 路径指向资源列表条目内部的块（如 `$subagents.greet.args.style.description:`）——按**别名**寻址 |
| glob 展开 | 资源列表条目支持 glob（`./tools/*.py`）：先显式后 glob、同资源跳过 |
| 编译等价 | `.fya` 编译产物与手写子类生成完全相同的类模型（行为要点级一致） |

## 目标

`.fya` 全字段参考与复杂装配：一个文件看懂字段全集、条目形态、深层
块与编译等价性。

## 正文

### 字段全集（root.fya 活参考）

```yaml
name: root                    # 可选：一致性断言（与推断名不符 → NameMismatchError）
description: …                # 一句话介绍（Parsable）
model_tag: default            # 模型标签（两跳解析，0-1）
args:                         # 实例化参数：糖（user_id: str）与 JSON Schema 展开并存
tools:                        # 工具条目：路径 / 裸名 / glob / as 别名 / 覆写体
subagents:                    # 子智能体条目：同构（2-1 / 4-4）
metadata: …                   # 任意静态键值（框架不解释）
```

`model:` 等旧字段不存在——模型只走 `model_tag`（0-1）。框架不认识的
字段落 `_extra`（扩展经 `__getattr__` 读取）。

### 具名块与深层块

- `$system_prompt:` / `$description:` / `$script:` 是常规块（多行原文）；
- **深层块按别名寻址**：`$subagents.greet.args.style.description:` 指向
  greet 条目的覆写体空槽——“列表段按 EntryRef.alias 匹配”，未命中
  报 `FormatError`；
- 空槽的写法：`style: {}`（显式空补丁）；
- 布局纪律：`---` 后必须是块头（注释行放不进块间，只能进 YAML 头）；
  块内原文含 `#` 行会被当作正文。

### glob 展开与 as

`tools: [./tools/*.py]` 一把声明目录下全部 `.py` 工具（**先显式后
glob、同资源跳过**）；`as` 给条目换 LLM 可见别名。glob 命中经形态过滤
（`__pycache__` 等杂项跳过——写 `*.py` 更稳）。

### $script 与编译等价

`$script` 是 Python 模块段：`@on` 钩子（类上声明，__init__ 阶段注册）、
`setup()`、实例方法皆可。`.fya` 与手写子类**编译等价**（demo ①逐项
比对：description / system_prompt 模板 / args schema 键集全等）。
失败断言是 fail-fast 的一部分：`name` 不符 → `NameMismatchError`；
`@on` 指向未声明钩子点 → `UnknownHookPointError`。

## 本篇不覆盖

- 编译管线内部实现，包括生成文件的防覆盖机制与落盘形态；
- 参数简写到校验 schema 的桥接；
- 运行期求值的完整语义——4-9。

## 主线示例

**演示 1：编译等价 + glob + 负例**（`demo_fya.py`；完整终端输出
内嵌于下文）：

```console
$ uv run python demo_fya.py
== ① 编译产物等价手写子类（逐项比对）==
   description 等价: True
   system_prompt 模板等价: True（块内容带末尾换行，strip 后同）
   args schema 等价（properties 键集）: True
== ② glob 展开与别名条目 ==
   tools（glob 展开）: ['audit', 'payment', 'subagent-invoke']
   subagent 别名 greet: source class='greeter' specified={'tone': 'casual'}
   catalog 视图: name='greet' （tone 已被 specified 移出参数表：'<params>' 中无 tone）
== ③ 失败断言（负例）==
   name 一致性断言: NameMismatchError
   @on 未声明钩子点: UnknownHookPointError: Unknown hook point: '@on markers found no owning hook point: no_such_hook (method _) — hook point name misspelled, or the corresponding plugin/Composable is not enabled in setup()' (not declared)
```

**演示 2：端到端**：传入 `--user_id u-7` 并使用下方内嵌输入。完整留档
展示 `subagent-invoke` 派单、`before_tool_call` 钩子触发，以及用户 ID
进入子智能体。再次运行时，生成的问候语可能不同。

`repl_input.txt`：

```text
让 greet 用俏皮风格问候我，并汇报当前用户。
/exit
```

运行 `uv run flowing repl . --user_id u-7 < repl_input.txt`。完整留档输出
见下文；推理痕迹已省略。

```text
$ uv run flowing repl . --user_id u-7 < repl_input.txt
(agent-main)>>>我来调用 greet 子智能体，并汇报当前用户。
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "greet", "prompt": "请用“俏皮”风格问候当前用户（用户 ID：u-7）。"}
[hook] before_tool_call: subagent-invoke
[tool:completed] subagent-invoke -> 
已完成 ✅

**greet 子智能体（俏皮风格）的问候：**
> 嘿，u-7，你的专属好运已空投，记得笑着签收哦～

**当前用户汇报：**
- 用户 ID：`u-7`

子智能体已按“俏皮”风格完成问候，任务结束。
(agent-main)>>> 
```

### 完整可运行材料

将每个代码块分别保存为标题所示相对文件名。以下是两个演示所需的全部
文件。调用模型前请设置环境变量 `DEEPSEEK_API_KEY`；文档不含凭证或
本机绝对路径。

`main.py`：

```python
from flowing import Runtime


async def main(user_id: str | None = None, resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        kwargs = {"user_id": user_id} if user_id else {}
        await runtime.mount("@/root.fya", agent_id="agent-main", **kwargs)
    return runtime
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

`root.fya`：

```yaml
name: root
description: 复杂装配示例助手：glob 工具 + 别名下属 + 深层块 + $script 钩子。
model_tag: default
args:
  user_id: str
  locale:
    type: string
    default: zh
    description: 回复语言
tools:
  - subagent-invoke
  - ./tools/*.py
subagents:
  - ./agents/greeter as greet:
      description: 问候用子智能体（别名 greet）
      args:
        tone: casual
        style: {}
---
$system_prompt:
你是装配示例助手（{{ locale }} 模式），当前用户 {{ user_id }}。
---
$subagents.greet.args.style.description:
  问候的措辞风格（正式 / 俏皮 / 古风）。
---
$script:
from flowing import on

@on("before_tool_call")
def _(self, tool_call):
    print(f"[hook] before_tool_call: {tool_call.name}")
    return tool_call

async def setup(self, user_id: str, locale: str = "zh"):
    self.user_id = user_id
    self.locale = locale
```

`agents/greeter/agent.fya`：

```yaml
description: 问候用子智能体。
model_tag: default
args:
  tone:
    type: string
    default: neutral
    description: 语气
  style:
    type: string
    default: friendly
    description: 措辞风格
---
$system_prompt:
你是问候助手。用 {{ tone }} 语气，用一句话问候用户。
```

`tools/payment.py`：

```python
from pydantic import BaseModel, Field

from flowing import ScriptTool


class PayArgs(BaseModel):
    order_id: str = Field(description="订单 ID")
    amount: float = Field(ge=0.01, description="金额")


class PaymentTool(ScriptTool):
    """发起示例订单的支付。"""

    name = "payment"
    args_model = PayArgs

    async def execute(self, *, order_id: str, amount: float) -> dict:
        return {"paid": order_id, "amount": amount}
```

`tools/audit.py`：

```python
from flowing import ScriptTool


class AuditTool(ScriptTool):
    """写入一条示例审计记录。"""

    name = "audit"

    async def execute(self, *, action: str) -> dict:
        return {"audited": action}
```

`handwritten.py`：

```python
from pydantic import BaseModel, Field

from flowing import Agent, on
from flowing.parsable import Parsable


class RootArgs(BaseModel):
    user_id: str = Field(description="user_id")
    locale: str = Field(default="zh", description="回复语言")


class RootAgent(Agent):
    description = "复杂装配示例助手：glob 工具 + 别名下属 + 深层块 + $script 钩子。"
    system_prompt = Parsable(
        "你是装配示例助手（{{ locale }} 模式），当前用户 {{ user_id }}。")
    model_tag = "default"
    args_model = RootArgs

    @on("before_tool_call")
    def _log(self, tool_call):
        print(f"[hook] before_tool_call: {tool_call.name}")
        return tool_call

    async def setup(self, user_id: str, locale: str = "zh"):
        self.user_id = user_id
        self.locale = locale
```

`demo_fya.py`：

```python
import asyncio
from pathlib import Path

from flowing import launch
from flowing.errors import NameMismatchError, UnknownHookPointError
from handwritten import RootAgent


async def main() -> None:
    runtime = await launch(".", user_id="u-7")
    agent = await runtime.get_agent("agent-main")

    print("== ① 编译产物等价手写子类（逐项比对）==")
    description_a = getattr(agent.description, "source", agent.description)
    description_b = getattr(RootAgent.description, "source", RootAgent.description)
    print(f"   description 等价: {description_a == description_b}")
    print(f"   system_prompt 模板等价: "
          f"{agent.system_prompt.source.strip() == RootAgent.system_prompt.source.strip()}"
          "（块内容带末尾换行，strip 后同）")
    fya_props = agent.args_model.model_json_schema()["properties"]
    py_props = RootAgent.args_model.model_json_schema()["properties"]
    print(f"   args schema 等价（properties 键集）: "
          f"{sorted(fya_props) == sorted(py_props)}")

    print("== ② glob 展开与别名条目 ==")
    print(f"   tools（glob 展开）: {sorted(agent._tool_entries)}")
    greet = agent._subagent_entries["greet"]
    source_class = greet.name_ori.rsplit("::", 1)[-1]
    specified = {key: value.source for key, value in greet.specified.items()}
    print(f"   subagent 别名 greet: source class={source_class!r} "
          f"specified={specified}")
    view = greet.catalog_view(agent)
    print(f"   catalog 视图: name={view['name']!r} "
          "（tone 已被 specified 移出参数表：'<params>' 中无 tone）")

    print("== ③ 失败断言（负例）==")
    Path("bad-name.fya").write_text(
        "name: wrong-name\nmodel_tag: default\n---\n$system_prompt:\n占位\n",
        encoding="utf-8")
    try:
        runtime.get_agent_class("@/bad-name.fya")
    except NameMismatchError:
        print("   name 一致性断言: NameMismatchError")

    Path("bad-hook.fya").write_text(
        "---\n$system_prompt:\n占位\n---\n$script:\n"
        "from flowing import on\n\n@on('no_such_hook')\n"
        "def _(self, turn):\n    return turn\n\n"
        "async def setup(self):\n    pass\n",
        encoding="utf-8")
    try:
        await runtime.create_agent("@/bad-hook.fya")
    except UnknownHookPointError as exc:
        print(f"   @on 未声明钩子点: UnknownHookPointError: {exc}")
    await runtime.shutdown()


asyncio.run(main())
```

使用 `uv run python demo_fya.py` 运行装配校验。使用
`uv run flowing repl . --user_id u-7 < repl_input.txt` 运行依赖 Provider
的对话；输入与留档输出已内嵌于正文前面。

## 小结

1. `.fya` 头部字段全集：name 断言 / description / model_tag / args（糖
   + 展开）/ tools / subagents / metadata；
2. 具名块按别名寻址深层路径；空槽写 `{}`；块间不放注释行；
3. glob 一把声明 + as 别名；显式优先于 glob；
4. 编译产物与手写子类等价；name / @on 失败断言 fail-fast。
