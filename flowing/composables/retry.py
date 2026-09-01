"""flowing.composables.retry —— 内置重试 Composable（可选、非默认）。

.. rubric:: 功能介绍

本模块提供 ``use_retry()``：为单个 Agent 实例启用「LLM 调用失败退避
重试」策略。启用后，该 Agent 的 LLM 调用抛出可重试类异常时，默认策略
按参数指定的退避模型等待一段时间（期间不调用 LLM），再在同一回合内
重发调用，直至成功或达到次数上限；抛出不可重试类错误与未知错误时不做
重试，本回合以错误结局终止。

本模块属应用层 / 内置 Composable：随 ``flowing`` 包发布但不自动启用，
必须由 Agent 开发者在 ``setup()`` 中显式调用 ``use_retry(self)``。不
调用的 Agent 不持有任何重试相关状态、不注册任何 handler——错误路径与
不调用时逐字节等价（LLM 异常 → ``on_provider_error`` 空链分发 →
``can_continue`` 保持 ``False`` → 本回合以 ``"error"`` 结局终止，
Agent 存活）。

「重试」是策略不是机制：框架核心只提供错误分类（哪些类型可重试、
哪些不可重试，见 ``flowing.errors``）、``on_provider_error`` 钩子点的
分发与 ``can_continue`` 决策通道；「重试几次、等多久、对哪些错误重试」
全部由本模块提供一份可整体替换的默认实现。替换方式：
``remove_by_owner("retry")`` 移除默认 handler 后自注册（见
:func:`use_retry` 使用示例）。

.. rubric:: 注册面清单

- 启用方式：仅阶段二——``setup()`` 中调用 ``use_retry(self)``（恢复
  管线在新实例上重跑 ``setup()``，天然不叠加）。未启用时零开销：
  ``on_provider_error`` / ``before_turn`` 链上无任何 ``by="retry"``
  handler，``on_retry`` 钩子点不存在（访问抛
  :class:`flowing.errors.UnknownHookPointError`）。
- 注册的资源：无 provide key、无工具注册、无 Agent 状态键。
- 声明的钩子点：``on_retry``（``by="retry"``，无 ``match_on``）——重试
  观测点，由本模块在使用处 dispatch（谁声明谁 dispatch）。
- 挂载的钩子：``before_turn`` 归零 handler（``by="retry"``，每个逻辑
  Turn 开始重置重试计数）+ ``on_provider_error`` 重试决策 handler
  （``by="retry"``）。两者共用 ``by="retry"``，
  ``remove_by_owner("retry")`` 可整组移除（``on_retry`` 钩子点属声明
  而非 handler，不受移除影响）。
- 作用域：只影响调用它的那一个 Agent 实例；不修改 ``agent.model``、
  不产生任何消息、不持久化任何状态。

.. rubric:: 使用示例

.. code-block:: python

    from flowing import Agent
    from flowing.composables.retry import use_retry

    class MyAgent(Agent):
        async def setup(self) -> None:
            use_retry(self, max_retries=3, base_delay=1.0)

.. rubric:: 行为要点

- 错误分类：默认策略只对 :data:`RETRYABLE_ERRORS` 中列出的四种异常
  重试；:data:`NON_RETRYABLE_ERRORS` 中列出的异常与一切未知异常一律
  不重试（凭证错误不会因等待而变对，请求体不会因等待而合法）。
- :class:`flowing.errors.ContextLengthError` 不经过
  ``on_provider_error``——它在核心分发之前就被直接上抛，本模块的
  handler 永远收不到它。
- 重试发生在逻辑 Turn 内部：不产生任何消息、不写消息级树；重试计数
  随 Turn 边界归零、不落盘、崩溃后不恢复。
- 同一 Turn 内的多次 ``provider_gen()`` 共享同一重试预算（失败预算不
  区分是哪一次调用消耗的）。

.. seealso::

    :func:`flowing.composables.compact.use_compact` —— 另一个内置
    Composable（同构形态）。
    :class:`flowing.agent.ProviderErrorContext` —— 决策 handler 的 value
    类型与 ``can_continue`` 决策字段。
    :class:`flowing.hooks.HookRegistry` —— ``on_provider_error`` /
    ``before_turn`` 钩子点的容器。
    :class:`flowing.agent.TurnContext` —— 逻辑 Turn 的执行期载体。
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
"""单次退避等待的时长上限（秒）。

默认策略计算出的任意一次等待时长（``asyncio.sleep`` 的参数）都不超过
该值——指数退避按 2 的幂增长时尤其如此：超过一分钟的等待对单机小工具
定位没有价值，反而让回合看起来像挂起。

这是策略的一部分，随 ``use_retry`` 整体可被替换，不属框架核心机制；
只约束默认 handler，用户自注册的 ``on_provider_error`` handler 不受此
值限制。

.. seealso:: :func:`flowing.composables.retry.use_retry`
"""

RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    RateLimitedError,
    ServerError,
    NetworkError,
    ProviderTimeoutError,
)
"""默认策略视为可重试的异常类型元组（「限流 + 基础设施瞬时故障」四类）。

分别是 :class:`flowing.errors.RateLimitedError`（限流）、
:class:`flowing.errors.ServerError`（服务端错误）、
:class:`flowing.errors.NetworkError`（网络故障）与
:class:`flowing.errors.ProviderTimeoutError`（Provider 超时）。

这是策略清单而非机制清单：机制只保证这些类型在对应故障时被 Provider
adapter 抛出、且会经过 ``on_provider_error`` 分发；「对它们重试」是
``use_retry`` 的选择，用户 handler 可自由采用不同清单。不在此元组中的
异常类型（含一切未知异常）默认策略一律不重试。

.. seealso:: :data:`flowing.composables.retry.NON_RETRYABLE_ERRORS`
"""

NON_RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    AuthenticationError,
    InvalidRequestError,
    ContentPolicyError,
    QuotaExhaustedError,
    RequestTooLargeError,
)
"""默认策略显式不重试的异常类型元组（凭证类、请求类与配额类错误）。

分别是 :class:`flowing.errors.AuthenticationError`、
:class:`flowing.errors.InvalidRequestError`、
:class:`flowing.errors.ContentPolicyError`、
:class:`flowing.errors.QuotaExhaustedError` 与
:class:`flowing.errors.RequestTooLargeError`。

这些异常到达默认 handler 时被原样放行：不等待、不计数、不改写任何
字段，``can_continue`` 保持 ``False``，本回合以错误结局终止——重试
它们不改变结果（凭证不会因等待而变对，请求体不会因等待而合法，余额
不会因等待而变多）。注意 :class:`flowing.errors.ContextLengthError` 不
在此列也无需在列：它在核心分发之前就被直接上抛，永远不会到达
``on_provider_error``。

.. seealso:: :data:`flowing.composables.retry.RETRYABLE_ERRORS`
"""


def use_retry(
    agent: Agent,
    max_retries: int = 3,
    base_delay: float = 1.0,
    backoff: Literal["exponential", "fixed"] = "exponential",
) -> None:
    """为单个 Agent 实例启用 LLM 调用失败的重试策略（可选、非默认）。

    .. rubric:: 功能介绍

    在 ``agent.hooks.on_provider_error`` 上注册一个重试决策 handler
    （``by="retry"``），并配套注册一个 ``before_turn`` 归零 handler
    用于在每个逻辑 Turn 开始时重置重试计数。启用后，该 Agent 的 LLM
    调用抛出可重试类异常时，决策 handler 按参数指定的退避模型等待一段
    时间（期间不调用 LLM），再在同一回合内重发调用，直至成功或达到
    ``max_retries`` 上限；抛出不可重试类异常或未知异常时不做重试，
    本回合以错误结局终止（Agent 存活）。

    本函数是双层启用的阶段二入口，只能在 ``setup()``（或实例存活期内
    的任意代码）中对已完成初始化的实例调用。

    .. rubric:: 使用示例

    .. code-block:: python

        # 1. 最小启用（默认参数：3 次重试、1 秒基准、指数退避）
        async def setup(self) -> None:
            use_retry(self)

        # 2. 调参
        async def setup(self) -> None:
            use_retry(self, max_retries=10, base_delay=0.5, backoff="fixed")

        # 3. 整体替换：移除默认 handler，注册自己的策略
        async def setup(self) -> None:
            use_retry(self)
            self.hooks.on_provider_error.remove_by_owner("retry")

            async def my_policy(agent, ctx):
                if isinstance(ctx.error, RateLimitedError):
                    await asyncio.sleep(5.0)
                    ctx.can_continue = True
                return ctx

            self.hooks.on_provider_error(my_policy, by="myapp")

    .. rubric:: 行为要点

    注册的两个 handler（都经 ``by="retry"`` 定位与移除）：

    - ``before_turn`` 归零 handler：每个逻辑 Turn 开始时把重试计数重置
      为 0（value 原样透传，纯观察不干预）。
    - ``on_provider_error`` 决策 handler：见下「重试决策」。

    参数语义：

    - ``max_retries``：单个逻辑 Turn 内允许的最大重试次数，``>= 0``。
      计数从 1 起：首次失败为第 1 次尝试，只有 ``attempt <= max_retries``
      才放行重试，因此默认值 3 表示「初始调用 + 至多 3 次重试」至多 4
      次 LLM 调用。``max_retries=0`` 合法：首次失败即放弃，等价于不
      重试（与不调用的差别仅在于多了一次空分发）。
    - ``base_delay``：退避基准秒数，``>= 0``；为 0 时不等待直接重试。
      ``backoff="fixed"`` 时即每次的等待时长。
    - ``backoff``：退避模型，仅接受 ``"exponential"``（默认）或
      ``"fixed"``。

    重试决策（决策 handler 收到 ``ProviderErrorContext`` 时）：

    - ``ctx.error`` 属于 :data:`NON_RETRYABLE_ERRORS`，或不在
      :data:`RETRYABLE_ERRORS` 中（含一切未知异常）：原样返回，不等待、
      不计数，``can_continue`` 保持 ``False``——保守原则，只对明确已知
      的可重试类型放行。
    - ``ctx.error`` 属于 :data:`RETRYABLE_ERRORS`：计数加 1；若计数已
      超过 ``max_retries`` 则放弃（原样返回，不发观测信号）；否则先置
      ``ctx.can_continue = True``，等待 ``delay`` 秒，回合层面随后在同一
      回合内重发调用。
    - 延迟计算：``backoff="exponential"`` 时，
      :class:`flowing.errors.RateLimitedError` 的等待时长为
      ``min(base_delay * 2 ** (attempt - 1), MAX_RETRY_DELAY)``，其余
      三类基础设施错误（``ServerError`` / ``NetworkError`` /
      ``ProviderTimeoutError``）恒为 ``base_delay``（瞬时基础设施故障
      不需要幂增长）；``backoff="fixed"`` 时所有可重试错误的等待时长恒
      为 ``base_delay``。不引入随机抖动——单机小工具定位下不存在多客户
      端同步重试打爆服务端的场景，等待时长可预测、可测试；需要抖动的
      应用自行注册 handler。

    重试计数：计数是 handler 闭包的内部状态，不是框架字段——回合
    层面不存在重试计数状态机。计数随逻辑 Turn 边界归零（经 ``before_turn``
    归零 handler），同一 Turn 内的多次 ``provider_gen()`` 共享计数
    （失败预算不区分是哪一次调用消耗的）；不落盘、不进快照、崩溃后不
    恢复。

    重试观测信号（``on_retry`` 钩子点）：每次放行重试时，在置
    ``can_continue`` 之后、等待之前，以 fire-and-forget 方式向
    ``on_retry`` 派发一条自洽快照，内容为 ``{"attempt": ...,
    "max_retries": ..., "delay": ..., "error": ...}``（``error`` 是本次
    失败的原始异常），供 UI 显示「retrying...(1/10)」之类的进行态。
    快照发出后订阅者的返回值被忽略（观测语义，不参与决策链）；达到上限
    放弃重试的路径不发信号；订阅者异常被记录为警告，不影响重试决策。

    重复调用：不做幂等去重——每次调用按注册语义各自叠加一组独立的
    handler（各自持有独立的计数闭包），允许以不同参数多次启用。恢复管线
    在新实例上重跑 ``setup()``，钩子注册表随实例重建，天然不叠加。

    回合中止：等待期间用户 ``cancel()`` 当前执行——等待本身不可
    中断，本次等待结束后 ``can_continue=True`` 放行，回合层面在下一次
    ``provider_gen()`` 前的检查点正常中止。取消的生效粒度是「当前这次
    退避等待结束」。

    不做什么：不修改 ``agent.model`` / ``agent.model_tag``（「改模型
    后重试」是用户自定义 handler 的合法写法，默认实现不做）；不产生任何
    消息、不写消息级树；不做跨错误联动（如「连续限流 3 次后换策略」）。

    :param agent: 目标 Agent 实例；标准用法是在 ``setup()`` 中传 ``self``。
    :param max_retries: 单个逻辑 Turn 内的最大重试次数，``>= 0``。
    :param base_delay: 退避基准秒数，``>= 0``；为 0 时不等待直接重试。
    :param backoff: 退避模型，``"exponential"``（默认）或 ``"fixed"``。
    :raises ValueError:
        ``max_retries < 0``、``base_delay < 0`` 或 ``backoff`` 不在
        ``{"exponential", "fixed"}`` 中时，于注册任何 handler 之前抛出。

    .. seealso::

        :class:`flowing.agent.ProviderErrorContext` —— 决策 handler 的
            value 类型与 ``can_continue`` 决策字段。
        :meth:`flowing.lists.ManagedList.remove_by_owner` —— 整组替换
            默认策略的入口。
        :data:`RETRYABLE_ERRORS` / :data:`NON_RETRYABLE_ERRORS` ——
            默认策略的错误分类清单。
    """
    from flowing.agent import TurnContext

    # 先校验后注册：非法参数抛 ValueError，不产生任何注册副作用
    if max_retries < 0:
        raise ValueError("max_retries 必须 >= 0")
    if base_delay < 0:
        raise ValueError("base_delay 必须 >= 0")
    if backoff not in ("exponential", "fixed"):
        raise ValueError("backoff 仅接受 'exponential' / 'fixed'")

    # 声明本 Agent 实例的重试观测钩子点（同名同 by 幂等）
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

        收到 ``ProviderErrorContext`` 时按错误类型决策：属于
        :data:`NON_RETRYABLE_ERRORS` 或不属于 :data:`RETRYABLE_ERRORS`
        （含一切未知异常）→ 原样返回，不等待、不计数；属于
        :data:`RETRYABLE_ERRORS` → 计数加 1，未超 ``max_retries`` 则置
        ``ctx.can_continue = True``、派发 ``on_retry`` 观测快照、等待
        ``delay`` 秒后返回，超限则原样返回（不发观测信号）。延迟公式见
        :func:`use_retry` 行为要点。必须 ``return ctx``；不
        ``raise Intercepted``（错误钩子无阻断语义，决策只经
        ``can_continue`` 表达）。
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

    # 两个 handler 共用 by="retry"，remove_by_owner("retry") 整组移除
    agent.hooks.before_turn(_reset, by="retry")
    agent.hooks.on_provider_error(_retry_handler, by="retry")

