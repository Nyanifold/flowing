"""flowing.plugins.comm —— 进程内通信扩展（CommPlugin / Communication / CommHandle / use_comm）。

.. rubric:: 功能介绍

通信扩展是 Flowing 的内置扩展（随 ``flowing`` 包发布但不自动启用），
为进程内各实体（Agent、UI 组件、应用层代码）提供两条互不交叉的消息
通道：点对点信号（``send`` / ``request`` / ``reply``）与发布-订阅事件
（``publish`` / ``subscribe``）。框架核心不感知本扩展——它完全由
``runtime.use()`` / ``use_comm()`` 按需启用。

启用遵循全局统一的双层启用模型：

- 阶段一（Runtime 安装）：``runtime.use(CommPlugin())`` ——
  ``CommPlugin.install(runtime)`` 创建全局 ``Communication`` 总线实例
  并经 ``runtime.provide(communication_key, bus)`` 注入 provide 链根；
- 阶段二（Agent 启用）：Agent 的 ``setup()`` 中调用 ``use_comm(self)``，
  为该实例创建 ``CommHandle``、注册端点、声明 ``on_signal`` /
  ``on_event`` 钩子点并挂载 ``before_destroy`` 清理 handler。

.. rubric:: 注册面清单

- 启用方式：

  - 阶段一：``runtime.use(CommPlugin())`` —— ``install()`` 同步创建
    总线并 provide；重复安装同名插件（再次 ``use(CommPlugin())``）抛
    ``ValueError`` （一个 Runtime 同时只装一个同名插件，见
    :meth:`flowing.runtime.Runtime.use`）；
  - 阶段二：``setup()`` 中 ``use_comm(self)`` —— 为该实例注册端点、
    声明钩子点、挂载清理 handler；恢复时 ``setup()`` 在新实例上执行
    安全（进程重启则总线随插件重新安装而新建；同进程内先
    ``destroy()`` 再恢复则旧端点已注销，不会重名报错）；同一实例
    重复调用（端点 ID 相同）复用已有句柄，见
    :func:`use_comm` 行为要点。

- 注册的资源：provide key ``communication_key``，其类型参数为
  ``InjectionKey["Communication"]``，键名 ``"communication"``；provide
  到 Runtime 根，消费方式为 ``agent.inject(communication_key)``，沿
  亲代链上溯查找；未安装 ``CommPlugin`` 时 inject 抛
  :class:`flowing.errors.MissingProvideError`。

- 声明的钩子点：``on_signal`` （``by="comm"``，``match_on="type"``）/
  ``on_event`` （``by="comm"``，``match_on="topic"``），由 ``use_comm()``
  声明、由句柄的接收闭包在收到信号 / 事件时 dispatch（谁声明谁
  dispatch）。注册开放：声明后任何扩展都可挂 handler，无需再声明。

- 挂载的钩子：``before_destroy`` 上的清理 handler（``by="comm"``）——
  Agent ``destroy()`` 时调用 ``handle.destroy()`` 取消订阅、注销端点、
  取消挂起的请求；整组可经 ``remove_by_owner("comm")`` 移除。

- 未启用时的行为（未安装 ``CommPlugin`` 的 Runtime、未调用
  ``use_comm()`` 的 Agent）：零开销——没有 ``agent.comm_handler``
  属性、没有 ``on_signal`` / ``on_event`` 钩子点（访问抛
  :class:`flowing.errors.UnknownHookPointError`）、不在总线端点表中、
  其他端点无法经总线寻址到它；此时调用 ``use_comm(self)`` 抛
  :class:`flowing.errors.MissingProvideError`。

.. rubric:: 两条通道完全隔离

框架内存在两条互不交叉的消息通道：

- 对话通道：``Message`` → Agent 消息队列 → 逻辑 Turn 循环 → LLM 上下文；
- 通信通道（本模块）：``SignalEnvelope`` / ``EventEnvelope`` → 端点
  回调 / 钩子 dispatch——永不进入 LLM 上下文、永不落盘为消息树节点、
  与 ``Message.id`` / ``parent_id`` 链无任何关系。

需要桥接时由 handler 显式完成（例如在 ``on_signal`` handler 中调用
``await self.enqueue_message(...)`` 把信号内容转入对话通道）。框架不做
任何自动桥接。

.. rubric:: 端点与命名规则

端点是可通信实体在总线上的地址（端点 ID 即寻址）。端点 ID 统一为
语义化名称（不使用 UUID）：可读、可路由、可发现；唯一性由总线全局
注册表保证，重名注册抛 :class:`flowing.errors.DuplicateEndpointError`。
总线本身不提供端点枚举 / 发现 API——发现机制由应用层目录服务承担。

- Agent 端点：``use_comm()`` 时自动注册，ID 取 ``use_comm(name=...)``
  显式指定的语义名，缺省回退 ``agent.node_id`` （语义名只存在于唤起方 /
  应用层，Agent 实例不自持）；
- 应用层端点：应用代码经 ``comm.create_handle(endpoint_id=...)`` 注册
  （如 UI 端点 ``"ui-main"``）。

端点种类在总线层面无结构差异——全部是「端点 ID → 接收回调」的路由表
条目。Cron 扩展不经通信总线（见 :mod:`flowing.plugins.cron`）。

.. rubric:: 总线四操作语义（契约要点）

- ``send``：点对点发送信号，立即返回，不等待接收方处理完成、不支持
  回复；目标端点不存在抛
  :class:`flowing.errors.SignalDeliveryError`。
- ``request``：点对点发送并阻塞等待回复，经 ``correlation_id`` 匹配
  挂起的请求；超时抛 :class:`flowing.errors.SignalTimeoutError`。
- ``publish``：对 topic 的全部订阅者广播事件，容错（单个订阅者异常
  静默忽略，不影响其他订阅者）且 fire-and-forget（返回 awaitable 的
  回调转为后台任务，发布者不阻塞）。
- ``reply``：回复一个 ``request``，使用保留信号类型 ``'_reply'``；
  接收侧句柄检测该类型并匹配挂起的请求，不 dispatch 到钩子。

.. rubric:: 信封自动填充

``SignalEnvelope`` 的 ``sender`` / ``correlation_id`` / ``reply_to`` /
``created_at`` 与 ``EventEnvelope`` 的 ``publisher`` / ``created_at``
均由总线在构造信封时自动填充，发送方只提供 ``type`` / ``topic`` 与
负载。``created_at`` 为时区无关（naive）UTC ``datetime``，用于审计、
排序与延迟测量；显示层负责转本地时区。

.. rubric:: 单进程边界

通信总线是单进程内基础设施：无跨进程通信、无信封序列化格式承诺、
无网络拓扑。信封对象按引用传递，订阅者回调与发布者运行在同一事件
循环。大规模（上万端点）场景不为此设计。

.. rubric:: 生命周期

总线生命周期与 Runtime 相同：``install`` 时创建 → Agent 经
``inject`` 消费 → ``runtime.shutdown()`` 插件收尾阶段清空端点表与
订阅表。挂起请求的取消归各句柄 ``destroy()`` 负责——每个句柄清理
自己的端点、订阅与挂起请求；总线不持有句柄名单，不兜底取消。正常
关闭顺序下 Agent 树先递归 destroy（各句柄已清理），总线后关闭。

.. rubric:: 直接操作总线的立场

``agent.comm_handler`` （``CommHandle``）是推荐路径——句柄自动携带
身份（sender / publisher），是「身份正确」的保证。技术上任何代码都
能经 ``runtime.inject(communication_key)`` 取到总线并直接调用
``send(sender=...)`` 冒充其他端点；框架接受这一约定，不做结构禁止。
直接操作总线属高级用法，调用方须自行保证身份字段正确。

.. rubric:: 使用示例

.. code-block:: python

    # 阶段一：安装插件即创建全局总线
    runtime.use(CommPlugin())

    # 阶段二：Agent 的 setup() 中按实例启用并挂钩子
    async def setup(self):
        use_comm(self)

        @self.hooks.on_signal["agent_message"]
        def _(self, envelope):
            # 通信通道与对话通道互不相通；需要时由 handler 显式桥接
            return envelope

.. seealso::

    - :mod:`flowing.hooks` —— ``HookRegistry.declare`` / dispatch 语义。
    - :mod:`flowing.message` —— 对话通道的 ``Message`` 模型（与本模块
      正交）。
    - :mod:`flowing.errors` —— ``DuplicateEndpointError`` /
      ``SignalDeliveryError`` / ``SignalTimeoutError`` / ``Intercepted``。
    - :mod:`flowing.plugins.cron` —— 定时扩展（不经通信总线）。
"""

from __future__ import annotations   # 注解延迟求值：Communication 与 CommHandle 互相前向引用

import asyncio
import inspect
import logging
import warnings
from collections.abc import Callable
from typing import Any, ClassVar
from uuid import uuid4

from flowing.agent import Agent
from flowing.errors import (
    DuplicateEndpointError,
    Intercepted,
    SignalDeliveryError,
    SignalTimeoutError,
)
from flowing.params import InjectionKey
from flowing.plugins import Plugin
from flowing.runtime import Runtime

from .models import EventEnvelope, SignalEnvelope

_logger = logging.getLogger(__name__)

communication_key: InjectionKey["Communication"] = InjectionKey("communication")
"""全局 ``Communication`` 总线实例在 provide 链上的注入键。

``CommPlugin.install()`` 以本键 provide 总线实例；``use_comm()`` 与
应用层经 ``agent.inject(communication_key)`` 消费（沿亲代链上溯到
Runtime 根）。

行为要点：

- 键名为 ``"communication"``。
- 同 key 重复 ``provide`` 是覆盖更新（后者生效，``inject`` 实时可见）
  ——这是 provide 机制的统一语义（见
  :meth:`flowing.runtime.Runtime.provide`），插件间避免键冲突靠键名
  前缀约定，机制本身不拦截。
- 未安装 ``CommPlugin`` 时 ``inject`` 本键抛
  :class:`flowing.errors.MissingProvideError`。

.. seealso:: :class:`flowing.params.InjectionKey`、:func:`use_comm`、
    :meth:`flowing.agent.Agent.inject`
"""


class Communication:
    """单进程通信总线——端点注册表、信号路由与发布-订阅派发。

    .. rubric:: 功能介绍

    通信扩展的全局服务面：维护「端点 ID → 接收回调」的端点表与
    「topic → 订阅者 ID → 回调」的订阅表，提供 ``send`` / ``request`` /
    ``publish`` / ``reply`` 四操作与 ``subscribe`` / ``unsubscribe`` /
    ``unsubscribe_all`` 订阅管理。由 ``CommPlugin.install()`` 创建并以
    ``communication_key`` provide 到 Runtime provide 链根，生命周期与
    Runtime 相同。

    总线是「机制」：它只做寻址、路由、关联与派发，不理解任何业务语义
    （不校验 payload、不感知端点种类）。订阅表与广播派发归总线，收到
    后的二次派发归端点（``CommHandle``）——两层分工使非 Agent 端点
    （UI、系统组件）与 Agent 端点共享同一基础设施。

    .. rubric:: 使用示例

    .. code-block:: python

        # 应用层注册 UI 端点（阶段一 runtime.use(CommPlugin()) 之后）
        comm = runtime.inject(communication_key)
        ui_handle = comm.create_handle(endpoint_id="ui-main")
        ui_handle.subscribe("assistant_replied", on_assistant_replied)
        # on_assistant_replied 为你的 UI 回调

    .. rubric:: 行为要点

    - 四操作语义：``send`` 立即返回（目标端点不存在抛
      ``SignalDeliveryError``）；``request`` 阻塞等待回复（超时抛
      ``SignalTimeoutError``）；``publish`` 容错广播（单个订阅者异常
      静默忽略）；``reply`` 以保留类型 ``'_reply'`` 定向投递、不
      dispatch 到钩子。
    - 端点 ID 唯一：重复 ``register_endpoint`` 抛
      ``DuplicateEndpointError`` 且原映射不变；注销不存在的端点抛自然
      ``KeyError`` （与订阅清理的幂等语义有意不同）。
    - 订阅：同一订阅者对同一 topic 至多一条订阅（重复订阅覆盖旧
      回调）；``unsubscribe`` / ``unsubscribe_all`` 对不存在的订阅是
      幂等空操作。
    - 正常创建路径是 ``CommPlugin.install()`` （创建后经
      ``communication_key`` 进入注入链）；直接 ``Communication()``
      构造得到的是空总线，但它不在 provide 链上，其他 Agent 无法经
      ``inject`` 获得它。
    - 关闭：``runtime.shutdown()`` 插件收尾阶段清空端点表与订阅表；
      挂起请求的取消归各句柄 ``destroy()`` （总线不持有句柄名单，不
      兜底）。

    .. seealso:: :class:`CommPlugin`、:class:`CommHandle`、
        :data:`communication_key`、:func:`use_comm`
    """

    _endpoints: dict[str, Callable[[SignalEnvelope], Any]]
    """内部存储：端点 ID → 接收回调。内部 API，不属稳定契约。
    """
    _subscriptions: dict[str, dict[str, Callable[[EventEnvelope], Any]]]
    """内部存储：topic → subscriber_id → 回调。内部 API，不属稳定契约。
    """
    _tasks: set[asyncio.Task]
    """内部存储：fire-and-forget 后台任务的强引用集（防 GC 提前回收；
    任务完成即弃）。内部 API，不属稳定契约。
    """

    def __init__(self) -> None:
        """构造空总线（端点表 / 订阅表 / 后台任务集均空）。

        正常创建路径是 ``CommPlugin.install()`` （创建后经
        ``communication_key`` provide 进入注入链）；直接构造得到空
        总线，但不会进入 provide 链。应用代码通常不直接构造本类。

        .. seealso:: :meth:`CommPlugin.install`
        """
        self._endpoints = {}
        self._subscriptions = {}
        self._tasks = set()

    def _spawn(self, awaitable: Any) -> None:
        """把 awaitable 转成后台任务（fire-and-forget）：强引用持有、
        完成即弃、异常记日志不逃逸进事件循环。内部 API，不属稳定契约。"""
        task = asyncio.ensure_future(awaitable)
        self._tasks.add(task)

        def _finalize(done: asyncio.Task) -> None:
            self._tasks.discard(done)
            if done.cancelled():
                return
            exc = done.exception()
            if exc is not None:
                _logger.error("comm background callback task failed", exc_info=exc)

        task.add_done_callback(_finalize)

    def _deliver(self, target: str, handler: Callable[[SignalEnvelope], Any],
                 envelope: SignalEnvelope) -> None:
        """向端点回调投递信封：同步直接调用，返回 awaitable 转后台任务；
        回调同步异常捕获记日志、不传播给发送方。内部 API，不属稳定契约。"""
        try:
            result = handler(envelope)
        except Exception:
            _logger.exception("comm: receive callback for endpoint %r raised", target)
            return
        if inspect.isawaitable(result):
            self._spawn(result)

    def register_endpoint(
        self, endpoint_id: str, handler: Callable[[SignalEnvelope], Any]
    ) -> None:
        """把端点 ID 与接收回调绑定进总线路由表。

        .. rubric:: 功能介绍

        注册成功后该端点立即可被 ``send`` / ``request`` 寻址。handler
        在每次有信号投递到该端点时被调用（同步直接调用；返回 awaitable
        时转为后台任务）。

        .. rubric:: 行为要点

        - 注册不幂等：同一端点 ID 重复注册抛
          :class:`flowing.errors.DuplicateEndpointError`，且原映射
          不变（已有回调不被覆盖）。语义化名称的可路由性依赖唯一性，
          静默覆盖会使信号被路由到错误的实体。
        - 通常不直接调用——``create_handle()`` / ``use_comm()`` 内部
          经本方法注册。

        :param endpoint_id: 端点 ID（语义化名称）。
        :param handler: 接收回调，签名 ``(envelope: SignalEnvelope)``。

        :raises flowing.errors.DuplicateEndpointError: ``endpoint_id``
            已存在时。

        .. seealso:: :meth:`unregister_endpoint`、:meth:`create_handle`
        """
        if endpoint_id in self._endpoints:
            raise DuplicateEndpointError(endpoint_id)  # 原映射不变（写入前先查）
        self._endpoints[endpoint_id] = handler

    def unregister_endpoint(self, endpoint_id: str) -> None:
        """从路由表移除端点。

        .. rubric:: 行为要点

        - 注销后向该端点 ``send`` / ``request`` 抛
          :class:`flowing.errors.SignalDeliveryError`。
        - 注销不存在的端点抛自然 ``KeyError``——注销路径几乎总是
          ``CommHandle.destroy()`` 清理链的一部分，重复注销属编程
          错误，应当暴露而非吞掉（``CommHandle.destroy()`` 的幂等由
          句柄层自行保证，不依赖本方法的容错）。
        - 注销后该端点在途 ``request`` 的回复不受影响：回复按
          ``correlation_id`` 匹配挂起的请求，不查端点表。

        :param endpoint_id: 要注销的端点 ID。

        :raises KeyError: 端点不存在时（不包装）。

        .. seealso:: :meth:`register_endpoint`、:meth:`CommHandle.destroy`
        """
        del self._endpoints[endpoint_id]  # 不存在 → 自然 KeyError（有意不做容错）

    def create_handle(
        self,
        endpoint_id: str,
        *,
        on_signal: Callable[[SignalEnvelope], Any] | None = None,
        on_event: Callable[[EventEnvelope], Any] | None = None,
    ) -> CommHandle:
        """创建通信句柄并注册端点——所有端点的统一创建入口。

        .. rubric:: 功能介绍

        一次完成「注册端点、构造 ``CommHandle``」两件事：返回的句柄
        自动携带身份（发送时填 ``sender`` / ``publisher``）。``on_signal`` /
        ``on_event`` 是端点接收信号 / 事件的回调，缺省为 noop（收到的
        信号 / 事件被忽略）。``use_comm()`` 与 UI 等应用层端点都经
        本方法创建。

        .. rubric:: 行为要点

        - 端点 ID 已被占用时抛
          :class:`flowing.errors.DuplicateEndpointError`，句柄对象
          直接废弃，无副作用（端点表保持原映射）。
        - 注册进路由表的是句柄的接收入口（保留类型 ``'_reply'`` 在此
          分流，见 :meth:`CommHandle._receive`）。

        :param endpoint_id: 端点 ID（语义化名称）。
        :param on_signal: 信号接收回调；``None`` 时收到的信号被忽略。
        :param on_event: 事件接收回调；``None`` 时收到的事件被忽略。

        :raises flowing.errors.DuplicateEndpointError: ``endpoint_id``
            已存在时。

        .. seealso:: :class:`CommHandle`、:func:`use_comm`
        """
        handle = CommHandle(endpoint_id, self, on_signal=on_signal, on_event=on_event)
        # 注册进路由表的是句柄的 _receive（保留类型 '_reply' 匹配挂起
        # 请求、其余转 _on_signal）；注册失败（DuplicateEndpointError）
        # 时句柄对象直接废弃，无副作用
        self.register_endpoint(endpoint_id, handle._receive)
        return handle

    def send(self, sender: str, target: str, type: str, payload: dict[str, Any]) -> None:
        """点对点发送信号，立即返回（即发即忘：不等待接收方处理完成）。

        .. rubric:: 功能介绍

        构造 ``SignalEnvelope`` （自动填充 ``sender`` 与 ``created_at``；
        ``correlation_id`` / ``reply_to`` 为 ``None``），按端点表路由到
        ``target`` 的接收回调，然后立即返回——不等待接收方处理完成，
        不支持回复。需要回复的场景用 ``request()``。

        .. rubric:: 行为要点

        - 同步完成路由：接收回调的同步部分直接执行，返回 awaitable 的
          回调转为后台任务；本方法返回 ``None``。
        - ``target`` 端点不存在 → 发送方同步感知
          :class:`flowing.errors.SignalDeliveryError` （这是发送方唯一
          能感知的失败）。
        - 接收回调异常 → 捕获并记日志，不传播给发送方（与 ``publish``
          的容错一致）。
        - 不重试、不排队：接收方处理失败不补偿。
        - ``sender`` 应为已注册端点 ID；框架不强制（直接操作总线可
          冒充，属已知高级用法风险）。

        :param sender: 发送方端点 ID。
        :param target: 目标端点 ID。
        :param type: 信号类型。
        :param payload: 业务负载（总线透明）。

        :raises flowing.errors.SignalDeliveryError: ``target`` 端点
            不存在时。

        .. seealso:: :meth:`request`、:meth:`CommHandle.send`
        """
        envelope = SignalEnvelope(sender=sender, type=type, payload=payload)
        handler = self._endpoints.get(target)
        if handler is None:
            raise SignalDeliveryError(target, type)  # 发送方同步可感知的唯一失败
        self._deliver(target, handler, envelope)  # 接收回调异常静默（记日志），awaitable 转后台任务

    async def request(
        self,
        sender: str,
        target: str,
        type: str,
        payload: dict[str, Any],
        reply_handler: CommHandle,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """点对点发送并阻塞等待回复（request-reply）。

        .. rubric:: 功能介绍

        生成 ``correlation_id`` （UUID），在 ``reply_handler`` 的挂起表
        （等待对端回复的请求的登记处）中登记本次请求，发送带
        ``correlation_id`` 与 ``reply_to=sender``
        的信号信封，然后阻塞等待对端经 ``reply()`` 回传的 payload。
        回复信封以保留类型 ``'_reply'`` 投递回 ``reply_handler``，接收
        侧按 ``correlation_id`` 匹配并完成本次等待——回复信封不
        dispatch 到任何钩子。关联经显式 ``correlation_id`` 而非调用栈，
        使回复可以跨越任意异步边界（对端可以在另一个任务、另一个回合
        里回复）。

        通常经 ``CommHandle.request()`` 调用（自动填 ``sender`` 与
        ``reply_handler``）；直接调用本方法属高级用法。

        .. rubric:: 行为要点

        - ``timeout`` 到期 → :class:`flowing.errors.SignalTimeoutError`，
          挂起登记随即摘除，迟到的回复被静默丢弃。
        - ``target`` 不存在 → 发送阶段即抛
          :class:`flowing.errors.SignalDeliveryError` （不会进入等待）。
        - 等待期间 ``reply_handler.destroy()`` → 本次 ``await`` 收到
          ``asyncio.CancelledError``。
        - ``timeout=None`` 表示不限时（仅句柄销毁可解除等待）。
        - 一个 ``correlation_id`` 至多完成一次；同时并发的多个
          ``request`` 互不干扰。

        :param sender: 发送方端点 ID。
        :param target: 目标端点 ID。
        :param type: 信号类型。
        :param payload: 业务负载。
        :param reply_handler: 承载挂起请求的句柄（回复投递回它）。
        :param timeout: 等待秒数；``None`` 表示不限时。

        :raises flowing.errors.SignalDeliveryError: ``target`` 端点
            不存在时。
        :raises flowing.errors.SignalTimeoutError: 等待超过 ``timeout``
            秒时。
        :raises asyncio.CancelledError: 等待期间 ``reply_handler`` 被
            销毁时。

        .. seealso:: :meth:`CommHandle.request`、:meth:`CommHandle.reply`
        """
        correlation_id = str(uuid4())
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        reply_handler._pending_replies[correlation_id] = future  # 登记挂起的请求
        try:
            envelope = SignalEnvelope(
                sender=sender, type=type, payload=payload,
                correlation_id=correlation_id, reply_to=sender,
            )
            handler = self._endpoints.get(target)
            if handler is None:
                raise SignalDeliveryError(target, type)  # 发送阶段即抛，不进入等待
            self._deliver(target, handler, envelope)
            if timeout is None:
                return await future  # destroy 取消 → CancelledError 自然传播
            try:
                return await asyncio.wait_for(future, timeout)
            except TimeoutError:
                raise SignalTimeoutError(target, timeout) from None
        finally:
            # 超时/取消/异常路径统一摘除登记，迟到回复由接收侧静默丢弃
            reply_handler._pending_replies.pop(correlation_id, None)

    def reply(self, sender: str, target: str, correlation_id: str, payload: dict[str, Any]) -> None:
        """以保留类型 ``'_reply'`` 定向投递回复信封（低层入口）。

        .. rubric:: 功能介绍

        构造 ``type='_reply'`` 的 ``SignalEnvelope`` 并投递给
        ``target``。接收侧句柄检测保留类型后按 ``correlation_id`` 匹配
        挂起的请求并完成等待——不 dispatch 到 ``on_signal`` 钩子，应用
        层永远观察不到回复信封。回复是 request-reply 机制的内部完成
        事件，不是业务信号。

        通常经 ``CommHandle.reply()`` 调用；直接调用本方法属高级用法。

        .. rubric:: 行为要点

        - ``correlation_id`` 无匹配的挂起请求（已超时或已销毁）→
          静默丢弃并发 ``warnings.warn`` 警告（回复丢失通常意味着请求
          方异常退出或超时配置不当，应可观测而非无声）。
        - ``target`` 端点已注销 → 静默丢弃并发 ``warnings.warn`` 警告
          （回复路径不抛 ``SignalDeliveryError``——发送方可能已不在，
          报错无人接收）。
        - 不 dispatch、不入队、不产生任何钩子事件。

        :param sender: 回复方端点 ID。
        :param target: 请求方端点 ID。
        :param correlation_id: 原 ``request`` 信封的关联 ID。
        :param payload: 回复负载。

        .. seealso:: :meth:`request`、:meth:`CommHandle.reply`
        """
        envelope = SignalEnvelope(
            sender=sender, type="_reply", payload=payload,
            correlation_id=correlation_id,
        )
        handler = self._endpoints.get(target)
        if handler is None:
            # 端点已注销 → 静默丢弃（回复路径不抛 SignalDeliveryError——
            # 发送方可能已不在，报错无人接收），但 warnings.warn 保持可观测
            warnings.warn(
                f"comm: reply target endpoint {target!r} does not exist; reply dropped"
                f" (correlation_id={correlation_id})",
                stacklevel=2,
            )
            return
        self._deliver(target, handler, envelope)  # 接收侧按 correlation_id 匹配挂起请求，不 dispatch 钩子

    def publish(self, topic: str, event: dict[str, Any]) -> None:
        """向 topic 的全部订阅者广播事件（容错且 fire-and-forget）。

        .. rubric:: 功能介绍

        构造 ``EventEnvelope`` （自动填充 ``created_at``；``publisher``
        取 ``event`` 中已有的值——``CommHandle.publish()`` 会在调用前
        注入句柄身份），遍历订阅表把信封派发给每个订阅回调。

        .. rubric:: 行为要点

        - 订阅回调按订阅表遍历顺序逐个调用：同步回调直接执行完毕，
          返回 awaitable 的回调转为后台任务后立即继续下一个；本方法
          在全部回调「已启动」后返回，不等待任何回调完成。
        - 无订阅者 → 空操作。
        - 单个订阅回调抛异常 → 捕获并记日志，其余订阅者与发布者不受
          影响（容错）。
        - 不向发布者回传任何结果；不做重试与死信处理。

        :param topic: 事件主题。
        :param event: 事件内容（总线透明；``publisher`` 键被总线读取
            为发布方身份）。

        .. seealso:: :meth:`subscribe`、:meth:`CommHandle.publish`
        """
        envelope = EventEnvelope(
            topic=topic, event=event,
            publisher=event.get("publisher"),  # publisher 取 event 中已有值（句柄路径已注入）
        )
        callbacks = self._subscriptions.get(topic)
        if not callbacks:
            return  # 无订阅者 → 空操作
        for callback in list(callbacks.values()):  # 快照遍历：回调内增删订阅不影响本轮派发
            try:
                result = callback(envelope)
            except Exception:
                # 容错：单个订阅者异常静默（记日志），不影响其余订阅者与发布者
                _logger.exception("comm: subscribe callback for topic %r raised", topic)
                continue
            if inspect.isawaitable(result):
                self._spawn(result)  # fire-and-forget：发布者不等待

    def subscribe(
        self,
        subscriber_id: str,
        topic: str,
        callback: Callable[[EventEnvelope], Any],
    ) -> None:
        """把 ``callback`` 登记为 ``subscriber_id`` 对 ``topic`` 的订阅回调。

        .. rubric:: 行为要点

        - 登记后立即生效：下一次 ``publish(topic, ...)`` 即派发到该
          回调。
        - 同一 ``subscriber_id`` 对同一 ``topic`` 重复订阅 → 覆盖旧
          回调（一个订阅者一个 topic 至多一条订阅）。
        - 订阅不要求 ``subscriber_id`` 是已注册端点（但
          ``CommHandle`` 路径下它总是句柄的 ``endpoint_id``）。
        - 订阅表与广播派发归总线；回调内部的二次派发（到
          ``agent.hooks`` 或自定义逻辑）归端点层。

        :param subscriber_id: 订阅者 ID。
        :param topic: 事件主题。
        :param callback: 订阅回调，签名 ``(envelope: EventEnvelope)``。

        .. seealso:: :meth:`unsubscribe`、:meth:`publish`
        """
        self._subscriptions.setdefault(topic, {})[subscriber_id] = callback  # 重复订阅覆盖旧回调

    def unsubscribe(self, subscriber_id: str, topic: str) -> None:
        """取消单个订阅。

        .. rubric:: 行为要点

        - 订阅不存在时为幂等空操作（不抛异常）——与
          ``unregister_endpoint`` 的 ``KeyError`` 语义有意不同：取消
          订阅常见于清理路径，重复清理不应报错
          （``CommHandle.destroy()`` 的幂等依赖这一点）。

        :param subscriber_id: 订阅者 ID。
        :param topic: 事件主题。

        .. seealso:: :meth:`subscribe`、:meth:`unsubscribe_all`
        """
        subscribers = self._subscriptions.get(topic)
        if subscribers is not None:
            subscribers.pop(subscriber_id, None)  # 订阅不存在 → 幂等空操作
            if not subscribers:
                del self._subscriptions[topic]  # 空 topic 键顺手摘除，不留空壳

    def unsubscribe_all(self, subscriber_id: str) -> None:
        """取消某订阅者的全部订阅（清理路径用）。

        .. rubric:: 行为要点

        - 该订阅者没有任何订阅时是幂等空操作。

        :param subscriber_id: 订阅者 ID。

        .. seealso:: :meth:`unsubscribe`、:meth:`CommHandle.destroy`
        """
        # 该订阅者无订阅时幂等空操作；空 topic 键顺手摘除
        for topic in [t for t, subs in self._subscriptions.items()
                      if subscriber_id in subs]:
            del self._subscriptions[topic][subscriber_id]
            if not self._subscriptions[topic]:
                del self._subscriptions[topic]

    def _close(self) -> None:
        """总线关闭：清空端点表、订阅表。

        内部 API，不属稳定契约。由 ``CommPlugin`` 在 ``runtime.shutdown()``
        的插件收尾阶段调用。先于本方法，Agent 树已递归 destroy（各句柄
        已各自清理自己的端点、订阅与挂起请求），此处做兜底回收——但
        不兜底取消挂起请求：请求由各 ``CommHandle._pending_replies``
        持有，总线不持有句柄名单，物理上到不了；取消是各句柄
        ``destroy()`` 的责任（shutdown 前未销毁的句柄——如 UI 端点——
        其挂起请求悬至超时或事件循环回收时取消）。

        .. seealso:: :meth:`flowing.runtime.Runtime.shutdown`
        """
        self._endpoints.clear()
        self._subscriptions.clear()
        # 不兜底取消挂起的请求：请求由各 CommHandle._pending_replies
        # 持有，总线无句柄名单可到；取消责任归各句柄 destroy()，
        # 未销毁者悬至超时/事件循环回收。


class CommHandle:
    """通用通信句柄——任何端点（Agent / UI / 系统组件）的统一句柄，无子类。

    .. rubric:: 功能介绍

    端点持有者的操作面：发送类方法（``send`` / ``request`` /
    ``publish``）自动携带自己的身份（``sender`` / ``publisher``），
    订阅类方法（``subscribe`` / ``unsubscribe``）自动填订阅者 ID，
    ``destroy()`` 一次性清理该端点在总线上的全部痕迹。

    统一无子类：Agent 端点与非 Agent 端点（UI / 系统组件）共用同一个
    类。Agent 端点的接收回调经闭包注入——``use_comm()`` 的函数域内
    捕获 agent，把收到的信号 / 事件 dispatch 到 ``agent.hooks``；非
    Agent 端点由应用层在 ``create_handle`` 时注入自定义回调。

    句柄由 :meth:`Communication.create_handle` 创建（``use_comm()`` 与
    应用层端点创建均经此），创建时即完成端点注册。

    .. rubric:: 使用示例

    .. code-block:: python

        # Agent 端点（use_comm 创建，接收回调 dispatch 到实例钩子点）
        self.comm_handler.send(target="payment-agent", type="query",
                               payload={"subject": "对账请求"})

        # 非 Agent 端点（UI 组件，应用层创建，自定义回调）
        comm = runtime.inject(communication_key)
        ui_handle = comm.create_handle(endpoint_id="ui-main")
        ui_handle.subscribe("assistant_replied", on_assistant_replied)

    .. rubric:: 行为要点

    - ``endpoint_id`` 构造后不可变；一个句柄对应总线上恰好一个端点
      注册项（``destroy()`` 后对应零个）。
    - 未注入接收回调的句柄收到信号 / 事件时被静默忽略（noop）。
    - ``destroy()`` 可重复调用（幂等），首次调用完成全部清理，后续
      调用为空操作；清理后该句柄不再代表任何端点——向已注销的端点 ID
      发送信号会抛 ``SignalDeliveryError``；向其它存活端点发送仍可
      工作（发送只查目标端点表，不校验发送方），但继续使用销毁后的
      句柄不被推荐。
    - ``destroy()`` 不触发任何钩子（销毁事件的观察走 Agent 侧的
      ``before_destroy`` 钩子，与本方法正交）。

    .. seealso:: :class:`Communication`、:func:`use_comm`、
        :class:`SignalEnvelope`、:class:`EventEnvelope`
    """

    endpoint_id: str
    """端点 ID（语义化名称），构造时确定、不可变。发送 / 订阅路径上的
    身份字段全部由它自动填充。

    .. seealso:: :meth:`Communication.create_handle`
    """
    _comm: Communication
    """所属总线引用。内部 API，不属稳定契约。
    """
    _on_signal: Callable[[SignalEnvelope], Any]
    """注入的信号接收回调（缺省 noop）。内部 API，不属稳定契约。
    """
    _on_event: Callable[[EventEnvelope], Any]
    """注入的事件接收回调（缺省 noop）。内部 API，不属稳定契约。
    """
    _pending_replies: dict[str, asyncio.Future]
    """correlation_id → 挂起的请求 future。``request()`` 登记、
    ``'_reply'`` 信封完成、``destroy()`` 取消。内部 API，不属稳定契约。
    """

    def __init__(
        self,
        endpoint_id: str,
        comm: Communication,
        *,
        on_signal: Callable[[SignalEnvelope], Any] | None = None,
        on_event: Callable[[EventEnvelope], Any] | None = None,
    ) -> None:
        """构造句柄（不注册端点）。

        .. rubric:: 行为要点

        - 构造本身不注册端点（注册是 ``create_handle()`` 的职责，正常
          路径下由总线先行注册），也不声明任何钩子点（那是
          ``use_comm()`` 的职责）。
        - ``on_signal`` / ``on_event`` 为 ``None`` 时以 noop 回调替代，
          收到的信号 / 事件被忽略。
        - 直接构造本类属高级用法，需自行保证端点注册与回调注入的一致
          性；正常路径经 :meth:`Communication.create_handle` 创建。

        :param endpoint_id: 端点 ID（语义化名称）。
        :param comm: 所属总线。
        :param on_signal: 信号接收回调。
        :param on_event: 事件接收回调。

        .. seealso:: :meth:`Communication.create_handle`
        """
        self.endpoint_id = endpoint_id
        self._comm = comm
        self._on_signal = on_signal or self._default_noop
        self._on_event = on_event or self._default_noop
        self._pending_replies = {}

    @staticmethod
    def _default_noop(*args: Any) -> None:
        """默认空回调——未注入接收回调时，收到的信号 / 事件被忽略。

        内部 API，不属稳定契约。同步无返回，保证「未配置接收方的端点」
        在总线派发路径上永远是安全的空操作。
        """
        pass  # 空操作即全部语义：收到的信号/事件被忽略

    def _receive(self, envelope: SignalEnvelope) -> Any:
        """端点接收入口——注册进总线路由表的回调本体（内部 API，不属
        稳定契约）。

        保留类型 ``'_reply'`` 在此分流：按 ``correlation_id`` 匹配
        ``_pending_replies`` 完成对应挂起的请求（以其 ``payload`` 为
        结果），不转 ``_on_signal``——回复信封永远不进业务 dispatch 链；
        无匹配的挂起请求（已超时 / 已销毁）→ 静默丢弃并发
        ``warnings.warn`` 警告（回复丢失通常意味着请求方异常退出或
        超时配置不当，应可观测而非无声）。其余信封转构造时注入的
        ``_on_signal`` （缺省 noop）。

        .. seealso:: :meth:`Communication.create_handle`、:meth:`reply`
        """
        if envelope.type == "_reply":
            future = self._pending_replies.pop(envelope.correlation_id, None)
            if future is None or future.done():
                warnings.warn(
                    f"comm: received a reply with no matching pending future"
                    f" (correlation_id={envelope.correlation_id}); dropped",
                    stacklevel=2,
                )
                return None
            future.set_result(envelope.payload)
            return None
        return self._on_signal(envelope)

    def send(self, target: str, type: str, payload: dict[str, Any]) -> None:
        """发送点对点信号（自动填 ``sender=self.endpoint_id``）。

        .. rubric:: 功能介绍

        :meth:`Communication.send` 的身份携带包装；语义（立即返回、
        即发即忘、目标不存在抛 ``SignalDeliveryError``）与总线方法
        完全一致。

        .. rubric:: 使用示例

        .. code-block:: python

            self.comm_handler.send(target="ui-main", type="status_update",
                                   payload={"state": "thinking"})

        :param target: 目标端点 ID。
        :param type: 信号类型。
        :param payload: 业务负载。

        :raises flowing.errors.SignalDeliveryError: ``target`` 端点
            不存在时。

        .. seealso:: :meth:`Communication.send`、:meth:`request`
        """
        self._comm.send(self.endpoint_id, target, type, payload)

    async def request(
        self,
        target: str,
        type: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """发送并阻塞等待回复（自动填 ``sender`` 与 ``reply_handler=self``）。

        .. rubric:: 功能介绍

        :meth:`Communication.request` 的身份携带包装；correlation_id
        关联、超时、销毁取消等语义与总线方法完全一致。

        .. rubric:: 使用示例（工具审批）

        .. code-block:: python

            class GuardrailAgent(Agent):
                # Agent 子类：setup() 中启用通信并挂审批 handler。

                def setup(self):
                    use_comm(self)
                    self.hooks.before_tool_call(self.check_dangerous, by="guardrail")

                async def check_dangerous(self, tool_call):
                    if not is_dangerous(tool_call):   # is_dangerous 由你实现
                        return tool_call
                    try:
                        result = await self.comm_handler.request(
                            target="ui-main",
                            type="permission_request",
                            payload={"tool_name": tool_call.name,
                                     "tool_args": tool_call.args},
                            timeout=120.0,
                        )
                    except SignalTimeoutError:
                        raise Intercepted("审批超时")
                    if result.get("approved"):
                        return tool_call
                    raise Intercepted(result.get("reason", "用户拒绝"))

        .. rubric:: 行为要点

        - 等待期间本句柄被 ``destroy()`` → ``await`` 收到
          ``asyncio.CancelledError``。
        - 对端经 ``reply()`` 回传的 payload 原样作为返回值（回复走
          保留类型，不回传信封本身，也不触发任何钩子）。

        :param target: 目标端点 ID。
        :param type: 信号类型。
        :param payload: 业务负载。
        :param timeout: 等待秒数；``None`` 表示不限时。

        :raises flowing.errors.SignalDeliveryError: ``target`` 端点
            不存在时。
        :raises flowing.errors.SignalTimeoutError: 超过 ``timeout`` 秒
            未收到回复时。
        :raises asyncio.CancelledError: 等待期间句柄被销毁时。

        .. seealso:: :meth:`Communication.request`、:meth:`reply`
        """
        result = await self._comm.request(
            self.endpoint_id, target, type, payload, self, timeout=timeout
        )  # -> dict[str, Any]（返回值语义见 docstring）
        return result

    def reply(self, in_reply_to: SignalEnvelope, payload: dict[str, Any]) -> None:
        """回复一个 ``request`` 信号。

        .. rubric:: 功能介绍

        以 ``in_reply_to.reply_to`` （缺省回退 ``in_reply_to.sender``）
        为目标、原样携带 ``in_reply_to.correlation_id`` 投递保留类型
        ``'_reply'`` 信封。回复不 dispatch 到任何钩子——它只完成请求方
        挂起的请求。

        .. rubric:: 使用示例

        .. code-block:: python

            @self.hooks.on_signal["permission_request"]
            def _(self, envelope: SignalEnvelope):
                approved = envelope.payload["tool_name"] in self.allowed_tools
                self.comm_handler.reply(envelope, {"approved": approved})
                return envelope
            # self.allowed_tools 为你的允许名单；本示例只展示回复的调用形态

        .. rubric:: 行为要点

        - ``in_reply_to`` 应是 ``request()`` 产生的信封
          （``correlation_id`` 非 ``None``）；对 ``send()`` 产生的信封
          调用本方法属编程错误（无挂起的请求可匹配，回复被静默丢弃）。
        - 目标端点已注销时按总线侧规则：静默丢弃并发 ``warnings.warn``
          警告。
        - 不校验 ``payload`` 结构（问答双方自行约定）。

        :param in_reply_to: 待回复的 ``request`` 信号信封。
        :param payload: 回复负载。

        .. seealso:: :meth:`request`、:meth:`Communication.reply`
        """
        self._comm.reply(
            self.endpoint_id,
            in_reply_to.reply_to or in_reply_to.sender,
            in_reply_to.correlation_id,  # 前置条件：request 信封的 correlation_id 非 None
            payload,
        )

    def publish(self, topic: str, event: dict[str, Any]) -> None:
        """发布事件（自动注入 ``publisher=self.endpoint_id``）。

        .. rubric:: 功能介绍

        以 ``{**event, "publisher": self.endpoint_id}`` 合并后的字典
        调用 :meth:`Communication.publish`——调用方传入的 ``event`` 中
        已有的 ``publisher`` 键会被句柄身份覆盖（句柄路径不允许伪造
        发布者）。容错与 fire-and-forget 语义与总线方法一致。

        .. rubric:: 行为要点

        - ``event`` 含 ``"publisher"`` 键 → 被覆盖为
          ``self.endpoint_id`` （``event`` 字典内的键同被覆盖）。
        - 无订阅者 → 空操作。

        :param topic: 事件主题。
        :param event: 事件内容。

        .. seealso:: :meth:`Communication.publish`、:meth:`subscribe`
        """
        merged = {**event, "publisher": self.endpoint_id}  # 身份键后置：覆盖调用方自填的 publisher
        self._comm.publish(topic, merged)

    def subscribe(
        self, topic: str, callback: Callable[[EventEnvelope], Any] | None = None
    ) -> None:
        """订阅 topic（自动填 ``subscriber_id=self.endpoint_id``）。

        .. rubric:: 行为要点

        - ``callback`` 缺省时使用构造时注入的 ``on_event`` （Agent 端点
          经 ``use_comm()`` 即 dispatch 到 ``agent.hooks.on_event``）。
        - 同一 topic 重复订阅 → 覆盖旧回调。

        :param topic: 事件主题。
        :param callback: 订阅回调；``None`` 时用构造时注入的
            ``on_event``。

        .. seealso:: :meth:`Communication.subscribe`、:meth:`unsubscribe`
        """
        self._comm.subscribe(self.endpoint_id, topic, callback or self._on_event)

    def unsubscribe(self, topic: str) -> None:
        """取消对单个 topic 的订阅（自动填 ``subscriber_id=self.endpoint_id``）。

        订阅不存在时是幂等空操作（不抛异常）。

        :param topic: 事件主题。

        .. seealso:: :meth:`Communication.unsubscribe`
        """
        self._comm.unsubscribe(self.endpoint_id, topic)

    def destroy(self) -> None:
        """清理该端点在总线上的全部痕迹（可重复调用）。

        .. rubric:: 功能介绍

        依次执行：取消该端点全部订阅 → 注销端点 → 取消挂起的请求
        （对应的 ``await`` 收到 ``asyncio.CancelledError``）并清空挂起
        表。

        .. rubric:: 行为要点

        - 首次调用完成上述全部清理；后续调用为空操作（幂等）。
        - 清理路径可重入：Agent 销毁链、插件收尾、应用层手动清理可能
          重复触发同一句柄的销毁，幂等性由句柄自身保证。
        - 不影响总线其他端点与订阅；不触发任何钩子（销毁事件的观察走
          Agent 侧的 ``before_destroy`` 钩子，与本方法正交）。

        .. seealso:: :meth:`Communication.unregister_endpoint`、
            :meth:`Communication.unsubscribe_all`、:func:`use_comm`
        """
        if getattr(self, "_destroyed", False):  # 幂等：内部记录销毁状态，重复调用直接返回
            return
        self._destroyed = True
        self._comm.unsubscribe_all(self.endpoint_id)
        self._comm.unregister_endpoint(self.endpoint_id)  # 注销不存在端点 → KeyError（幂等由上 guard 保证）
        for future in self._pending_replies.values():
            future.cancel()  # 对应 await 收到 CancelledError；已完成 future 不受影响
        self._pending_replies.clear()


class CommPlugin(Plugin):
    """通信扩展插件——阶段一入口：创建总线并 provide。

    .. rubric:: 功能介绍

    ``runtime.use(CommPlugin())`` 时框架调用 ``install(runtime)``，创建
    全局 ``Communication`` 实例并以 ``communication_key`` provide 到
    Runtime provide 链根。此后任何 Agent 可经 ``use_comm(self)`` 启用
    实例级通信能力。框架核心发布时不预装本插件——未启用的通信扩展
    「从没存在过」。

    .. rubric:: 行为要点

    - ``install()`` 只做 provide 注册（插件约定：``install`` 只注册，
      约定定义见 :mod:`flowing.plugins`；本插件无工具、无配置命名
      空间、无钩子声明）。
    - 重复安装同名插件（再次 ``runtime.use(CommPlugin())``）→
      ``ValueError`` （一个 Runtime 同时只装一个同名插件，见
      :meth:`flowing.runtime.Runtime.use`）。
    - 生命周期：``runtime.shutdown()`` 插件收尾阶段调用总线
      ``_close()`` 清空端点表与订阅表（挂起请求的取消归各句柄
      ``destroy()``，总线不兜底）。

    .. seealso:: :class:`Communication`、:func:`use_comm`、
        :class:`flowing.plugins.Plugin`、:meth:`flowing.runtime.Runtime.use`
    """

    name: ClassVar[str] = "comm"
    """注册名（显式声明，无框架默认；推荐格式见 ``flowing.plugins``
    命名约定）。
    """

    dependencies: ClassVar[list[str]] = []   # 与基类 Plugin 的 ClassVar 对齐
    """依赖声明（类属性元数据）。本插件无依赖，为空列表；框架在
    ``use()`` 时做存在性与无环 DAG 校验。

    .. seealso:: :meth:`flowing.runtime.Runtime._check_dependencies`
    """

    def install(self, runtime: Runtime) -> None:
        """创建 ``Communication`` 总线并 ``provide(communication_key, bus)``。

        .. rubric:: 行为要点

        - 仅做 provide 注册（插件约定：``install`` 只注册），同步返回。
        - 后置条件：总线 ready，可被任何 Agent 经 ``inject`` 消费；
          总线的回收由插件收尾阶段负责。

        :param runtime: 当前安装的 Runtime 实例。

        .. seealso:: :data:`communication_key`、:class:`Communication`
        """
        bus = Communication()
        self._bus = bus   # 自留引用：shutdown() 收尾用
        runtime.provide(communication_key, bus)

    async def shutdown(self) -> None:
        """插件收尾：关闭通信总线。

        先于本方法，Agent 树已递归 destroy（各句柄已各自清理），此处经
        ``Communication._close()`` 做兜底回收（清空端点表与订阅表）。

        .. seealso:: :meth:`flowing.runtime.Runtime.shutdown`
        """
        self._bus._close()


def use_comm(agent: Agent, *, name: str | None = None) -> None:
    """为 Agent 实例启用通信能力——阶段二入口（``setup()`` 中调用）。

    .. rubric:: 功能介绍

    完成四件事：

    1. ``agent.inject(communication_key)`` 获取全局总线（未安装
       ``CommPlugin`` → :class:`flowing.errors.MissingProvideError`）；
    2. 确定端点 ID（``name`` 参数指定的语义名，缺省回退
       ``agent.node_id``），经 ``comm.create_handle()`` 注册端点并创建
       句柄；句柄的接收回调把收到的信号 / 事件 dispatch 到
       ``agent.hooks.on_signal`` / ``agent.hooks.on_event``；
    3. ``agent.hooks.declare("on_signal", by="comm", match_on="type")``
       与 ``agent.hooks.declare("on_event", by="comm", match_on="topic")``
       声明两个实例级钩子点；
    4. 在 ``before_destroy`` 上注册清理 handler（``by="comm"``）：
       Agent ``destroy()`` 时调用 ``handle.destroy()`` 注销端点、取消
       订阅、取消挂起的请求。

    句柄固定挂载为 ``agent.comm_handler``——命名遵循「插件绑定成员以
    注册名 underscore 版为前缀」的共同约定（见 ``flowing.plugins``
    模块 docstring），无改名参数。

    .. rubric:: 使用示例

    .. code-block:: python

        class CompanionAgent(Agent):
            async def setup(self, data_dir: str):
                use_comm(self)

                @self.hooks.on_signal["agent_message"]
                async def _(self, envelope: SignalEnvelope):
                    await self.enqueue_message(Message(
                        kind=MessageKind.PEER,
                        source=f"agent:{envelope.sender}",
                        content=[TextBlock(text=envelope.payload["body"])],
                        tags=["internal_message"],
                    ))
                    return envelope

    .. rubric:: 行为要点

    - 钩子点声明（同名且同 ``by``）幂等：恢复时 ``setup()`` 在新实例
      上执行、重复声明不报错（进程重启或同进程先 ``destroy()`` 再
      恢复，均无残留声明）；声明后任何代码都可向 ``on_signal`` /
      ``on_event`` 挂 handler，无需再声明（注册开放）。
    - handler 按 ``envelope.type`` / ``envelope.topic`` 经 ``fnmatch``
      规则过滤注册（``@agent.hooks.on_signal["<pattern>"]`` 装饰器
      形态）；handler 签名统一为 ``(agent, envelope) -> envelope``；
      可 ``raise Intercepted`` 阻止后续 handler（硬阻断，通信侧无后续
      动作）。
    - 同一实例重复调用：端点 ID 与已有句柄相同 → 复用该句柄（不重复
      注册端点、不重复挂清理 handler）；端点 ID 不同 → 以新 ID 另建
      句柄并覆盖 ``agent.comm_handler`` （旧句柄仍留在总线上，销毁
      Agent 时两个句柄都会清理）。正常管线中该情形不发生，此为兜底
      同一活实例上的意外二次启用。
    - 端点 ID 被其他端点占用 → ``setup()`` 期即抛
      :class:`flowing.errors.DuplicateEndpointError` （显式 ``name``
      与其他端点冲突同理）。
    - 通信信封永不自动进入对话通道——桥接（如 ``enqueue_message``）
      由 handler 显式完成；本函数不注册任何工具、不修改 prompt。
    - 前置条件：``CommPlugin`` 已经 ``runtime.use()`` 安装（否则
      ``MissingProvideError``）；本函数应在 ``setup()`` 中调用（钩子点
      声明属 setup 阶段契约，见
      :meth:`flowing.hooks.HookRegistry.declare`）。
    - 后置条件：Agent 可被总线寻址；``destroy()`` 时端点、订阅、挂起
      请求全部清理。

    :param agent: 要启用的 Agent 实例。
    :param name: 端点 ID 的显式语义名；``None`` 时回退
        ``agent.node_id``。

    .. seealso:: :class:`CommPlugin`、:class:`CommHandle`、
        :meth:`flowing.hooks.HookRegistry.declare`、
        :meth:`flowing.agent.Agent.enqueue_message`
    """
    comm: Communication = agent.inject(communication_key)  # 未装 CommPlugin → MissingProvideError
    endpoint_id = name or agent.node_id
    if hasattr(agent, "comm_handler") and agent.comm_handler.endpoint_id == endpoint_id:
        handle = agent.comm_handler  # 幂等防御条款：同实例同端点复用已注册句柄（不重复注册、不重复挂清理）
    else:
        async def _on_signal(envelope: SignalEnvelope) -> SignalEnvelope:
            # 闭包捕获 agent，dispatch 到实例钩子点；句柄自身不持 agent
            try:
                return await agent.hooks.on_signal.dispatch(agent, envelope)
            except Intercepted:
                return envelope  # 硬阻断信号：链已停，通信侧无后续动作（dispatch 已记日志）
            except Exception:
                _logger.exception(  # 记日志不逃逸进事件循环（后台任务路径）
                    "on_signal dispatch raised (endpoint=%r, type=%r)",
                    endpoint_id, envelope.type)
                return envelope

        async def _on_event(envelope: EventEnvelope) -> EventEnvelope:
            try:
                return await agent.hooks.on_event.dispatch(agent, envelope)
            except Intercepted:
                return envelope
            except Exception:
                _logger.exception(
                    "on_event dispatch raised (endpoint=%r, topic=%r)",
                    endpoint_id, envelope.topic)
                return envelope

        handle = comm.create_handle(
            endpoint_id, on_signal=_on_signal, on_event=_on_event)
        agent.comm_handler = handle

        def _cleanup(_a: Agent, _value: Any = None) -> None:
            handle.destroy()  # 闭包直接引用句柄，不反查属性名（dispatch 恒两参调用）

        agent.hooks.before_destroy(_cleanup, by="comm")
    agent.hooks.declare("on_signal", by="comm", match_on="type")
    agent.hooks.declare("on_event", by="comm", match_on="topic")
