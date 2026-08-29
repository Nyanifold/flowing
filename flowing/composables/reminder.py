"""``flowing.composables.reminder`` —— ``use_system_reminder``：每回合注入系统提醒。

.. rubric:: 功能介绍

应用层 Composable。把一个「提醒内容清单」在每个逻辑 Turn 开始前压缩为
**一条** ``Message(kind=EVENT)``（多个 Content 块合并），经
``before_turn`` 向 ``TurnContext.pending_messages`` 附加式注入（M-29
唯一推荐通道：随批次挂树持久化）。典型内容：当前时间、当前目录、
任务状态等**高频更新值**——它们不应进 prompt 块（破坏 provider 前缀
缓存，见 ``flowing.context`` 模块 docstring），消息通道（历史尾部
追加）是正确位置。

.. rubric:: 设计动机

思路锚定两条既定裁决：注入走 M-29 的 ``before_turn`` 附加式通道；
清理走 tags 直删（``agent.remove_by_tags``——Agent 层包装，自动处理
head 回退），不需要自记 ``last_turn_message_id`` 反向扫描。
安装模式与 :func:`flowing.composables.retry.use_retry` 同构（setup 中
调用、挂 handler、``remove_by_owner`` 整组卸载）。

.. rubric:: 参数语义

- ``clean=False``（默认）：注入的提醒消息**留在树上**（持久化历史
  的一部分，崩溃可恢复）。``clean=True``：``after_turn`` 时按
  ``tags`` 擦除本回合注入的提醒——代价：每 turn 至少 1 条注入 + 1 条
  tombstone（写盘量翻倍），且清理后**历史回放中该信息丢失**（崩溃
  恢复后那条 reminder 已被删除）。
- ``message_interval=0``：距上次注入后新增消息数 **≥ interval** 才再次
  注入（``0`` = 每回合都注入）；实现为 Composable 实例属性计数，
  不需要框架新机制。
- ``time_interval=0``（秒）：距上次注入的墙钟间隔低于该值则跳过
  （``0`` = 不限）。与 ``message_interval`` 是「任一不满足即跳过」
  的与关系。

.. rubric:: 使用示例

.. code-block:: python

    async def setup(self):
        use_system_reminder(
            self,
            contents=[lambda agent: f"当前目录：{agent.cwd}"],
            clean=True,             # 响应后清洗，不累积到后续轮次
            message_interval=4,     # 至少隔 4 条新消息再注入
        )

.. rubric:: 行为规约

- 安装（同步 ``def``，M-91 范式）：注册 ``before_turn`` handler
  （``by="system-reminder"``）——把当前可见提醒块压缩成一条
  ``Message(kind=EVENT, source="system-reminder", content=[...])``，
  ``tags=["system-reminder"]``，附加到 ``pending_messages`` 末尾
  （排在触发消息之后）；``clean=True`` 时再注册 ``after_turn``
  handler（同 ``by``）按 tags 擦除。卸载：
  ``remove_by_owner("system-reminder")`` 整组移除。
- 间隔判定的状态（上次注入的消息序/时间戳）存 Composable 闭包/
  实例属性——纯运行期，不落盘、不进 state 袋。
- 非行为：不注册模板全局函数、不改 prompt 块；注入内容回调
  （``contents`` 各项）为 ``(agent) -> str`` 或静态字符串，每次
  注入现场求值。
- 边缘情况：提醒清单为空或全部回调返回空串 → 本回合不注入；
  ``before_turn`` 被 ``Intercepted`` 阻断时批次整体丢弃，提醒随之
  不注入（同批次语义）。

.. rubric:: 调用关系（审计）

- 调用：``before_turn`` / ``after_turn`` handler 注册（时机：安装）；
  ``agent.remove_by_tags``（时机：``clean=True`` 时每回合收尾）
- 被调：无框架内调用方（应用层在 ``setup()`` 中调用）

.. seealso::

    - :class:`flowing.agent.TurnContext` —— ``pending_messages`` 附加式
      注入的载体（其 ``_reminder`` 示例即本 Composable 的雏形）。
    - :func:`flowing.composables.retry.use_retry` —— 同构的安装模式。
"""

from __future__ import annotations

import time

from typing import TYPE_CHECKING

from flowing.message import Message, MessageKind, TextBlock

if TYPE_CHECKING:
    from flowing.agent import Agent

__all__ = ["use_system_reminder"]


def use_system_reminder(
    agent: "Agent",
    contents: "list | None" = None,
    *,
    clean: bool = False,
    message_interval: int = 0,
    time_interval: float = 0,
) -> None:
    """把系统提醒清单装到 Agent 上（模块 docstring 为完整规约）。

    同步注册型 Composable（M-91）：注册 ``by="system-reminder"`` 的
    ``before_turn`` 注入 handler（``clean=True`` 时加 ``after_turn``
    清理 handler）；卸载经 ``remove_by_owner("system-reminder")``。

    间隔判定的消息计数口径：以消息级树的总结点数（``len(agent._messages)``）
    为水位——``before_turn`` 触发时 ``turn.message_ids`` 恒为空（本回合
    批次尚未挂树），骨架注释中的 ``turn.message_ids`` 提法不成立，按
    「距上次注入后新增消息数」的语义以树水位差实现。首次注入前
    （``last_count is None``）不做间隔判定——「距上次注入」在从未注入时
     vacuously 满足（先注入再谈「再次注入」）。
    """
    items = list(contents or [])
    # 间隔判定状态：闭包持有，纯运行期——不落盘、不进 state 袋
    state: dict = {"last_count": None, "last_fired": None}

    async def _inject(agent: "Agent", turn) -> "object":
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
            text = c(agent) if callable(c) else c
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

    async def _cleanup(agent: "Agent", turn) -> "object":
        # clean=True 时每回合收尾按 tags 擦除本回合注入的提醒（head 回退
        # 由 Agent 层 remove_by_tags 处理）
        agent.remove_by_tags({"system-reminder"})
        return turn

    agent.hooks.before_turn(_inject, by="system-reminder")
    if clean:
        agent.hooks.after_turn(_cleanup, by="system-reminder")
