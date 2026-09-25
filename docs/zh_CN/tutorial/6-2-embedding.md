# 6-2 · 嵌入宿主应用

## 前置阅读

[4-8 Runtime 机制全量](4-8-runtime-mechanics.md)（`launch` 与 `@` 隔离）、
[1-4 钩子基础](1-4-hooks-basics.md)（`before_tool_call` 审批闸）。本篇的
内联完整 Runtime 配置、宿主程序、提示词、输入与留档输出。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 嵌入 | 宿主进程自己持有 `launch` 返回的 Runtime：输入走 `query` / `message` / `steer`，输出走钩子订阅，配置经 `set_config` / `provide` 对接 |
| shutdown 责任 | write-behind 需要排空——不 `shutdown()` 直接退进程会丢尾部记录；CLI 子命令替你关，嵌入时责任转移给宿主 |
| 薄 `main()` | 嵌入形态的 main() 可以只装配不交互（甚至不 mount，宿主按需 `create_agent`） |

## 目标

把 flowing 嵌入既有大应用：对接面四条（配置 / 共享对象 / 输出 / 审批）
与一条责任（shutdown）。

## 正文

### 嵌入 = 持有 Runtime + 四条对接

```python
import asyncio

from flowing import launch


async def host_integration() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    runtime.set_config("agent.timeout", 90)

    async def on_delta(agent_, delta):
        return delta
    agent.hooks.on_provider_delta["_turn"](on_delta, by="host-ui")

    connection_pool = object()
    runtime.provide("workspace_root", "<workspace-root>")
    runtime.register_resource("db", connection_pool)

    async def approve(agent_, tool_call):
        return tool_call
    agent.hooks.before_tool_call(approve, by="host-approval")

    await runtime.shutdown()


asyncio.run(host_integration())
```

此可运行初始化过程装配了四类对接面。下方的宿主示例还会发送查询，使
钩子实际收到流式输出与审批事件。

多 Runtime 共存靠 `@` 上下文 per-Task 隔离（4-8 实证）；**强隔离**（多
租户 / A/B）用子进程，不靠多 Runtime。

### 三条边界

1. **launch 返回后模块级 `resolve()` 不可用**——用 `runtime.resolve_path`
   （4-8 实证）；
2. **`shutdown()` 责任在宿主**——CLI 子命令替你关，嵌入时自己关
   （demo ⑤）；
3. **同步框架宿主**（Django 等）需独立线程跑 loop，经
   `run_coroutine_threadsafe` 投递——不要在框架的同步线程里直接
   `await`。

## 本篇不覆盖

- WebSocket / SSE 的具体协议设计——你的应用形态决定；
- 多进程部署与负载均衡——运维话题超出教程范围；
- 权限分级（谁能挂钩子 / 发消息）——框架不做权限，由宿主在钩子层自行
  实现。

## 主线示例

以下留档记录了一次由宿主驱动的运行：

```console
$ uv run python demo_embed.py
① 宿主 set_config 覆盖: agent.timeout = 90

[宿主审批] bash args={'command': 'echo 宿主你好'}
[宿主审批] 通过
输出为：宿主你好
[宿主] 回合 status=completed
[宿主] shutdown 完成：尾部记录已排空
```

读这段留档：配置覆盖层生效（①）；工具的调用在 execute 之前抵达宿主
审批 handler（③的耗时确认模拟人工）；流式正文经 `on_provider_delta`
上屏；回合完成后宿主显式 `shutdown()` 排空 write-behind 尾部——四
条对接与一条责任全部落位。

**完整可运行材料。**

将每个代码块按标题保存到同一个 Flowing 项目中。API 凭证只以环境变量
占位符表示。以下留档来自一次真实模型运行；自然语言措辞可能变化，
bash 命令输出与宿主生命周期则应保持一致。

### Runtime 入口：`main.py`

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

### Agent 定义与宿主提示词：`root.fya`

```yaml
description: 嵌入演示助手：供宿主驱动的最小 Agent。
model_tag: default
tools:
  - bash
---
$system_prompt:
你是宿主应用内的助手，回答控制在一句话以内。
```

### 宿主程序、输入与关闭：`demo_embed.py`

```python
import asyncio

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    runtime.set_config("agent.timeout", 90)
    print(f"① 宿主 set_config 覆盖: agent.timeout = "
          f"{runtime.get_config('agent.timeout')}")

    async def on_delta(agent_, delta):
        if delta.kind == "text" and delta.by == "_turn":
            print(delta.text, end="", flush=True)
        return delta
    agent.hooks.on_provider_delta["_turn"](on_delta, by="host-ui")

    async def approve(agent_, tool_call):
        print(f"\n[宿主审批] {tool_call.name} args={tool_call.args}")
        await asyncio.sleep(0.2)
        print("[宿主审批] 通过")
        return tool_call
    agent.hooks.before_tool_call(approve, by="host-approval")

    result = await agent.query("用 bash 运行 echo 宿主你好，把输出原样告诉我。")
    print(f"\n[宿主] 回合 status={result.status}")
    await runtime.shutdown()
    print("[宿主] shutdown 完成：尾部记录已排空")


asyncio.run(main())
```

宿主输入就是程序中的 `agent.query` 字符串。在保存内联材料的工作目录
运行 `uv run python demo_embed.py`。通过环境变量提供 `DEEPSEEK_API_KEY`；
不要把凭证写进配置块。

## 小结

1. 嵌入 = 持有 launch 的 Runtime；薄 main() 只装配；
2. 四条对接：set_config / provide+Resource / 钩子订阅 / before_tool_call
   审批；
3. `@` per-Task 隔离多 Runtime；强隔离用子进程；
4. shutdown 责任在宿主——write-behind 需要排空。
