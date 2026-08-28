"""flowing.message —— 对象层消息模型、消息队列与消息级树手术（最终 API 规约）.

.. rubric:: 模块定位

本模块定义 Flowing 的**对象层消息表示**（与任何 Provider API 的 ``role`` 字段、任何 UI
渲染样式彻底解耦）、Agent 的**消息队列**（优先级排序 + 同级 FIFO），以及消息级树的
**任意手术入口** ``MessageChain``。本模块属于框架核心层。

消息子系统的职责边界（五条）：

1. 消息的产生与表示：任意来源（用户、LLM、工具、外部 Agent、系统事件）的新信息统一
   表示为 :class:`Message`。
2. 消息的组织结构：**消息级树（允许多根的森林）**——树节点 = 消息，
   ``Message.id`` + ``Message.parent_id`` 链；``parent_id=None`` 是**根标记**，
   一棵树允许多个根（根集合 = 所有 ``parent_id=None`` 的消息），新根由
   ``MessageChain.branch(None, msg)`` 开启（典型场景：上下文压缩换链——摘要
   作为新根开新链，旧树完整保留）；**不设虚拟根节点**（裁决：统一性收益抵不上
   对持久化 / 恢复 / 上下文组装的侵入）。``Agent.current_head_id`` 指向
   **某条消息的 id**；fork 可切到任意消息（含根——fork 只切视角，从不创建
   节点，「开新根」归 ``MessageChain``）
   （fork 的钩子与回合内 seek 语义见 ``flowing.agent.Agent.fork``）。
3. 逻辑 Turn 只是**执行概念**：消费一条消息 → ``finish=True`` 的执行过程；执行期载体是
   ``flowing.agent.TurnContext``（不落盘、不进树、崩溃后不恢复）。本模块的
   ``Message.turn_end`` 是「逻辑 turn 关闭」的**边界标记**（agent 层写入，
   含取消关闭——见 S-14 分层）。
4. 消息的排队与调度：外部消息经 :class:`MessageQueue` 按优先级排序消费；
   ``MessageKind.PROVIDER`` **永不进队列**（永远在逻辑 turn 内由 Provider adapter 产生）。
5. 消息的持久化与手术：一行一个 Message，消息完整后 append；任意历史修改走
   :class:`MessageChain` 五 op + tombstone + 撕裂末行容忍（无 checkpoint）。

.. rubric:: Message 字段规约总表

================ ================================================================================
字段             规约要点
================ ================================================================================
``id``           全局唯一消息 id（UUID），同时是消息级树的节点 id
``parent_id``    消息级父链；``None`` = 根标记（允许多根，森林模型）；树上溯与上下文组装的唯一依据
``kind``         对象层唯一角色判别，八值枚举 :class:`MessageKind`；**无** ``role`` 属性
``content``      :class:`ContentBlock` 列表，不同 type **交错排列**
``turn_end``     逻辑 turn 关闭的边界标记；**agent 层**写入（turn 随本条消息关闭 → ``True``）
``partial``      流式中断的未完成消息标记；中断时已累积内容**保留落盘**（非丢弃）
``synthetic``    ``True`` = 恢复时合成的占位消息（孤立 tool_call 的占位 TOOL 消息），非真实产物
``tool_call_id`` 配对锚（仅 ``kind=TOOL`` 非 ``None``）：与 PROVIDER 消息 ``ToolCallBlock.id`` 1:1 严格成对
``tool_status``  工具结果状态四值（仅 ``kind=TOOL`` 非 ``None``）；``__post_init__`` 双向强制
``source``       自由字符串二级分类（框架不枚举），投递方填写
``tags``         任意标签列表，用于分组 / 过滤 / 清洗
``priority``     队列排序依据，``INTERRUPT > STEER > HIGH > NORMAL > LOW``
``timestamp``    时区无关（UTC / epoch）；附着于消息，**是否进 LLM 上下文由 adapter 决定**
================ ================================================================================

.. rubric:: kind → API role 发送映射（Provider adapter 职责）

========== ====================================== ========== ============
kind       Anthropic                              OpenAI     Gemini
========== ====================================== ========== ============
USER       user                                   user       user
PROVIDER   assistant                              assistant  model
TOOL       user（tool_result）                    tool       tool
SYSTEM     user（XML 包裹）                       system     user
PEER       user（XML 包裹）                       user       user
EVENT      user（XML 包裹）                       user       user
PLUGIN     独立消息，XML 包裹（格式 adapter 定）  user       同左
SUBAGENT   独立消息，XML 包裹（格式 adapter 定）  user       同左
========== ====================================== ========== ============

- **OpenAI 列的裁决**：PLUGIN / SUBAGENT 落 ``user`` role（XML 包裹）——
  依据官方 role 语义（OpenAI prompt engineering 指南：``assistant`` =
  模型自产消息、``developer``/``system`` = 应用开发者的指令与规则、
  ``user`` = 输入与配置）：外部结果回喂属「输入」，不落 developer
  （避免给外部数据提指令权），更不伪造 assistant（会破坏轮次语义与
  tool_calls 配对）。官方未定义 system 消息合并/重排行为，该风险仅
  存在于第三方兼容层，不作为裁决依据。
- SYSTEM / PEER / EVENT / PLUGIN / SUBAGENT 的 XML 包裹格式由 adapter 按 Provider 能力
  决定，框架核心不约束具体格式。
- 「某 block type 出现在哪些 kind 中」一律是**典型情况**而非硬约束；不合法排列
  （如 PROVIDER 消息含 image block）由 **Provider adapter 在组装 API 请求时**验证并报错，
  框架核心不验证。

.. rubric:: 入队规则（七类 + SYSTEM 双通道）

- 进队列：``USER`` / ``EVENT`` / ``PEER`` / ``PLUGIN`` / ``SUBAGENT`` /
  ``SYSTEM``（可选）/ 异步工具最终结果（**以 ``EVENT`` kind 入队**，
  ``source="tool_result"``，多块 content = 标注块 + 结果块；``TOOL``
  kind 本身不入队）。
- 不进队列：``PROVIDER``——永远在逻辑 turn 内产生，经 ``Agent._append_message`` 直接挂树。
- ``SYSTEM`` 双通道：经队列投递（触发新逻辑 turn）**或**由 ``_assemble_context()``
  内部注入本 turn 的 system prompt 段（不触发新 turn）。
- **kind 无行为含义**（P3-02 裁决）：消息类型只决定 adapter 的呈现映射
  （见上表），不改变 turn 语义——任何入队批次都正常开回合、正常
  query；不存在「纯系统消息不触发 LLM 调用」之类的短路。回合级
  system 信息的三条正规路径：启动注入（``_assemble_context``）、
  随时入队 SYSTEM 消息、``before_turn`` 钩子向 ``pending_messages``
  附加 system reminder。

.. rubric:: 持久化分层（tombstone + 撕裂末行容忍）

文件 A（``tree.jsonl``）一行一个 Message，**消息完整后 append**（append-only）：

- **运行期**：手术（:class:`MessageChain` 五 op）只改内存链（内存链是唯一权威）+
  **append 变更记录行**（``{"op":"remove","id":...}`` tombstone / update / move 等），
  零截断、零重写。
- **压缩期（恢复 / compact）**：重放变更记录得权威链 → 按权威链**整文件**
  重写（FileRecordStore drain 任务内执行，tmp + rename 原子替换，见
  :mod:`flowing.persistence` 模块 docstring「原子重写统一规约」）——
  物理清除墓碑行与被标记删除的消息行。
- **压缩触发**（两个条件同时满足）：tombstone 数量 ≥ 256（默认值，可调）
  **且** Agent 空闲（``current_turn is None``；含回合收尾观察
  窗口——此时本回合消息已全部落盘，墓碑压缩在此期间触发是安全的）。
- **崩溃恢复（无 checkpoint，N-06 裁决）**：撕裂末行（崩溃半截写的
  产物）截断丢弃——至多丢失最后一次写入，语义等价于断电；中间行
  损坏按 corruption 报警，不容忍。checkpoint（链式 hash）机制已删除：
  其定位/校验收益在 tmp+rename 原子重写与撕裂容忍之下无增量
  （参考框架 kimi/pi 同以撕裂容忍覆盖）。
- 副线（``side_query``）消息不落盘；半截 message 因「完整后才 append」天然不在文件中。
- 行格式的字段级 schema 属持久化规约（参见 ``flowing.agent.Agent._persist_message``
  与 ``flowing.persistence`` 相关章节），本模块只约束**消息对象 ↔ 行**的映射语义。

.. rubric:: 恢复三条规则（摘要）

1. **半截 message 天然丢弃**：流式中未完整 append 的消息不在文件里，恢复时不存在。
2. **半截 turn 消息保留，不续跑**：已 append 的完整消息是历史；最后一个
   ``turn_end=True`` 之后的消息 = 半截 turn——在树上可观测、可审计，且
   **照常进入 LLM 上下文**（M-28 裁决：已放弃半截 turn 截断，仅保留
   规则一的半截消息丢弃）；孤立 tool_call 由规则三封闭成对后上下文合法；
   执行状态不恢复、不续跑，新 turn 正常开始。
3. **孤立 tool_call 合成占位 TOOL 消息**：恢复扫描发现 tool_call 的结果消息
   缺失（工具执行中崩溃）时，合成 ``Message(kind=TOOL, tool_call_id=<孤立调用 id>,
   tool_status="error", synthetic=True, content=[TextBlock(占位说明)])``，
   保证严格成对匹配。

恢复后 ``current_head_id`` 指向**最后持久化的消息**（不论其是否
``turn_end=True``——半截 turn 的消息照常在上溯路径上，M-28）。
完整恢复管线见 ``flowing.runtime.Runtime.recover_agent`` 与
``flowing.agent.Agent._restore``。

.. rubric:: 流式与 partial（仅三处影响面）

流式被 ``query()`` 的「返回完整 ProviderResponse」契约挡在逻辑 turn 循环之外，
真正影响的只有三处：``query()`` 内部双模式（由 ``stream: bool = True`` 显式参数
决定，S-17——``on_provider_delta`` 订阅者**纯观察**，不影响流式 / 非流式选择）、
``on_provider_delta`` 钩子（volatile，不落盘）、``Message.partial`` 字段。
流式中断（abort）时已累积内容以 ``partial=True`` 的消息**保留落盘**，而非丢弃——
这是对「半截 message 天然丢弃」的唯一修正（delta 本身 volatile，中断时的
已累积内容作为持久化状态保留）。

.. rubric:: 机制 vs 策略

本模块只提供机制：媒体 base64 统一编码（**消息层不做文件大小检查**——前端校验 /
``before_enqueue`` 钩子 / adapter 的 ``ContextLengthError`` 分层兜底）、优先级排序队列、
五 op 手术原语。**策略**（防饿死加权、drain 合并、何时压缩上下文、摘要如何生成）
由覆写 ``Agent._dequeue()``、Composable 或应用层决定。

.. seealso::
   :class:`flowing.agent.Agent`
       消息队列与工作循环的宿主；``Agent.chain`` 是 :class:`MessageChain` 的访问入口。
   :class:`flowing.agent.TurnContext`
       逻辑 turn 的执行期临时对象（``started_at`` / ``message_ids`` / ``aborted`` /
       ``pending_messages`` / ``usages``）。
   :mod:`flowing.tool`
       ``ToolCall`` / ``ToolResult`` 的定义处（与 block 形式互转）。
   :mod:`flowing.context`
       ``Context`` / ``PromptBlock`` / ``PromptSegment``——上下文组装产物。
"""

from __future__ import annotations   # S-43 裁决③：注解延迟求值（message→model 为注解级边）

import enum
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from flowing.providers import Usage   # 注解级引用（message→providers 为注解级边，无环）

__all__ = [
    "MessageKind",
    "MessagePriority",
    "ContentBlock",
    "TextBlock",
    "ThinkingBlock",
    "ToolCallBlock",
    "StructBlock",
    "MediaBlock",
    "ImageBlock",
    "VideoBlock",
    "AudioBlock",
    "FileBlock",
    "Message",
    "MessageQueue",
    "MessageChain",
    "MEDIA_TOKEN_ESTIMATE",
    "estimate_message_tokens",
]


class MessageKind(enum.Enum):
    """消息来源枚举：对象层**唯一的角色判别**（八值，跨版本稳定契约）.

    .. rubric:: 功能介绍

    判别「这条消息**来自谁**」。``Message`` **没有 ``role`` 属性**——Provider adapter
    负责把 ``kind`` 映射为各 API 的 role（Anthropic ``assistant``、OpenAI ``assistant``、
    Gemini ``model`` 等），UI 通过 ``kind`` + ``tags`` 自行决定渲染方式。

    .. rubric:: 设计动机

    命名原则：所有 kind 描述「**来自谁**」而非「扮演什么角色」。旧名 ``ASSISTANT``
    更名为 ``PROVIDER`` 的三个理由：它是 API 的历史命名、在 Flowing 自己的消息模型中
    不自然；它破坏命名一致性（其余 kind 都描述来源）；它隐含「纯文本 LLM」假设，
    无法覆盖文生图 / 语音 / 视频等多模态 Provider。``PLUGIN`` / ``SUBAGENT`` 的存在
    是因为 Skill 渲染结果与子 Agent 返回结果**永远是独立消息**——不能合并进
    tool_result 消息（尽管 LLM 经 ``skill-load`` / ``subagent-invoke`` 工具调用语法触发；
    工具结果摊平后 TOOL 消息只承载纯内容块，更无包装块可合并）。

    .. rubric:: 使用示例

    .. code-block:: python

        msg = Message(kind=MessageKind.USER, content=[TextBlock(text="你好")])
        event = Message(
            kind=MessageKind.EVENT,               # 异步工具最终结果
            source="tool_result",
            content=[TextBlock(text="<标注>"), StructBlock(data={...})],   # 标注块 + 结果块
        )

    .. rubric:: 行为规约

    期待行为：

    - 枚举值跨版本稳定；序列化（``tree.jsonl`` 行）使用成员的字符串值
      （``"user"`` / ``"provider"`` / ……）。
    - 各 kind 的典型 ``content`` 组合：

      ============ ============================================================
      kind         content 中 block type 的典型排列
      ============ ============================================================
      ``USER``     ``[text]`` / ``[image]`` / ``[text, image]`` / ``[text, file]``
      ``PROVIDER`` ``[thinking, text]`` / ``[thinking, tool_call]`` / 交错组合
      ``TOOL``     ``[text]`` / ``[struct]`` / ``[struct, image]``（纯内容块，无协议块）
      ``SYSTEM``   ``[text]``
      ``PEER``     ``[text]`` / ``[text, file]``
      ``EVENT``    ``[text]`` / ``[text, image]`` / ``[text, struct]``（异步最终结果：标注块 + 结果块）
      ============ ============================================================

    非行为：

    - 上表是**典型情况**而非硬约束；框架核心不验证「某 kind 可否含某 block type」，
      合法性验证在 Provider adapter 组装 API 请求时进行。
    - ``kind`` 不携带 UI 渲染信息（折叠 / 颜色 / 字体是应用层概念）。
    - 框架不枚举 ``source``（二级分类自由字符串，由投递方填写）。

    边缘情况：

    - ``PEER`` vs ``EVENT``：PEER = 另一个 **Agent 实例**有意图地主动发送（语义上
      更接近用户消息，``source`` 典型值 ``"message_to"`` / ``"agent_delegate"`` /
      ``"agent_steer"``）；EVENT = 非 Agent 实体触发的「某事发生了」（辅助信息，
      ``source`` 典型值 ``"scheduled_task"`` / ``"plugin_event"`` / ``"tool_result"``）。
      语义区分让钩子（如 ``before_turn``）可以按触发来源做精准决策。
    - ``TOOL`` kind 不入队：同步工具结果在逻辑 turn 内经 ``_append_message``
      直接挂树（``kind=TOOL``，携带 ``tool_call_id`` / ``tool_status``，不经过队列）；
      异步最终结果以 ``EVENT`` kind 入队（标注块 + 结果块，不参与配对）。

    .. rubric:: 测试案例

    - 前置：无 → 操作：``len(MessageKind)`` → 期望：8，且存在
      ``USER / PROVIDER / TOOL / SYSTEM / PEER / EVENT / PLUGIN / SUBAGENT``。
    - 前置：无 → 操作：``MessageKind.PROVIDER.value`` → 期望：``"provider"``。
    - 前置：无 → 操作：``hasattr(MessageKind, "ASSISTANT")`` → 期望：``False``。

    .. rubric:: 调用关系（审计）

    - 调用：无（枚举，纯判别值）
    - 被调：``flowing.agent.Agent.enqueue_message``（时机：每次入队，
      入队 kind 判定）；``flowing.model`` Provider adapter（时机：每次
      组装 API 请求，kind → role 映射）；``flowing.plugins.skills`` /
      ``flowing.plugins.cron`` / ``flowing.plugins.comm`` /
      ``flowing.plugins.workflow``（时机：各插件每次投递时选定 kind）
    - 实例化方：不适用（枚举，无实例化方）

    .. seealso::
       :class:`flowing.message.Message`、:class:`flowing.message.MessageQueue`、
       :mod:`flowing.model`（Provider adapter 的映射职责）。
    """

    USER = "user"
    """用户输入。进队列；LLM 视为对话中的用户消息。
    """
    PROVIDER = "provider"
    """LLM / 多模态模型 / 任意 Provider 的响应。**永远在逻辑 turn 内产生，不进队列**；
    ``turn_end`` 边界标记只对本 kind 有语义。
    """
    TOOL = "tool"
    """工具执行结果（摊平形态：``content`` 只装纯内容块，配对元数据在消息级
    ``tool_call_id`` / ``tool_status`` 字段，``__post_init__`` 双向强制）。
    同步结果在 turn 内直接挂树；异步最终结果以 ``EVENT`` kind 入队。
    """
    SYSTEM = "system"
    """框架 / Composable 的上下文注入。双通道：经队列（触发新 turn）或
    ``_assemble_context()`` 内部注入（不触发新 turn）。
    """
    PEER = "peer"
    """来自另一个 Agent 实例的有意图消息。
    """
    EVENT = "event"
    """外部事件（Cron、事件插件、Composable 提醒注入、异步工具最终结果）；
    通常附带 ``source`` 说明来路（如 ``"system-reminder"``）。
    异步最终结果为多块 content（标注块 + 结果块），不参与配对。
    """
    PLUGIN = "plugin"
    """扩展产生的内容（Skill 渲染结果等）；永远以独立消息入队，不合并进
    tool_result 消息（摊平后工具结果无包装块，见 :class:`MessageKind` 设计动机）。
    """
    SUBAGENT = "subagent"
    """子 Agent 返回结果；永远以独立消息入队，与普通工具结果明确分离。
    """


class MessagePriority(enum.IntEnum):
    """消息优先级：:class:`MessageQueue` 的排序依据（数值越小越优先）.

    .. rubric:: 功能介绍

    五级优先级，决定工作循环 ``dequeue`` 的消费顺序与 ``_run_turn`` 的
    urgent 吸收行为：``INTERRUPT (0) > STEER (1) > HIGH (2) > NORMAL (3)
    > LOW (4)``。

    .. rubric:: 设计动机

    用 ``IntEnum`` 且让「更优先 = 更小数值」，使排序实现就是普通的数值升序 +
    入队序号（FIFO tie-break），无需自定义比较器。``INTERRUPT`` 用于打断当前
    回合的插队场景，``STEER`` 仅弱于它——回合内吸收、当轮 provider_gen 的 context
    即可见但不打断（吸收语义见下）；``HIGH`` 的典型场景是人工催办、外部
    高优告警等紧急但不打断当前回合的通知。

    .. rubric:: 使用示例

    .. code-block:: python

        # 亲 Agent steer 子 Agent：目录已改动，请重新读取——
        # 当轮 context 可见、不打断（ steer() 糖即此形态：
        # await child_agent.steer("目录 src/ 已改动，请重新读取后再继续") ）
        steer = Message(
            kind=MessageKind.PEER,
            content=[TextBlock(text="目录 src/ 已改动，请重新读取后再继续")],
            source="agent_steer",
            priority=MessagePriority.STEER,
        )
        await child_agent.enqueue_message(steer)

    .. rubric:: 行为规约

    - 排序语义：按枚举数值升序；同优先级按入队顺序（FIFO）。排序发生在
      :class:`MessageQueue` 内部，``Message`` 本身不可比较。
    - ``INTERRUPT`` / ``STEER`` 构成「urgent 带」，其吸收语义由
      ``flowing.agent.Agent._run_turn`` 检查点 ②.5 实现：回合进行中 peek 到
      ``INTERRUPT`` 则连 drain 两带、挂树并 abort 本回合；仅有 ``STEER``
      则只 drain 本带、挂树继续当轮 provider_gen。普通 ``dequeue`` 路径只认排序，
      不区分 urgent 带的吸收语义。
    - 非行为：优先级不防饿死、不按来源加权——具体调度策略由队列内部实现
      或覆写 ``Agent._dequeue()`` 决定，不在框架核心。
    - 边缘情况：副线消息不入队（副线走 ``Agent.side_query``，不经队列），
      ``priority`` 对其无意义；``PROVIDER``
      消息不入队，``priority`` 附着但无调度效果。
    - 落盘耦合：枚举数值即消息的落盘值（恢复时按附着值重建），调整枚举
      取值即变更持久化格式。

    .. rubric:: 测试案例

    - 前置：队列含 NORMAL 消息 A（先入）、HIGH 消息 B、NORMAL 消息 C（后入）
      → 操作：连续 ``dequeue`` → 期望：顺序为 B、A、C。
    - 前置：回合进行中，``INTERRUPT`` 消息入队 → 期望：当前回合 abort、
      该消息挂树，下一回合 context 可见；改入 ``STEER`` 消息 → 期望：
      回合不 abort，当轮 provider_gen 的 context 即可见。

    .. rubric:: 调用关系（审计）

    - 调用：无（IntEnum，纯排序值）
    - 被调：``flowing.message.MessageQueue.enqueue``（时机：每次入队，按
      数值升序定位）；``flowing.message.MessageQueue.set_priority``
      （时机：每次重设未出队消息优先级，按新值重新定位）；
      ``flowing.agent.Agent.enqueue_message``（时机：
      每次入队透传）；``flowing.agent.Agent._run_turn``（时机：检查点
      ②.5 urgent 吸收，``peek`` / ``take_while`` 按带过滤）
    - 实例化方：不适用（枚举，无实例化方）

    .. seealso::
       :class:`flowing.message.MessageQueue`、:meth:`flowing.agent.Agent.enqueue_message`。
    """

    INTERRUPT = 0
    """打断：最优先消费；回合进行中被吸收并 abort 当前回合（检查点 ②.5）。
    """
    STEER = 1
    """引导：仅弱于 INTERRUPT；回合进行中被吸收、当轮 context 可见但不打断。
    """
    HIGH = 2
    """紧急性事件（如人工催办、外部高优告警）——紧急但不打断当前回合。
    """
    NORMAL = 3
    """默认优先级。
    """
    LOW = 4
    """低优先级。
    """


@dataclass
class ContentBlock:
    """消息内容片段基类：``type`` 字段判别「这一段是什么」（八种 type）.

    .. rubric:: 功能介绍

    一条 :class:`Message` 的 ``content`` 数组可含多种 type 的 ContentBlock，
    **交错排列**（Anthropic 原生支持；OpenAI / Gemini 由 adapter 做映射转换，
    如 OpenAI 的 ``tool_calls[]`` 逐项转回 block 以保持交错顺序）。

    .. rubric:: 设计动机

    block 化的消息内容使多模态（文本 + 图像 + 文件混排）、思考过程（thinking）、
    工具调用（tool_call）在同一个消息模型内统一表达，与具体 Provider
    API 的内容结构解耦。媒体块**一律 base64 内联**（``data`` 必填且是权威表示）：
    统一性（文件路径 / URL / 剪贴板进入消息层后是同一格式）+ Provider 适配
    （adapter 从统一 base64 出发做各 API 转换，无需知道原始来源）。
    旧的「``data=None`` 时 ``name`` 作为路径引用」模式**已废弃，不存在**。

    .. rubric:: 行为规约

    - 框架核心只接纳 base64 数据，**不做文件大小检查**，不引入分块 / 流式读取 /
      引用传递机制。大小问题分层处理：前端上传前校验 → ``before_enqueue`` 钩子
      reject → Provider adapter 的 ``ContextLengthError`` 兜底。
    - ``mime_type`` 始终可选：adapter 缺失时按文件扩展名或内容魔数推断；显式指定优先。
    - 降级策略属 UI / Provider 层（非框架核心）：video → 首帧截图或文件引用；
      audio → 占位文本或 STT 转文字。
    - 边界区分：``text`` vs ``file``（可读字符串 vs 二进制）；``image`` vs ``video``
      （单帧 vs 连续帧）；``image`` vs ``file``（多模态视觉输入 vs 仅作文件传递）；
      ``audio`` vs ``file``（需语音理解 vs 仅传 mp3）；``struct`` vs ``text``
      （程序可读的 JSON 结构 vs 纯文本——对 LLM 的投影同为文本）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.model`` Provider adapter（时机：每次组装 API
      请求，逐 ``type`` 映射）；``flowing.message.Message``
      （``content`` 字段的元素类型）
    - 实例化方：不直接实例化（基类；实例化见各子类）

    .. seealso::
       :class:`flowing.message.Message`、:mod:`flowing.model`（adapter 逐 type 映射）。
    """

    type: str
    """片段类型判别，八值之一：``"text" | "thinking" | "tool_call" | "struct" |
    "image" | "video" | "audio" | "file"``。子类将其收窄为对应的 ``Literal``。
    序列化时作为 ``tree.jsonl`` 行内 content 项的判别字段。
    """


@dataclass
class TextBlock(ContentBlock):
    """纯文本内容块.

    .. rubric:: 功能介绍

    直接可读的字符串片段；``message("...")`` 的 str 入参即自动打包为
    ``[TextBlock(text=content)]``。最典型的 block type，可能出现在任何 kind 中。

    .. rubric:: 设计动机

    文本是对话的主载体；单独成块（而非 Message 上的字段）使文本可与其他类型
    block 交错混排（如 ``[thinking, text, tool_call, text]``）。

    .. rubric:: 使用示例

    .. code-block:: python

        msg = Message(kind=MessageKind.USER, content=[TextBlock(text="帮我查订单")])

    .. rubric:: 行为规约

    - ``text`` 为空字符串是合法的（语义由上层决定，框架不拒绝）。
    - 非行为：不做长度限制、不做内容审核（审核走 ``before_enqueue`` 钩子）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.side_query``（时机：每次副线响应收尾，
      拼接 ``TextBlock.text``）
    - 实例化方：``flowing.agent.Agent.message``（时机：每次调用，str
      入参自动打包）；``flowing.plugins.skills``（时机：每次 Skill
      渲染投递）；``flowing.plugins.cron``（时机：每次 job 触发投递）；
      ``flowing.plugins.comm``（时机：每次收信转入对话）；
      ``flowing.plugins.workflow``（时机：轮次通知投递）；Provider
      adapter（时机：每次解析响应文本 block）

    .. seealso::
       :class:`flowing.message.ContentBlock`、:meth:`flowing.agent.Agent.message`。
    """

    type: Literal["text"]
    """固定为 ``"text"``。
    """
    text: str
    """文本内容。直接可读；序列化时原样进入 ``tree.jsonl`` 行。
    """


@dataclass
class ThinkingBlock(ContentBlock):
    """LLM 思考 / 推理过程内容块.

    .. rubric:: 功能介绍

    承载 Provider 返回的推理过程（Anthropic ``thinking`` block、OpenAI
    ``reasoning_content`` 等）；典型出现在 ``PROVIDER`` 消息中，UI 通常折叠或淡化显示。

    .. rubric:: 设计动机

    思考过程是 Provider 响应的一等部分：持久化后可审计、可在后续上下文中回传
    （Anthropic 要求 thinking 块原样回传并校验签名）。``signature`` 字段即为此保留。

    .. rubric:: 行为规约

    - ``signature``：Anthropic 的签名（校验思考内容未被篡改）；不适用此机制的
      Provider 为 ``None``。
    - 非行为：UI 的折叠 / 淡化是应用层策略，框架不预设渲染方式。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.side_query``（时机：每次副线响应消费，
      一律丢弃）；``flowing.providers.ProviderDelta`` 流式路径（时机：流式
      响应期间按 ``content_index`` 归位累积）
    - 实例化方：Provider adapter（时机：每次响应含 thinking block 时；
      逐字构造点未见规约）

    .. seealso::
       :class:`flowing.message.ContentBlock`、:class:`flowing.providers.ProviderDelta`。
    """

    type: Literal["thinking"]
    """固定为 ``"thinking"``。
    """
    thinking: str
    """思考过程文本。
    """
    signature: str | None = None
    """Provider 签发的完整性签名（如 Anthropic）；无签名机制时为 ``None``。
    """


@dataclass
class ToolCallBlock(ContentBlock):
    """工具调用请求内容块（LLM 侧）.

    .. rubric:: 功能介绍

    PROVIDER 消息中的一个工具调用请求。``id`` 由 Provider 分配（如 Anthropic
    ``tool_use.id``、OpenAI ``tool_calls[].id``），作为配对锚点：与同分支后续
    TOOL 消息的 :attr:`Message.tool_call_id` 严格配对（1:1）。

    .. rubric:: 设计动机

    block 形式是**消息层的权威表示**（持久化、上下文组装都用它）；
    :class:`flowing.tool.ToolCall` 是它的「解析后」形式——剥离通用字段，只保留
    ``id`` / ``name`` / ``args``（+ 通用短路字段 ``shortcut``），供 ``Agent.tool_call()``
    与工具钩子使用。两者经 :meth:`flowing.tool.ToolCall.from_block` 单向转换
    （**转换点定在 ToolCall 侧**，S-43 裁决④：message 不再 import tool，
    「tool 认识 message、message 不认识 tool」，模块依赖保持单向）。

    .. rubric:: 使用示例

    .. code-block:: python

        for block in response.message.content:
            if block.type == "tool_call":
                result = await agent.tool_call(ToolCall.from_block(block))

    .. rubric:: 行为规约

    - 恢复不变量：tool_call block 必须有对应的 TOOL 结果消息（同分支、严格成对，
      锚点为本 block 的 ``id`` ↔ 结果消息的 ``Message.tool_call_id``）；
      缺失时恢复流程合成 ``synthetic=True`` 的占位 TOOL 消息（见模块级
      docstring「恢复三条规则」规则三）。
    - ``args`` 是 LLM 填写的原始参数字典；参数合并优先级（**inject >
      specified > LLM args > 默认值**——inject 最高，M-54 安全不变量，
      C-07 裁决回正）与 ``inject`` 防篡改通道在 ``flowing.tool.ToolEntry``
      层处理，block 本身只是载体。

    .. rubric:: 调用关系（审计）

    - 被调：逻辑 Turn 循环经 :meth:`flowing.tool.ToolCall.from_block` 转换后交
      ``flowing.agent.Agent.tool_call``（时机：响应含 tool_call block
      时，逐字调用点见 ``flowing.agent.Agent._run_turn`` 工具调用循环）
    - 实例化方：Provider adapter（时机：每次响应含 tool_call 时；逐字
      构造点未见规约）

    .. seealso::
       :class:`flowing.tool.ToolCall`、:attr:`flowing.message.Message.tool_call_id`、
       :meth:`flowing.agent.Agent.tool_call`。
    """

    type: Literal["tool_call"]
    """固定为 ``"tool_call"``。
    """
    id: str
    """工具调用 ID（Provider 分配）；与后续 TOOL 消息的
    :attr:`Message.tool_call_id` 配对（1:1）。
    """
    name: str
    """工具名（LLM 可见的声明名，即 ``ToolEntry`` 的别名）。
    """
    args: dict[str, Any]
    """LLM 填写的参数字典（原始、未合并）。
    """


@dataclass
class StructBlock(ContentBlock):
    """结构数据内容块：对程序是结构、对 LLM 是 dumps 文本（D18）.

    .. rubric:: 功能介绍

    承载工具结果 / 框架内部产生的 JSON 兼容结构数据（dict / list / dataclass /
    BaseModel 等的序列化形态）。对程序是结构（``block.data`` 直读，无需
    ``json.loads`` 回解）；对 LLM 是文本——adapter 恒投影为
    ``json.dumps(data, ensure_ascii=False)``，LLM 可见面与纯文本方案逐字节相同。

    .. rubric:: 设计动机

    工具结果摊平（删除 ``ToolResultBlock``，配对元数据上移到消息级
    ``tool_call_id`` / ``tool_status``）后，混合结果序列里的结构数据若就地
    textify 成 ``TextBlock``，机器可读形态只剩 JSON 文本；``StructBlock``
    让结构随行而不丢程序可读性。分工口诀：「文本与标量装 ``TextBlock``
    （str 原样、标量 dumps 成 JSON 拼写可解析回），复合结构装 ``StructBlock``，
    媒体装媒体块」。

    .. rubric:: 行为规约

    - ``data`` 恒 JSON 兼容（``json.dumps`` 可序列化）；构造时校验，违反 →
      ``ValueError``（框架错误通道，作者 bug——如深层埋藏的非 JSON 对象在
      塑形时于此诚实失败）。
    - adapter 不对 ``StructBlock`` 做任何原生结构化映射，恒投影为
      ``json.dumps(ensure_ascii=False)`` 文本（所有 adapter 统一）。
    - 作者不直接构造本块：它由归一化 / 塑形（``flowing.tool.normalize_output`` /
      ``flowing.tool.output_to_blocks``）与框架内部产生；工具 ``execute``
      签名中禁止出现 Block 类。
    - token 估算按 ``json.dumps`` 长度计（见 :func:`estimate_message_tokens`）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.model`` Provider adapter（时机：每次组装 API
      请求，恒投影为 dumps 文本）；``flowing.message.estimate_message_tokens``
      （时机：每次估算，按 dumps 长度计）
    - 实例化方：``flowing.tool.output_to_blocks``（时机：每次塑形工具结果，
      dict / dataclass / BaseModel / 纯基础 list / tuple → 本块）；
      其余框架内部产出处未见规约

    .. seealso::
       :class:`flowing.message.ContentBlock`、:class:`flowing.message.TextBlock`、
       :class:`flowing.tool.ToolResult`。
    """

    type: Literal["struct"]
    """固定为 ``"struct"``。
    """
    data: Any
    """JSON 兼容结构数据（构造校验，否则 ``ValueError``）；程序方直读，
    adapter 恒投影为 ``json.dumps(ensure_ascii=False)`` 文本。
    """

    def __post_init__(self) -> None:
        # 结构示意：校验 data 为 JSON 兼容（json.dumps 可序列化），违反 → ValueError。
        ...


@dataclass
class MediaBlock(ContentBlock):
    """媒体内容块基类：``data`` base64 **必填**且为权威表示.

    .. rubric:: 功能介绍

    ``image`` / ``video`` / ``audio`` / ``file`` 四种媒体块的共同字段基座。
    无论原始来源是文件路径、URL 还是内存 buffer，进入消息层时**统一转换为 base64**，
    ``data`` 持有权威表示；``name``（必填，缺省合成）/ ``mime_type`` 仅是元数据，
    不替代 ``data``。

    .. rubric:: 设计动机

    - 统一性：来源无关的单一格式，消息层不感知「文件最初从哪来」。
    - Provider 适配：各 API 接收方式不同（base64 内联 / URL / multipart），
      adapter 从统一 base64 出发转换。
    - 旧的 ``data=None`` 路径引用模式**已废弃**（安全与一致性理由：消息层不应
      隐式读盘；``data`` 必填使消息对象自包含、可序列化、可持久化）。

    .. rubric:: 使用示例

    .. code-block:: python

        import base64

        data = base64.b64encode(open("chart.png", "rb").read()).decode()
        msg = Message(
            kind=MessageKind.USER,
            content=[
                TextBlock(text="这张图里有什么异常？"),
                ImageBlock(type="image", data=data, name="chart.png",
                           mime_type="image/png"),
            ],
        )

    .. rubric:: 行为规约

    - ``data`` 必填：构造时不校验 base64 合法性（机制从简——非法数据由 adapter
      或 Provider 报错），但缺失 ``data`` 是契约违反。
    - ``name`` 必填（恒非 ``None``）：填充链 = 显式 ``name`` > ``path`` 文件名 >
      合成 ``<sha256(data)[:12]>.<ext>``（ext 由 MIME 反推，MIME 未知 → ``.bin``）。
      规则**全局统一**：不只工具结果转换层（``flowing.tool.normalize_output``），
      adapter 从 provider 响应 / 用户上传建媒体块时同守。
    - **消息层不做文件大小检查**：超大 base64 字符串直接存储；大小治理分层
      （前端校验 / ``before_enqueue`` 钩子 / adapter ``ContextLengthError``）。
    - ``mime_type`` 可选：缺失时 adapter 按扩展名或内容魔数推断；显式指定优先。
    - 非行为：不做病毒扫描、不做格式转码、不做缩略图生成。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.model`` Provider adapter（时机：每次组装多模态
      请求，从统一 base64 出发转换）
    - 实例化方：不直接实例化（基类；实例化见各子类）

    .. seealso::
       :class:`flowing.message.ImageBlock`、:class:`flowing.message.FileBlock`、
       :class:`flowing.message.ContentBlock`。
    """

    data: str
    """媒体内容的 base64 编码，**必填**，权威表示。路径引用模式不存在。
    """
    name: str
    """文件名等元数据，**必填**（恒非 ``None``）；不替代 ``data``。缺省时由转换 /
    构造层按填充链合成：显式 ``name`` > ``path`` 文件名 >
    ``<sha256(data)[:12]>.<ext>``（MIME 未知 → ``.bin``）。
    """
    mime_type: str | None = None
    """MIME 类型（可选）；缺失时 adapter 推断，显式指定优先。
    """


@dataclass
class ImageBlock(MediaBlock):
    """图像内容块（单帧静态，多模态视觉输入）.

    .. rubric:: 功能介绍

    用户上传图片且**希望多模态视觉理解**时使用；仅作文件传递（不做图像识别）
    时用 :class:`FileBlock`。映射：Anthropic ``image`` block、OpenAI ``image_url``
    content part。

    .. rubric:: 行为规约

    - 字段与 :class:`MediaBlock` 一致；``type`` 固定 ``"image"``。
    - 非行为：框架不校验数据确实是图像（adapter / Provider 负责）。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.model`` Provider adapter（时机：每次组装 API
      请求，映射为 Anthropic ``image`` / OpenAI ``image_url``——本类
      docstring 自述）
    - 实例化方：用户代码（公共 API）；框架内未见逐字构造点（时机：
      未见规约）

    .. seealso::
       :class:`flowing.message.MediaBlock`、:class:`flowing.message.VideoBlock`。
    """

    type: Literal["image"]
    """固定为 ``"image"``。
    """


@dataclass
class VideoBlock(MediaBlock):
    """视频内容块（时间维度连续帧）.

    .. rubric:: 功能介绍

    视频输入。多数 Provider 不原生支持视频——**降级策略属 adapter / UI 层**
    （首帧截图、转为文件引用或略过），框架核心只做承载。

    .. rubric:: 行为规约

    - 字段与 :class:`MediaBlock` 一致；``type`` 固定 ``"video"``。
    - 与 :class:`ImageBlock` 的边界：单帧静态用 image；连续帧用 video。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.model`` Provider adapter（时机：每次组装 API
      请求；不支持视频的 Provider 由 adapter 降级——本类 docstring
      自述）
    - 实例化方：用户代码（公共 API）；框架内未见逐字构造点（时机：
      未见规约）

    .. seealso::
       :class:`flowing.message.MediaBlock`、:class:`flowing.message.ImageBlock`。
    """

    type: Literal["video"]
    """固定为 ``"video"``。
    """


@dataclass
class AudioBlock(MediaBlock):
    """音频内容块（需要语音理解时使用）.

    .. rubric:: 功能介绍

    语音 / 音频输入。不支持的 Provider 由 adapter 降级（``<audio>`` 占位文本或
    STT 转文字）；仅传递 ``.mp3`` 等文件而无需理解时用 :class:`FileBlock`。

    .. rubric:: 行为规约

    - 字段与 :class:`MediaBlock` 一致；``type`` 固定 ``"audio"``。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.model`` Provider adapter（时机：每次组装 API
      请求；不支持的 Provider 由 adapter 降级为占位文本或 STT——本类
      docstring 自述）
    - 实例化方：用户代码（公共 API）；框架内未见逐字构造点（时机：
      未见规约）

    .. seealso::
       :class:`flowing.message.MediaBlock`、:class:`flowing.message.FileBlock`。
    """

    type: Literal["audio"]
    """固定为 ``"audio"``。
    """


@dataclass
class FileBlock(MediaBlock):
    """文件内容块（不直接可读的二进制或大型结构化数据）.

    .. rubric:: 功能介绍

    与 tool_call 无绑定关系的文件载体——一条 USER 消息可同时含 ``text`` 与多个
    ``file`` block。与媒体块的区别在**意图**：用户上传图片但只希望作为文件处理
    （不做图像识别）时用 ``file``；tool 结果附带的产物文件亦可直接出现于
    TOOL 消息 content（摊平形态，如 ``[struct, file]``）。

    .. rubric:: 行为规约

    - ``name`` **必填**（文件名是文件块的核心元数据）；``data`` base64 必填
      （路径引用模式已废弃）；``mime_type`` 可选。
    - adapter 映射示例：Anthropic 转文本引用或 ``document`` API；OpenAI 经
      ``file`` attachment / ``data`` content part。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.model`` Provider adapter（时机：每次组装 API
      请求，映射示例见本类 docstring）
    - 实例化方：用户代码 / 工具结果附带产物（TOOL 消息 content 直接含
      ``file`` 块，如 ``[struct, file]``，时机：工具产出文件时——本类
      docstring 自述；框架内逐字构造点未见规约）

    .. seealso::
       :class:`flowing.message.MediaBlock`、:class:`flowing.message.StructBlock`。
    """

    type: Literal["file"]
    """固定为 ``"file"``。
    """


@dataclass
class Message:
    """对象层消息：所有来源信息的统一表示，**消息级树的节点**.

    .. rubric:: 功能介绍

    任意来源（用户、LLM、工具、外部 Agent、系统事件、扩展）产生的新信息统一表示
    为 ``Message``。它同时是消息级树的**节点**——``id`` 是节点 id，``parent_id``
    是父链，``Agent.current_head_id`` 指向某条消息的 id，上下文组装沿 ``parent_id``
    上溯收集路径。

    .. rubric:: 设计动机

    - **无 ``role`` 属性**：kind 替代 API 层 role，与 Provider 解耦（见
      :class:`MessageKind` 设计动机）。
    - **消息级树**（取代旧 Turn 粒度树）：goal 模式下一条指令可连续工作数天，
      Turn 粒度下树只有一个巨大节点、数天不落盘、中途不能 fork——消息级让
      fork / 压缩 / 恢复都落在任意消息上。代价是放弃「当前 turn
      内存缓冲区」这个免费手术区，历史手术需要 :class:`MessageChain` + tombstone
      支撑。
    - **字段复用**：``id`` 直接用作节点 id；``turn_end`` 恰好就是「完整逻辑 turn
      结束」的边界标记；新增仅 ``parent_id`` / ``partial`` 两个字段。

    .. rubric:: 使用示例

    .. code-block:: python

        msg = Message(
            kind=MessageKind.USER,
            content=[TextBlock(text="帮我查订单 ORD-12345")],
            source="chat_input",
            tags=["order"],
        )
        message_id = await agent.enqueue_message(msg)   # 返回 msg.id

    .. rubric:: 行为规约

    期待行为：

    - ``id`` 默认由框架分配（UUID）；``enqueue_message`` 返回 ``msg.id``，
      ``Agent.query()`` 的等待与该 id 绑定（``_pending_turns`` 出队绑定）。
    - ``parent_id`` 由 ``Agent._append_message`` 在挂树时设置（首条消息链到
      ``current_head_id``，后续链到上一条）；手动构造的消息入队前通常为 ``None``。
    - ``turn_end``：**agent 层概念**（turn 是 agent 层概念），由
      ``_run_turn`` 在挂树时写入：本条 PROVIDER 消息落盘时 turn 随之关闭
      （自然 ``finish`` 或取消/abort）→ ``True``；与 provider 层的
      ``ProviderResponse.finish`` 分层——中断的流式没有 finish，但 turn
      照样关闭（S-14 裁决）。恢复时「最后一个 ``turn_end=True`` 的
      PROVIDER 消息」之后的已落盘消息 = 半截 turn（崩溃撕裂或异常终结；
      保留不续跑，但按 M-28 裁决**照常进入 LLM 上下文**，不截断）。
    - ``partial``：流式中断时置 ``True``，已累积内容**保留落盘**（对应中断保留
      行为）；正常完成的消息恒为 ``False``。
    - ``synthetic``：仅恢复流程合成的占位消息为 ``True``（孤立 tool_call 的
      占位 TOOL 消息），标记「不是真实结果」；其余消息恒为 ``False``。
    - ``tool_call_id`` / ``tool_status``：**仅 ``kind=TOOL`` 非 None**；
      ``__post_init__`` 双向强制（``kind=TOOL`` ⟺ 两字段非 ``None``，违反 →
      ``ValueError``）——把「孤儿结果」消灭在构造点，不等上下文组装才发现。
      既有 kind 条件字段（``turn_end`` / ``synthetic`` / ``priority``）维持
      文档约定，不追溯校验（追溯可能误伤存量构造路径）。
    - ``timestamp``：时区无关（UTC / epoch）——统一排序基准、延迟测量可比较、
      跨机器审计对齐、夏令时稳健；显示转换由 UI / 日志层负责；**是否进入发给
      LLM 的消息记录由 Provider adapter 决定**（多数 adapter 不带）。

    非行为：

    - 历史消息**不可变**于应用层直接修改——修改历史走 :class:`MessageChain`
      五 op（持久化手术）或 fork（切分支）。
    - 消息对象不携带执行状态 / 等待标记（``_pending_turns`` 是纯运行时结构，
      future 不可序列化，不落盘）。
    - 兄弟分支无顺序信息（互斥分支）；分支列表 UI 排序用 ``timestamp``，
      树结构不需要显式排序字段。

    不变量：

    - 树上任意消息的 ``parent_id`` 为 ``None``（根标记）或指向另一条已落盘消息；
      **允许多个根**（森林模型）——新根由 ``MessageChain.branch(None, msg)``
      开启（如压缩换链），旧根链完整保留、不再进入上下文。
    - 一个逻辑 turn = 一条消费消息开始，到一条 ``turn_end=True`` 的 PROVIDER
      消息结束（或被 abort）。
    - 配对锚（新锚点）：同一分支上，PROVIDER 消息 ``ToolCallBlock.id`` ↔
      后续 TOOL 消息的 ``tool_call_id``，1:1 严格成对；孤立调用由恢复合成的
      ``synthetic`` 占位 TOOL 消息封闭。
    - 副线（``side_query``）消息从不进树——「副线」是调用路径属性而非
      消息属性（``side`` 字段已删除）。

    .. rubric:: 测试案例

    - 前置：空树 Agent → 操作：``enqueue_message`` 一条 USER 消息后等待 turn
      完成 → 期望：树为 ``user(parent=None) → provider(parent=user.id,
      turn_end=True)``，``current_head_id == provider.id``。
    - 前置：流式 turn 执行中 → 操作：abort → 期望：已累积内容以
      ``partial=True`` 的 PROVIDER 消息落盘，``timestamp`` 为 UTC。
    - 前置：``side_query`` 完成 → 期望：消息树与 ``tree.jsonl`` 均无新行。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.enqueue_message``（时机：每次入队）；
      ``flowing.agent.Agent._append_message``（时机：每次挂树落盘，
      五步统一入口）；``flowing.message.MessageChain`` 五 op（时机：
      每次手术）；``flowing.context`` 组装沿 ``parent_id`` 上溯
      （时机：每次组装上下文）
    - 实例化方：``flowing.providers.Provider.generate`` 的 adapter 实现
      （时机：每次模型响应，产出 ``kind=PROVIDER`` 消息）；
      ``flowing.tool.ToolResult.as_message``（时机：逻辑 Turn 收尾，
      每次工具结果转 TOOL 消息，``tool_call_id`` 由 ``Agent.tool_call``
      管线接线，C-05 裁决）；``flowing.agent`` 异步完成回调（时机：
      fire-and-forget Task 完成，产出标注块 + 结果块的 EVENT 消息；
      固定行为，非扩展点）；``flowing.agent.Agent._restore``（时机：每次
      恢复遇孤立 tool_call，合成 ``synthetic=True`` 占位 TOOL 消息）；
      ``flowing.plugins.skills`` / ``flowing.plugins.cron`` /
      ``flowing.plugins.comm`` / ``flowing.plugins.workflow``（时机：
      各插件每次投递）；用户代码经 ``Agent.message`` /
      ``enqueue_message`` 投递

    .. seealso::
       :class:`flowing.message.MessageKind`、:class:`flowing.message.MessageChain`、
       :class:`flowing.message.MessageQueue`、:class:`flowing.agent.TurnContext`、
       :meth:`flowing.agent.Agent._append_message`。
    """

    kind: MessageKind
    """消息来源（对象层唯一角色判别），见 :class:`MessageKind`。**必填**。
    """
    content: list[ContentBlock]
    """内容片段列表（不同 type 交错排列），见 :class:`ContentBlock`。**必填**。
    TOOL 消息中只装纯内容块（text / struct / 媒体），无协议块。
    """
    tool_call_id: str | None = ...
    """配对锚（默认 ``None``）：**仅 ``kind=TOOL`` 非 None**，值为对应 PROVIDER
    消息 :attr:`ToolCallBlock.id`；由 ``Agent.tool_call`` 管线在
    ``ToolResult.as_message`` 时接线（C-05 裁决）。``__post_init__`` 双向强制。
    """
    tool_status: Literal["completed", "pending", "blocked", "error"] | None = ...
    """工具结果状态四值（默认 ``None``）：**仅 ``kind=TOOL`` 非 None**。
    用内联 ``Literal`` 而非 import ``flowing.tool.ToolStatus``——避免
    message↔tool 循环依赖（「tool 认识 message、message 不认识 tool」单向依赖）。
    ``__post_init__`` 双向强制。
    """
    id: str = ...
    """全局唯一消息 id（默认 UUID），同时是消息级树的节点 id；
    ``enqueue_message`` 的返回值与 ``_pending_turns`` 的绑定键。
    """
    parent_id: str | None = ...
    """消息级父链：``None`` = 根标记（森林模型，一棵树允许多个根——新根经
    ``MessageChain.branch(None, msg)`` 开启）；挂树时由 ``_append_message``
    设置（首条链到 ``current_head_id``，后续链到上一条）。
    fork 目标、上下文上溯、恢复重建的唯一依据。
    """
    turn_end: bool = ...
    """「逻辑 turn 关闭」边界标记（默认 ``False``）；仅 PROVIDER 消息上有语义。
    **agent 层写入**（``_run_turn`` 挂树时：turn 随本条消息关闭——自然
    ``finish`` 或取消/abort——→ ``True``），与 provider 层的
    ``ProviderResponse.finish`` 分层（S-14）。恢复时定位完整 turn 边界
    与 ``current_head_id`` 锚点的依据。
    """
    partial: bool = ...
    """流式中断标记（默认 ``False``）；``True`` 表示该消息内容不完整但保留落盘。
    """
    synthetic: bool = ...
    """合成占位标记（默认 ``False``）；仅恢复流程为孤立 tool_call 合成的
    占位 TOOL 消息（``tool_status="error"`` + ``TextBlock`` 占位说明）为 ``True``。
    """
    source: str = ...
    """二级分类自由字符串（默认 ``""``；框架不枚举，投递方填写），
    典型值 ``"tool_result"`` / ``"scheduled_task"`` / ``"message_to"``。
    """
    tags: list[str] = ...
    """任意标签列表（默认空），用于分组 / 过滤 / 清洗（如 reminder 清洗）。
    """
    priority: MessagePriority = ...
    """队列排序依据（默认 ``MessagePriority.NORMAL``）；仅对入队消息有调度效果。
    """
    timestamp: datetime = ...
    """产生时间戳（默认构造时的当前 UTC），**时区无关**；附着于消息供查询 /
    日志 / 审计 / 排序，是否进 LLM 上下文由 adapter 决定。
    """
    usage: Usage | None = ...
    """本消息对应的 provider 实测用量（默认 ``None``）。**仅 PROVIDER 消息
    携带**：adapter 构造响应消息时附着（见 ``flowing.providers.Provider``
    契约；``ProviderResponse`` **不携带** usage——本字段是唯一权威
    落点，单源化裁决），随消息落盘、恢复后仍在——它是
    :meth:`flowing.agent.Agent.estimate_context_tokens` 锚点机制的载体：
    挂在树节点上使锚点天然跟随 fork / 树手术 / 跨进程恢复，无需任何
    失效逻辑。非 PROVIDER 消息恒为 ``None``；框架核心不读它做计费决策
    （计费走 ``TurnResult.token_usage`` 聚合——其累加器持有的正是
    本字段对象的引用）。
    """

    def __post_init__(self) -> None:
        # 结构示意：双向强制——kind == MessageKind.TOOL ⟺ tool_call_id 与
        # tool_status 均非 None；违反 → ValueError（孤儿结果消灭在构造点）。
        # 既有 kind 条件字段（turn_end / synthetic / priority）不追溯校验。
        ...


MEDIA_TOKEN_ESTIMATE: int = 2000
"""单个媒体块的固定 token 估算值。消息层媒体一律 base64 内联，但其 token
成本由 provider 按图像/媒体规格定档，与 base64 长度无关——按字符数
估会失真几个数量级，故 :func:`estimate_message_tokens` 对
:class:`MediaBlock` 及其子类固定计此常数、不看内容（kimi-code 同值口径）。
"""


def estimate_message_tokens(msg: Message) -> int:
    """估算一条消息的 token 数（字符启发式，仅供上下文窗口预算观测）.

    .. rubric:: 功能介绍

    对消息逐块展开的本地估算：``TextBlock.text`` / ``ThinkingBlock.thinking``
    的文本、``ToolCallBlock`` 的 ``name`` + JSON 序列化参数、
    ``StructBlock`` 的 ``json.dumps`` 序列化长度均按字符启发式计；
    ``MediaBlock`` 及其子类固定计 :data:`MEDIA_TOKEN_ESTIMATE`。被
    :meth:`flowing.agent.Agent.estimate_context_tokens` 用于估算锚点之后
    （或无锚点时全部）路径消息。

    .. rubric:: 设计动机

    字符启发式取 kimi-code 口径：**ASCII ≈ 4 字符/token、非 ASCII
    （CJK 等）≈ 1 字符/token**——中文场景下远优于统一 ÷4（pi 口径）。
    不用 tokenizer：估算只服务于「该不该压缩、还剩多少余量」这类
    窗口预算判断（±20% 足够），不值得引入 tokenizer 依赖；**永不用于
    计费**（计费走 ``TurnResult.token_usage`` 聚合，数据源是
    ``Message.usage`` 实测）。

    .. rubric:: 使用示例

    .. code-block:: python

        estimate_message_tokens(msg)   # 纯函数，随时可调用

    .. rubric:: 行为规约

    - 同步纯函数：只读 ``msg``，无副作用、无缓存（「无本地缓存」原则，
      每次现场求值）。
    - 逐块规则：文本类块按（ASCII 字符数 ÷ 4 + 非 ASCII 字符数）向上
      取整；``ToolCallBlock`` 计 ``name`` 与 ``args`` JSON 序列化后的
      字符启发式之和；``StructBlock`` 计 ``json.dumps(ensure_ascii=False)``
      结果的字符启发式；``MediaBlock`` 族固定 ``MEDIA_TOKEN_ESTIMATE``，
      **不读 base64 ``data``**。
    - 不计入：消息元数据（``id`` / ``source`` / ``tags`` 等）不进 LLM
      上下文，不参与估算；kind 的角色开销为零头，不单独计。
    - 边缘情况：空 ``content`` → 0；未知块类型按「无文本内容」计 0。

    .. rubric:: 测试案例

    - 前置：消息含一个 100 字符纯中文 ``TextBlock`` → 期望：估值 ≈ 100
      （非 ASCII ×1），而非 25（统一 ÷4 口径的失真值）。
    - 前置：消息含一张 base64 长度 300k 的 ``ImageBlock`` → 期望：该块
      计 ``MEDIA_TOKEN_ESTIMATE``（2000），与 base64 长度无关。

    .. rubric:: 调用关系（审计）

    - 调用：无（字符启发式为内部机制，逐块分支未见规约）
    - 被调：``flowing.agent.Agent.estimate_context_tokens``（时机：每次
      现场估算，对锚点后 / 无锚点时路径上的每条消息各调一次）

    .. seealso::
       :data:`MEDIA_TOKEN_ESTIMATE`、:class:`Message`（``usage`` 字段——
       实测锚点载体）、:meth:`flowing.agent.Agent.estimate_context_tokens`。
    """
    ...


class MessageQueue:
    """Agent 的消息队列：优先级排序 + 同优先级 FIFO 的异步队列.

    .. rubric:: 功能介绍

    每个 Agent 一个独立队列（``Agent._message_queue``），是外部消息进入逻辑 turn
    循环的唯一通道。工作循环经 ``Agent._dequeue()`` 消费——核心默认**一条一条**
    （最安全），drain / 合并 / 按来源分组等**策略由覆写** ``_dequeue()`` 实现，
    本类提供 ``drain_all`` / ``take_while`` 等批量取出原语支撑覆写。

    .. rubric:: 设计动机

    - **排序即机制**：按 ``MessagePriority`` 数值升序、同级按入队序号 FIFO——
      队列只承诺这一最小调度语义；防饿死、来源加权等策略不在框架核心。
    - **纯入队统一**：忙时不拒绝（无「拒绝 + 入队」两路），活跃 turn 中入队的
      消息自然排队，turn 结束后被消费；消费保证由常驻工作循环提供（入队即会被
      消费，无需「入队触发」逻辑）。
    - **覆写管策略、钩子管观察**：``_dequeue()`` 覆写管「多条 / 策略」，
      ``before_dequeue`` / ``after_dequeue`` 钩子管「观察 / 变换」。

    .. rubric:: 使用示例

    .. code-block:: python

        # 覆写 _dequeue：drain——批量吸收所有等待消息
        def use_message_drain(agent):
            async def _drain_dequeue():
                return await agent._message_queue.drain_all()
            agent._dequeue = _drain_dequeue

        # 覆写 _dequeue：按来源合并
        def use_message_coalescing(agent):
            original = agent._dequeue
            async def _coalescing_dequeue():
                msgs = await original()
                extras = agent._message_queue.take_while(
                    lambda m: m.source == msgs[0].source
                )
                return msgs + extras
            agent._dequeue = _coalescing_dequeue

    .. rubric:: 行为规约

    - 排序：``priority`` 升序（``INTERRUPT`` 最先）；同优先级 FIFO（入队序号，
      单调递增，无需比较 ``timestamp``）。
    - **进队列的 kind**：``USER`` / ``EVENT`` / ``PEER`` / ``SYSTEM`` /
      ``PLUGIN`` / ``SUBAGENT``（+ 异步工具最终结果以 ``EVENT`` 入队）；
      ``PROVIDER`` 永不入队。本类**不校验** kind——投递纪律由调用方与
      ``Agent.enqueue_message`` 的钩子链负责。
    - 非行为：不防低优先级饿死、不持久化（队列待消费消息以框架核心
      裸名键在 ``state.jsonl`` 有最小集记录（单袋化后无命名空间），
      恢复语义见持久化规约；本类自身不落盘）。
    - 并发模型：单进程 asyncio；``enqueue`` 是无等待的同步操作（队列无界），
      ``dequeue`` / ``drain_all`` 是协程。

    .. rubric:: 调用关系（审计）

    - 调用：无（容器类；逐方法调用关系见各方法 rubric）
    - 被调：``flowing.agent.Agent.enqueue_message``（时机：每次入队，
      经 :meth:`enqueue`）；``flowing.agent.Agent._dequeue``（时机：常驻
      工作循环每轮消费，经 :meth:`dequeue`）；
      ``flowing.agent.Agent.cancel_queued``（时机：每次撤回，经
      :meth:`remove`）；``flowing.agent.Agent.set_queued_priority``
      （时机：每次重设未出队消息优先级，经 :meth:`set_priority`）
    - 实例化方：``flowing.agent.Agent``（每实例一份
      ``Agent._message_queue``；逐字构造点未见规约，时机：未见规约）

    .. seealso::
       :class:`flowing.message.MessagePriority`、
       :meth:`flowing.agent.Agent._dequeue`、:meth:`flowing.agent.Agent.enqueue_message`、
       :meth:`flowing.agent.Agent.cancel_queued`、
       :meth:`flowing.agent.Agent.set_queued_priority`。
    """

    def enqueue(self, msg: Message) -> None:
        """入队一条消息（无等待、无界）.

        .. rubric:: 功能介绍

        将 ``msg`` 按（``priority``, 入队序号）插入排序位置。``Agent.enqueue_message``
        在 ``before_enqueue`` 钩子之后调用本方法。

        .. rubric:: 行为规约

        - 同步、无阻塞、立即返回；入队即保证会被常驻工作循环消费。
        - 不修改 ``msg``（``id`` / ``timestamp`` 在构造时已就位）。
        - 非行为：不做容量限制、不做去重、不做内容审核（审核走钩子）。

        .. rubric:: 测试案例

        - 前置：空队列 → 操作：``enqueue`` NORMAL 消息 A、HIGH 消息 B → 操作：
          ``await dequeue()`` → 期望：先得 B。

        .. rubric:: 调用关系（审计）

        - 调用：无（排序插入为内部机制，逐字段路径未见规约）
        - 被调：``flowing.agent.Agent.enqueue_message``（时机：每次入队，
          时序 dispatch ``before_enqueue`` → ``enqueue`` → dispatch
          ``after_enqueue``）
        """
        ...

    async def wait_not_empty(self) -> None:
        """阻塞等待队列变为非空（不取消息）。

        .. rubric:: 功能介绍

        「等消息」与「取消息」的拆分原语之一（R-13 裁决）：``Agent._dequeue``
        的默认实现用它把 ``before_dequeue`` 的派发点移到「队列确实非空、
        即将出队」的时刻，消除空转期的无效钩子派发。enqueue 时唤醒等待者。

        .. rubric:: 行为规约

        - 队列非空时立即返回；为空时挂起至下一次入队。
        - 返回后队列可能再次被掏空（如 ``before_dequeue`` 钩子扔消息），
          本方法不做保证——重试循环由调用方（``_dequeue``）承担。

        .. seealso:: :meth:`dequeue`、:meth:`dequeue_nowait`
        """
        ...

    def dequeue_nowait(self) -> Message | None:
        """非阻塞取出优先级最高的一条；队列空返回 ``None``。

        与 :meth:`wait_not_empty` 配套（R-13 裁决）：``_dequeue`` 在
        ``before_dequeue`` 派发后用它取消息——钩子在此窗口把消息扔掉
        （``cancel_queued`` / ``remove``）时返回 ``None``，调用方重新
        等待并重新派发 ``before_dequeue``（每条真正出队的消息之前恰好
        一次 before 派发）。
        """
        ...

    async def dequeue(self) -> Message:
        """阻塞取出优先级最高的一条消息（工作循环核心默认路径）.

        .. rubric:: 功能介绍

        队列空时挂起等待，直到有消息入队；``Agent._dequeue()`` 核心默认实现即
        ``[await self._message_queue.dequeue()]``（一条一条）。

        .. rubric:: 行为规约

        - 前置条件：在事件循环中调用（工作循环 Task 内）。
        - 后置条件：返回的消息已从队列移除；其等待者绑定（``_pending_turns.pop``）
          由工作循环在出队时完成，不在本方法内。
        - 边缘情况：Agent ``destroy()`` 取消工作循环后，挂起的 ``dequeue`` 随 Task
          取消而结束，不返回、不泄漏。

        .. rubric:: 调用关系（审计）

        - 调用：无（阻塞等待为内部机制，未见规约）
        - 被调：``flowing.agent.Agent._dequeue`` 核心默认实现（时机：
          常驻工作循环每轮消费一条，时序 dispatch ``before_dequeue`` →
          ``dequeue`` → dispatch ``after_dequeue``）

        .. seealso::
           :meth:`drain_all`、:meth:`take_while`、:meth:`flowing.agent.Agent._dequeue`。
        """
        ...

    async def drain_all(self) -> list[Message]:
        """一次性取出当前所有排队消息（drain 覆写原语）.

        .. rubric:: 功能介绍

        按出队顺序（优先级 + FIFO）返回并移除**调用时刻**已在队列中的全部消息；
        不为等待新消息而阻塞（若队列为空返回空列表）。

        .. rubric:: 行为规约

        - 用于 ``_dequeue()`` 覆写实现「合并回合」：多条消息一次吸收，回合收尾时
          **每条的等待者共享同一 ``TurnResult`` 并被全部 resolve**（核心通用收尾
          天然兼容批量）。
        - 边缘情况：调用与「另一生产者正在 enqueue」交错时，只保证取出调用时刻的
          存量；新入队者留给下一次。

        .. rubric:: 测试案例

        - 前置：队列有 3 条消息（各有 ``message()`` 等待者）→ 操作：覆写
          ``_dequeue`` 为 ``drain_all`` 后触发回合 → 期望：一个回合消费 3 条，
          3 个等待者 resolve 到同一 ``TurnResult``。

        .. rubric:: 调用关系（审计）

        - 调用：无（批量取出为内部机制，未见规约）
        - 被调：无框架内逐字调用方（``_dequeue()`` 覆写原语；示例性
          用法见 ``flowing.agent.Agent._dequeue`` 与本类 docstring）

        .. seealso::
           :meth:`dequeue`、:meth:`flowing.agent.Agent._dequeue`。
        """
        ...

    def take_while(self, predicate: Callable[[Message], bool]) -> list[Message]:
        """从队首按出队顺序连续取出满足条件的消息（合并覆写原语）.

        .. rubric:: 功能介绍

        标准 take-while 语义：从队首开始，按出队顺序逐条检查，**连续**取出满足
        ``predicate`` 的消息，遇到首个不满足者停止；不阻塞。

        .. rubric:: 行为规约

        - 同步、立即返回（可能为空列表）。
        - 典型用途：``_dequeue()`` 覆写中的按来源 / 按窗口合并
          （``lambda m: m.source == first.source``）。
        - 边缘情况：队首消息即不满足 → 返回空列表，队列不变；队列被取空 →
          返回全部已取消息。

        .. rubric:: 调用关系（审计）

        - 调用：无（取出为内部机制，未见规约）
        - 被调：``flowing.agent.Agent._run_turn``（时机：检查点 ②.5
          urgent 吸收，按 urgent 带谓词连 drain）；``_dequeue()`` 覆写
          原语（示例性用法见 ``flowing.agent.Agent._dequeue`` 与本类
          docstring）

        .. seealso::
           :meth:`drain_all`、:meth:`flowing.agent.Agent._dequeue`。
        """
        ...

    def peek(self, priority: MessagePriority | None = None) -> Message | None:
        """窥看下一条将被出队的消息，**不移除**（观测原语）.

        .. rubric:: 功能介绍

        按出队顺序（优先级 + 同级 FIFO）返回队首消息；指定 ``priority`` 时
        返回该优先级带内 FIFO 队首（用于「只看某一带」的观察，如 UI 分列
        预览各优先级各一条）。队列为空、或指定优先级带内无消息时返回
        ``None``。

        .. rubric:: 设计动机

        ``dequeue`` 阻塞且移除、``drain_all`` / ``take_while`` 批量移除——
        队列此前没有「只看不动」的入口，UI 预览、``_dequeue()`` 覆写的
        前置决策（先看队首再决定取多少）只能先取后放（破坏 FIFO 序号）。
        ``peek`` 补齐观测面，与 :meth:`__len__` 同属观测原语。

        .. rubric:: 行为规约

        - 同步、不阻塞、立即返回；**不移除**消息，队列状态不变。
        - 返回的消息仍在队列中：后续 ``dequeue`` / ``set_priority`` /
          ``remove`` 照常作用于它；不得将返回值视为「已占有」。
        - 无同步语义：跨 Task 观察时是瞬时值，不得据此做互斥决策
          （同 :meth:`__len__` 的观测约定）。
        - ``priority`` 过滤只选带内队首，不跨带比较；``None`` 表示全局队首
          （即下一次 ``dequeue`` 会取到的那条）。

        .. rubric:: 测试案例

        - 前置：队列含 NORMAL 消息 A（先入）、HIGH 消息 B（后入）→ 操作：
          ``peek()`` → 期望：返回 B，``len(queue)`` 不变；``await dequeue()``
          仍得 B。
        - 前置：同上 → 操作：``peek(MessagePriority.NORMAL)`` → 期望：
          返回 A（带内队首，不看全局）。
        - 前置：空队列 → 操作：``peek()`` → 期望：``None``，不阻塞。

        .. rubric:: 调用关系（审计）

        - 调用：无（只读内部排序结构，未见规约）
        - 被调：``flowing.agent.Agent._run_turn``（时机：检查点 ②.5
          urgent 吸收，按带窥看队首）；观测原语（典型调用方是 UI 预览与
          ``_dequeue()`` 覆写的前置决策）

        .. seealso::
           :meth:`dequeue`、:meth:`__len__`、:class:`MessagePriority`。
        """
        ...

    def remove(self, message_id: str) -> bool:
        """按 id 撤回一条**未出队**的消息（支撑 ``Agent.cancel_queued``）.

        .. rubric:: 功能介绍

        从队列中移除指定消息；返回是否找到并移除。``Agent.cancel_queued``
        在本方法返回 ``True`` 时联动 resolve 对应等待者（cancelled），调用方不挂起。

        .. rubric:: 行为规约

        - 已出队（正在或已被回合消费）或不存在的 id → 返回 ``False``，无副作用。
        - 非行为：不影响正在执行的回合（撤回的是排队消息，不是执行）。

        .. rubric:: 测试案例

        - 前置：``message()`` 已入队未消费 → 操作：``remove(message_id)`` →
          期望：返回 ``True``，且该等待者收到 ``status="cancelled"`` 的
          ``TurnResult``（联动在 Agent 层完成）。
        - 前置：消息已被消费 → 操作：``remove(message_id)`` → 期望：``False``。

        .. rubric:: 调用关系（审计）

        - 调用：无（移除为内部机制，未见规约）
        - 被调：``flowing.agent.Agent.cancel_queued``（时机：每次撤回
          未出队消息；返回 ``True`` 时由 Agent 层联动 resolve 等待者）

        .. seealso::
           :meth:`flowing.agent.Agent.cancel_queued`。
        """
        ...

    def set_priority(self, message_id: str, priority: MessagePriority) -> bool:
        """按 id 重设一条**未出队**消息的优先级，队列立即按新值重排.

        .. rubric:: 功能介绍

        找到队列中指定消息，原地改写 ``msg.priority`` 并按新值重新定位到
        ``(priority, 入队序号)`` 排序点。支撑
        :meth:`flowing.agent.Agent.set_queued_priority`。

        .. rubric:: 设计动机

        优先级是入队后仍可变的调度属性（典型场景：用户催办、Guardrail 升级
        提醒）。重排复用既有的 ``(priority, 入队序号)`` 排序键——**保留原
        入队序号**（用户裁决）：语义为「这条消息从入队起就该是新优先级」，
        而非「现在新来的一条高优先级消息」；它在新优先级带内的 FIFO 位置由
        原入队早晚决定。

        .. rubric:: 使用示例

        .. code-block:: python

            # 用户催办：把排队中的消息提升为 HIGH
            agent._message_queue.set_priority(msg_id, MessagePriority.HIGH)

        .. rubric:: 行为规约

        - 找到未出队消息 → 原地改写 ``msg.priority``、按 ``(新 priority,
          原入队序号)`` 重新定位，返回 ``True``。
        - 已出队（正在或已被回合消费）或不存在的 id → 返回 ``False``，
          无副作用。
        - 同步、无等待；不改写 ``id`` / ``timestamp`` / 等待者绑定。
        - 非行为：不影响正在执行的回合；自身不落盘（队列不持久化排序状态，
          恢复时按消息上附着的 ``priority`` 值重建——原地改写使恢复语义
          自动正确）。

        .. rubric:: 测试案例

        - 前置：队列含 HIGH 消息 A（先入）、NORMAL 消息 B（后入）→ 操作：
          ``set_priority(B.id, MessagePriority.HIGH)`` → 期望：返回
          ``True``，连续 ``dequeue`` 顺序为 A、B（B 保留原入队序号，
          晚于先入的 A）。
        - 前置：B 已出队 → 操作：同上 → 期望：``False``，队列不变。

        .. rubric:: 调用关系（审计）

        - 调用：无（重排为内部机制，未见规约）
        - 被调：``flowing.agent.Agent.set_queued_priority``（时机：每次
          重设未出队消息优先级）

        .. seealso::
           :meth:`enqueue`、:meth:`remove`、:class:`MessagePriority`、
           :meth:`flowing.agent.Agent.set_queued_priority`。
        """
        ...

    def __len__(self) -> int:
        """当前排队消息数（观测用途，无同步语义）.

        .. rubric:: 行为规约

        - 单事件循环内是调用时刻的精确值；跨 Task 观察时是瞬时值，不得据此做
          互斥决策（机制从简：队列无锁，依赖单循环串行）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：无（观测用途；框架内未见调用方，时机：未见规约）
        """
        ...


class MessageChain:
    """消息级树的任意手术入口（``Agent.chain``）：五 op 最小完备集.

    .. rubric:: 功能介绍

    对已落盘 / 在树上的历史消息做**增、删、改、重连**的统一入口。**内存消息链是
    唯一权威**——op 直接修改内存中的 ``Agent._messages`` 映射与 ``parent_id`` 链，
    随后向 ``tree.jsonl`` **append 一条变更记录**（运行期零截断、零重写）；
    物理重写延迟到压缩期。

    .. rubric:: 设计动机

    消息级设计放弃了旧 Turn 模型的「当前 turn 内存缓冲区」（prepend / remove 曾是
    免费的纯内存操作）：消息产生即落盘，手术直接作用于已落盘历史。没有 tombstone
    时，删除中间消息会迫使 ``parent_id`` 链断裂点之后的**每一行重写**（删 3 条
    可能重写 94 条）；tombstone 把「分散的 O(n) 重写」变成「运行期 append O(1) +
    压缩期一次 O(n)」。

    **一切内容走持久化路径**（M-29 裁决：原 ``TurnContext.inject`` 临时注入
    通道已删除）：recap / reminder 等「临时上下文」也由本类挂上、用完
    ``remove`` 擦除；回合开头的附加式注入走 ``before_turn`` 的
    ``TurnContext.pending_messages``（同样随批次挂树持久化）。

    .. rubric:: 使用示例

    .. code-block:: python

        # 单条增：在 m5 之后插入一条 SYSTEM 提醒（永久）
        agent.chain.insert("m5", Message(kind=MessageKind.SYSTEM,
                                         content=[TextBlock(text="...")]))

        # 单条删：删除一条消息（子树不级联）
        agent.chain.remove("m7")

        # 删除带子树的消息：先 reparent 子树，再 remove（定式）
        for child_id in children_of(agent, "m7"):
            agent.chain.reparent(child_id, to="m6")
        agent.chain.remove("m7")

        # 子树整体重连到另一分支
        agent.chain.reparent("m8", to="m3")

    .. rubric:: 行为规约

    五 op 语义总表：

    ============ ============ ============================================
    op           语义         影响范围
    ============ ============ ============================================
    ``insert``   单条增       新增消息 + 调整前后邻接（既有子消息重挂其下）
    ``branch``   单条增分支   新增消息挂到指定 parent 下（``None`` = 开新根），**不动既有子消息**
    ``remove``   单条删       **仅该消息，子树不自动级联**
    ``update``   单条改       **仅内容，不动链**
    ``reparent`` 子树重连     该消息及其整个子树移动到新位置
    ============ ============ ============================================

    通用规约：

    - 五 op 正交，任意手术由它们组合（最小完备集）。``branch`` 为 M-20
      裁决新增——``insert`` 的「既有子消息重挂」规则在分叉点会把多个
      子分支合并到新消息之下，只想**新增平行分支**时用 ``branch``；
      ``branch(None, msg)`` 是森林模型下唯一的持久化开根入口（压缩
      换链等场景，见 :meth:`branch`）。
    - 每个 op = 改内存链 + append 一条变更记录行（insert 为新消息行 + 邻接调整
      记录；branch 仅新消息行（无邻接调整）；remove 为 tombstone
      ``{"op":"remove","id":...}``；update / reparent 为对应变更行）。
    - **级联规则**：``remove`` 不级联——删除带子树的消息会留下父链指向不存在
      节点的孤儿子树；正确做法是**先 ``reparent`` 子树再 ``remove``**（定式，
      见使用示例）。
    - **不自动移动** ``current_head_id``：手术目标是历史结构，head 切换是
      ``Agent.fork`` 的职责；删除 / 重连当前 head 或其上溯路径上的消息属于
      调用方责任（MessageChain 本身不修正 head；需要删除当前 head 并回退
      head 的便捷语义用 ``Agent.remove`` / ``Agent.pop``）。
    - 不变量：op 完成后内存链与「文件重放结果」一致（重放变更记录必得同一
      权威链）。
    - 压缩（清理 tombstone、重写尾部）由持久化层（
      :class:`flowing.persistence.FileRecordStore` 的 drain 任务）在「队列
      排空后且 tombstone ≥ 阈值（默认 256）」时自主触发（S-31 裁决），
      **不是**本类方法的同步副作用。

    非行为：

    - 不做「级联删除」「自动 fork」「自动更新 head」等便利策略——策略在应用层。
    - 不提供批量 op 糖衣（批量 = 循环调用五 op，变更记录逐条 append）。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.agent.Agent._persist_message()``（insert / branch
      的新消息行）与 ``flowing.agent.Agent._persist_tree_record()``
      （remove 的 tombstone / update / reparent 与 insert 邻接调整的
      move 行）（时机：每次 op 完成内存修改后同步提交，S-31 裁决——
      write-behind 排队即返，op 保持同步签名）
    - 被调：应用 / 策略层经 ``flowing.agent.Agent.chain`` 调用（公共
      手术 API；框架内 docstring 示例见 ``flowing.errors``、
      ``flowing.context``，时机：未见规约）
    - 实例化方：``flowing.agent.Agent.__init__``（时机：同步骨架阶段，
      每次创建/恢复管线一次；构造契约见 ``_agent`` 字段，S-26 裁决）

    .. seealso::
       :meth:`flowing.agent.Agent.fork`（切换 head，与手术正交——手术改结构，
       fork 改视角）、
       :meth:`flowing.agent.Agent._persist_message` /
       :meth:`flowing.agent.Agent._persist_tree_record`（两条落盘通道）、
       :class:`flowing.persistence.FileRecordStore`（压缩的落盘细节）。
       原 ``TurnContext.inject`` 临时注入通道已删除（M-29）：注入走持久化
       的 :meth:`insert`，无独立旁路。
    """

    _agent: "Agent"
    """属主 Agent 反向引用（构造契约：``Agent.__init__`` 以
    ``MessageChain(self)`` 传入，S-26 裁决）——五个 op 直接操作
    ``Agent._messages``，落盘经 ``Agent._persist_message``（新消息行）
    与 ``Agent._persist_tree_record``（变更记录行）同步提交（S-31）。
    内部 API，不属稳定契约。
    """

    def insert(self, after_id: str, msg: Message) -> str:
        """单条增：在 ``after_id`` 之后插入一条消息，返回新消息 id.

        .. rubric:: 功能介绍

        将 ``msg`` 挂到 ``after_id`` 之下（``msg.parent_id = after_id``），并调整
        前后邻接：``after_id`` 原有的直接子消息**重挂到新消息之下**（各自子树
        随之整体移动，其内部链不变）。若 ``after_id`` 无子消息，等价于追加一个
        新分支。

        .. rubric:: 设计动机

        「单点 op、邻接调整」使线性链上的插入（最典型的历史手术，如补插一条
        SYSTEM 提醒）保持链序直觉：``m1 → m2`` 上 ``insert("m1", x)`` 得
        ``m1 → x → m2``。分支点上插入会把多个子分支合并到新消息之下——这是
        确定性规则而非特例处理；只想新增平行分支时用 :meth:`branch`。

        .. rubric:: 行为规约

        - 前置条件：``after_id`` 在树中存在；``msg.id`` 不与现有节点冲突
          （``msg.id`` 缺省时由框架分配）。
        - 后置条件：``msg`` 成为 ``after_id`` 的直接子消息且（若原有子消息）成为
          它们的父消息；``msg`` 落盘（append 新消息行）+ 邻接调整记录 append。
        - 边缘情况：插入当前 head 之后**不移动** ``current_head_id``（head 切换
          是 ``Agent.fork`` 的职责；turn 内 head 随消息挂树即时前移，回合末
          无结算写入）。
        - 非行为：不校验 ``msg.kind``（任何 kind 的消息都可成为历史节点）。

        :raises KeyError:
            ``after_id`` 不存在于消息树。
        :raises ValueError:
            ``msg.id`` 与现有节点冲突。

        .. rubric:: 测试案例

        - 前置：线性链 ``m1 → m2 → m3`` → 操作：``insert("m1", x)`` → 期望：
          ``x.parent_id == "m1"`` 且 ``m2.parent_id == x.id``；``m3`` 链不变。
        - 前置：``m1`` 无子消息 → 操作：``insert("m1", x)`` → 期望：``m1`` 有
          唯一子消息 ``x``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent._persist_message()``（新消息行）
          与 ``flowing.agent.Agent._persist_tree_record()``（邻接调整
          的 move 变更行，每个被重挂子消息一条）（时机：内存修改完成后
          同步提交，S-31）
        - 被调：应用 / 策略层经 ``Agent.chain``（公共 API）；
          ``flowing.context`` 规约的「回合中途追加用 ``chain.insert``」
          （时机：回合中途追加注入）

        .. seealso::
           :meth:`remove`、:meth:`reparent`、:meth:`flowing.agent.Agent.fork`。
        """
        # 宿主 Agent 引用经 S-26 裁决的构造契约字段 ``_agent``（见类 docstring），
        # 访问真实存在的 Agent._messages / Agent._persist_message /
        # Agent._persist_tree_record（S-31：均为同步提交，op 保持同步签名）。
        if after_id not in self._agent._messages:
            raise KeyError(after_id)
        if msg.id in self._agent._messages:
            raise ValueError(msg.id)
        rehung: list[str] = []
        for child in self._agent._messages.values():
            # 邻接调整：after_id 既有直接子消息重挂到新消息之下（各自子树随之整体移动）
            if child.parent_id == after_id:
                child.parent_id = msg.id
                rehung.append(child.id)
        msg.parent_id = after_id
        self._agent._messages[msg.id] = msg
        self._agent._persist_message(msg)   # 新消息行（同步提交，S-31）
        for cid in rehung:   # 邻接调整：每个被重挂的子消息一条 move 变更行
            self._agent._persist_tree_record(
                {"type": "move", "id": cid, "parent_id": msg.id})
        return msg.id

    def branch(self, parent_id: str | None, msg: Message) -> str:
        """单条增分支：把 ``msg`` 挂到指定 parent 下（``None`` = 开新根），返回该消息 id（M-20 裁决新增）.

        .. rubric:: 功能介绍

        将 ``msg`` 直接挂为 ``parent_id`` 的新子消息（``msg.parent_id =
        parent_id``），**不调整任何既有子消息**——与 :meth:`insert` 的
        「既有子消息重挂到新消息之下」规则正交。``parent_id=None`` 时
        ``msg`` 成为**新根**（森林模型的开根入口，见模块 docstring
        「消息级树（允许多根的森林）」）。

        .. rubric:: 设计动机

        ``insert`` 在分叉点的合并语义对「新增平行分支」（fork 分支上补一条
        新消息、并行探索线等）是错的；``branch`` 提供无语义陷阱的纯挂接
        原语。``None`` 放宽（裁决）：森林模型下「根」即「虚拟空父的子节点」，
        上下文压缩换链（摘要作为新根开新链，旧树完整保留）等场景由此
        获得**唯一**的持久化开根入口——不新增 ``add_root`` 之类同义方法，
        不引入虚拟根节点。

        .. rubric:: 行为规约

        - 前置条件：``parent_id`` 为 ``None``（开新根）或在树中存在；
          ``msg.id`` 不与现有节点冲突（缺省时由框架分配）。
        - 后置条件：``parent_id`` 非 ``None`` 时，``msg`` 成为其直接子消息，
          兄弟消息（既有子消息）的 ``parent_id`` 与位置**均不变**；
          ``parent_id=None`` 时，``msg.parent_id is None``，成为新的根，
          既有各根链不受影响。落盘仅 append 新消息行（无邻接调整记录）。
        - 返回值：``msg`` 的 id（调用方用于后续 ``reparent`` / ``remove``
          或 fork 目标）。
        - 非行为：不移动 ``current_head_id``（开新根后切 head 走
          :meth:`flowing.agent.Agent.fork`）；不校验 ``msg.kind``。

        :raises KeyError:
            ``parent_id`` 非 ``None`` 且不存在于消息树。
        :raises ValueError:
            ``msg.id`` 与现有节点冲突。

        .. rubric:: 测试案例

        - 前置：``m1`` 已有子消息 ``m2`` → 操作：``branch("m1", x)`` →
          期望：``x.parent_id == "m1"`` 且 ``m2.parent_id == "m1"`` 不变，
          ``m1`` 现有两个直接子消息。
        - 前置：``branch("m9", x)`` 后 ``fork(x_id)`` → 期望：head 切换到
          新分支，上下文上溯经 ``m1``。
        - 前置：已有链 ``m1 → m2`` → 操作：``branch(None, s)``（``s`` 为
          SYSTEM 摘要消息）→ 期望：``s.parent_id is None``，``_messages``
          中现有两个根（``m1`` 与 ``s``），``m1`` 链不变；再
          ``fork(s.id)`` → 期望：上下文上溯只含 ``s``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent._persist_message()``（时机：仅
          append 新消息行，无邻接调整记录；同步提交，S-31）
        - 被调：应用 / 策略层经 ``Agent.chain``（公共 API，M-20 裁决
          新增；``None`` 开根形态的典型调用方是
          ``flowing.composables.compact.use_compact`` 的压缩换链，时机：
          压缩回合收尾）

        .. seealso::
           :meth:`insert`（分叉点的合并语义对偶）、
           :meth:`flowing.agent.Agent.fork`（切到新分支 / 新根的视角操作——
           fork 只切视角，从不创建节点）。
        """
        # 宿主 Agent 引用见类 docstring ``_agent`` 字段（S-26 构造契约）。
        if parent_id is not None and parent_id not in self._agent._messages:
            raise KeyError(parent_id)
        if msg.id in self._agent._messages:
            raise ValueError(msg.id)
        msg.parent_id = parent_id   # None = 开新根（森林模型）
        self._agent._messages[msg.id] = msg
        self._agent._persist_message(msg)   # 仅新消息行，无邻接调整（同步提交，S-31）
        return msg.id

    def remove(self, msg_id: str) -> None:
        """单条删：仅删除该消息，**子树不级联**（tombstone）.

        .. rubric:: 功能介绍

        将 ``msg_id`` 从权威链中删除：内存链移除该节点 + 提交墓碑变更行
        （``{"type": "tombstone", "id": ...}``，经
        ``Agent._persist_tree_record``）；物理删除延迟到压缩期
        （FileRecordStore drain 任务排空后阈值触发，S-31）。

        .. rubric:: 行为规约

        - **不级联**：该消息的子消息**不**随之删除，其 ``parent_id`` 变为指向
          不存在节点的孤儿链——上下文上溯到断点即终止。删除带子树的消息前，
          **先 ``reparent`` 子树再 ``remove``**（定式）：

          .. code-block:: python

              for child_id in children:
                  agent.chain.reparent(child_id, to=msg.parent_id)
              agent.chain.remove(msg_id)

        - 删除尾部消息是纯截断语义；删除中间消息在「直接物理删除」模型下本需
          O(后续长度) 重写，tombstone 将其降为运行期 O(1)。
        - 幂等性：对不存在的 id 抛 ``KeyError``（重复删除不是静默成功）。
        - 非行为：不移动 ``current_head_id``；删除 head 上溯路径上的消息属于
          调用方责任。需要「删除当前 head 并回退到父节点」时，请使用
          ``Agent.remove`` / ``Agent.pop``（Agent 层负责 head 维护）。

        :raises KeyError:
            ``msg_id`` 不存在于消息树（或已被删除）。

        .. rubric:: 测试案例

        - 前置：``m1 → m2 → m3`` → 操作：``remove("m2")`` → 期望：``m2`` 不可达；
          ``m3.parent_id == "m2"``（孤儿）；重放文件（消息行 + tombstone）后
          权威链同样不含 ``m2``。
        - 前置：``m2`` 带子消息 ``m3`` → 操作：先 ``reparent("m3", to="m1")``
          再 ``remove("m2")`` → 期望：``m3.parent_id == "m1"``，无孤儿。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent._persist_tree_record()``（时机：
          内存移除后同步提交 tombstone 变更行，S-31）
        - 被调：应用 / 策略层经 ``Agent.chain``（公共 API；docstring
          示例见 ``flowing.errors``，时机：未见规约）；recap / reminder
          等临时注入的擦除（时机：用完即擦——本类 docstring 自述）

        .. seealso::
           :meth:`reparent`、:meth:`insert`。
        """
        # 宿主 Agent 引用见类 docstring ``_agent`` 字段（S-26 构造契约）。
        if msg_id not in self._agent._messages:
            raise KeyError(msg_id)
        del self._agent._messages[msg_id]  # 内存链移除；子树不级联（留下孤儿链）
        self._agent._persist_tree_record(
            {"type": "tombstone", "id": msg_id})   # 墓碑行（同步提交，S-31）

    def remove_by_tags(self, tags: set[str]) -> int:
        """按 **tags 过滤**批量删（recap / reminder 等临时注入的擦除入口）。

        .. rubric:: 功能介绍

        遍历消息树删除满足以下**任一**条件的消息，逐条走 :meth:`remove`
        （内存移除 + 墓碑行），返回删除条数：

        - 消息自身 ``Message.tags`` 与给定 ``tags`` **相交**；
        - 消息任一 ``content`` block 的 ``tags`` 与给定 ``tags`` 相交。

        支撑「临时注入、响应后清洗」场景：``after_turn`` 钩子内按 tags 擦除
        本回合附加的提醒消息，保证不累积到后续轮次。

        .. rubric:: 行为规约

        - 匹配：以上两条规则任一命中即删除；``tags`` 为空集 → 不删任何消息
          （返回 0）。
        - 逐条 :meth:`remove`：子树不级联、产生墓碑行、已删除/不存在 id 的
          幂等语义与 :meth:`remove` 一致（本方法只遍历现存消息，天然无
          ``KeyError``）。
        - 非行为：本方法不直接移动 ``current_head_id``；但若按 tags 删除的
          消息中包含当前 head，调用方应使用 ``Agent.remove_by_tags``
          （Agent 层负责 head 维护）。不触碰 ``pending_messages``（本方法
          作用于已挂树的消息）。

        .. rubric:: 测试案例

        - 前置：树上含 ``Message(tags=['reminder'])`` 的消息两条 + 无 tags
          消息一条。操作：``chain.remove_by_tags({'reminder'})`` → 期望：
          返回 2，两条 reminder 消息从树与重放权威链中消失，无 tags 消息保留。
        - 前置：消息的 ``TextBlock`` 带有 ``tags=['reminder']``。操作：
          ``chain.remove_by_tags({'reminder'})`` → 期望：该消息被删除。
        - 前置：``remove_by_tags(set())`` → 期望：返回 0，无删除。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.message.MessageChain.remove``（时机：逐条，每命中一条）
        - 被调：应用 / 策略层经 ``Agent.chain``（时机：after_turn 等收尾
          钩子内按 tags 清洗临时注入消息）；``Agent.remove_by_tags`` 透传
          本方法并补 head 维护

        .. seealso:: :meth:`remove`、:meth:`flowing.agent.Agent.remove_by_tags`
        """
        if not tags:
            return 0
        to_remove = []
        for mid, m in self._agent._messages.items():
            msg_tags = m.tags if isinstance(m.tags, (list, tuple, set)) else []
            if (set(msg_tags) & tags) or any(
                set(getattr(b, "tags", None) or []) & tags for b in m.content
            ):
                to_remove.append(mid)
        for mid in to_remove:
            self.remove(mid)
        return len(to_remove)

    def update(self, msg_id: str, content: list[ContentBlock]) -> None:
        """单条改：仅替换消息内容，**不动链**.

        .. rubric:: 功能介绍

        替换 ``msg_id`` 的 ``content`` 列表（如编辑历史中的一条用户消息、修正
        一条 SYSTEM 注入）；``parent_id`` / ``kind`` / ``id`` 等其余字段不变，
        前后邻接不变。

        .. rubric:: 行为规约

        - 运行期副作用：改内存 + append update 变更记录；压缩期随尾部重写固化。
        - 非行为：不允许改 ``kind`` / ``parent_id``（改结构用 :meth:`reparent`，
          改身份等于删除 + 插入的组合）；不递归校验新 content 的合法性
          （block 排列合法性是 adapter 组装时的职责）。
        - 边缘情况：更新一条 PROVIDER 消息的 content 不会自动重算 ``turn_end``
          ——``turn_end`` 是写入时的边界事实，调用方需自行保持一致（机制不猜策略）。

        :raises KeyError:
            ``msg_id`` 不存在于消息树。

        .. rubric:: 测试案例

        - 前置：``m1(USER, [text="旧"])`` → 操作：``update("m1",
          [TextBlock(text="新")])`` → 期望：内容替换、``parent_id`` 不变、
          子消息链不受影响、文件尾部有 update 变更行。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent._persist_tree_record()``（时机：
          内容替换后同步提交 update 变更行，S-31）
        - 被调：应用 / 策略层经 ``Agent.chain``（公共 API；框架内未见
          逐字调用方，时机：未见规约）

        .. seealso::
           :meth:`remove`、:meth:`reparent`。
        """
        # 宿主 Agent 引用见类 docstring ``_agent`` 字段（S-26 构造契约）。
        if msg_id not in self._agent._messages:
            raise KeyError(msg_id)
        self._agent._messages[msg_id].content = content  # 仅替换内容，不动链
        self._agent._persist_tree_record(
            {"type": "update", "id": msg_id,
             "content": content})   # update 变更行（同步提交，S-31）；压缩期固化

    def reparent(self, msg_id: str, *, to: str) -> None:
        """子树重连：改 ``msg_id`` 的 ``parent_id``，**整个子树随之移动**.

        .. rubric:: 功能介绍

        「单点 op，子树效应」——只改目标消息一个字段，其后代链不变，整个子树
        自然跟着走。是五 op 中唯一能移动既有结构的 op：分支迁移、删除前的子树
        保全、压缩摘要分支的挂接都经它完成。

        .. rubric:: 使用示例

        .. code-block:: python

            # 把 m8 分支整体挂到 m3 之下
            agent.chain.reparent("m8", to="m3")

        .. rubric:: 行为规约

        - 前置条件：``msg_id`` 与 ``to`` 均在树中存在；``to`` 不是 ``msg_id``
          自身或其**后代**（防环）。
        - 后置条件：``msg_id.parent_id == to``；其后代的 ``parent_id`` 不变；
          append move 变更记录。
        - 边缘情况：重连根消息（``parent_id=None`` 者）等价于把整棵树挂为新
          子树——规约不禁止，但调用方应明确自己在做什么（机制不拦策略）。
        - 非行为：不移动 ``current_head_id``；不复制子树（移动语义，非拷贝）。

        :raises KeyError:
            ``msg_id`` 或 ``to`` 不存在于消息树。
        :raises ValueError:
            ``to`` 是 ``msg_id`` 自身或其后代（会成环）。

        .. rubric:: 测试案例

        - 前置：``m1 → m2 → m3`` 与分支 ``m2 → m4 → m5`` → 操作：
          ``reparent("m4", to="m1")`` → 期望：``m4.parent_id == "m1"``，
          ``m5.parent_id == "m4"`` 不变（子树整体移动）。
        - 前置：同上 → 操作：``reparent("m2", to="m4")`` → 期望：抛
          ``ValueError``（``m4`` 是 ``m2`` 的后代，会成环）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent._persist_tree_record()``（时机：
          改链后同步提交 move 变更行，S-31）
        - 被调：应用 / 策略层经 ``Agent.chain``（公共 API）；「删除前
          保全子树」定式中与 :meth:`remove` 组合（时机：删除带子树
          消息前——本类 docstring 定式）

        .. seealso::
           :meth:`remove`（删除前保全子树的定式）、:meth:`insert`、
           :meth:`flowing.agent.Agent.fork`。
        """
        # 宿主 Agent 引用见类 docstring ``_agent`` 字段（S-26 构造契约）。
        if msg_id not in self._agent._messages:
            raise KeyError(msg_id)
        if to not in self._agent._messages:
            raise KeyError(to)
        if to == msg_id:
            raise ValueError(to)
        ancestor = self._agent._messages[to]
        while ancestor.parent_id is not None:
            # 防环：上溯 to 的祖先链，若经过 msg_id 则 to 是其后代
            if ancestor.parent_id == msg_id:
                raise ValueError(to)
            ancestor = self._agent._messages[ancestor.parent_id]
        self._agent._messages[msg_id].parent_id = to  # 单点改链，整个子树随之移动
        self._agent._persist_tree_record(
            {"type": "move", "id": msg_id,
             "parent_id": to})   # move 变更行（同步提交，S-31）
