# 示例：3-3 扩展架构

离线脚本演示插件两阶段启用、迟装失败、依赖环校验、限流 Composable，以及卸载一组处理器。下文完整内联了所需代码、配置、提示词、输入与示例输出。

运行环境需要 Python 3.13 或更高版本、uv，以及可使用 Flowing CLI 和包的 Python 环境。模型调用还需要在 shell 中设置你自己的 `DEEPSEEK_API_KEY`；本文不含真实凭证。请在全新的工作目录下按下文相对文件名创建文件，并从该目录执行命令。

## 复现

### 运行扩展演示

请在包含本文内联文件的工作目录中执行以下命令：

```console
$ uv run python demo_extensions.py
```

该脚本不调用模型。请在全新工作状态下运行一次；迟装失败可能留下状态，再次尝试时请使用全新状态。

## 完整内联材料

下方每个代码块都包含本示例所用相对文件的完整内容。输出代码块是示例留档；模型答复和主机相关信息可能变化。

### `composables/rate_limit.py`

```python
"""自写 Composable 示例：use_rate_limit —— 工具调用频率限制。

示例结构（与内置 Composable 的钩子挂载方式同构）：
  use_xxx(agent, ...) → 可选 declare 扩展钩子点 → 可选挂核心钩子点 handler。
同一个函数可用不同参数多次调用；调用效果由函数实现决定。本例的限流
计数只需在当前运行期使用，因此存在闭包中；持久化状态可通过
agent.state.register() 或应用自有的持久化与恢复机制管理。
"""
import asyncio

from flowing.errors import Intercepted


def use_rate_limit(agent, *, max_calls: int = 5, window_seconds: float = 60.0) -> None:
    """窗口内工具调用超额的调用被 Intercepted 阻断（blocked 结果）。

    声明观测钩子点 ``on_rate_limited``（match_on="name"，按工具名过滤）；
    主逻辑挂在 ``before_tool_call`` 上（by="rate-limit" 整组可管理）。
    """
    agent.hooks.declare("on_rate_limited", by="rate-limit", match_on="name")

    calls: list[float] = []

    async def _gate(agent_, tool_call):
        now = asyncio.get_event_loop().time()
        calls[:] = [t for t in calls if now - t < window_seconds]   # 滑窗清理
        if len(calls) >= max_calls:
            await agent_.hooks.on_rate_limited.dispatch(agent_, tool_call)  # 观测通道
            raise Intercepted(
                f"调用频率超限：{max_calls} 次 / {window_seconds:.0f} 秒，请稍后再试")
        calls.append(now)
        return tool_call

    agent.hooks.before_tool_call(_gate, by="rate-limit", tags=["rate-limit"])


def remove_rate_limit(agent) -> int:
    """整组移除（by 归组管理的标准姿势）。"""
    return agent.hooks.before_tool_call.remove_by_owner("rate-limit")
```

### `demo_extensions.py`

```python
"""扩展架构演示：两阶段启用 / 迟装无效 / 依赖成环 / 策略注入与卸载。

运行：uv run python demo_extensions.py
"""
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

    # ① 两阶段启用：阶段一 install 注册本体，阶段二 setup 里 use_skill 启用
    print("== ① 两阶段启用的产物检查 ==")
    print(f"   skill-load 在全局注册表: "
          f"{agent.get_tool('skill-load') is not None}")
    print(f"   prompt 块含 skills catalog: "
          f"{any(b.name == 'skills' for b in agent.prompt_blocks)}")

    # ② 迟装无效：mount 在前、install 在后 → 创建期失败
    print("== ② 迟装（install 迟于 mount）→ 创建期失败 ==")
    try:
        await launch(".", main_file="@/notes-late-main.py")
        print("   意外成功（与契约不符）")
    except (ToolNotFoundError, MissingProvideError) as exc:
        print(f"   迟装 launch 失败: {type(exc).__name__}"
              f"（插件注册的工具本体在阶段一；必须先于首个 mount）")

    # ③ 依赖声明：成环在 install 现场抛 DependencyError
    print("== ③ 依赖声明：成环 fail-fast ==")
    try:
        runtime.install(_P1(), _P2())
    except DependencyError:
        print("   p1 ↔ p2 互相依赖 → DependencyError")

    # ④ 策略注入：自写 use_rate_limit（窗口 60s、上限 3 次）
    print("== ④ use_rate_limit(max_calls=3) 注入 ==")
    limited: list[str] = []
    use_rate_limit(agent, max_calls=3, window_seconds=60.0)

    async def observe(agent_, tool_call):
        limited.append(tool_call.name)
        return tool_call
    agent.hooks.on_rate_limited(observe, by="demo")

    results = []
    for i in range(5):   # 连发 5 次：前 3 放行、后 2 阻断
        r = await agent.tool_call(ToolCall(id=f"c{i}", name="echo",
                                           args={"text": f"第{i}发"}))
        results.append(r.status)
    print(f"   5 次调用结果: {results}")
    print(f"   on_rate_limited 观测到 {len(limited)} 次超限（第 4、5 发）")

    # ⑤ 可卸载：by 归组整组移除后行为还原
    print("== ⑤ remove_by_owner 整组移除 ==")
    removed = remove_rate_limit(agent)
    r = await agent.tool_call(ToolCall(id="c9", name="echo", args={"text": "恢复"}))
    print(f"   移除 {removed} 条 handler；再调一次: status={r.status}")

    await runtime.shutdown()


asyncio.run(main())
```

### `demo_output.txt`

```text
== ① 两阶段启用的产物检查 ==
   skill-load 在全局注册表: True
   prompt 块含 skills catalog: True
== ② 迟装（install 迟于 mount）→ 创建期失败 ==
   迟装 launch 失败: ToolNotFoundError（插件注册的工具本体在阶段一；必须先于首个 mount）
== ③ 依赖声明：成环 fail-fast ==
   p1 ↔ p2 互相依赖 → DependencyError
== ④ use_rate_limit(max_calls=3) 注入 ==
   5 次调用结果: ['completed', 'completed', 'completed', 'blocked', 'blocked']
   on_rate_limited 观测到 2 次超限（第 4、5 发）
== ⑤ remove_by_owner 整组移除 ==
   移除 1 条 handler；再调一次: status=completed
```

### `main.py`

```python
"""扩展示例工程入口：launch 会 import 本文件并 await main()。"""

from flowing import Runtime
from flowing.plugins.skills import SkillPlugin


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # 阶段一：install 做全局注册（工具本体 / 注册表）——须在首个 mount 之前
    runtime.install(SkillPlugin())
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### `model-tags.yaml`

```yaml
# 标签 → 模型条目名映射（单值：一个标签只映射一个条目）。
tags:
  default: deepseek-flash
```

### `models.yaml`

```yaml
# 模型条目：一个条目 = 一个具体模型（绑定一个 provider 条目）。
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### `notes-late-main.py`

```python
"""迟装负例的入口（迟装负例演示用）：mount 前不 install 插件。"""

from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing-late")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    # 故意不 install(SkillPlugin()) —— root.fya 的 setup 里 use_skill 将无注册表可注入
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### `providers.yaml`

```yaml
# provider 条目：一个条目 = 一个 API key 身份。
# {{env.VAR}} 在加载期替换；缺失时替换为空串并告警（warnings.warn），加载不中断。
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### `root.fya`

```text
description: 扩展示例助手：skill-load + echo 工具 + 策略注入演示。
model_tag: default
tools:
  - skill-load       # 插件 install 注册的本体；显式声明才对 LLM 可见
  - ./tools/echo.py
skills:
  - ./skills/daily-tip.md
---
$system_prompt:
你是演示助手。用户想加载技能时用 skill-load。回答控制在一句话以内。
---
$script:
from flowing.plugins.skills import use_skill


async def setup(self):
    # 阶段二：实例级启用（声明钩子点 + 注册 handler + 注入技能目录）
    use_skill(self)
```

### `tools/echo.py`

```python
"""回声工具（策略注入演示的调用素材）。"""
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

### `skills/daily-tip.md`

```markdown
---
description: 每日一技：为用户当天的工作场景推荐一个实用小技巧。
---
# 每日一技

根据用户提到的今日工作，给出一条具体、可立即执行的小技巧
（命令、快捷键或流程改进），一句话讲完。
```
