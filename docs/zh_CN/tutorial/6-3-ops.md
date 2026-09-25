# 6-3 · 运维

## 前置阅读

[1-8 运行时目录](1-8-runtime-and-agent-dirs.md)（目录表象）、[4-1 核心
状态与消息持久化](4-1-core-state-and-message-persistence.md)（崩溃恢
复——本篇的崩溃演练直接引用）、[4-8 Runtime 机制全量](4-8-runtime-mechanics.md)
（池与懒加载）。本篇内联完整相关配置、程序、运行输入与输出。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 配置链 | 框架默认值 < 项目级 `@/config.yaml` < 用户级 `$FLOWING_CONFIG_HOME/config.yaml`；`set_config` 覆盖层最优先（不持久化） |
| 配置文件优先级 | 模型三文件的定位顺序：`FLOWING_PROVIDERS_PATH` / `FLOWING_MODELS_PATH` / `FLOWING_MODEL_TAGS` 环境变量 > 默认用户配置位置；`set_providers` 等在 mount 前编程覆盖 |
| 遗忘三档 | destroy（丢实例留记录）→ archive（名录移除、文件留档）→ 物理删除（框架不提供） |
| `archive_orphans()` | 归档 parent 悬空的池条目（崩溃遗留的孤儿） |
| `_unstable` | 实验命名空间：功能确定但接口 / 格式不冻结，任何版本可改可删，下游禁止依赖 |

## 目标

上线后的观测与运维：配置链、模型接入的运维面、池与恢复、快照审计。

## 正文

### 配置链

```
set_config 覆盖层（进程级，最优先）
  > 用户级  $FLOWING_CONFIG_HOME/config.yaml
  > 项目级  @/config.yaml
  > 框架默认（如 agent.timeout=60）
```

`FLOWING_*` 环境变量由各自消费点直读（路径 / 开关类），不进配置链。
构造期浅合并完成前调用 `get_config` → `ConfigNotReadyError`（import
期笔误的典型死法）。

### 模型接入的运维面

0-1 的两跳解析与 3-4 的标签设计是“使用面”；运维面是配置管理：
三文件默认位于用户配置位置，经
`FLOWING_PROVIDERS_PATH` / `FLOWING_MODELS_PATH` / `FLOWING_MODEL_TAGS`
重定向；`set_providers` / `set_models` / `set_model_tags` 在 mount 前
编程覆盖。完整默认配置与替代配置见下方内联示例。

### 池、恢复与遗忘

- 池名录（`core.jsonl`）是智能体池 key 的唯一权威（4-1）；构造期扫描
  不实例化；
- `recover_agent(id)` 亲代链自动向上递归恢复，子代惰性现场恢复；
- 遗忘三档：destroy（演示反复使用）→ `archive_agent`（名录移除、文件
  留档）→ 物理删除（应用层自做，建议先归档再删）；
- `archive_orphans()` 清理崩溃遗留的孤儿条目。

### 观测与日志

`snapshot()` 两级拉取（4-8）：运行时观测的最稳入口。结构化日志落盘
仍属实验性功能——**边界声明**：`_unstable` 不属跨版本稳
定契约，格式可能变化，下游发布物禁止依赖。

## 本篇不覆盖

- 监控指标导出（Prometheus 等）——框架不含，宿主自行挂钩子；
- 备份策略——`persist_dir` 的文件级备份由运维定；
- 多机部署——Flowing 是轻量式框架，多机部署由应用层拆分处理。

## 主线示例

以下留档记录了一次运维运行：

```console
$ uv run python demo_ops.py
== ① 配置链三层 ==
   项目级 @/config.yaml 覆盖框架默认: agent.timeout = 45（框架默认 60）
   set_config 覆盖层最优先: agent.timeout = 90
   FLOWING_CONFIG_HOME 用户级: 默认用户配置目录（未设置）
== ② 模型接入来源覆盖 ==
   默认 provider 候选: ['deepseek']
   优先级：FLOWING_PROVIDERS_PATH 环境变量 > 默认路径；set_providers 在 mount 前编程覆盖
   内联替代配置的条目名为 deepseek-alt；在 mount 前调用 set_providers('@/alt-providers.yaml') 即可选择它
== ③ 池 / 归档（遗忘三档之二）==
   池名录（含子 Agent）: ['<运行时生成的子 Agent ID>', 'agent-main']
   archive_agent 后 get_agent → None（运行时遗忘，文件留档）
   destroy 档（丢实例留记录）→ archive 档（名录移除）；物理删除框架不提供，应用层自做
== ④ snapshot 审计 ==
   nodes=['agent-main', 'runtime-0']
   config_overrides={'agent.timeout': 90}
   崩溃演练（kill -9 → 重启恢复）见 4-1；结构化日志仍为实验性功能，格式尚未冻结
```

崩溃演练（kill -9 → 重启恢复 → 半截 turn 重放）见 [4-1](4-1-core-state-and-message-persistence.md)
——恢复的正确性由“重放 = 顺序重放结果”保证，运维只需要把
`persist_dir` 保住。

**完整可运行材料。**

将每个代码块按标题保存到同一个 Flowing 项目中。文件名均为项目根级
相对名称；Provider 凭证保留为环境变量占位符。留档中的子 Agent ID
已归一化，因为它由运行时生成。

### Runtime 入口：`main.py`

```python
from flowing import Runtime


async def main() -> Runtime:
    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_providers("@/providers.yaml")
    runtime.set_models("@/models.yaml")
    runtime.set_model_tags("@/model-tags.yaml")
    await runtime.mount("@/root.fya", agent_id="agent-main")
    return runtime
```

### 项目配置：`config.yaml`

```yaml
agent:
  timeout: 45
```

### 默认 Provider 配置：`providers.yaml`

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

### 替代 Provider 配置：`alt-providers.yaml`

```yaml
deepseek-alt:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
```

要使用替代 Provider 条目，请在 Runtime 入口中、挂载 Agent 之前，用下面的
调用替换默认 Provider 调用：

```python
runtime.set_providers("@/alt-providers.yaml")
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
description: 运维演示助手：最小问答。
model_tag: default
---
$system_prompt:
你是简洁的中文助手。
```

### 运维程序与输入：`demo_ops.py`

```python
import asyncio
import os

from flowing import launch


async def main() -> None:
    print("== ① 配置链三层 ==")
    runtime = await launch(".")
    print(f"   项目级 @/config.yaml 覆盖框架默认: agent.timeout = "
          f"{runtime.get_config('agent.timeout')}（框架默认 60）")
    runtime.set_config("agent.timeout", 90)
    print(f"   set_config 覆盖层最优先: agent.timeout = "
          f"{runtime.get_config('agent.timeout')}")
    print(f"   FLOWING_CONFIG_HOME 用户级: "
          f"{os.environ.get('FLOWING_CONFIG_HOME', '默认用户配置目录（未设置）')}")

    print("== ② 模型接入来源覆盖 ==")
    print(f"   默认 provider 候选: "
          f"{sorted(runtime.provider_registry._candidates)}")
    print("   优先级：FLOWING_PROVIDERS_PATH 环境变量 > 默认路径；"
          "set_providers 在 mount 前编程覆盖")
    print("   内联替代配置的条目名为 deepseek-alt；在 mount 前调用 "
          "set_providers('@/alt-providers.yaml') 即可选择它")

    print("== ③ 池 / 归档（遗忘三档之二）==")
    root = await runtime.get_agent("agent-main")
    child = await root.create_subagent("builtin::explore-agent", name="exp-1")
    print(f"   池名录（含子 Agent）: {sorted(runtime._agent_pool)}")
    await runtime.archive_agent(child.node_id)
    print(f"   archive_agent 后 get_agent → "
          f"{await runtime.get_agent(child.node_id)}（运行时遗忘，文件留档）")
    print("   destroy 档（丢实例留记录）→ archive 档（名录移除）；"
          "物理删除框架不提供，应用层自做")

    print("== ④ snapshot 审计 ==")
    snapshot = runtime.snapshot()
    print(f"   nodes={sorted(snapshot.nodes)}")
    print(f"   config_overrides={snapshot.config_overrides}")
    print("   崩溃演练（kill -9 → 重启恢复）见 4-1；"
          "结构化日志仍为实验性功能，格式尚未冻结")
    await runtime.shutdown()


asyncio.run(main())
```

在保存这些内联文件的工作目录运行 `uv run python demo_ops.py`。Provider
凭证通过环境变量 `DEEPSEEK_API_KEY` 提供；文档不包含凭证值或本机路径。
演示不调用模型，因此没有模型生成文本输入或输出。

## 小结

1. 配置链四层：默认 < 项目 < 用户 < set_config（进程级最优先）；
2. 模型三文件的位置可由环境变量重定向，也可在程序中覆盖；
3. 遗忘三档：destroy / archive /（物理删除归应用层）；孤儿用
   `archive_orphans()`；
4. snapshot 是观测主通道；`_unstable` 的边界声明务必遵守。
