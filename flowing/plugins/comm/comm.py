"""flowing.plugins.comm —— 通信扩展（CommPlugin / Communication / CommHandle / 信封 / use_comm）。

.. rubric:: 模块定位

通信扩展是 Flowing 的**内置扩展**（随 ``flowing`` 包发布但不自动启用），
不是框架核心。启用遵循全局统一的**双层启用**模型：

- 阶段一（Runtime 安装）：``runtime.use(CommPlugin())`` →
  ``CommPlugin.install(runtime)`` 创建全局 ``Communication`` 总线实例并经
  ``runtime.provide(communication_key, bus)`` 注入 provide 链根。
- 阶段二（Agent 启用）：Agent 的 ``setup()`` 中调用 ``use_comm(self)``，
  为**该实例**创建 ``CommHandle``、注册端点、声明 ``on_signal`` / ``on_event``
  钩子点并挂载 ``self.comm_handler``。

不调用 ``use_comm()`` 的 Agent 零开销：没有 ``agent.comm_handler`` 属性、没有
``on_signal`` / ``on_event`` 钩子点、不在总线端点表中、其他端点无法经总线
寻址到它。零开销的含义是「扩展代码路径从未加载到该实例上」，而非「被 skip」。

.. rubric:: 两条通道完全隔离

框架内存在两条互不交叉的消息通道：

- **对话通道**：``Message`` → Agent 消息队列 → 逻辑 Turn 循环 → LLM 上下文。
- **通信通道**（本模块）：``SignalEnvelope`` / ``EventEnvelope`` → 端点回调 /
  钩子 dispatch——**永不进入 LLM context**、永不落盘为消息树节点、与
  ``Message.id`` / ``parent_id`` 链无任何关系。

需要桥接时由 handler 显式完成（例如在 ``on_signal`` handler 中
``enqueue_message(Message(kind=MessageKind.PEER, ...))`` 把信号内容转入对话
通道）。框架不做任何自动桥接。

.. rubric:: 端点与命名规则

端点 = 可通信实体在总线上的地址（ID 即寻址）。端点 ID 统一为**语义化名称**
（不使用 UUID）：可读、可路由、可发现；唯一性由总线全局注册表保证，重名注册
抛 ``DuplicateEndpointError``。

- **Agent 端点**：``use_comm()`` 时自动注册，ID 取
  ``use_comm(name=...)`` 显式指定的语义名，缺省回退 ``agent.node_id``
  （语义名只存在于唤起方/应用层，Agent 实例不自持——A15 裁决：
  ``simplename`` 字段已删除）。
- **UI 端点**：应用层注册，一个 Runtime 通常一个（如 ``ui-main``）。
- **系统端点**：Runtime 自身等内部组件（Cron 调度器**不是**总线使用者——
  cron 不经通信总线，见 :mod:`flowing.plugins.cron` 非行为）。

端点种类在总线层面无结构差异——全部是「ID → 接收回调」的路由表条目。

.. rubric:: 总线四操作语义（契约要点）

- ``send``：点对点，**立即返回**，不等待接收方处理完成、不支持回复。
- ``request``：点对点 + 阻塞等回复，经 ``correlation_id`` 匹配 pending
  future；超时抛 ``SignalTimeoutError``。
- ``publish``：对 topic 全部订阅者广播，**容错**（单订阅者异常静默忽略，
  不影响其他订阅者）且 **fire-and-forget**（回调返回 awaitable 时
  ``asyncio.create_task`` 调度，发布者不阻塞）。
- ``reply``：回复一个 ``request``，使用保留信号类型 ``'_reply'``；
  接收侧句柄检测该类型并匹配 pending future，**不 dispatch 到钩子**。

.. rubric:: 信封自动填充

``SignalEnvelope`` 的 ``sender`` / ``correlation_id`` / ``reply_to`` /
``created_at`` 与 ``EventEnvelope`` 的 ``publisher`` / ``created_at`` 均由
**总线在构造信封时自动填充**，发送方只提供 ``type`` / ``topic`` 与负载。
``created_at`` 为时区无关（naive）UTC ``datetime``，用于审计、排序与延迟
测量；显示层负责转本地时区。

.. rubric:: 单进程边界

通信总线是**单进程内**基础设施：存活端点预计不超过 100 个，单事件循环 +
fire-and-forget 在该规模下足够。框架不做分布式假设——无跨进程通信、无信封
序列化格式承诺、无网络拓扑。信封对象按引用传递，订阅者回调与发布者运行在同
一事件循环。大规模（上万端点）属后续演进方向，初版不为此设计。

.. rubric:: 生命周期

总线生命周期 = Runtime 生命周期：``install`` 时创建 → Agent 经 ``inject``
消费 → ``runtime.shutdown()`` 时销毁（清空端点表、订阅表）。pending futures
的清理由各句柄 ``destroy()`` 各自负责——单个 Agent 的 ``handle.destroy()``
清理**自己的**端点、订阅与 pending futures；总线 ``_close()`` 只清两张表，
不兜底取消 futures（S-22 裁决：总线无句柄名单，物理上到不了；shutdown 前
未 destroy 的句柄，其 pending future 悬至 request 超时或事件循环回收）。
总线整体的回收发生在 Runtime shutdown（递归 destroy Agent 树之后、插件
收尾阶段）。

.. rubric:: 直接操作总线的立场

``agent.comm_handler``（``CommHandle``）是推荐路径——句柄自动携带身份
（sender/publisher），是「身份正确」的保证。技术上任何 Agent 都能经
``self.runtime`` inject 到总线并直接调用 ``send(sender=...)`` 冒充其他端点；
框架接受这一约定（与插件「install 直暴露 Runtime」同构），不做结构禁止。
直接操作总线属高级用法，调用方须自行保证身份字段正确。

.. seealso::

    - :mod:`flowing.plugins.cron` —— 定时扩展（不经通信总线；未来自定义
      执行器可经 comm 转 Signal，属前瞻方向）。
    - :mod:`flowing.hooks` —— ``HookRegistry.declare`` / dispatch 语义。
    - :mod:`flowing.message` —— 对话通道的 ``Message`` 模型（与本模块正交）。
    - :mod:`flowing.errors` —— ``DuplicateEndpointError`` /
      ``SignalDeliveryError`` / ``SignalTimeoutError`` / ``Intercepted``。
"""

from __future__ import annotations   # S-43 裁决③：注解延迟求值（Communication→CommHandle 前向引用）

import asyncio
from collections.abc import Callable
from typing import Any, ClassVar

from flowing.agent import Agent
from flowing.params import InjectionKey
from flowing.plugins import Plugin
from flowing.runtime import Runtime

from .models import EventEnvelope, SignalEnvelope

communication_key: InjectionKey["Communication"] = InjectionKey("communication")
"""功能/动机：全局 ``Communication`` 总线实例在 provide 链上的注入键。
``CommPlugin.install()`` 以该键 provide 总线；``use_comm()`` 与应用层经
``agent.inject(communication_key)`` 消费。

行为边界：键名为 ``"communication"``；同名 provide key 冲突时后注册者
报错（框架全局约定）。未安装 ``CommPlugin`` 时 inject 该键抛
``MissingProvideError``。

.. seealso:: :class:`flowing.params.InjectionKey`、:func:`use_comm`、
:meth:`flowing.agent.Agent.inject`
"""


class Communication:
    """单进程通信总线——端点注册表 + 信号路由 + 发布订阅派发。

    .. rubric:: 功能介绍

    通信扩展的全局服务面：维护 ``_endpoints``（端点 ID → 接收回调）与
    ``_subscriptions``（topic → subscriber_id → 回调）两张表，提供
    ``send`` / ``request`` / ``publish`` / ``reply`` 四操作与
    ``subscribe`` / ``unsubscribe`` / ``unsubscribe_all`` 订阅管理。
    由 ``CommPlugin.install()`` 创建并以 ``communication_key`` provide 到
    Runtime provide 链根，生命周期与 Runtime 相同。

    .. rubric:: 设计动机

    总线是「机制」：它只做寻址、路由、关联与派发，不理解任何业务语义
    （不校验 payload、不感知 Agent/UI 的区别）。订阅表与广播派发归总线，
    收到后的二次派发归端点（``CommHandle``）——两层分工使总线保持极简，
    也使非 Agent 端点（UI、系统组件）与 Agent 端点共享同一基础设施。

    .. rubric:: 使用示例

    .. code-block:: python

        # main.py —— 阶段一：安装插件即创建总线
        runtime.use(CommPlugin())

        # 应用层注册 UI 端点（阶段一之后任意时刻）
        comm = runtime.inject(communication_key)
        ui_handle = comm.create_handle(endpoint_id="ui-main")
        ui_handle.subscribe("assistant_replied", on_assistant_replied)

    .. rubric:: 行为规约

    - 期待行为：``send`` 查端点表路由后立即返回；``request`` 生成
      ``correlation_id``、在 ``reply_handler`` 上登记 pending future 并
      阻塞等待；``publish`` 遍历订阅表逐个派发；``reply`` 以保留类型
      ``'_reply'`` 定向投递。
    - 非行为：不做持久化（信封不落盘）；不做跨进程传输；不对 payload
      做任何校验或转换；不提供端点枚举/发现 API（初版仅约定 ID 语义化
      可发现，发现机制由应用层目录服务承担）。
    - 边缘情况：目标端点不存在 → ``SignalDeliveryError``；向无订阅者的
      topic 发布 → 空操作；订阅者回调异常 → 捕获并静默忽略（不影响其他
      订阅者与发布者）；回调返回 awaitable → ``asyncio.create_task``
      转为后台任务，发布者不等待。
    - 前置条件：实例只能经 ``CommPlugin.install()`` 创建并进入 provide
      链；应用层不应自行实例化。
    - 后置条件：``runtime.shutdown()`` 的插件收尾阶段清空两张表。
      pending futures 的取消**不**由总线兜底——它是各句柄 ``destroy()``
      的责任（S-22 裁决：总线不持有句柄名单，物理上到不了 futures；
      shutdown 前未 destroy 的句柄，其 future 悬至 request 超时或
      事件循环回收时取消）。
    - 不变量：同一时刻一个 ``endpoint_id`` 至多映射一个接收回调；
      一个 subscriber 对同一 topic 至多一条订阅（重复 ``subscribe``
      覆盖旧回调）。

    .. rubric:: 测试案例

    - 前置：已 ``install`` 的总线；操作：``register_endpoint("a", h)``
      后再次 ``register_endpoint("a", h2)``；期望：第二次调用抛
      ``DuplicateEndpointError``，且 ``"a"`` 仍映射到 ``h``。
    - 前置：端点 ``"a"`` 已注册；操作：``send(sender="s", target="a",
      type="t", payload={})``；期望：调用立即返回，``h`` 收到
      ``SignalEnvelope`` 且 ``sender=="s"``、``created_at`` 已填充。
    - 前置：topic ``"x"`` 有两个订阅者，第一个回调抛异常；操作：
      ``publish("x", {...})``；期望：第二个订阅者仍被调用，``publish``
      本身不抛异常。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.plugins.comm.CommHandle`` 发送/订阅系列方法
      （每次经句柄操作）；``flowing.plugins.comm.use_comm``（每个
      启用 Agent 的 ``setup()``，经 ``create_handle``）
    - 实例化方：``flowing.plugins.comm.CommPlugin.install``（时机：
      阶段一 ``runtime.use(CommPlugin())``，每次安装）

    .. seealso:: :class:`CommPlugin`、:class:`CommHandle`、
       :data:`communication_key`、:func:`use_comm`
    """

    _endpoints: dict[str, Callable[[SignalEnvelope], Any]]
    """内部存储：端点 ID → 接收回调。内部 API，不属稳定契约；
    禁止插件/应用层直接读写（插件约定 R2/R3）。
    """
    _subscriptions: dict[str, dict[str, Callable[[EventEnvelope], Any]]]
    """内部存储：topic → subscriber_id → 回调。内部 API，不属稳定契约。
    """

    def register_endpoint(
        self, endpoint_id: str, handler: Callable[[SignalEnvelope], Any]
    ) -> None:
        """注册端点（**不幂等**）。

        .. rubric:: 功能介绍

        把 ``endpoint_id`` 与接收回调绑定进总线路由表。handler 在每次有
        信号投递到该端点时被调用（同步直接调用；返回 awaitable 时转为
        ``asyncio.create_task``）。

        .. rubric:: 设计动机

        重复注册报错而非覆盖，是为了让「端点 ID 冲突」在注册时刻暴露——
        语义化名称的可路由性依赖唯一性，静默覆盖会使信号被路由到错误的
        实体且难以排查。

        .. rubric:: 使用示例

        .. code-block:: python

            def on_signal(env: SignalEnvelope): ...

            comm.register_endpoint("ui-main", on_signal)

        通常不直接调用——``create_handle()`` / ``use_comm()`` 内部经它注册。

        .. rubric:: 行为规约

        - 期待行为：注册成功后该端点立即可被 ``send`` / ``request`` 寻址。
        - 非行为：不做 handler 签名校验；不记录注册者身份。
        - 边缘情况：重复注册同一 ID → ``DuplicateEndpointError``，原映射
          不变；空字符串 ID 属非法输入（实现应拒绝，但初版仅约定
          「ID 为非空语义化名称」，不做结构强制）。

        :raises DuplicateEndpointError: —— ``endpoint_id`` 已存在时。

        .. rubric:: 测试案例

        - 前置：空总线；操作：注册 ``"a"`` 再注册 ``"a"``；期望：第二次
          抛 ``DuplicateEndpointError``。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``flowing.plugins.comm.Communication.create_handle``
          （每次创建句柄）；应用层直接注册端点属用户代码

        .. seealso:: :meth:`unregister_endpoint`、:meth:`create_handle`
        """
        self._endpoints[endpoint_id] = handler  # 重复注册 → DuplicateEndpointError 且原映射不变（分支略）

    def unregister_endpoint(self, endpoint_id: str) -> None:
        """注销端点。

        .. rubric:: 功能介绍

        从路由表移除端点。注销后向该端点 ``send`` / ``request`` 抛
        ``SignalDeliveryError``。

        .. rubric:: 设计动机

        注销不存在的端点时**不**做特殊处理——由内部 ``del`` 自然抛
        ``KeyError``。这是有意的「小接口」立场：注销路径几乎总是
        ``destroy()`` 清理链的一部分，重复注销属编程错误，应当暴露而非
        吞掉。``CommHandle.destroy()`` 的幂等由句柄层自行保证，不依赖
        本方法的容错。

        .. rubric:: 行为规约

        - 边缘情况：注销不存在的端点 → 自然 ``KeyError``；注销后该端点的
          在途 ``request`` 回复不受影响（回复按 ``correlation_id`` 匹配
          pending future，不查端点表）。

        :raises KeyError: —— 端点不存在时。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``flowing.plugins.comm.CommHandle.destroy``（每次句柄
          销毁）

        .. seealso:: :meth:`register_endpoint`、:meth:`CommHandle.destroy`
        """
        del self._endpoints[endpoint_id]  # 不存在 → 自然 KeyError（docstring 规约，不做容错）

    def create_handle(
        self,
        endpoint_id: str,
        *,
        on_signal: Callable[[SignalEnvelope], Any] | None = None,
        on_event: Callable[[EventEnvelope], Any] | None = None,
    ) -> CommHandle:
        """创建通信句柄并注册端点——所有端点的统一创建入口。

        .. rubric:: 功能介绍

        原子地完成「注册端点 + 构造 ``CommHandle``」：返回的句柄自动携带
        身份（发送时填 ``sender`` / ``publisher``），接收回调经
        ``on_signal`` / ``on_event`` 注入（缺省为 noop，收到的信号/事件
        被忽略）。

        .. rubric:: 设计动机

        统一句柄 + 闭包注入取代了旧设计「handle 持 ``agent`` 字段 +
        ``AgentCommHandle`` 子类」——后者只适用于 Agent 端点（UI/系统等
        非 Agent 端点没有 agent），而闭包注入在 ``use_comm()`` 的函数域内
        即可捕获 agent，单一 ``CommHandle`` 类服务全部端点类型。

        .. rubric:: 使用示例

        .. code-block:: python

            ui_handle = comm.create_handle(endpoint_id="ui-live2d")
            ui_handle.subscribe("assistant_replied", on_assistant_replied)

        .. rubric:: 行为规约

        - 期待行为：等价于 ``register_endpoint(endpoint_id, on_signal)``
          + 构造句柄；端点 ID 冲突同样抛 ``DuplicateEndpointError``。
        - 边缘情况：``on_signal=None`` 时端点仍注册，收到的信号被
          ``_default_noop`` 忽略（不产生任何副作用）。

        :raises DuplicateEndpointError: —— ``endpoint_id`` 已存在时。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.comm.Communication.register_endpoint``
          （每次创建）、``flowing.plugins.comm.CommHandle.__init__``
          （每次创建）
        - 被调：``flowing.plugins.comm.use_comm``（时机：阶段二，每个
          启用 Agent 的 ``setup()``）；UI/系统端点的直接调用属应用层

        .. seealso:: :class:`CommHandle`、:func:`use_comm`
        """
        self.register_endpoint(endpoint_id, on_signal)  # None 时句柄侧 _default_noop 兜底（docstring 规约）
        handle = CommHandle(endpoint_id, self, on_signal=on_signal, on_event=on_event)
        return handle

    def send(self, sender: str, target: str, type: str, payload: dict[str, Any]) -> None:
        """点对点发送信号，立即返回（即发即忘）。

        .. rubric:: 功能介绍

        构造 ``SignalEnvelope``（自动填充 ``sender`` 与 ``created_at``；
        ``correlation_id`` / ``reply_to`` 为 ``None``），查端点表路由到
        ``target`` 的接收回调，然后**立即返回**——不等待接收方处理完成，
        不支持回复。

        .. rubric:: 设计动机

        通知、指令类场景的天然语义：发送方只关心「已投递给总线」，不关心
        接收方的处理结果。需要结果的场景用 ``request()``。

        .. rubric:: 使用示例

        .. code-block:: python

            agent.comm_handler.send(
                target="payment-agent",
                type="agent_message",
                payload={"subject": "对账请求", "body": "..."},
            )

        .. rubric:: 行为规约

        - 期待行为：同步完成路由；接收回调同步部分直接执行，返回
          awaitable 则转为后台 Task；本方法返回 ``None``。
        - 非行为：不等待、不回传接收方结果；不重试、不排队（接收方处理
          失败不补偿）。
        - 边缘情况：``target`` 不存在 → ``SignalDeliveryError``（发送方
          **同步**感知，这是发送方唯一能感知的失败）；接收回调异常 →
          捕获并静默忽略，不传播给发送方（与 ``publish`` 容错一致）。
        - 前置条件：``sender`` 应为已注册端点 ID；框架不强制（直接操作
          总线可冒充，属已知高级用法风险）。

        :raises SignalDeliveryError: —— ``target`` 端点不存在时。

        .. rubric:: 测试案例

        - 前置：``"b"`` 已注册；操作：``send("a", "b", "t", {})``；期望：
          立即返回，``"b"`` 的回调被调用且信封 ``sender=="a"``。
        - 前置：无端点 ``"ghost"``；操作：``send("a", "ghost", "t", {})``；
          期望：抛 ``SignalDeliveryError``。

        .. rubric:: 调用关系（审计）

        - 调用：构造 ``SignalEnvelope``（每次发送，自动填充
          ``sender`` / ``created_at``）；目标端点接收回调（每次路由
          投递）
        - 被调：``flowing.plugins.comm.CommHandle.send``（每次经句柄
          发送）；直接操作总线属应用层高级用法

        .. seealso:: :meth:`request`、:meth:`CommHandle.send`
        """
        envelope = SignalEnvelope(sender=sender, type=type, payload=payload, created_at=...)  # created_at：naive UTC，总线填充
        handler = self._endpoints[target]  # target 不存在 → SignalDeliveryError（查表分支略）
        handler(envelope)  # 返回 awaitable 时转 asyncio.create_task（分支略）

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

        生成 ``correlation_id``（UUID），在 ``reply_handler`` 的
        ``_pending_replies`` 中登记 future，发送带 ``correlation_id`` 与
        ``reply_to=sender`` 的信封，然后阻塞等待对端 ``reply()`` 回传的
        payload。

        .. rubric:: 设计动机

        权限请求、数据查询等场景需要「问-答」语义；关联经显式
        ``correlation_id`` 而非调用栈，使回复可以跨越任意异步边界（对端
        可以在另一个 Task、另一个回合里回复）。

        .. rubric:: 使用示例

        .. code-block:: python

            result = await agent.comm_handler.request(
                target="ui-main",
                type="permission_request",
                payload={"tool_name": tool_call.name},
                timeout=120.0,
            )
            if not result.get("approved"):
                raise Intercepted(result.get("reason", "用户拒绝"))

        通常经 ``CommHandle.request()`` 调用（自动填 ``sender`` 与
        ``reply_handler``）；直接调用本方法属高级用法。

        .. rubric:: 行为规约

        - 期待行为：收到匹配 ``correlation_id`` 的 ``'_reply'`` 信封后以
          其 ``payload`` 为返回值完成 future；回复信封**不** dispatch 到
          任何钩子。
        - 非行为：不校验对端是否「真的会回复」；超时后 future 移除，迟到的
          回复被静默丢弃。
        - 边缘情况：``timeout`` 到期 → ``SignalTimeoutError``；``target``
          不存在 → 发送阶段即抛 ``SignalDeliveryError``（不会进入等待）；
          等待期间 ``reply_handler.destroy()`` → future 被取消，``await``
          收到 ``asyncio.CancelledError``；``timeout=None`` 表示不限时
          （仅 destroy 可解除等待）。
        - 不变量：一个 ``correlation_id`` 至多完成一次；同时并发的多个
          ``request`` 互不干扰。

        :raises SignalDeliveryError: —— ``target`` 端点不存在时。
        :raises SignalTimeoutError: —— 等待超过 ``timeout`` 秒时。
        :raises asyncio.CancelledError: —— 等待期间句柄被销毁时。

        .. rubric:: 测试案例

        - 前置：``"b"`` 已注册且其 handler 调用 ``handle.reply(env,
          {"ok": True})``；操作：``await request(...)``；期望：返回
          ``{"ok": True}``，且 ``"b"`` 侧 ``on_signal`` 未收到
          ``'_reply'`` 类型信封。
        - 前置：``"b"`` 永不回复；操作：``await request(..., timeout=0.05)``；
          期望：约 0.05s 后抛 ``SignalTimeoutError``。
        - 前置：request 等待中；操作：``handle.destroy()``；期望：
          ``await`` 侧收到 ``CancelledError``。

        .. rubric:: 调用关系（审计）

        - 调用：构造 ``SignalEnvelope``（每次 request，填充
          ``correlation_id`` / ``reply_to``）；登记
          ``reply_handler._pending_replies``（每次 request）
        - 被调：``flowing.plugins.comm.CommHandle.request``（每次经
          句柄 request）；直接调用本方法属应用层高级用法

        .. seealso:: :meth:`CommHandle.request`、:meth:`CommHandle.reply`
        """
        correlation_id = "..."  # 实际为 UUID 字符串（uuid 未具名导入，占位）
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        reply_handler._pending_replies[correlation_id] = future  # 登记 pending future
        envelope = SignalEnvelope(
            sender=sender, type=type, payload=payload,
            correlation_id=correlation_id, reply_to=sender,
            created_at=...,  # created_at：naive UTC，总线填充
        )
        handler = self._endpoints[target]  # target 不存在 → SignalDeliveryError（查表分支略）
        handler(envelope)  # 返回 awaitable 时转 asyncio.create_task（分支略，同 send）
        if timeout is None:
            result: dict[str, Any] = await future  # destroy 取消 → CancelledError 自然传播
        else:
            result = await asyncio.wait_for(future, timeout)  # 超时 → SignalTimeoutError（TimeoutError 转换分支略）
        # 超时/取消路径：future 从 _pending_replies 移除，迟到回复静默丢弃（finally 略）
        return result

    def reply(self, sender: str, target: str, correlation_id: str, payload: dict[str, Any]) -> None:
        """以保留类型 ``'_reply'`` 定向投递回复信封（低层入口）。

        .. rubric:: 功能介绍

        构造 ``type='_reply'`` 的 ``SignalEnvelope`` 并投递给 ``target``。
        接收侧句柄检测保留类型后按 ``correlation_id`` 匹配 pending
        future——**不 dispatch 到 ``on_signal`` 钩子**，应用层永远观察不到
        回复信封。

        .. rubric:: 设计动机

        回复是 request-reply 机制的内部完成事件，不是业务信号；用保留类型
        走独立路径，避免回复混入业务 dispatch 链触发无关 handler。

        .. rubric:: 行为规约

        - 边缘情况：``correlation_id`` 无匹配 pending future（已超时或
          已销毁）→ 静默丢弃；``target`` 端点已注销 → 静默丢弃（回复路径
          不抛 ``SignalDeliveryError``——发送方可能已不在，报错无人接收），
          但经 ``warnings.warn`` 报告一条警告——回复丢失通常意味着
          请求方异常退出或超时配置不当，应可观测而非无声。
        - 非行为：不 dispatch、不入队、不产生任何钩子事件。

        .. rubric:: 调用关系（审计）

        - 调用：构造 ``type='_reply'`` 的 ``SignalEnvelope``（每次
          reply）；目标端点接收回调（每次投递）
        - 被调：``flowing.plugins.comm.CommHandle.reply``（每次回复
          request）

        .. seealso:: :meth:`request`、:meth:`CommHandle.reply`
        """
        envelope = SignalEnvelope(
            sender=sender, type="_reply", payload=payload,
            correlation_id=correlation_id, created_at=...,  # created_at：naive UTC，总线填充
        )
        handler = self._endpoints[target]  # 端点已注销 → 静默丢弃并 warnings.warn（分支略）
        handler(envelope)  # 接收侧按 correlation_id 匹配 pending future，不 dispatch 钩子

    def publish(self, topic: str, event: dict[str, Any]) -> None:
        """向 topic 的全部订阅者广播事件（容错 + fire-and-forget）。

        .. rubric:: 功能介绍

        构造 ``EventEnvelope``（自动填充 ``created_at``；``publisher`` 取
        ``event`` 中已有值，``CommHandle.publish()`` 会在调用前注入），
        遍历订阅表把信封派发给每个订阅回调。

        .. rubric:: 设计动机

        状态广播、表情控制、文件变更通知等场景是典型的一对多：发布者不知道
        也不应知道订阅者是谁。**容错**（单订阅者异常静默忽略）与
        **fire-and-forget**（awaitable 回调转后台 Task）保证一个坏订阅者
        或一个慢订阅者不拖垮发布者与同 topic 的其他订阅者。

        .. rubric:: 使用示例

        .. code-block:: python

            self.comm_handler.publish("assistant_replied", {
                "text": text,
                "emotion": "happy",
            })

        .. rubric:: 行为规约

        - 期待行为：订阅回调按订阅表遍历顺序逐个调用；同步回调直接执行
          完毕，返回 awaitable 的回调经 ``asyncio.create_task`` 调度后
          立即继续下一个；本方法在全部回调「已启动」后即返回。
        - 非行为：不等待任何回调完成；不向发布者回传任何结果；不做
          重试与死信处理。
        - 边缘情况：无订阅者 → 空操作；某回调抛异常 → 捕获并静默忽略，
          其余订阅者不受影响；同一订阅者重复订阅同一 topic → 覆盖旧回调。
        - 后置条件：返回时不保证任何 awaitable 回调已完成。

        .. rubric:: 测试案例

        - 前置：topic ``"x"`` 订阅者 A（抛异常）、B（正常）；操作：
          ``publish("x", {})``；期望：B 被调用，``publish`` 不抛。
        - 前置：topic ``"y"`` 无订阅者；操作：``publish("y", {})``；期望：
          正常返回，无任何副作用。

        .. rubric:: 调用关系（审计）

        - 调用：构造 ``EventEnvelope``（每次 publish）；逐个调用订阅
          回调，返回 awaitable 时经 ``asyncio.create_task`` 调度
          （每次 publish）
        - 被调：``flowing.plugins.comm.CommHandle.publish``（每次经
          句柄发布）；直接操作总线属应用层高级用法

        .. seealso:: :meth:`subscribe`、:meth:`CommHandle.publish`
        """
        envelope = EventEnvelope(
            topic=topic, event=event,
            publisher=event.get("publisher"),  # publisher 取 event 中已有值（docstring 规约）
            created_at=...,  # naive UTC，总线填充
        )
        callbacks = self._subscriptions[topic]  # 无订阅者 → 空操作（分支略）
        # 遍历 callbacks 逐个派发：同步直接执行、awaitable 经 asyncio.create_task、
        # 异常静默忽略（for/try 属控制流，略；语义见 docstring）

    def subscribe(
        self,
        subscriber_id: str,
        topic: str,
        callback: Callable[[EventEnvelope], Any],
    ) -> None:
        """登记订阅（topic → subscriber_id → callback）。

        .. rubric:: 功能介绍

        把 ``callback`` 登记为 ``subscriber_id`` 对 ``topic`` 的订阅回调。
        订阅表与广播派发归总线；回调内部的二次派发（到 ``agent.hooks`` 或
        自定义逻辑）归端点层。

        .. rubric:: 行为规约

        - 期待行为：登记后立即生效，下一次 ``publish(topic, ...)`` 即派发
          到该回调。
        - 边缘情况：同一 ``subscriber_id`` 对同一 ``topic`` 重复订阅 →
          **覆盖**旧回调（一个订阅者一个 topic 至多一条订阅）；订阅不
          要求 ``subscriber_id`` 是已注册端点（但 ``CommHandle`` 路径
          下它总是句柄的 ``endpoint_id``）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``flowing.plugins.comm.CommHandle.subscribe``（每次经
          句柄订阅）

        .. seealso:: :meth:`unsubscribe`、:meth:`publish`
        """
        self._subscriptions[topic][subscriber_id] = callback  # topic 键缺省创建；重复订阅覆盖旧回调（细节略）

    def unsubscribe(self, subscriber_id: str, topic: str) -> None:
        """取消单个订阅。

        .. rubric:: 行为规约

        - 边缘情况：订阅不存在时为**幂等空操作**（不抛异常）——与
          ``unregister_endpoint`` 的 ``KeyError`` 语义有意不同：取消订阅
          常见于清理路径，重复清理不应报错；此差异是
          ``CommHandle.destroy()`` 幂等友好的一部分。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``flowing.plugins.comm.CommHandle.unsubscribe``（每次
          经句柄取消）

        .. seealso:: :meth:`subscribe`、:meth:`unsubscribe_all`
        """
        del self._subscriptions[topic][subscriber_id]  # 订阅不存在时幂等空操作（分支略，docstring 规约）

    def unsubscribe_all(self, subscriber_id: str) -> None:
        """取消某订阅者的全部订阅（清理路径用）。

        .. rubric:: 行为规约

        - 边缘情况：该订阅者没有任何订阅 → 幂等空操作。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``flowing.plugins.comm.CommHandle.destroy``（每次句柄
          销毁）

        .. seealso:: :meth:`unsubscribe`、:meth:`CommHandle.destroy`
        """
        # 遍历 _subscriptions 移除该 subscriber_id 的全部条目（for 属控制流，略；
        # 该订阅者无订阅时幂等空操作，docstring 规约）

    def _close(self) -> None:
        """总线关闭：清空端点表、订阅表。

        内部 API，不属稳定契约。由 ``CommPlugin`` 在 ``runtime.shutdown()``
        的插件收尾阶段调用；先于本方法，Agent 树已递归 destroy（各句柄
        已各自清理自己的端点、订阅与 pending futures），此处做兜底回收
        ——但**不兜底取消 pending futures**（S-22 裁决：futures 由各
        ``CommHandle._pending_replies`` 持有，总线不持有句柄名单，物理上
        到不了；取消是各句柄 ``destroy()`` 的责任。shutdown 前未 destroy
        的句柄——如 UI/系统端点——其 pending future 悬至 request 超时或
        事件循环回收时取消）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``CommPlugin.shutdown()``（时机：
          ``runtime.shutdown()`` 插件收尾阶段，按 install 顺序逐个 await）

        .. seealso:: :meth:`flowing.runtime.Runtime.shutdown`
        """
        self._endpoints.clear()
        self._subscriptions.clear()
        # 不兜底取消 pending futures（S-22 裁决）：futures 由各
        # CommHandle._pending_replies 持有，总线无句柄名单可到；
        # 取消责任归各句柄 destroy()，未 destroy 者悬至超时/循环回收。


class CommHandle:
    """通用通信句柄——任何端点（Agent / UI / 系统组件）使用的统一句柄，无子类。

    .. rubric:: 功能介绍

    端点持有者的操作面：发送类方法（``send`` / ``request`` / ``publish``）
    自动填充自己的身份（sender / publisher），订阅类方法
    （``subscribe`` / ``unsubscribe``）自动填 ``subscriber_id``，
    ``destroy()`` 一次性清理该端点在总线上的全部痕迹。

    .. rubric:: 设计动机

    **统一无子类**：旧设计「handle 持 ``agent`` 字段 + ``AgentCommHandle``
    子类」已废除——它只适用于 Agent 端点，且完全不必需：``use_comm()``
    的函数域内就有 agent，接收回调经**闭包捕获**注入即可。句柄的推荐地位
    来自「自动携带身份」：经句柄发送不可能写错 ``sender``，直接操作总线
    则可以冒充任意端点（已知风险，框架接受约定）。

    .. rubric:: 使用示例

    .. code-block:: python

        # Agent 端点（由 use_comm 创建，回调闭包 dispatch 到 agent.hooks）
        use_comm(self)
        self.comm_handler.send(target="payment-agent", type="query", payload={...})

        # 非 Agent 端点（UI 组件，自定义回调）
        ui_handle = comm.create_handle(endpoint_id="ui-live2d")
        ui_handle.subscribe("assistant_replied", on_assistant_replied)

    .. rubric:: 行为规约

    - 不变量：``endpoint_id`` 构造后不可变；一个句柄对应总线上恰好一个
      端点注册项（``destroy()`` 后对应零个）。
    - 边缘情况：未注入 ``on_signal`` / ``on_event`` 时收到的信号/事件
      被 ``_default_noop`` 忽略；``destroy()`` 可重复调用（幂等友好）。
    - 后置条件：``destroy()`` 后该句柄上所有方法除 ``destroy`` 外的行为
      不再有任何保证（发送会抛 ``SignalDeliveryError``——自己的端点已
      注销，若向自己发送；向他人发送仍可工作，因为发送只查目标端点表）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.plugins.comm.use_comm``（每个启用 Agent 的
      ``setup()``，挂载为 ``agent.comm_handler``）
    - 实例化方：``flowing.plugins.comm.Communication.create_handle``
      （每次创建句柄；``use_comm`` 与 UI/系统端点创建均经此）

    .. seealso:: :class:`Communication`、:func:`use_comm`、
       :class:`SignalEnvelope`、:class:`EventEnvelope`
    """

    endpoint_id: str
    """端点 ID（语义化名称），构造时确定、不可变。发送/订阅路径上的
    身份字段全部由它自动填充。
    
    .. seealso:: :meth:`Communication.create_handle`
    """
    _comm: Communication
    """所属总线引用。内部 API，不属稳定契约。
    """
    _on_signal: Callable[[SignalEnvelope], Any]
    """注入的信号接收回调（缺省 ``_default_noop``）。内部 API，不属稳定契约。
    """
    _on_event: Callable[[EventEnvelope], Any]
    """注入的事件接收回调（缺省 ``_default_noop``）。内部 API，不属稳定契约。
    """
    _pending_replies: dict[str, asyncio.Future]
    """correlation_id → pending future。``request()`` 登记、``'_reply'``
    信封完成、``destroy()`` 取消。内部 API，不属稳定契约。
    """

    def __init__(
        self,
        endpoint_id: str,
        comm: Communication,
        *,
        on_signal: Callable[[SignalEnvelope], Any] | None = None,
        on_event: Callable[[EventEnvelope], Any] | None = None,
    ) -> None:
        """构造句柄。

        .. rubric:: 行为规约

        - 前置条件：``endpoint_id`` 应已通过 ``register_endpoint`` 注册
          （``create_handle()`` 路径下由总线先行注册）；直接构造本类属
          高级用法，需自行保证端点注册与回调注入的一致性。
        - 边缘情况：``on_signal=None`` → ``_default_noop``；``on_event``
          同理。
        - 非行为：构造本身**不**注册端点（注册是 ``create_handle()`` 的
          职责），也不声明任何钩子点（那是 ``use_comm()`` 的职责）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.comm.CommHandle._default_noop``
          （``on_signal`` / ``on_event`` 为 None 时缺省注入，每次构造）
        - 被调：``flowing.plugins.comm.Communication.create_handle``
          （每次创建句柄）

        .. seealso:: :meth:`Communication.create_handle`
        """
        self.endpoint_id = endpoint_id
        self._comm = comm
        self._on_signal = on_signal or self._default_noop
        self._on_event = on_event or self._default_noop
        self._pending_replies = {}

    @staticmethod
    def _default_noop(*args: Any) -> None:
        """默认空回调——未注入接收回调时，收到的信号/事件被忽略。

        内部 API，不属稳定契约。同步无返回，保证「未配置接收方的端点」
        在总线派发路径上永远是安全的空操作。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：``flowing.plugins.comm.CommHandle.__init__``（缺省注入，
          每次构造）；总线派发路径（每次有信号/事件投递到未注入回调的
          端点）

        .. seealso:: :meth:`__init__`
        """
        pass  # 空操作即全部语义（docstring：收到的信号/事件被忽略）

    def send(self, target: str, type: str, payload: dict[str, Any]) -> None:
        """发送点对点信号（自动填 ``sender=self.endpoint_id``）。

        .. rubric:: 功能介绍

        ``Communication.send()`` 的身份携带包装；语义（立即返回、即发即忘、
        目标不存在抛 ``SignalDeliveryError``）与总线方法完全一致。

        .. rubric:: 使用示例

        .. code-block:: python

            self.comm_handler.send(
                target="ui-main",
                type="status_update",
                payload={"state": "thinking"},
            )

        :raises SignalDeliveryError: —— ``target`` 端点不存在时。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.comm.Communication.send``（每次发送）
        - 被调：无（框架内无调用方；``on_signal`` handler 等应用代码
          调用）

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

        ``Communication.request()`` 的身份携带包装；correlation_id 关联、
        超时、销毁取消等语义与总线方法完全一致。

        .. rubric:: 使用示例（工具审批）

        .. code-block:: python

            async def check_dangerous(self, tool_call):
                if not is_dangerous(tool_call, policy_prompt):
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

            agent.hooks.before_tool_call(check_dangerous, by="guardrail")

        .. rubric:: 行为规约

        - 边缘情况：等待期间本句柄被 ``destroy()`` → ``await`` 收到
          ``asyncio.CancelledError``；对端经 ``reply()`` 回传的 payload
          原样作为返回值（**保留类型，不回传信封本身，也不触发钩子**）。

        :raises SignalDeliveryError: —— ``target`` 端点不存在时。
        :raises SignalTimeoutError: —— 超过 ``timeout`` 秒未收到回复时。
        :raises asyncio.CancelledError: —— 等待期间句柄销毁时。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.comm.Communication.request``（每次
          请求，自动填 ``sender`` 与 ``reply_handler=self``）
        - 被调：无（框架内无调用方；工具审批等 handler 属应用代码）

        .. seealso:: :meth:`Communication.request`、:meth:`reply`
        """
        result = await self._comm.request(
            self.endpoint_id, target, type, payload, self, timeout=timeout
        )  # -> dict[str, Any]（返回值语义见 docstring）
        return result

    def reply(self, in_reply_to: SignalEnvelope, payload: dict[str, Any]) -> None:
        """回复一个 ``request`` 信号。

        .. rubric:: 功能介绍

        以 ``in_reply_to.reply_to or in_reply_to.sender`` 为目标、原样携带
        ``in_reply_to.correlation_id`` 投递保留类型 ``'_reply'`` 信封。
        回复**不 dispatch 到任何钩子**——它只完成请求方的 pending future。

        .. rubric:: 使用示例

        .. code-block:: python

            @self.hooks.on_signal["permission_request"]
            def _(self, envelope: SignalEnvelope):
                approved = envelope.payload["tool_name"] in self.allowed
                self.comm_handler.reply(envelope, {"approved": approved})
                return envelope

        .. rubric:: 行为规约

        - 前置条件：``in_reply_to`` 应是 ``request()`` 产生的信封
          （``correlation_id`` 非 ``None``）；对 ``send()`` 产生的信封
          调用本方法属编程错误（无 pending future 可匹配，回复被静默
          丢弃）。目标端点已注销时按总线侧规则：静默丢弃并发
          ``warnings.warn`` 警告。
        - 非行为：不校验 ``payload`` 结构（问答双方自行约定）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.comm.Communication.reply``（每次回复，
          目标取 ``in_reply_to.reply_to or in_reply_to.sender``）
        - 被调：无（框架内无调用方；``on_signal`` handler 中由应用调用）

        .. seealso:: :meth:`request`、:meth:`Communication.reply`
        """
        self._comm.reply(
            self.endpoint_id,
            in_reply_to.reply_to or in_reply_to.sender,
            in_reply_to.correlation_id,  # type: 规约保证 request 信封非 None
            payload,
        )

    def publish(self, topic: str, event: dict[str, Any]) -> None:
        """发布事件（自动注入 ``publisher=self.endpoint_id``）。

        .. rubric:: 功能介绍

        以 ``{'publisher': self.endpoint_id, **event}`` 合并后的字典调用
        ``Communication.publish()``——调用方传入的 ``event`` 中已有的
        ``publisher`` 键**会被句柄身份覆盖**（句柄路径不允许伪造发布者）。
        容错与 fire-and-forget 语义与总线方法一致。

        .. rubric:: 行为规约

        - 边缘情况：``event`` 含 ``"publisher"`` 键 → 被覆盖为
          ``self.endpoint_id``；无订阅者 → 空操作。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.comm.Communication.publish``（每次
          发布，调用前以 ``{'publisher': self.endpoint_id, **event}``
          合并）
        - 被调：无（框架内无调用方；应用代码调用）

        .. seealso:: :meth:`Communication.publish`、:meth:`subscribe`
        """
        merged = {"publisher": self.endpoint_id, **event}
        self._comm.publish(topic, merged)

    def subscribe(
        self, topic: str, callback: Callable[[EventEnvelope], Any] | None = None
    ) -> None:
        """订阅 topic（自动填 ``subscriber_id=self.endpoint_id``）。

        .. rubric:: 行为规约

        - 期待行为：``callback`` 缺省时使用构造时注入的 ``on_event``
          （Agent 端点经 ``use_comm()`` 即 dispatch 到
          ``agent.hooks.on_event``）。
        - 边缘情况：同一 topic 重复订阅 → 覆盖旧回调。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.comm.Communication.subscribe``（每次
          订阅，自动填 ``subscriber_id=self.endpoint_id``）
        - 被调：无（框架内无调用方；应用代码调用）

        .. seealso:: :meth:`Communication.subscribe`、:meth:`unsubscribe`
        """
        self._comm.subscribe(self.endpoint_id, topic, callback or self._on_event)

    def unsubscribe(self, topic: str) -> None:
        """取消对单个 topic 的订阅（幂等空操作安全）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.comm.Communication.unsubscribe``
          （每次取消，自动填 ``subscriber_id=self.endpoint_id``）
        - 被调：无（框架内无调用方；应用代码调用）

        .. seealso:: :meth:`Communication.unsubscribe`
        """
        self._comm.unsubscribe(self.endpoint_id, topic)

    def destroy(self) -> None:
        """清理该端点在总线上的全部痕迹（**幂等友好**）。

        .. rubric:: 功能介绍

        依次执行：``unsubscribe_all(endpoint_id)`` →
        ``unregister_endpoint(endpoint_id)`` → 取消 ``_pending_replies``
        中全部未完成 future（对应的 ``await`` 收到 ``CancelledError``）
        并清空该表。

        .. rubric:: 设计动机

        清理路径必须可重入：Agent 销毁链、插件收尾、应用层手动清理可能
        重复触发同一句柄的销毁。幂等性由句柄层自行保证（内部记录销毁
        状态，重复调用直接返回），不依赖 ``unregister_endpoint`` 的
        ``KeyError`` 容错。

        .. rubric:: 行为规约

        - 期待行为：首次调用完成上述全部清理；后续调用为空操作。
        - 边缘情况：清理后在途 ``request()`` 的 ``await`` 收到
          ``CancelledError``；已完成的 future 不受影响。
        - 非行为：不影响总线其他端点与订阅；不触发任何钩子（销毁事件
          的观察走 Agent 侧的 ``before_destroy`` 钩子，与本方法正交）。

        .. rubric:: 测试案例

        - 前置：句柄有一条订阅与一个 pending request；操作：连续两次
          ``destroy()``；期望：第一次后订阅消失、端点注销、request 的
          ``await`` 收到 ``CancelledError``；第二次无任何效果也不抛异常。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.plugins.comm.Communication.unsubscribe_all``
          → ``flowing.plugins.comm.Communication.unregister_endpoint``
          → 取消 ``_pending_replies`` 全部 future 并清表（每次销毁，
          时序见上文功能介绍）
        - 被调：``flowing.plugins.comm.use_comm`` 注册的
          ``before_destroy`` 清理 handler（时机：Agent ``destroy()``
          管线 dispatch ``before_destroy``，每次销毁；
          ``runtime.shutdown()`` 递归 destroy 时间接触发）；应用层手动
          清理属用户代码

        .. seealso:: :meth:`Communication.unregister_endpoint`、
           :meth:`Communication.unsubscribe_all`、:func:`use_comm`
        """
        if getattr(self, "_destroyed", False):  # 幂等：内部记录销毁状态，重复调用直接返回（字段名规约未具名）
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
    实例级通信能力。

    .. rubric:: 设计动机

    遵守插件约定 R1–R4：``install`` 只注册（此处只 provide 一个服务实例，
    无工具、无配置命名空间、无钩子声明）；不查询其他插件；依赖只经
    ``dependencies`` 声明。框架核心发布时不预装本插件——未启用的通信
    扩展「从没存在过」。

    .. rubric:: 使用示例

    .. code-block:: python

        # @/main.py
        import flowing
        from flowing.plugins.comm import CommPlugin
        from flowing.plugins.cron import CronPlugin

        async def main() -> flowing.Runtime:
            runtime = flowing.Runtime()
            runtime.use(CommPlugin(), CronPlugin())
            await runtime.mount("@/root.fya")
            return runtime

    .. rubric:: 行为规约

    - 期待行为：``install`` 完成后 ``runtime.inject(communication_key)``
      立即可得总线实例。
    - 非行为：不注册任何端点（端点随 ``use_comm()`` / ``create_handle()``
      按需创建）；不声明任何 Agent 钩子点。
    - 边缘情况：重复 ``runtime.use(CommPlugin())`` → 第二次 provide 同名
      key 时按「同名 provide key 冲突，后注册者报错」的全局规则报错。
    - 生命周期：``runtime.shutdown()`` 的插件收尾阶段调用总线
      ``_close()`` 清空端点表与订阅表（pending futures 的取消归各句柄
      ``destroy()``，总线不兜底——S-22 裁决）。

    .. rubric:: 测试案例

    - 前置：新 Runtime；操作：``runtime.use(CommPlugin())`` 后
      ``runtime.inject(communication_key)``；期望：得到 ``Communication``
      实例且端点表为空。
    - 前置：未安装本插件；操作：Agent ``setup()`` 中 ``use_comm(self)``；
      期望：``inject`` 抛 ``MissingProvideError``。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.runtime.Runtime.use``（每次 ``use()``，按实参
      顺序对每个插件调用一次 ``install``）
    - 实例化方：应用层 ``runtime.use(CommPlugin())``（用户代码；
      框架内无实例化方）

    .. seealso:: :class:`Communication`、:func:`use_comm`、
       :class:`flowing.runtime.Plugin`、:meth:`flowing.runtime.Runtime.use`
    """

    name: ClassVar[str] = "comm"
    """注册名（显式声明，无框架默认；推荐格式见 ``flowing.plugins`` 命名约定）。
    """

    dependencies: ClassVar[list[str]] = []   # S-10：与基类 Plugin 的 ClassVar 对齐
    """依赖声明（类属性元数据）。本插件无依赖，为空列表；框架在
    ``mount()`` 时做存在性与无环 DAG 校验。
    
    .. seealso:: :meth:`flowing.runtime.Runtime._check_dependencies`
    """

    def install(self, runtime: Runtime) -> None:
        """创建 ``Communication`` 总线并 ``provide(communication_key, bus)``。

        .. rubric:: 行为规约

        - 期待行为：仅做 provide 注册（R1），同步返回。
        - 非行为：不创建 Agent、不查询 ``runtime._plugins``、不读取其他
          插件状态（R1/R2）。
        - 后置条件：总线 ready，可被任何 Agent 经 inject 消费；总线的
          回收由插件收尾阶段负责。

        .. rubric:: 调用关系（审计）

        - 调用：构造 ``Communication``（每次 install）；
          ``flowing.runtime.Runtime.provide(communication_key, bus)``
          （每次 install）
        - 被调：``flowing.runtime.Runtime.use``（时机：每次
          ``use()``，按实参顺序对每个插件调用一次）

        .. seealso:: :data:`communication_key`、:class:`Communication`
        """
        bus = Communication()
        self._bus = bus   # 自留引用：shutdown() 收尾用（S-05）
        runtime.provide(communication_key, bus)

    async def shutdown(self) -> None:
        """插件收尾：关闭通信总线（S-05 裁决的显式收尾通道）。

        先于本方法，Agent 树已递归 destroy（各句柄已各自清理），
        此处经 ``Communication._close()`` 做兜底回收。

        .. rubric:: 调用关系（审计）

        - 调用：``Communication._close()``（每次优雅关闭）
        - 被调：``flowing.runtime.Runtime.shutdown()``（插件收尾阶段，
          按 install 顺序）
        """
        self._bus._close()


def use_comm(agent: Agent, *, name: str | None = None) -> None:
    """为 Agent 实例启用通信能力——阶段二入口（Composable）。

    句柄固定挂载为 ``agent.comm_handler``——命名遵循「插件绑定成员
    以注册名 underscore 版为前缀」的共同约定（见 ``flowing.plugins``
    模块 docstring），无改名参数（改名会令下游失去规范发现通道，
    或被迫维护重命名表——均被否决）。

    .. rubric:: 功能介绍

    在 ``setup()`` 中调用，完成四件事：

    1. ``agent.inject(communication_key)`` 获取全局总线（未安装
       ``CommPlugin`` → ``MissingProvideError``）。
    2. 以**端点命名规则**（``name`` 参数指定的语义名，缺省回退
       ``agent.node_id``）为 ID
       调用 ``comm.create_handle()`` 注册端点并创建句柄，接收回调经闭包
       dispatch 到 ``agent.hooks.on_signal`` / ``agent.hooks.on_event``；
       句柄挂载为 ``agent.comm_handler``（固定名，无前缀冲突——命名约定见
       ``flowing.plugins`` 模块 docstring）。
    3. ``agent.hooks.declare("on_signal", by="comm", match_on="type")``
       与 ``agent.hooks.declare("on_event", by="comm", match_on="topic")``
       声明两个实例级钩子点。
    4. 在 ``before_destroy`` 上注册清理 handler：``handle.destroy()``
       （by="comm"，闭包直接引用句柄，不反查属性名）。

    .. rubric:: 设计动机

    端点 ID 用 ``use_comm(name=...)`` 的显式语义名优先、``node_id``
    兜底——语义化名称保证信号可路由、可发现（如 ``payment-agent``），
    唯一性由总线注册表保证（重名 → ``DuplicateEndpointError`` 在
    ``setup()`` 期即暴露）。名字不再取自实例（A15 裁决：
    ``Agent.simplename`` 已删除，语义名由应用层/唤起方在启用时显式
    给出）。「声明幂等、注册开放、谁声明谁 dispatch」：
    钩子点由 ``use_comm`` 声明（by="comm"，同名 + 同 ``by`` 幂等返回
    已有 ``HookList``，见 hooks.pyi 总则——recover 管线重跑
    ``setup()`` 时重复声明不报错），handler 由应用层任意注册，
    dispatch 由句柄闭包完成。

    .. rubric:: 使用示例

    Python 子类形式：

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

    ``.fya`` 声明式形式（``$script`` 块，与上例等价）：

    .. code-block:: text

        ---
        name: companion-agent
        system_prompt: 你是陪伴助手。
        ---

        --- $script
        from flowing.plugins.comm import use_comm, SignalEnvelope
        from flowing.message import Message, MessageKind, TextBlock

        async def setup(self, data_dir: str):
            use_comm(self)

            @self.hooks.on_signal["agent_message"]
            async def _(self, envelope: SignalEnvelope):
                await self.enqueue_message(Message(
                    kind=MessageKind.PEER,
                    source=f"agent:{envelope.sender}",
                    content=[TextBlock(text=envelope.payload["body"])],
                ))
                return envelope

            @self.hooks.on_event["file_changed"]
            async def _(self, envelope: EventEnvelope):
                await self.enqueue_message(Message(
                    kind=MessageKind.EVENT,
                    source="file_watcher",
                    content=[TextBlock(text=f"文件已变更: {envelope.event['path']}")],
                ))
                return envelope
        ---

    .. rubric:: 行为规约

    - 期待行为：调用后 ``agent.comm_handler`` 可用；``on_signal`` handler 经
      ``@agent.hooks.on_signal["<fnmatch pattern>"]`` 按 ``envelope.type``
      过滤注册；``on_event`` 同理按 ``envelope.topic`` 过滤。handler
      签名统一 ``(self, envelope) -> envelope``；可 ``raise Intercepted``
      阻止后续 handler。
    - 非行为：通信信封**永不**自动进入对话通道——桥接（如
      ``enqueue_message``）由 handler 显式完成；``use_comm`` 不注册任何
      工具、不修改 prompt。
    - 边缘情况：重复调用 ``use_comm`` → 幂等（端点已注册且属本实例时
      复用已注册句柄，不重复注册）。此为**防御条款**：正常管线中该
      情形不发生（recover 重跑 ``setup()`` 时总线无旧注册——进程重启
      则总线新建，同进程 destroy 则 ``before_destroy`` 已注销），仅
      兜底同一活实例上的意外二次启用；端点 ID 被**其他** Agent 占用 →
      ``DuplicateEndpointError`` 在 ``setup()`` 期即暴露；
      显式 ``name`` 与其他端点冲突同样在 ``setup()`` 期报错。
    - 前置条件：``CommPlugin`` 已经 ``runtime.use()`` 安装（否则
      ``MissingProvideError``）；只能在 ``setup()``（或其后）调用。
    - 后置条件：Agent 可被总线寻址；``destroy()`` 时端点、订阅、pending
      futures 全部清理。
    - 零开销不变量：未调用的 Agent 无 ``comm`` 属性、无两个钩子点、不在
      端点表中。

    .. rubric:: 测试案例

    - 前置：已装 ``CommPlugin`` 的 Runtime 与 Agent A/B（均
      ``use_comm(self, name=...)``，
      端点名分别为 ``"a"`` / ``"b"``）；操作：``A.comm.send("b",
      "ping", {})``；期望：B 的 ``on_signal["ping"]`` handler 被调用，
      信封 ``sender=="a"``。
    - 前置：同上；操作：B 的 handler 注册为
      ``@hooks.on_signal["pe*"]``；发送 ``type="ping"`` 与
      ``type="other"``；期望：前者触发、后者不触发（fnmatch 过滤）。
    - 前置：Agent 未调 ``use_comm``；操作：``hasattr(agent, "comm")``；
      期望：``False``；向 ``agent.node_id`` 发送信号抛
      ``SignalDeliveryError``。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.agent.Agent.inject(communication_key)``、
      ``flowing.plugins.comm.Communication.create_handle``、
      ``flowing.hooks.HookRegistry.declare("on_signal" / "on_event",
      by="comm")``、``before_destroy`` 上注册清理 handler——均为每次
      启用，阶段二三步+清理注册，时序见上文功能介绍第 1–4 步
    - 被调：无（框架内无调用方；用户在 ``setup()`` 中调用）

    .. seealso:: :class:`CommPlugin`、:class:`CommHandle`、
       :meth:`flowing.hooks.HookRegistry.declare`、
       :meth:`flowing.agent.Agent.enqueue_message`
    """
    comm = agent.inject(communication_key)  # -> Communication（未装 CommPlugin → MissingProvideError）
    endpoint_id = name or agent.node_id
    handle = comm.create_handle(endpoint_id)  # 接收回调为闭包 dispatch 到 agent.hooks.on_signal / on_event（def 闭包略）
    agent.comm_handler = handle
    agent.hooks.declare("on_signal", by="comm", match_on="type")
    agent.hooks.declare("on_event", by="comm", match_on="topic")
    agent.hooks.before_destroy(agent.comm_handler.destroy, by="comm")  # 清理 handler 注册（docstring 第 4 步）
