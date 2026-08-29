"""flowing.composables.compact —— 内置上下文压缩 Composable（可选、非默认）。

.. rubric:: 功能介绍

本模块提供 ``use_compact()``：为 Agent 实例启用「上下文占用超阈值自动压缩」
策略。压缩 = **side_query 摘要 + fork 换链的单时机动作**：检测到超阈值后，
handler 内立即以 side_query 让模型基于当前完整上下文产出交接摘要（副线
不挂树、不落盘），把摘要复制为 ``SYSTEM`` 新根（``MessageChain.branch(None,
副本)``），再经 ``Agent.fork()`` 把 ``current_head_id`` 切到新根——旧链
完整保留在 ``tree.jsonl`` 中退役为历史，新链从摘要开始，**当前回合无缝
继续**。本模块属**应用层 / 内置 Composable 层**：随 ``flowing`` 包发布但
不自动启用，必须由 Agent 开发者在 ``setup()`` 中显式调用。

.. rubric:: 设计动机

- **单时机动作**（裁决）：检测、摘要、换链在 ``after_provider_gen`` handler 的
  一次调用内顺序完成。摘要不经主循环产出——凡主循环产出的方案都必然是
  两时机（等待下一轮 provider_gen）+ 跨时机状态（闭包标记 / 消息身份），
  ``side_query`` 把「等主循环」折叠成 handler 内一次同步的侧路调用，
  是唯一不需要第二相位与链接状态的形态。
- **机制 vs 策略**：框架核心只提供机制——``estimate_context_tokens()``
  占用估计、``side_query`` 侧路调用、``branch(None, msg)`` 开根、
  ``fork()`` 切视角（全时合法，无守卫）。「压不压、阈值多少、摘要怎么
  写」全是策略，由本模块承载一份可整体替换的默认实现（替换方式同
  ``use_retry``：``remove_by_owner("compact")`` 后自注册）。
- **回合内检测**（硬约束）：挂 ``after_provider_gen`` 而非 ``after_turn``——
  单个长回合本身可能撑爆上下文，回合间检测会漏掉「回合内爆掉」的
  场景；也不依赖 ``ContextLengthError`` 自愈——长回合不应因此被中断。
- **fork 落点是干净点**：``after_provider_gen`` 触发时，此前的 tool_call 配对
  已全部完成、本轮响应尚未挂树——fork 不产生配对断裂；fork 后本轮
  响应与后续产物嫁接新根（回合在压缩后的链上继续），这是 seek 语义的
  预期行为，不是损坏。
- **压缩对回合透明**：不 abort、不取消、不占用消息队列、不在旧链留
  压缩指令消息；被压缩的任务不感知压缩发生。压缩自身的 LLM 调用走
  ``side_query``（``by="_side"``），用量可经 ``after_provider_gen`` 钩子观测。

.. rubric:: 使用示例

.. code-block:: python

    # 手写子类：setup() 中启用（默认阈值 0.8）
    from flowing import Agent
    from flowing.composables.compact import use_compact

    class MyAgent(Agent):
        system_prompt = "你是一个助手。"

        async def setup(self) -> None:
            use_compact(self)

    # 调参 + 自定义压缩指令（开发者定义优先，use_compact 不覆盖）
    class CarefulAgent(Agent):
        compact_prompt = Parsable("请将以上对话压缩为交接摘要，保留全部文件路径与待办。")

        async def setup(self) -> None:
            use_compact(self, threshold=0.9)

    # 介入压缩决策：拦截本次压缩
    async def setup(self) -> None:
        use_compact(self)

        @on("on_compact", by="myapp")
        async def _veto(agent, estimate):
            if agent.tags.get("no_compact"):
                raise Intercepted()   # 取消本次压缩（回合照常继续）
            return estimate

.. rubric:: 行为规约（模块级）

- 期待行为：调用 ``use_compact(agent)`` 后，每次主回合成功 ``provider_gen()``
  （``by="_turn"``）经 ``after_provider_gen`` 检查
  ``estimate_context_tokens().usage_ratio``；``ratio > threshold`` 时
  执行压缩序列（dispatch ``on_compact`` → ``side_query`` 取摘要 →
  开新根 → ``fork`` 换链），全部在 handler 一次调用内完成。
- 非行为：不 abort 回合、不操作消息队列、不在旧链追加任何压缩相关
  消息、不 drain、不打断在途 provider_gen / 工具执行、不持久化任何压缩状态
  （无闭包标记——本设计没有需要持久化的状态）。
- 边缘情况：``usage_ratio is None``（模型未声明 ``context_window``）时
  **永不触发**——不可测则不动作，文档化口径；``on_compact`` 被
  ``Intercepted`` → 本次取消；``side_query`` 异常 / 空摘要（abort 或
  截断）→ 不换链、不影响本回合，下一次超阈值自然重试（无热循环：
  两次尝试之间至少隔着一次主循环 provider_gen）；压缩途中崩溃 → 旧链完好、
  无副作用，恢复后下一次超阈值重来；``before_fork`` 拦截 → 新根已
  落盘而 head 未切，白压缩一次（安全）。
- 不变量：旧链物理完整保留（append-only，压缩不写 tombstone、不做
  手术）；新根 ``parent_id is None``、``kind=SYSTEM``、
  ``source="compact"``。
- 递归防护：经 ``after_provider_gen["_turn"]`` pattern 注册（``match_on="by"``，
  fnmatch 字面量即精确匹配）——副线（含压缩自身的 ``side_query``）与其余
  来源在 dispatch 层被过滤、根本不分发到本 handler，防护不依赖任何状态。

.. seealso::

    :func:`flowing.composables.retry.use_retry` —— 另一个内置 Composable（同构形态）。
    :meth:`flowing.agent.Agent.estimate_context_tokens` —— 阈值检测的数据源。
    :meth:`flowing.agent.Agent.side_query` —— 摘要的侧路调用通道。
    :meth:`flowing.agent.Agent.fork` —— 换链的视角切换通道（含回合内 fork 责任段落）。
    :meth:`flowing.message.MessageChain.branch` —— ``None`` 开新根的持久化入口。
"""

from flowing.agent import Agent
from flowing.errors import Intercepted
from flowing.message import Message, MessageKind, TextBlock
from flowing.parsable import Parsable
from flowing.providers import ProviderResponse

__all__ = ["use_compact", "DEFAULT_COMPACT_PROMPT"]

DEFAULT_COMPACT_PROMPT: str = (
    "你即将耗尽上下文。请将以上对话压缩为一份结构化的交接摘要，"
    "供你在历史被清空后无缝继续当前任务。必须使用如下结构：\n"
    "## 目标\n## 进展\n## 关键决策\n## 后续步骤\n## 关键上下文\n"
    "直接输出摘要本体；不要继续对话，不要输出任何其它内容。"
)
"""默认压缩指令（``compact_prompt`` 的缺省值）。

风格参照 kimi-code 的 ``compaction-instruction.md``（第一人称交接笔记）
与 pi 的 ``SUMMARIZATION_PROMPT``（结构化检查点），取两者的公共结构：
目标 / 进展 / 关键决策 / 后续步骤 / 关键上下文。

行为边界：仅在 Agent 上**尚无** ``compact_prompt`` 属性时由
``use_compact`` 绑定为实例属性（与「插件绑函数」同约定——开发者
已定义的优先）；这是策略的一部分，随 ``use_compact`` 整体可被替换。
"""


def use_compact(agent: Agent, threshold: float = 0.8) -> None:
    """为 Agent 实例启用「上下文超阈值自动压缩」策略（可选、非默认）。

    .. rubric:: 功能介绍

    依次完成三次挂载（均 ``by="compact"``，``remove_by_owner("compact")``
    整组移除）：

    0. ``agent.hooks.declare("on_compact", by="compact")`` —— 声明压缩
       观测/拦截钩子点（同名同 ``by`` 幂等）；value 为
       :class:`flowing.context.ContextUsageEstimate`，handler 可
       ``raise Intercepted`` 取消本次压缩。
    1. ``compact_prompt`` 缺省绑定——Agent 上尚无该属性（含类属性）时，
       把 ``Parsable(DEFAULT_COMPACT_PROMPT)`` 绑为实例属性；开发者已
       定义的优先，不覆盖。绑定经 ``Agent.__setattr__`` 常规落
       ``__dict__``（``compact_prompt`` 非注册状态键，不写透状态袋），
       并照常触发一次 fire-and-forget 的 watcher 通道（透明、无改写）；
    2. ``agent.hooks.after_provider_gen["_turn"](决策 handler 闭包, by="compact")``
       —— 检测 + 摘要 + 换链的唯一 handler，经 pattern 注册（``match_on="by"``，
       字面量 ``"_turn"`` 即精确匹配主回合来源）；行为契约见本函数
       实现内 ``_compact_after_provider_gen`` 闭包的 docstring。

    .. rubric:: 设计动机

    「何时压、压成什么样」没有唯一正确答案：阈值、prompt 都应可整体
    替换，因此核心只保留估计 / side_query / 开根 / fork 四个机制，本
    函数提供默认策略。检测挂 ``after_provider_gen``（每次 provider_gen 后）而非
    ``after_turn``（每回合后）：单个长回合本身可能撑爆上下文，回合间
    检测会漏掉「回合内爆掉」的场景；也不在 tool_call 后检测——工具
    结果挂树后轮到下一次 provider_gen，``after_provider_gen`` 自然覆盖。

    .. rubric:: 使用示例

    .. code-block:: python

        # 1. 最小启用
        async def setup(self) -> None:
            use_compact(self)

        # 2. 调阈值
        async def setup(self) -> None:
            use_compact(self, threshold=0.9)

        # 3. 整体替换默认策略
        async def setup(self) -> None:
            use_compact(self)
            self.hooks.after_provider_gen.remove_by_owner("compact")
            # …注册自己的检测与换链 handler…

    .. rubric:: 行为规约

    - ``threshold``：触发线为 ``usage_ratio > threshold``（严格大于）；
      合法区间 ``0 < threshold <= 1.0``（``usage_ratio`` 不 clamp，
      ``> 1.0`` 是合法溢出信号，``threshold=1.0`` 即「只在溢出后压缩」）。
    - **重复调用**：不做幂等去重记号——每次调用按注册语义各自叠加一个
      检测 handler（各自闭包持有独立 ``threshold``；允许以不同参数多次
      启用）。recover 管线在新实例上重跑 ``setup()``，钩子注册表随实例
      重建，天然不叠加。
    - 非行为：不修改 ``agent.model``、不动消息队列、不注册工具、不
      provide 值、不持久化任何状态。
    - 并发：同一 Agent 回合串行（单工作循环 Task），handler 在回合内
      同步段执行，无并发读写。

    **前置条件**：``agent.hooks`` 可用（实例已过 ``__init__``）；
    ``after_provider_gen`` 为核心预填钩子点，无需 ``declare()``。

    **后置条件**：``on_compact`` 钩子点已声明；``after_provider_gen`` 链尾追加
    一个 ``by="compact"`` handler；Agent 上存在 ``compact_prompt``
    （缺省绑定或开发者定义）。

    .. rubric:: 测试案例

    1. 前置：未调用 ``use_compact`` → 操作：上下文占用 95% → 期望：
       无任何钩子触发、无新根、head 不变，行为与不调用逐字节等价。
    2. 前置：``use_compact(agent)``，占用 85% → 操作：一次主回合
       成功 ``provider_gen()`` → 期望：``on_compact`` 收到
       ``ContextUsageEstimate`` 一次；``side_query`` 被调用一次
       （指令为 resolve 后的 ``compact_prompt``）；``_messages`` 新增
       ``parent_id is None``、``kind=SYSTEM``、``source="compact"`` 的
       根消息；``current_head_id`` 指向它；旧链物理完整；本回合未被
       abort，后续 provider_gen 的上下文从新根上溯（只见摘要）。
    3. 前置：同上 → 操作：压缩过程中检查 ``side_query`` 响应的
       ``by`` → 期望：``"_side"``，且该副线调用的 ``after_provider_gen``
       因 pattern 不匹配不分发到本 handler（无递归，dispatch 层过滤）。
    4. 前置：``on_compact`` handler ``raise Intercepted`` → 期望：
       不调 side_query、不建根、不换链，回合照常继续。
    5. 前置：模型未声明 ``context_window``（``usage_ratio is None``）
       → 期望：任何占用下均不触发。
    6. 前置：``side_query`` 返回空文本 / 抛 Provider 异常 → 期望：
       不换链、异常不外抛、本回合正常收尾；下一次超阈值 provider_gen 后
       再次尝试压缩。
    7. 前置：``use_compact(agent, threshold=0)`` 或 ``1.5`` → 期望：
       抛 ``ValueError``，无任何注册副作用。
    8. 前置：开发者子类已定义 ``compact_prompt = Parsable("自定义")``
       → 操作：``use_compact(agent)`` → 期望：实例上仍是自定义值，
       side_query 承载自定义指令。
    9. 前置：压缩完成后 → 期望：旧链消息全部仍在 ``_messages`` /
       tree.jsonl（append-only）；``fork`` 经 ``before_fork`` /
       ``after_fork`` 正常钩子路径。

    .. rubric:: 参数

    :param agent: 目标 Agent 实例；标准用法是在 ``setup()`` 中传 ``self``。
    :param threshold: 触发阈值（``usage_ratio`` 严格大于它时启动压缩），
        ``0 < threshold <= 1.0``，默认 ``0.8``。

    .. rubric:: 异常

    :raises ValueError: ``threshold`` 不在 ``(0, 1.0]`` 区间（于注册任何
        handler 之前抛出）。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.hooks.HookRegistry.declare()``（时机：注册形态
      第 0 步，幂等）；``flowing.hooks.HookList.__getitem__``（str 形态）
      与 ``flowing.hooks.PatternRegistrar.__call__()``（时机：注册形态
      第 2 步，pattern 注册）；``hasattr`` 检查 + 实例属性绑定（时机：
      第 1 步，仅缺省时）
    - 被调：无（框架内无调用方；应用层在 Agent ``setup()`` 中调用）

    .. seealso::

        :data:`DEFAULT_COMPACT_PROMPT` —— 缺省压缩指令。
        :meth:`flowing.agent.Agent.side_query` —— 摘要取用的侧路通道。
    """
    # 先校验后注册：非法参数抛 ValueError，不产生任何注册副作用
    if not 0 < threshold <= 1.0:
        raise ValueError("threshold 必须在 (0, 1.0] 区间")

    # 注册形态第 0 步：声明压缩观测/拦截钩子点（同名同 by 幂等）
    agent.hooks.declare("on_compact", by="compact")

    # 注册形态第 1 步：compact_prompt 缺省绑定（开发者已定义优先；
    # hasattr 经 __getattr__ 回退探测（_extra），无 _extra 键时
    # AttributeError → False → 绑定缺省实例属性。状态键不在属性
    # 协议上，本探测与状态系统无涉）
    if not hasattr(agent, "compact_prompt"):
        agent.compact_prompt = Parsable(DEFAULT_COMPACT_PROMPT)

    async def _compact_after_provider_gen(agent: Agent, response: ProviderResponse) -> ProviderResponse:
        """检测 + 摘要 + 换链的唯一 handler（内部 API，经 ``by="compact"``
        定位与移除）。

        - async handler（dispatch 与 ``side_query`` / ``fork`` 有真实
          await 需求，S-08/M-91），dispatch 经 ``inspect.isawaitable``
          透明 await；``threshold`` 为本次 ``use_compact`` 调用闭包的
          内部状态。
        - **pattern 过滤**：本 handler 经 ``after_provider_gen["_turn"]``
          注册（``match_on="by"``），仅主回合来源会被分发到；副线
          （``by="_side"``，含压缩自身的 ``side_query``）与其余来源在
          dispatch 层被过滤——递归防护不依赖任何状态。
        - **检测口径**：本 handler 运行时本轮响应**尚未挂树**，估计不含
          它——比率按上一锚点口径，略滞后一条消息，文档化可接受。
        - ``usage_ratio is None``（模型未声明 ``context_window``）→
          原样返回，永不触发（不可测则不动作）。
        - ``on_compact`` 被 ``Intercepted`` → 本次取消：不调 side_query、
          不建根、不换链。
        - ``side_query`` 抛异常（Provider 异常直抛是副线规格）或返回空
          摘要（abort / 截断）→ 不换链、异常**不外抛**（压缩失败不影响
          本回合），下一次超阈值自然重试；两次尝试之间至少隔着一次主循环
          provider_gen，无热循环。
        - **fork 时序**：位于「此前 tool_call 配对完整、本轮响应未挂树」
          的干净点，不产生配对断裂；fork 后本轮响应按 seek 语义嫁接新根。
          ``before_fork`` 拦截（``Intercepted``）→ 新根已落盘而 head 未
          切——白压缩一次，安全（下次超阈值重来）；改写 target 属开发者
          责任（见 :meth:`flowing.agent.Agent.fork`）。
        - 必须 ``return response``；不得改写 ``response``。
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

    # 注册形态第 2 步：唯一 handler，pattern 注册（match_on="by"，"_turn" 精确
    # 匹配主回合来源）；by="compact"，remove_by_owner("compact") 整组移除
    agent.hooks.after_provider_gen["_turn"](_compact_after_provider_gen, by="compact")
