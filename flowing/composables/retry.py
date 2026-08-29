"""flowing.composables.retry —— 内置重试 Composable（可选、非默认）。

.. rubric:: 功能介绍

本模块提供内置 Composable ``use_retry()``（另一个内置 Composable 是
:func:`flowing.composables.compact.use_compact`）。它在 Agent 实例的
``on_provider_error`` 钩子点上注册一个重试决策 handler（``by="retry"``），为 LLM
调用失败提供可选的退避重试能力。本模块属**应用层 / 内置 Composable 层**：
随 ``flowing`` 包发布，但**不自动启用**，必须由 Agent 开发者在 ``setup()``
中显式调用。

.. rubric:: Composable 模式总述（模块级契约）

**Composable 是什么**：一个形如 ``use_xxx(agent, ...)`` 的普通函数，
以 Agent 实例为第一参数，在实例上就地注册钩子 handler、provide 值或
声明扩展钩子点。**同步还是 async 由内部是否确需 ``await`` 决定**
（M-91 范式裁决：纯注册型写同步 ``def``，不为形态统一强行 async，
见 :mod:`flowing.composables` 行为规约）。Composable **不是类、不是
Plugin**：不做全局注册、不经过 ``runtime.use()``、不需要 ``install()``，
调用即生效，作用域严格限于传入的那一个 Agent 实例。

**双层启用中的位置**：Flowing 的能力启用分两层——

- 阶段一 ``runtime.use(plugin)`` → ``plugin.install(runtime)``：注册全局能力
  （工具、provide 值、配置命名空间）。Composable **不参与**这一阶段。
- 阶段二 Agent 的 ``setup()`` 中调用 ``use_xxx(self)``：为该实例启用能力。
  ``use_retry`` 是纯阶段二机制——它只往**核心已有**的 ``on_provider_error``
  钩子点上注册 handler，连 ``hooks.declare()`` 都不需要。

**零开销不变量**：未调用 ``use_retry()`` 的 Agent 不持有任何重试相关状态、
不注册任何 handler、不产生任何计时或分支开销——错误路径与不调用时逐字节
等价：``query()`` 异常 → ``on_provider_error`` 分发（空链）→ ``can_continue``
保持 ``False`` → 逻辑 Turn 终止、Agent 存活。「没启用」是「代码路径从没
存在过」，不是「被 skip」。

**机制 vs 策略**：框架核心只保留机制——错误类型定义（可重试 /
不可重试的分类事实）、``on_provider_error`` 钩子点的分发、``can_continue``
决策模型。重试次数、退避模型、等待时长全部是策略，由本模块提供**一份可
整体替换的默认实现**。Agent 开发者可以：调参（``use_retry(self,
max_retries=10)``）、整组移除后注册自己的 handler、或完全不调用。

**内置范围声明**：初版框架内置的 Composable 共两个——``use_retry`` 与
``use_compact``（:mod:`flowing.composables.compact`）。设计
草稿场景篇中出现的 ``use_logging`` / ``use_guardrail`` / ``use_audit_trail``
等均为应用层写法示例，不属框架内置承诺；本包不为它们预留符号。

.. rubric:: 设计动机

早期设计曾把重试硬编码在框架核心 ``provider_gen()`` 内（固定次数、固定指数退避、
错误→动作映射表）。该方案的根本问题是：Agent 开发者既不能替换策略，也不
能选择「不重试直接失败」。定稿裁决（覆盖旧内置重试设计）：

1. 错误分类是**事实**（上下文溢出重试无意义、凭证错误重试无意义），留在核心；
2. 「该不该重试、等多久、试几次」是**策略**，迁出核心，由 Composable 承载；
3. 决策通道压缩为 ``ProviderErrorContext.can_continue: bool`` 一个布尔值——
   handler 内部自行 ``await asyncio.sleep(...)``、自行决定是否改
   ``agent.model``，回合层面不需要任何重试状态机。

.. rubric:: 使用示例

.. code-block:: python

    # 手写子类：setup() 中启用
    from flowing import Agent
    from flowing.composables.retry import use_retry

    class MyAgent(Agent):
        system_prompt = "你是一个助手。"

        async def setup(self) -> None:
            use_retry(self, max_retries=3, base_delay=1.0)

.. code-block:: text

    # order-agent/agent.fya —— .fya 声明式入口，经 $script 块在 setup 中等价调用
    ---
    name: my-agent
    system_prompt: 你是一个助手。
    ---
    ```$script
    from flowing.composables.retry import use_retry

    async def setup(self) -> None:
        use_retry(self, max_retries=3)
    ```

.. rubric:: 行为规约（模块级）

- 期待行为：调用 ``use_retry(agent)`` 后，该 Agent 的 ``on_provider_error``
  钩子链上追加一个 ``by="retry"`` 的 handler；此后该 Agent 的 LLM 调用
  抛出可重试类异常时，handler 内部 sleep 后置 ``can_continue=True``，
  回合层面 ``continue`` 重试。
- 非行为：本模块不定义新的钩子点、不注册工具、不 provide 值、不修改
  ``agent.model``、不持久化任何状态、不感知消息级树（重试发生在逻辑
  Turn 内部，不产生任何消息）。
- 边缘情况：重复调用不做幂等去重——每次调用按注册语义各自独立叠加
  一组 handler（允许以不同参数多次启用，见 ``use_retry`` 条目）；
  重试计数器是 handler 闭包内部状态，不落盘、不进 ``ProviderErrorContext``、
  崩溃后不恢复（崩溃恢复以消息级树的 ``turn_end`` 边界为准，与重试无关）。
- 不变量：``use_retry`` 注册的 handler 遵守统一 handler 契约——签名为
  ``(agent, value) -> value``，必须返回 ``ctx``；不 ``raise Intercepted``
  （错误钩子无阻断语义，决策只通过 ``can_continue`` 表达）。

.. seealso::

    :class:`flowing.agent.Agent` —— Composable 的作用目标与 ``setup()`` 宿主。
    :class:`flowing.agent.TurnContext` —— 逻辑 Turn 的执行期载体；重试发生在其内循环中。
    :class:`flowing.hooks.HookRegistry` —— ``on_provider_error`` 钩子点的容器。
    :class:`flowing.hooks.HookEntry` —— ``by`` / ``tags`` 元信息的载体。
    :class:`flowing.errors.FlowingError` —— 统一异常层次的根。
    :mod:`flowing.plugins.skills` —— 另一个内置能力的阶段二入口（``use_skill``）。
"""

from typing import Literal

import asyncio
import logging

from flowing.agent import Agent
from flowing.errors import (
    AuthenticationError,
    ContentPolicyError,
    InvalidRequestError,
    NetworkError,
    QuotaExhaustedError,
    RateLimitedError,
    RequestTooLargeError,
    ServerError,
    ProviderTimeoutError,
)
from flowing.agent import ProviderErrorContext

__all__ = ["use_retry", "MAX_RETRY_DELAY", "RETRYABLE_ERRORS", "NON_RETRYABLE_ERRORS"]

_logger = logging.getLogger(__name__)

MAX_RETRY_DELAY: float = 60.0
"""单次重试等待的硬上限（秒）。

无论退避模型如何增长，单次 ``asyncio.sleep`` 的时长都不超过该值。
动机：限流场景下 2 的幂增长很快失去意义——超过一分钟的等待对单机
小工具定位没有价值，反而让 Turn 看起来像挂起。这是策略的一部分，
随 ``use_retry`` 整体可被替换，不属框架核心机制。

行为边界：仅作用于 ``use_retry`` 注册的默认 handler；用户自注册
的 ``on_provider_error`` handler 不受此值约束。

.. seealso:: :func:`flowing.composables.retry.use_retry`
"""

RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    RateLimitedError,
    ServerError,
    NetworkError,
    ProviderTimeoutError,
)
"""默认 handler 视为可重试的异常类型元组。

即「限流 + 基础设施瞬时故障」四类：:class:`flowing.errors.RateLimitedError`、
:class:`flowing.errors.ServerError`、:class:`flowing.errors.NetworkError`、
:class:`flowing.errors.ProviderTimeoutError`。

行为边界：这是**策略清单而非机制清单**——机制只保证这些类型由 Provider
adapter 在对应故障时抛出、且会经过 ``on_provider_error`` 分发；「对它们
重试」是 ``use_retry`` 的选择，用户 handler 可自由采用不同清单。
不在此元组中的异常类型（含所有未知异常）默认 handler 一律不重试。

.. seealso:: :data:`flowing.composables.retry.NON_RETRYABLE_ERRORS`
"""

NON_RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    AuthenticationError,
    InvalidRequestError,
    ContentPolicyError,
    QuotaExhaustedError,
    RequestTooLargeError,
)
"""默认 handler 显式不重试的异常类型元组。

凭证类、请求类与配额类错误：:class:`flowing.errors.AuthenticationError`、
:class:`flowing.errors.InvalidRequestError`、
:class:`flowing.errors.ContentPolicyError`、
:class:`flowing.errors.QuotaExhaustedError`、
:class:`flowing.errors.RequestTooLargeError`。

行为边界：这些异常到达 handler 时，handler 原样返回 ``ctx``
（``can_continue`` 保持 ``False``），不 sleep、不计数、不改写任何字段
——重试它们不改变结果（凭证不会因等待而变对，请求体不会因等待而合法，
余额不会因等待而变多）。``RequestTooLargeError`` 在列指默认 handler 不
重试；媒体剥离后重发属应用层自定义 handler 的职责。注意
:class:`flowing.errors.ContextLengthError` **不在此列也无需在列**：
它在核心分发之前就被直接上抛，永远不会到达 ``on_provider_error``。

.. seealso:: :data:`flowing.composables.retry.RETRYABLE_ERRORS`
"""


def use_retry(
    agent: Agent,
    max_retries: int = 3,
    base_delay: float = 1.0,
    backoff: Literal["exponential", "fixed"] = "exponential",
) -> None:
    """为 Agent 实例启用 LLM 调用失败的重试策略（可选、非默认）。

    .. rubric:: 功能介绍

    在 ``agent.hooks.on_provider_error`` 上注册一个重试决策 handler
    （``by="retry"``），并附带注册一个
    ``before_turn`` 归零 handler 用于重置重试计数。调用后，该 Agent 的
    LLM 调用抛出可重试类异常时将按参数指定的退避模型等待并重试，直至
    成功或达到 ``max_retries`` 上限。

    属**应用层 / 内置 Composable**；是双层启用的阶段二入口，只能在
    ``setup()``（或其实例存活期内的任意代码）中对已创建实例调用。

    .. rubric:: 设计动机

    「重试」是策略不是机制：重试几次、等什么退避、对哪些错误重试，都不
    存在唯一正确答案，因此核心只提供 ``on_provider_error`` 分发与
    ``can_continue`` 决策通道，本函数提供**一份可整体替换的默认策略**。
    注册形态（普通函数 + ``by="retry"``）刻意与
    用户手写 handler 完全同构——默认实现不享有任何特权，替换它不需要
    框架开后门，只需 ``remove_by_owner("retry")`` 后注册自己的 handler。

    不带 ``tags``（M-93 裁决）：retry 自身不需要按 tag 成组管理，
    ``by="retry"`` 对他人（``remove_by_owner`` / ``disable_by_owner``）
    已足够；不为用不到的管理维度预占元信息。

    .. rubric:: 使用示例

    .. code-block:: python

        # 1. 最小启用（默认参数：3 次重试、1s 基准、指数退避）
        async def setup(self) -> None:
            use_retry(self)

        # 2. 调参
        async def setup(self) -> None:
            use_retry(self, max_retries=10, base_delay=0.5, backoff="fixed")

        # 3. 整体替换：移除默认 handler，注册自己的策略
        async def setup(self) -> None:
            use_retry(self)
            self.hooks.on_provider_error.remove_by_owner("retry")

            async def my_policy(agent: Agent, ctx: ProviderErrorContext) -> ProviderErrorContext:
                if isinstance(ctx.error, RateLimitedError):
                    await asyncio.sleep(5.0)
                    ctx.can_continue = True
                return ctx

            self.hooks.on_provider_error(my_policy, by="myapp")

        # 4. 完全不调用（零开销）：provider_gen() 异常 → 空钩子链 →
        #    can_continue 保持 False → 逻辑 Turn 终止，Agent 存活等待下一条消息。

    .. code-block:: text

        # order.fya —— .fya 声明式入口的等价写法
        ---
        name: order-agent
        system_prompt: 你是订单处理助手。
        ---
        ```$script
        from flowing.composables.retry import use_retry

        async def setup(self) -> None:
            use_retry(self, max_retries=5)
        ```

    .. rubric:: 行为规约

    **参数语义**：

    - ``agent``：目标 Agent 实例。必须是已完成 ``__init__`` 的实例
      （``hooks`` 注册表已预填核心钩子点）；在 ``setup()`` 中传入 ``self``
      是标准用法。
    - ``max_retries``：单个逻辑 Turn 内允许的最大重试次数。计数从 1 起：
      首次失败为第 1 次尝试，``attempt <= max_retries`` 时才放行重试，
      因此默认值 3 表示「初始调用 + 至多 3 次重试」至多 4 次 LLM 调用。
    - ``base_delay``：退避基准秒数。``backoff="fixed"`` 时即每次等待时长。
    - ``backoff``：退避模型，仅接受 ``"exponential"`` / ``"fixed"``。
      增长规则见下「延迟计算」。

    **注册形态（时序契约）**：本函数依次完成三次注册/声明：

    0. ``agent.hooks.declare("on_retry", by="retry")`` —— 声明本 Agent
       实例的重试观测钩子点（同名同 ``by`` 幂等，重复声明返回同一
       ``HookList``）。
    1. ``agent.hooks.before_turn(_reset, by="retry")``
       —— 归零 handler：每个逻辑 Turn 开始时把闭包内的 attempt 计数器
       重置为 0。value（``TurnContext``）原样透传，纯观察不干预。
    2. ``agent.hooks.on_provider_error(_handler, by="retry")``
       —— 决策 handler：见下。

    两次注册共用 ``by="retry"``，因此 ``remove_by_owner("retry")``  /
    ``disable_by_owner("retry")`` 会同时作用于两者，整组替换无残留。
    ``on_retry`` 钩子点本身属声明而非 handler，不受 remove 影响。

    **重试观测信号（M-87 最终裁决）**：重试期间的退避等待是 Agent
    「静默卡顿」的唯一来源——``snapshot()`` 只能看到 Execution 存活，
    看不到 sleep 进度，UI 没有任何其它途径区分「在思考」与「在等
    重试」。因此重试**应当可观测**：决策 handler 在**置
    ``can_continue=True`` 之后、sleep 之前**，以 **fire-and-forget**
    方式 dispatch ``on_retry``，value 为自洽快照
    ``{"attempt": attempt, "max_retries": max_retries, "delay": delay,
    "error": ctx.error}``。用途是 UI 显示「retrying...(1/10)」之类的
    进行态；**attempt 本体仍留在闭包**做预算判断，发出去的只是副本。
    handler 返回值被忽略（观测语义，不参与决策链）。

    **决策 handler 行为**（收到 ``ProviderErrorContext ctx`` 时）：

    - ``isinstance(ctx.error, NON_RETRYABLE_ERRORS)`` —— 直接 ``return ctx``；
      不 sleep、不计数、``can_continue`` 保持 ``False``。凭证错误、非法
      请求、内容策略错误重试无意义。
    - ``isinstance(ctx.error, RateLimitedError)`` —— 计数器加 1；若
      ``attempt > max_retries`` 则放弃（``return ctx``）；否则按退避模型
      计算延迟并 ``await asyncio.sleep(delay)``，置 ``can_continue=True``
      后 ``return ctx``。
    - ``isinstance(ctx.error, (ServerError, NetworkError, ProviderTimeoutError))`` ——
      同上计数与上限判断；延迟固定为 ``base_delay``（瞬时基础设施故障
      不需要幂增长），置 ``can_continue=True`` 后返回。
    - 其它一切异常类型（含框架未知异常）—— 一律 ``return ctx`` 不重试。
      保守原则：默认策略只对**明确已知**的可重试类型放行。

    **延迟计算**：

    - ``backoff="exponential"``：``RateLimitedError`` 的延迟为
      ``min(base_delay * 2 ** (attempt - 1), MAX_RETRY_DELAY)``；
      三类基础设施错误恒为 ``base_delay``。
    - ``backoff="fixed"``：所有可重试错误的延迟均恒为 ``base_delay``。
    - 实现不引入随机 jitter——单机小工具定位下不存在多客户端同步重试
      打爆服务端的场景，保持延迟可预测、可测试。需要 jitter 的应用自行
      注册 handler。

    **重试计数（attempt）的所有权**：attempt 是 handler 闭包的**内部状态**，
    不是框架字段——``ProviderErrorContext`` 只有 ``error`` / ``provider`` /
    ``model`` / ``can_continue`` 四个字段，回合层面不存在重试计数状态机。
    计数器随逻辑 Turn 边界归零（经 ``before_turn``），同一 Turn 内的多次
    ``provider_gen()`` 共享计数（保守策略：一个 Turn 的失败预算不区分是哪一次
    调用消耗的）。计数器不落盘、不进快照、崩溃后不恢复。

    **非行为**：

    - 不修改 ``agent.model`` / ``agent.model_tag``——「改模型后重试」是
      用户自定义 handler 的合法写法，默认实现不做（模型 ↔ Provider 1:1
      绑定、无 fallback 体系，默认实现无候选可切）。
    - 不 ``raise Intercepted``、不写任何 ``shortcut`` 字段——
      ``on_provider_error`` 的决策只经 ``can_continue`` 表达。
    - 不产生任何消息、不写消息级树；除上述 ``on_retry`` 观测信号外不
      触发其它钩子点——重试等待期间逻辑 Turn 只是「在 sleep」。
    - 不处理 :class:`flowing.errors.ContextLengthError`——该异常在核心
      分发 ``on_provider_error`` **之前**直接上抛，本 handler 永远收不到它；
      上下文溢出后的出路是消息级树手术（``MessageChain``）或 fork，属
      应用层决策。
    - 不做跨错误联动（如「连续限流 3 次后换策略」）；计数器只服务于
      上限判断。

    **边缘情况**：

    - **重复调用**：不做幂等去重记号——每次调用按注册语义各自叠加一组
      独立的 handler（各自持有独立的 attempt 计数闭包；允许以不同参数
      多次启用同一 Composable）。recover 管线在新实例上重跑
      ``setup()``，钩子注册表随实例重建，天然不叠加。
    - **max_retries=0**：合法。计数器加 1 后 ``1 > 0`` 立即放弃，等价于
      不重试（与不调用的差别仅在于多了一次空分发）。
    - **max_retries 为负 / base_delay 为负 / backoff 非法**：调用时立即
      抛 ``ValueError``，不产生任何注册副作用（先校验后注册）。
    - **并发**：同一 Agent 的逻辑 Turn 串行执行（单工作循环 Task），闭包
      计数器无并发读写；不同 Agent 各自持有独立闭包，互不影响。
    - **Turn 中止**：sleep 期间用户 ``cancel()`` 当前执行——abort 是
      协作式检查点机制，sleep 本身不可中断；本次 sleep 结束后
      ``can_continue=True`` 放行，回合层面在下一个 ``provider_gen()`` 前的
      abort 检查点正常中止。即取消的生效粒度是「当前这次退避等待结束」。
    - **恢复后的 Agent**：恢复管线在新实例上重跑 ``setup()``，钩子
      注册表随实例重建，``use_retry(self)`` 重新注册天然不叠加；
      计数器从 0 开始——崩溃前消耗的重试次数不继承（逻辑 Turn 本就随
      崩溃终结）。

    **前置条件**：``agent.hooks`` 可用（实例已过 ``__init__``）；
    ``on_provider_error`` 为核心预填钩子点，无需 ``declare()``。

    **后置条件**：``agent.hooks.on_provider_error`` 与
    ``agent.hooks.before_turn`` 链尾各追加一个 ``by="retry"`` 的
    :class:`flowing.hooks.HookEntry`；此后该实例的 LLM 错误路径按上述
    规则决策。

    .. rubric:: 测试案例

    1. 前置：Agent 未调用 ``use_retry`` → 操作：Provider 抛
       ``ServerError`` → 期望：``on_provider_error`` 空链分发，
       ``can_continue=False``，逻辑 Turn 终止，Agent 回到空闲。
    2. 前置：``use_retry(agent)``（默认参数）→ 操作：Provider 连续 2 次
       抛 ``RateLimitedError`` 后成功 → 期望：两次 handler 各 sleep
       ``min(1.0 * 2**0, 60)``=1.0s、``min(1.0 * 2**1, 60)``=2.0s，
       第三次调用成功，逻辑 Turn 正常结束。
    3. 前置：``use_retry(agent, max_retries=2)`` → 操作：Provider 持续抛
       ``ServerError`` → 期望：attempt=1、2 各 sleep ``base_delay`` 并
       重试，attempt=3 时 ``3 > 2`` 放弃，Turn 终止；LLM 共被调用 3 次
       （初始 1 + 重试 2）。
    4. 前置：``use_retry(agent)`` → 操作：Provider 抛
       ``AuthenticationError`` → 期望：handler 立即返回、不 sleep、
       ``can_continue=False``，Turn 终止。
    5. 前置：``use_retry(agent)`` → 操作：Provider 抛
       ``ContextLengthError`` → 期望：异常**不经** ``on_provider_error``
       直接上抛（核心机制行为，与本模块无关但需回归验证不被拦截）。
    6. 前置：``use_retry(agent, backoff="fixed", base_delay=0.5)`` →
       操作：连续抛 ``RateLimitedError`` → 期望：每次延迟恒为 0.5s。
    7. 前置：``use_retry(agent)`` → 操作：``agent.hooks.on_provider_error
       .remove_by_owner("retry")`` 后 Provider 抛 ``ServerError`` →
       期望：默认策略已被整组移除（含 ``before_turn`` 归零 handler），
       Turn 直接终止。
    8. 前置：``use_retry(agent)`` → 操作：同一逻辑 Turn 内第一次
       ``provider_gen()`` 用掉 2 次重试后成功，随后第二次 ``provider_gen()`` 抛
       ``ServerError``（``max_retries=3``）→ 期望：计数器不清零，
       本次 attempt 从 3 起计——共享 Turn 失败预算。
    9. 前置：无 → 操作：``use_retry(agent, max_retries=-1)`` → 期望：
       抛 ``ValueError``，且两个钩子链均无新增条目。
    10. 前置：``use_retry(agent)`` 且 Turn 内重试 1 次后逻辑 Turn 正常
        结束 → 操作：下一条消息开启新 Turn，Provider 抛 ``ServerError``
        → 期望：``before_turn`` 归零 handler 已将计数器重置，本次
        attempt 从 1 起计。
    11. 前置：Provider 抛自定义未知异常 ``MyBizarreError`` → 操作：
        经过默认 handler → 期望：不重试，``can_continue=False``。
    12. 前置：``use_retry(agent)`` 且有订阅者挂在 ``on_retry`` → 操作：
        Provider 抛 ``RateLimitedError`` → 期望：订阅者收到
        ``{"attempt": 1, "max_retries": 3, "delay": 1.0, "error": ...}``；
        重试成功路径每重试一次收到一条；放弃路径不发信号。

    .. rubric:: 参数

    :param agent: 目标 Agent 实例；标准用法是在 ``setup()`` 中传 ``self``。
    :param max_retries: 单个逻辑 Turn 内的最大重试次数，``>= 0``。
    :param base_delay: 退避基准秒数，``>= 0``；为 0 时不等待直接重试。
    :param backoff: 退避模型，``"exponential"``（默认，限流错误按 2 的幂
        增长、封顶 :data:`MAX_RETRY_DELAY`）或 ``"fixed"``（恒定等待）。

    .. rubric:: 异常

    :raises ValueError:
        ``max_retries < 0``、``base_delay < 0`` 或 ``backoff`` 不在
        ``{"exponential", "fixed"}`` 中时，于注册任何 handler 之前抛出。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.hooks.HookRegistry.declare()``（时机：注册形态
      第 0 步，声明 ``on_retry`` 钩子点，幂等）；
      ``flowing.hooks.HookList.__call__()``（时机：注册形态第 1、2
      步，分别在 ``before_turn`` 与 ``on_provider_error`` 链尾追加
      ``by="retry"`` handler）
    - 被调：无（框架内无调用方；应用层在 Agent ``setup()`` 中调用，
      属用户代码）

    .. seealso::

        :class:`flowing.agent.ProviderErrorContext` —— handler 的 value
            类型与 ``can_continue`` 决策字段。
        :meth:`flowing.hooks.HookList.dispatch` —— handler 的执行与改写链语义。
        :meth:`flowing.lists.ManagedList.remove_by_owner` —— 整组替换默认策略的入口。
        :class:`flowing.agent.TurnContext` —— 逻辑 Turn 的执行期载体。
        :class:`flowing.errors.RateLimitedError` 等 —— 错误分类机制（核心保留，不可替换）。
    """
    from flowing.agent import TurnContext

    # 先校验后注册：非法参数抛 ValueError，不产生任何注册副作用
    if max_retries < 0:
        raise ValueError("max_retries 必须 >= 0")
    if base_delay < 0:
        raise ValueError("base_delay 必须 >= 0")
    if backoff not in ("exponential", "fixed"):
        raise ValueError("backoff 仅接受 'exponential' / 'fixed'")

    # 注册形态第 0 步：声明本 Agent 实例的重试观测钩子点（同名同 by 幂等）
    agent.hooks.declare("on_retry", by="retry")

    # attempt 计数器为本次调用闭包的内部状态（规约：非 ProviderErrorContext /
    # TurnContext 字段），由配套的 before_turn 归零 handler 重置；计数从 1
    # 起：首次失败为第 1 次尝试，attempt <= max_retries 时才放行重试
    state = {"attempt": 0}
    # on_retry 观测信号是 fire-and-forget：强引用集防 GC 早收，done 回调
    # 吃掉订阅者异常（观测通道不逃逸进事件循环，与插件层后台任务同惯例）
    pending: set[asyncio.Task] = set()

    def _reset(agent: Agent, turn: TurnContext) -> TurnContext:
        # 归零 handler：每个逻辑 Turn 开始把闭包 attempt 计数器重置为 0；
        # value 原样透传，纯观察不干预
        state["attempt"] = 0
        return turn

    async def _retry_handler(agent: Agent, ctx: ProviderErrorContext) -> ProviderErrorContext:
        """默认重试决策 handler（内部 API，经 ``by="retry"`` 定位与移除）。

        - 签名遵守统一 handler 契约 ``(agent, value) -> value``；为 async
          handler（退避 sleep 有真实 await 需求，M-91），dispatch 经
          ``inspect.isawaitable`` 透明 await。
        - 必须 ``return ctx``；不 ``raise Intercepted``（错误钩子无阻断
          语义，决策只经 ``can_continue`` 表达）；自身若抛出普通异常，
          按钩子系统规则直接上抛（无兜底）。
        - 分类决策（保守原则：只对明确已知的可重试类型放行）：
          NON_RETRYABLE 直接返回；RateLimited 按退避模型（exponential 时
          ``min(base_delay * 2 ** (attempt - 1), MAX_RETRY_DELAY)``）；
          其余三类基础设施错误恒 ``base_delay``；``attempt > max_retries``
          放弃（放弃路径不发 ``on_retry`` 信号）；未知异常一律不重试。
        - ``on_retry`` 观测信号（M-87）：置 ``can_continue=True`` 之后、
          sleep 之前，fire-and-forget 派发 ``{"attempt", "max_retries",
          "delay", "error"}`` 自洽快照；订阅者返回值被忽略。
        """
        if isinstance(ctx.error, NON_RETRYABLE_ERRORS):
            return ctx  # 不 sleep、不计数，can_continue 保持 False
        if isinstance(ctx.error, RETRYABLE_ERRORS):
            state["attempt"] += 1
            attempt = state["attempt"]
            if attempt > max_retries:
                return ctx  # 达到上限，放弃重试（放弃路径不发 on_retry 信号）
            # 延迟计算：三类基础设施错误恒为 base_delay；限流错误按退避模型
            delay = base_delay
            if isinstance(ctx.error, RateLimitedError) and backoff == "exponential":
                delay = min(base_delay * 2 ** (attempt - 1), MAX_RETRY_DELAY)
            ctx.can_continue = True
            task = asyncio.ensure_future(agent.hooks.on_retry.dispatch(
                agent, {"attempt": attempt, "max_retries": max_retries,
                        "delay": delay, "error": ctx.error}))
            pending.add(task)

            def _observe(done: asyncio.Task) -> None:
                pending.discard(done)
                if not done.cancelled() and done.exception() is not None:
                    _logger.warning("on_retry 订阅者异常（观测通道，不影响重试决策）",
                                    exc_info=done.exception())

            task.add_done_callback(_observe)
            await asyncio.sleep(delay)
        # 其它一切异常类型（含未知异常）：原样返回不重试
        return ctx

    # 注册形态第 1、2 步：共用 by="retry"，remove_by_owner("retry") 整组移除
    agent.hooks.before_turn(_reset, by="retry")
    agent.hooks.on_provider_error(_retry_handler, by="retry")

