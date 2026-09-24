"""flowing.composables.prompt_until —— 回合收尾断言续跑 Composable（可选、非默认）。

.. rubric:: 功能介绍

本模块提供 ``use_prompt_until()``：为单个 Agent 实例启用“prompt until
续跑循环”策略。启用后，每个逻辑 Turn 收尾（``after_turn``）时运行一次
断言回调：断言成立（返回真值）则通过、什么都不做；断言不成立则把导向
内容求值为一条 ``Message(kind=EVENT, priority=STEER, ...)`` 经
:meth:`flowing.agent.Agent.steer` 入队——该消息由下一个逻辑回合消费，
从而把“任务未完成就继续”表达为普通的消息流转，不打断任何回合。

典型用途：要求模型持续工作直到输出满足验收条件的场景（如“继续直到
测试全绿”“产出包含结论标记才停”）。断言与导向内容都是策略，本模块
只提供“回合收尾检查 + steer 续跑”的机制粘合。

本模块属应用层 / 内置 Composable：随 ``flowing`` 包发布但不自动启用，
必须由 Agent 开发者在 ``setup()`` 中显式调用。不调用的 Agent 不持有
任何断言相关 handler 与状态。

.. rubric:: 注册面清单

- 启用方式：仅阶段二——``setup()`` 中调用 ``use_prompt_until(self, ...)``
  （恢复时 ``setup()`` 在新实例上执行，天然不叠加）。未启用时零开销：
  ``after_turn`` 链上无任何 ``by="prompt-until"`` handler。
- 注册的资源：无 provide key、无工具注册、无 Agent 状态键、不改
  prompt 块。
- 声明的钩子点：无（本模块只往核心已有的钩子点挂 handler，纯挂载）。
- 挂载的钩子：``after_turn`` 检查 handler（``by="prompt-until"``，见
  :func:`use_prompt_until`）。``remove_by_owner("prompt-until")`` 可整组
  移除。
- 作用域：只影响调用它的那一个 Agent 实例；导向消息随下一回合批次挂树
  并持久化，本模块自身不持久化任何状态。

.. rubric:: 使用示例

.. code-block:: python

    async def setup(self) -> None:
        # 每个回合收尾检查：本回合最终回复含 "DONE" 才放行，否则导向续跑
        def _not_done(agent, turn) -> bool:
            for mid in reversed(turn.message_ids):
                msg = agent.chain.get(mid)
                if msg.kind is MessageKind.PROVIDER:
                    text = "".join(b.text for b in msg.content
                                   if isinstance(b, TextBlock))
                    return "DONE" in text
            return True   # 无 PROVIDER 消息（空回合）→ 不催

        use_prompt_until(
            self,
            predicate=_not_done,
            message="验收条件未满足（最终回复需包含 DONE），请继续。",
        )

    # 导向内容为模板：随每次导向现场求值
    async def setup(self) -> None:
        self.continue_hint = "第 {{ retry_no }} 次续跑：请继续未完成的任务。"
        use_prompt_until(self, self._not_done_yet, self.continue_hint)

.. rubric:: 行为要点

- 检查时点：``after_turn``（所有路径收尾的唯一观察点）——每个逻辑
  Turn 收尾时检查一次，与回合结局无关地触发（见下条的两个例外）。
- 断言回调：``(agent, turn) -> bool``——``turn`` 是
  :class:`flowing.agent.TurnContext` （``after_turn`` 钩子点的 value
  类型；``TurnResult`` 在 ``after_turn`` 之后才组装，回合收尾时点可读
  的是回合执行期载体：``aborted`` / ``message_ids`` / ``usages`` /
  ``finish_output``）。返回真值即通过；假值触发导向。结果只做真值
  判断，不要求严格 ``bool``。
- 回合结局分流：``turn.aborted`` 为真（取消 / 打断 / destroy 级联）的
  回合**不检查、不入队**——取消语义优先于续跑循环，断言回调也收不到
  该回合；blocked（``Intercepted`` 阻断）与 error 回合照常检查，是否
  续跑由断言回调读 ``turn`` 自行决策。
- 导向内容三态（同 :func:`flowing.composables.reminder.use_system_reminder`
  的条目形态）：``(agent) -> str | None`` 回调（返回 ``None`` / 空串时
  放弃本次导向）、:class:`flowing.parsable.Parsable` （求值时以当前
  Agent 为上下文 ``resolve()``）、普通字符串（注册时原地归一为
  Parsable——``{{ }}`` 模板随每次导向现场求值）。第四类条目（非
  callable / Parsable / str）调用时 ``TypeError`` fail fast。求值结果
  为 ``None`` / 空串时不入队（无物可导向），续跑循环自然终止。
- 导向消息形态：``Message(kind=EVENT, priority=STEER,
  source="prompt-until", tags=["prompt-until"])``，经
  :meth:`flowing.agent.Agent.steer` 入队（走 ``on_enqueue``
  时序，``Intercepted`` 原样上抛）。STEER 不打断
  任何回合：由下一个逻辑回合消费（队首 INTERRUPT/STEER 连续段并入其
  批次，见 :meth:`flowing.agent.Agent._dequeue`），随批次挂树并持久化。
- 终止责任在调用方：断言恒假且导向内容恒非空 → 回合无限续跑（每次
  导向都触发新回合）。保证终止的三个杠杆——断言最终成立、导向回调
  返回 ``None``（放弃）、``remove_by_owner("prompt-until")`` 整组拆除。
- 异常语义：断言回调或内容求值的普通异常按 ``after_turn`` handler
  异常语义处理——回合产物照常交付（``TurnResult.turn`` 为合成空载体），
  异常上抛落工作循环日志（“turn crashed”），本模块不再导向、循环
  停摆；错误以异常形态可见，不被吞掉。
- 重复调用：不做幂等去重——每次调用各叠加一个独立 handler（各自持有
  独立闭包），允许以不同断言多次启用。恢复时 ``setup()`` 在新实例上
  执行，钩子注册表随实例重建，天然不叠加。
- 不做什么：不注册模板全局函数、不改 prompt 块、不修改
  ``agent.model``、不持久化任何状态、不声明新钩子点。

.. seealso::

    :func:`flowing.composables.reminder.use_system_reminder` —— 同构的
    三态内容形态（``before_turn`` 注入对照 ``after_turn`` 导向）。
    :meth:`flowing.agent.Agent.steer` —— 导向消息的入队通道。
    :class:`flowing.agent.TurnContext` —— 断言回调收到的回合载体。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, TypeAlias, Union

from flowing.message import MessageKind
from flowing.parsable import Parsable

if TYPE_CHECKING:
    from flowing.agent import Agent, TurnContext

__all__ = ["use_prompt_until"]

PromptPredicate: TypeAlias = Callable[["Agent", "TurnContext"], bool]
"""断言回调类型：``(agent, turn) -> bool``，``turn`` 为
:class:`flowing.agent.TurnContext` （``after_turn`` 钩子点的 value）。
返回真值即通过；假值触发导向。
"""

PromptMessage: TypeAlias = Union[
    Callable[["Agent"], "str | None"], Parsable, str
]
"""导向内容三态：``(agent) -> str | None`` 回调 /
:class:`~flowing.parsable.Parsable` / 普通字符串（同
:func:`flowing.composables.reminder.use_system_reminder` 的条目形态）。
"""


def use_prompt_until(
    agent: "Agent",
    predicate: PromptPredicate,
    message: PromptMessage,
) -> None:
    """为单个 Agent 实例启用“prompt until 续跑循环”策略（可选、非默认）。

    .. rubric:: 功能介绍

    注册 ``by="prompt-until"`` 的 ``after_turn`` 检查 handler：每个逻辑
    Turn 收尾时运行 ``predicate(agent, turn)``——成立（真值）则通过；
    不成立则把 ``message`` 求值为一条
    ``Message(kind=EVENT, priority=STEER, source="prompt-until", ...)``，
    经 :meth:`flowing.agent.Agent.steer` 入队，由下一个逻辑回合消费。
    “任务未完成就继续”由此表达为普通的消息流转，不打断任何回合。

    ``message`` 与 ``predicate`` 是策略的两大可替换件：断言决定“何时
    算完成”，导向内容决定“怎么催”。本函数是双层启用的阶段二入口，
    只能在 ``setup()`` （或实例存活期内的任意代码）中对已完成初始化的
    实例调用。

    .. rubric:: 使用示例

    .. code-block:: python

        async def setup(self) -> None:
            def _not_done(agent, turn) -> bool:
                # 本回合最后一条 PROVIDER 消息含 "DONE" 即通过
                for mid in reversed(turn.message_ids):
                    msg = agent.chain.get(mid)
                    if msg.kind is MessageKind.PROVIDER:
                        return "DONE" in "".join(
                            b.text for b in msg.content if isinstance(b, TextBlock))
                return True   # 无 PROVIDER 消息（空回合）→ 不催

            use_prompt_until(
                self,
                predicate=_not_done,
                message="验收条件未满足（最终回复需包含 DONE），请继续。",
            )

        # 导向内容三态：回调 / Parsable / 普通字符串（模板现场求值）
        async def setup(self) -> None:
            use_prompt_until(
                self,
                self._tests_not_green,
                lambda agent: f"还有 {agent.state.remaining} 项未完成，继续。",
            )

    .. rubric:: 行为要点

    - 检查时点与回合结局分流：``after_turn`` 每个逻辑 Turn 收尾检查
      一次；``turn.aborted`` 为真（取消 / 打断 / destroy 级联）的回合
      不检查、不入队（取消语义优先，断言回调收不到该回合）；blocked
      （``Intercepted`` 阻断）与 error 回合照常检查——是否续跑由断言
      回调读 ``turn`` 自行决策。
    - 断言回调：``(agent, turn) -> bool``，``turn`` 是
      :class:`flowing.agent.TurnContext` （``after_turn`` 钩子点的
      value 类型；``TurnResult`` 在 ``after_turn`` 之后才组装，此处
      可读 ``aborted`` / ``message_ids`` / ``usages`` / ``finish_output``）。
      返回值只做真值判断。
    - 导向内容三态：``(agent) -> str | None`` 回调（``None`` / 空串
      放弃本次导向）、:class:`~flowing.parsable.Parsable`
      （``resolve(agent)`` 现场求值）、普通字符串（调用时原地归一为
      Parsable，``{{ }}`` 模板随每次导向现场求值）；第四类条目调用时
      ``TypeError`` fail fast。求值为空时不入队，续跑循环自然终止。
    - 导向消息与消费路径：``kind=EVENT`` / ``priority=STEER`` /
      ``source="prompt-until"`` / ``tags=["prompt-until"]``，经
      ``steer()`` 入队（``on_enqueue`` 时序，
      ``Intercepted`` 原样上抛）；由下一个逻辑回合消费（队首
      INTERRUPT/STEER 连续段并入其批次），随批次挂树并持久化。
    - 终止责任在调用方：断言恒假且导向内容恒非空 → 回合无限续跑。
      保证终止的杠杆：断言最终成立、导向回调返回 ``None``、
      ``remove_by_owner("prompt-until")`` 整组拆除。
    - 异常语义：断言回调或内容求值的普通异常按 ``after_turn`` handler
      异常语义处理——回合产物照常交付（``TurnResult.turn`` 为合成空
      载体），异常上抛落工作循环日志，本模块不再导向、循环停摆。
    - 重复调用：不做幂等去重——每次调用各叠加一个独立 handler（各自
      持有独立闭包），允许以不同断言多次启用。恢复时 ``setup()`` 在
      新实例上执行，钩子注册表随实例重建，天然不叠加。
    - 不做什么：不注册模板全局函数、不改 prompt 块、不修改
      ``agent.model``、不持久化任何状态、不声明新钩子点。

    :param agent: 目标 Agent 实例；标准用法是在 ``setup()`` 中传 ``self``。
    :param predicate: 断言回调 ``(agent, turn) -> bool``——``turn`` 为
        :class:`~flowing.agent.TurnContext`；返回真值即通过（本回合
        放行），假值触发导向。
    :param message: 导向内容（三态：``(agent) -> str | None`` 回调 /
        :class:`~flowing.parsable.Parsable` / 普通字符串）；求值为
        ``None`` / 空串时不入队（续跑循环自然终止）。

    .. seealso::

        :func:`flowing.composables.reminder.use_system_reminder` ——
        三态内容形态的同构参照（``before_turn`` 注入对照 ``after_turn``
        导向）。
        :meth:`flowing.agent.Agent.steer` —— 导向消息的入队通道。
    """
    # message 三态归一（同 use_system_reminder 的初始化扫描语义）：
    # 裸 str 原地归一为 Parsable（模板随每次导向现场求值）；第四类 fail fast
    if callable(message) or isinstance(message, Parsable):
        content: "Callable[[Agent], str | None] | Parsable" = message
    elif isinstance(message, str):
        content = Parsable(message)
    else:
        raise TypeError(
            "prompt_until message must be callable / Parsable / str, "
            f"got {type(message).__name__}")

    async def _prompt_until_after_turn(
        agent: "Agent", turn: "TurnContext"
    ) -> "TurnContext":
        """检查 handler（内部 API，经 ``by="prompt-until"`` 定位与移除）。

        ``turn.aborted`` 为真时直接放行（取消语义优先，不检查、不入队）；
        断言成立直接放行；不成立则求值导向内容（空结果放弃本次导向），
        经 ``steer()`` 入队一条 EVENT/STEER 消息由下一回合消费。必须
        ``return turn``。
        """
        if turn.aborted:
            return turn   # 取消 / 打断的回合：不检查、不入队（取消语义优先）
        if predicate(agent, turn):
            return turn   # 断言成立：通过
        if callable(content):
            text = content(agent)   # None / 空串 → 放弃本次导向
        else:
            text = content.resolve(agent)   # 以当前 Agent 为上下文现场求值
        if not text:
            return turn   # 无物可导向：循环自然终止
        await agent.steer(text, kind=MessageKind.EVENT,
                          source="prompt-until", tags=["prompt-until"])
        return turn

    agent.hooks.after_turn(_prompt_until_after_turn, by="prompt-until")
