# 5-2 · 插件

## 前置阅读

[1-9 插件与 Composable 初识](1-9-plugins-and-composables.md)（仅使用
的初见）。本篇内联完整配置、Agent 定义、技能提示词、演示程序与留档
输出。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 两阶段启用 | 插件启用的固定两步：阶段一 `runtime.install(...)`（Runtime 级全局注册）→ 阶段二 Agent `setup()` 里的 `use_xxx(self)`（实例级启用） |
| Plugin 基类 | 插件统一契约包含 `name` / `namespace` / `dependencies` / `install(runtime)` / `shutdown()` |
| 依赖声明 | `dependencies` 只声明：已装依赖图成环 → `DependencyError`（install 现场）；缺失只 `warnings.warn` 不抛 |
| install 时机 | 须在首个 `mount()` 之前——迟装对已建 Agent 无效（其 setup 已运行、注册表已定型） |

## 目标

插件机制与内置插件巡礼：两阶段启用、依赖声明、install 时机——并各看
一个真实运行例（skills 加载 + cron 到点）。

## 正文

### 两阶段启用

```python
# main.py —— 阶段一：Runtime 级全局注册（工具本体 / provide 值 / 配置命名空间）
runtime.install(SkillPlugin(), CronPlugin())   # 须在首个 mount 之前

# root.fya $script —— 阶段二：实例级启用（声明钩子点 + 注册 handler + 绑实例方法）
async def setup(self):
    use_skill(self)
    use_cron(self)
```

install 只注册、不装配；Agent 未做阶段二的，扩展对它“没存在过”（零
开销而非被跳过）。**迟装无效**：install 迟于 mount，已建 Agent 的
setup 早已跑完——演示 ② 里 `skill-load` 工具本体在创建期即不可见
（`ToolNotFoundError`）。

### 依赖声明

`dependencies` 是声明不是请求：成环 → 当场 `DependencyError`（报错
现场即引入环的那次 install）；缺失 → 只警告（“声明了依赖但用不上”
合法，install 可分批）。

### 内置插件巡礼（是什么 + 何时用）

| 插件 | 是什么 | 何时用 |
|---|---|---|
| skills | “按名加载一段提示词”：catalog 懒注入 + `skill-load` 工具 | 给 Agent 可装配的专长（1-9 实证） |
| comm | 进程内通信总线：点对点信号 + 发布订阅 | Agent 间带外交互（不进 LLM 上下文、不落盘） |
| cron | 逐 Agent 定时任务（`schedule-cron` / `manage-cron` 工具 + 模块 API） | 定时提醒、周期巡检 |
| workflow | Python 代码显式编排的分支 / 循环 / 并行（非 Agent 节点） | 规则性流程、确定性控制流 |
| clipboard | 文件区段剪切 / 复制 / 粘贴 | LLM 跨文件搬运代码段 |

此表用于认识内置扩展；下方可复现示例聚焦 skills 与 cron。

## 本篇不覆盖

- 所有内置插件的完整参数参考；
- cron 表达式的完整语法；
- 自写 Composable 的常见结构——5-3。

## 主线示例

以下留档记录了一次运行；③为真实时间触发：

```console
$ uv run python demo_plugins.py
== ① 依赖声明：成环 fail-fast ==
   p1 ↔ p2 互相依赖 → DependencyError
== ② 迟装（install 迟于 mount）→ 创建期失败 ==
   迟装 launch 失败: ToolNotFoundError（插件注册的工具本体/注册表在阶段一；必须先于首个 mount）
== ③ cron 到点触发（编程式登记，最多等 70s）==
   已登记每分钟任务，等待到点…
   on_cron_trigger 触发 1 次；内容=['【提醒】该起来活动一下了。']
   EVENT 消息进树 1 条（投递 source：'cron' 或自定义）
== ④ 阶段一产物检查 ==
   skill-load 在全局注册表: True
   prompt 块含 skills catalog: True
```

读这段留档：①的环在 install 现场爆炸；②把“迟装无效”从口头约定变成
创建期错误；③是 cron 的完整链路——模块 API `schedule(agent, cron,
content)` 登记（同步调用），到点后 `on_cron_trigger` 钩子（handler
可改写 content 或 `shortcut=True` 跳过）→ EVENT 消息以 STEER 优先级
入队进树触发新回合；④确认阶段一产物就位但**注册 ≠ 可见**的老规则依
然适用（本体在注册表，Agent 仍须显式声明 `skill-load`）。

**完整可运行材料。**

将每个代码块按标题保存到同一个 Flowing 项目中。Provider 配置保留了
环境变量占位符；运行会调用 Provider 的示例前，请在环境中设置
`DEEPSEEK_API_KEY`。cron 演示会等待每分钟触发点，最长约 70 秒。负例
入口故意不安装插件。技能提示词本身就是项目根级文件，所需内容已完整内联。

### Runtime 入口：`main.py`

```python
from flowing import Runtime
from flowing.plugins.cron import CronPlugin
from flowing.plugins.skills import SkillPlugin


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.install(SkillPlugin(), CronPlugin())
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### Provider 配置：`providers.yaml`

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### 模型配置：`models.yaml`

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

### 模型标签：`model-tags.yaml`

```yaml
tags:
  default: deepseek-flash
```

### Agent 定义：`root.fya`

```yaml
description: 插件巡礼助手：skills + cron 双插件。
model_tag: default
tools:
  - skill-load
  - schedule-cron
  - manage-cron
skills:
  - ./daily-tip.md
---
$system_prompt:
你是插件演示助手。用户想加载技能时用 skill-load；定时提醒类需求用
schedule-cron。cron 到点触发时如实告知用户收到了什么。
---
$script:
from flowing.plugins.cron import use_cron
from flowing.plugins.skills import use_skill


async def setup(self):
    use_skill(self)
    use_cron(self)
```

### 技能提示词：`daily-tip.md`

```markdown
---
description: 每日一技：为用户当天的工作场景推荐一个实用小技巧。
---
# 每日一技

根据用户提到的今日工作，给出一条具体、可立即执行的小技巧
（命令、快捷键或流程改进），一句话讲完。

## 示例

输入：今天我需要花几个小时审查 Pull Request。

输出：用编辑器的差异导航快捷键在变更文件和代码块之间跳转，不要一直滚动查看整份补丁。
```

### 迟装负例入口：`notes-late-main.py`

此入口故意不安装任一插件，却装配同一个 Agent；演示程序只在迟装负例
中使用它。

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing-late")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### 演示程序：`demo_plugins.py`

```python
import asyncio

from flowing import launch
from flowing.errors import DependencyError, MissingProvideError, ToolNotFoundError
from flowing.message import MessageKind
from flowing.plugins import Plugin
from flowing.plugins.cron import schedule


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

    print("== ① 依赖声明：成环 fail-fast ==")
    try:
        runtime.install(_P1(), _P2())
    except DependencyError:
        print("   p1 ↔ p2 互相依赖 → DependencyError")

    print("== ② 迟装（install 迟于 mount）→ 创建期失败 ==")
    try:
        await launch(".", main_file="@/notes-late-main.py")
        print("   意外成功（与契约不符）")
    except (ToolNotFoundError, MissingProvideError) as exc:
        print(f"   迟装 launch 失败: {type(exc).__name__}"
              f"（插件注册的工具本体/注册表在阶段一；必须先于首个 mount）")

    print("== ③ cron 到点触发（编程式登记，最多等 70s）==")
    fired: list[str] = []

    async def on_fire(agent_, ctx):
        fired.append(ctx.content)
        return ctx
    agent.hooks.on_cron_trigger(on_fire, by="demo")

    schedule(agent, "*/1 * * * *", "【提醒】该起来活动一下了。")
    print("   已登记每分钟任务，等待到点…")
    for _ in range(140):
        await asyncio.sleep(0.5)
        if fired:
            break
    for _ in range(40):
        await asyncio.sleep(0.5)
        events = [m for m in agent._messages.values()
                  if m.kind is MessageKind.EVENT
                  and m.source in ("cron", "scheduled_task")]
        if events:
            break
    print(f"   on_cron_trigger 触发 {len(fired)} 次；内容={fired!r}")
    print(f"   EVENT 消息进树 {len(events)} 条（投递 source：'cron' 或自定义）")

    print("== ④ 阶段一产物检查 ==")
    print(f"   skill-load 在全局注册表: "
          f"{agent.get_tool('skill-load') is not None}")
    print(f"   prompt 块含 skills catalog: "
          f"{any(b.name == 'skills' for b in agent.prompt_blocks)}")
    await runtime.shutdown()


asyncio.run(main())
```

从保存这些内联材料的工作目录运行 `uv run python demo_plugins.py`。
Provider 凭证只通过 `DEEPSEEK_API_KEY` 环境变量提供；示例所需材料均已
在上文给出。

## 小结

1. 两阶段启用：install 全局注册（mount 前）→ use_xxx 实例启用
   （setup 里）；
2. 依赖只声明：成环抛错、缺失警告；install 可分批；
3. 迟装无效是创建期错误，不是运行时惊喜；
4. 巡礼：skills / comm / cron / workflow / clipboard 各取所需。
