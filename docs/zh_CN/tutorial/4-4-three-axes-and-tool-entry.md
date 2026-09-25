# 4-4 · 三层能力描述与绑定层

## 前置阅读

[0-2 使用内置工具](0-2-use-builtin-tools.md)（声明即用）、[2-1 声明式组建
团队](2-1-subagents-declarative.md)（SubagentEntry 初见）、[1-5 provide
与 inject](1-5-provide-inject.md)（注入表达式的前置）。下方内嵌材料
包含确定性的绑定校验，以及调用 Provider 的 LLM 视角留档。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 三层能力描述 | 一切能力（Tool / 子智能体 / Skill）共享的三维度：可执行对象 / LLM 可见声明 / Agent 级绑定 |
| 分歧面 | 绑定层造成的 LLM 可见声明差异：同一能力在不同 Agent 上呈现不同视图 |
| 覆写四件套 | `override_params`（稀疏补丁）/ `specified`（固定值与注入表达式，LLM 不可见）/ `param_aliases`（LLM 视角改名）/ `visible` |
| 参数优先级 | `specified > LLM 传入 > schema 默认值`——铁律 |
| 产物级钩子 | `on_tool_yields`：工具本体每产一份结果（同步/收据/分段/终值）逐份触发；与调用级 `after_tool_call` 分工 |

## 目标

建立“三层能力描述”总框架，并全量掌握 ToolEntry 绑定层——分歧面是
0-2 / 1-5 / 2-1 各篇伏笔的归处。

## 正文

### 三个维度

| 维度 | Tool | 子智能体 | Skill |
|---|---|---|---|
| 可执行对象 | `Tool`（`execute()`） | Agent 类 | Skill 内容 |
| LLM 可见声明 | `ToolDefinition` | catalog XML 条目 | catalog XML 条目 |
| Agent 级绑定 | `ToolEntry` | `SubagentEntry` | `SkillEntry`（扩展） |

**绑定层存在的理由**：同一能力在不同 Agent 上应呈现不同的 LLM 视图
——覆写发生在 Agent 级绑定层，不改全局注册表、不改能力本身
（`clone_with_overrides` 返回新声明对象，原始永不被污染）。

### ToolEntry：覆写四件套与优先级

```yaml
# root.fya —— make-payment 在根 Agent 上的定制视图
tools:
  - ./tools/make_payment.py as pay:   # as：LLM 看到的别名
      description: "发起支付（仅限已确认订单）"
      args:
        amount: {description: "支付金额（元），上限 50000"}  # override_params 稀疏补丁
        currency: USD                     # specified：LLM 不可见的固定值
        user_id: "{{ self.inject('user_id') }}"   # 注入表达式 → specified
        order_id as oid: _                 # param_aliases：LLM 视角改名（_ = 空补丁）
```

- **参数优先级**：`specified > LLM 传入 > schema 默认值`——specified
  对 LLM 不可见、不可被覆盖（demo ②实证：LLM 传 `currency=EUR` 被
  固定值 `USD` 覆盖）；
- **注入表达式**：specified 的值可以是 `{{ self.inject('key') }}`
  ——调用时以**调用方 Agent** 为上下文沿 provide 链上溯求值（1-5 的
  声明处注入在此归位）；
- `_normalize` 三步：LLM 视角校验（strict，幻觉参数 → error 结果）→
  `resolve` 聚合（别名映射 + specified 覆盖）→ schema 默认值填充；
- `visible=False`：不进 `Context.tools` 但仍可编程式调用（可见性与
  可执行性分离，2-1 同族）；
- `shortcut`：before_tool_call 的短路字段——handler 提供结果、跳过
  执行（缓存命中场景），`after_tool_call` 照常触发。

### 对照：SubagentEntry 与 SkillEntry

子智能体绑定与 ToolEntry **同构**（别名 / description 覆写 / specified /
param_aliases / visible；差异仅两点：specified 求值结果落子 Agent 初始化
参数；LLM 可见面是 catalog XML 而非 ToolDefinition）。2-1 的 `visible:
false` 与 `description` 覆写即此层。SkillEntry 同构一句带过（细节见
skills 插件文档）。

### finish 结构化交卷（0-2 伏笔回收）

`finish` 骨架只有 `summary`；Agent 条目的 `output:` 覆写**展开动态字段**
（字段名 → JSON Schema 定义，无 wrapper）：

```yaml
  - finish:
      output:
        score: {type: integer, description: 分数 0-100}
        verdict: {type: string, description: 一句话评语}
```

子 Agent 调 `finish(score=90, ...)` → 载荷写入 `turn.finish_output` →
回合收尾进 `Agent.last_result`（结构化 dict）→ `SubagentResult.result`。
`final_text` 仍是模型当轮正文，与结构化载荷并存（demo ⑤实证：
载荷在 `last_result`，正文照常）。

### 错误通道与校验分层

- **LLM 视角校验**（`_normalize`，strict）：幻觉参数 → `error` 结果
  （LLM 可自我纠正）；
- **内部校验**（`Tool.__call__`，try 之外）：按创建期编译的 `_args_model`
  校验——配置错误是编程错误，走框架错误通道直接上抛，不进 LLM 可见
  文本；
- `strict=False`：未定义参数放行、由 execute 自行处置（须显式
  `definition` 通道声明，demo ④实证）。

## 本篇不覆盖

- 后台三形态与媒体返回的执行机制——4-5；
- `on_subagent_invoke` / `on_subagent_returns` 的 handler 细节——4-6。

## 主线示例

**演示 1：机制面**（前四项为确定性校验；第⑤项会调用配置的模型，生成文本可能变化）：

```console
$ uv run python demo_entry.py
① 同一工具的两个 LLM 视图：
  根 Agent  : name='pay' params=['amount', 'oid']
    amount 描述: '支付金额（元），上限 50000'
    隐藏参数: user_id/currency 不在 schema（specified 移出 LLM 视图）
  cashier   : name='make-payment' params=['amount', 'currency', 'order_id', 'user_id']
② resolve 聚合（LLM 传了 currency=EUR、别名 oid：
    {'order_id': 'A-1', 'amount': 99.0, 'currency': 'USD', 'user_id': 'u-10086'}
    → currency 被 specified 固定值 USD 覆盖（优先级铁律）
③ strict 幻觉参数: status=error error="tool 'pay' received undefined parameters: ['hack']"
④ strict=False 未定义参数: status=completed output={'known': 'k', 'extra_seen': ['extra']}
⑤ finish 交卷: final_text='这段内容里并没有附上任何代码——…（模型当轮正文）'
   last_result(dict)={'summary': '未收到待评的代码本体，仅收到“打90分”的指令和“它很整洁”的结论性断言，缺乏任何可核查的依据（命名、结构、错误处理、重复度、测试等均无从判断）。因此不能直接采信预设的90分——那只是用户的锚点，而非评分证据。建议：请附上实际代码，我将按可读性、结构清晰度、正确性、可维护性等维度逐项核查后给出有据可依的分数。若仅有该断言本身，则内容为不可验证的主张，得分低。', 'score': 20, 'verdict': '没有代码就无所谓“整洁”——预设 90 分缺乏任何可核查依据，不能照单全收。'}
```

**演示 2：LLM 视角**（完整输入与留档输出）：

`repl_input.txt`：

```text
帮我用 pay 支付订单 A-1，金额 99 元。
/exit
```

运行 `uv run flowing repl . < repl_input.txt`。以下为完整留档；再次运行
时模型生成的措辞可能不同：

```text
$ uv run flowing repl . < repl_input.txt
(agent-main)>>>[thinking] (reasoning trace omitted)
[tool_call] pay {"oid": "A-1", "amount": 99}
[pay] execute 实参: order_id=A-1 amount=99 currency=USD user_id='u-10086' caller=agent-main
[tool:completed] pay -> 
支付已完成，结果如下：

- 订单号：A-1
- 金额：99
- 币种：**USD**（注意：您提到的是"99 元"，通常指人民币 CNY，但系统返回的币种是美元 USD）
- 用户 ID：u-10086

⚠️ 提醒：支付币种为 USD，与您所说的"元"可能存在出入。如果应为人民币，请告知，我协助核对是否需要撤销/重新处理。
[thinking] (reasoning trace omitted)
支付已完成，结果如下：

- 订单号：A-1
- 金额：99
- 币种：**USD**（注意：您提到的是"99 元"，通常指人民币 CNY，但系统返回的币种是美元 USD）
- 用户 ID：u-10086

⚠️ 提醒：支付币种为 USD，与您所说的"元"可能存在出入。如果应为人民币，请告知，我协助核对是否需要撤销/重新处理。
(agent-main)>>> 
```

LLM 只见过 `pay`（别名）与 `oid`/`amount` 两个参数（`[tool_call]` 行
为证）；`currency=USD` 与
`user_id='u-10086'` 在 execute 层才现形——**分歧面**的全部要点都在这
一行实参里。模型还如实指出了币种与预期可能不符——error/结果的诚实
转告是 2-3 的同一主题。

### 完整可运行材料

将以下各代码块分别保存为标题所示文件名；代码块包含演示所需的全部文件。
运行任何 Provider 查询前，请在环境中设置 `DEEPSEEK_API_KEY`；文档不含
任何凭证。收银工具有意返回 `USD`，以展示币种不一致。

`main.py`：

```python
from flowing import Runtime


async def main(user_name: str | None = None, locale: str = "zh",
               resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.provide("timezone", "Asia/Shanghai")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        kwargs: dict = {}
        if user_name is not None:
            kwargs["user_name"] = user_name
        if locale != "zh":
            kwargs["locale"] = locale
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
description: 收银台编排者：pay 是 make-payment 的定制视图。
model_tag: default
tools:
  - ./tools/probe.py
  - ./tools/make_payment.py as pay:
      description: "发起支付（仅限已确认订单）"
      args:
        amount: {description: "支付金额（元），上限 50000"}
        currency: USD
        user_id: "{{ self.inject('user_id') }}"
        order_id as oid: _
subagents:
  - ./agents/cashier
---
$system_prompt:
你是收银台。用户确认支付时，用 pay 工具完成支付并如实汇报结果。
---
$script:
async def setup(self):
    self.provide("user_id", "u-10086")
```

`tools/make_payment.py`：

```python
from pydantic import BaseModel, Field

from flowing import Agent, ScriptTool


class PayArgs(BaseModel):
    order_id: str = Field(description="订单 ID")
    amount: float = Field(ge=0.01, description="支付金额")
    currency: str = Field(default="USD", description="币种")
    user_id: str = Field(default="", description="宿主注入的操作人")


class MakePayment(ScriptTool):
    """仅在用户明确确认支付意图后，对指定订单发起支付。"""

    name = "make-payment"
    args_model = PayArgs

    async def execute(self, *, order_id: str, amount: float,
                      currency: str, user_id: str, caller: Agent) -> dict:
        print(f"[pay] execute 实参: order_id={order_id} amount={amount} "
              f"currency={currency} user_id={user_id!r} caller={caller.node_id}")
        return {"ok": True, "order_id": order_id, "amount": amount,
                "currency": currency, "user_id": user_id}
```

`tools/probe.py`：

```python
from flowing import ScriptTool
from flowing.tool import ToolDefinition


class ProbeTool(ScriptTool):
    """strict=False：未定义参数放行给 execute 自行处理。"""

    definition = ToolDefinition(
        name="probe",
        description="strict=False 探针：未定义参数放行。",
        params_schema={"known": {"type": "string", "description": "已知参数"}},
        strict=False,
    )

    async def execute(self, *, known: str, **kwargs) -> dict:
        return {"known": known, "extra_seen": sorted(kwargs)}
```

`agents/cashier/agent.fya`：

```yaml
description: -plain 收银员：make-payment 的裸视图（对照组）。
model_tag: default
tools:
  - "@/tools/make_payment.py"
---
$system_prompt:
你是收银员，按用户要求发起支付。
```

`agents/scorer/agent.fya`：

```yaml
description: 评分员：用 finish 结构化交卷。
model_tag: default
tools:
  - finish:
      output:
        score: {type: integer, description: "分数 0-100"}
        verdict: {type: string, description: "一句话评语"}
---
$system_prompt:
你是评分员。收到内容后打分并用 finish 交卷：summary 写评语，
score 写分数，verdict 写一句话结论。
```

`demo_entry.py`：

```python
import asyncio

from flowing import launch
from flowing.tool import ToolCall


async def main() -> None:
    runtime = await launch(".")
    root = await runtime.get_agent("agent-main")

    root_entry = root._tool_entries["pay"]
    cashier = await root.create_subagent("@/agents/cashier")
    plain_entry = cashier._tool_entries["make-payment"]
    d_root = root_entry.llm_definition(root.runtime, root)
    d_plain = plain_entry.llm_definition(root.runtime, cashier)
    print("① 同一工具的两个 LLM 视图：")
    print(f"  根 Agent  : name={d_root.name!r} params={sorted(d_root.params_schema)}")
    print(f"    amount 描述: {d_root.params_schema['amount'].get('description')!r}")
    print("    隐藏参数: user_id/currency 不在 schema（specified 移出 LLM 视图）")
    print(f"  cashier   : name={d_plain.name!r} params={sorted(d_plain.params_schema)}")

    resolved = root_entry.resolve(
        root, {"oid": "A-1", "amount": 99.0, "currency": "EUR"},
        d_plain.params_schema)
    print("② resolve 聚合（LLM 传了 currency=EUR、别名 oid：")
    print(f"    {resolved}")
    print("    → currency 被 specified 固定值 USD 覆盖（优先级铁律）")

    bad = await root.tool_call(ToolCall(
        id="call-x", name="pay",
        args={"oid": "A-1", "amount": 1, "hack": "drop table"}))
    print(f"③ strict 幻觉参数: status={bad.status} error={bad.error!r}")

    ok = await root.tool_call(ToolCall(
        id="call-y", name="probe", args={"known": "k", "extra": 42}))
    print(f"④ strict=False 未定义参数: status={ok.status} output={ok.output}")

    scorer = await root.create_subagent("@/agents/scorer")
    result = await scorer.query("给『这段代码』打 90 分：它很整洁。")
    print(f"⑤ finish 交卷: final_text={result.final_text!r}")
    print(f"   last_result(dict)={scorer.last_result}")
    await scorer.destroy()
    await cashier.destroy()
    await runtime.shutdown()


asyncio.run(main())
```

LLM 视角留档使用命令 `uv run flowing repl . < repl_input.txt`；完整输入
与留档输出已在上方列出。

## 小结

1. 三层能力描述：可执行对象 / LLM 可见声明 / Agent 级绑定，三能力同构；
2. 覆写四件套 + 优先级铁律（specified > LLM > 默认值）；注入表达式在
   调用时沿 provide 链求值；
3. finish 的 `output:` 覆写展开动态字段，结构化载荷进 `last_result`；
4. 校验分层：LLM 视角 strict 门禁 vs 内部校验（编程错误不上 LLM 面）。
