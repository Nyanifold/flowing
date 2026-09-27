"""``flowing.message`` —— 对象层消息模型、消息队列与消息级树手术。

.. rubric:: 功能介绍

本模块定义 Flowing 的对象层消息表示（与任何 Provider API 的 ``role``
字段、任何 UI 渲染样式解耦）、Agent 的消息队列（优先级排序 + 同级 FIFO），
以及消息级树的手术入口 :class:`MessageChain`。

职责边界：

1. 消息的产生与表示：任意来源（用户、LLM、工具、外部 Agent、系统事件）
   产生的新信息统一表示为 :class:`Message`。
2. 消息的组织结构：消息级树（允许多根的森林）——树节点 = 消息，
   ``Message.id`` + ``Message.parent_id`` 构成亲代链；``parent_id=None``
   是根标记，一棵树允许多个根（根集合 = 全部 ``parent_id=None`` 的
   消息），新根由 :meth:`MessageChain.branch` 以 ``parent_id=None``
   开启（典型场景：上下文压缩换链——摘要作为新根开新链，旧树完整保留）。
   不设虚拟根节点。``Agent.current_head_id`` 指向某条消息的 id；fork
   只切视角、从不创建节点。
3. 消息的排队与调度：外部消息经 :class:`MessageQueue` 按优先级排序消费；
   ``MessageKind.PROVIDER`` 永不进队列（永远在逻辑 turn 内由 Provider
   adapter 产生）。
4. 消息的持久化与手术：一行一个 Message、消息完整后提交记录（记录流——
   追加是主要形态，后端定期清洗重写，重放结果不变，见
   :mod:`flowing.persistence`）；任意历史修改走 :class:`MessageChain` 五
   op。tombstone（删除标记行）/ 撕裂末行容忍 / 压缩等持久化机制属
   :mod:`flowing.persistence` 与 ``flowing.agent`` 的职责，本模块只约束
   “消息对象 ↔ 行”的映射语义（:func:`to_record` /
   :func:`from_record`）。

逻辑 Turn 只是执行概念：消费一条消息 → 产生一条 ``turn_end=True`` 的
PROVIDER 消息（或被 abort）的过程；执行期载体是
``flowing.agent.TurnContext`` （不落盘、不进树、崩溃后不恢复）。

Message 字段规约总表：

.. list-table::
   :header-rows: 1
   :widths: 16 84

   * - 字段
     - 规约要点
   * - ``id``
     - Agent 内自增序列号（纯数字字符串），同时是消息级树的节点 id；缺省 ``None`` 由 Agent 边界铸造
   * - ``parent_id``
     - 消息级亲代链；``None`` = 根标记（允许多根，森林模型）；树上溯与上下文组装的唯一依据
   * - ``kind``
     - 对象层唯一角色判别，七值枚举 :class:`MessageKind`；消息没有 ``role`` 属性
   * - ``content``
     - :class:`ContentBlock` 列表，不同 type 可交错排列
   * - ``turn_end``
     - 逻辑 turn 关闭的边界标记；由 agent 层在挂树时写入（turn 随本条消息关闭 → ``True``）
   * - ``partial``
     - 流式中断的未完成消息标记；中断时已累积内容保留落盘（非丢弃）
   * - ``synthetic``
     - ``True`` = 恢复时合成的占位消息（孤立 tool_call 的占位 TOOL 消息），非真实产物
   * - ``tool_call_id``
     - 配对锚（仅 ``kind=TOOL`` 非 ``None``）：与 PROVIDER 消息 ``ToolCallBlock.id`` 1:1 严格成对
   * - ``tool_status``
     - 工具结果状态五值（仅 ``kind=TOOL`` 非 ``None``）；``__post_init__`` 双向强制
   * - ``source``
     - 自由字符串二级分类（框架不枚举），投递方填写
   * - ``tags``
     - 任意标签列表，用于分组 / 过滤 / 清洗
   * - ``priority``
     - 队列排序依据（``INTERRUPT > STEER > HIGH > NORMAL > LOW``）
   * - ``timestamp``
     - 时区无关（UTC / epoch）；附着于消息，是否进 LLM 上下文由 adapter 决定
   * - ``usage``
     - 本消息对应的 Provider 实测用量（仅 PROVIDER 消息携带），随消息落盘、恢复后仍在

kind → API role 发送映射（Provider adapter 职责）：

.. list-table::
   :header-rows: 1

   * - kind
     - Anthropic
     - OpenAI
     - Gemini
   * - ``USER``
     - ``user``
     - ``user``
     - ``user``
   * - ``PROVIDER``
     - ``assistant``
     - ``assistant``
     - ``model``
   * - ``TOOL``
     - ``user`` （tool_result）
     - ``tool``
     - ``tool``
   * - ``SYSTEM``
     - ``user`` （XML 包裹）
     - ``system``
     - ``user``
   * - ``PEER``
     - ``user`` （XML 包裹）
     - ``user``
     - ``user``
   * - ``EVENT``
     - ``user`` （XML 包裹）
     - ``user``
     - ``user``
   * - ``SUBAGENT``
     - 独立消息，XML 包裹（格式由 adapter 定）
     - ``user``
     - 同左

- OpenAI 侧 SUBAGENT 落 ``user`` role（XML 包裹）：外部结果
  回喂属“输入”，不落 ``developer`` （避免给外部数据提指令权），更不伪造
  ``assistant`` （会破坏轮次语义与 tool_calls 配对）。
- SYSTEM / PEER / EVENT / SUBAGENT 的 XML 包裹格式由 adapter
  按 Provider 能力决定，框架核心不约束具体格式。
- “某 block type 出现在哪些 kind 中”是典型情况而非硬约束；不合法排列
  （如 PROVIDER 消息含 image block）由 Provider adapter 在组装 API
  请求时验证并报错，框架核心不验证。

入队规则：

- 进队列：``USER`` / ``EVENT`` / ``PEER`` / ``SUBAGENT`` /
  ``SYSTEM`` （可选）/ 异步工具最终结果（以 ``EVENT`` kind 入队，
  ``source="tool_result"``，多块 content = 标注块 + 结果块；拦截通知
  与终止通知——异常 / 取消——同走本通道）。``TOOL``
  kind 本身不入队——同步工具结果在逻辑 turn 内经挂树直接进入消息树。
- 不进队列：``PROVIDER``——永远在逻辑 turn 内产生。
- ``SYSTEM`` 双通道：经队列投递（触发新逻辑 turn），或由上下文组装内部
  注入本 turn 的 system prompt 段（不触发新 turn）。
- kind 无行为含义：消息类型只决定 adapter 的呈现映射（见上表），不改变
  turn 语义——任何入队批次都正常开回合、正常调用 LLM，不存在“纯系统
  消息不触发 LLM 调用”之类的短路。

流式与 partial：

- 流式中断（abort）时，已累积内容以 ``partial=True`` 的消息保留落盘，
  而不是丢弃；正常完成的消息恒为 ``partial=False``。
- ``on_provider_delta`` 钩子收到的 delta 本身不落盘（volatile），中断时
  的已累积内容作为持久化状态保留。

.. rubric:: 使用示例

.. code-block:: python

    # 构造一条用户消息并入队；enqueue_message 返回消息 id
    msg = Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="帮我查订单 4521")],
        source="chat_input",
        tags=["order"],
    )
    message_id = await agent.enqueue_message(msg)

    # 历史手术：在 m5 之后插入一条 SYSTEM 提醒（永久）
    agent.chain.insert("m5", Message(kind=MessageKind.SYSTEM,
                                     content=[TextBlock(text="...")]))

.. rubric:: 行为要点

- 媒体块一律 base64 内联（``data`` 必填且是权威表示）：消息层不感知
  “文件最初从哪来”，也不做文件大小检查（大小治理分层：前端上传前校验 /
  ``on_enqueue`` 钩子 / Provider adapter 的 ``ContextLengthError``
  兜底）。
- 历史消息不可在应用层直接修改——修改历史走 :class:`MessageChain` 五 op
  或 fork（切分支）。
- 本模块只提供机制：优先级排序队列、五 op 手术原语、媒体 base64 统一编码。
  策略（防饿死加权、drain 合并、何时压缩上下文、摘要如何生成）由覆写
  ``Agent._dequeue()``、Composable 或应用层决定。

.. seealso::

   :class:`flowing.agent.Agent` （消息队列与工作循环的宿主；
   ``Agent.chain`` 是 :class:`MessageChain` 的访问入口）
   :class:`flowing.agent.TurnContext` （逻辑 turn 的执行期临时对象：
   ``started_at`` / ``message_ids`` / ``aborted`` / ``pending_messages`` /
   ``usages``）
   :mod:`flowing.tool` （``ToolCall`` / ``ToolResult`` 的定义处，与 block
   形式互转）
   :mod:`flowing.context` （``Context`` / ``PromptBlock`` / ``PromptSegment``
   ——上下文组装产物）
"""

from __future__ import annotations   # 注解延迟求值（message→model 为注解级边）

import asyncio
import bisect
import enum
import itertools
import json
import math
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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
    "estimate_block_tokens",
    "estimate_message_tokens",
    "to_record",
    "from_record",
]


class MessageKind(enum.Enum):
    """消息来源枚举：判别“这条消息来自谁”（七值，跨版本稳定契约）。

    .. rubric:: 功能介绍

    消息对象没有 ``role`` 属性——Provider adapter 负责把 ``kind`` 映射
    为各 API 的 role（Anthropic ``assistant``、OpenAI ``assistant``、
    Gemini ``model`` 等），UI 通过 ``kind`` 与 ``tags`` 自行决定渲染
    方式。

    命名原则：所有 kind 描述“来自谁”而非“扮演什么角色”。``PROVIDER``
    是 Provider 输出的统一来源名——不取会隐含“纯文本 LLM”的
    “assistant”读法，以覆盖文生图 / 语音 / 视频等多模态输出。
    Skill 正文以 ``EVENT`` 消息送达。子 Agent 返回结果使用独立的
    ``SUBAGENT`` kind，避免与工具结果混淆。

    .. rubric:: 使用示例

    .. code-block:: python

        msg = Message(kind=MessageKind.USER, content=[TextBlock(text="你好")])
        event = Message(
            kind=MessageKind.EVENT,               # 异步工具最终结果
            source="tool_result",
            content=[TextBlock(text="<标注>"), StructBlock(data={...})],
        )

    .. rubric:: 行为要点

    - 序列化（``tree.jsonl`` 行）使用当前枚举成员的字符串值
      （``"user"`` / ``"provider"`` 等）；``from_record`` 将 ``kind``
      严格还原为当前枚举值。
    - 各 kind 的典型 ``content`` 组合（典型情况而非硬约束，框架核心不
      验证“某 kind 可否含某 block type”，合法性由 Provider adapter 在
      组装 API 请求时验证）：

      .. list-table::
         :header-rows: 1

         * - kind
           - content 中 block type 的典型排列
         * - ``USER``
           - ``[text]`` / ``[image]`` / ``[text, image]`` / ``[text, file]``
         * - ``PROVIDER``
           - ``[thinking, text]`` / ``[thinking, tool_call]`` / 交错组合
         * - ``TOOL``
           - ``[text]`` / ``[struct]`` / ``[struct, image]`` （纯内容块，无协议块）
         * - ``SYSTEM``
           - ``[text]``
         * - ``PEER``
           - ``[text]`` / ``[text, file]``
         * - ``EVENT``
           - ``[text]`` / ``[text, image]`` / ``[text, struct]`` （异步最终结果：标注块 + 结果块）

    - ``kind`` 不携带 UI 渲染信息（折叠 / 颜色 / 字体是应用层概念）；
      框架不枚举 ``source`` （二级分类自由字符串，由投递方填写）。
    - ``PEER`` 与 ``EVENT`` 的区分：PEER = 另一个 Agent 实例有意图地
      主动发送（语义上更接近用户消息，``source`` 典型值 ``"message_to"`` /
      ``"agent_delegate"`` / ``"agent_steer"``）；EVENT = 非 Agent 实体
      触发的“某事发生了”（辅助信息，``source`` 典型值
      ``"scheduled_task"`` / ``"plugin_event"`` / ``"tool_result"``）。
      钩子（如 ``before_turn``）可据此按触发来源做精准决策。
    - ``TOOL`` kind 不入队：同步工具结果在逻辑 turn 内经挂树直接进入
      消息树（携带 ``tool_call_id`` / ``tool_status``，不经过队列）；异步
      最终结果以 ``EVENT`` kind 入队（标注块 + 结果块，不参与配对）。

    .. seealso::
       :class:`flowing.message.Message`、
       :class:`flowing.message.MessageQueue`、
       :mod:`flowing.model` （Provider adapter 的映射职责）。
    """

    USER = "user"
    """用户输入。进队列；LLM 视为对话中的用户消息。
    """
    PROVIDER = "provider"
    """LLM / 多模态模型 / 任意 Provider 的响应。永远在逻辑 turn 内产生、
    不进队列；``turn_end`` 边界标记只对本 kind 有语义。
    """
    TOOL = "tool"
    """工具执行结果（摊平形态：``content`` 只装纯内容块，配对元数据在
    消息级 ``tool_call_id`` / ``tool_status`` 字段，``__post_init__``
    双向强制）。同步结果在 turn 内直接挂树；异步最终结果以 ``EVENT``
    kind 入队。
    """
    SYSTEM = "system"
    """框架 / Composable 的上下文注入。双通道：经队列（触发新 turn）或
    由上下文组装内部注入（不触发新 turn）。
    """
    PEER = "peer"
    """来自另一个 Agent 实例的有意图消息。
    """
    EVENT = "event"
    """外部事件或扩展交付内容（Cron、事件插件、Composable 提醒、Skill
    正文、异步工具最终结果）；通常附带 ``source`` 说明来路（如
    ``"system-reminder"``、``"skill:review"``）。
    异步最终结果为多块 content（标注块 + 结果块），不参与配对。
    """
    SUBAGENT = "subagent"
    """子 Agent 返回结果；永远以独立消息入队，与普通工具结果明确分离。
    """


class MessagePriority(enum.IntEnum):
    """消息优先级：:class:`MessageQueue` 的排序依据（数值越小越优先）。

    .. rubric:: 功能介绍

    五级优先级，决定工作循环的出队消费顺序：``INTERRUPT (0) > STEER (1)
    > HIGH (2) > NORMAL (3) > LOW (4)``。同优先级按入队顺序（FIFO）。

    用 ``IntEnum`` 且“更优先 = 更小数值”使排序实现为普通数值升序 + 入队
    序号，无需自定义比较器。``INTERRUPT`` 用于打断当前回合的插队场景；
    ``STEER`` 仅弱于它——回合进行中入队的 STEER 消息被当轮吸收，当轮
    LLM 调用的上下文即可见但不打断；``HIGH`` 的典型场景是人工催办、
    外部高优告警等紧急但不打断当前回合的通知。

    .. rubric:: 使用示例

    .. code-block:: python

        # 亲代 Agent 引导子 Agent：目录已改动，请重新读取——
        # 当轮 context 可见、不打断（Agent.steer 便捷封装即此形态）
        steer = Message(
            kind=MessageKind.PEER,
            content=[TextBlock(text="目录 src/ 已改动，请重新读取后再继续")],
            source="agent_steer",
            priority=MessagePriority.STEER,
        )
        await child_agent.enqueue_message(steer)

    .. rubric:: 行为要点

    - 排序语义：按枚举数值升序；同优先级按入队顺序（FIFO）。排序发生在
      :class:`MessageQueue` 内部，``Message`` 本身不可比较。
    - ``INTERRUPT`` / ``STEER`` 的回合内吸收语义由 Agent 层实现：回合
      进行中入队的 ``INTERRUPT`` 消息会中断当前回合并挂树、下一回合
      上下文可见；``STEER`` 消息不中断回合，当轮 LLM 调用的上下文即可见。
      普通 ``dequeue`` 路径只认排序，不区分吸收语义。
    - 优先级只是排序依据：不防饿死、不按来源加权——具体调度策略由覆写
      ``Agent._dequeue()`` 或应用层决定，不在框架核心。
    - 副线消息不入队（副线走 ``Agent.side_query``，不经队列），``priority``
      对其无意义；``PROVIDER`` 消息不入队，``priority`` 附着但无调度效果。
    - 落盘耦合：枚举数值即消息的落盘值（恢复时按附着值重建），调整枚举
      取值即变更持久化格式。

    .. seealso::
       :class:`flowing.message.MessageQueue`、
       :meth:`flowing.agent.Agent.enqueue_message`。
    """

    INTERRUPT = 0
    """打断：最优先消费；回合进行中入队会中断当前回合。
    """
    STEER = 1
    """引导：仅弱于 INTERRUPT；回合进行中入队会被当轮吸收、上下文可见
    但不打断。
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


@dataclass(kw_only=True)
class ContentBlock:

    """消息内容片段基类：``type`` 字段判别“这一段是什么”（八种 type）。

    .. rubric:: 功能介绍

    一条 :class:`Message` 的 ``content`` 列表可含多种 type 的
    ContentBlock，交错排列（Anthropic 原生支持；OpenAI / Gemini 由
    adapter 做映射转换，如 OpenAI 的 ``tool_calls[]`` 逐项转回 block
    以保持交错顺序）。

    block 化的消息内容使多模态（文本 + 图像 + 文件混排）、思考过程
    （thinking）、工具调用（tool_call）在同一个消息模型内统一表达，与
    具体 Provider API 的内容结构解耦。媒体块一律 base64 内联（``data``
    必填且是权威表示）：文件路径、URL、剪贴板进入消息层后是同一格式，
    adapter 从统一 base64 出发做各 API 转换。

    .. rubric:: 行为要点

    - 框架核心只接纳 base64 数据，不做文件大小检查（大小治理分层：
      前端上传前校验 → ``on_enqueue`` 钩子 → Provider adapter 的
      ``ContextLengthError`` 兜底）。
    - ``mime_type`` 始终可选：缺失时由 adapter 按文件扩展名或内容魔数
      推断；显式指定优先。
    - 降级策略属 UI / Provider 层（非框架核心）：video → 首帧截图或
      文件引用；audio → 占位文本或语音转文字。
    - 边界区分：``text`` 与 ``file`` （可读字符串与二进制）；``image``
      与 ``video`` （单帧与连续帧）；``image`` 与 ``file`` （多模态视觉
      输入与仅作文件传递）；``audio`` 与 ``file`` （需语音理解与仅传
      mp3）；``struct`` 与 ``text`` （程序可读的 JSON 结构与纯文本——
      对 LLM 的投影同为文本）。

    .. seealso::
       :class:`flowing.message.Message`、
       :mod:`flowing.model` （adapter 逐 type 映射）。
    """

    type: str = ""
    """片段类型判别，八值之一：``"text" | "thinking" | "tool_call" | "struct" | "image" | "video" | "audio" | "file"``。
    子类将其收窄为对应的 ``Literal``。序列化时作为 ``tree.jsonl`` 行内
    content 项的判别字段。
    """


@dataclass(kw_only=True)
class TextBlock(ContentBlock):
    """纯文本内容块。

    .. rubric:: 功能介绍

    直接可读的字符串片段；``Agent.message("...")`` 的 ``str`` 入参自动
    打包为 ``[TextBlock(text=content)]``。最典型的 block type，可能
    出现在任何 kind 中。文本单独成块（而非 Message 上的字段）使其可与
    其它类型 block 交错混排（如 ``[thinking, text, tool_call, text]``）。

    .. rubric:: 使用示例

    .. code-block:: python

        msg = Message(kind=MessageKind.USER, content=[TextBlock(text="帮我查订单")])

    .. rubric:: 行为要点

    - ``text`` 为空字符串是合法的（语义由上层决定，框架不拒绝）。
    - 不做长度限制、不做内容审核（审核走 ``on_enqueue`` 钩子）。

    .. seealso::
       :class:`flowing.message.ContentBlock`、
       :meth:`flowing.agent.Agent.message`。
    """

    type: Literal["text"] = "text"
    """固定为 ``"text"``。
    """
    text: str
    """文本内容。直接可读；序列化时原样进入 ``tree.jsonl`` 行。
    """


@dataclass(kw_only=True)
class ThinkingBlock(ContentBlock):
    """LLM 思考 / 推理过程内容块。

    .. rubric:: 功能介绍

    承载 Provider 返回的推理过程（Anthropic ``thinking`` block、OpenAI
    ``reasoning_content`` 等）；典型出现在 ``PROVIDER`` 消息中，UI 通常
    折叠或淡化显示。思考过程是 Provider 响应的一等部分：持久化后可审计、
    可在后续上下文中回传（Anthropic 要求 thinking 块原样回传并校验
    签名）。

    .. rubric:: 行为要点

    - ``signature``：Provider 签发的完整性签名（校验思考内容未被篡改，
      如 Anthropic）；不适用此机制的 Provider 为 ``None``。
    - UI 的折叠 / 淡化是应用层策略，框架不预设渲染方式。

    .. seealso::
       :class:`flowing.message.ContentBlock`、
       :class:`flowing.providers.ProviderDelta`。
    """

    type: Literal["thinking"] = "thinking"
    """固定为 ``"thinking"``。
    """
    thinking: str
    """思考过程文本。
    """
    signature: str | None = None
    """Provider 签发的完整性签名（如 Anthropic）；无签名机制时为 ``None``。
    """


@dataclass(kw_only=True)
class ToolCallBlock(ContentBlock):
    """工具调用请求内容块（LLM 侧）。

    .. rubric:: 功能介绍

    ``PROVIDER`` 消息中的一个工具调用请求。``id`` 由 Provider 分配（如
    Anthropic ``tool_use.id``、OpenAI ``tool_calls[].id``），作为配对
    锚点：与同分支后续 TOOL 消息的 :attr:`Message.tool_call_id` 严格
    配对（1:1）。

    block 形式是消息层的权威表示（持久化、上下文组装都用它）；
    :class:`flowing.tool.ToolCall` 是它的“解析后”形式——剥离通用字段，
    只保留 ``id`` / ``name`` / ``args`` （+ 通用短路字段 ``shortcut``），
    供 ``Agent.tool_call()`` 与工具钩子使用。两者经
    :meth:`flowing.tool.ToolCall.from_block` 单向转换（模块依赖保持
    单向：``flowing.tool`` 认识 ``flowing.message``，反之不认识）。

    .. rubric:: 使用示例

    .. code-block:: python

        for block in response.message.content:
            if block.type == "tool_call":
                result = await agent.tool_call(ToolCall.from_block(block))

    .. rubric:: 行为要点

    - 配对不变量（树内封闭）：tool_call block 必须有对应的 TOOL 结果消息
      （同分支、严格成对，锚点为本 block 的 ``id`` 与结果消息的
      ``Message.tool_call_id``）——执行期取消以 ``tool_status="cancelled"``
      封闭、缺失时恢复流程合成 ``synthetic=True`` 的占位 TOOL 消息（见
      :attr:`Message.synthetic`），均落盘；装配对未配对只做断言
      （``UnpairedToolCallError``），不做读时修补。
    - ``args`` 是 LLM 填写的原始参数字典，未合并；参数合并（specified
      （含注入表达式）> LLM args > schema 默认值，specified 最高）与
      防篡改通道在 ``flowing.tool.ToolEntry`` 层处理，block 本身只是
      载体。

    .. seealso::
       :class:`flowing.tool.ToolCall`、
       :attr:`flowing.message.Message.tool_call_id`、
       :meth:`flowing.agent.Agent.tool_call`。
    """

    type: Literal["tool_call"] = "tool_call"
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


@dataclass(kw_only=True)
class StructBlock(ContentBlock):
    """结构数据内容块：对程序是结构、对 LLM 是 dumps 文本。

    .. rubric:: 功能介绍

    承载工具结果 / 框架内部产生的 JSON 兼容结构数据（dict / list /
    dataclass / BaseModel 等的序列化形态）。对程序是结构（``block.data``
    直读，无需 ``json.loads`` 回解）；对 LLM 是文本——adapter 恒投影为
    ``json.dumps(data, ensure_ascii=False)``，LLM 可见面与纯文本方案
    逐字节相同。

    工具结果摊平后，混合结果序列里的结构数据若就地转成 ``TextBlock``，
    机器可读形态只剩 JSON 文本；``StructBlock`` 让结构随行而不丢程序
    可读性。分工：文本与标量装 ``TextBlock``，复合结构装
    ``StructBlock``，媒体装媒体块。

    .. rubric:: 行为要点

    - ``data`` 恒 JSON 兼容（``json.dumps`` 可序列化）；构造时校验，
      违反抛 ``ValueError`` （作者 bug 的诚实失败点，如深层埋藏的非
      JSON 对象在塑形时于此报错）。
    - adapter 不对 ``StructBlock`` 做任何原生结构化映射，恒投影为
      ``json.dumps(ensure_ascii=False)`` 文本（所有 adapter 统一）。
    - 作者不直接构造本块：它由归一化 / 塑形
      （``flowing.tool.normalize_output`` / ``flowing.tool.output_to_blocks``）
      与框架内部产生；工具 ``execute`` 签名中禁止出现 Block 类。
    - token 估算按 ``json.dumps`` 长度计（见 :func:`estimate_message_tokens`）。

    .. seealso::
       :class:`flowing.message.ContentBlock`、
       :class:`flowing.message.TextBlock`、
       :class:`flowing.tool.ToolResult`。
    """

    type: Literal["struct"] = "struct"
    """固定为 ``"struct"``。
    """
    data: Any
    """JSON 兼容结构数据（构造校验，否则 ``ValueError``）；程序方直读，
    adapter 恒投影为 ``json.dumps(ensure_ascii=False)`` 文本。
    """

    def __post_init__(self) -> None:
        # 校验 data 为 JSON 兼容（json.dumps 可序列化），违反 → ValueError
        # （作者 bug 的诚实失败点）
        try:
            json.dumps(self.data)
        except TypeError as e:
            raise ValueError(f"StructBlock.data must be JSON-compatible: {e}") from e


@dataclass(kw_only=True)
class MediaBlock(ContentBlock):
    """媒体内容块基类：``data`` base64 必填且为权威表示。

    .. rubric:: 功能介绍

    ``image`` / ``video`` / ``audio`` / ``file`` 四种媒体块的共同字段
    基座。无论原始来源是文件路径、URL 还是内存 buffer，进入消息层时统一
    转换为 base64，``data`` 持有权威表示；``name`` （必填，缺省合成）与
    ``mime_type`` 只是元数据，不替代 ``data``。

    统一 base64 的意义：消息层不感知“文件最初从哪来”（消息对象自包含、
    可序列化、可持久化，不会隐式读盘），adapter 从统一 base64 出发做各
    API 转换（base64 内联 / URL / multipart）。

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

    .. rubric:: 行为要点

    - ``data`` 必填：构造时不校验 base64 合法性（非法数据由 adapter 或
      Provider 报错），但缺失 ``data`` 是契约违反。
    - ``name`` 必填（恒非 ``None``）：填充链为 显式 ``name`` > ``path``
      文件名 > 合成 ``<sha256(data)[:12]>.<ext>`` （ext 由 MIME 反推，
      MIME 未知 → ``.bin``）。规则全局统一：不只工具结果转换层
      （``flowing.tool.normalize_output``），adapter 从 provider 响应 /
      用户上传建媒体块时同守。
    - 消息层不做文件大小检查：超大 base64 字符串直接存储；大小治理分层
      （前端校验 / ``on_enqueue`` 钩子 / adapter 的
      ``ContextLengthError``）。
    - ``mime_type`` 可选：缺失时 adapter 按扩展名或内容魔数推断；显式
      指定优先。
    - 不做病毒扫描、不做格式转码、不做缩略图生成。

    .. seealso::
       :class:`flowing.message.ImageBlock`、
       :class:`flowing.message.FileBlock`、
       :class:`flowing.message.ContentBlock`。
    """

    data: str
    """媒体内容的 base64 编码，必填，权威表示。路径引用模式不存在。
    """
    name: str
    """文件名等元数据，必填（恒非 ``None``）；不替代 ``data``。缺省时由
    转换 / 构造层按填充链合成：显式 ``name`` > ``path`` 文件名 >
    ``<sha256(data)[:12]>.<ext>`` （MIME 未知 → ``.bin``）。
    """
    mime_type: str | None = None
    """MIME 类型（可选）；缺失时 adapter 推断，显式指定优先。
    """


@dataclass(kw_only=True)
class ImageBlock(MediaBlock):
    """图像内容块（单帧静态，多模态视觉输入）。

    .. rubric:: 功能介绍

    用户上传图片且希望多模态视觉理解时使用；仅作文件传递（不做图像识别）
    时用 :class:`FileBlock`。映射：Anthropic ``image`` block、OpenAI
    ``image_url`` content part。

    .. rubric:: 行为要点

    - 字段与 :class:`MediaBlock` 一致；``type`` 固定 ``"image"``。
    - 框架不校验数据确实是图像（adapter / Provider 负责）。

    .. seealso::
       :class:`flowing.message.MediaBlock`、
       :class:`flowing.message.VideoBlock`。
    """

    type: Literal["image"] = "image"
    """固定为 ``"image"``。
    """


@dataclass(kw_only=True)
class VideoBlock(MediaBlock):
    """视频内容块（时间维度连续帧）。

    .. rubric:: 功能介绍

    视频输入。多数 Provider 不原生支持视频——降级策略属 adapter / UI
    层（首帧截图、转为文件引用或略过），框架核心只做承载。

    .. rubric:: 行为要点

    - 字段与 :class:`MediaBlock` 一致；``type`` 固定 ``"video"``。
    - 与 :class:`ImageBlock` 的边界：单帧静态用 ``image``；连续帧用
      ``video``。

    .. seealso::
       :class:`flowing.message.MediaBlock`、
       :class:`flowing.message.ImageBlock`。
    """

    type: Literal["video"] = "video"
    """固定为 ``"video"``。
    """


@dataclass(kw_only=True)
class AudioBlock(MediaBlock):
    """音频内容块（需要语音理解时使用）。

    .. rubric:: 功能介绍

    语音 / 音频输入。不支持的 Provider 由 adapter 降级（``<audio>``
    占位文本或语音转文字）；仅传递 ``.mp3`` 等文件而无需理解时用
    :class:`FileBlock`。

    .. rubric:: 行为要点

    - 字段与 :class:`MediaBlock` 一致；``type`` 固定 ``"audio"``。

    .. seealso::
       :class:`flowing.message.MediaBlock`、
       :class:`flowing.message.FileBlock`。
    """

    type: Literal["audio"] = "audio"
    """固定为 ``"audio"``。
    """


@dataclass(kw_only=True)
class FileBlock(MediaBlock):
    """文件内容块（不直接可读的二进制或大型结构化数据）。

    .. rubric:: 功能介绍

    与 tool_call 无绑定关系的文件载体——一条 USER 消息可同时含 ``text``
    与多个 ``file`` block。与媒体块的区别在意图：用户上传图片但只希望
    作为文件处理（不做图像识别）时用 ``file``；tool 结果附带的产物文件
    亦可直接出现于 TOOL 消息 content（摊平形态，如 ``[struct, file]``）。

    .. rubric:: 行为要点

    - ``name`` 必填（文件名是文件块的核心元数据）；``data`` base64 必填
      （路径引用模式不存在）；``mime_type`` 可选。
    - adapter 映射示例：Anthropic 转文本引用或 ``document`` API；OpenAI
      经 ``file`` attachment / ``data`` content part。

    .. seealso::
       :class:`flowing.message.MediaBlock`、
       :class:`flowing.message.StructBlock`。
    """

    type: Literal["file"] = "file"
    """固定为 ``"file"``。
    """


@dataclass
class Message:
    """对象层消息：所有来源信息的统一表示，消息级树的节点。

    .. rubric:: 功能介绍

    任意来源（用户、LLM、工具、外部 Agent、系统事件、扩展）产生的新信息
    统一表示为 ``Message``。它同时是消息级树的节点——``id`` 是节点 id，
    ``parent_id`` 是亲代链；``Agent.current_head_id`` 指向某条消息的 id，
    上下文组装沿 ``parent_id`` 上溯收集路径。

    消息没有 ``role`` 属性（kind 替代 API 层 role，与 Provider 解耦，
    见 :class:`MessageKind`）。消息级树取代了旧 Turn 粒度树：fork /
    压缩 / 恢复都落在任意消息上；代价是历史修改需要 :class:`MessageChain`
    的五 op 支撑。

    .. rubric:: 使用示例

    .. code-block:: python

        msg = Message(
            kind=MessageKind.USER,
            content=[TextBlock(text="帮我查订单 4521")],
            source="chat_input",
            tags=["order"],
        )
        message_id = await agent.enqueue_message(msg)   # 返回 msg.id

    .. rubric:: 行为要点

    - ``id`` 缺省为 ``None``（未指定），由 Agent 边界（入队 / 挂树）
      铸造为按 Agent 的自增序列号；显式给定的一律尊重。
      ``enqueue_message`` 返回 ``msg.id``，``Agent.query()`` 的等待与
      该 id 绑定。
    - ``parent_id`` 由 ``Agent._append_message`` 在挂树时设置（首条消息
      链到 ``current_head_id``，后续链到上一条）；手动构造的消息入队前
      通常为 ``None``。
    - ``turn_end``：由 agent 层在挂树时写入——本条 PROVIDER 消息落盘时
      turn 随之关闭（自然完成或取消 / abort）→ ``True``；与 provider 层
      的 ``ProviderResponse.finish`` 分层（中断的流式没有 finish，但
      turn 照样关闭）。恢复时“最后一个 ``turn_end=True`` 的 PROVIDER
      消息”之后的已落盘消息 = 半截 turn（崩溃撕裂或异常终结；保留不
      续跑，照常进入 LLM 上下文，不截断）。
    - ``partial``：流式中断时置 ``True``，已累积内容保留落盘；正常完成
      的消息恒为 ``False``。
    - ``synthetic``：仅恢复流程合成的占位消息为 ``True`` （孤立 tool_call
      的占位 TOOL 消息），标记“不是真实结果”；其余消息恒为 ``False``。
    - ``tool_call_id`` / ``tool_status``：仅 ``kind=TOOL`` 非 None；
      ``__post_init__`` 双向强制（``kind=TOOL`` 与两字段非 ``None`` 互为
      充要条件，违反抛 ``ValueError``）——把“孤儿结果”消灭在构造点。
      其余 kind 条件字段（``turn_end`` / ``synthetic`` / ``priority``）
      维持文档约定，不追溯校验。
    - ``timestamp``：时区无关（UTC / epoch）——统一排序基准、延迟测量可
      比较、跨机器审计对齐；显示转换由 UI / 日志层负责；是否进入发给
      LLM 的消息记录由 Provider adapter 决定（多数 adapter 不带）。
    - 历史消息不可在应用层直接修改——修改历史走 :class:`MessageChain`
      五 op（持久化手术）或 fork（切分支）。
    - 消息对象不携带执行状态 / 等待标记（等待绑定是纯运行时结构，不落盘）。
    - 兄弟分支无顺序信息（互斥分支）；分支列表 UI 排序用 ``timestamp``，
      树结构不需要显式排序字段。
    - 不变量：树上任意消息的 ``parent_id`` 为 ``None`` （根标记）或指向
      另一条已落盘消息；允许多个根（森林模型）——新根由
      :meth:`MessageChain.branch` 以 ``parent_id=None`` 开启（如压缩换
      链），旧根链完整保留、不再进入上下文。
    - 不变量：一个逻辑 turn = 一条消费消息开始，到一条 ``turn_end=True``
      的 PROVIDER 消息结束（或被 abort）。
    - 不变量：配对锚——同一分支上，PROVIDER 消息 ``ToolCallBlock.id`` 与
      后续 TOOL 消息的 ``tool_call_id`` 1:1 严格成对；孤立调用由恢复合成
      的 ``synthetic`` 占位 TOOL 消息封闭。
    - 副线（``Agent.side_query``）消息从不进树（“副线”是调用路径属性
      而非消息属性）。

    .. seealso::
       :class:`flowing.message.MessageKind`、
       :class:`flowing.message.MessageChain`、
       :class:`flowing.message.MessageQueue`、
       :class:`flowing.agent.TurnContext`。
    """

    kind: MessageKind
    """消息来源（对象层唯一角色判别），见 :class:`MessageKind`。必填。
    """
    content: list[ContentBlock]
    """内容片段列表（不同 type 可交错排列），见 :class:`ContentBlock`。
    必填。TOOL 消息中只装纯内容块（text / struct / 媒体），无协议块。
    """
    tool_call_id: str | None = None
    """配对锚（默认 ``None``）：仅 ``kind=TOOL`` 非 None，值为对应
    PROVIDER 消息 :attr:`ToolCallBlock.id`。``__post_init__`` 双向强制。
    """
    tool_status: Literal["completed", "pending", "blocked", "cancelled", "error"] | None = None
    """工具结果状态五值（默认 ``None``）：仅 ``kind=TOOL`` 非 None。
    用内联 ``Literal`` 而非 import ``flowing.tool.ToolStatus``——保持
    “tool 认识 message、message 不认识 tool”的单向依赖。
    ``__post_init__`` 双向强制。
    """
    id: str | None = None
    """消息 id（默认 ``None`` = 未指定），同时是消息级树的节点 id；
    ``enqueue_message`` 的返回值。

    ``None`` 的消息在进入 Agent 边界（入队 / 挂树 / 回合内预铸）时由
    属主 Agent 铸造——按 Agent 维口的自增序列号（纯数字字符串），
    计数器持久化于 core 状态袋（``message_seq``），跨恢复续接；
    显式给定的 id 一律尊重，不被重铸。
    """
    parent_id: str | None = None
    """消息级亲代链：``None`` = 根标记（森林模型，一棵树允许多个根——新根经
    :meth:`MessageChain.branch` 以 ``None`` 开启）；挂树时由
    ``Agent._append_message`` 设置（首条链到 ``current_head_id``，后续
    链到上一条）。fork 目标、上下文上溯、恢复重建的唯一依据。
    """
    turn_end: bool = False
    """“逻辑 turn 关闭”边界标记（默认 ``False``）；仅 PROVIDER 消息上
    有语义。由 agent 层写入（``_run_turn`` 挂树时：turn 随本条消息关闭
    ——自然完成或取消 / abort → ``True``），与 provider 层的
    ``ProviderResponse.finish`` 分层。恢复时定位完整 turn 边界与
    ``current_head_id`` 锚点的依据。
    """
    partial: bool = False
    """流式中断标记（默认 ``False``）；``True`` 表示该消息内容不完整但
    保留落盘。
    """
    synthetic: bool = False
    """合成占位标记（默认 ``False``）；仅恢复流程为孤立 tool_call 合成的
    占位 TOOL 消息（``tool_status="error"`` + ``TextBlock`` 占位说明）
    为 ``True``。
    """
    source: str = ""
    """二级分类自由字符串（默认 ``""``；框架不枚举，投递方填写），
    典型值 ``"tool_result"`` / ``"scheduled_task"`` / ``"message_to"``。
    """
    tags: list[str] = field(default_factory=list)
    """任意标签列表（默认空），用于分组 / 过滤 / 清洗（如 reminder 清洗）。
    """
    priority: MessagePriority = MessagePriority.NORMAL
    """队列排序依据（默认 ``MessagePriority.NORMAL``）；仅对入队消息有
    调度效果。
    """
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    """产生时间戳（默认构造时的当前 UTC），时区无关；附着于消息供查询 /
    日志 / 审计 / 排序，是否进 LLM 上下文由 adapter 决定。
    """
    usage: Usage | None = None
    """本消息对应的 Provider 实测用量（默认 ``None``）。仅 PROVIDER 消息
    携带：adapter 构造响应消息时附着（``ProviderResponse`` 不携带
    usage——本字段是唯一权威落点），随消息落盘、恢复后仍在。非 PROVIDER
    消息恒为 ``None``；框架核心不读它做计费决策（计费走
    ``TurnResult.token_usage`` 聚合）。
    """

    def __post_init__(self) -> None:
        # 双向强制——kind == MessageKind.TOOL ⟺ tool_call_id 与
        # tool_status 均非 None；违反 → ValueError（孤儿结果消灭在构造点）。
        # 既有 kind 条件字段（turn_end / synthetic / priority）不追溯校验。
        if self.kind is MessageKind.TOOL:
            if self.tool_call_id is None or self.tool_status is None:
                raise ValueError(
                    "kind=TOOL messages must carry both tool_call_id and tool_status"
                )
        elif self.tool_call_id is not None or self.tool_status is not None:
            raise ValueError(
                "tool_call_id / tool_status may only be carried by kind=TOOL messages"
            )


# ---------------------------------------------------------------------------
# 消息 ↔ 持久化行（行格式 schema 的唯一序列化点；Agent._persist_message 复用）
# ---------------------------------------------------------------------------

_BLOCK_TYPES: dict[str, type[ContentBlock]] = {
    "text": TextBlock,
    "thinking": ThinkingBlock,
    "tool_call": ToolCallBlock,
    "struct": StructBlock,
    "image": ImageBlock,
    "video": VideoBlock,
    "audio": AudioBlock,
    "file": FileBlock,
}
"""序列化判别表：行内 content 项的 ``type`` 字段 → 块类。"""


def _block_to_record(block: ContentBlock) -> dict:
    """ContentBlock → 行内 content 项（含 ``type`` 判别字段）。"""
    return asdict(block)


def _block_from_record(data: dict) -> ContentBlock:
    """行内 content 项 → ContentBlock（按 ``type`` 判别字段分派）。"""
    try:
        cls = _BLOCK_TYPES[data["type"]]
    except KeyError:
        raise ValueError(f"unknown content block type: {data.get('type')!r}") from None
    return cls(**data)


def to_record(msg: Message) -> dict:
    """把 ``Message`` 序列化为 ``tree.jsonl`` 的一行（JSON 兼容 dict）。

    .. rubric:: 功能介绍

    消息对象与持久化行映射的序列化点（:func:`from_record` 的逆映射）。
    行格式的字段级 schema 冻结在本函数与 :func:`from_record` 一对中，
    ``Agent._persist_message`` 以本函数为唯一序列化点。

    落盘行格式（JSON 对象）：

    .. code-block:: python

        {
            "type": "message",
            "id": "...",
            "parent_id": None,
            "kind": "user",
            "content": [{"type": "text", "text": "..."}],
            "tool_call_id": None,
            "tool_status": None,
            "turn_end": False,
            "partial": False,
            "synthetic": False,
            "source": "",
            "tags": [],
            "priority": 3,
            "timestamp": "2026-01-01T00:00:00",
            "usage": None,
        }

    .. rubric:: 行为要点

    - ``kind`` 落盘为枚举字符串值；``priority`` 落盘为枚举数值；
      ``timestamp`` 落盘为 ISO 格式（naive UTC）。
    - ``content`` 逐块序列化，每块含 ``type`` 判别字段。
    - ``usage`` 为 ``None`` 或七计数字段（``input`` / ``fresh_input`` /
      ``output`` / ``cache_read`` / ``cache_write`` / ``reasoning`` /
      ``total_tokens``）+ ``raw`` 的 dict（存量行的
      ``"usage": null`` 兼容）。message 不 import providers（单向依赖），
      序列化按 ``Usage`` 的字段名读取，还原端在函数内局部 import。

    .. seealso::
       :func:`from_record`、:meth:`flowing.agent.Agent._persist_message`。
    """
    return {
        "type": "message",
        "id": msg.id,
        "parent_id": msg.parent_id,
        "kind": msg.kind.value,
        "content": [_block_to_record(b) for b in msg.content],
        "tool_call_id": msg.tool_call_id,
        "tool_status": msg.tool_status,
        "turn_end": msg.turn_end,
        "partial": msg.partial,
        "synthetic": msg.synthetic,
        "source": msg.source,
        "tags": list(msg.tags),
        "priority": int(msg.priority),
        "timestamp": msg.timestamp.isoformat(),
        # 鸭子类型读取七字段 + raw，不 import providers
        "usage": None if msg.usage is None else {
            "input": msg.usage.input,
            "fresh_input": msg.usage.fresh_input,
            "output": msg.usage.output,
            "cache_read": msg.usage.cache_read,
            "cache_write": msg.usage.cache_write,
            "reasoning": msg.usage.reasoning,
            "total_tokens": msg.usage.total_tokens,
            "raw": dict(msg.usage.raw),
        },
    }


def from_record(record: dict) -> Message:
    """把 ``tree.jsonl`` 的一行（dict）还原为 ``Message``。

    .. rubric:: 行为要点

    - 要求 ``record["type"] == "message"``，否则抛 ``ValueError``。
    - 逐字段还原：``kind`` / ``priority`` 重建为枚举，``timestamp`` 经
      ``datetime.fromisoformat`` 还原；``usage`` 为 ``None`` 或重建为
      ``flowing.providers.Usage`` （局部 import——message 不 import
      providers 的单向依赖在运行期无环）。

    :raises ValueError: ``record`` 不是消息行（``type`` 字段不是
        ``"message"``），或 ``kind`` / ``priority`` 不属于当前枚举。

    .. seealso::
       :func:`to_record`、:meth:`flowing.agent.Agent._restore`。
    """
    if record.get("type") != "message":
        raise ValueError(f"not a message record: type={record.get('type')!r}")
    raw_usage = record.get("usage")
    usage = None
    if raw_usage is not None:
        # 局部 import 破 message→providers 方向
        from flowing.providers.provider import Usage

        usage = Usage(**raw_usage)
    return Message(
        id=record["id"],
        parent_id=record["parent_id"],
        kind=MessageKind(record["kind"]),
        content=[_block_from_record(b) for b in record["content"]],
        tool_call_id=record["tool_call_id"],
        tool_status=record["tool_status"],
        turn_end=record["turn_end"],
        partial=record["partial"],
        synthetic=record["synthetic"],
        source=record["source"],
        tags=list(record["tags"]),
        priority=MessagePriority(record["priority"]),
        timestamp=datetime.fromisoformat(record["timestamp"]),
        usage=usage,
    )


MEDIA_TOKEN_ESTIMATE: int = 2000
"""单个媒体块的固定 token 估算值。

消息层媒体一律 base64 内联，但其 token 成本由 provider 按图像 / 媒体
规格定档，与 base64 长度无关——按字符数估会失真几个数量级，故
:func:`estimate_message_tokens` 对 :class:`MediaBlock` 及其子类固定计
此常数、不看内容。
"""


def estimate_block_tokens(block: ContentBlock) -> int:
    """估算单个内容块的 token 数（字符启发式，逐块规则的单一来源）。

    .. rubric:: 功能介绍

    :func:`estimate_message_tokens` 的逐块循环体提取——一条消息的估算
    就是其各内容块估算之和。需要块级粒度的观测方（如 ``/context v``
    的分类占比）直接调用本函数，无需为复用规则构造临时消息。

    .. rubric:: 使用示例

    .. code-block:: python

        estimate_block_tokens(TextBlock(text="你好"))   # 纯函数，随时可调用

    .. rubric:: 行为要点

    - 同步纯函数：只读 ``block``，无副作用、无缓存，每次现场求值。
    - 逐块规则：文本类块（``TextBlock.text`` / ``ThinkingBlock.thinking``）
      按字符启发式（ASCII 字符数 ÷ 4 + 非 ASCII 字符数，向上取整）；
      ``ToolCallBlock`` 计 ``name`` 与 ``args`` JSON 序列化后的字符
      启发式之和；``StructBlock`` 计 ``json.dumps(ensure_ascii=False)``
      结果的字符启发式；``MediaBlock`` 族固定
      :data:`MEDIA_TOKEN_ESTIMATE`（不读 base64 ``data``）；未知块类型
      按“无文本内容”计 0。

    .. seealso::

        :func:`estimate_message_tokens` —— 消息级求和（本函数的调用方）。
        :data:`MEDIA_TOKEN_ESTIMATE` —— 媒体块固定估算值。
    """
    if isinstance(block, MediaBlock):
        # 媒体固定估算，不读 base64 data（token 成本由 provider 按规格定档）
        return MEDIA_TOKEN_ESTIMATE
    if isinstance(block, TextBlock):
        return _text_tokens(block.text)
    if isinstance(block, ThinkingBlock):
        return _text_tokens(block.thinking)
    if isinstance(block, ToolCallBlock):
        return (_text_tokens(block.name)
                + _text_tokens(json.dumps(block.args, ensure_ascii=False)))
    if isinstance(block, StructBlock):
        return _text_tokens(json.dumps(block.data, ensure_ascii=False))
    return 0   # 未知块类型按“无文本内容”计 0


def estimate_message_tokens(msg: Message) -> int:
    """估算一条消息的 token 数（字符启发式，仅供上下文窗口预算观测）。

    .. rubric:: 功能介绍

    对消息逐块展开的本地估算：``content`` 各内容块经
    :func:`estimate_block_tokens` 估算后求和（逐块规则以该函数为单一
    来源）。被 :meth:`flowing.agent.Agent.estimate_context_tokens` 用于
    估算——锚点指最近一条携带实测用量（``Message.usage``）的消息，
    估算覆盖锚点之后（或无锚点时全部）路径上的消息。

    字符启发式口径：ASCII 约 4 字符/token、非 ASCII（CJK 等）约 1
    字符/token——中文场景下远优于统一除以 4。不用 tokenizer：估算只
    服务于“该不该压缩、还剩多少余量”这类窗口预算判断（±20% 足够），
    永不用于计费（计费走 ``TurnResult.token_usage`` 聚合，数据源是
    ``Message.usage`` 实测）。

    .. rubric:: 使用示例

    .. code-block:: python

        estimate_message_tokens(msg)   # 纯函数，随时可调用

    .. rubric:: 行为要点

    - 同步纯函数：只读 ``msg``，无副作用、无缓存，每次现场求值。
    - 逐块规则：对 ``content`` 逐块调用 :func:`estimate_block_tokens`
      求和（规则细节以该函数为单一来源）。
    - 不计入：消息元数据（``id`` / ``source`` / ``tags`` 等）不进 LLM
      上下文，不参与估算。
    - 边缘情况：空 ``content`` 返回 0。

    .. seealso::
       :data:`MEDIA_TOKEN_ESTIMATE`、
       :func:`estimate_block_tokens`、
       :meth:`flowing.agent.Agent.estimate_context_tokens`。
    """
    return sum(estimate_block_tokens(block) for block in msg.content)


def _text_tokens(text: str) -> int:
    """字符启发式：ASCII ≈ 4 字符/token，非 ASCII（CJK 等）≈ 1 字符/token，向上取整。"""
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    return non_ascii + math.ceil((len(text) - non_ascii) / 4)


class MessageQueue:
    """Agent 的消息队列：优先级排序 + 同优先级 FIFO 的异步队列。

    .. rubric:: 功能介绍

    每个 Agent 一个独立队列（``Agent._message_queue``），是外部消息进入
    逻辑 turn 循环的唯一通道。工作循环经 ``Agent._dequeue()`` 消费——
    默认批次为“队首 INTERRUPT/STEER 连续段 + 其后第一条非紧急消息”
    （队首即非紧急时批次为单条），drain / 合并 / 按来源分组等更宽的
    批量策略由覆写 ``_dequeue()`` 实现，本类提供 ``drain_all`` /
    ``take_while`` 等批量取出原语支撑默认批次与覆写。

    队列只承诺最小调度语义：按 ``MessagePriority`` 数值升序、同级按入队
    顺序 FIFO。防饿死、来源加权等策略不在框架核心。忙时不拒绝：活跃
    turn 中入队的消息自然排队，turn 结束后被消费；入队即保证会被常驻
    工作循环消费（无需“入队触发”逻辑）。

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

    .. rubric:: 行为要点

    - 排序：``priority`` 升序（``INTERRUPT`` 最先）；同优先级 FIFO
      （入队序号，单调递增，无需比较 ``timestamp``）。
    - 进队列的 kind：``USER`` / ``EVENT`` / ``PEER`` / ``SYSTEM`` /
      ``SUBAGENT`` （异步工具最终结果以 ``EVENT`` 入队）；
      ``PROVIDER`` 永不入队。本类不校验 kind——投递纪律由调用方与
      ``Agent.enqueue_message`` 的钩子链负责。
    - 不防低优先级饿死；本类自身不持久化（队列待消费消息的恢复语义属
      持久化规约）。
    - 并发模型：单进程 asyncio；``enqueue`` 是无等待的同步操作（队列
      无界），``dequeue`` / ``drain_all`` 是协程。

    .. seealso::
       :class:`flowing.message.MessagePriority`、
       :meth:`flowing.agent.Agent._dequeue`、
       :meth:`flowing.agent.Agent.enqueue_message`、
       :meth:`flowing.agent.Agent.cancel_queued`、
       :meth:`flowing.agent.Agent.set_queued_priority`。
    """

    def __init__(self) -> None:
        # 有序 list + (priority 数值, 自增入队序号) 二分插入排序；
        # remove / set_priority 线性扫描定位（队列规模小，机制从简）；
        # 阻塞唤醒用 asyncio.Event（enqueue 置位、取空后清位）。
        self._items: list[tuple[int, int, Message]] = []
        self._seq = itertools.count()
        self._not_empty = asyncio.Event()

    def enqueue(self, msg: Message) -> None:
        """入队一条消息（无等待、无界）。

        .. rubric:: 功能介绍

        将 ``msg`` 按（``priority``, 入队序号）插入排序位置。
        ``Agent.enqueue_message`` 在 ``on_enqueue`` 钩子之后调用本
        方法。

        .. rubric:: 行为要点

        - 同步、无阻塞、立即返回；入队即保证会被常驻工作循环消费。
        - 不修改 ``msg`` （``id`` / ``timestamp`` 在构造时已就位）。
        - 不做容量限制、不做去重、不做内容审核（审核走钩子）。

        .. seealso::
           :meth:`dequeue`、:meth:`set_priority`。
        """
        # 入队序号单调递增，同优先级 FIFO tie-break；seq 唯一保证
        # 元组比较不会回落到 Message 本身（Message 不可比较）
        bisect.insort(self._items, (int(msg.priority), next(self._seq), msg))
        self._not_empty.set()

    async def wait_not_empty(self) -> None:
        """阻塞等待队列变为非空（不取消息）。

        .. rubric:: 功能介绍

        “等消息”与“取消息”的拆分原语：``Agent._dequeue`` 的默认实现
        用它保证“队列确实非空”才进入取批与 ``on_dequeue`` 派发，消除
        空转期的无效动作。enqueue 时唤醒等待者。

        .. rubric:: 行为要点

        - 队列非空时立即返回；为空时挂起至下一次入队。
        - 返回后 ``on_dequeue`` 仍可能把批次变换为空（丢弃本批），
          重试循环由调用方（``_dequeue``）承担。

        .. seealso:: :meth:`dequeue`、:meth:`dequeue_nowait`
        """
        while not self._items:
            await self._not_empty.wait()

    def dequeue_nowait(self) -> Message | None:
        """非阻塞取出优先级最高的一条消息；队列空返回 ``None``。

        与 :meth:`wait_not_empty` 配套：``Agent._dequeue`` 在
        ``take_while`` 取完紧急段后用它取“其后第一条非紧急消息”——
        无紧急前缀的批次就是本方法取出的单条。
        """
        if not self._items:
            return None
        _, _, msg = self._items.pop(0)
        if not self._items:
            self._not_empty.clear()
        return msg

    async def dequeue(self) -> Message:
        """阻塞取出优先级最高的一条消息（一次一条的取出原语）。

        .. rubric:: 功能介绍

        队列空时挂起等待，直到有消息入队。本方法是“等消息 + 取消息”合体
        的便捷原语；``Agent._dequeue()`` 的默认实现不使用它——默认批次
        语义（队首紧急连续段 + 首条非紧急 + ``on_dequeue`` 派发）需要
        :meth:`wait_not_empty` + :meth:`take_while` /
        :meth:`dequeue_nowait` 的拆分组合（见
        :meth:`flowing.agent.Agent._dequeue`）。不需要批次语义的直接
        消费方（如自定义 ``_dequeue()`` 覆写）可用本方法。

        .. rubric:: 行为要点

        - 前置条件：在事件循环中调用（工作循环 Task 内）。
        - 后置条件：返回的消息已从队列移除；等待者绑定由工作循环在出队
          时完成，不在本方法内。
        - 边缘情况：Agent ``destroy()`` 取消工作循环后，挂起的
          ``dequeue`` 随 Task 取消而结束，不返回、不泄漏。

        .. seealso::
           :meth:`wait_not_empty`、:meth:`dequeue_nowait`、
           :meth:`drain_all`、:meth:`take_while`、
           :meth:`flowing.agent.Agent._dequeue`。
        """
        while True:
            msg = self.dequeue_nowait()
            if msg is not None:
                return msg
            await self._not_empty.wait()

    async def drain_all(self) -> list[Message]:
        """一次性取出当前所有排队消息（drain 覆写原语）。

        .. rubric:: 功能介绍

        按出队顺序（优先级 + FIFO）返回并移除调用时刻已在队列中的全部
        消息；不为等待新消息而阻塞（队列为空时返回空列表）。

        .. rubric:: 行为要点

        - 用于 ``_dequeue()`` 覆写实现“合并回合”：多条消息一次吸收，
          回合收尾时每条消息的等待者共享同一 ``TurnResult`` 并被全部
          resolve（核心通用收尾天然兼容批量）。
        - 边缘情况：调用与“另一生产者正在 enqueue”交错时，只保证取出
          调用时刻的存量；新入队者留给下一次。

        .. seealso::
           :meth:`dequeue`、:meth:`flowing.agent.Agent._dequeue`。
        """
        # 只取调用时刻存量（交错的新入队者留给下一次）；有序 list 本身即出队序
        items = self._items
        self._items = []
        self._not_empty.clear()
        return [msg for _, _, msg in items]

    def take_while(self, predicate: Callable[[Message], bool]) -> list[Message]:
        """从队首按出队顺序连续取出满足条件的消息（合并覆写原语）。

        .. rubric:: 功能介绍

        标准 take-while 语义：从队首开始，按出队顺序逐条检查，连续取出
        满足 ``predicate`` 的消息，遇到首个不满足者停止；不阻塞。

        .. rubric:: 行为要点

        - 同步、立即返回（可能为空列表）。
        - 典型用途：``_dequeue()`` 覆写中的按来源 / 按窗口合并
          （``lambda m: m.source == first.source``）。
        - 边缘情况：队首消息即不满足 → 返回空列表，队列不变；队列被取
          空 → 返回全部已取消息。

        .. seealso::
           :meth:`drain_all`、:meth:`flowing.agent.Agent._dequeue`。
        """
        taken: list[Message] = []
        while self._items and predicate(self._items[0][2]):
            taken.append(self._items.pop(0)[2])
        if not self._items:
            self._not_empty.clear()
        return taken

    def peek(self, priority: MessagePriority | None = None) -> Message | None:
        """窥看下一条将被出队的消息，不移除（观测原语）。

        .. rubric:: 功能介绍

        按出队顺序（优先级 + 同级 FIFO）返回队首消息；指定 ``priority``
        时返回该优先级带内 FIFO 队首（用于“只看某一带”的观察，如 UI
        分列预览各优先级各一条）。队列为空、或指定优先级带内无消息时
        返回 ``None``。

        ``dequeue`` 阻塞且移除、``drain_all`` / ``take_while`` 批量移除
        ——队列此前没有“只看不动”的入口；``peek`` 补齐观测面，与
        :meth:`__len__` 同属观测原语。

        .. rubric:: 行为要点

        - 同步、不阻塞、立即返回；不移除消息，队列状态不变。
        - 返回的消息仍在队列中：后续 ``dequeue`` / ``set_priority`` /
          ``remove`` 照常作用于它；不得将返回值视为“已占有”。
        - 无同步语义：跨 Task 观察时是瞬时值，不得据此做互斥决策
          （同 :meth:`__len__` 的观测约定）。
        - ``priority`` 过滤只选带内队首，不跨带比较；``None`` 表示全局
          队首（即下一次 ``dequeue`` 会取到的那条）。

        .. seealso::
           :meth:`dequeue`、:meth:`__len__`、:class:`MessagePriority`。
        """
        if priority is None:
            return self._items[0][2] if self._items else None
        # 带内队首：只选该优先级带内 FIFO 第一条，不跨带比较
        for prio, _, msg in self._items:
            if prio == int(priority):
                return msg
        return None

    def remove(self, message_id: str) -> bool:
        """按 id 撤回一条未出队的消息（支撑 ``Agent.cancel_queued``）。

        .. rubric:: 功能介绍

        从队列中移除指定消息；返回是否找到并移除。``Agent.cancel_queued``
        在本方法返回 ``True`` 时联动 resolve 对应等待者（cancelled），
        调用方不挂起。

        .. rubric:: 行为要点

        - 已出队（正在或已被回合消费）或不存在的 id → 返回 ``False``，
          无副作用。
        - 不影响正在执行的回合（撤回的是排队消息，不是执行）。

        .. seealso::
           :meth:`flowing.agent.Agent.cancel_queued`。
        """
        for i, (_, _, msg) in enumerate(self._items):
            if msg.id == message_id:
                del self._items[i]
                if not self._items:
                    self._not_empty.clear()
                return True
        return False

    def set_priority(self, message_id: str, priority: MessagePriority) -> bool:
        """按 id 重设一条未出队消息的优先级，队列立即按新值重排。

        .. rubric:: 功能介绍

        找到队列中指定消息，原地改写 ``msg.priority`` 并按新值重新定位
        到（``priority``, 入队序号）排序点。支撑
        :meth:`flowing.agent.Agent.set_queued_priority`。

        优先级是入队后仍可变的调度属性（典型场景：用户催办、Guardrail
        升级提醒）。重排保留原入队序号：语义为“这条消息从入队起就该是
        新优先级”，而非“现在新来的一条高优先级消息”；它在新优先级带
        内的 FIFO 位置由原入队早晚决定。

        .. rubric:: 使用示例

        .. code-block:: python

            # 用户催办：把排队中的消息提升为 HIGH
            agent._message_queue.set_priority(msg_id, MessagePriority.HIGH)

        .. rubric:: 行为要点

        - 找到未出队消息 → 原地改写 ``msg.priority``、按（新 ``priority``,
          原入队序号）重新定位，返回 ``True``。
        - 已出队（正在或已被回合消费）或不存在的 id → 返回 ``False``，
          无副作用。
        - 同步、无等待；不改写 ``id`` / ``timestamp`` / 等待者绑定。
        - 不影响正在执行的回合；自身不落盘（队列不持久化排序状态，恢复
          时按消息上附着的 ``priority`` 值重建——原地改写使恢复语义自动
          正确）。

        .. seealso::
           :meth:`enqueue`、:meth:`remove`、:class:`MessagePriority`、
           :meth:`flowing.agent.Agent.set_queued_priority`。
        """
        for i, (_, seq, msg) in enumerate(self._items):
            if msg.id == message_id:
                del self._items[i]
                msg.priority = priority
                # 保留原入队序号：在新优先级带内按原入队早晚定位
                bisect.insort(self._items, (int(priority), seq, msg))
                return True
        return False

    def __len__(self) -> int:
        """当前排队消息数（观测用途，无同步语义）。

        .. rubric:: 行为要点

        - 单事件循环内是调用时刻的精确值；跨 Task 观察时是瞬时值，不得
          据此做互斥决策（队列无锁，依赖单循环串行）。
        """
        return len(self._items)

    def is_empty(self) -> bool:
        """队列是否为空（观测用途，无同步语义）。

        .. rubric:: 行为要点

        - 与 :meth:`__len__` 同口径的观测原语：``is_empty()`` 等价于
          ``len(self) == 0``；单事件循环内是调用时刻的精确值，跨 Task
          观察时是瞬时值，不得据此做互斥决策。
        - 典型用途：回合收尾期判断“是否已有待消费消息”——非空时工作
          循环自然开下一回合，外部续跑策略（如 prompt until）无需再
          入队导向消息。
        """
        return not self._items


class MessageChain:
    """消息级树的任意手术入口（``Agent.chain``）：写手术五 op 最小完备集 + 只读 ``get``/``walk``。

    .. rubric:: 功能介绍

    对已落盘 / 在树上的历史消息做增、删、改、重连的统一入口。内存消息链
    是唯一权威——op 直接修改内存中的 ``Agent._messages`` 映射与
    ``parent_id`` 链，随后向 ``tree.jsonl`` append 一条变更记录（运行期
    零截断、零重写）；物理重写延迟到压缩期。

    消息产生即落盘，手术直接作用于已落盘历史：删除中间消息时，tombstone
    （删除标记行）把“分散的逐行重写”变成“运行期 append 一条标记 +
    压缩期一次整体重写”。

    一切内容走持久化路径：recap / reminder 等“临时上下文”也由本类挂上、
    用完 ``remove`` 擦除；回合开头的附加式注入走 ``before_turn`` 的
    ``TurnContext.pending_messages`` （同样随批次挂树持久化）。

    读路径两个只读 op：:meth:`get` 按 id 反查单条；:meth:`walk` 从指定
    消息沿 ``parent_id`` 上溯到根（``Agent._assemble_context`` 等内部
    消费路径与本 op 同口径）。两者都是内存读，不落盘、不派发钩子。

    .. rubric:: 使用示例

    .. code-block:: python

        # 单条增：在 m5 之后插入一条 SYSTEM 提醒（永久）
        agent.chain.insert("m5", Message(kind=MessageKind.SYSTEM,
                                         content=[TextBlock(text="...")]))

        # 单条删：删除一条消息（子树不级联）
        agent.chain.remove("m7")

        # 子树整体重连到另一分支
        agent.chain.reparent("m8", to="m3")

        # 按 id 反查单条
        msg = agent.chain.get("m5")

        # 回放活跃分支（head → 根，逆时间序；head 是 Agent 层游标，显式传入）
        for msg in agent.chain.walk(agent.current_head_id):
            ...

    .. rubric:: 行为要点

    写手术五 op 语义总表：

    .. list-table::
       :header-rows: 1

       * - op
         - 语义
         - 影响范围
       * - ``insert``
         - 单条增
         - 新增消息 + 调整前后邻接（既有子消息重挂其下）
       * - ``branch``
         - 单条增分支
         - 新增消息挂到指定 parent 下（``None`` = 开新根），不动既有子消息
       * - ``remove``
         - 单条删
         - 仅该消息，子树不自动级联
       * - ``update``
         - 单条改
         - 仅内容，不动链
       * - ``reparent``
         - 子树重连
         - 该消息及其整个子树移动到新位置

    - 五 op 正交，任意手术由它们组合（最小完备集）。``branch`` 是
      ``insert`` 的补充——``insert`` 在分叉点会把多个子分支合并到新消息
      之下，只想新增平行分支时用 ``branch``；``branch(None, msg)`` 是
      森林模型下唯一的持久化开根入口（压缩换链等场景，见 :meth:`branch`）。
    - 每个 op = 改内存链 + append 一条变更记录行（insert 为新消息行 +
      邻接调整记录；branch 仅新消息行；remove 为 tombstone
      ``{"type": "tombstone", "id": ...}``；update / reparent 为对应变更行）。
    - 级联规则：``remove`` 不级联——删除带子树的消息会留下亲代链指向不存在
      节点的孤儿子树；正确做法是先对子树逐条 :meth:`reparent` 到新亲
      节点、再 ``remove`` （定式）。
    - 不自动移动 ``current_head_id``：手术目标是历史结构，head 切换是
      ``Agent.fork`` 的职责；删除 / 重连当前 head 或其上溯路径上的消息
      属于调用方责任（需要“删除当前 head 并回退到亲节点”的便捷语义用
      ``Agent.remove`` / ``Agent.pop``）。
    - 不变量：op 完成后内存链与“文件重放结果”一致（重放变更记录必得
      同一权威链）。
    - 压缩（清理 tombstone、重写尾部）由持久化层
      （:class:`flowing.persistence.FileRecordStore` 的 drain 任务）在
      “队列排空后且 tombstone 数量 ≥ 阈值（默认 256）”时自主触发，
      不是本类方法的同步副作用。
    - 不做“级联删除”“自动 fork”“自动更新 head”等便利策略——策略在
      应用层；不提供批量 op 糖衣（批量 = 循环调用五 op，变更记录逐条
      append）。

    .. seealso::
       :meth:`flowing.agent.Agent.fork` （切换 head，与手术正交——手术改
       结构，fork 改视角）、
       :meth:`flowing.agent.Agent._persist_message` /
       :meth:`flowing.agent.Agent._persist_tree_record` （两条落盘通道）、
       :class:`flowing.persistence.FileRecordStore` （压缩的落盘细节）。
    """

    _agent: "Agent"
    """属主 Agent 反向引用（``Agent.__init__`` 以 ``MessageChain(self)``
    传入）——写 op 直接操作 ``Agent._messages``，落盘经
    ``Agent._persist_message`` （新消息行）与 ``Agent._persist_tree_record``
    （变更记录行）同步提交；读 op（``get`` / ``walk``）同样读该映射。
    内部 API，不属稳定契约。
    """

    def __init__(self, agent: "Agent") -> None:
        """构造消息链：绑定属主 Agent。

        通常不直接实例化——应用层经 ``Agent.chain`` 访问本类实例。

        :param agent: 属主 Agent（各 op 直接操作其 ``_messages`` 映射，
            写 op 落盘经其 ``_persist_message`` / ``_persist_tree_record``）。
        """
        self._agent = agent

    def get(self, msg_id: str) -> Message:
        """按 id 反查消息（只读）。

        .. rubric:: 行为要点

        - 内存读：直接查属主 Agent 的 ``_messages`` 映射，O(1)、不触碰
          持久化层；已被 :meth:`remove` 删除（tombstone）的消息不在内存
          映射中，按不存在处理。
        - 对不存在的 id 抛 ``KeyError``（与 :meth:`remove` 同口径——
          重复 / 悬空引用不是静默成功）。
        - 返回树中实况对象（非副本）：读随意，改内容请走写 op
          （直接改对象会绕过落盘与不变量）。

        :param msg_id: 消息 id。
        :return: 该 id 的消息对象。
        :raises KeyError: ``msg_id`` 不存在于消息树（或已被删除）。

        .. seealso:: :meth:`walk` —— 沿亲链的连续上溯遍历。
        """
        return self._agent._messages[msg_id]

    def walk(self, from_id: str | None) -> Iterator[Message]:
        """从指定消息沿 ``parent_id`` 链上溯到根（只读遍历）。

        .. rubric:: 功能介绍

        “活跃分支回放”的正式读路径：``Agent._assemble_context`` 与
        :meth:`flowing.agent.Agent.estimate_context_tokens` 的消息路径
        收集与本 op 同口径（内部已收敛为调用本方法）。

        .. rubric:: 行为要点

        - 产出 ``from_id`` → 根的逆时间序（含 ``from_id`` 本身）；要
          根 → 起点的时间序，调用方自行反转（``list(...)[::-1]``）。
        - 本方法**不感知游标**：head 是 Agent 层状态
          （``Agent.current_head_id``），回放活跃分支须显式传——
          ``agent.chain.walk(agent.current_head_id)``。
        - ``from_id=None`` 或起点 id 不存在 → 空迭代（对应空树 /
          游标悬置情形，不抛错）。
        - 孤儿断点容忍：上溯途中 ``parent_id`` 指向不存在的消息
          （中间消息已被 :meth:`remove` 而子树未先 reparent）即终止，
          与 ``Agent._assemble_context`` 同口径。
        - 只沿亲链上溯，不分叉——分支枚举不在本类提供面内；产出的是
          树中实况对象（改动请走写 op）。

        :param from_id: 起点消息 id（必传）；``None`` 表示空迭代。
        :return: 迭代器，依次产出起点到根的消息（逆时间序）。

        .. seealso:: :meth:`get` —— 单条反查；
            :meth:`flowing.agent.Agent.fork` —— 游标切换（与本 op 正交）。
        """
        cursor = from_id
        while cursor is not None:
            msg = self._agent._messages.get(cursor)
            if msg is None:
                break   # 起点缺失或孤儿链断点：上溯到断点即终止
            yield msg
            cursor = msg.parent_id

    def insert(self, after_id: str, msg: Message) -> str:
        """单条增：在 ``after_id`` 之后插入一条消息，返回新消息 id。

        .. rubric:: 功能介绍

        将 ``msg`` 挂到 ``after_id`` 之下（``msg.parent_id = after_id``），
        并调整前后邻接：``after_id`` 原有的直接子消息重挂到新消息之下
        （各自子树随之整体移动，其内部链不变）。若 ``after_id`` 无子消息，
        等价于追加一个新分支。

        “单点 op、邻接调整”使线性链上的插入保持链序直觉：``m1 → m2``
        上 ``insert("m1", x)`` 得 ``m1 → x → m2``。分支点上插入会把多个
        子分支合并到新消息之下——这是确定性规则而非特例处理；只想新增
        平行分支时用 :meth:`branch`。

        .. rubric:: 行为要点

        - 前置条件：``after_id`` 在树中存在；``msg.id`` 不与现有节点
          冲突（``msg.id`` 缺省时由框架分配）。
        - 后置条件：``msg`` 成为 ``after_id`` 的直接子消息且（若原有子
          消息）成为它们的亲代消息；``msg`` 落盘（append 新消息行）+ 邻接
          调整记录 append。
        - 插入当前 head 之后不移动 ``current_head_id`` （head 切换是
          ``Agent.fork`` 的职责）。
        - 不校验 ``msg.kind`` （任何 kind 的消息都可成为历史节点）。

        :return: 新消息的 id。
        :raises KeyError: ``after_id`` 不存在于消息树。
        :raises ValueError: ``msg.id`` 与现有节点冲突。

        .. seealso::
           :meth:`remove`、:meth:`reparent`、
           :meth:`flowing.agent.Agent.fork`。
        """
        # 宿主 Agent 引用见类 docstring ``_agent`` 字段；op 经其
        # _persist_message / _persist_tree_record 同步提交（write-behind 排队即返）。
        if after_id not in self._agent._messages:
            raise KeyError(after_id)
        if msg.id is None:
            msg.id = self._agent._next_message_id()   # 未显式指定 → 属主 Agent 铸造自增 id
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
        self._agent._persist_message(msg)   # 新消息行（同步提交）
        for cid in rehung:   # 邻接调整：每个被重挂的子消息一条 move 变更行
            self._agent._persist_tree_record(
                {"type": "move", "id": cid, "parent_id": msg.id})
        return msg.id

    def branch(self, parent_id: str | None, msg: Message) -> str:
        """单条增分支：把 ``msg`` 挂到指定 parent 下（``None`` = 开新根），返回新消息 id。

        .. rubric:: 功能介绍

        将 ``msg`` 直接挂为 ``parent_id`` 的新子消息（``msg.parent_id =
        parent_id``），不调整任何既有子消息——与 :meth:`insert` 的“既有
        子消息重挂到新消息之下”规则正交。``parent_id=None`` 时 ``msg``
        成为新根（森林模型的开根入口，见模块 docstring）。

        ``insert`` 在分叉点的合并语义对“新增平行分支”（fork 分支上补
        一条新消息、并行探索线等）是错的；``branch`` 提供无语义陷阱的
        纯挂接原语。``None`` 开根使“上下文压缩换链（摘要作为新根开新链，
        旧树完整保留）”等场景获得唯一的持久化开根入口——不新增
        ``add_root`` 之类同义方法，不引入虚拟根节点。

        .. rubric:: 行为要点

        - 前置条件：``parent_id`` 为 ``None`` （开新根）或在树中存在；
          ``msg.id`` 不与现有节点冲突（缺省时由框架分配）。
        - 后置条件：``parent_id`` 非 ``None`` 时，``msg`` 成为其直接子
          消息，既有子消息的 ``parent_id`` 与位置均不变；``parent_id``
          为 ``None`` 时，``msg.parent_id is None``，成为新的根，既有各
          根链不受影响。落盘仅 append 新消息行（无邻接调整记录）。
        - 不移动 ``current_head_id`` （开新根后切 head 走
          :meth:`flowing.agent.Agent.fork`）；不校验 ``msg.kind``。

        :return: 新消息的 id（调用方用于后续 ``reparent`` / ``remove``
            或 fork 目标）。
        :raises KeyError: ``parent_id`` 非 ``None`` 且不存在于消息树。
        :raises ValueError: ``msg.id`` 与现有节点冲突。

        .. seealso::
           :meth:`insert` （分叉点的合并语义对偶）、
           :meth:`flowing.agent.Agent.fork` （切到新分支 / 新根的视角操作
           ——fork 只切视角，从不创建节点）。
        """
        # 宿主 Agent 引用见类 docstring ``_agent`` 字段。
        if parent_id is not None and parent_id not in self._agent._messages:
            raise KeyError(parent_id)
        if msg.id is None:
            msg.id = self._agent._next_message_id()   # 未显式指定 → 属主 Agent 铸造自增 id
        if msg.id in self._agent._messages:
            raise ValueError(msg.id)
        msg.parent_id = parent_id   # None = 开新根（森林模型）
        self._agent._messages[msg.id] = msg
        self._agent._persist_message(msg)   # 仅新消息行，无邻接调整（同步提交）
        return msg.id

    def remove(self, msg_id: str) -> None:
        """单条删：仅删除该消息；直接子消息自动重挂到亲节点（与 insert 对称）。

        .. rubric:: 功能介绍

        将 ``msg_id`` 从权威链中删除，同时**照顾子节点**（与 :meth:`insert`
        的邻接调整对称）：先把该消息的**直接子消息**
        的 ``parent_id`` 改为被删消息的亲节点（``None`` = 提升为新根）——
        各自子树随之整体移动，链保持连续、不留孤儿；每个被重挂的直接子
        写一条 ``move`` 变更行（同步落盘，恢复按行序应用，含提升为根）。
        最后移除该节点内存项并提交墓碑变更行
        （``{"type": "tombstone", "id": ...}``）；物理删除延迟到压缩期。

        .. rubric:: 行为要点

        - 只删除该节点本身——子树不随删（被删消息的**直接子**被重挂，
          它们的后代不动）；需要整棵删除时，先 :meth:`reparent` 子树到
          别处、或逐条 ``remove``。
        - 重挂目标 = 被删消息的亲节点：删除**尾部消息**是纯截断语义
          （无子，不产生 move）；删除**中间消息**由“逐行重写”降为
          “N 条 move + 1 条 tombstone”，运行期 O(子数)。
        - 对不存在的 id 抛 ``KeyError`` （重复删除不是静默成功）。
        - 不移动 ``current_head_id``；删除 head 上溯路径上的消息属于调用
          方责任。需要“删除当前 head 并回退到亲节点”时，请使用
          ``Agent.remove`` / ``Agent.pop`` （Agent 层负责 head 维护）。
        - **配对警示**：删除一条 TOOL 结果消息或含 ``ToolCallBlock`` 的
          PROVIDER 消息会重新打开配对（tool_call 与结果不再 1:1）——树内
          成对是不变量，本方法不自动封闭；调用方须随后自行补封闭（如
          再 ``insert`` 一条结果消息），否则装配的配对断言
          （``UnpairedToolCallError``）会响亮失败。

        :raises KeyError: ``msg_id`` 不存在于消息树（或已被删除）。

        .. seealso::
           :meth:`reparent`、:meth:`insert`。
        """
        # 宿主 Agent 引用见类 docstring ``_agent`` 字段。
        if msg_id not in self._agent._messages:
            raise KeyError(msg_id)
        removed = self._agent._messages[msg_id]
        parent = removed.parent_id
        # 邻接保持（与 insert 对称）：直接子重挂到被删消息的亲节点
        rehung = [cid for cid, cm in self._agent._messages.items()
                  if cm.parent_id == msg_id]
        for cid in rehung:
            self._agent._messages[cid].parent_id = parent
            self._agent._persist_tree_record(
                {"type": "move", "id": cid, "parent_id": parent})   # move 变更行（同步提交）
        del self._agent._messages[msg_id]  # 内存链移除本节点（子树不级联：只删本节点）
        self._agent._persist_tree_record(
            {"type": "tombstone", "id": msg_id})   # 墓碑行（同步提交）

    def remove_by_tags(self, tags: set[str]) -> int:
        """按 tags 过滤批量删除（recap / reminder 等临时注入的擦除入口）。

        .. rubric:: 功能介绍

        遍历消息树，删除满足以下任一条件的消息，逐条走 :meth:`remove`
        （内存移除 + 墓碑行），返回删除条数：

        - 消息自身 ``Message.tags`` 与给定 ``tags`` 相交；
        - 消息任一 ``content`` block 的 ``tags`` 与给定 ``tags`` 相交。

        支撑“临时注入、响应后清洗”场景：``after_turn`` 钩子内按 tags
        擦除本回合附加的提醒消息，保证不累积到后续轮次。

        .. rubric:: 行为要点

        - 匹配：以上两条规则任一命中即删除；``tags`` 为空集 → 不删任何
          消息（返回 0）。
        - **删除顺序 = 消息链线性顺序从后往前**：按消息产生序
          （``_messages`` 键序，父恒先于子产生）逆序逐条
          :meth:`remove`——子先删、父后删。这样删除父节点时其后代中的
          待删者已清除，只需把幸存的直接子重挂到父节点的亲节点（一步到
          位、无中间挪动），与 :meth:`remove` 的邻接保持语义一致。
        - 逐条 :meth:`remove`：只删本节点（不级联子树）+ 墓碑行 + 直接
          子自动重挂（每个重挂子一条 move 行）；本方法只遍历现存消息，
          天然不会因 id 不存在抛 ``KeyError``。
        - 不直接移动 ``current_head_id``；若按 tags 删除的消息中包含当前
          head，调用方应使用 ``Agent.remove_by_tags`` （Agent 层负责 head
          维护）。不触碰 ``pending_messages`` （本方法作用于已挂树的消息）。

        :return: 删除的消息条数。

        .. seealso::
           :meth:`remove`、:meth:`flowing.agent.Agent.remove_by_tags`。
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
        # 从后往前（子先删、父后删）：父恒先于子产生 → 逆产生序即“链尾向根”
        for mid in reversed(to_remove):
            self.remove(mid)
        return len(to_remove)

    def update(self, msg_id: str, content: list[ContentBlock]) -> None:
        """单条改：仅替换消息内容，不动链。

        .. rubric:: 功能介绍

        替换 ``msg_id`` 的 ``content`` 列表（如编辑历史中的一条用户消息、
        修正一条 SYSTEM 注入）；``parent_id`` / ``kind`` / ``id`` 等其余
        字段不变，前后邻接不变。

        .. rubric:: 行为要点

        - 运行期副作用：改内存 + append update 变更记录；压缩期随尾部
          重写固化。
        - 不允许改 ``kind`` / ``parent_id`` （改结构用 :meth:`reparent`，
          改身份等于删除 + 插入的组合）；不递归校验新 content 的合法性
          （block 排列合法性是 adapter 组装时的职责）。
        - 更新一条 PROVIDER 消息的 content 不会自动重算 ``turn_end``
          ——``turn_end`` 是写入时的边界事实，调用方需自行保持一致。

        :raises KeyError: ``msg_id`` 不存在于消息树。

        .. seealso::
           :meth:`remove`、:meth:`reparent`。
        """
        # 宿主 Agent 引用见类 docstring ``_agent`` 字段。
        if msg_id not in self._agent._messages:
            raise KeyError(msg_id)
        self._agent._messages[msg_id].content = content  # 仅替换内容，不动链
        # update 变更行（同步提交）；压缩期固化。content 序列化为行内
        # content 项形态，保证 JSON 可落盘
        self._agent._persist_tree_record(
            {"type": "update", "id": msg_id,
             "content": [_block_to_record(b) for b in content]})

    def reparent(self, msg_id: str, *, to: str) -> None:
        """子树重连：改 ``msg_id`` 的 ``parent_id``，整个子树随之移动。

        .. rubric:: 功能介绍

        “单点 op，子树效应”——只改目标消息一个字段，其后代链不变，整个
        子树自然跟着走。是五 op 中唯一能移动既有结构的 op：分支迁移、
        删除前的子树保全、压缩摘要分支的挂接都经它完成。

        .. rubric:: 使用示例

        .. code-block:: python

            # 把 m8 分支整体挂到 m3 之下
            agent.chain.reparent("m8", to="m3")

        .. rubric:: 行为要点

        - 前置条件：``msg_id`` 与 ``to`` 均在树中存在；``to`` 不是
          ``msg_id`` 自身或其后代（防环）。
        - 后置条件：``msg_id.parent_id == to``；其后代的 ``parent_id``
          不变；append move 变更记录。
        - 边缘情况：重连根消息（``parent_id=None`` 者）等价于把整棵树挂
          为新子树——规约不禁止，但调用方应明确自己在做什么。
        - 不移动 ``current_head_id``；不复制子树（移动语义，非拷贝）。

        :raises KeyError: ``msg_id`` 或 ``to`` 不存在于消息树。
        :raises ValueError: ``to`` 是 ``msg_id`` 自身或其后代（会成环）。

        .. seealso::
           :meth:`remove` （删除前保全子树的定式）、:meth:`insert`、
           :meth:`flowing.agent.Agent.fork`。
        """
        # 宿主 Agent 引用见类 docstring ``_agent`` 字段。
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
             "parent_id": to})   # move 变更行（同步提交）
