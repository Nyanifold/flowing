# 4-7 · Agent 机制全量

## 前置阅读

[3-1 消息树与循环机制](3-1-message-tree-and-loop.md)（检查点语义）、
[4-6 错误与控制](4-6-errors-and-control.md)（取消决策面）。四个实验的完整
程序与配置内嵌如下。

## 本篇名词表

| 名词 | 一句话定义 |
|---|---|
| `_dequeue` 覆写点 | 出队扩展点：构造一个逻辑回合的消费批次（默认 = 队首紧急连续段 + 第一条非紧急消息）——drain 合并 / 按来源分组等调度策略的落点 |
| `_run_turn` 内层 | 回合执行主体：检查点序列 → provider_gen → 工具并行批次 → 收尾组装 `TurnResult` |
| watch 通道 | `Agent.__setattr__` 拦截赋值、以 `FieldUpdate(new, old)` 快照 fire-and-forget 通知的观测机制（纯观察，改写无效） |
| 副线（side_query） | 不经队列、不挂树、不落盘的独立调用——上下文现场组装、结果只回调用方 |

## 目标

Agent 类的机制全量：标准循环之内，这个类还藏着哪些机制——实现者视角
的伏笔总回收。

## 正文

### 工作循环与出队扩展点

常驻工作循环的每次迭代：`msgs = await self._dequeue()` →
`_run_turn(msgs, waiters)`。`_dequeue` 是**显式扩展点**（覆写管“多条 /
策略”，`on_dequeue` 钩子管“观察 / 变换”）。注意覆写语义：循环按次
查属性——patch 从下一次出队生效；**restore 不撤回已在途的出队调用**
（循环正阻塞在旧方法的 `wait_not_empty` 里，demo ②的“本批 1 条”即
此现象的实证）。

### _run_turn 内层（顺序为不变量）

创建 `TurnContext` → 置 `current_turn` → `before_turn`（拦截则批次
丢弃不落盘）→ 批次逐条挂树 → 内层循环（检查点：pause gate → urgent
吸收 → abort 判定 → `provider_gen` → 同一响应内**全部 tool_call 并行
执行**（asyncio.gather，结果按响应顺序挂树）→ finish 判定）→ `finally`
：释放 `current_turn` → `after_turn`（唯一全路径收尾点）→
`build_turn_result` 组装 → 写 `last_result` → resolve 全部等待者
（drain 合并的批次共享同一 `TurnResult`）。

`TurnResult` 构建口径（demo ②）：`token_usage` 对
`TurnContext.usages` 逐字段求和（raw 不聚合），无成功调用时为
`None`；`turn.message_ids` 回溯本回合挂树的消息。

### __setattr__ / watch 机制全貌

每次实例属性赋值：构造 `FieldUpdate(name, old, new)` 快照 →
fire-and-forget 通知（不 await、异常不影响赋值）→ 写入。要点：watcher
收到的是**赋值那一刻的自洽快照**，执行时序不保证；纯观察（改写 `new`
无效）；state 写不触发；`_` 前缀骨架字段不经过；`model_tag` 赋值额外
触发重新解析（且不再二次触发 model 的 watcher）。

### 全入口巡礼（机制底座）

| 入口 | 底座 |
|---|---|
| `query` | 打包 + 入队 + 绑定等待 Future（`_pending_turns`） |
| `message` / `enqueue_message` | 纯入队（`on_enqueue` 可拒绝/改写） |
| `steer` | STEER 优先级消息（检查点吸收，3-1） |
| `side_query` | 不经队列：现场组装 + 非流式调用 + `by="_side"`，**零树痕迹**（demo ④） |
| `invoke_subagent` | 两段式管线（2-2）+ 亲代钩子 |

### state 袋的实例机制

`agent.state` / 命名袋的每次 set/delete：JSON 校验（fail fast）→ 更
新 `_persisted` 内存权威 → `submit` 写透（write-behind 排队）→ 追加行
计数超阈值触发整文件压缩请求（4-1 的排空屏障钉在压缩前）。

### 快照投影的执行期可见性

`current_turn` 只在回合执行期间非 None（快照层同期可见）；回合收尾
先释放身份牌再跑 `after_turn`——钩子期间快照里该字段已为 None。观测
与控制的边界：**快照是观察通道不是控制依据**。

## 本篇不覆盖

- 取消的决策面与语义全集——4-6；
- 子智能体 catalog / 绑定层——4-4；
- 恢复管线——4-1。

## 主线示例

`demo_internals.py` 四个实验；完整终端留档内嵌于下文：

```console
$ uv run python demo_internals.py
== ① 自定义 _dequeue：drain 合并 ==
   [_dequeue] drain 合并：本批 3 条消息进一个回合
== ② 回合收尾：TurnResult 构建 ==
   [_dequeue] drain 合并：本批 1 条消息进一个回合
   status=completed turn.message_ids=['5', '6']
   token_usage 聚合=164 （TurnContext.usages 逐字段求和）
== ③ watch 属性联动 ==
   watch(locale) 收到赋值事件: [(None, 'zh'), ('zh', 'en')]
== ④ side_query 副线 ==
   side_query 返回='ok'；树节点数 6 → 6（不变 = 副线零痕迹）
```

读这四个实验：①一次出队吃掉三条消息（drain 合并策略的最小实现）；
②的那次“本批 1 条”是 restore 语义的现象学证据——工作循环还阻塞在
旧出队方法的等待里；同一回合的 message_ids 与聚合用量展示了
`TurnResult` 的组装口径；③的 `(old, new)` 快照序列是 watch 契约
（首次赋值 old=None）；④证明副线的零痕迹——总结数不变、无挂树、无
落盘。

### 完整可运行材料

将每个代码块分别保存为标题所示文件名，并放在同一工作目录。程序的 query
与 side-query 实验会调用 Provider，需设置环境变量 `DEEPSEEK_API_KEY`；
文档不含凭证。Provider 生成的 token 数和回复可能变化。

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
description: Agent 机制演示助手：最小问答。
model_tag: default
---
$system_prompt:
你是简洁的中文助手，回答控制在一句话以内。
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

`demo_internals.py`：

```python
import asyncio
import types

from flowing import launch


async def main() -> None:
    runtime = await launch(".")
    agent = await runtime.get_agent("agent-main")

    print("== ① 自定义 _dequeue：drain 合并 ==")
    original = agent._dequeue

    async def drain_all(self):
        await self._message_queue.wait_not_empty()
        batch = await self._message_queue.drain_all()
        print(f"   [_dequeue] drain 合并：本批 {len(batch)} 条消息进一个回合")
        return batch

    agent._dequeue = types.MethodType(drain_all, agent)
    for text in ["第一句", "第二句", "第三句"]:
        await agent.message(text)
    while agent.current_turn is None:
        await asyncio.sleep(0.1)
    while agent.current_turn is not None:
        await asyncio.sleep(0.2)
    await asyncio.sleep(0.3)

    print("== ② 回合收尾：TurnResult 构建 ==")
    result = await agent.query("用一句话介绍你自己。")
    print(f"   status={result.status} turn.message_ids={result.turn.message_ids}")
    print(f"   token_usage 聚合={result.token_usage.total_tokens if result.token_usage else None} "
          "（TurnContext.usages 逐字段求和）")
    agent._dequeue = original

    print("== ③ watch 属性联动 ==")
    seen: list[tuple] = []
    agent.watch("locale", lambda new, old: seen.append((old, new)))
    agent.locale = "zh"
    agent.locale = "en"
    await asyncio.sleep(0.5)
    print(f"   watch(locale) 收到赋值事件: {seen}")

    print("== ④ side_query 副线 ==")
    before = len(agent._messages)
    side = await agent.side_query("只回复：ok")
    after = len(agent._messages)
    print(f"   side_query 返回={side!r}；树节点数 {before} → {after}（不变 = 副线零痕迹）")
    await runtime.shutdown()


asyncio.run(main())
```

使用 `uv run python demo_internals.py` 运行。留档输出见上文；完整输入即为
程序中的三条消息。

## 小结

1. `_dequeue` 是调度策略的扩展点；覆写/还原都从“下一次出队”生效；
2. `_run_turn` 内层：检查点序列 + 工具并行批次 + finally 收尾组装；
3. watch 是赋值事件的快照观测；side_query 是零痕迹副线；
4. state 写透 + 阈值压缩；快照只观察不控制。
