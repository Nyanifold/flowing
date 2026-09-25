# 5-4 · 实例生命周期：命名、续接与内容状态分离

> 自包含示例：本篇下方列出完整运行配置、编排者与记忆助手定义、输入和代表性输出。创建这些相对文件后，在当前目录运行 `uv run flowing repl .`。
> 对话示例需要 `DEEPSEEK_API_KEY` 环境变量；不要将真实凭证写入配置。

> 前置：第 5-3 篇“编排者设计”

## 这篇讲什么

工作者实例的生命周期：类型绑定与实例化的区分、命名与按名续接
（记忆连续）、返回结果的内容与执行状态分离。

## 背景知识

编排者目录里声明的是**类型**（可以使用哪种工作者）；派单时才
创建实例。这个区分是普通 OOP 中“类引用”与“对象构造”的重演：
声明阶段定义可能集，构造时刻决定具体对象。

## 核心概念

### 类型绑定 vs 实例化

```python
team.declare("translator")          # 类型绑定：目录里出现可选项
worker = team.invoke("translator")  # 实例化：此刻才真正创建
```

上面是表达两阶段关系的概念伪代码，不是独立可运行程序；下方完整示例使用真实的 Flowing 配置与调用协议。

两阶段的理由：目录关心“有谁可用”，实例关心“这一次给谁做”。
混在一起会让“可用性”与“存在性”无法独立变化——停用某类工作者
不应销毁在跑的实例；按名续接也不应依赖目录当前状态（第 5-3 篇
的 `auditor` 演示了“停用后仍可代码唤起”这一侧）。

### 命名与按名续接

每次实例化可登记语义名；后续请求按名指向**同一实例**——它的
历史、状态、记忆全部延续。这是多轮协作的基础：用户期待“上次
那个助手还记得我”，技术上就是“实例可寻址”。

```python
worker = team.invoke("translator", name="cn")   # 创建并登记
team.invoke("cn", prompt="继续上次的翻译")        # 按名续接同一实例
```

创建本篇后文列出的文件并运行 `uv run flowing repl .`。会话里先做命名唤起
（让 assistant 记住数字 42），再追问（追问里**不含数字本身**）。
然后按顺序输入：

```console
让 assistant 记住数字 42。
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"agent_type": "assistant", "name": "memo", "prompt": "请记住这个数字：42。记住后请复述确认。"}
[tool:completed] subagent-invoke ->
assistant 已记住数字 42，并复述确认：“我记住了：这个数字是42。”
问 memo：我之前让你记的数字是几？
[thinking] (reasoning trace omitted)
[tool_call] subagent-invoke {"resume": "memo", "prompt": "我之前让你记的数字是几？"}
[tool:completed] subagent-invoke ->
memo 回答：“你之前让我记的数字是42。”
/exit
```

逐行看：第一次派单用 `name="memo"` 创建实例并登记语义名；
第二次派单用 `resume="memo"` 按名寻址——两次调用的参数差异
在留档里直接可见。追问的内容只有
“我之前让你记的数字是几”，答案“42”不可能来自这次的输入，
只能来自同一实例延续下来的历史。记忆连续不是提示词技巧，
而是寻址能力：按名续接时，工作者看到自己此前的完整历史，
如同对话从未中断。

### 内容 / 状态分离

工作者返回的结果分两个字段：

```python
result = team.invoke("translator", prompt="…")
result.content      # 产出内容：最终答复或结构化数据
result.status       # 执行状态：完成 / 失败 / 被取消 / 被阻断
```

分离的理由：被取消的工作者可能已经产出大半内容——内容有效、
取消事实也有效。两个事实塞进一个字段，要么内容被污染（追加
“已取消”标记），要么状态丢失。调用方按字段分别处理：内容照
用，状态决定是否需要重试或降级。

### 遗忘的层级

实例不需要永久存在。层级化的遗忘设计：

| 层级 | 效果 | 适用 |
|---|---|---|
| 停止实例 | 释放运行资源，记录保留，可按名恢复 | 阶段性闲置 |
| 归档 | 名录移除，文件留档，不再可寻址 | 确认不再需要 |
| 物理删除 | 记录清除 | 合规要求；框架通常不提供，由应用层实现 |

本篇运行时在 `.flowing/` 下为实例保存记录：进程退出后记录仍在，同一个练习目录再次启动时可以按名找回。以下会话输入在同一次 REPL 运行中完成；若要验证跨进程续接，应在同一练习目录退出后再次运行同一命令，并只检查输出是否仍回答 42。

### 完整示例材料

以下文件构成完整的命名续接示例。请按相对文件名创建；输入与代表性会话结果已在上方给出。

```console
export DEEPSEEK_API_KEY=sk-your-key-here
```

**`providers.yaml`**

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

**`models.yaml`**

```yaml
deepseek-flash:
  provider: deepseek
  model: deepseek-v4-flash
```

**`model-tags.yaml`**

```yaml
tags:
  default: deepseek-flash
```

**`main.py`**

```python
from flowing import Runtime


async def main(resume: str | None = None) -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    # runtime.set_providers("@/providers.yaml")
    # runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    if resume is not None:
        await runtime.recover_agent(resume)
    else:
        await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

**`root.fya`**

```yaml
description: 编排者：通过命名唤起与续接验证子智能体记忆。
model_tag: default
tools:
  - subagent-invoke
subagents:
  - ./agents/assistant
---
$system_prompt:
你是编排者。用户要求 assistant 记住内容时，以 agent_type=assistant、name=memo
调用 subagent-invoke，并让它复述确认。用户追问时用 resume=memo 续接同一实例，
原样转达回答；除非用户明确要求新实例，否则不要另建实例。
```

**`agents/assistant/agent.fya`**

```yaml
description: 记忆助手：记住用户告知的内容，并在被问及时复述。
model_tag: default
---
$system_prompt:
你是记忆助手。当用户告诉你一个信息时，用一句话复述确认你记住了什么；
当用户问起时，准确复述最近记住的内容。回答控制在一句话以内。
```

按上方输入连续提问，第一次调用会创建名为 `memo` 的实例；第二次以 `resume="memo"` 指向同一实例。需要验证跨进程续接时，请在同一个练习目录内关闭并重新运行 REPL；本任务不要求重新运行该示例。

## 常见误区

1. **每次派单新建实例**。短期请求可以；多轮协作必须用命名续接，
   否则“记忆”无从谈起；
2. **取消即丢弃产出**。内容与状态分字段处理；大半完成的翻译
   仍可用；
3. **实例永不释放**。遗忘层级化；长期闲置的实例应停止，保留
   可恢复性而不占资源。

## 练习

1. 设计“翻译项目”的实例策略：哪些任务每次新建、哪些按名续接，
   语义名如何命名才能支持按项目找回；
2. 给 `content` / `status` 各写一个处理分支：内容如何交付、
   四种状态各自触发什么后续动作；
3. 在本篇示例里用“继续对话”方式重启进程后再次追问 memo，
   验证按名续接跨进程仍然成立，并说明是哪一层持久化支撑了它。

## 小结

1. 类型绑定定义可能集，实例化决定具体对象；两阶段独立演化；
2. 命名使实例可寻址，按名续接是记忆连续的技术基础；
3. 返回结果内容与执行状态分字段，分别消费；
4. 遗忘分层：停止（可恢复）→ 归档（留档）→ 物理删除（应用层）。
