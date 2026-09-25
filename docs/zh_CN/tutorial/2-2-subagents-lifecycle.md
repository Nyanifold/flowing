# 2-2 · 子智能体的创建与续接

## 前置阅读

[2-1 声明式组建团队](2-1-subagents-declarative.md)（类型绑定与 catalog）。
本篇列出完整的父 Agent 与 assistant 声明、提示词、输入和代表性输出。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 创建-续接 | 子智能体实例生命周期的固定两分：`agent_type=`（或 catalog 唤起）新建实例；`resume=`（或 `name=` 登记后的语义名）续接既有实例 |
| `name=` | 唤起时给新实例登记的语义名（进亲代 Agent 的 `child_ids` 表），之后按名续接 |
| `child_ids` | 亲代 Agent 持有的“语义名 → agent_id”翻译表；记忆续接的寻址依据 |
| `SubagentResult` | 唤起返回结构：`result`（最终产出：finish 结构化 dict 或最后回复文本）与 `subagent_status`（回合结局四态）**分离** |

## 目标

掌握子智能体实例的两件事：**创建**与**续接**——并用“记忆还在”验证
续接的是同一个实例。

## 正文

### 唤起两段式

`invoke_subagent` 分两段：

1. **准备段**（同步失败上抛）：解析类型、聚合参数（specified / 别名映射）、
   `on_subagent_invoke` 钩子——失败直接抛给调用方，实例未创建；
2. **运行段**：实例创建（或续接）→ 跑回合 → 构造 `SubagentResult` →
   `on_subagent_returns` 钩子（先于交付）→ 交付。运行段的回合结局是
   1-1 的 `TurnResult` 四态，经 `subagent_status` 透传。

LLM 路径与此同构：`subagent-invoke(agent_type=..., name=..., prompt=...)`
创建，`subagent-invoke(resume=..., prompt=...)` 续接（`agent_type` 与
`resume` 互斥且至少其一）。

### 创建：name= 登记语义名

新建时给 `name=`，实例 id 登记进亲代的 `child_ids`——语义名只存在于
唤起方这张表里，子实例自己不持名字。之后 `resume="memo"` 按名寻址：
**同一个实例、同一棵树、同一份记忆**（主线示例实证）。

### SubagentResult：内容与结局分离

```python
result = await parent.invoke_subagent("assistant", prompt="记住 42", name="memo")
result.result            # 最终产出：finish 结构化 dict > 最后回复文本 > None
result.subagent_status   # "completed" / "blocked" / "error" / "cancelled"
result.subagent_id       # 实例 id（需要活实例时经 runtime.get_agent 现场恢复）
```

子智能体被取消时已产出的内容照返（`result`），取消事实只看
`subagent_status`——内容不被“已取消”标注污染。这是它与 `TurnResult`
一脉相承的设计。

## 本篇不覆盖

- `on_subagent_invoke` / `on_subagent_returns` 的 handler 细节——4-6；
- 遗忘三档（destroy / archive / 物理删除）与智能体池——6-3；
- 程序化并行唤起（`create_subagent` + `asyncio.gather`）——2-4。

## 主线示例

repl 两问：先让 assistant 记住数字（命名唤起），再追问（续接）。第二问
的用户输入里**不含数字本身**——若答复中出现 42，就来自续接实例的上下文：

```console
$ uv run flowing repl .
(agent-main)>>> 让 assistant 记住数字 42。
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "assistant", "name": "memo", "prompt": "请记住这个内容：数字 42。记住后请复述一遍以确认你已记住。"}
[tool:completed] subagent-invoke ->
已完成，assistant 已记住数字 42，它的确认回复是：
> 我记住了：数字 42。
(agent-main)>>> 问 memo：我之前让你记的数字是几？
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"resume": "memo", "prompt": "我之前让你记的数字是几？"}
[tool:completed] subagent-invoke ->
memo 的回答（原样转达）：
> 你之前让我记的数字是 42。
(agent-main)>>> /exit
```

读这段会话：第一次 `subagent-invoke` 的 `name="memo"` 创建了实例并登记；
第二次 `resume="memo"` 唤起**同一个实例**——两次调用的参数差异在留档
里直接可见；它的消息树里留着第一次的
“记住 42”，所以能准确复述。编排提示词里“除非用户明确要求新实例，
不要新建”是路由纪律，记忆连续性由 `resume` 机制保证，不靠模型自觉。

## 完整示例材料

在已安装 Flowing 的项目中按相对文件名创建以下内容，并在环境变量中设置
`DEEPSEEK_API_KEY`。assistant 的确切措辞由模型生成；实例名与续接提示保持固定。

`main.py`：

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
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
description: 编排者：经命名唤起与续接验证子智能体记忆。
model_tag: default
tools:
  - subagent-invoke
subagents:
  - ./agents/assistant
---
$system_prompt:
assistant 下属在续接同一实例时会保留记忆。
- 用户要求记住信息时，用 agent_type="assistant" 和 name="memo" 唤起，
  并要求它复述确认；
- 用户追问时，用 resume="memo" 续接同一实例并原样转达回答；除非用户明确
  要求新实例，否则不要创建新实例。
```

`agents/assistant/agent.fya`：

```yaml
description: 记忆助手：记住用户告知的信息，并在被问及时复述。
model_tag: default
---
$system_prompt:
用户告诉你一个信息时，用一句话复述确认；用户问起时，准确复述最近记住的
内容。每次回答控制在一句话以内。
```

完整输入与运行命令：

```text
让 assistant 记住数字 42。
问 memo：我之前让你记的数字是几？
/exit
```

```console
$ uv run flowing repl .
```

预期工具交互：第一次调用使用
`{"agent_type":"assistant","name":"memo"}`，prompt 中包含 42；第二次使用
`{"resume":"memo"}`，问题本身不包含 42。续接后的 assistant 应答出 42；
外层自然语言措辞会随模型变化。

## 小结

1. 唤起两段式：准备段同步失败上抛，运行段四态结局；
2. `name=` 登记语义名（`child_ids`），`resume=` 续接同一实例——记忆连续；
3. `SubagentResult` 内容与结局分离：取消不污染已产出的 `result`；
4. 记忆续接是机制保证（实例寻址），不是模型自觉。
