"""flowing.composables.reminder —— 内置系统提醒 Composable（可选、非默认）。

.. rubric:: 功能介绍

本模块提供 ``use_system_reminder()``：为单个 Agent 实例启用「每个逻辑
Turn 开始前注入系统提醒」策略。启用后，每个逻辑 Turn 开始时，默认策略
把当前可见的提醒内容清单压缩为一条 ``Message(kind=EVENT, ...)`` （多个
内容块合并成一条消息），经 ``before_turn`` 钩子附加进本回合的待挂树
批次——提醒随批次挂树并持久化，排在触发消息之后。

典型内容：当前时间、当前目录、任务状态等高频率更新的值。它们不应进
prompt 块（破坏 Provider 前缀缓存，见 ``flowing.context`` 模块
docstring）；消息通道（历史尾部追加）是正确位置。

本模块属应用层 / 内置 Composable：随 ``flowing`` 包发布但不自动启用，
必须由 Agent 开发者在 ``setup()`` 中显式调用。不调用的 Agent 不持有
任何提醒相关 handler 与状态。

.. rubric:: 注册面清单

- 启用方式：仅阶段二——``setup()`` 中调用 ``use_system_reminder(self)``
  （恢复时 ``setup()`` 在新实例上执行，天然不叠加）。未启用时零开销：
  ``before_turn`` / ``after_turn`` 链上无任何 ``by="system-reminder"``
  handler。
- 注册的资源：无 provide key、无工具注册、无 Agent 状态键、不改
  prompt 块。
- 声明的钩子点：无（本模块只往核心已有的钩子点挂 handler，纯挂载）。
- 挂载的钩子：``before_turn`` 注入 handler（``by="system-reminder"``，
  见 :func:`use_system_reminder`）+ ``clean=True`` 时的 ``after_turn``
  清理 handler（``by="system-reminder"``）。两者共用 ``by``，
  ``remove_by_owner("system-reminder")`` 可整组移除。
- 作用域：只影响调用它的那一个 Agent 实例；注入的提醒随批次落盘
  （``clean=False`` 时持久化、崩溃可恢复），间隔判定状态不落盘。

.. rubric:: 使用示例

.. code-block:: python

    async def setup(self) -> None:
        use_system_reminder(
            self,
            contents=[lambda agent: f"当前节点：{agent.node_id}"],
            message_interval=4,   # 至少隔 4 条新消息再注入
        )

.. rubric:: 行为要点

- 注入形态：每个满足注入条件的逻辑 Turn 压缩为一条 ``Message``——
  ``kind=EVENT``、``source="system-reminder"``、
  ``tags=["system-reminder"]``，内容为多个 ``TextBlock`` 合并——附加到
  ``pending_messages`` 末尾（排在触发消息之后），随批次挂树并持久化。
- 内容求值：``contents`` 各项为三态之一——``(agent) -> str | None``
  回调（返回 ``None`` / 空串时跳过该条目）、
  :class:`flowing.parsable.Parsable`（注入时以当前 Agent 为上下文
  ``resolve()``）、普通字符串（初始化扫描时原地归一为 Parsable——
  ``{{ }}`` 模板与 ``$`` 引用随每次注入现场求值，无标记的字符串是
  字面量常量）。空结果条目剔除；清单为空（含缺省 ``None``）或全部
  条目为空结果时本回合不注入。
- ``contents`` 列表被**持有引用**（不拷贝）：典型形态
  ``use_system_reminder(self, self.system_reminders)``——此后对列表的
  增删直接反映到注入。初始化扫描只归一化已有条目（幂等：已是
  Parsable 的不重复包装）；扫描后 append 的裸字符串在注入期惰性兼容
  （按 Parsable 求值）。第四类条目（非 callable / Parsable / str）
  在初始化扫描时 ``TypeError`` fail fast。
- 间隔判定：``message_interval`` 与 ``time_interval`` 是与关系——任一
  不满足即跳过（默认两者均为 0：每回合都注入）；首次注入不受间隔限制。
- 清理语义：``clean=True`` 时每回合收尾按 ``tags`` 擦除本回合注入的
  提醒（head 回退由 Agent 层处理）——代价是每回合收尾都擦除一次（写
  盘量约翻倍），且清理后历史回放中该信息丢失（崩溃恢复后那条提醒
  已被删除）。``clean=False`` （默认）时提醒留在树上。
- 与批次同生共死：``before_turn`` 被 ``Intercepted`` 阻断时整个批次
  丢弃、不落盘，提醒随之不注入（同批次语义）。

.. seealso::

    :class:`flowing.agent.TurnContext` —— ``pending_messages`` 附加式
    注入的载体。
    :func:`flowing.composables.retry.use_retry` —— 同构的安装模式。
"""

from __future__ import annotations

import time

from typing import TYPE_CHECKING

from flowing.message import Message, MessageKind, TextBlock
from flowing.parsable import Parsable

if TYPE_CHECKING:
    from flowing.agent import Agent, TurnContext

__all__ = ["use_system_reminder"]


def use_system_reminder(
    agent: "Agent",
    contents: "list | None" = None,
    *,
    clean: bool = False,
    message_interval: int = 0,
    time_interval: float = 0,
) -> None:
    """为单个 Agent 实例启用「每回合注入系统提醒」策略（可选、非默认）。

    .. rubric:: 功能介绍

    注册 ``by="system-reminder"`` 的 ``before_turn`` 注入 handler：每个
    逻辑 Turn 开始时，把当前可见的提醒内容压缩为一条
    ``Message(kind=EVENT, source="system-reminder", ...)`` 附加进
    ``pending_messages`` 末尾（排在触发消息之后），随批次挂树并持久化。
    ``clean=True`` 时再注册 ``after_turn`` 清理 handler（同 ``by``），
    每回合收尾按 ``tags`` 擦除本回合注入的提醒。

    ``contents`` 的条目是三态（回调 / Parsable / 普通字符串）且列表被
    持有引用——提醒内容因此可以作为 Agent 实例属性上的纯数据维护
    （如 ``self.system_reminders = [...]`` 后透传），模板随注入现场
    求值。

    本函数是双层启用的阶段二入口，只能在 ``setup()`` （或实例存活期内
    的任意代码）中对已完成初始化的实例调用。

    .. rubric:: 使用示例

    .. code-block:: python

        async def setup(self) -> None:
            use_system_reminder(
                self,
                contents=[
                    lambda agent: f"当前节点：{agent.node_id}",
                    "请优先核对金额。",
                ],
                clean=True,           # 回合收尾后擦除，不累积到后续轮次
                message_interval=4,   # 至少隔 4 条新消息再注入
            )

        # 属性形态：提醒内容是实例属性上的纯数据（模板随注入现场求值）
        async def setup(self) -> None:
            self.system_reminders = ["当前模式：{{ current_mode }}"]
            use_system_reminder(self, self.system_reminders)

        # .fya 形态：YAML 头部直接声明（未知字段落 ``_extra``，装配层在
        # 用户 setup 前合入实例；``Agent.__getattr__`` 回退使
        # ``self.system_reminders`` 直接可读）——setup 里一行透传
        #   system_reminders:
        #     - "当前模式：{{ current_mode }}"
        async def setup(self) -> None:
            use_system_reminder(self, self.system_reminders)

    .. rubric:: 行为要点

    - ``contents``：提醒内容清单，各项为三态之一——``(agent) ->
      str | None`` 回调（``None`` / 空串跳过该条目）、
      :class:`~flowing.parsable.Parsable`（注入时 ``resolve(agent)``）、
      普通字符串（调用时原地归一为 Parsable）。空结果条目剔除；清单
      为空（含缺省 ``None``）或全部条目为空结果时本回合不注入。列表
      被持有引用（不拷贝），此后的增删直接反映到注入；第四类条目
      （非 callable / Parsable / str）调用时 ``TypeError`` fail fast。
    - 注入条件：距上次注入后新增消息数 ``>= message_interval`` 且距上次
      注入的墙钟间隔 ``>= time_interval`` （秒）——两者是与关系，任一不
      满足即跳过；两者均为 0（默认）时每回合都注入；首次注入不受间隔
      限制。间隔判定状态是闭包内部状态，纯运行期，不落盘、不进状态袋。
    - 消息计数口径：以消息级树的总结点数（``len(agent._messages)``）为
      水位——``before_turn`` 触发时 ``turn.message_ids`` 恒为空（本回合
      批次尚未挂树），无法用回合消息列表计数。
    - ``clean=True`` 的代价与边界：每回合收尾擦除（写盘量约翻倍）；
      清理后历史回放中该信息丢失（崩溃恢复后那条提醒已被删除）。
    - 与批次同生共死：``before_turn`` 被 ``Intercepted`` 阻断时整个批次
      丢弃、不落盘，提醒随之不注入。
    - 重复调用：不做幂等去重——每次调用各叠加一组独立的 handler（各自
      持有独立的间隔状态闭包），允许以不同参数多次启用。恢复时 ``setup()`` 在新实例上执行，钩子注册表随实例重建，天然不叠加。
    - 不做什么：不注册模板全局函数、不改 prompt 块、不修改
      ``agent.model``、不持久化任何状态。

    :param agent: 目标 Agent 实例；标准用法是在 ``setup()`` 中传 ``self``。
    :param contents: 提醒内容清单（三态条目：``(agent) -> str | None``
        回调 / :class:`~flowing.parsable.Parsable` / 普通字符串）；缺省
        ``None`` 视为空清单。列表被持有引用——传入
        ``agent.system_reminders`` 这类实例属性后，运行期增删即生效。
    :param clean: 为 ``True`` 时每回合收尾按 ``tags`` 擦除本回合注入的
        提醒；为 ``False`` （默认）时提醒留在树上（持久化、崩溃可恢复）。
    :param message_interval: 距上次注入后新增消息数达到该值才再次注入，
        ``>= 0``，默认 ``0`` （每回合都注入）。
    :param time_interval: 距上次注入的墙钟间隔（秒）达到该值才再次注入，
        ``>= 0``，默认 ``0`` （不限）。

    .. seealso::

        :class:`flowing.agent.TurnContext` —— ``pending_messages`` 附加式
        注入的载体。
    """
    # contents 持有引用（不拷贝）：此后对列表的增删直接反映到注入；
    # 非列表容器（tuple 等）无法持有可变引用，拷贝归一
    items: list = contents if isinstance(contents, list) else list(contents or [])
    # 初始化扫描：非 callable 且非 Parsable 的条目原地归一为 Parsable
    # （幂等——已是 Parsable 的不重复包装；第四类 fail fast）
    for i, item in enumerate(items):
        if callable(item) or isinstance(item, Parsable):
            continue
        if not isinstance(item, str):
            raise TypeError(
                "reminder content items must be callable / Parsable / str, "
                f"got {type(item).__name__}")
        items[i] = Parsable(item)
    # 间隔判定状态：闭包持有，纯运行期——不落盘、不进 state 袋
    state: dict = {"last_count": None, "last_fired": None}

    async def _inject(agent: "Agent", turn: "TurnContext") -> "TurnContext":
        """注入 handler（内部 API，经 ``by="system-reminder"`` 定位与移除）。

        间隔判定（``message_interval`` / ``time_interval`` 与关系，任一
        不满足即跳过；从未注入过时直接放行）→ ``contents`` 现场求值
        （空串条目剔除，全空不注入）→ 压缩为一条 ``Message``
        （``kind=EVENT``、``source="system-reminder"``、
        ``tags=["system-reminder"]``）附加到 ``pending_messages`` 末尾
        （排在触发消息之后）。消息计数以消息树总结点数
        （``len(agent._messages)``）为水位——``before_turn`` 触发时
        ``turn.message_ids`` 恒为空（本回合批次尚未挂树）。必须
        ``return turn``。
        """
        # 间隔判定：message_interval 与 time_interval 与关系（任一不满足
        # 即跳过）；从未注入过时直接放行
        if state["last_count"] is not None:
            if len(agent._messages) - state["last_count"] < message_interval:
                return turn   # 新增消息数未达间隔
            if time_interval > 0 and time.time() - state["last_fired"] < time_interval:
                return turn   # 墙钟间隔未达
        # contents 现场求值：callable 或静态串；空串条目剔除，全空不注入
        texts = []
        for c in items:
            if callable(c):
                text = c(agent)   # 返回 None / 空串 → 跳过该条目
            elif isinstance(c, Parsable):
                text = c.resolve(agent)   # 注入时以当前 Agent 为上下文求值
            elif isinstance(c, str):
                # 初始化扫描后 append 的裸字符串：惰性兼容（同归一化语义）
                text = Parsable(c).resolve(agent)
            else:
                raise TypeError(
                    "reminder content items must be callable / Parsable / str, "
                    f"got {type(c).__name__}")
            if text:
                texts.append(text)
        if not texts:
            return turn
        # 压缩为一条 Message 附加到 pending_messages 末尾（排在触发消息
        # 之后），随批次挂树持久化
        turn.pending_messages.append(Message(
            kind=MessageKind.EVENT, source="system-reminder",
            content=[TextBlock(text=t) for t in texts],
            tags=["system-reminder"]))
        state["last_count"] = len(agent._messages)
        state["last_fired"] = time.time()
        return turn

    async def _cleanup(agent: "Agent", turn: "TurnContext") -> "TurnContext":
        """清理 handler（内部 API，经 ``by="system-reminder"`` 定位与移除）。

        ``clean=True`` 时每回合收尾按 ``tags`` 擦除本回合注入的提醒
        （head 回退由 Agent 层 :meth:`flowing.agent.Agent.remove_by_tags`
        处理）。必须 ``return turn``。
        """
        agent.remove_by_tags({"system-reminder"})
        return turn

    agent.hooks.before_turn(_inject, by="system-reminder")
    if clean:
        agent.hooks.after_turn(_cleanup, by="system-reminder")
