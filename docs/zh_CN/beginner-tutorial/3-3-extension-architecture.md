# 3-3 · 扩展架构：插件模型与策略组合

> 示例运行前提：环境中已安装 Flowing CLI；本演示不调用模型，也不需要真实凭证。下文逐项给出运行所需文件全文。
> 运行：在包含这些相对文件的全新隔离工作目录中执行 `uv run python demo_extensions.py`；每次演示使用新的工作目录。

> 前置：第 2-4 篇“干预点”

## 这篇讲什么

系统能力的两种扩展形态：插件（带扩展点的能力包）与策略组合
（通过 Composable 函数注入应用逻辑），以及两阶段启用、handler 可卸载等设计。

## 背景知识

可扩展性是成熟系统的共同特征：核心保持稳定，能力经扩展进入。
插件模型的历史很长（浏览器扩展、编辑器插件、服务端的中间件
生态），沉淀出几个稳定的设计结论，Agent 框架直接沿用。

## 核心概念

### 扩展点设计

插件机制的根基是**扩展点**：核心执行管线中预留给扩展介入的
位置（概念上即第 2-4 篇的干预点，区别在于扩展点同时携带一份
契约——扩展能拿到什么数据、能影响什么行为）。设计扩展点时的
判断标准：

- 扩展点暴露的数据面要窄：只给必要的数据，不给内部对象的可变
  引用；
- 扩展点声明方与使用方分离：核心声明，扩展消费；
- 未启用扩展时零开销：扩展点存在 ≠ 有成本，空扩展点应与无
  扩展点等效。

### 两阶段启用

带扩展能力的系统普遍采用两阶段启用：

```mermaid
flowchart LR
    A["阶段一：注册（全局、一次）<br/>扩展登记工具本体 / 钩子点 / 资源"] --> B["阶段二：启用（按 Agent、可多次）<br/>Agent 装配时取用已注册的能力"]
```

两阶段的理由：注册是全局的、一次性的；启用是按 Agent 的、可能
多次的。合在一起会让“已安装”与“在用”无法区分——未启用的
Agent 也背上扩展的开销与风险。分阶段后，“未启用 = 未存在”。

### 完整示例材料

以下代码块标题给出应创建的相对文件名，块内均为完整文件内容。
演示脚本不进行模型调用；示例技能 `quick-tip` 只用于展示目录注入，
其完整定义也内联在本文中。

#### `root.fya`

```yaml
description: 扩展示例助手：skill-load + echo 工具 + 策略注入演示。
model_tag: default
tools:
  - skill-load
  - ./tools/echo.py
skills:
  - ./skills/quick-tip.md
---
$system_prompt:
你是演示助手。用户想加载技能时用 skill-load。回答控制在一句话以内。
---
$script:
from flowing.plugins.skills import use_skill


async def setup(self):
    use_skill(self)
```

#### `skills/quick-tip.md`

```markdown
---
name: quick-tip
description: 提供一个适合当前情境的简短建议。
---

## 执行说明

根据调用者提出的任务，给出一条具体、简洁、可执行的建议。
只输出建议本身。
```

#### `main.py`

```python
from flowing import Runtime
from flowing.plugins.skills import SkillPlugin


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.install(SkillPlugin())
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

#### `late-main.py`

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing-late")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # 故意不安装 SkillPlugin：setup() 中的 use_skill() 无法取得注册表。
    await runtime.mount("@/root.fya", agent_id="agent-main")
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

#### `tools/echo.py`

```python
from pydantic import BaseModel

from flowing import ScriptTool


class EchoArgs(BaseModel):
    text: str


class EchoTool(ScriptTool):
    """原样返回输入文本。"""

    name = "echo"
    args_model = EchoArgs

    async def execute(self, *, text: str) -> str:
        return text
```

#### `composables/rate_limit.py`

```python
import asyncio

from flowing.errors import Intercepted


def use_rate_limit(agent, *, max_calls: int = 5, window_seconds: float = 60.0) -> None:
    agent.hooks.declare("on_rate_limited", by="rate-limit", match_on="name")
    calls: list[float] = []

    async def _gate(agent_, tool_call):
        now = asyncio.get_event_loop().time()
        calls[:] = [t for t in calls if now - t < window_seconds]
        if len(calls) >= max_calls:
            await agent_.hooks.on_rate_limited.dispatch(agent_, tool_call)
            raise Intercepted(
                f"调用频率超限：{max_calls} 次 / {window_seconds:.0f} 秒，请稍后再试")
        calls.append(now)
        return tool_call

    agent.hooks.before_tool_call(_gate, by="rate-limit", tags=["rate-limit"])


def remove_rate_limit(agent) -> int:
    return agent.hooks.before_tool_call.remove_by_owner("rate-limit")
```

#### `demo_extensions.py`

```python
import asyncio
import sys

from flowing import Runtime, launch
from flowing.errors import DependencyError, MissingProvideError, ToolNotFoundError
from flowing.plugins import Plugin
from flowing.tool import ToolCall

sys.path.insert(0, "composables")
from rate_limit import remove_rate_limit, use_rate_limit


class _P1(Plugin):
    name = "p1"
    dependencies = ("p2",)

    def install(self, runtime):
        pass


class _P2(Plugin):
    name = "p2"
    dependencies = ("p1",)

    def install(self, runtime):
        pass


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    print("== ① 两阶段启用的产物检查 ==")
    print(f"   skill-load 在全局注册表: {agent.get_tool('skill-load') is not None}")
    print(f"   prompt 块含 skills catalog: "
          f"{any(b.name == 'skills' for b in agent.prompt_blocks)}")

    print("== ② 迟装（install 迟于 mount）→ 创建期失败 ==")
    try:
        await launch(".", main_file="@/late-main.py")
        print("   意外成功（与契约不符）")
    except (ToolNotFoundError, MissingProvideError) as exc:
        print(f"   迟装 launch 失败: {type(exc).__name__}"
              f"（插件注册的工具本体在阶段一；必须先于首个 mount）")

    print("== ③ 依赖声明：成环 fail-fast ==")
    try:
        runtime.install(_P1(), _P2())
    except DependencyError:
        print("   p1 ↔ p2 互相依赖 → DependencyError")

    print("== ④ use_rate_limit(max_calls=3) 注入 ==")
    limited: list[str] = []
    use_rate_limit(agent, max_calls=3, window_seconds=60.0)

    async def observe(agent_, tool_call):
        limited.append(tool_call.name)
        return tool_call
    agent.hooks.on_rate_limited(observe, by="demo")

    results = []
    for i in range(5):
        result = await agent.tool_call(ToolCall(
            id=f"c{i}", name="echo", args={"text": f"第{i}发"}))
        results.append(result.status)
    print(f"   5 次调用结果: {results}")
    print(f"   on_rate_limited 观测到 {len(limited)} 次超限（第 4、5 发）")

    print("== ⑤ remove_by_owner 整组移除 ==")
    removed = remove_rate_limit(agent)
    result = await agent.tool_call(
        ToolCall(id="c9", name="echo", args={"text": "恢复"}))
    print(f"   移除 {removed} 条 handler；再调一次: status={result.status}")
    await runtime.shutdown()


asyncio.run(main())
```

运行 `uv run python demo_extensions.py` 后，第①至③段的确定性输出为：

```console
== ① 两阶段启用的产物检查 ==
   skill-load 在全局注册表: True
   prompt 块含 skills catalog: True
== ② 迟装（install 迟于 mount）→ 创建期失败 ==
   迟装 launch 失败: ToolNotFoundError（插件注册的工具本体在阶段一；必须先于首个 mount）
== ③ 依赖声明：成环 fail-fast ==
   p1 ↔ p2 互相依赖 → DependencyError
```

逐段看：第①段是正常路径的两个产物——阶段一把技能加载工具
注册进了全局注册表，阶段二让 Agent 的提示词里出现了技能目录；
第②段是故意反过来做的负例——先创建 Agent 再注册插件，Agent 在
创建期就直接失败（它要取用的注册表还不存在），“迟装无效”
是创建期错误而不是运行期惊喜；第③段是两个互相声明依赖的
插件在注册现场成环报错——依赖关系只声明、不自动求解，成环
立即失败。迟装负例会在失败前留下部分会话状态，因此每次运行
`demo_extensions.py` 都应使用一份全新的隔离工作目录；不要在同一
工作目录重复运行，也不需要手动清理状态。

### 策略可组合，handler 可卸载

行为策略（重试、限流、提醒注入）可以通过 Composable 函数注入。下面的
限流示例通过在干预点注册 handler 实现策略；Composable 函数体也可以挂载
Agent 属性、登记状态或执行其他应用层逻辑。只在当前运行期使用的临时
状态可放在闭包变量或 Agent 属性中：把状态放在该次调用创建的闭包中，可
避免与 Agent 属性名冲突，但外部无法通过 Agent 直接访问；Agent 属性便于
外部读取和管理，但需避免命名冲突。需要
持久化的状态可通过 Flowing 内置的 ``agent.state.register()`` 登记，也可
由应用自行维护持久化与恢复；闭包变量和普通 Agent 属性本身不提供持久化
能力。

下面是说明形态的伪代码，不可直接运行；其中的辅助函数名是占位符。
完整可运行的限流实现已包含在上方示例材料中。

```python
# 策略注入的概念形态
def use_rate_limit(component, *, max=5, window=60):
    calls = []
    def gate(call):                        # 策略本体 = 一个拦截处理函数
        prune_old(calls, window)
        if len(calls) >= max:
            raise Blocked("超出调用频率")
        calls.append(now())
        return call
    component.on_before_call(gate, owner="rate-limit")   # 归组挂点
```

本例展示三项组合方式：

- **可组合**：多个策略各自挂 handler，互不感知；handler 的组合顺序即注册顺序；
- **handler 可卸载**：需要整组管理时，以组名注册 handler，再用
  ``remove_by_owner`` 移除；Composable 的其他行为不受此规则限制；
- **调用不自动去重**：同一个 Composable 可用不同参数多次调用。框架
  不会自动去重，具体调用效果由 Composable 实现决定；本例中的 handler
  按注册语义叠加。

演示输出的第④、⑤段展示本例的实现；完整策略代码已在上方
内联：

```console
== ④ use_rate_limit(max_calls=3) 注入 ==
   5 次调用结果: ['completed', 'completed', 'completed', 'blocked', 'blocked']
   on_rate_limited 观测到 2 次超限（第 4、5 发）
== ⑤ remove_by_owner 整组移除 ==
   移除 1 条 handler；再调一次: status=completed
```

逐行看：注入“60 秒内最多 3 次”的限流策略后连发 5 次调用，
前 3 次放行（completed）、第 4、5 次被阻断（blocked）——
阻断同时触发了一条观测通知；整组移除这条策略后再调用一次，
立即恢复放行。策略通过 Agent 的钩子注册表显式添加、移除处理函数，
无需改写 Agent 的核心实现。

## 常见误区

1. **注册即启用**。两阶段的意义正在于区分；混用导致无法回答
   “这个 Agent 身上现在有哪些能力”；
2. **需要批量移除的 handler 没有归组**。挂载 handler 时，如需整组
   移除，应提供归组名；
3. **扩展点暴露内部可变对象**。数据面收窄是长期稳定性的前提。

## 练习

1. 为你熟悉的编辑器或浏览器列出三个扩展点，按“暴露的数据面
   宽窄”排序，说明最宽的那个为什么危险；
2. 用伪代码实现“每轮注入一条提醒”策略（挂点 + 归组 + 卸载
   函数），对照本篇的示例结构自查三个设计结论是否都满足；
3. 修改上方完整演示脚本，把限流策略连续注入两次
   （`use_rate_limit` 调两遍），运行后从调用结果推断“叠加
   注册”的实际语义。

## 小结

1. 插件机制的根基是扩展点：位置、窄数据面、声明与消费分离、
   空载零开销；
2. 两阶段启用区分“已安装”与“在用”；
3. Composable 函数注入应用逻辑；handler 可按需归组管理，调用不自动去重；
4. 三个设计结论在成熟生态中反复出现，Agent 框架直接沿用。
