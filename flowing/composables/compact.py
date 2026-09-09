"""flowing.composables.compact —— 内置上下文压缩 Composable（可选、非默认）。

.. rubric:: 功能介绍

本模块提供两个内置上下文压缩 Composable（可选、非默认；二选一）：

- ``use_compact()`` —— 请求**后**触发（``after_provider_gen["_turn"]``），
  全量摘要换新根：让模型把当前对话压缩为交接摘要，摘要作为新根开新链，
  旧链整体成为历史。
- ``use_auto_compact()`` —— 请求**前**触发（``before_provider_gen``），
  头尾保留式压缩：保留链头（≤ ``head_tokens``）与链尾（≤ ``tail_tokens``）
  的原始消息，中段压缩为「用户指令汇编 + 交接摘要」一条消息，开分支
  换链后本次请求直接携压缩后上下文发出。

``use_compact()`` 的压缩序列：主回合 LLM 调用成功返回后检查
``estimate_context_tokens().usage_ratio``，超阈值时在同一 handler 调用内
依次——经 ``side_query`` 副线让模型基于当前完整上下文产出交接摘要
（不挂树、不落盘）→ 摘要作为新的 ``SYSTEM`` 根消息挂树
（``MessageChain.branch(None, ...)``）→ ``Agent.fork()`` 把
``current_head_id`` 切到新根。旧链完整保留在消息树与 ``tree.jsonl``
中成为历史，新链从摘要开始，当前回合无缝继续。

``use_auto_compact()`` 的注册面同构：handler 挂 ``before_provider_gen``
（``by="auto_compact"``，普通注册——主回合/副线区分由 handler 读
``context.messages`` 末条消息是否在树内完成，零状态递归防护），详见
:func:`use_auto_compact`。

本模块属应用层 / 内置 Composable：随 ``flowing`` 包发布但不自动启用，
必须由 Agent 开发者在 ``setup()`` 中显式调用。不调用的 Agent 不持有任何
压缩相关 handler 与状态——行为与不调用时逐字节等价。

「何时压、压成什么样」没有唯一正确答案：阈值与摘要指令都应可整体替换，
框架核心只提供占用估计、``side_query`` 副线、开新根（``branch``）与
``fork`` 四个机制，本模块提供一份可整体替换的默认策略。替换方式同
``use_retry``：``remove_by_owner("compact")`` 移除默认 handler 后自注册。

.. rubric:: 注册面清单

- 启用方式：仅阶段二——``setup()`` 中调用 ``use_compact(self)`` （恢复
  时 ``setup()`` 在新实例上执行，天然不叠加）。未启用时零开销：
  ``after_provider_gen`` 链上无任何 ``by="compact"`` handler，
  ``use_compact`` 不会为 Agent 绑定 ``compact_prompt`` 属性，
  ``on_compact`` 钩子点不存在（访问抛
  :class:`flowing.errors.UnknownHookPointError`）。
- 注册的资源：Agent 侧 ``compact_prompt`` 属性（缺省绑定
  :data:`DEFAULT_COMPACT_PROMPT`，开发者已定义时保留开发者的，见
  :func:`use_compact`）；副线查询 ``side_query`` （``by="_side"``，框架
  机制，非本模块注册）。无 provide key、无工具注册、无 Agent 状态键。
- 声明的钩子点：``on_compact`` （``by="compact"``，无 ``match_on``）——
  压缩观测 / 拦截点，由本模块在使用处 dispatch（谁声明谁 dispatch）。
- 挂载的钩子：``after_provider_gen["_turn"]`` 检测与换链 handler
  （``by="compact"``，pattern 注册：``match_on="by"``，字面量
  ``"_turn"`` 精确匹配主回合来源）。``remove_by_owner("compact")`` 可
  整组移除（``on_compact`` 钩子点属声明而非 handler，不受移除影响）。
- 作用域：只影响调用它的那一个 Agent 实例；不修改 ``agent.model``、
  不动消息队列、不 abort 回合、不持久化任何压缩状态。

.. rubric:: 使用示例

.. code-block:: python

    from flowing import Agent
    from flowing.composables.compact import use_compact

    class MyAgent(Agent):
        async def setup(self) -> None:
            use_compact(self, threshold=0.8)   # 占用率超过 80% 时压缩

.. rubric:: 行为要点

- 递归防护：检测 handler 经 ``after_provider_gen["_turn"]`` pattern
  注册，副线（含压缩自身的 ``side_query``）与其余来源在 dispatch 层被
  过滤、根本不分发到本 handler——防护不依赖任何状态。
- 触发条件：``usage_ratio > threshold`` （严格大于）；模型未声明
  ``context_window`` （``usage_ratio is None``）时永不触发——不可测则
  不动作。
- 压缩对回合透明：不 abort、不占用消息队列、不在旧链追加任何压缩相关
  消息；被压缩的任务不感知压缩发生。
- 旧链物理完整保留（append-only：压缩不写删除标记、不做树手术）；新根
  ``parent_id is None``、``kind=SYSTEM``、``source="compact"``。

.. seealso::

    :func:`flowing.composables.retry.use_retry` —— 另一个内置
    Composable（同构形态）。
    :meth:`flowing.agent.Agent.estimate_context_tokens` —— 阈值检测的
    数据源。
    :meth:`flowing.agent.Agent.side_query` —— 摘要的副线调用通道。
    :meth:`flowing.agent.Agent.fork` —— 换链的视角切换通道。
    :meth:`flowing.message.MessageChain.branch` —— ``None`` 开新根的
    持久化入口。
"""

from __future__ import annotations

import copy

from flowing.agent import Agent
from flowing.context import Context
from flowing.errors import Intercepted
from flowing.message import (
    Message,
    MessageKind,
    TextBlock,
    estimate_message_tokens,
)
from flowing.parsable import Parsable
from flowing.providers import ProviderResponse

__all__ = ["use_compact", "use_auto_compact", "DEFAULT_COMPACT_PROMPT"]

DEFAULT_COMPACT_PROMPT: str = (
    "You are about to run out of context. Compress the conversation above into a "
    "structured handover summary so you can seamlessly continue the current task "
    "after the history is cleared. Use exactly this structure:\n"
    "## Goal\n## Progress\n## Key decisions\n## Next steps\n## Key context\n"
    "Output only the summary itself; do not continue the conversation and do not "
    "output anything else."
)
"""默认压缩指令（``compact_prompt`` 的缺省值）。

要求模型把当前对话压缩为一份结构化交接摘要（目标 / 进展 / 关键决策 /
后续步骤 / 关键上下文五节），供上下文被清空后无缝继续当前任务。

行为边界：仅在 Agent 上尚无 ``compact_prompt`` 属性时由 ``use_compact``
绑定为实例属性（开发者已定义的优先，不覆盖）；这是策略的一部分，随
``use_compact`` 整体可被替换。
"""


def use_compact(agent: Agent, threshold: float = 0.8) -> None:
    """为单个 Agent 实例启用「上下文超阈值自动压缩」策略（可选、非默认）。

    .. rubric:: 功能介绍

    依次完成三件事（都经 ``by="compact"`` 定位与移除）：

    1. 声明 ``on_compact`` 钩子点（同名同 ``by`` 幂等）——压缩观测 /
       拦截点，value 为 :class:`flowing.context.ContextUsageEstimate`，
       handler 可 ``raise Intercepted`` 取消本次压缩；
    2. ``compact_prompt`` 缺省绑定——Agent 上尚无该属性（含类属性）时，
       把 ``Parsable(DEFAULT_COMPACT_PROMPT)`` 绑为实例属性；开发者已
       定义的优先，不覆盖；
    3. 在 ``agent.hooks.after_provider_gen["_turn"]`` 上注册检测与换链
       handler（``by="compact"``，pattern 注册：``match_on="by"``，
       字面量 ``"_turn"`` 精确匹配主回合来源）。

    本函数是双层启用的阶段二入口，只能在 ``setup()`` （或实例存活期内
    的任意代码）中对已完成初始化的实例调用。

    .. rubric:: 使用示例

    .. code-block:: python

        # 1. 最小启用（默认阈值 0.8）
        async def setup(self) -> None:
            use_compact(self)

        # 2. 调阈值：只在实际溢出后压缩
        async def setup(self) -> None:
            use_compact(self, threshold=1.0)

        # 3. 整体替换默认策略
        async def setup(self) -> None:
            use_compact(self)
            self.hooks.after_provider_gen.remove_by_owner("compact")
            # …注册自己的检测与换链 handler…

    .. rubric:: 行为要点

    检测与压缩序列（主回合响应经 ``after_provider_gen`` 到达后，handler
    一次调用内完成）：

    - 检测：调用 ``estimate_context_tokens()`` 取 ``usage_ratio``；比率
      ``> threshold`` 才进入压缩序列（严格大于）。``usage_ratio is None``
      （模型未声明 ``context_window``）时不动作、永不触发。
    - 拦截：先向 ``on_compact`` 派发本次估计值；任一 handler
      ``raise Intercepted`` 则本次压缩取消——不调 ``side_query``、不建
      新根、不换链，本回合照常继续。
    - 摘要：经 ``side_query`` 让模型基于当前完整上下文产出交接摘要
      （指令为 ``compact_prompt`` 经 Parsable 模板求值（resolve）后的
      文本；副线调用不挂树、不落盘，来源标记为 ``by="_side"``）。
    - 换链：摘要非空时把它作为新根挂树（``parent_id is None``、
      ``kind=SYSTEM``、``source="compact"``），再 ``fork`` 到新根。旧链
      物理完整保留。
    - 摘要失败（``side_query`` 抛异常或返回空文本）→ 不换链、异常不外
      抛、本回合正常收尾；下一次超阈值的主回合 ``provider_gen`` 后再试
      ——两次尝试之间至少隔着一次主循环调用，不会热循环。
    - ``before_fork`` 被拦截 → 新根已落盘而 head 未切：本次压缩白做
      一次（安全，下次超阈值重来）。

    检测口径：handler 运行时本轮响应尚未挂树，估计值不含它——比率按
    上一锚点口径，略滞后一条消息。

    参数语义：

    - ``threshold``：触发阈值，``usage_ratio`` 严格大于它时启动压缩；
      合法区间 ``0 < threshold <= 1.0`` （``usage_ratio`` 不做上限截断，
      ``> 1.0`` 是合法的溢出信号，``threshold=1.0`` 即「只在溢出后压
      缩」）。区间外的值抛 ``ValueError``。

    重复调用：不做幂等去重——每次调用按注册语义各自叠加一个检测 handler
    （各自闭包持有独立的 ``threshold``），允许以不同参数多次启用。恢复
    时 ``setup()`` 在新实例上执行，钩子注册表随实例重建，天然不叠加。

    并发：同一 Agent 的回合串行执行（单工作循环 Task），handler 在回合
    内同步段执行，无并发读写。

    不做什么：不修改 ``agent.model``、不动消息队列、不 abort 回合、不
    注册工具、不 provide 值、不持久化任何状态。

    :param agent: 目标 Agent 实例；标准用法是在 ``setup()`` 中传 ``self``。
    :param threshold: 触发阈值（``usage_ratio`` 严格大于它时启动压缩），
        ``0 < threshold <= 1.0``，默认 ``0.8``。
    :raises ValueError: ``threshold`` 不在 ``(0, 1.0]`` 区间（于注册任何
        handler 之前抛出）。

    .. seealso::

        :data:`DEFAULT_COMPACT_PROMPT` —— 缺省压缩指令。
        :meth:`flowing.agent.Agent.side_query` —— 摘要取用的副线通道。
        :meth:`flowing.agent.Agent.fork` —— 换链的视角切换通道。
    """
    # 先校验后注册：非法参数抛 ValueError，不产生任何注册副作用
    if not 0 < threshold <= 1.0:
        raise ValueError("threshold must be in (0, 1.0]")

    # 声明压缩观测/拦截钩子点（同名同 by 幂等）
    agent.hooks.declare("on_compact", by="compact")

    # compact_prompt 缺省绑定（开发者已定义优先；hasattr 经 __getattr__
    # 回退探测 _extra，未命中时返回 False 才绑定缺省实例属性）
    if not hasattr(agent, "compact_prompt"):
        agent.compact_prompt = Parsable(DEFAULT_COMPACT_PROMPT)

    async def _compact_after_provider_gen(agent: Agent, response: ProviderResponse) -> ProviderResponse:
        """检测 + 摘要 + 换链的唯一 handler（内部 API，经 ``by="compact"``
        定位与移除）。

        只处理主回合来源（经 ``after_provider_gen["_turn"]`` pattern
        注册，副线 ``by="_side"`` 在 dispatch 层被过滤——递归防护不依赖
        任何状态）；必须 ``return response``、不得改写 ``response``。
        ``threshold`` 为本次 ``use_compact`` 调用闭包的内部状态。

        行为：``estimate_context_tokens().usage_ratio`` 为 ``None`` 或
        ``<= threshold`` 时不动作；超过阈值时依次 dispatch ``on_compact``
        （``Intercepted`` 取消本次：不建根、不换链）、``side_query`` 取
        摘要（异常或空文本：不换链、不外抛，下一次超阈值的主回合后再试）
        、摘要非空则挂新根后 ``fork`` （``before_fork`` 拦截时新根已落盘
        而 head 未切，安全）。检测口径：本轮响应尚未挂树，估计不含它。
        """
        # 无来源判断：pattern 注册（after_provider_gen["_turn"]）保证只有主回合响应会被分发到
        estimate = agent.estimate_context_tokens()   # 检测口径：本轮响应尚未挂树
        ratio = estimate.usage_ratio
        if ratio is None or ratio <= threshold:
            return response   # 不可测（窗口未知）或未超阈值：不动作
        try:
            await agent.hooks.on_compact.dispatch(agent, estimate)   # 可 Intercepted 取消
            summary = await agent.side_query(
                agent.compact_prompt.resolve(agent))   # 散装打包；开发者定义优先
        except Intercepted:
            return response   # on_compact 取消本次压缩：不建根、不换链
        except Exception:
            return response   # side_query 失败：不换链、不外抛，下次超阈值重试
        if not summary.strip():
            return response   # 空摘要（abort / 截断）兜底：不换链
        new_root_id = agent.chain.branch(None, Message(
            kind=MessageKind.SYSTEM, content=[TextBlock(text=summary)],
            source="compact"))   # 新根：id 重新分配，旧链完整保留
        try:
            await agent.fork(new_root_id)   # 干净点（配对完整、响应未挂树）；before_fork 拦截属开发者责任
        except Intercepted:
            pass   # 新根已落盘而 head 未切：白压缩一次，安全
        return response

    # 唯一 handler，pattern 注册（match_on="by"，"_turn" 精确匹配主回合
    # 来源）；by="compact"，remove_by_owner("compact") 整组移除
    agent.hooks.after_provider_gen["_turn"](_compact_after_provider_gen, by="compact")


def use_auto_compact(
    agent: Agent,
    *,
    threshold: float = 0.8,
    tail_tokens: int = 20_000,
    head_tokens: int = 10_000,
    user_instruction_tokens: int = 2_000,
) -> None:
    """为单个 Agent 实例启用「请求前头尾保留式自动压缩」策略（可选、非默认）。

    .. rubric:: 功能介绍

    在 ``before_provider_gen`` 上注册检测与压缩 handler
    （``by="auto_compact"``）：每次主回合 LLM 请求发出**之前**检查上下文
    占用，超过 ``threshold`` 时在同一 handler 调用内完成压缩——保留链
    头部（≤ ``head_tokens``）与尾部（≤ ``tail_tokens``）的原始消息，
    中段压缩为一条「用户指令汇编 + 交接摘要」消息，从前缀末条开分支、
    后缀深拷贝拼接，再 ``fork`` 换链；本次请求直接携带压缩后的上下文
    发出（handler 整体改写 ``Context``）。

    与 :func:`use_compact` 的区别：use_compact 在请求**后**触发、全量
    摘要换新根（工作现场丢失）；本函数在请求**前**触发、头尾保留（开场
    指令与最近的工具调用现场原样留在上下文里）。两者并存但应二选一——
    同时启用会在不同触发点各自动作。

    .. rubric:: 使用示例

    .. code-block:: python

        async def setup(self) -> None:
            use_auto_compact(self)   # 默认 0.8 / 头 10k / 尾 20k / 指令 2k

        async def setup(self) -> None:
            use_auto_compact(self, threshold=0.9, tail_tokens=40_000)

    .. rubric:: 行为要点

    - 主回合判定：handler 入口读 ``context.messages`` 末条消息——主回合
      请求的末条消息必在消息树内（入队 / 挂树时已铸自增 id）；副线
      ``side_query`` 自组装散装 Context（不进树），据此放行。递归防护
      零状态（压缩自身的 side_query 也被同一规则放行）。
    - 触发条件：``estimate_context_tokens().usage_ratio > threshold``
      （严格大于）；``usage_ratio is None``（模型未声明 context_window）
      时永不触发。
    - 拦截：压缩前 dispatch ``on_compact``（value 为
      :class:`flowing.context.ContextUsageEstimate`）；任一 handler
      ``raise Intercepted`` 则本次取消。
    - 切割规则：前缀从链头累计 token 取 ≤ ``head_tokens`` 的最长前缀，
      切割点只允许落在 PROVIDER 消息的**上边界**（ToolCallBlock/TOOL
      配对天然完整）；后缀从链尾同规则取 ≤ ``tail_tokens``，切割点同样
      在 PROVIDER 上边界——链尾的非 PROVIDER 连续段（本轮新指令 / 刚落盘
      的 TOOL 结果）始终保留，含 TOOL 时连同其 PROVIDER 配对锚一起保留
      （配对完整优先于预算）；与前缀重叠时收缩后缀（前缀优先）。触发点在请求
      之前，上一轮的工具配对均已闭合挂树。
    - 中段处理：前后缀之间从前数累计 ≤ ``user_instruction_tokens`` 的
      USER 消息，紧凑格式化（一个小标题 + 各条 ``---`` 分隔），与
      ``side_query`` 产出的交接摘要合并为一条 ``SYSTEM`` 消息
      （``source="auto_compact"``），经 ``chain.branch`` 挂到前缀末条
      之下。内置 prompt 与格式化文本一律英文。
    - 后缀拼接：逐条 ``copy.deepcopy`` 后置 ``id=None`` /
      ``parent_id=None``，链式 ``branch`` 拼接（挂树自动铸新 id，与旧
      节点无冲突）；旧链物理完整保留（append-only）。
    - 换链：``fork`` 到后缀末条新 id（无后缀则到压缩消息）；
      ``before_fork`` 被拦截 → 新分支已落盘而 head 未切（白压一次，
      安全），本次请求携原上下文发出。
    - 摘要失败（``side_query`` 异常或返回空文本）→ 不换链、异常不外
      抛、本次请求照常发出；下一次请求前再试，不会热循环。
    - 边界情形：链太短（前缀 + 后缀已覆盖全链、无中段）→ 跳过；压缩后
      仍超窗 → 本次请求照常发出，由
      :class:`flowing.errors.ContextLengthError` 通道兜底。

    重复调用：不做幂等去重——每次调用各自叠加一个 handler（闭包持有
    独立参数）。恢复时 ``setup()`` 在新实例上执行，钩子注册表随实例
    重建，天然不叠加。``remove_by_owner("auto_compact")`` 可整组移除。

    不做什么：不修改 ``agent.model``、不动消息队列、不 abort 回合、不
    注册工具、不持久化任何压缩状态（消息树变更本身是常规落盘）。

    :param agent: 目标 Agent 实例；标准用法是在 ``setup()`` 中传 ``self``。
    :param threshold: 触发阈值（``usage_ratio`` 严格大于它时启动压缩），
        ``0 < threshold <= 1.0``，默认 ``0.8``。
    :param tail_tokens: 尾部保留的 token 上限，``>= 0``，默认 ``20_000``。
    :param head_tokens: 头部保留的 token 上限，``>= 0``，默认 ``10_000``。
    :param user_instruction_tokens: 中段用户指令汇编的 token 上限，
        ``>= 0``，默认 ``2_000``。
    :raises ValueError: 参数越界（于注册任何 handler 之前抛出）。

    .. seealso::

        :func:`use_compact` —— 请求后全量换链的同族策略（二选一）。
        :meth:`flowing.agent.Agent.estimate_context_tokens` —— 阈值检测的
            数据源。
        :meth:`flowing.message.estimate_message_tokens` —— 单条消息的
            估算口径。
    """
    # 先校验后注册：非法参数抛 ValueError，不产生任何注册副作用
    if not 0 < threshold <= 1.0:
        raise ValueError("threshold must be in (0, 1.0]")
    for name, value in (("tail_tokens", tail_tokens),
                        ("head_tokens", head_tokens),
                        ("user_instruction_tokens", user_instruction_tokens)):
        if value < 0:
            raise ValueError(f"{name} must be >= 0")

    # 声明压缩观测/拦截钩子点（与 use_compact 共用；同名同 by 幂等）
    agent.hooks.declare("on_compact", by="compact")

    # compact_prompt 缺省绑定（开发者已定义优先）
    if not hasattr(agent, "compact_prompt"):
        agent.compact_prompt = Parsable(DEFAULT_COMPACT_PROMPT)

    def _cut_prefix(chain: list[Message], budget: int) -> int:
        """最长前缀的右开边界：累计 ≤ budget，且切割点落在 PROVIDER 上边界。"""
        cut, total = 0, 0
        for i, msg in enumerate(chain):
            total += estimate_message_tokens(msg)
            if total > budget:
                break
            if i + 1 >= len(chain) or chain[i + 1].kind is MessageKind.PROVIDER:
                cut = i + 1   # 下一条是 PROVIDER（或已到链尾）：合法切割点
        return cut

    def _cut_suffix(chain: list[Message], budget: int, lo: int) -> int:
        """最长后缀的起始下标：累计 ≤ budget，切割点落在 PROVIDER 上边界。

        链尾的非 PROVIDER 连续段（本轮新用户指令 / 刚落盘的 TOOL 结果）
        始终保留——触发压缩的这次请求不能丢掉用户刚发的指令；该段含 TOOL
        时连同其 PROVIDER（配对锚）一起保留：配对完整优先于预算。
        """
        n = len(chain)
        t = n
        while t > lo and chain[t - 1].kind is not MessageKind.PROVIDER:
            t -= 1
        if t < n and any(m.kind is MessageKind.TOOL for m in chain[t:n]):
            start = t - 1 if t - 1 >= lo else t   # TOOL 段连同其 PROVIDER
        else:
            start = t
        total = sum(estimate_message_tokens(m) for m in chain[start:])
        i = start - 1
        while i >= lo:
            total += estimate_message_tokens(chain[i])
            if total > budget:
                break
            if chain[i].kind is MessageKind.PROVIDER:
                start = i   # PROVIDER 上边界：合法的后缀起点
            i -= 1
        return start

    async def _auto_compact_before_gen(agent: Agent, context: Context) -> Context:
        """请求前检测与压缩 handler（内部 API，``by="auto_compact"``）。"""
        # 主回合判定：末条消息须在消息树内（副线/散装 Context 直接放行）
        if not context.messages or context.messages[-1].id not in agent._messages:
            return context
        estimate = agent.estimate_context_tokens()
        ratio = estimate.usage_ratio
        if ratio is None or ratio <= threshold:
            return context
        try:
            await agent.hooks.on_compact.dispatch(agent, estimate)
            summary = await agent.side_query(agent.compact_prompt.resolve(agent))
        except Intercepted:
            return context   # on_compact 取消本次压缩
        except Exception:
            return context   # side_query 失败：不动作、不外抛，下次再试
        if not summary.strip():
            return context   # 空摘要兜底

        chain = list(agent.chain.walk(agent.current_head_id))[::-1]   # 根 → head
        cut = _cut_prefix(chain, head_tokens)
        start = _cut_suffix(chain, tail_tokens, cut)
        middle = chain[cut:start]
        if not middle:
            return context   # 无中段可压（前缀+后缀已覆盖全链）：跳过

        # 中段用户指令汇编（从前数累计 ≤ user_instruction_tokens；--- 分隔）
        user_parts: list[str] = []
        used = 0
        for msg in middle:
            if msg.kind is not MessageKind.USER:
                continue
            cost = estimate_message_tokens(msg)
            if used + cost > user_instruction_tokens:
                break
            used += cost
            user_parts.append("".join(
                b.text for b in msg.content if isinstance(b, TextBlock)))
        sections = ["following are compacted:"]
        if user_parts:
            sections.append("## User instructions\n" + "\n---\n".join(user_parts))
        sections.append("## Compaction summary\n" + summary)

        # 从前缀末条开分支（空前缀 → 新根）；旧链物理完整保留
        parent_id = chain[cut - 1].id if cut > 0 else None
        cursor = agent.chain.branch(parent_id, Message(
            kind=MessageKind.SYSTEM,
            content=[TextBlock(text="\n\n".join(sections))],
            source="auto_compact"))
        # 后缀逐条深拷贝拼接（id/parent_id 置 None，挂树铸新 id）
        for msg in chain[start:]:
            twin = copy.deepcopy(msg)
            twin.id = None
            twin.parent_id = None
            cursor = agent.chain.branch(cursor, twin)
        try:
            await agent.fork(cursor)   # 切到压缩后分支
        except Intercepted:
            return context   # 新分支已落盘而 head 未切：白压一次，安全
        return agent._assemble_context()   # 本次请求直接携压缩后上下文发出

    # 唯一 handler，普通注册；by="auto_compact"，remove_by_owner 整组移除
    agent.hooks.before_provider_gen(_auto_compact_before_gen, by="auto_compact")
