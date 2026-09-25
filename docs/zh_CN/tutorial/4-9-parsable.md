# 4-9 · Parsable 求值体系

## 前置阅读

[0-1 第一个智能体](0-1-hello-agent.md)（`{{ user_name }}` 初见）、[1-5
provide 与 inject](1-5-provide-inject.md)（多语言模板）、[4-4 三层能力
描述与绑定层](4-4-three-axes-and-tool-entry.md)（注入表达式）。本篇的
完整离线程序与配置内嵌如下。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 求值体系 | Parsable 五形式 + 渲染上下文 + PENDING 哨兵的总称 |
| 五形式 | `LITERAL` / `FILE_REF`（`$./`）/ `EXPRESSION`（完整 `{{ }}`）/ `TEMPLATE`（混合文本）/ `RAW`（`$"..."`）——type 由 source 样式自动推断 |
| 惰性求值 | 构造只存指令、用时才渲染（文件读取/模板渲染全在 resolve 时）——功能正确性前提 |
| PENDING | `.fya` 中 `field: _` 的解析产物：延迟定义承诺哨兵；判定唯一合法方式是 `is` |
| 渲染上下文 | 实例属性摊平 + `env` / `config` / `agent` / `self` 保留名入口 |

## 目标

掌握 Parsable——flowing 的值表达与求值体系：fya 值、args 默认值、
system_prompt 模板、ToolEntry `specified` / 注入表达式共用同一套。

## 正文

### 五种形式（demo ①）

| 形式 | 判定 | resolve 结果 |
|---|---|---|
| `RAW` | `$"..."` 贪婪匹配 | 引号内原文，**跳过全部渲染**（想要字面 `$` / `{{ }}` 用它） |
| `FILE_REF` | `$` 开头的路径 | 现场读文件后渲染（文件修改下次求值即新值） |
| `EXPRESSION` | 恰好一个完整 `{{ }}` | **表达式原生类型**（int/dict…不字符串化）——非 str 结果的唯一来源 |
| `TEMPLATE` | 含 Jinja2 语法的混合文本 | 渲染后的 str |
| `LITERAL` | 其余 | 原样返回 |

### 渲染上下文与保留名（demo ②④）

`resolve(agent)` 的上下文 = 实例属性摊平 + 保留入口：`env`
（os.environ 只读视图）/ `config`（配置链）/ `agent` / `self`（实例
自身）。实例属性**占用了保留名** → `ReservedAttributeError`（fail
fast，不静默遮蔽）。

### `{{ x.resolved }}`：显式求值入口（demo ③）

模板里引用另一个 Parsable 对象：写 `{{ x }}` 只显示它的模板原文，
要渲染结果必须写 `{{ x.resolved }}`——**嵌套渲染的每一步都是显式的**，
不存在无限递归通道。类属性形态的 Parsable 经描述符协议自动绑定实例；
实例属性形态用 `.bind(agent)` 显式绑定。

### 边界（demo ⑤）

- 未绑定 + 无上下文 `resolve()` → `MissingContextError`；
- 注入链未命中 → `MissingProvideError`（1-5 的边界同一通道）；
- `EXPRESSION` 求值异常原样上抛（由回合层的 `on_provider_error`
  错误路径处理）。

### PENDING 哨兵与位置分流（demo ⑥）

`field: _` 解析为 PENDING 单例；位置决定语义：

| 位置 | 语义 |
|---|---|
| 字段位 | **必须兑现的承诺**：setup 后仍为 PENDING → `MissingFieldError`（创建管线检查点） |
| 覆写位（entry args 等） | 空补丁：声明了覆写位、内容为空，从基底回填 |
| 资源列表项 | 禁止：`FormatError`（无别名无法兑现） |

`$"_"`（RAW 形式）是普通字符串，不触发 PENDING。

## 本篇不覆盖

- compiler 侧 fya → 代码生成的细节——5-1；
- 参数声明到校验模型的 schema 桥接；
- watch 与解析值联动的一句带过——4-7 已述。

## 主线示例

`demo_parsable.py` 全离线运行；完整终端留档内嵌于下文：

```console
$ uv run python demo_parsable.py
== ① 五种形式判定（type 自动推断，构造时不求值）==
   RAW        type=RAW          → '含 {{ 花括号 }} 的原文'
   FILE_REF   type=FILE_REF     → '退款政策：七天内无理由退款，超期需审核。'
   EXPRESSION type=EXPRESSION   → 42
   TEMPLATE   type=TEMPLATE     → '你好 小明'
   LITERAL    type=LITERAL      → '普通文本'
== ② 渲染上下文（实例属性摊平 + env / config / self 入口）==
   {{ env.DEMO_VAR }}    → '来自环境变量'
   {{ config.agent.timeout }} → 60
   {{ self.node_id }}  → 'agent-main'
== ③ {{ x.resolved }}：模板内引用另一个 Parsable 的渲染结果 ==
   '政策：退款政策：七天内无理由退款，超期需审核。'
   对照（不写 .resolved 只显示模板原文）: '政策：$./refund.md'
== ④ 保留名冲突 → ReservedAttributeError ==
   Reserved attribute name occupied: 'env'
== ⑤ 未绑定 + 未命中 ==
   未绑定无上下文 resolve → MissingContextError
   inject 未命中 → MissingProvideError
== ⑥ PENDING 位置分流：字段位 _ = 必须兑现的承诺 ==
   description: _ 未兑现 → MissingFieldError: Required field 'description' of agent type '@/pending-agent.fya' is still PENDING
```

### 完整可运行材料

将每个代码块分别保存为标题所示文件名。程序会自行写入退款政策输入与
PENDING 字段测试夹具，不需要其他数据文件。若 Provider 配置要求凭证，
再设置环境变量 `DEEPSEEK_API_KEY`；本离线演示不会发起 Provider 调用。

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

`root.fya`：

```yaml
description: Parsable 演示助手：最小问答。
model_tag: default
---
$system_prompt:
你是一个简洁的中文助手。
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

`demo_parsable.py`：

```python
import asyncio
import os
from pathlib import Path

from flowing import Parsable, launch
from flowing.errors import (MissingContextError, MissingFieldError,
                            MissingProvideError, ReservedAttributeError)


REFUND_POLICY = "退款政策：七天内无理由退款，超期需审核。"


async def main() -> None:
    Path("refund.md").write_text(REFUND_POLICY, encoding="utf-8")
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    agent.user_name = "小明"

    print("== ① 五种形式判定（type 自动推断，构造时不求值）==")
    for label, parsable in [
        ("RAW      ", Parsable('$"含 {{ 花括号 }} 的原文"')),
        ("FILE_REF ", Parsable("$./refund.md")),
        ("EXPRESSION", Parsable("{{ 40 + 2 }}")),
        ("TEMPLATE ", Parsable("你好 {{ user_name }}")),
        ("LITERAL  ", Parsable("普通文本")),
    ]:
        print(f"   {label} type={parsable.type:<11} → {parsable.resolve(agent)!r}")

    print("== ② 渲染上下文（实例属性摊平 + env / config / self 入口）==")
    os.environ["DEMO_VAR"] = "来自环境变量"
    print(f"   {{{{ env.DEMO_VAR }}}}    → "
          f"{Parsable('{{ env.DEMO_VAR }}').resolve(agent)!r}")
    print(f"   {{{{ config.agent.timeout }}}} → "
          f"{Parsable('{{ config.agent.timeout }}').resolve(agent)!r}")
    print(f"   {{{{ self.node_id }}}}  → "
          f"{Parsable('{{ self.node_id }}').resolve(agent)!r}")

    print("== ③ {{ x.resolved }}：模板内引用另一个 Parsable 的渲染结果 ==")
    agent.refund_policy = Parsable("$./refund.md").bind(agent)
    rendered = Parsable("政策：{{ refund_policy.resolved }}").resolve(agent)
    print(f"   {rendered!r}")
    raw = Parsable("政策：{{ refund_policy }}").resolve(agent)
    print(f"   对照（不写 .resolved 只显示模板原文）: {raw!r}")

    print("== ④ 保留名冲突 → ReservedAttributeError ==")
    agent.env = "占用了保留名"
    try:
        Parsable("{{ env.DEMO_VAR }}").resolve(agent)
    except ReservedAttributeError as exc:
        print(f"   {exc}")

    print("== ⑤ 未绑定 + 未命中 ==")
    try:
        Parsable("{{ anything }}").resolve()
    except MissingContextError:
        print("   未绑定无上下文 resolve → MissingContextError")
    try:
        agent.inject("no_such_key")
    except MissingProvideError:
        print("   inject 未命中 → MissingProvideError")

    print("== ⑥ PENDING 位置分流：字段位 _ = 必须兑现的承诺 ==")
    Path("pending-agent.fya").write_text(
        "description: _\nmodel_tag: default\n---\n$system_prompt:\n占位\n",
        encoding="utf-8")
    try:
        await runtime.create_agent("@/pending-agent.fya")
    except MissingFieldError as exc:
        print(f"   description: _ 未兑现 → MissingFieldError: {exc}")
    await runtime.shutdown()


asyncio.run(main())
```

使用 `uv run python demo_parsable.py` 运行。六种输入情况、退款政策文本、
环境变量值与 PENDING 夹具均在程序中定义；完整留档输出见上文。

## 小结

1. 五形式自动判定；EXPRESSION 返回原生类型，RAW 跳过全部渲染；
2. 惰性求值是正确性前提；渲染上下文 = 实例属性 + env/config/self；
3. `{{ x.resolved }}` 显式求值、无隐式递归；
4. PENDING 按位置分流：字段位必兑现、覆写位空补丁、列表项禁止。
