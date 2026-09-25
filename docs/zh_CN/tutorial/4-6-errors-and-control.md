# 4-6 · 错误与控制

## 前置阅读

[1-4 钩子基础](1-4-hooks-basics.md)（拦截与观察）、[2-3 编写
ScriptTool](2-3-write-script-tool.md)（error 结果通道）、[3-1 消息树与
循环机制](3-1-message-tree-and-loop.md)（检查点语义）。完整演示程序与配置
内嵌如下；程序注入前两次 Provider 失败，后续成功调用真实配置的 Provider。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| 三类错误通道 | 工具业务错误（LLM 可见 error 产物）/ 编程错误（框架错误通道上抛）/ `Intercepted`（硬阻断 → blocked） |
| 可重试分类 | `RateLimitedError` / `ServerError` / `NetworkError` / `ProviderTimeoutError` 属可重试类——**分类是事实陈述，重试是策略** |
| `can_continue` | `on_provider_error` 决策字段：`False`（默认）回合以 error 收尾，`True` 同回合内重发 provider_gen |
| 取消语义 | `cancel`（整个 Agent）/ `cancel_children` / `cancel_by_tag` / `abort_turn`（只终止当前回合）/ `stop`（强制式，可覆写）——协作式信号，执行体在检查点响应 |
| `before_cancel` 拦截 | handler 抛 `Intercepted` → 取消被阻止、信号不置位，异常上抛给 `cancel()` 调用方 |

## 目标

错误分类与控制流的完全体：三类错误通道各走各的路；Provider 错误有
决策点；取消是协作式信号，且可以被拦住。

## 正文

### 三类错误通道

| 通道 | 形态 | 去向 |
|---|---|---|
| 工具业务错误 | `ToolResult(status="error")` | LLM 可见、可自我纠正；**不触发任何错误钩子**（2-3 实证） |
| 编程错误 | 框架异常（`FlowingError` 层次） | 直接上抛，无兜底钩子（import 期笔误刻意用内置 `ValueError`） |
| 硬阻断 | `raise Intercepted` | 当前操作作废 → blocked 结果与原因（1-4 实证） |

注册表一族：三注册表（Tool / Agent 类型 / Skill）解析未命中统一挂
`RegistryNotFoundError`、注册冲突挂 `RegistryConflictError`；绑定层别名
异常（`UnknownToolError` / `EntryNameConflictError`）与之分层。

### Provider 错误与 on_provider_error

Provider 调用期错误**一律经 `on_provider_error` 分发**（含
`ContextLengthError`——原样重发必然重现，但压缩历史 / 换大窗模型 /
仅观察都是 handler 的合法处置）。框架只读一个字段：`can_continue`
（默认 `False` → 回合以 error 收尾、Agent 存活；`True` → 同回合内
重发）。**`use_retry` 只是挂在这条通道上的一个可选 handler**——不启用
时通道照常分发，只是没人改写决策字段；`on_retry` 是纯观测通道（观测者
崩不影响重试决策，demo ①的写法即此）。

### 取消语义全集

粒度族：`abort_turn()`（只终止当前回合）< `cancel_by_tag()` /
`cancel_children()` < `cancel()`（整个 Agent）；`stop()` 是留给子类
覆写的强制式入口（默认等价 cancel）；`pause()` / `resume()`（含
`_recursive` 子树版）挂起工作循环检查点。取消是**协作式信号**：执行体
在检查点决定立即停止 / 忽略并完成 / 收尾后返回部分结果（3-1 实验 4
的 partial 保留即此）。**`before_cancel` 可阻止取消**：`Intercepted`
上抛给 `cancel()` 调用方、信号不置位——支付已提交等不可中断场景的
标准用法（demo ②实证）。

## 本篇不覆盖

- 检查点序列的实现细节——4-7；
- Provider 传输错误映射为错误分类的细节；
- `use_retry` / `use_compact` 的参数全集——5-3。

## 主线示例

`demo_control.py`：FlakyProvider 前两次调用抛 `RateLimitedError`
（第三次委托 DeepSeek）；`use_retry` 决策重试；`on_retry` 纯观测。
完整终端留档内嵌于下文；模型生成的回复可能因运行而异：

```console
$ uv run python demo_control.py
① 限流重试: status=completed
   on_retry 观测: [{'attempt': 1, 'error': 'RateLimitedError'}, {'attempt': 2, 'error': 'RateLimitedError'}]
   FlakyProvider 实际调用次数: 3（2 次失败 + 1 次成功）
② cancel 被 before_cancel 拦截: Intercepted(支付已提交：该回合不可取消)
   信号未置位——取消从未发生（不可中断场景的标准用法）
③ abort_turn: status=cancelled（回合终止，Agent 存活）
   下一回合照常: status=completed final='在，sleep 10 已'
```

读这段留档：①的机制点是“分类是事实、重试是策略”——
`RateLimitedError` 只是被标为可重试，真正让回合活下来的的是
`use_retry` 写的 `can_continue=True`；`on_retry` 收到的快照 dict 是纯
观测通道。②的拦截语义是“取消被阻止”而非“取消后回滚”——信号从未
置位。③的 `abort_turn` 与 `cancel` 的差别在粒度：只死当前回合，Agent
照常接下一回合。

### 完整可运行材料

将每个代码块分别保存为标题所示文件名，并放在同一工作目录。运行环境需
安装 Python 3.13+、Flowing 及 DeepSeek Provider 依赖。请设置环境变量
`DEEPSEEK_API_KEY`；本文不包含密钥。模型生成的回复文本可能与留档不同。

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
description: 错误与控制演示助手：配 bash 供取消实验。
model_tag: default
tools:
  - bash
---
$system_prompt:
你是简洁的演示助手，回答控制在一句话以内。
```

`providers.yaml`：

```yaml
deepseek:
  adapter: deepseek
  base_url: https://api.deepseek.com
  api_key: "{{env.DEEPSEEK_API_KEY}}"
flaky:
  adapter: flaky
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

`demo_control.py`：

```python
import asyncio

from flowing import launch
from flowing.composables import use_retry
from flowing.errors import Intercepted, RateLimitedError
from flowing.message import TextBlock, ToolCallBlock
from flowing.model import ModelConfig
from flowing.providers import Provider, register_provider
from flowing.providers.deepseek import DeepSeekProvider


@register_provider
class FlakyProvider(Provider):
    name = "flaky"
    calls = 0

    async def generate(self, context, model):
        type(self).calls += 1
        if type(self).calls <= 2:
            raise RateLimitedError(retry_after=0)
        real = DeepSeekProvider(self.config)
        response = await real.generate(context, model)
        message = response.message
        if message is not None and not any(
                isinstance(block, ToolCallBlock) for block in message.content
        ) and not any(
                isinstance(block, TextBlock) and block.text.strip()
                for block in message.content):
            message.content.append(TextBlock("（仅思考过程，无正文）"))
        return response


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")
    agent.model = ModelConfig(model="deepseek-v4-flash", provider="flaky")

    retries: list[dict] = []
    use_retry(agent, max_retries=3, base_delay=0.1)

    async def observe(agent_, snapshot):
        retries.append({"attempt": snapshot["attempt"],
                        "error": type(snapshot["error"]).__name__})
        return snapshot
    agent.hooks.on_retry(observe, by="demo")

    result = await agent.query("用一句话介绍你自己。")
    print(f"① 限流重试: status={result.status}")
    print(f"   on_retry 观测: {retries}")
    print(f"   FlakyProvider 实际调用次数: {FlakyProvider.calls}（2 次失败 + 1 次成功）")

    async def guard(agent_, context):
        raise Intercepted("支付已提交：该回合不可取消")
    agent.hooks.before_cancel(guard, by="guard")
    try:
        await agent.cancel()
        print("② cancel 未被阻止（与预期不符！）")
    except Intercepted as exc:
        print(f"② cancel 被 before_cancel 拦截: Intercepted({exc})")
        print("   信号未置位——取消从未发生（不可中断场景的标准用法）")
    agent.hooks.before_cancel.remove_by_owner("guard")

    task = asyncio.create_task(
        agent.query("再跑一次 bash sleep 10，然后回答。"))
    while agent.current_turn is None:
        await asyncio.sleep(0.05)
    await asyncio.sleep(3.0)
    agent.abort_turn()
    result = await task
    print(f"③ abort_turn: status={result.status}（回合终止，Agent 存活）")
    result = await agent.query("还在吗？")
    print(f"   下一回合照常: status={result.status} final={result.final_text[:12]!r}")
    await runtime.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
```

三个 query 输入——`用一句话介绍你自己。`、`再跑一次 bash sleep 10，然后回答。`
和 `还在吗？`——均已内嵌在程序中。运行命令为
`uv run python demo_control.py`。

## 小结

1. 三类错误通道：error 产物（LLM 可见）/ 编程错误（上抛）/ Intercepted
   （blocked）；
2. Provider 错误一律经 `on_provider_error` 分发，`can_continue` 是决策
   字段；`use_retry` 是策略不是机制；
3. 取消协作式、分粒度；`before_cancel` 的 Intercepted 阻止取消并上抛；
4. `on_retry` 等观测通道的崩溃不影响主决策。
