# 1-9 · 插件与 Composable 初识

## 前置阅读

[1-4 钩子基础](1-4-hooks-basics.md)、[1-5 provide 与 inject](1-5-provide-inject.md)。
本篇**仅使用**现成扩展——每种能力用一次接入调用。完整 setup、技能正文、
提示词、输入和可观察输出均列在下文。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 插件（Plugin） | Runtime 级能力包：`runtime.install(...)` 启用，给 Runtime 注册工具 / 注册表等全局能力（本篇以 SkillPlugin 为例） |
| Composable | 以 Agent 为主要作用对象的应用逻辑注入：在 `setup()` 里调用 `use_xxx(agent, ...)`；函数可按需挂载 handler、Agent 属性或登记状态（本篇用 `use_system_reminder` / `use_prompt_until`） |
| `use_skill` | skills 插件的实例级启用函数（模块级函数，参数是 agent）——声明 `.fya` 的 `skills:` 列表并注入技能目录 |
| `skill-load` | skills 插件注册的工具：LLM 按名加载技能，正文经 EVENT 消息入队进树 |
| `use_retry` | 重试策略 Composable：LLM 调用遇限流/瞬时故障时退避重试（有 `on_retry` 观测钩子点）——依赖真实故障触发，本篇不演示 |

## 目标

会用现成的插件与 Composable：知道一行怎么写、现象长什么样。

## 正文

### 分工一句话

**插件是 Runtime 级能力包，Composable 通常以 Agent 为装配对象。** 插件在
`main()` 里、首个 `mount()` 之前 `runtime.install(...)`（给整个 Runtime
注册能力）；Composable 在 Agent 的 `setup()` 里调用 `use_xxx(self, ...)`。
该函数以当前 Agent 为主要作用对象，但其应用层行为由函数实现决定。

### 插件（仅使用）：以 skills 为例

```python
# main.py
runtime.install(SkillPlugin())   # 须在首个 mount 之前（迟装对已建 Agent 无效）
```

```yaml
# root.fya 头部
tools:
  - skill-load   # 插件 install 已注册本体；显式声明才对 LLM 可见（注册 ≠ 可见）
skills:
  - ./skills/polite.md   # 声明可用技能
```

```python
# root.fya $script
from flowing.plugins.skills import use_skill

async def setup(self):
    use_skill(self)   # 实例级启用：注入技能目录、注册加载入口
```

Agent 从此多出“按名加载专长”的能力：catalog 里有技能清单，LLM 经
`skill-load` 加载，正文作为 EVENT 消息进树（主线示例 run 1 可见）。
一个实用的提示词配合：加载类请求先调用 `skill-load` 并结束本轮，等正文
送达再作答——正文是独立消息，不是工具返回值。

### Composable（仅使用）：两个可复现的例子

```python
from flowing import MessageKind, TextBlock
from flowing.composables import use_prompt_until, use_system_reminder

async def setup(self):
    use_system_reminder(self, contents=["[提醒] 请遵循当前任务要求。"])

    def _done(agent, turn) -> bool:
        for mid in reversed(turn.message_ids):
            msg = agent.chain.get(mid)
            if msg.kind is MessageKind.PROVIDER:
                text = "".join(
                    block.text for block in msg.content
                    if isinstance(block, TextBlock)
                )
                return "DONE" in text
        return True

    use_prompt_until(
        self,
        predicate=_done,
        message="未满足验收条件：最终回复中须包含 DONE，请重新回答。",
    )
```

- `use_system_reminder(agent, contents=[...])`：每回合开头注入一条提醒
  消息（kind=EVENT），模板随注入现场求值；
- `use_prompt_until(agent, predicate, message)`：回合收尾断言——
  `predicate(agent, turn)` 为假则把 `message` 经 `steer()` 入队续跑
  （“最终回复含 DONE 才停”即此实现）。

`use_retry` 只说一句：LLM 调用遇限流/瞬时故障时退避重试，依赖真实故障
触发、本篇不可控故不演示（其观测通道 `on_retry` 的复现小例见 5-3）。

## 本篇不覆盖

- 两阶段启用、declare 钩子点、幂等性、依赖声明——5-2；
- 参数全集与自写 Composable 的常见结构——5-3；
- `use_retry` 的机制——4-6（错误决策面）与 5-3。

## 主线示例

**run 1：skills + 两个 Composable 同场**（代表性消息流程）：

```console
$ uv run flowing repl .
(agent-main)>>> 用礼貌语问候我：早上好，回答末尾请附 DONE。
[thinking] (reasoning trace omitted)
[tool_call] skill-load {"name": "polite"}
[tool:completed] skill-load ->
技能正文将作为独立的 event 消息送达。
[steer] 未满足验收条件：最终回复中须包含 DONE，请重新回答。
愿阁下今日顺遂，凡事称心。早上好，也谢谢您的问候。

DONE
(agent-main)>>> 继续：请用刚学到的固定句式祝我下午顺利，末尾附 DONE。
愿阁下今日顺遂，凡事称心。也祝您下午一切顺利。谢谢。

DONE
(agent-main)>>> /messages
1  user      用礼貌语问候我：早上好，回答末尾请附 DONE。
2  event     [提醒] 请遵循当前任务要求。
3  provider  第一条回复尚未包含 DONE。
5  tool
6  provider  技能已加载，正文将作为独立消息送达。
7  event     未满足验收条件：最终回复中须包含 DONE，请重新回答。
4  event     完整技能正文见下方内联的礼貌问候技能。
8  event     [提醒] 请遵循当前任务要求。
9  provider  愿阁下今日顺遂，凡事称心。早上好，也谢谢您的问候。DONE
10  user      继续：请用刚学到的固定句式祝我下午顺利，末尾附 DONE。
11  event     [提醒] 请遵循当前任务要求。
12  provider  愿阁下今日顺遂，凡事称心。也祝您下午一切顺利。谢谢。DONE
```

读这段留档（只观察现象）：`skill-load` 被调用后，技能正文作为 `event`
消息（id 4）进树；正文送达后的回答（id 9）用上了正文规定的固定句式
——“愿阁下今日顺遂，凡事称心”定义在下方完整技能正文中；每个回合开头都有
`event` 提醒消息（`use_system_reminder`）；id 6 的回答没附 DONE，回合
收尾即被 `use_prompt_until` 导向续跑（id 7 的 steer 消息）。

**run 2：故意不说 DONE → 被续跑**：

```console
$ uv run flowing repl .
(agent-main)>>> 只回复“好的”两个字，什么都不要加。
[thinking] (reasoning trace omitted)
好的
[steer] 验收条件未满足：你的最终回复必须包含单独一行的 DONE。请重新回答刚才的问题，并在末尾附上 DONE。
好的
(agent-main)>>> /messages
1  user      只回复“好的”两个字，什么都不要加。
2  event     [提醒] 请遵循当前任务要求。
3  provider  好的
4  event     未满足验收条件：最终回复中须包含 DONE，请重新回答。
5  event     [提醒] 请遵循当前任务要求。
```

这次示意交互中，模型在 steer 后仍重复“好的”；其他模型的回答可能不同。
稳定行为是：回复未包含 `DONE` 时，`use_prompt_until` 会在回合结束时添加
steer 消息。输入 `/exit` 可结束循环。

## 完整示例材料

在已安装 Flowing 的项目中按相对文件名创建以下内容，并在环境变量中设置
`DEEPSEEK_API_KEY`。提醒文本不含任何机器路径。回复由模型生成；稳定可观察
的行为是：加载技能后正文以 event 消息入队，缺少 `DONE` 的回复会触发 steer。

`main.py`：

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
description: 插件与 Composable 演示助手。
model_tag: default
tools:
  - skill-load
skills:
  - ./skills/polite.md
---
$system_prompt:
你是简洁的演示助手。遇到问候请求时，先用 name="polite" 调用 skill-load，
并结束本回合，不要回答。技能正文送达后，严格遵循其中要求。每次最终回复
都要包含 DONE。
---
$script:
from flowing import MessageKind, TextBlock
from flowing.composables import use_prompt_until, use_system_reminder
from flowing.plugins.skills import use_skill


async def setup(self):
    use_skill(self)
    use_system_reminder(
        self,
        contents=["[提醒] 请遵循当前任务要求。"],
    )

    def _done(agent, turn) -> bool:
        for mid in reversed(turn.message_ids):
            msg = agent.chain.get(mid)
            if msg.kind is MessageKind.PROVIDER:
                text = "".join(
                    block.text for block in msg.content
                    if isinstance(block, TextBlock)
                )
                return "DONE" in text
        return True

    use_prompt_until(
        self,
        predicate=_done,
        message="未满足验收条件：最终回复中须包含 DONE，请重新回答。",
    )
```

`skills/polite.md`（完整技能正文）：

```markdown
---
description: 礼貌问候技能：使用固定问候句式，并在回复中使用敬语。
---
# 礼貌问候规范

- 每次问候必须以固定句式开头：“愿阁下今日顺遂，凡事称心。”
- 尊称用户为“您”，并在自然合适处使用“请”“谢谢”等敬语。
- 语气谦逊，避免生硬祈使句。

## 示例

输入：`早上好。`

回复：`愿阁下今日顺遂，凡事称心。早上好，也谢谢您的问候。`
```

第一轮输入：

```text
用礼貌语问候我：早上好，回答末尾请附 DONE。
```

第二轮输入：

```text
继续：请用刚学到的固定句式祝我下午顺利，末尾附 DONE。
```

预期消息流：`skill-load` 收到 `{ "name": "polite" }`，技能正文以
`event` 消息送达；`use_system_reminder` 在每轮开头添加 `event` 消息；若
最新 provider 回复不含 `DONE`，`use_prompt_until` 会添加 `steer` 消息。模型的
具体问候措辞不固定。可以输入“只回复“好的”两个字，什么都不要加。”尝试
触发负例，但模型也可能遵循 system prompt 并附上 `DONE`。只要实际回复不含
`DONE`，回合结束时就会出现 steer event。

## 小结

1. 插件 = Runtime 级能力包（`install` 在 mount 前）；Composable = 以 Agent
   为主要作用对象的应用逻辑注入（`use_xxx(self, ...)` 在 setup 里）；
2. 插件注册的工具本体仍须 Agent 显式声明（注册 ≠ 可见，`skill-load` 为例）；
3. `use_system_reminder` 每回合注入提醒、`use_prompt_until` 断言不成立则
   steer 续跑——两个现象都可复现观察；
4. `use_retry` 依赖真实故障触发，本篇不演示（机制见 4-6 / 5-3）。
