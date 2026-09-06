"""``flowing.plugins.comm.models`` —— 通信扩展的数据对象。

本模块定义通信扩展的两种信封模型：:class:`SignalEnvelope` （点对点
信号通道的载体）与 :class:`EventEnvelope` （发布-订阅事件通道的载体）。
信封由总线在发送 / 发布路径上构造并自动填充元信息（见各字段
docstring），是 ``on_signal`` / ``on_event`` 钩子 handler 的 value
类型，也是端点接收回调的唯一参数。信封只存在于通信通道，不是
``Message``——不进消息级树、不落盘、不进 LLM 上下文。

.. seealso:: :mod:`flowing.plugins.comm` （扩展的启用方式与整体契约）、
    :mod:`flowing.plugins.comm.comm` （总线与句柄）
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> datetime:
    """naive UTC 当前时刻（与 ``flowing.message`` 的时间约定同口径）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class SignalEnvelope:
    """点对点信号信封——Signal 通道的唯一载体。

    .. rubric:: 功能介绍

    承载一次点对点通信的全部信息：发送方身份、信号类型、业务负载、
    request-reply 关联字段与创建时间。是 ``on_signal`` 钩子 handler
    的 value 类型，也是端点接收回调的唯一参数。

    .. rubric:: 使用示例

    .. code-block:: python

        @self.hooks.on_signal["permission_request"]
        def _(self, envelope: SignalEnvelope):
            approved = envelope.payload["tool_name"] in self.allowed_tools
            self.comm_handler.reply(envelope, {"approved": approved})
            return envelope
        # self.allowed_tools 为你的允许名单；本示例只展示信封字段的读取与回复

    .. rubric:: 行为要点

    - ``sender`` / ``correlation_id`` / ``reply_to`` / ``created_at``
      由总线在构造时自动填充：应用层只提供 ``type`` 与 ``payload``。
      自行构造信封（如测试中直接 ``SignalEnvelope(...)``）时元信息由
      构造方负责，总线不校验、不修正。
    - ``send()`` 产生的信封 ``correlation_id`` 与 ``reply_to`` 均为
      ``None``；``request()`` 产生的信封二者均非 ``None``；
      ``reply()`` 产生的信封 ``type`` 固定为 ``'_reply'``，且不会出现
      在任何 ``on_signal`` dispatch 中。
    - ``type`` 以 ``'_reply'`` 为唯一保留值，应用层不得使用。
    - 总线对 ``payload`` 内容完全透明（不校验、不修改、不序列化）。
    - ``created_at`` 是时区无关（naive）UTC ``datetime``，构造时填充，
      用于审计、排序与延迟测量；显示层负责转本地时区。

    .. seealso:: :class:`EventEnvelope`、:class:`CommHandle`、
        :meth:`Communication.request`、:func:`use_comm`
    """

    sender: str
    """发送方端点 ID。总线构造信封时填充；经 ``CommHandle`` 发送时
    自动等于 ``handle.endpoint_id``。直接操作总线时可传任意值（可冒充，
    属已知高级用法风险）。
    """
    type: str
    """信号类型——``on_signal`` 钩子的 ``match_on`` 字段（按 ``fnmatch``
    规则过滤注册）。``'_reply'`` 为框架保留值，标识 request 的回复
    信封；保留类型信封只用于匹配挂起的请求，不 dispatch 到钩子。
    """
    payload: dict[str, Any]
    """业务负载。总线对其内容完全透明（不校验、不修改、不序列化）。
    """
    correlation_id: str | None = None
    """request-reply 关联 ID（UUID 字符串）。仅 ``request()`` 路径填充；
    回复信封原样携带，接收侧据此匹配挂起的请求。
    """
    reply_to: str | None = None
    """回复目标端点 ID（通常等于 ``sender``）。仅 ``request()`` 路径
    填充；``CommHandle.reply()`` 据此路由回复。
    """
    created_at: datetime = field(default_factory=_utcnow)
    """创建时间，构造时填充，时区无关 UTC（naive ``datetime``）。
    用途：审计 / 日志、排序、延迟测量（接收时间减去创建时间）。
    """


@dataclass
class EventEnvelope:
    """发布-订阅事件信封——Event 通道的唯一载体。

    .. rubric:: 功能介绍

    承载一次 topic 广播的全部信息：事件主题、事件内容、发布方身份与
    创建时间。是 ``on_event`` 钩子 handler 的 value 类型，也是订阅
    回调的唯一参数。

    与 ``SignalEnvelope`` 的差别在于语义：Event 是一对多、无回复的
    广播，没有 ``correlation_id`` / ``reply_to``；多出的 ``publisher``
    字段由 ``CommHandle.publish()`` 自动注入，使订阅者能溯源发布方，
    不必信任负载内容。

    .. rubric:: 使用示例

    .. code-block:: python

        # 应用层注册 UI 端点（阶段一 runtime.install(CommPlugin()) 之后；
        # runtime 为 Runtime 实例）
        comm = runtime.inject(communication_key)
        ui_handle = comm.create_handle(endpoint_id="ui-main")

        def on_assistant_replied(env: EventEnvelope):
            render_message(env.event["text"])   # 由你实现的 UI 回调

        ui_handle.subscribe("assistant_replied", on_assistant_replied)

    .. rubric:: 行为要点

    - ``publisher`` 与 ``created_at`` 由总线 / 句柄在发布路径上自动
      填充：经 ``CommHandle.publish()`` 发布时 ``publisher`` 恒为该
      句柄的 ``endpoint_id`` （调用方在 ``event`` 里自填的 ``publisher``
      键被覆盖）；直接操作总线 ``publish()`` 时可为 ``None`` （高级
      用法，自行负责身份正确性）。
    - 同一 ``publish()`` 调用产生的信封按引用派发给全部订阅者：订阅者
      不应修改 ``event``，修改会影响后续订阅者（单进程按引用传递的
      直接推论）。
    - 无订阅者的 topic 上 ``publish()`` 是合法空操作。
    - 不保证任何时序承诺：按订阅表遍历顺序同步派发，返回 awaitable 的
      回调转为后台任务，发布者不等待。
    - ``created_at`` 是时区无关（naive）UTC ``datetime``。

    .. seealso:: :class:`SignalEnvelope`、:meth:`CommHandle.publish`、
        :meth:`Communication.subscribe`
    """

    topic: str
    """事件主题——``on_event`` 钩子的 ``match_on`` 字段（按 ``fnmatch``
    规则过滤注册）。
    """
    event: dict[str, Any]
    """事件内容。总线对其透明；``CommHandle.publish()`` 在调用总线前
    把 ``{**event, "publisher": self.endpoint_id}`` 合并进该字典（句柄
    身份恒覆盖调用方自填的 ``publisher`` 键）。
    """
    publisher: str | None = None
    """发布方端点 ID。经 ``CommHandle.publish()`` 发布时自动填充；
    直接操作总线时可为 ``None`` （高级用法，自行负责身份正确性）。
    """
    created_at: datetime = field(default_factory=_utcnow)
    """创建时间，构造时填充，时区无关 UTC（naive ``datetime``）。
    """
