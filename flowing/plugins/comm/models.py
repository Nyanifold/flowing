"""通信信封与数据对象：``SignalEnvelope`` / ``EventEnvelope``。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class SignalEnvelope:
    """点对点信号信封——Signal 通道的唯一载体。

    .. rubric:: 功能介绍

    承载一次点对点通信的全部信息：发送方身份、信号类型、负载、request-reply
    关联字段与创建时间。是 ``on_signal`` 钩子 handler 的 value 类型，也是
    端点接收回调的唯一参数。

    .. rubric:: 设计动机

    信封（而非裸 dict）使「总线自动填充的元信息」与「业务负载」结构性分离：
    路由与关联字段（sender/correlation_id）归框架管，业务内容（payload）归
    应用管。信封只存在于通信通道，**不是** ``Message``——不进入消息级树、
    不产生 ``parent_id`` 链、不落盘。

    .. rubric:: 使用示例

    .. code-block:: python

        @self.hooks.on_signal["permission_request"]
        def _(self, envelope: SignalEnvelope):
            if envelope.payload["tool_name"] in self.allowed_tools:
                self.comm_handler.reply(envelope, {"approved": True})
            else:
                self.comm_handler.reply(envelope, {"approved": False,
                                           "reason": "角色无权调用"})
            return envelope

    .. rubric:: 行为规约

    - 期待行为：``sender`` / ``correlation_id`` / ``reply_to`` /
      ``created_at`` 由总线在构造时填充；应用层构造信封属非法用法（字段
      语义无法保证）。
    - 非行为：不校验 ``payload`` 的内部结构（总线对负载透明）；不提供
      序列化方法（单进程内按引用传递）。
    - 边缘情况：``send()`` 产生的信封 ``correlation_id`` 与 ``reply_to``
      均为 ``None``；``request()`` 产生的信封二者均非 ``None``；
      ``reply()`` 产生的信封 ``type`` 固定为 ``'_reply'`` 且不会出现在任何
      ``on_signal`` dispatch 中。
    - 不变量：``created_at`` 为 naive UTC ``datetime``；``type`` 以
      ``'_reply'`` 为唯一保留值，应用层不得使用。

    .. rubric:: 调用关系（审计）

    - 被调：无（框架内无调用方；作为 ``on_signal`` handler 的 value
      类型，由 ``use_comm()`` 注入的接收闭包承接 dispatch）
    - 实例化方：``flowing.plugins.comm.Communication.send``（每次
      send）、``flowing.plugins.comm.Communication.request``（每次
      request）、``flowing.plugins.comm.Communication.reply``（每次
      reply）——三处均由总线在发送路径上构造并自动填充元信息

    .. seealso:: :class:`EventEnvelope`、:class:`CommHandle`、
       :meth:`Communication.request`、:func:`use_comm`
    """

    sender: str
    """发送方端点 ID。总线构造信封时填充；经 ``CommHandle`` 发送时自动等于
    ``handle.endpoint_id``。直接操作总线时可传任意值（可冒充，已知风险，
    属高级用法）。
    
    .. seealso:: :class:`CommHandle`
    """
    type: str
    """信号类型——``on_signal`` 钩子的 ``match_on`` 字段（fnmatch 过滤）。
    ``'_reply'`` 为框架保留值，标识 request 的回复信封；保留类型信封
    只用于匹配 pending future，不 dispatch 到钩子。
    """
    payload: dict[str, Any]
    """业务负载。总线对其内容完全透明（不校验、不修改、不序列化）。
    """
    correlation_id: str | None = ...
    """request-reply 关联 ID（UUID 字符串）。仅 ``request()`` 路径填充；
    回复信封原样携带，接收侧据此匹配 pending future。
    """
    reply_to: str | None = ...
    """回复目标端点 ID（通常等于 ``sender``）。仅 ``request()`` 路径填充；
    ``CommHandle.reply()`` 据此路由回复。
    """
    created_at: datetime = ...
    """创建时间，构造时由总线填充，时区无关 UTC（naive ``datetime``）。
    用途：审计/日志、排序、延迟测量（接收时间 − ``created_at``）。
    """


@dataclass
class EventEnvelope:
    """发布-订阅事件信封——Event 通道的唯一载体。

    .. rubric:: 功能介绍

    承载一次 topic 广播的全部信息：主题、事件内容、发布方身份与创建时间。
    是 ``on_event`` 钩子 handler 的 value 类型，也是订阅回调的唯一参数。

    .. rubric:: 设计动机

    与 ``SignalEnvelope`` 分离的原因：Event 是**一对多、无回复**语义——没有
    ``correlation_id`` / ``reply_to``；多出的 ``publisher`` 字段由
    ``CommHandle.publish()`` 自动注入，使订阅者能溯源而不必信任负载内容。

    .. rubric:: 使用示例

    .. code-block:: python

        ui_handle = comm.create_handle(endpoint_id="ui-live2d")

        def on_assistant_replied(env: EventEnvelope):
            set_expression(env.event["emotion"])
            play_talk_animation()

        ui_handle.subscribe("assistant_replied", on_assistant_replied)

    .. rubric:: 行为规约

    - 期待行为：``publisher`` 与 ``created_at`` 由总线/句柄在发布路径上
      自动填充；同一 ``publish()`` 调用产生的信封按引用派发给全部订阅者
      （订阅者不应修改 ``event``，修改会影响后续订阅者——单进程按引用
      传递的直接推论）。
    - 非行为：不支持回复语义；不保证跨订阅者的接收顺序之外的任何时序
      承诺（按订阅表遍历顺序同步派发，awaitable 回调转为后台 Task）。
    - 边缘情况：无订阅者的 topic 上 ``publish()`` 是合法空操作；直接操作
      总线 ``publish()`` 时 ``publisher`` 可为 ``None`` 或由调用方自填。
    - 不变量：``created_at`` 为 naive UTC ``datetime``。

    .. rubric:: 调用关系（审计）

    - 被调：无（框架内无调用方；作为 ``on_event`` handler 的 value
      类型，由 ``use_comm()`` 注入的接收闭包承接 dispatch）
    - 实例化方：``flowing.plugins.comm.Communication.publish``
      （每次 publish，由总线构造并填充 ``created_at``）

    .. seealso:: :class:`SignalEnvelope`、:meth:`CommHandle.publish`、
       :meth:`Communication.subscribe`
    """

    topic: str
    """事件主题——``on_event`` 钩子的 ``match_on`` 字段（fnmatch 过滤）。
    """
    event: dict[str, Any]
    """事件内容。总线对其透明；``CommHandle.publish()`` 在调用总线前把
    ``{'publisher': self.endpoint_id, **event}`` 合并进该字典的语义
    由句柄层保证（见 :meth:`CommHandle.publish`）。
    """
    publisher: str | None = ...
    """发布方端点 ID。经 ``CommHandle.publish()`` 发布时自动填充；
    直接操作总线时可为 ``None``（高级用法，自行负责身份正确性）。
    """
    created_at: datetime = ...
    """创建时间，构造时由总线填充，时区无关 UTC（naive ``datetime``）。
    """
