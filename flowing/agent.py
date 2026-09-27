"""``flowing.agent`` —— Agent 对象模型：逻辑 Turn 执行、消息级树与异步执行管理。

.. rubric:: 功能介绍

框架核心层。本模块承载 Flowing 的中心对象 :class:`Agent` （智能体基类，
``.fya`` 声明式与手写 Python 子类生成完全相同的类模型），以及围绕它的
回合载体与结果结构：

- :class:`TurnContext` —— 逻辑 Turn 的执行期临时对象（``before_turn`` /
  ``on_turn_abort`` / ``after_turn`` 钩子点的 value 类型）。
- :class:`TurnResult` —— 回合产物（``query()`` 等待语义的返回值）。
- :class:`Execution` —— 异步执行追踪条目（“谁正在运行、谁可取消”）。
- :class:`FieldUpdate` —— ``watch`` watcher 通道的 value（赋值事件快照）。
- :class:`ProviderErrorContext` / :class:`CancelContext` ——
  ``on_provider_error`` / ``before_cancel`` / ``after_cancel`` 钩子点的
  value 类型。
- :func:`build_turn_result` —— 回合收尾组装函数。
- :class:`flowing.persistence.StateView` —— 持久化状态袋视图（声明经
  ``Agent.register_state``、读写统一经 ``Agent.state``）。

子 Agent 绑定条目（三层能力描述的绑定层）与唤起 / 结果结构
（:class:`flowing.subagents.SubagentEntry` /
:class:`flowing.subagents.SubagentInvocation` /
:class:`flowing.subagents.SubagentResult`）拆在
:mod:`flowing.subagents`；持久化机制（``FileRecordStore`` write-behind
落盘）在 :mod:`flowing.persistence`；24 个核心钩子点的触发时机 / value
类型 / handler 能力见 :mod:`flowing.hooks` 模块 docstring 的全集表。

本模块遵循“框架只提供机制，不提供策略”：核心只做错误分类、钩子点
分发与消息流转；重试、压缩、审批等策略全部放在扩展 / Composable /
应用层。

.. rubric:: 全局约定（跨符号、影响使用的约定）

async 方法约定：一切会在内部 dispatch 钩子的公开方法都是 async
方法，调用时用 ``await``——``query`` / ``message`` / ``steer`` /
``enqueue_message`` / ``enqueue_messages`` / ``fork`` / ``cancel`` /
``stop`` / ``destroy`` / ``create_subagent`` / ``invoke_subagent`` /
``setup`` / ``provider_gen`` / ``side_query`` / ``tool_call`` 等。不触发
钩子的方法保持同步：``pause`` / ``resume`` / ``abort_turn`` /
``cancel_queued`` / ``provide`` / ``inject`` / ``watch`` / ``parsable`` /
``snapshot`` / ``register_state`` / ``add_tool`` 等。

实例化与生命周期：Agent 实例不直接构造——唯一创建路径是
:meth:`flowing.runtime.Runtime.create_agent`，恢复路径是
:meth:`flowing.runtime.Runtime.recover_agent` （``create_subagent`` /
``Workflow.create_agent`` / ``Runtime.mount`` 全部委托创建管线）。创建与
恢复管线各自在一个新实例上执行 ``setup()``；实例诞生（``after_create`` /
``after_recover`` 完成）即启动常驻工作循环 Task，``destroy()`` 时取消。
``destroy()`` 递归销毁子树、丢弃实例，但 session 记录（tree.jsonl /
state.jsonl）与池 key 保留——“有 key 无 value”可现场恢复。

消息级树：树节点是消息（``Message.id`` + ``parent_id`` 链，
``current_head_id`` 指向消息 id，``None`` 表示空树）。``fork`` 只切换
视角（游标）、从不创建节点；树手术（插入 / 删除 / 更新 / 重挂）经
``Agent.chain`` （:class:`flowing.message.MessageChain`）。

Turn 只是逻辑执行阶段：``TurnContext`` 不落盘、不进树、进程崩溃后
不恢复；恢复 = 重放 ``tree.jsonl`` / ``state.jsonl`` 日志现场重建。

持久化布局：每 Agent 一个 session 目录（``agent_id`` 命名，亲子
平级），内含 ``tree.jsonl`` （消息树）/ ``core.jsonl`` （框架私有核心
状态）/ ``state.jsonl`` （默认状态袋）与 ``meta.json`` （身份四键，
Runtime 属主）。物理读写由 :class:`flowing.persistence.FileRecordStore`
执行——write-behind：提交同步排队、drain 任务串行落盘，调用返回不代表
已写盘，正常关闭（``destroy()`` 排空后端）会写完未落盘内容。状态键声明
经 ``agent.state.register(key, default)`` （缺省即写、幂等）；扩展状态
空间经 ``register_state(name)`` 开启（命名袋，独立文件）。

provide-inject 链：``provide(key, value)`` 在本节点注册值；``inject``
从当前节点沿亲代链逐级向根查找（先近后远，终点是 Runtime）；同 key 重复
``provide`` 是覆盖更新，查找实时、不缓存；找到根仍未命中抛
``MissingProvideError``。敏感信息（API key / 凭证）走本通道——不进消息
流、不进 LLM 上下文、不落盘。

钩子与 watch：每个实例持有一个独立的钩子注册表（``agent.hooks``），
全部钩子只对当前实例生效；核心钩子点全集见 :mod:`flowing.hooks`。
``watch`` 通道监听实例属性赋值事件——fire-and-forget 通知、纯观察
（赋值不等待 watcher，watcher 改写无效）。

死锁禁止：在当前回合的调用栈内（任何钩子、工具 ``execute``、
``provider_gen`` 期间的 await 点）``await query()`` 必死锁；跨 Agent
等待图成环（A 等 B、B 等 A）同样是死锁，框架不做环检测。回合内需要
驱动用 :meth:`Agent.steer` （STEER 优先级，当轮 context 可见）。

无生命周期状态机：Agent 不提供 ``status`` 字段；“在干什么”的观测
由快照层从内部状态现场派生（``snapshot()``）。

.. rubric:: 使用示例

手写子类（``.fya`` 声明式等价形态见 :class:`Agent`）：

.. code-block:: python

    from flowing import Agent, on
    from flowing.parsable import Parsable

    class OrderAgent(Agent):
        system_prompt = Parsable("你是订单助手。")

        @on("before_tool_call")
        def _tag(self, tool_call):
            tool_call.args["lang"] = self.locale
            return tool_call

        async def setup(self, locale: str = "zh"):
            self.locale = locale
            self.add_tool("make-payment", alias="pay")

    # 创建与消息驱动（创建入口见 :mod:`flowing.runtime`）
    agent = await runtime.create_agent(OrderAgent)
    result = await agent.query("帮我查订单 4521")
    print(result.status)       # "completed"
    print(result.final_text)   # 最后一条 PROVIDER 消息的文本

.. seealso::

    - :class:`flowing.runtime.Runtime` —— 对象图根、唯一创建 / 恢复入口宿主。
    - :class:`flowing.message.Message` / :class:`flowing.message.MessageChain`
      —— 消息级树节点与树手术。
    - :class:`flowing.hooks.HookRegistry` —— 实例级钩子注册表（钩子点全集
      与 dispatch 规则见其模块 docstring）。
    - :mod:`flowing.model` —— ``ModelConfig`` / ``ProviderResponse`` /
      ``Usage`` / ``ProviderDelta``。
"""

from __future__ import annotations   # 注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import asyncio
import contextlib
import inspect
import json
import logging
import sys

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypeVar, overload

from pydantic import ValidationError

from flowing.context import Context, ContextUsageEstimate, PromptBlockList, PromptSegment
from flowing.errors import (
    EntryNameConflictError,
    FlowingError,
    FormatError,
    Intercepted,
    ToolNotFoundError,
    UnknownToolError,
    UnpairedToolCallError,
)
from flowing.hooks import HookRegistry
from flowing.message import (
    ContentBlock,
    Message,
    MessageChain,
    MessageKind,
    MessagePriority,
    MessageQueue,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    estimate_message_tokens,
    from_record,
    to_record,
    _block_from_record,
    _text_tokens,
)
from flowing.model import ModelConfig, load_model_tags, load_models
from flowing.params import InjectionKey, schema_to_model
from flowing.parsable import _UNSET, PENDING, Parsable
from flowing.parser import EntryRef, normalize_entries, split_as
from flowing.paths import GLOB_META
from flowing.persistence import FileRecordStore, RecordStore, StateView
from flowing.provide import inject_from
from flowing.providers import Provider, ProviderDelta, ProviderResponse, Usage
from flowing.snapshot import (
    AgentSnapshot,
    EntryInfo,
    ExecutionInfo,
    MessageQueueInfo,
    MessageTreeInfo,
    ModelInfo,
    TurnContextInfo,
)
from flowing.subagents import (
    DEFAULT_SUBAGENT_CATALOG_TEMPLATE,
    SubagentEntry,
    SubagentInvocation,
    SubagentResult,
)
from flowing.tool import (
    TOOL_NAMING,
    McpTool,
    Tool,
    ToolCall,
    ToolDefinition,
    ToolEntry,
    ToolResult,
    normalize_output,
    output_to_blocks,
)
from flowing.tool.core import _has_forbidden_block

if TYPE_CHECKING:
    from flowing.runtime import Runtime

T = TypeVar("T")

_logger = logging.getLogger(__name__)
"""模块级 logger：工作循环回合异常等的记录点（规约只要求“记日志”，未具名
logger 符号）。"""


class _LlmViewValidationError(FlowingError):
    """LLM 视角校验失败时抛出的内部信号（内部 API，不属稳定契约）。

    未知键检查在 ``Agent._normalize`` 内以本异常报出，键名即 LLM 自己提供
    的参数名；``Agent.tool_call`` 捕获后包装为 ``ToolResult(status="error")``
    正常产物。``strict=False`` 的工具不施加未知键拒绝。
    """


def _estimate_tool_schema_tokens(definition: ToolDefinition) -> int:
    """工具 schema 的 token 补估（内部 API，不属稳定契约）。

    ``llm_definition()`` 产物的 JSON 序列化经
    :func:`flowing.message._text_tokens` 估算（ASCII ÷ 4 + 非 ASCII × 1，
    向上取整），与消息 / system prompt 估算同一字符启发式口径——中文
    描述不再被统一除 4 系统性低估；``estimate_context_tokens`` 的
    “锚点后新增工具补估”与“无锚点全估”共用。
    """
    from dataclasses import asdict

    return _text_tokens(json.dumps(asdict(definition), ensure_ascii=False))


def _as_parsable_patch(value: Any) -> Parsable | None:
    """覆写体的文本类补丁值归一（内部 API，不属稳定契约）。

    ``_`` （PENDING）→ ``None`` （空补丁语义：声明了覆写位、内容为空，从基底
    回填）；``Parsable`` 原样透传；其余包装为 ``Parsable`` 常量。
    ``add_tool`` 的 ``description`` 与 ``add_agent`` 的 ``system_prompt`` /
    ``description`` 共用。
    """
    if value is PENDING:
        return None   # 空补丁语义：声明了覆写位、内容为空（从基底回填）
    if isinstance(value, Parsable):
        return value
    return Parsable(value)


def _classify_override_args(
    args_body: Mapping[str, Any],
    override_params: dict[str, dict[str, Any]],
    specified: dict[str, Parsable],
    param_aliases: dict[str, str],
) -> None:
    """覆写体 ``args:`` 映射的逐参数判别（内部 API，``add_tool`` /
    ``add_agent`` 共用，同一套代码路径）：

    - 值是 dict → ``override_params`` 稀疏补丁（JSON Schema 关键字，零糖）；
    - 键含 ``<name> as <alias>`` → ``param_aliases``，值部分照常判别；
    - 值是 ``_`` （PENDING）→ 空补丁（``override_params[name] = {}``，
      深层块可逐字段填充，未填充则合成时全量回填）；
    - 其它值 → ``specified`` （包装 ``Parsable``；注入表达式
      ``"{{ self.inject('key') }}"`` 在此落入）。
    """
    for raw_key, value in args_body.items():
        pname, palias = split_as(raw_key)
        if palias is not None:
            param_aliases[palias] = pname
        if value is PENDING:
            override_params[pname] = {}   # 空补丁
        elif isinstance(value, Mapping):
            override_params[pname] = dict(value)   # 稀疏补丁（零糖）
        else:
            specified[pname] = value if isinstance(value, Parsable) else Parsable(value)

WatchHandler = Callable[[Any, Any], None]
"""``watch`` 的回调类型：``(new_value, old_value) -> None``，返回值
被忽略。
"""


@dataclass
class TurnContext:
    """逻辑 Turn 的执行期临时对象——``before_turn`` / ``on_turn_abort`` /
    ``after_turn`` 三个 turn 族钩子点的 value 类型。

    .. rubric:: 功能介绍

    逻辑 Turn 是“消费一条（或按覆写的出队策略多条）消息、直到 Provider
    响应 ``finish=True`` 才结束”的执行过程。本对象由回合执行体在 Turn
    开始时创建、收尾后丢弃，是 :attr:`TurnResult.turn` 与
    ``Agent.current_turn`` 的类型。它只承载回合执行期间的临时信息：不进入
    消息树、不写任何持久化文件、进程崩溃后不恢复（恢复只重建消息级树与
    状态袋，见本模块 docstring）。

    .. rubric:: 使用示例

    ``before_turn`` 钩子：把一条消息附加进本回合的待挂树批次（排在触发
    消息之后，随批次一起挂树并持久化）：

    .. code-block:: python

        async def _reminder(agent, turn):
            turn.pending_messages.append(Message(
                kind=MessageKind.EVENT, source="reminder",
                content=[TextBlock(text="请先核对金额")]))
            return turn

        agent.hooks.before_turn(_reminder)

    .. rubric:: 行为要点

    - ``message_ids`` 引用消息级树中的节点：按产生顺序追加 id，不含消息
      对象副本；``message_ids[0]`` 是本 Turn 首条消息的 id——本对象没有
      独立标识字段，需要逻辑标识时用它。
    - ``pending_messages`` 只在 ``before_turn`` 钩子执行期间可读写：
      追加即附加式注入（排在触发消息之后），清空即空 Turn；批次挂树
      完成后清空，此后读写没有意义。``before_turn`` 被 ``Intercepted``
      阻断时整个批次丢弃——不落盘、不留痕（显式丢失语义）。
    - ``aborted`` 由 abort 标记路径置位（``abort_turn()`` / 取消 / 亲节点级联
      / 钩子内直接置位走同一路径）；置位幂等，``on_turn_abort`` 随之
      每回合至多触发一次。``after_turn`` 钩子读 ``aborted`` 区分正常结束
      与取消。
    - ``usages`` 是本回合各次成功 ``provider_gen`` 上报用量的纯内存累加
      器（追加的是消息上附着的同一个 ``Usage`` 对象引用，不是第二份
      数据；用量唯一权威是 ``Message.usage``）；收尾时聚合进
      ``TurnResult.token_usage`` 后随本对象丢弃。副线调用
      （``side_query``）不经回合循环，其用量不记入本字段。
    - ``finish_output`` 是 finish 工具的结构化交卷载荷：置位即请求本
      回合自然结束（工具段照常执行完，随后视同 ``finish=True`` 走统一
      收尾），载荷由收尾段写入 ``Agent.last_result``。瞬态字段，随回合
      丢弃、不落盘。
    - 边缘情况：空 Turn（启动即被 abort）不产生新树节点，
      ``message_ids`` 可能只含触发消息或为空，``current_head_id`` 不变。

    .. seealso::

        - :class:`TurnResult` —— ``turn`` 字段即本类，回合产物的载体。
        - :meth:`Agent.query` —— 等待一个逻辑 Turn 完成的入口。
    """

    started_at: datetime
    """本 Turn 开始时间戳（监控 / 日志 / 耗时统计用）。
    """
    finished_at: datetime | None = None
    """本 Turn 收尾完成的时间戳；执行期间为 ``None``，由回合收尾时落位。
    快照投影只在执行期间可见，故其中该字段恒为 ``None``；完整取值见
    ``TurnResult.turn.finished_at``。
    """
    message_ids: list[str] = field(default_factory=list)
    """本 Turn 已挂树消息的 id 列表（引用消息级树节点，按产生顺序追加）。
    ``message_ids[0]`` 是本 Turn 首条消息的 id。
    """
    aborted: bool = False
    """abort 标记：置位幂等，``on_turn_abort`` 随之每回合至多触发一次。
    """
    pending_messages: list[Message] = field(default_factory=list)
    """出队后、挂树前的待发批次（含触发本 Turn 的消息）；仅 ``before_turn``
    钩子执行期间可读写，批次挂树完成后清空。被 ``Intercepted`` 阻断时
    整体丢弃、不落盘。
    """
    usages: list[Usage] = field(default_factory=list)
    """本 Turn 各次成功 ``provider_gen`` 上报的用量（追加的是消息上附着的
    同一个 ``Usage`` 对象引用，不是第二份数据）；纯内存累加器，不落盘，
    收尾时由 ``build_turn_result`` 聚合进 ``TurnResult.token_usage``。
    副线调用（``side_query``）不经过本字段。
    """
    finish_output: dict[str, Any] | None = None
    """finish 工具的结构化交卷载荷：置位即请求本回合自然结束（工具段照常
    执行完，随后视同 ``finish=True`` 走统一收尾），载荷由收尾段写入
    ``last_result``。瞬态字段，随回合丢弃、不落盘。
    """


@dataclass
class TurnResult:
    """逻辑 Turn 的产物——``query()`` 等待语义的返回值。

    .. rubric:: 功能介绍

    由 :func:`build_turn_result` 在回合收尾（``after_turn`` 之后）组装；
    同一回合内所有消息的等待者共享同一个实例（出队合并时两条消息的
    等待者拿到同一对象）。四种结局（正常完成 / 被 ``Intercepted`` 阻断 /
    取消 / 异常终止）都会产生本对象并 resolve 给等待者，调用方永不挂起。

    .. rubric:: 使用示例

    .. code-block:: python

        result = await agent.query("帮我查订单 4521")
        if result.status == "completed":
            print(result.final_text)
            print(result.token_usage)   # Usage | None

    .. rubric:: 行为要点

    - ``status`` 四值：``"completed"`` （Provider 响应 ``finish=True``
      自然结束）/ ``"blocked"`` （被 ``Intercepted`` 阻断）/ ``"cancelled"``
      （取消 / 销毁 / ``cancel_queued`` 联动）/ ``"error"`` （未捕获异常
      终止）。四种结局都会 resolve 给等待者。
    - ``final_text`` 是本 Turn 最后一条 PROVIDER 消息中全部
      ``TextBlock`` 的文本拼接；本 Turn 没有 PROVIDER 消息（如 abort 于
      首次 ``provider_gen`` 之前）时为空字符串。
    - ``token_usage`` 是本 Turn 各次成功 ``provider_gen`` 上报用量的
      聚合（对 ``TurnContext.usages`` 逐字段求和；``raw`` 不聚合）；为
      ``None`` 当且仅当没有任何成功调用上报用量——区分“provider 未上报”
      与“真用了 0”。部分调用上报时只就上报者求和。
    - ``finish_reason`` 是信息字段（原始停止原因，如 ``"end_turn"`` /
      ``"cancelled"`` / ``"intercepted"``），不参与控制流；Provider 侧的
      结束判定字段是 ``ProviderResponse.finish``，二者不要混用。
    - 本对象自身不落盘：``turn`` 是执行期临时对象，读它的 ``message_ids``
      可回溯树中消息，但本对象随回合结束即丢弃。

    .. seealso::

        - :meth:`Agent.query` —— 本结构的获取入口。
        - :func:`build_turn_result` —— 组装函数。
        - :class:`flowing.providers.Usage` —— ``token_usage`` 的类型。
    """

    turn: TurnContext
    """产生本结果的逻辑 Turn 的执行期临时对象；其 ``message_ids`` 引用
    消息级树中的节点。
    """
    final_text: str
    """最终回复文本；本 Turn 无 PROVIDER 消息时为空字符串。
    """
    status: Literal["completed", "blocked", "error", "cancelled"]
    """回合结局；四种结局均会 resolve 给等待者。
    """
    token_usage: Usage | None
    """本 Turn 聚合 token 用量；无任何成功 ``provider_gen`` 上报用量时为
    ``None``。
    """
    finish_reason: str
    """信息性停止原因（不参与控制流）。
    """


@dataclass
class Execution:
    """活跃异步执行条目——“谁正在运行、谁可取消”的追踪单位。

    .. rubric:: 功能介绍

    Agent 发起的每个异步执行（工具调用、子 Agent、LLM 请求、副线查询）
    启动时注册一条本实例进 ``Agent._executions`` 注册表，完成 / 异常 /
    取消后移除（清理在 ``finally`` 中）。``cancel`` / ``stop`` 族通过
    遍历该注册表置位控制信号；快照层只投影其中的 ``kind`` / ``tags`` /
    ``started_at`` 三个字段。

    .. rubric:: 行为要点

    - ``kind`` 是开放字符串（非封闭枚举）：内置 ``"tool"`` / ``"agent"`` /
      ``"request"`` / ``"side_query"`` 四种；扩展可引入新值，注册与冲突
      规则由扩展自管。
    - ``tags`` 供分组取消（``cancel_by_tag`` / ``stop_by_tag``）；典型
      用法 ``tags=["bash", "long-running"]``。
    - 注册与清理严格成对、清理一定在 ``finally``：执行条目不残留，避免
      后续 ``cancel()`` 误伤已完成的执行。
    - 取消是协作式的：``cancel`` 置位是请求不是命令——执行体在检查点检测
      信号后自行决定立即停止（返回已有或空结果）、忽略信号正常完成、或
      做关键收尾后返回部分结果。框架不设强制终止语义（那是可覆写
      ``stop()`` 的职责）。例外：前台 awaitable 工具（script / cli /
      request / mcp）的在途执行由 ``Tool.__call__`` 的取消竞速中断
      （CancelledError 注入其 await 点）——竞速注入不等工具的协作检查点。
    - ``pause`` 置位同样是协作式请求，保留给 Tool 覆写与 Composable——
      框架核心不主动 set / clear 它；只影响单个执行、不级联（区别于
      Agent 层 ``pause()`` 对工作循环 gate 的控制）。
    - 快照（``AgentSnapshot.executions``）只暴露 ``kind`` / ``tags`` /
      ``started_at``，不暴露两个 Event——避免绕过控制 API 与钩子。

    .. seealso::

        - :meth:`Agent.cancel` / :meth:`Agent.cancel_by_tag` —— 信号置位入口。
        - :class:`flowing.tool.Tool` —— 工具执行的宿主（``execution`` 注入）。
    """

    id: str
    """按 Agent 的自增序列号（纯数字字符串），注册表 ``Agent._executions``
    的 key。
    """
    kind: str
    """开放字符串：内置 ``"tool"`` / ``"agent"`` / ``"request"`` /
    ``"side_query"``；扩展可引入新值。
    """
    tags: list[str]
    """自由标签，``cancel_by_tag`` / ``stop_by_tag`` 的分组依据。
    """
    started_at: datetime
    """启动时间戳（监控 / 日志 / 超时判断）。
    """
    cancel: asyncio.Event
    """置位 = 请求取消（初始未 set）；协作式信号，执行体可自行决定
    是否响应。词汇约定：执行侧统一叫 cancel（方法 ``cancel()`` /
    ``cancel_by_tag()``、钩子 ``before_cancel`` / ``after_cancel`` 与本
    字段）；Turn 循环一侧保留 abort（``abort_turn()`` /
    ``on_turn_abort`` / ``turn.aborted``）——两个域的词汇各自内部
    自洽，不跨域混用。
    """
    pause: asyncio.Event
    """置位 = 请求暂停（初始未 set）；保留给 Tool 覆写与 Composable，
    框架核心不主动 set / clear；只影响单个执行，不级联。
    """


@dataclass
class FieldUpdate:
    """``watch`` watcher 通道的 value——一次实例属性赋值事件的快照。

    .. rubric:: 功能介绍

    ``Agent.__setattr__`` 拦截实例属性赋值时，在写入前构造本对象并以
    fire-and-forget 方式通知 watcher 通道（``hooks._notify_watch``，
    pattern 匹配本对象的 ``name`` 字段——``agent.watch('locale', ...)``
    即字面量精确匹配）。watcher 收到的是赋值事件的自洽快照：无论
    watcher 何时真正执行，``old`` / ``new`` 都是触发那一刻的值。

    .. rubric:: 行为要点

    - 纯观察：watcher 改写 ``new`` 无效（watcher 返回值被忽略）；
      ``raise Intercepted`` 与普通异常同处理——终止本次 watcher 链、
      记录日志，不影响赋值（fire-and-forget 无上抛对象）。
    - watcher 可为同步或异步函数（后台任务统一 await）。
    - 时序不保证：通知任务在写入前入队，但 watcher 的实际执行可能晚于
      写入完成；连续多次赋值的多个 watcher 间执行顺序亦不保证——依赖
      时序的逻辑应自行序列化，快照数据始终自洽。
    - 无运行中的 event loop 时（如 ``__init__`` 骨架阶段的赋值），通知
      静默跳过——赋值照常，watcher 不触发。这是 fire-and-forget 的已知
      边界。
    - 触发范围：仅实例属性赋值；描述符 / 类属性 / ``_`` 前缀骨架字段的
      初始化不经过本机制。
    - watcher 内再次给同名字段赋值会造成递归通知——框架不做递归防护，
      属编程错误。
    - 插件约定：托管变量（如 i18n 插件注入的 ``agent.i18n``）应以插件
      自有对象为载体，属性级拦截在该对象自己的类里实现；顶层槽位的
      重绑定只能经 ``watch`` 观察、不能拦截（可纠正性回写会二次触发
      监听）。

    .. seealso::

        - :meth:`Agent.watch` —— ``(new, old)`` 形态的注册糖。
        - :meth:`flowing.hooks.HookRegistry.watch` —— 低层注册
          （``(agent, value)`` 形态）。
    """

    name: str
    """被赋值的字段名；watcher 注册的 pattern 按本字段匹配。
    """
    old: Any
    """旧值；字段此前不存在时为 ``None``。
    """
    new: Any
    """即将写入的值（快照，只读语义——改写不影响赋值）。
    """


@dataclass
class ProviderErrorContext:
    """``on_provider_error`` 钩子的 value——LLM 调用异常的唯一决策上下文。

    .. rubric:: 功能介绍

    ``provider_gen()`` 抛出的异常被回合层捕获后，构造本对象并 dispatch
    ``on_provider_error``。handler 在内部执行动作（退避等待 / 改
    ``self.model`` / 调 ``abort_turn()``）并写 ``can_continue`` 表达决策。
    核心只提供机制（错误分类与分发）；“该不该重试、重试几次”是策略——
    内置的可选 Composable ``use_retry()`` 以 ``by="retry"`` 注册 handler
    提供重试；不启用时 ``can_continue`` 保持 ``False``，错误直接终止
    回合（Agent 存活，可继续消费后续消息）。

    .. rubric:: 使用示例

    .. code-block:: python

        from flowing.errors import RateLimitedError

        async def _retry(agent, ctx):
            if isinstance(ctx.error, RateLimitedError):
                await asyncio.sleep(2)
                ctx.can_continue = True   # 触发回合内重试
            return ctx

    .. rubric:: 行为要点

    - ``can_continue=False`` （默认；无 handler 或 handler 未改写）→ 回合
      中断，本回合以 ``"error"`` 结局收尾；``True`` → 重新发起
      ``provider_gen``——handler 须已完成退避 / 换模型等动作。
    - handler 内调 ``agent.abort_turn()`` 是合法出口：``continue`` 后
      下一次 ``provider_gen()`` 开头检测到信号 → 返回 ``cancelled`` 响应
      → 回合走 abort 收尾（``"cancelled"`` 结局）。
    - 全部 Provider 调用期异常都经本钩子分发（含 ``ContextLengthError``）：
      原样重发必然重现的错误（token 超限、凭证错误等）默认策略同样不重试；
      压缩历史 / 换大窗模型 / 仅观察都是 handler 的合法处置。
    - ``provider`` 是 provider 条目名字符串（``self.model.provider``），
      不是 Provider 实例。

    .. seealso::

        - :meth:`Agent.provider_gen` —— 异常来源（其本身不捕获、不重试）。
        - :mod:`flowing.composables.retry` —— 可选重试策略。
    """

    error: Exception
    """``provider_gen()`` 上抛的原始异常。
    """
    provider: str
    """发生错误的 provider 条目名（``ModelConfig.provider``，字符串）。
    """
    model: ModelConfig
    """发生错误时的模型结构体（``ModelConfig``）。
    """
    can_continue: bool = False
    """决策字段：handler 写 ``True`` 触发回合内重试；默认 ``False``
    表示回合中断。
    """


@dataclass
class CancelContext:
    """``before_cancel`` / ``after_cancel`` 钩子点的 value——取消操作的上下文。

    .. rubric:: 功能介绍

    ``cancel()`` 在置位任何取消信号之前 dispatch ``before_cancel``，
    handler 可 ``raise Intercepted`` 阻止取消——适用于当前操作不可中断的
    场景（支付已提交、关键事务进行中）。``after_cancel`` 在信号置位后
    立即 dispatch，是“取消请求已被接受”的事实事件（纯观察）。

    .. rubric:: 行为要点

    - ``reason`` 是取消原因说明。``Agent.cancel()`` / ``stop()`` 均不接受
      原因参数（无参），框架内部 dispatch 时填默认值 ``""``；扩展若自行
      dispatch ``before_cancel`` 可携带自定义原因。
    - handler ``raise Intercepted`` → 取消被阻止，``_executions`` 与
      ``_turn_abort`` 均不置位。
    - handler 普通异常 → 直接上抛给 ``cancel()`` 调用方，信号不置位。

    .. seealso::

        - :meth:`Agent.cancel` —— 触发点。
        - :class:`flowing.errors.Intercepted` —— 阻断信号。
    """

    reason: str = ""
    """取消原因；框架内部调用恒为 ``""`` （cancel 族无参），供扩展
    自行 dispatch 时携带。
    """


@dataclass
class ForkContext:
    """``on_fork`` 钩子点的 value——一次 head 切换的上下文（换前 / 换后两个 id）。

    .. rubric:: 功能介绍

    ``fork()`` 在合法性检查与切换落盘之前 dispatch ``on_fork``：handler
    可改写 ``target_message_id`` 改变切换落点（``None`` = 悬置游标），
    或 ``raise Intercepted`` 阻止本次切换；纯观察用途（日志 / 通知 UI
    刷新分支列表）读换前 / 换后两个 id 即得一次切换的完整信息。

    .. rubric:: 行为要点

    - ``previous_head_id`` 是切换前一刻的 ``current_head_id`` （空树 /
      已悬置为 ``None``）——信息性字段，改写它不影响任何行为。
    - ``target_message_id`` 是切换目标，可改写——合法性检查与实际切换
      均以改写后的值为准；``None`` 合法，语义同 ``fork(None)``。
    - dispatch 发生在切换落盘之前：``Intercepted`` 与合法性检查失败都
      使 head 原地不动，树与游标零副作用。
    - handler 普通异常 → 直接上抛给 ``fork()`` 调用方，不切换。

    .. seealso::

        - :meth:`Agent.fork` —— 触发点。
        - :class:`flowing.errors.Intercepted` —— 阻断信号。
    """

    previous_head_id: str | None
    """切换前一刻的 ``current_head_id`` （换前；空树 / 已悬置为
    ``None``）。信息性字段，改写不影响行为。
    """
    target_message_id: str | None
    """切换目标消息 id（换后）；``None`` = 游标悬置。可改写——合法性
    检查与实际切换均以改写后的值为准。
    """


def build_turn_result(turn: TurnContext, agent: "Agent", *,
                      intercepted: bool = False,
                      error: BaseException | None = None,
                      finish_reason: str = "") -> TurnResult:
    """回合收尾组装 :class:`TurnResult` 的模块级函数。

    .. rubric:: 功能介绍

    回合执行体在收尾段（``after_turn`` 之后）调用：从 :class:`TurnContext`
    与消息级树聚合 ``final_text`` / ``status`` / ``token_usage`` /
    ``finish_reason``，产物 resolve 给本回合全部等待者（共享同一实例）。

    .. rubric:: 行为要点

    - 纯函数式组装：读 ``turn.message_ids`` 指向的树中消息，不修改树、
      不落盘、不 dispatch 钩子。
    - ``status`` 判定（优先级从上到下）：``turn.aborted`` →
      ``"cancelled"``；``intercepted=True`` → ``"blocked"``；
      ``error`` 非 ``None`` → ``"error"``；否则 ``"completed"``。
    - ``token_usage`` 聚合：``turn.usages`` 为空 → ``None`` （区分
      “provider 未上报”与“真用了 0”）；非空 → 七个计数字段逐字段
      求和，聚合体的 ``raw`` 为空字典（逐次原始字段的消费方走
      ``after_provider_gen`` 钩子）。
    - ``finish_reason`` 取值：取消 / 拦截 / 异常结局填对应字面量
      （``"cancelled"`` / ``"intercepted"`` / ``"error"``）；自然完成填
      调用点传入的末次 ``provider_gen`` 响应原始停止原因；空 Turn 填
      空字符串。信息字段，不参与控制流。
    - 边缘情况：``turn.message_ids`` 为空（空 Turn）→ ``final_text`` 为
      空字符串、``token_usage`` 为 ``None``。

    :param turn: 本回合的执行期载体（:class:`TurnContext`）。
    :param agent: 属主 Agent——树访问载体（读 ``Agent._messages``）。
    :param intercepted: 本回合是否被钩子 ``Intercepted`` 硬阻断。
    :param error: 本回合未捕获的异常对象（无则为 ``None``）。
    :param finish_reason: 自然完成时末次 ``provider_gen`` 响应的原始停止
        原因；取消 / 拦截 / 异常结局忽略本参数、由本函数按 ``status``
        填对应字面量。
    :return: 组装好的 :class:`TurnResult`。

    .. seealso::

        - :class:`TurnResult` —— 产物类型。
        - :meth:`Agent.query` —— 等待者的获取入口。
    """
    status: Literal["completed", "blocked", "error", "cancelled"]
    if turn.aborted:
        status = "cancelled"
    elif intercepted:
        status = "blocked"
    elif error is not None:
        status = "error"
    else:
        status = "completed"
    # final_text：turn.message_ids 逆序找最后一条 PROVIDER 消息，取其
    # content 中 TextBlock 的拼接文本；回合内挂树、收尾期被清理的消息
    #（如 use_system_reminder clean=True 的提醒——after_turn 先于本函数
    # 运行）按 tombstone 语义跳过，不抛 KeyError
    final_text: str = ""   # 空 turn / 无 PROVIDER 消息 → ""
    for mid in reversed(turn.message_ids):
        m = agent._messages.get(mid)
        if m is None:
            continue   # 已被收尾清理删除（tombstone）：跳过
        if m.kind is MessageKind.PROVIDER:
            final_text = "".join(b.text for b in m.content
                                 if isinstance(b, TextBlock))
            break
    # token_usage 聚合：usages 空 -> None（区分未上报与真零）；
    # 非空 -> 七字段逐字段求和，raw 不聚合
    token_usage: Usage | None = None
    if turn.usages:
        token_usage = Usage(
            input=sum(u.input for u in turn.usages),
            fresh_input=sum(u.fresh_input for u in turn.usages),
            output=sum(u.output for u in turn.usages),
            cache_read=sum(u.cache_read for u in turn.usages),
            cache_write=sum(u.cache_write for u in turn.usages),
            reasoning=sum(u.reasoning for u in turn.usages),
            total_tokens=sum(u.total_tokens for u in turn.usages),
            raw={},
        )
    # finish_reason：取消 / 拦截 / 异常结局填对应字面量；
    # 自然完成填末次 provider_gen 响应的 stop_reason（调用点显式传入）；
    # 空 turn 填 ""。信息字段，不参与控制流。
    if status == "cancelled":
        finish_reason = "cancelled"
    elif status == "blocked":
        finish_reason = "intercepted"
    elif status == "error":
        finish_reason = "error"
    return TurnResult(turn=turn, final_text=final_text, status=status,
                      token_usage=token_usage, finish_reason=finish_reason)


# ─────────────────── 后台工具投递驱动（Agent 侧统一接缝） ───────────────────


def _start_background_drive(
    caller: "Agent",
    tool: Tool,
    source: Any,
    form: str,
    tool_call: ToolCall | None,
) -> str:
    """启动后台工具的投递驱动并注册任务，返回注册键（内部 API，不属稳定契约）。

    ``form="asyncgen"``：驱动任务逐段消费 async generator；``form="task"``：
    给既有 Task 挂完成回调。两形态统一经 ``caller.track_background_task``
    注册（强引用 + 按 id 取消 / 查询 + destroy 覆盖）。
    """
    if form == "task":
        source.add_done_callback(
            lambda t: asyncio.ensure_future(
                _deliver_task_result(tool, t, caller, tool_call)))
        return caller.track_background_task(source)
    drive_task = asyncio.ensure_future(
        _drive_asyncgen(tool, source, caller, tool_call))
    return caller.track_background_task(drive_task)


def _yield_meta(tool: Tool, tool_call: ToolCall | None) -> tuple[str, str | None]:
    """后台产物 ToolResult 的元信息（内部 API）：别名取 ``tool_call.name``
    （LLM 命名空间）；无 ``tool_call`` 时回退工具规范名、配对 id 为 None。"""
    if tool_call is not None:
        return tool_call.name, tool_call.id
    return tool.definition.name, None


async def _enqueue_event(caller: "Agent", blocks: list[ContentBlock], *,
                         warn_tool: str | None = None) -> None:
    """EVENT 投递统一出口（内部 API）：``warn_tool`` 非空时投递失败记
    warning（分段：不中断驱动），否则静默（终止 / 拦截通知：尽力而为）。"""
    try:
        await caller.enqueue_message(Message(
            kind=MessageKind.EVENT, source="tool_result",
            content=blocks, priority=MessagePriority.STEER))
    except Exception:
        if warn_tool is not None:
            # 投递失败不掩盖原结局——enqueue_message 只入内存队列，
            # 失败仅钩子 / 极端场景
            _logger.warning("async tool %s: report delivery failed", warn_tool)


async def _segment_result(item: Any, name: str, call_id: str | None) -> ToolResult:
    """单个 yield → ``ToolResult(completed, production="segment")`` （内部 API）。

    浅层违禁块（``ToolCallBlock`` / ``ThinkingBlock``）容错转普通文本说明
    ——后台任务已脱离调用栈，“抛异常”无人接收（与 completed 路径的
    ``ValueError`` 框架错误通道区分）。
    """
    if _has_forbidden_block(item):
        return ToolResult(
            status="completed",
            output="report content contained forbidden blocks (tool calls / thinking blocks); omitted",
            name=name, tool_call_id=call_id, production="segment")
    return ToolResult(status="completed",
                      output=await normalize_output(item),
                      name=name, tool_call_id=call_id, production="segment")


async def _deliver_terminal(caller: "Agent", tool: Tool, marker: TextBlock,
                            name: str, call_id: str | None,
                            error_text: str) -> None:
    """async generator 终止通知（内部 API）：包装
    ``ToolResult(status="error", production="final")`` 过
    ``on_tool_yields`` 后投递；拦截改投拦截通知；投递失败静默。"""
    result = ToolResult(status="error", error=error_text,
                        name=name, tool_call_id=call_id, production="final")
    try:
        result = await caller.hooks.on_tool_yields.dispatch(caller, result)
    except Intercepted as exc:
        blocks = [marker, TextBlock(text=f"yield intercepted, reason: {exc}")]
    except Exception:
        blocks = [marker, TextBlock(text=error_text)]
    else:
        blocks = [marker, *output_to_blocks(result.output, error=result.error)]
    await _enqueue_event(caller, blocks)


async def _drive_asyncgen(tool: Tool, agen: Any, caller: "Agent",
                          tool_call: ToolCall | None) -> None:
    """后台驱动 async generator（内部 API，不属稳定契约）。

    每个 yield：归一 → dispatch ``on_tool_yields`` （可改写；``raise
    Intercepted`` → 丢弃该分段并投递含拦截原因的 EVENT 通知，驱动继续）
    → 塑形 → EVENT（``source="tool_result"``、STEER 优先级）入队。
    中途异常与取消按终止通知处理（``production="final"``）；取消在通知
    后裸 ``raise`` （任务以 cancelled 终态结束——不 raise 则任务继续跑）。
    """
    marker = TextBlock(text=f"async tool {tool.definition.name}: ")
    name, call_id = _yield_meta(tool, tool_call)
    try:
        async for item in agen:
            result = await _segment_result(item, name, call_id)
            try:
                result = await caller.hooks.on_tool_yields.dispatch(caller, result)   # 可改写本段产物
            except Intercepted as exc:
                # 弃段 + 通知（通知由驱动循环直接构造入队，不再回过钩子）
                await _enqueue_event(caller, [marker, TextBlock(
                    text=f"yield intercepted, reason: {exc}")])
                continue
            result.output = await normalize_output(result.output)   # 钩子改写兜底归一（幂等）
            await _enqueue_event(
                caller,
                [marker, *output_to_blocks(result.output, error=result.error)],
                warn_tool=tool.definition.name)
    except asyncio.CancelledError:
        await _deliver_terminal(caller, tool, marker, name, call_id,
                                "async task cancelled")
        raise
    except Exception as exc:
        await _deliver_terminal(caller, tool, marker, name, call_id, str(exc))
        _logger.exception("async tool %s failed in the background", tool.definition.name)


async def _deliver_task_result(tool: Tool, task: "asyncio.Task", caller: "Agent",
                               tool_call: ToolCall | None) -> None:
    """Task 终值投递（内部 API，不属稳定契约）。

    取消 / 异常 → ``ToolResult(status="error", production="final")``；正常
    → 归一后的 completed 终值。统一 dispatch ``on_tool_yields`` 后塑形入队
    （拦截改投拦截通知）。全过程兜底——投递链任何环节失败记日志，不成
    为无人 retrieve 的 Task 异常。
    """
    marker = TextBlock(text=f"final result of async tool {tool.definition.name}: ")
    name, call_id = _yield_meta(tool, tool_call)
    try:
        if task.cancelled():
            result = ToolResult(status="error", error="async task cancelled",
                                name=name, tool_call_id=call_id, production="final")
        elif (exc := task.exception()) is not None:
            result = ToolResult(status="error", error=str(exc),
                                name=name, tool_call_id=call_id, production="final")
        else:
            result = ToolResult(status="completed",
                                output=await normalize_output(task.result()),
                                name=name, tool_call_id=call_id, production="final")
        try:
            result = await caller.hooks.on_tool_yields.dispatch(caller, result)
        except Intercepted as exc:
            blocks = [marker, TextBlock(text=f"yield intercepted, reason: {exc}")]
        else:
            result.output = await normalize_output(result.output)   # 钩子改写兜底归一（幂等）
            blocks = [marker, *output_to_blocks(result.output, error=result.error)]
        await caller.enqueue_message(Message(
            kind=MessageKind.EVENT, source="tool_result",
            content=blocks, priority=MessagePriority.STEER))
    except Exception:
        _logger.exception("async tool %s: final result delivery failed", tool.definition.name)


class Agent:
    """智能体基类——``.fya`` 声明式与手写子类共用的同一类模型。

    .. rubric:: 功能介绍

    Flowing 的中心对象：每个实例拥有自己的消息队列、常驻工作循环 Task、
    消息级树、实例级钩子注册表、工具 / 子 Agent 绑定条目与 provide-inject
    链位置。实例即 handle（没有独立的 ``AgentHandle`` 包装）；元信息直接
    作为类属性（没有独立的 ``AgentSpec`` 结构）。

    实例化不直接进行：唯一创建路径是
    :meth:`flowing.runtime.Runtime.create_agent` （``Agent.create_subagent``
    / ``Workflow.create_agent`` / ``Runtime.mount`` 全部委托它）；恢复路径
    是 :meth:`flowing.runtime.Runtime.recover_agent`。两条管线都会执行
    ``setup()``——每个实例上恰好执行一次（实例级单次契约见
    :meth:`setup`）。

    .. rubric:: 使用示例

    ``.fya`` 声明式（``agents/order-agent/agent.fya``）：

    .. code-block:: text

        name: order-agent   # 可选一致性断言（与目录名推断一致）；不写则由 agent.fya 所在目录名推断
        description: 处理电商订单的查询、退款和物流跟踪。
        model_tag: default
        args:
          user_id:                  # 完整写法：JSON Schema 关键字多行展开
            type: string
            description: 用户 ID
          order_id: str             # 糖：裸类型字符串（必填）
        subagents:
          - payment as pay   # 运行期示例中 create_subagent("payment") 的声明依据
        ---
        $system_prompt:
        你是一个订单处理助手。{% if locale == 'zh' %}请用简体中文回复。{% endif %}
        ---
        $script:
        from flowing import on

        @on('before_tool_call')
        def _(self, tool_call):
            tool_call.args['lang'] = self.locale
            return tool_call

        async def setup(self, user_id: int, order_id: str, locale: str = "zh"):
            self.user_id = user_id
            self.order_id = order_id
            self.locale = locale

    手写子类（完全等价）：

    .. code-block:: python

        from pydantic import BaseModel, Field

        from flowing import Agent, on
        from flowing.parsable import Parsable

        class OrderArgs(BaseModel):   # 参数声明（声明即模型）
            user_id: int = Field(description="用户 ID")
            order_id: str

        class OrderAgent(Agent):
            # name 省略——由类名 kebab 化推断为 "order-agent"（写了仅作一致性断言）
            description = "处理订单查询的 Agent"
            system_prompt = Parsable(
                "你是一个订单处理助手。{% if locale == 'zh' %}请用简体中文回复。{% endif %}")
            model_tag = "default"
            args_model = OrderArgs

            @on('before_tool_call')
            def _(self, tool_call):
                tool_call.args['lang'] = self.locale
                return tool_call

            async def setup(self, user_id: int, order_id: str, locale: str = "zh"):
                self.user_id = user_id
                self.order_id = order_id
                self.locale = locale
                self.add_agent("payment", alias="pay")   # 等价于 fya 的 subagents 声明

    运行期使用：

    .. code-block:: python

        child = await self.create_subagent("payment", order_id="456")   # 类型名引用
        result = await child.query("发起退款")
        child.pause()      # 协作式暂停工作循环（同步方法）
        await child.destroy()    # 显式销毁；session 记录保留

    .. rubric:: 行为要点

    - 同名 ``.fya`` 与手写子类并存时 ``.fya`` 优先并告警；两者生成的类
      结构完全相同（等价且互斥）。
    - ``setup()`` 轻量约束：只做状态赋值、钩子注册、inject 读取、
      Composable 调用；网络请求 / 文件 I/O / 大量计算移到工具调用或按需
      阶段。``setup()`` 第一个 ``await`` 之前的代码不被其它协程打断。
    - 不变量：``self.model`` 永远是 ``ModelConfig``；Agent 对模型结构体
      只做持有与机械传递，不解释字段。
    - 不变量：创建即注册（``_nodes``）；亲节点销毁 → 子递归销毁；destroy
      后实例不再可用（从 ``_nodes`` 摘除、三层能力描述结构均清空），但 session
      记录保留、可现场恢复。

    .. seealso::

        - :meth:`flowing.runtime.Runtime.create_agent` —— 唯一创建入口。
        - :meth:`flowing.runtime.Runtime.recover_agent` —— 恢复入口。
        - :class:`flowing.message.Message` / :class:`flowing.message.MessageChain`
          —— 消息级树节点与树手术。
    """

    # ────────────────────────── 类属性（.fya 元信息即类属性） ──────────────

    class_name: ClassVar[str]
    """Python 类名（PascalCase）。``.fya`` 可显式声明 ``class_name:``；
    缺省从身份名推断——格式转换经 :func:`flowing.paths.kebab_to_pascal`，
    并始终保证以 ``Agent`` 结尾（已结尾则原样，否则补上）。手写子类即
    ``__name__``，无需声明。

    .. rubric:: 身份名的推断（``name`` 非机制字段）

    Agent 的身份名（注册表 key、``agent_type`` 裸名、引用别名缺省、
    ``class_name`` 推断的输入）一律由推断产生，框架不存在 ``name``
    机制字段。推断规则本体在 :func:`flowing.paths.infer_name`
    （去后缀、通用名取目录名、snake → kebab）：

    - 路径形态 ``.fya`` / 手写 ``.py``：文件名去 ``.agent.fya`` /
      ``.fya`` / ``.py`` 后缀；文件名是 ``agent.fya`` / ``AGENT.fya``
      这两个通用名 → 取目录名；
    - 裸名：注册表 key 本身（``register_agent_type`` 的显式参数）；
    - 手写子类：类名 kebab 化（``OrderAgent`` → ``order-agent``，经
      :func:`flowing.paths.pascal_to_kebab`）。

    ``.fya`` 与手写子类中不禁止写 ``name``，但仅作一致性断言：与推断值
    不符抛 :class:`flowing.errors.NameMismatchError` （消息含声明值 /
    推断值 / 来源）；一致时无任何效果。
    """
    description: ClassVar[Parsable]
    """Agent 的一句话介绍（Parsable），供亲代 Agent 路由决策与 catalog
    渲染读取；渲染上下文为亲代 Agent 实例。可选：缺失 / ``null`` 落
    ``None`` （消费方按空串处理）。普通字符串赋值同样可用（消费方原样
    取文本）。
    """
    metadata: ClassVar[dict[str, Any]]
    """任意静态键值对，框架不解释，透传 ``self.metadata``，供扩展 /
    Composable 读取。
    """
    system_prompt: ClassVar[Parsable]
    """唯一必填类属性：系统提示词。实例化后进入 ``prompt_blocks[0]``
    的惰性引用块（``by="core"``，内容为 ``{{ self.system_prompt }}``
    模板），每次组装上下文时现场求值——``setup()`` 中改它，下一次
    ``provider_gen()`` 自动反映。创建管线在 ``setup()`` 后检查本属性
    仍为 PENDING（未赋值）→ ``MissingFieldError``。
    """
    model_tag: str = "default"
    """声明层模型意图（``"fast" / "high" / "default"`` 是语义约定、非
    枚举），与 Provider 完全解耦；支持 Jinja2 模板（惰性求值，每次
    ``provider_gen()`` 前重新求值）。运行时可变：``agent.model_tag = "high"``
    会重新解析并覆盖 ``self.model``，只能指向配置已定义模型。
    标签未定义回退 ``default``；``default`` 也未定义 → 报错（不静默
    回退）。
    """
    args_model: ClassVar[type[BaseModel] | None]
    """实例化参数声明（Pydantic BaseModel 子类，声明即模型——同时是
    ``subagent-invoke`` 工具 LLM 可见 schema 与执行层校验的来源）。
    三种来源形态：

    - ``.fya`` 显式写 ``args:`` （纯 JSON Schema 展开式）→ 经
      :func:`flowing.params.schema_to_model` 桥接成模型，以其为准
      （YAML 优先），``setup()`` 签名仅作校验对照（参数必须有对应字段、
      类型标注必须兼容）；
    - 手写子类显式声明 ``args_model = MyArgs`` （BaseModel 子类）→
      直接使用；
    - 两者皆无 → 类创建时从 ``setup()`` 签名推导：类型标注 → 字段类型
      （标注缺失 → 构造期抛 ``ValueError``）；带默认值 → 字段默认值
      （可选）；无默认值 → 必填。``setup(**kwargs)`` 全 kwarg 吸收形态
      → 推导产物为 ``None`` （空模型不接受构造参数）。

    推导发生在类创建期而非实例化期：产物是确定的类属性，运行时无重复
    推导。
    """
    _extra: dict[str, Any]
    """``.fya`` 中框架不认识的字段（如 ``skills:`` / ``modes:``）的静默
    仓库；``__getattr__`` 回退查找。实例属性（非 ClassVar）：``__init__``
    建立为空表，``.fya`` 装配层在生成 ``setup()`` 的前置段合入未知字段。
    框架不解析其中的 Parsable——扩展自行 ``.resolve(render_context)``。
    """
    source_file: ClassVar[str | None]
    """``@/`` 格式源文件路径。类创建时经 ``__init_subclass__`` 从
    ``__module__.__file__`` 推算；``.fya`` 来源的类由装配层在生成类时
    显式注入（值为该 ``.fya`` 文件的 ``@/`` 路径，优先级最高，不走
    ``__module__`` 推算）。显式写 ``None`` 或推算失败 → ``None``。
    只读，实例化后不可变。
    """
    registry_key: ClassVar[str | None] = None
    """注册表键回写——``AgentRegistry`` 注册本类时写入（``register`` 与
    文件派生注册两处）：``ns::name`` 全限定键；从未注册的手写子类为
    ``None``。与 ``Tool.registry_key`` / ``Skill.registry_key`` 同构，
    供 ``add_agent`` 落账 ``name_ori``。内部 API，不属稳定契约。
    """
    subagent_catalog_template: ClassVar[str | None] = None
    """Agent 级 catalog 模板覆写槽位（``<available_subagents>`` 块）。

    Agent 对象自己有该属性则用其值，否则用
    :data:`flowing.subagents.DEFAULT_SUBAGENT_CATALOG_TEMPLATE`；
    ``.fya`` 中同名字段亦可（落入实例属性或 ``_extra``，``getattr``
    同样命中）。值是 Jinja2 模板源字符串，上下文变量表见
    :data:`flowing.subagents.DEFAULT_SUBAGENT_CATALOG_TEMPLATE`。
    """
    _id_prefix: ClassVar[str] = "agent"
    """``node_id`` 前缀（``runtime-*`` / ``workflow-*`` / ``agent-*``
    共享 ID 空间，看 ID 即知节点类型）。内部 API，不属稳定契约。
    """

    # ────────────────────────── 实例属性：标识与关系 ──────────────────────

    node_id: str
    """全局唯一 ID（缺省自动生成 ``agent-`` + 6 位随机 hex，create 管线
    带注册表防撞重试），生命周期内不可变；恢复路径
    ``node_id = agent_id`` （身份连续、可重现）。
    """
    runtime: Runtime
    """返指 Runtime（对象图根）；不构成循环引用——关闭时 Runtime 主动
    遍历销毁子树。内部 API，不属稳定契约。
    """
    _parent_id: str
    """亲节点 ID 字符串（非对象引用）。双重用途：① 销毁时亲节点 → 子定位；
    ② provide 链子 → 亲节点上溯（``inject_from``）。创建时绑定、终身不变的
    历史事实——根 Agent 指向 Runtime 的 ``node_id`` （``"runtime-0"``）；
    节点存活与否由 ``_nodes`` 在册与否表达，不由本字段。内部 API。
    """
    _children: dict[str, Agent]
    """生命周期子树（``node_id → 子 Agent 实例``）：创建加入、销毁移除；
    回答“谁该随我销毁”。与 ``_executions`` / provide 链正交，不可合并。
    内部 API。
    """
    child_ids: dict[str, str]
    """语义名 → agent_id 翻译表：``invoke_subagent(resume=...)`` 的按名
    查找载体——语义名只存在于唤起方（亲代 Agent）的这张表里，子实例不自
    持名字。只读 property（用户写 → ``AttributeError``）；框架内部写经
    ``_core_state["child_ids"]`` 整表写透落盘。未命名子 Agent（创建时
    ``name=None``）不入表。destroy 不删表项（记录保留，带记忆续接依赖
    它）；显式遗忘经 ``Runtime.archive_agent`` 受控清理。内部 API。
    """
    _provided: dict[str, Any]
    """provide 存储；``inject(key)`` 沿 ``_parent_id`` 链上溯至此（终点
    为 ``Runtime._provided``）。敏感信息（``user_id`` / 凭证派生值）走
    本通道——不进消息流、不进 LLM 上下文、不落盘。内部 API。
    """

    # ────────────────────────── 实例属性：核心结构 ────────────────────────

    hooks: HookRegistry
    """实例级钩子注册表——全部钩子只对当前实例生效；预填核心钩子点 +
    ``declare()`` 扩展点。``@on()`` 声明在 ``__init__`` 阶段注册，
    ``setup()`` 中 ``self.hooks.<point>(...)`` 注册的在其后。
    """
    prompt_blocks: PromptBlockList
    """system prompt 分层组装列表（ManagedList 语义）；列表顺序即拼接
    顺序（``append`` 追加尾部、``insert`` 任意位置插入）；框架注入的
    ``system_prompt`` 惰性引用块（``by="core"``）初始位于 ``[0]``。
    动态内容的正确做法是块内模板引用（惰性求值保证最新），而非每回合
    append + remove_by_tag。
    """
    _message_queue: MessageQueue
    """每实例独立的消息队列；按 ``priority`` 排序、同级 FIFO 消费；
    出队阻塞。内部 API。
    """
    _messages: dict[str, Message]
    """消息级树的内存索引（``id → Message``）；持久化文件是它的 append
    投影。fork 目标合法性与上下文组装的上溯都经它。内部 API。
    """
    chain: MessageChain
    """消息级树手术入口（``insert`` / ``branch`` / ``remove`` / ``update`` /
    ``reparent`` 五 op，最小完备集）；运行期改内存链 + append 变更
    记录，压缩期重写。
    """
    current_head_id: str | None
    """消息级树游标——指向某条消息的 id，即“添加节点的位置”：新消息
    链到它并随即将它前移；fork 即切换它。空树（新 Agent）为 ``None``。

    只读 property（用户写 → ``AttributeError``）；框架内部写透落盘
    core.jsonl，恢复时以袋值为准、不校验树。
    """
    current_turn: TurnContext | None
    """当前活跃逻辑 Turn 的执行期临时对象；回合收尾（``after_turn``
    钩子之前）置 ``None``。``None`` 语义 = 回合物质上不存活（不再产生
    消息）；快照投影只在执行期间可见。
    """
    last_result: Any
    """最近一次逻辑 Turn 的最终产出（文本或 finish 结构化输出）；亲代
    Agent 侧由 ``invoke_subagent`` 读取它构造 ``SubagentResult.result``。
    未产生过结果为 ``None``。

    填充规则（回合收尾时、resolve 等待者之前写入）：``turn.finish_output``
    非 ``None`` → 该 dict（finish 结构化交卷）；否则 → 本轮最后一条
    PROVIDER 消息的文本，为空串则写 ``None``。abort / cancel / error
    路径同样覆写。``side_query`` 不写本字段。
    """
    _executions: dict[str, Execution]
    """执行追踪注册表（动态结构：仅“有正在运行的异步操作”时有条目）；
    注册 / 清理 finally 成对；``cancel`` / ``stop`` 族遍历它置位信号。
    回答“谁在运行、谁可取消”。内部 API。
    """
    _pending_turns: dict[str, asyncio.Future[TurnResult]]
    """``message_id → Future`` 等待句柄，纯运行时不持久化（future 不可
    序列化；恢复后的回合没有等待者）。内部 API。
    """
    _measured_tool_names: set[str]
    """本进程内已被实测覆盖过的工具规范名集——``provider_gen()`` 每次
    收到带 ``usage`` 的响应时，把当时 ``Context.tools`` 的名字并入。
    仅服务于 :meth:`estimate_context_tokens` 的“锚点后新增工具补估”
    规则；不持久化（重启后清空，首次估计把全部工具算进 ``estimated``
    而暂时偏高，下一次带实测的 ``provider_gen`` 自愈）；纯内存、不进
    快照。内部 API。
    """

    # ────────────────────────── 实例属性：资源绑定与模型 ──────────────────

    _tool_entries: dict[str, ToolEntry]
    """工具绑定条目（key 为别名）；``tool_call()`` 仅按别名查找。
    内部 API。
    """
    _subagent_entries: dict[str, SubagentEntry]
    """子 Agent 绑定条目（key 为别名）；catalog 渲染与
    ``invoke_subagent`` 的 resolve 依据。内部 API。
    """
    model: ModelConfig
    """模型结构体（运行时成员，可解析、可变）；实例化时由 ``model_tag``
    解析填充初始值。两条动态修改路径：① 改 ``model_tag`` （只能指向配置
    已定义模型）；② 直接赋 ``ModelConfig(...)`` （任意模型）。“换模型”
    = 换一份完整规格，无中间态。
    """

    # ────────────────────────── 实例属性：控制信号（内部） ────────────────

    _pause_gate: asyncio.Event
    """工作循环协作式 gate，初始放行；``pause()`` clear / ``resume()``
    set。三个检查点（dequeue / provider_gen / toolcall 前）均等待它，
    且每处 pause 在前、abort 在后。内部 API。
    """
    _turn_abort: asyncio.Event
    """每个逻辑 Turn 独立的退出信号（回合执行体入口新建）；置位后由三
    检查点 / ``provider_gen()`` 开头 / 工具包装层检测。内部 API。
    """
    _loop_task: asyncio.Task
    """常驻工作循环 Task 的具名句柄。创建 / 恢复管线在 ``after_create`` /
    ``after_recover`` 完成后赋值；``destroy()`` 经本句柄取消它。内部
    API。
    """

    # ─────────────────── 实例属性：持久化（内部） ────────────────────────

    _session_dir: Path
    """本 Agent 的 session 持久化目录（``tree.jsonl`` / ``core.jsonl`` /
    ``state.jsonl`` / ``meta.json`` 所在目录）。

    创建管线预绑：``Runtime.create_agent(session_dir=...)`` 指定（绝对
    路径原样 / 相对 ``runtime._persist_dir``），缺省
    ``persist_dir / node_id``；恢复路径从池元数据回绑。子类可在
    ``super().__init__()``
    之前覆写本字段实现“初始化时指定”。内部 API，不属稳定契约。
    """
    _tree_store: RecordStore
    """``tree.jsonl`` 的落盘后端（write-behind：提交同步排队、drain
    任务串行落盘；墓碑压缩在 store 内部自主触发）。构造在
    ``__init__`` （经 :meth:`_open_stores`）。内部 API，不属稳定契约。
    """
    _core_state: StateView
    """核心状态袋（``core.jsonl`` 后端内嵌，``merge_last_line=True``）；
    框架私有——真状态 2 键（child_ids + current_head_id，property
    透传）。不对外暴露（用户经 :attr:`state` 或 :meth:`register_state`
    开新袋，不碰 core）。构造点同为 ``__init__`` （经
    :meth:`_open_stores`）。内部 API。
    """
    _state_bag: StateView
    """默认状态袋（``state.jsonl`` 后端内嵌，``merge_last_line=True``）；
    :attr:`state` property 的落点；业务 / 插件状态键（``state.register``
    缺省落点）。构造点同为 ``__init__`` （经 :meth:`_open_stores`）。
    内部 API。
    """

    # ────────────────────────── 构造与内部初始化 ──────────────────────────

    def __init__(self) -> None:
        """同步骨架——只做赋值，不做任何 I/O（内部 API，不属稳定契约）。

        实例化不由用户直接调用：创建走 ``Runtime.create_agent`` 管线
        （前处理已分配 ``node_id`` / ``runtime`` / ``_parent_id`` /
        ``_session_dir``），恢复走 ``Runtime.recover_agent``。

        职责（顺序不敏感，全部同步）：

        - 建立全部空结构：``_children`` / ``_provided`` / ``hooks`` /
          ``prompt_blocks`` / ``_message_queue`` / ``_messages`` / ``chain`` /
          ``_executions`` / ``_pending_turns`` / ``_tool_entries`` /
          ``_subagent_entries`` 等；``current_turn = None``、``_pause_gate``
          初始放行。
        - 建立 ``_extra`` （实例级静默仓库）与持久化后端——经
          :meth:`_open_stores` （session 目录由管线预绑，骨架期即可知）。
        - 注入 ``system_prompt`` 惰性引用块（``prompt_blocks[0]``，
          ``by="core"``）。
        - 调 :meth:`_init_hooks` 同步注册 ``@on()`` 声明的钩子。

        开发者自己引入的装配逻辑（依赖 args / inject 的赋值、业务钩子
        注册、Composable 启用）放 :meth:`setup`。
        """
        # _extra 与状态袋在 __init__ 建立——管线第 2 步
        # （__new__ 绑 node_id / runtime / _parent_id）先于 __init__，
        # session 目录骨架期即可知，“尚不可知”的旧表述作废
        self._extra = {}   # 实例级静默仓库；fya 装配层在生成 setup() 前置段合入未知字段
        self._open_stores(self._session_dir)   # 持久化后端（换装点，见 _open_stores；session 目录由管线预绑——create_agent(session_dir=...) 或默认 persist_dir/node_id，子类可于 super().__init__() 前覆写 self._session_dir）
        self._children = {}
        self._state_bags: dict[str, StateView] = {}   # 命名状态空间注册表（D3：name → 视图；destroy 全关 + 幂等）
        self._provided = {}
        self.hooks = HookRegistry()
        self.prompt_blocks = PromptBlockList()
        self._message_queue = MessageQueue()   # 无参：纯内存优先级队列
        self._messages = {}
        self.chain = MessageChain(self)   # 构造收属主 Agent——五 op
        # 直接操作 Agent._messages 并经 Agent._persist_message 落盘
        self._executions = {}
        self._background_tasks: dict[str, asyncio.Task] = {}   # 后台任务注册表（B9：按 id 取消/查询 + 强引用防 GC + destroy 统一覆盖；cancel_by_tag 不涉及）
        self._task_seq = 0   # 后台任务 id 自增计数器（纯内存：注册表不落盘，恢复后为空，计数随之归零）
        self._execution_seq = 0   # Execution id 自增计数器（同上，纯内存）
        self._pending_turns = {}
        self._tool_entries = {}
        self._subagent_entries = {}
        self.current_turn = None
        self.last_result = None   # 未产生过结果为 None（_run_turn 统一收尾段覆写）
        self._measured_tool_names = set()   # 估算锚点记账（provider_gen 并入；纯内存不持久化）
        self._pause_gate = asyncio.Event()
        self._pause_gate.set()   # 初始放行
        self._turn_abort = asyncio.Event()
        # 注入 system_prompt 惰性引用块（prompt_blocks[0]，by="core"；
        # cache 取 append 默认值 "dynamic"——该标记暂无消费方（内置
        # adapter 不消费；每次 provider_gen 都现场重新解析，与取值无关），
        # 且不推荐 prompt 块放真正频繁变动的内容（未来 adapter 消费
        # 该标记时会破坏 provider 侧前缀缓存），故无需显式标 static）
        self.prompt_blocks.append("system_prompt", Parsable("{{ self.system_prompt }}"), by="core")   # 类属性经渲染上下文 self 入口访问（不在 __dict__ 摊平里）
        self._init_hooks()   # 同步注册 @on() 声明的钩子

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """``source_file`` 自动推算（内部 API，不属稳定契约）。

        类创建时执行：``source_file`` 未显式赋值 → 从 ``__module__.__file__``
        推算为 ``@/`` 格式（项目根之外或无法推算 → ``None``）；已显式赋值
        则尊重（含 ``None``；``.fya`` 装配层显式注入的值优先级最高）。
        """
        super().__init_subclass__(**kwargs)
        # source_file is _UNSET -> 从 __module__.__file__ 推算为 "@/" 格式
        # （失败 -> None）；非 _UNSET -> 尊重显式值（.fya 注入优先）
        # （_UNSET 哨兵见 flowing.parsable；"@/" 推算经 runtime.resolve_path）
        if getattr(cls, "source_file", _UNSET) is not _UNSET:
            return   # 显式值（含 None；.fya 装配层注入优先）
        module_file = getattr(sys.modules.get(cls.__module__), "__file__", None)
        if module_file is None:
            cls.source_file = None   # 推算失败 -> None
            return
        # 经 launch 上下文的当前项目根换算 "@/" 格式；无 launch 上下文（裸
        # Runtime 不存在 / 测试直接定义子类）时推算失败 -> None
        try:
            from flowing.runtime import _current_project_root
            root = _current_project_root.get()
        except Exception:
            root = None
        if root is None:
            cls.source_file = None
            return
        try:
            cls.source_file = "@/" + str(
                Path(module_file).resolve().relative_to(Path(root).resolve()))
        except ValueError:
            cls.source_file = None   # 项目根之外的源文件：无 "@/" 表达 -> None

    def __getattr__(self, name: str) -> Any:
        """回退查找链：``_extra`` （内部 API，不属稳定契约）。

        ``name`` 在 ``_extra`` 中 → 返回 ``_extra[name]`` （原值返回，不做
        隐式 Parsable 解包）；否则 ``AttributeError``。状态量不在回退链上
        ——读写统一走 ``agent.state`` 显式视图。体内只经 ``__dict__.get``
        探查 ``_extra``：子类在 ``super().__init__()`` 之前赋值等场景下
        ``_extra`` 尚未建立，回退路径整体短路，干净抛 ``AttributeError``
        而非递归错误。
        """
        extra = self.__dict__.get("_extra")   # 护栏：未建立 -> 跳过（不经属性查找，天然无递归）
        if extra is not None and name in extra:
            return extra[name]   # 原值返回，不做隐式 Parsable 解包
        raise AttributeError(name)

    def _init_hooks(self) -> None:
        """把 ``@on()`` 声明的钩子注册到实例 ``hooks`` （同步；内部 API）。

        ``__init__`` 阶段执行，早于 ``setup()``——这就是 ``before_create``
        只能经 ``@on('before_create')`` 声明的原因（setup 尚未运行时无法
        ``self.hooks`` 注册）。

        - 扫 ``type(self).__mro__`` 逐名解析：每个属性名以派生优先取最终
          定义（子类覆写未标记的同名方法，基类的标记不生效——覆写即覆盖，
          与 Python 方法解析一致）。
        - 最终定义带 ``__flowing_hooks__`` 标记的成员：按绑定方法逐条记录
          注册，顺序为基类 → 派生类（同名钩子点上基类 handler 先挂）。
        - 两段式注册：钩子点已存在（核心预填点）→ 立即注册；尚未声明
          （插件点）→ 记入 ``self.hooks._pending_on``，待
          :meth:`flowing.hooks.HookRegistry.declare` 冲刷。``setup()`` 后
          PENDING 检查发现暂记列表非空 → ``UnknownHookPointError``。
        """
        # 收集原语：MRO 派生→基类方向判覆写（每个名字只认最派生的
        # 最终定义），收集后按基类→派生类顺序注册
        seen: set[str] = set()
        marked: list[tuple[Any, tuple]] = []   # （最终定义函数， 记录 tuple）
        for cls in type(self).__mro__:
            for attr_name, member in vars(cls).items():
                if attr_name in seen:
                    continue   # 更派生的类已裁定此名字（覆写即覆盖）
                seen.add(attr_name)
                marks = getattr(member, "__flowing_hooks__", None)
                if marks:
                    marked.append((member, marks))
        for member, marks in reversed(marked):   # 基类 → 派生类
            # spec 未写清处落实（“按绑定方法注册”与 dispatch 的 (agent, value)
            # 统一签名冲突——绑定方法会多收一个位置参数）：注册未绑定函数，
            # dispatch 时首参 agent 恰好落进方法的 self 位（与 .fya $script
            # 的 def _(self, tool_call) 写法相容）
            for hook_name, by, tags, pattern in marks:
                point = self.hooks._hook_points.get(hook_name)
                if point is None:
                    # 钩子点尚未声明（插件点，等 use_xxx 的 declare 冲刷）；
                    # setup 后 PENDING 检查仍非空 → UnknownHookPointError
                    self.hooks._pending_on.append((member, hook_name, by, tags, pattern))
                elif pattern is None:
                    point(member, by=by, tags=tags)
                else:
                    point[pattern](member, by=by, tags=tags)

    # ────────────────────────── 生命周期 ──────────────────────────────────

    @property
    def child_ids(self) -> dict[str, str]:
        """语义名 → agent_id 翻译表——core 袋唯一真值（内部 API）。

        只读（用户写 → ``AttributeError``）；框架内部写经
        ``_core_state["child_ids"]`` 整表写透落盘。骨架期（袋未建立）返回
        空表。
        """
        bag = self.__dict__.get("_core_state")
        if bag is None:
            return {}
        try:
            return bag["child_ids"]
        except KeyError:
            return {}

    @property
    def current_head_id(self) -> str | None:
        """消息级树游标——core 袋真值（内部 API）。

        只读（用户写 → ``AttributeError``）；框架内部写经
        ``_core_state["current_head_id"]`` 写透落盘，恢复以袋值为准、不
        校验树。骨架期（袋未建立）返回 ``None``。
        """
        bag = self.__dict__.get("_core_state")
        if bag is None:
            return None
        try:
            return bag["current_head_id"]
        except KeyError:
            return None

    def _open_stores(self, session_dir: Path) -> None:
        """建立本 Agent 的持久化后端（内部 API，不属稳定契约）。

        构造点为 ``__init__``：session 目录由管线预绑
        （``create_agent(session_dir=...)`` 或默认 ``persist_dir / node_id``），
        骨架期即可知。建立三个后端：

        - ``_tree_store`` —— ``tree.jsonl`` 的
          :class:`~flowing.persistence.FileRecordStore`；
        - ``_core_state`` —— 核心状态袋（``core.jsonl`` 内嵌，
          ``merge_last_line=True``；框架私有：child_ids / current_head_id）；
        - ``_state_bag`` —— 默认状态袋（``state.jsonl`` 内嵌，
          ``merge_last_line=True``；:attr:`state` property 返回）。

        身份四键（agent_type / parent_agent_id / created_at / args）不在
        本方法——独立 ``meta.json`` 由 Runtime 管线整写。命名状态空间
        （:meth:`register_state`）也不在此（独立文件、即时重放）。本方法
        是后端换装点：未来非文件后端在此替换 ``FileRecordStore`` 构造。
        """
        self._tree_store = FileRecordStore(session_dir / "tree.jsonl")
        self._core_state = StateView(
            FileRecordStore(session_dir / "core.jsonl", merge_last_line=True))
        self._state_bag = StateView(
            FileRecordStore(session_dir / "state.jsonl", merge_last_line=True))
        # 核心键初始槽（直写 _persisted 不落盘——create 首次写透才产生行；
        # recover 由 _restore 重放覆盖；D4 后 register 缺省即写会落盘，
        # 初始化语义不走 register 以避免恢复期产生脏行）
        self._core_state._persisted.setdefault("child_ids", {})
        self._core_state._persisted.setdefault("current_head_id", None)
        self._core_state._persisted.setdefault("message_seq", 0)   # 消息 id 自增计数器（持久化：恢复续接，tombstone/压缩不影响）

    def register_state(self, name: str, backend: str = "file") -> StateView:
        """开启一个命名状态空间，返回其 :class:`StateView` （公共 API）。

        .. rubric:: 功能介绍

        打开本 Agent 的一个命名状态袋：``<session_dir>/<name>.jsonl``。
        与 :attr:`state` （默认袋）对称的扩展通道：命名袋是插件 / 领域的
        独立状态空间（独立文件、独立压缩 / 崩溃边界），不挂载任何 agent
        属性——调用方自行持有返回的袋对象。

        .. rubric:: 行为要点

        - 幂等：同 ``name`` 重复调用返回同一视图（同一文件一视图）。
        - ``backend`` 当前仅 ``"file"``；其它值 → ``ValueError``。
        - ``name`` 校验：合法文件名（不含路径分隔符 / ``..``）；不与
          保留文件 ``state`` / ``core`` / ``tree`` / ``meta`` 重名 →
          否则 ``ValueError``。
        - 打开即重放：持久值立即装袋（开空间即恢复）。
        - 键登记走 ``view.register(key, default)``。

        :param name: 命名状态空间名。
        :param backend: 后端（当前仅 ``"file"``）。
        :return: 命名袋的 :class:`StateView`。

        .. seealso:: :attr:`state`、:class:`flowing.persistence.StateView`
        """
        if backend != "file":
            raise ValueError(f"register_state backend only supports 'file': {backend!r}")
        existing = self._state_bags.get(name)
        if existing is not None:
            return existing   # 幂等：同 name → 同视图
        # name 校验：合法文件名（无路径分隔符 / ..）；不与保留文件重名
        if not name or name in ("state", "core", "tree", "meta"):
            raise ValueError(
                f"state namespace name is unavailable: {name!r} (empty or reserved name state/core/tree/meta)")
        if "/" in name or "\\" in name or ".." in Path(name).parts:
            raise ValueError(
                f"state namespace name must not contain path separators or ..: {name!r}")
        view = StateView(FileRecordStore(
            self._session_dir / f"{name}.jsonl", merge_last_line=True))
        # 即时恢复：重放持久值进 _persisted（开空间即恢复）
        persisted = view._persisted
        for record in list(view._store.replay()):   # replay 是惰性生成器——显式消费
            op = record.get("op")
            if op == "set":
                persisted[record["key"]] = record["value"]
            elif op == "delete":
                persisted.pop(record["key"], None)
            # 未知行形态（meta 已被 replay 吸收）静默跳过
        if persisted:
            view._maybe_compact(force=True)
        self._state_bags[name] = view
        return view

    @property
    def state(self) -> StateView:
        """本 Agent 默认状态袋的视图——钩子 / 工具 / 插件代码里以 agent
        视角读写持久化状态的唯一默认通道（公共 API）。

        ``agent.state.cron_jobs`` 属性式、``agent.state["weird-key"]``
        字典式；读写语义（写透落盘、defaults 回退、fail fast）全部继承
        :class:`flowing.persistence.StateView` 的类级契约。写透不触发
        watcher（watch 不察觉 state）。扩展状态空间经 :meth:`register_state`
        开启（命名袋，独立文件，不挂载属性）。本 property 自身无副作用。

        .. seealso:: :meth:`register_state`、:meth:`get`、:meth:`set`、
            :meth:`delete`
        """
        return self._state_bag   # 单袋视图（_open_stores 建立，无副作用）

    def get(self, key: str, default: Any = None) -> Any:
        """动态键统一读——状态键 / 实例与类属性 / ``_extra`` 三域兼容。

        字符串键的通用读取入口（key 是变量的场景：插件写通用逻辑、slash
        命令、调试工具）。查找序：已注册状态键 → 从 ``agent.state`` 读出
        （持久值，无则回退 ``default``）；否则 ``getattr`` （实例属性 →
        类属性 → ``_extra`` 回退）；仍无 → 返回 ``default``。

        :param key: 字符串键。
        :param default: 三域均未命中时的返回值（默认 ``None``）。

        .. seealso:: :meth:`set`、:meth:`delete`、:attr:`state`
        """
        bag = self.__dict__.get("_state_bag")   # 护栏：袋未建立时跳过
        if bag is not None and key in bag:
            return bag[key]   # 状态域：持久值 ?? default
        return getattr(self, key, default)   # 属性域 + _extra 回退（__getattr__）

    def set(self, key: str, value: Any) -> None:
        """动态键统一写——状态键 / ``_extra`` / 实例属性三域路由。

        - 已注册状态键 → ``self.state[key] = value`` （写透落盘 + JSON
          校验；写不触发 watcher）。
        - ``key`` 在 ``_extra`` 中 → 原地更新（静默仓库，不触发 watcher）。
        - 否则 ``setattr(self, key, value)``——普通实例属性路径
          （``__setattr__`` 的 watcher 通知照常）。

        .. seealso:: :meth:`get`、:meth:`delete`
        """
        bag = self.__dict__.get("_state_bag")
        if bag is not None and key in bag:
            bag[key] = value   # 状态域：写透 + watcher 通知（StateView 写路径）
            return
        extra = self.__dict__.get("_extra")
        if extra is not None and key in extra:
            extra[key] = value   # _extra 原地更新（静默仓库，不触发 watcher）
            return
        setattr(self, key, value)   # 属性域：__setattr__ 拦截照常

    def delete(self, key: str) -> None:
        """动态键统一删——三域路由（语义同 :meth:`set` 的镜像）。

        - 已注册状态键 → ``del self.state[key]`` （删持久值，读回退 default；
          不触发 watcher——删除不是赋值事件）。
        - ``key`` 在 ``_extra`` 中 → 移除该键。
        - 否则 ``delattr(self, key)`` （普通实例属性删除；类属性 / 方法删
          不掉，``AttributeError`` 原样上抛）。

        .. seealso:: :meth:`get`、:meth:`set`
        """
        bag = self.__dict__.get("_state_bag")
        if bag is not None and key in bag:
            del bag[key]   # 状态域：删持久值，读回退 default
            return
        extra = self.__dict__.get("_extra")
        if extra is not None and key in extra:
            del extra[key]
            return
        delattr(self, key)

    async def setup(self, **kwargs: Any) -> None:
        """实例初始化生命周期方法——子类覆写的装配入口。

        .. rubric:: 功能介绍

        创建管线内被调用（``before_create`` 之后、PENDING 检查之前）；
        恢复管线同样调用本方法（args 为持久化值，可被 ``recover_agent``
        的 ``override_args`` 覆盖，触发的是 ``before_recover`` /
        ``after_recover`` 钩子对）。签名即 args 来源（与 ``args_model``
        对照，见 :attr:`args_model`）。与 ``__init__`` 的分工：核心必要
        的初始化全部在 ``__init__`` （同步骨架，无外部输入）；``setup``
        只承载开发者引入的装配逻辑——依赖 args 与 inject 的赋值、业务
        钩子注册、Composable 启用。

        .. rubric:: 使用示例

        .. code-block:: python

            async def setup(self, user_id: int, order_id: str, locale: str = "zh"):
                self.user_id = user_id
                self.order_id = order_id
                self.locale = locale
                self.provide("user_id", user_id)     # 敏感信息走注入通道
                use_retry(self, max_retries=2)       # 可选 Composable（幂等注册）

        .. rubric:: 行为要点

        - 实例级单次：``setup()`` 的语义效果与执行它的实例无关——create
          与 recover 两条管线各自在一个新建实例上执行同一份装配代码，
          每个实例恰好执行一次。装配以新实例的钩子注册表与状态袋为
          起点展开（declare 钩子点、注册 handler、provide、Composable），
          各实例装配结果一致；普通实例属性赋值是运行期配置，不落盘、
          不加限制；持久化 state 经 ``agent.state.register`` 键登记、
          setup 中写 state 合法（recover 先重放后 setup，写入不被
          覆盖）。
        - 期待行为：状态赋值、钩子注册、inject 读取、Composable 调用。
        - 轻量约束：网络请求 / 文件 I/O / 大量计算移到工具调用或按需
          阶段。第一个 ``await`` 之前的代码不被其它协程打断——关键初始化
          放第一个 ``await`` 前。
        - 后置条件：返回后框架做 PENDING 检查——仍有 PENDING 字段（如
          必填 ``system_prompt`` 未赋值）→ ``MissingFieldError``。

        .. seealso::

            - :meth:`flowing.runtime.Runtime.recover_agent` —— 恢复管线
              宿主（恢复时在新实例上执行本方法）。
            - :meth:`destroy` —— 对等销毁入口。
        """
        # 基类空实现（契约注释）：子类覆写承载装配逻辑——状态赋值、钩子注册、
        # inject 读取、Composable 调用；setup 中写 state 合法（D5）；实例级
        # 单次语义见上文 docstring（每个实例恰好执行一次）。
        ...

    async def destroy(self) -> None:
        """递归销毁管线入口——实例丢弃，session 记录保留（公共 API）。

        .. rubric:: 功能介绍

        销毁本实例及其整个生命周期子树。时序（顺序为不变量）：

        1. resolve 全部 ``_pending_turns`` （``TurnResult(status="cancelled")``）
           ——调用方不挂起；
        2. 取消常驻工作循环 Task + 取消全部后台任务（只 cancel 不 await，
           await 无界会让 destroy 挂起）→ 关闭持久化后端（``close()`` =
           排空写队列 + 停写任务——不排空即销毁会静默丢尾部记录）；
        3. dispatch ``before_destroy``；
        4. 深度优先递归 ``child.destroy()`` （子树收集：``_children`` ∪
           ``_nodes`` 按 ``_parent_id`` 扫描），清空 ``_children``；
        5. 从 ``_nodes`` 摘除（池移除实例值）；
        6. dispatch ``after_destroy``。

        .. rubric:: 行为要点

        - destroy ≠ 删除：只丢实例；session（tree.jsonl + state.jsonl）
          与池 key 保留到显式删除目录——“有 key 无 value → 现场恢复”
          （``Runtime.get_agent`` 触发）。
        - 幂等：重复调用安全（二次调用时子树已空、已摘除，直接返回）。
        - 与回合收尾窗口的关系：destroy 不以 ``current_turn`` 为守卫，
          回合收尾观察窗口（``after_turn`` 钩子运行期间）调用 destroy
          合法——取消工作循环 Task 可能中断 finally 的交付段，但第 1 步
          会把 ``_pending_turns`` 全部 resolve（cancelled），等待者不挂起；
          ``after_turn`` 钩子可能被中断跳过，调用方须自知。
        - 注意：``before_destroy`` 触发时持久化后端已关闭——handler 中
          写状态不会落盘。
        - 销毁后实例不可用：``inject`` / ``message`` / ``tool_call`` 等
          行为无契约保证。
        - ``_parent_id`` 不在销毁时改写：id 是创建时绑定的历史事实。

        .. seealso::

            - :meth:`flowing.runtime.Runtime.get_agent` —— 现场恢复入口。
            - :meth:`flowing.runtime.Runtime.shutdown` —— 进程级收尾。
        """
        # 幂等守卫（docstring 行为要点：重复调用安全，二次调用“直接返回”）：
        # **本实例**已摘除即二次调用，直接返回——按身份比较而非 id：本 id
        # 可能已被“有 key 无 value → 现场恢复”重建为新实例重新注册，
        # 旧实例（如亲节点 _children 里的过期引用）不得再操作已关闭的后端
        if self.runtime._nodes.get(self.node_id) is not self:
            return
        for fut in list(self._pending_turns.values()):   # 1. resolve 所有 pending
            if not fut.done():
                fut.set_result(TurnResult(
                    turn=TurnContext(started_at=datetime.now(), message_ids=[]),
                    final_text="", status="cancelled", token_usage=None,
                    finish_reason="cancelled"))
        self._pending_turns.clear()
        # 1.5（B11）：后台任务统一取消 + 显式清空——后台任务不在工作循环栈内，
        # loop_task.cancel() 覆盖不到，必须显式取消；只 cancel 不 await（执行体
        # 可忽略取消，await 无界会让 destroy 挂起）；clear() 不依赖 done_callback
        # 时序（destroy 后注册表立即为空）
        self.cancel_all_background_tasks()
        self._background_tasks.clear()
        # 2. 取消工作循环 Task（具名句柄 _loop_task，由管线第 10 步赋值）；
        # 幂等与骨架期护栏：loop 未启动（__dict__ 无句柄）时跳过
        loop_task = self.__dict__.get("_loop_task")
        if loop_task is not None:
            loop_task.cancel()   # cancel 注入点 = 工作循环当前悬停的 await
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task   # 等循环 finally 落地后再关后端（防关库后提交）
        await self._tree_store.close()   # 2b. 排空屏障 + 停写任务（契约②钉死点）
        # 2c. 压缩三时点②：destroy 收尾双袋 + 全部已打开命名袋全量压缩
        #     （请求随 _close 排空一并执行）
        self._core_state._maybe_compact(force=True)
        await self._core_state._close()
        self._state_bag._maybe_compact(force=True)
        await self._state_bag._close()
        for view in self._state_bags.values():   # 命名袋（D3）：枚举全部逐个关闭
            view._maybe_compact(force=True)
            await view._close()
        await self.hooks.before_destroy.dispatch(self)   # 3.
        # 4. 深度优先递归（双来源收集：_children 生命周期子树 ∪ _nodes 按
        #    _parent_id 扫描——后者覆盖“destroy 后现场恢复”重新注册的同 id
        #    新实例（旧引用已随本例的幂等守卫失效）；重复/过期引用由
        #    child.destroy() 的幂等守卫兜住）
        seen_children: set[int] = set()
        for child in [*list(self._children.values()),
                      *[node for node in self.runtime._nodes.values()
                        if getattr(node, "_parent_id", None) == self.node_id]]:
            if id(child) in seen_children:
                continue
            seen_children.add(id(child))
            await child.destroy()
        self._children.clear()
        self.runtime._nodes.pop(self.node_id, None)     # 5. 从 _nodes 摘除（池 key 保留）
        await self.hooks.after_destroy.dispatch(self)    # 6.
        # _parent_id 不改写：id 是历史事实，不承担死活语义（见 docstring 时序说明）

    async def _restore(self) -> None:
        """从自己 session 目录重放持久化记录（内部 API，不属稳定契约）。

        只有恢复管线调用，时机固定在 ``__init__`` 之后、``before_recover``
        之前：读自己目录的 ``tree.jsonl`` 逐行重建消息级树（``Message.id``
        + ``parent_id`` 链；撕裂末行丢弃；低版本经
        :data:`flowing.persistence.MIGRATIONS` 迁移链升级并回写）→ 读
        ``core.jsonl`` 恢复核心袋（child_ids / current_head_id，head 以
        袋值为准）→ 读 ``state.jsonl`` 将全部持久化行重放进默认袋。恢复
        边界 = 已持久化消息；进行中的逻辑 Turn 丢弃不续跑。孤立 tool_call
        （PROVIDER 消息引用、但结果消息缺失）合成 ``synthetic=True``
        占位 TOOL 消息封闭配对，占位 id 为确定性的 ``synthetic-{call_id}``
        （同一次调用每次恢复合成同一 id，parent 链自愈）；若 provider
        消息本是分支尾，head 随占位消息上移。纯数据之外派生的运行时结构
        （如 cron 定时器）由随后的 ``after_recover`` 钩子重建。session
        目录不存在时按空 session 处理并报出可诊断错误。
        """
        # ① self._tree_store.replay() 逐行重建消息级树（Message.id +
        #    parent_id 链；消息行建树、tombstone/update/move 变更行按序
        #    做手术；撕裂末行由 replay 截断丢弃；悬空变更行（墓碑压缩后
        #    目标可缺失）跳过容忍——不当作 corruption）
        if not self._session_dir.exists():
            # 池元数据与目录不一致：按空 session 处理 + 可诊断告警
            _logger.warning("agent %s: session dir missing; restoring with an empty session: %s",
                            self.node_id, self._session_dir)
        order: list[str] = []   # 消息行序（head 推导：最后一条存活的已挂树消息）
        for record in list(self._tree_store.replay()):   # replay 是惰性生成器——显式消费
            rtype = record.get("type")
            if rtype == "message":
                msg = from_record(record)
                self._messages[msg.id] = msg
                order.append(msg.id)
            elif rtype == "tombstone":
                rid = record.get("id")
                self._messages.pop(rid, None)   # 悬空墓碑容忍（目标缺失跳过）
                if rid in order:
                    order.remove(rid)
            elif rtype == "update":
                target = self._messages.get(record.get("id"))
                if target is not None:   # 悬空 update 容忍
                    target.content = [_block_from_record(b)
                                      for b in record.get("content", [])]
            elif rtype == "move":
                target = self._messages.get(record.get("id"))
                if target is not None:   # 悬空 move 容忍
                    target.parent_id = record.get("parent_id")
        # ② 读 core.jsonl（_core_state._store.replay()）逐键重放进核心袋
        #    （直写 _persisted 绕过写通道；current_head_id 落盘此袋——
        #    恢复 head 以袋为准、不树校验，决策 9）
        core_persisted = self._core_state._persisted
        for record in list(self._core_state._store.replay()):
            op = record.get("op")
            if op == "set":
                core_persisted[record["key"]] = record["value"]
            elif op == "delete":
                core_persisted.pop(record["key"], None)
            # 未知行形态（meta 已被 replay 吸收）静默跳过
        # ①b 孤立 tool_call 合成占位（配对锚为消息字段）：
        #    扫描 PROVIDER 消息的 ToolCallBlock.id，全局（全树）无
        #    tool_call_id 匹配的 TOOL 消息者，经 chain.insert 合成占位消息
        #    挂树**落盘**封闭配对（消息行 + 邻接调整记录一并写回，占位出现在
        #    “调用之后、既有后续之前”的链上位置）：
        #    Message(kind=TOOL, tool_call_id=<孤立调用 id>,
        #            tool_status="error", synthetic=True,
        #            content=[TextBlock(占位说明)])
        #    （不再是合成 ToolResultBlock；content 为纯内容块）
        answered = {m.tool_call_id for m in self._messages.values()
                    if m.kind is MessageKind.TOOL}
        orphans: list[tuple[str, str]] = []   # (provider 消息 id, 孤立调用 id)，按行序确定序
        for mid in order:
            m = self._messages.get(mid)
            if m is None or m.kind is not MessageKind.PROVIDER:
                continue
            for block in m.content:
                if isinstance(block, ToolCallBlock) and block.id not in answered:
                    orphans.append((mid, block.id))
        for provider_id, call_id in orphans:
            placeholder = Message(
                # 确定性 id（审查修复）：同一孤立调用每次恢复合成同一 id——
                # 恢复后继续对话的新消息以 parent_id=占位.id 落盘，二次恢复时
                # 占位落回同一 id，parent 链自愈；随机 id 会让旧子消息悬空、
                # 重放后历史静默截断
                id=f"synthetic-{call_id}",
                kind=MessageKind.TOOL, tool_call_id=call_id,
                tool_status="error", synthetic=True,
                content=[TextBlock(
                    text=f"tool call {call_id} result is missing (the tool crashed mid-execution, "
                         "or the result message was removed); "
                         "placeholder message synthesized on restore.")])
            # 落盘封闭（树内永远成对的恢复期保障）：insert 把占位挂在调用直接
            # 后继并持久化邻接调整（既有子消息重挂到占位之下，各自子树随之整体
            # 移动）；已落盘的占位在 answered 中，二次恢复不重复合成
            self.chain.insert(provider_id, placeholder)
            if self.current_head_id == provider_id:
                # provider 消息本是分支尾：占位消息成为新尾，head 随之上移
                # （写袋——head 以袋为准）
                self._core_state["current_head_id"] = placeholder.id
        # ③ 读 state.jsonl（_state_bag._store.replay()）逐键重放进默认袋
        #    （直写 _persisted 绕过写通道；无 schema：持久化键
        #    无论登记与否一律装袋——逐键覆盖 register 的初值
        #    是“已持久值优先”语义的天然结果（D4）；child_ids 已在 core 袋
        #    重放中装袋——core 袋唯一真值，无内存镜像）
        persisted = self._state_bag._persisted
        for record in list(self._state_bag._store.replay()):
            op = record.get("op")
            if op == "set":
                persisted[record["key"]] = record["value"]
            elif op == "delete":
                persisted.pop(record["key"], None)
            # 未知行形态（meta 已被 replay 吸收）静默跳过
        # ④ 压缩三时点①：恢复重放后请求双袋全量压缩
        #    （_maybe_compact(force=True)；物理重写在 drain 任务）
        self._core_state._maybe_compact(force=True)
        self._state_bag._maybe_compact(force=True)
        # ⑤ 派生运行时结构（cron 定时器等）由随后的 after_recover 钩子重建

    # ────────────────────────── 消息进入与等待 ────────────────────────────

    async def query(
        self,
        content: str | list[ContentBlock],
        kind: MessageKind = MessageKind.USER,
        **kwargs: Any,
    ) -> TurnResult:
        """统一入口：打包 + 入队 + 等待“包含我这条消息的回合”产物。

        .. rubric:: 功能介绍

        ``content: str`` 自动打包为 ``[TextBlock(text=content)]``；
        ``list`` 直接作为 ContentBlock 列表。``kind`` 默认 ``USER``，可传
        ``SYSTEM`` / ``EVENT`` / ``PEER`` 等。``**kwargs`` 透传给 Message
        构造（``source`` / ``priority`` / ``tags`` 等）。返回包含本消息
        的逻辑 Turn 的 :class:`TurnResult` （四种结局均 resolve，调用方不
        挂起）。

        .. rubric:: 使用示例

        .. code-block:: python

            result = await agent.query("帮我查订单 4521")
            assert result.status == "completed"

        .. rubric:: 行为要点

        - 等待语义：等“包含我这条消息”的逻辑回合完成；空闲时可能被
          出队合并（一次取出多条消息，共享同一产物），活跃回合中排队等
          当前回合完成。
        - 副线不走本方法——副线唯一入口是 :meth:`side_query`。
        - 取消等待 ≠ 取消回合：``await`` 被取消时消息已在队列（可能已
          执行），取消只是不领结果；撤回未出队消息用 :meth:`cancel_queued`。
        - 死锁禁止：在当前回合的调用栈内（任何钩子、工具 ``execute``、
          ``provider_gen`` 期间的 await 点）``await query()`` 必死锁——
          回合收尾要等钩子返回，钩子要等下一回合产物，下一回合要等当前
          回合收尾。跨 Agent 等待同理：等待图成环（A 等 B、B 等 A）即
          死锁，框架不做环检测。
        - 回合内需要驱动，用 :meth:`steer` （STEER 优先级，当轮 context
          可见）；``enqueue_message`` / :meth:`message` 入队的非 STEER
          消息回合内不可察觉，只在回合间消费。
        - 崩溃 / ``destroy()`` 都不挂起调用方（resolve cancelled）。
        - 前置条件：实例未被 ``destroy()``。

        .. seealso::

            - :meth:`message` —— 散装直发（fire-and-forget 糖）。
            - :meth:`enqueue_message` —— fire-and-forget 入口。
            - :meth:`cancel_queued` —— 撤回未出队消息。
            - :meth:`side_query` —— 副线路径。
        """
        blocks: list[ContentBlock]
        if isinstance(content, str):
            blocks = [TextBlock(text=content)]   # str 自动打包
        else:
            blocks = content
        msg = Message(kind=kind, content=blocks, **kwargs)
        if msg.id is None:
            msg.id = self._next_message_id()   # 铸造先于 _pending_turns 注册——等待句柄以终态 id 为键
        fut: asyncio.Future[TurnResult] = asyncio.get_running_loop().create_future()
        self._pending_turns[msg.id] = fut   # 注册先于入队——消息对外可见时句柄必已存在
        try:
            message_id = await self.enqueue_message(msg)   # 打包 + 入队
            return await fut   # 等待“包含我这条消息的回合”产物（四结局均 resolve）
        finally:
            self._pending_turns.pop(msg.id, None)   # Intercepted / 取消 / 正常三路均清理；取消等待 ≠ 取消回合

    async def message(
        self,
        content: str | list[ContentBlock],
        kind: MessageKind = MessageKind.USER,
        **kwargs: Any,
    ) -> str:
        """散装直接入队（fire-and-forget），返回 ``message_id``，不等待结果。

        .. rubric:: 功能介绍

        :meth:`enqueue_message` 的散装箱糖：免去调用方手工构造
        ``Message``——``content: str`` 自动打包为
        ``[TextBlock(text=content)]``；``list`` 直接作为 ContentBlock
        列表。``kind`` 默认 ``USER``，``**kwargs`` 透传给 Message 构造
        （``source`` / ``priority`` / ``tags`` 等）。打包后委托
        :meth:`enqueue_message`，返回其 ``message_id``。

        .. rubric:: 使用示例

        .. code-block:: python

            message_id = await agent.message("稍后提醒我喝水")
            # 需要回合产物（最终文本 / token 用量）时改用：
            result = await agent.query("帮我查订单 4521")

        .. rubric:: 行为要点

        - fire-and-forget：不注册 ``_pending_turns``、不等待任何回合；
          返回值仅是消息 id，可用于 :meth:`cancel_queued` 撤回。
        - 入队时序（``on_enqueue`` → enqueue）与
          :meth:`enqueue_message` 完全一致；``Intercepted`` 原样上抛。
        - 不返回 ``TurnResult``——想要回合产物请用 :meth:`query`。

        .. seealso::

            - :meth:`query` —— 等待语义的封装。
            - :meth:`steer` —— STEER 优先级的特例。
            - :meth:`enqueue_message` —— 被委托的原始入队入口。
        """
        blocks: list[ContentBlock]
        if isinstance(content, str):
            blocks = [TextBlock(text=content)]   # str 自动打包
        else:
            blocks = content
        msg = Message(kind=kind, content=blocks, **kwargs)
        return await self.enqueue_message(msg)   # 散装箱糖：不注册 _pending_turns、不等待

    async def steer(self, content: str | list[ContentBlock], **kwargs: Any) -> str:
        """注入 steer 消息（不等回应）：``message(..., priority=MessagePriority.STEER)``
        的特例。

        .. rubric:: 功能介绍

        回合进行中向 Agent 追加导向信息（补充约束、修正方向、追加资料）。
        固定 ``priority=MessagePriority.STEER``，fire-and-forget，返回
        ``message_id``。

        .. rubric:: 使用示例

        .. code-block:: python

            await agent.steer("预算上限改为 500，别直接下单")

        .. rubric:: 行为要点

        - 吸收语义：STEER 消息的两个消费路径——随出队批次进入回合
          （队首 INTERRUPT/STEER 连续段并入下一逻辑回合的消费批次，
          见 :meth:`_dequeue`），或回合内层循环每轮 ``provider_gen``
          前被 drain 挂树、当轮 context 即可见；两条路径都不打断当前
          回合（对比 ``INTERRUPT`` 的 abort 语义）。
        - fire-and-forget：不注册 ``_pending_turns``、不等待回合产物。
        - 不保证被哪个回合消费：若当前回合已收尾，由下一回合消费。

        .. seealso::

            - :meth:`message` —— 散装直发的一般形。
            - :meth:`query` —— 需要回合产物时用。
        """
        return await self.message(content, priority=MessagePriority.STEER, **kwargs)

    async def enqueue_message(self, msg: Message) -> str:
        """纯入队（fire-and-forget），返回 ``message_id``，不等待结果。

        .. rubric:: 功能介绍

        接收已构造的 ``Message`` 对象（打包责任上移到 ``query()`` /
        ``message()`` 或调用方）。远程用户 / 第三方 / Cron / 异步工具最终
        结果的统一投递入口。

        .. rubric:: 使用示例

        .. code-block:: python

            msg = Message(kind=MessageKind.EVENT, source="tool_result",
                          content=[TextBlock(text="异步任务完成")],
                          priority=MessagePriority.HIGH)
            message_id = await agent.enqueue_message(msg)

        .. rubric:: 行为要点

        - 时序：dispatch ``on_enqueue`` （可检查 / 修改 /
          ``raise Intercepted`` 拒绝——内容审核、速率限制、文件过大）→
          ``_message_queue.enqueue(msg)`` → 返回 ``msg.id``。
        - 消费保证：入队即会被消费（常驻工作循环），无需“入队触发”逻辑。
        - 可入队种类：USER / EVENT / SYSTEM / SUBAGENT / PEER，
          以及异步工具最终结果（以 ``EVENT`` kind 入队，content 为标注块 +
          结果块列表）；``PROVIDER`` 消息永远不进队列（回合内产生）。
        - 优先级插队只影响消费顺序，不影响 ``_pending_turns`` 关联
          （逐条按 id pop）。
        - :raises flowing.errors.Intercepted: ``on_enqueue`` handler
          拒绝入队时原样上抛。

        .. seealso::

            - :meth:`enqueue_messages` —— 批量入队。
            - :meth:`query` —— 等待语义的封装。
            - :meth:`message` —— 散装箱糖。
            - :class:`flowing.message.MessageQueue` —— 排序与阻塞语义。
        """
        if msg.id is None:
            msg.id = self._next_message_id()   # 入队即铸造——on_enqueue 起钩子即可见终态 id
        msg = await self.hooks.on_enqueue.dispatch(self, msg)   # 可检查/修改；Intercepted 原样上抛
        self._message_queue.enqueue(msg)
        return msg.id

    async def enqueue_messages(
        self, msgs: Message | list[Message], **kwargs: Any
    ) -> list[str]:
        """批量入队（接受单条或列表），返回 ``message_id`` 列表。

        .. rubric:: 行为要点

               - 逐条委托 :meth:`enqueue_message` （每条独立经过
          ``on_enqueue``）；任一条被 ``Intercepted`` 时异常上抛，
          已入队的不回滚。
        - 返回顺序与输入顺序一致。

        .. seealso:: :meth:`enqueue_message`
        """
        if isinstance(msgs, Message):
            msgs = [msgs]
        ids: list[str] = []
        for m in msgs:
            ids.append(await self.enqueue_message(m))   # 逐条委托；Intercepted 上抛，已入队不回滚
        return ids

    def _pop_pending_cancelled(self, message_id: str) -> None:
        """摘除 ``_pending_turns`` 条目并联动 resolve cancelled（框架合成空
        ``TurnContext``；内部 API）——:meth:`cancel_queued` 与
        :meth:`_dequeue` 的“已出队但未消费”处理共用同一填充规则。"""
        fut = self._pending_turns.pop(message_id, None)
        if fut is not None and not fut.done():
            fut.set_result(TurnResult(   # 联动 resolve cancelled（框架合成空 TurnContext）
                turn=TurnContext(started_at=datetime.now(), message_ids=[]),
                final_text="", status="cancelled", token_usage=None,
                finish_reason="cancelled"))

    def cancel_queued(self, message_id: str) -> bool:
        """撤回一条未出队的排队消息。

        .. rubric:: 功能介绍

        从消息队列移除指定消息；若该消息有 ``query()`` 等待者
        （``_pending_turns`` 条目），联动 resolve
        （``TurnResult(status="cancelled")``，turn 字段为框架合成的空
        ``TurnContext``）并移除条目——调用方不挂起。

        .. rubric:: 行为要点

        - 返回 ``True``：消息在队列中，已移除（等待者已联动 resolve）。
        - 返回 ``False``：消息已出队（正在或已被回合消费）或不存在——
          不做任何动作。
        - 同步方法：不 dispatch 钩子、不 await；future 的 ``set_result``
          同步完成。
        - 不影响其它排队消息；不触碰 ``current_turn``。

        .. seealso:: :meth:`query`、:meth:`abort_turn`、
            :meth:`set_queued_priority`
        """
        removed = self._message_queue.remove(message_id)   # 同步方法，不 dispatch 钩子
        if not removed:
            return False   # 已出队或不存在：不做任何动作
        self._pop_pending_cancelled(message_id)   # 等待者联动 resolve cancelled
        return True

    def _next_message_id(self) -> str:
        """铸造下一条消息 id（内部 API，不属稳定契约）。

        按 Agent 的自增序列号：计数器存 core 状态袋 ``message_seq`` 键
        （持久化，恢复重放自动续接——tombstone 与物理压缩不影响），
        每次铸造 +1 写透，返回纯数字字符串。显式给定的消息 id 不经
        本方法，铸造方负责先查 ``_messages`` 冲突。
        """
        seq = self._core_state.get("message_seq", 0) + 1
        self._core_state["message_seq"] = seq   # 写透（D7 末行合并）
        return str(seq)

    def _next_task_id(self) -> str:
        """铸造下一个后台任务注册键（内部 API）：纯内存自增，进程内单调不复用。"""
        self._task_seq += 1
        return str(self._task_seq)

    def _next_execution_id(self) -> str:
        """铸造下一个 Execution id（内部 API）：纯内存自增，进程内单调不复用。"""
        self._execution_seq += 1
        return str(self._execution_seq)

    def _drive_background(self, tool: Tool, source: Any, form: str,
                          tool_call: ToolCall | None = None) -> str:
        """后台工具投递驱动的统一接缝（内部 API，不属稳定契约）。

        ``Tool.__call__`` 识别后台形态（async generator / ``background``
        标记 / 返回 ``asyncio.Task``）后经本接缝把投递驱动移交调用方
        Agent：``form="asyncgen"`` → 驱动任务逐段消费；``form="task"``
        → 完成回调投递终值。投递全程在本 Agent 侧执行（归一 →
        ``on_tool_yields`` → 塑形 → EVENT 入队），返回后台任务注册键。
        """
        return _start_background_drive(self, tool, source, form, tool_call)

    def track_background_task(self, task: asyncio.Task) -> str:
        """注册一个后台任务并返回注册键（后台机制的唯一注册入口）。

        .. rubric:: 功能介绍

        后台机制（async generator 工具形态 / ``background`` 标记 / 返回
        Task 路径）的驱动任务统一经本方法登记：生成自增序列号注册键 →
        写入 ``_background_tasks`` → 挂 done_callback 闭包移除（完成 /
        异常 / 取消即弃）→ 返回注册键。注册键随 pending 收据交付
        （``ToolResult.background_task_id``），调用方 / LLM 可据此按 id
        取消或查询。

        .. rubric:: 行为要点

        - 同步方法；同一任务重复注册产生两个不同键（调用方责任）。
        - 注册表同时承担：按 id 精确取消、强引用防 GC（后台任务无其他
          持有者）、destroy 统一覆盖。
        - done_callback 用闭包捕获 str 键——直接传 ``dict.pop`` 会收到
          Task 参数而 KeyError。

        .. seealso:: :meth:`cancel_background_task`、
            :meth:`cancel_all_background_tasks`
        """
        task_id = self._next_task_id()
        self._background_tasks[task_id] = task
        task.add_done_callback(
            lambda t: self._background_tasks.pop(task_id, None))
        return task_id

    def cancel_background_task(self, task_id: str) -> bool:
        """按注册键取消单个后台任务。

        .. rubric:: 行为要点

        - 返回是否命中注册表（不在册 → ``False``，幂等）；与
          ``task.cancel()`` 的返回值无关——命中已完成任务时 ``cancel()``
          返回 ``False`` 但本方法仍返回 ``True``。
        - 同步方法：``task.cancel()`` 为协作式——执行体可在 await 点收
          ``CancelledError`` 后自行决定收尾。
        """
        task = self._background_tasks.get(task_id)
        if task is None:
            return False
        task.cancel()
        return True

    def cancel_all_background_tasks(self) -> None:
        """取消全部在册后台任务。

        .. rubric:: 行为要点

        - 遍历 ``task.cancel()`` （只 cancel 不 await——执行体可忽略取消，
          await 无界会让 ``destroy()`` 挂起）；幂等，空表 no-op。
        - 不清空注册表（移除由 done_callback 闭包完成；destroy 场景由
          destroy 显式 ``clear()``）。
        """
        for task in list(self._background_tasks.values()):
            task.cancel()

    def get_background_tasks(self) -> dict[str, asyncio.Task]:
        """后台任务注册表快照（``task_id → Task``）。

        .. rubric:: 行为要点

        - 快照：复制 dict，不返回内部引用——调用方改动不影响注册表。
        - 直接暴露 Task 对象，不做防调用方包装。
        """
        return dict(self._background_tasks)

    def get_background_task(self, task_id: str) -> asyncio.Task | None:
        """按注册键查询单个后台任务；不在册 → ``None``。"""
        
        return self._background_tasks.get(task_id)

    def set_queued_priority(self, message_id: str, priority: MessagePriority) -> bool:
        """重设一条未出队排队消息的优先级（队列立即重排）。

        .. rubric:: 功能介绍

        :meth:`flowing.message.MessageQueue.set_priority` 的 Agent 层入口：
        把排队中的消息提升 / 降低优先级，影响下一轮出队的消费顺序。与
        :meth:`cancel_queued` 对称——那个管“反悔撤回”，这个管“催办 /
        降级”。重排保留原入队序号：被改优先级的消息插入新优先级带时按
        原入队早晚定位，如同它入队时就带着新优先级。

        .. rubric:: 使用示例

        .. code-block:: python

            agent.set_queued_priority(msg_id, MessagePriority.HIGH)   # 催办

        .. rubric:: 行为要点

        - 返回 ``True``：消息在队列中，``priority`` 已改写并重排。
        - 返回 ``False``：消息已出队（正在或已被回合消费）或不存在——
          不做任何动作。
        - 同步方法：不 dispatch 钩子、不 await。
        - 不影响正在执行的回合与 ``current_turn``；不改写消息其它字段
          （``id`` / ``timestamp`` 等）。

        .. seealso:: :meth:`cancel_queued`、:meth:`enqueue_message`
        """
        return self._message_queue.set_priority(message_id, priority)   # 纯转发，无等待者牵连

    def estimate_context_tokens(self) -> ContextUsageEstimate:
        """估计当前上下文占用的 token 数（锚点实测 + 尾部估算，纯观测）。

        .. rubric:: 功能介绍

        回答“现在把上下文发给 LLM 大约多大”：沿 ``current_head_id`` 上溯
        的当前路径上，找最近一条有效锚点（``kind == PROVIDER`` 且
        ``usage`` 非 ``None`` 且 ``usage.total_tokens > 0`` 的消息），
        锚点覆盖部分用实测值（``measured``），之后的新内容用
        :func:`flowing.message.estimate_message_tokens` 逐条估算
        （``estimated``）；无有效锚点时全段估算（含 system prompt 与工具
        schema）。每次现场扫描，锚点随 fork / 树手术自动迁移——不维护
        持久锚点账本，无陈旧锚点问题；代价是 O(路径长) 扫描。

        .. rubric:: 使用示例

        .. code-block:: python

            est = agent.estimate_context_tokens()
            print(est.tokens, est.usage_ratio)   # 比率不 clamp，>1 即溢出信号

        .. rubric:: 行为要点

        - 同步、纯读取、无副作用：不 dispatch 钩子、不改任何状态。
        - 路径收集口径与上下文组装相同（沿 ``current_head_id`` 上溯）。
        - 锚点命中时：``measured = 锚点.usage.total_tokens`` （含
          cache_read——缓存读的 token 也占窗口）；``estimated`` 追加
          “当前启用工具中不在 ``_measured_tool_names`` 的 schema 估算”
          ——锚点后新增工具的补估规则。
        - 无锚点时：``measured = None``，``estimated`` = system prompt 各
          segment 文本估算 + 当前全部启用工具 schema 估算 + 路径全部消息
          估算。
        - ``context_window`` 取 ``self.model.resolve(self).context_window``
          （现场求值；模型未声明为 ``None``，此时 ``usage_ratio`` 为
          ``None``）。
        - 不设阈值、不触发压缩、不告警（策略归插件）；不缓存结果；估算值
          永不用于计费。
        - 边缘情况：路径为空 → 全零且 ``measured is None``；锚点消息的
          ``usage.total_tokens == 0`` （异常响应）不算有效锚点，继续上溯。

        .. seealso::

            :class:`ContextUsageEstimate`、
            :func:`flowing.message.estimate_message_tokens`、
            :class:`flowing.providers.Usage`
        """
        # 沿 current_head_id 上溯收集（chain.walk，与 _assemble_context 同口径；
        # 孤儿链断点容忍——上溯到断点即终止，同 MessageChain.remove 的语义）
        path = list(self.chain.walk(self.current_head_id))[::-1]
        anchor = None
        for m in reversed(path):
            if m.kind is MessageKind.PROVIDER and m.usage is not None and m.usage.total_tokens > 0:
                anchor = m   # 有效锚点：实测覆盖到本条为止
                break
        measured: int | None = None
        estimated = 0
        if anchor is not None:
            measured = anchor.usage.total_tokens   # 恒等式：含 cache_read（缓存也占窗口）
            for m in path[path.index(anchor) + 1:]:
                estimated += estimate_message_tokens(m)
            # 锚点后新增工具补估：当前启用工具中不在 _measured_tool_names 者
            for entry in self._tool_entries.values():
                if entry.visible and entry.name_alias not in self._measured_tool_names:
                    estimated += _estimate_tool_schema_tokens(
                        entry.llm_definition(self.runtime, self))
        else:
            # 无锚点全估：system prompt 各 segment 文本 + 全部启用工具
            # schema + 路径全部消息
            for block in self.prompt_blocks:
                estimated += _text_tokens(str(block.content.resolve(self)))
            for entry in self._tool_entries.values():
                if entry.visible:
                    estimated += _estimate_tool_schema_tokens(
                        entry.llm_definition(self.runtime, self))
            for m in path:
                estimated += estimate_message_tokens(m)
        model = self.__dict__.get("model")   # 模型尚未解析（创建管线中）-> 窗口为 None
        window = model.resolve(self).context_window if model is not None else None   # 现场求值；未声明为 None
        return ContextUsageEstimate(
            tokens=(measured or 0) + estimated, measured=measured,
            estimated=estimated,
            anchor_message_id=anchor.id if anchor is not None else None,
            context_window=window)

    # ────────────────────────── 模型调用与副线 ────────────────────────────

    def _resolve_model_tag(self, tag: str) -> ModelConfig:
        """模型标签 → ``ModelConfig`` 的现场解析（内部 API，不属稳定契约）。

        两跳现场求值、无缓存：``model_tag`` → 标签映射
        （:func:`flowing.model.load_model_tags`）→ 模型条目名 → 条目表
        （:func:`flowing.model.load_models`）→ ``ModelConfig``。来源路径
        读 Runtime 的登记字段；两者缺失时报错（fail fast，不静默回退）。
        标签未定义回退 ``default``；``default`` 也未定义 → 报错。本方法是
        ``self.model`` 初始解析与 ``side_query(model_tag=...)`` 的同一代码
        路径；调用方负责把产物落到 ``self.model`` 或仅作一次性使用。
        """
        tags_path = getattr(self.runtime, "_model_tags_path", None)
        models_path = getattr(self.runtime, "_models_path", None)
        if tags_path is None or models_path is None:
            raise FlowingError(
                f"model tag {tag!r} cannot be resolved: Runtime has no registered model-tags/models "
                "source paths (_model_tags_path / _models_path)")
        tags = load_model_tags(Path(tags_path))
        entry_name = tags.get(tag) or tags.get("default")
        if entry_name is None:
            raise FlowingError(
                f"model tag {tag!r} is undefined and model-tags has no default fallback")
        models = load_models(Path(models_path))
        config = models.get(entry_name)
        if config is None:
            raise FlowingError(
                f"model tag {tag!r} points to model entry {entry_name!r} which is not in the models table")
        return config

    async def _race_cancel(
        self, awaitable: Awaitable[Any], execution: Execution
    ) -> tuple[bool, Any]:
        """Provider 在途调用与取消信号的竞速：``(False, 结果)`` / ``(True, None)``。

        把 ``awaitable`` 包成 Task，与 ``_turn_abort`` / ``execution.cancel``
        两个信号的等待 Task 做 FIRST_COMPLETED 竞速：调用先完成 →
        ``(False, result)`` （调用异常经 ``task.result()`` 原样上抛，含
        ``StopAsyncIteration``——“不捕获任何异常”契约不受影响）；信号先
        置位 → ``cancel()`` 在途 Task（``CancelledError`` 注入 adapter 的
        HTTP await 点，adapter 不捕获、原样透传），suppress 收尾后返回
        ``(True, None)``，由调用方合成 cancelled 响应（cancel 是正常终止
        不是错误）。竞速本身被外部取消（如 ``destroy()`` 的
        ``task.cancel()``）时，在途 Task 一并 cancel，不泄漏。
        """
        call_task = asyncio.ensure_future(awaitable)
        signal_waits = [asyncio.ensure_future(self._turn_abort.wait()),
                        asyncio.ensure_future(execution.cancel.wait())]
        try:
            done, _pending = await asyncio.wait(
                {call_task, *signal_waits}, return_when=asyncio.FIRST_COMPLETED)
            if call_task in done:
                return False, call_task.result()   # 调用异常原样上抛（不捕获契约）
            call_task.cancel()   # 信号先赢：CancelledError 注入在途调用的 await 点
            with contextlib.suppress(asyncio.CancelledError):
                await call_task
            return True, None
        finally:
            for wait_task in signal_waits:
                wait_task.cancel()
            if not call_task.done():
                call_task.cancel()   # 竞速被外部取消：在途调用一并取消，不泄漏
                with contextlib.suppress(asyncio.CancelledError):
                    await call_task   # 等其收尾：async generator 脱离 running 态（调用方 aclose 的前提）

    async def provider_gen(
        self, context: Context, *, stream: bool = True, by: str | None = None
    ) -> ProviderResponse:
        """单次模型调用——双模式（流式 / 非流式）的唯一入口。

        .. rubric:: 功能介绍

        时序：``_turn_abort`` 检查（置位 → 直接返回
        ``ProviderResponse(message=None, finish=False, cancelled=True)``，
        不抛异常）→ ``self.model.resolve(self)`` 字段级惰性求值 → provider
        懒获取（``provider_registry.get(model.provider)``）→ dispatch
        ``before_provider_gen`` （可改写完整 ``Context``）→ Provider 调用
        （注册 ``Execution(kind="request")``；在途期间与取消信号竞速，
        可被 ``cancel()`` / ``abort_turn()`` 当场中断）→ dispatch
        ``after_provider_gen`` （可改写 ``ProviderResponse``）→ 返回。

        .. rubric:: 使用示例

        .. code-block:: python

            # 默认流式；显式关闭
            response = await self.provider_gen(context, stream=False)

        .. rubric:: 行为要点

        - ``stream=True`` （默认）：流式路径——逐 delta 累积进
          ``Message.content`` 并经 ``on_provider_delta`` dispatch
          （纯观察：累积只认 Provider 原始 delta，dispatch 的返回值被
          丢弃、不回写——改写单条 delta 无意义，需改写走
          ``after_provider_gen`` 改整条消息；delta 本身不落盘）；
          ``stream=False``：非流式一次性请求，拿到完整响应后合成一条
          全量 delta 同样 dispatch——两种路径的 delta 数据格式完全一致，
          订阅者永远可以依赖“每次正常完成的 ``provider_gen`` 至少一条
          delta”（在途被取消的调用除外：无任何 delta、``message=None``）。
        - ``by``：来源标记，透写到每条 ``ProviderDelta`` 与返回的
          ``ProviderResponse`` （adapter 不填，由本方法盖写）。主 Turn
          内层循环传 ``"_turn"``，``side_query`` 传 ``"_side"``；下划线
          开头为框架保留值，插件自定义来源勿用。``after_provider_gen``
          与 ``on_provider_delta`` 钩子点以 ``match_on="by"`` 声明——
          ``match_on`` 指定按 value 的哪个字段过滤注册（此处按来源标记
          ``by``），handler 可按来源模式过滤注册。
        - 流式中断（abort）：已累积内容保留为 ``partial=True`` 的消息
          随响应返回（保留落盘），``cancelled=True``；取消点起不再
          dispatch delta；底层流立即 ``aclose()``（HTTP 连接不滞留）。
          取消检测点 = 每个 delta 之间 + 等待下一 delta 期间（竞速），
          停滞的流同样可中断。
        - 不捕获任何异常：Provider 异常分类（``RateLimitedError`` /
          ``ContextLengthError`` 等）原样上抛给回合层（统一经
          ``on_provider_error`` 分发）。
        - 不重试、不缓存、不聚合用量——回合级聚合是回合执行体的职责；
          本方法只保证响应消息上附着的 ``message.usage`` 原样抵达
          ``after_provider_gen`` 钩子与调用方。
        - 边缘情况：取消信号于 Provider 调用在途期间置位——
          :meth:`_race_cancel` 竞速 cancel 在途调用（``CancelledError``
          注入 adapter 的 HTTP await 点，adapter 不捕获、原样透传），
          非流式返回 ``ProviderResponse(message=None, finish=False,
          cancelled=True)`` 而非抛异常（cancel 是正常终止不是错误）；
          流式按上条的中断规则收尾。

        .. seealso::

            - :meth:`side_query` —— 副线封装（强制非流式）。
            - :meth:`flowing.providers.Provider.generate` /
              :meth:`flowing.providers.Provider.generate_stream` —— 底层契约。
            - :class:`ProviderErrorContext` —— 异常上抛后的决策上下文。
        """
        if self._turn_abort.is_set():
            return ProviderResponse(message=None, finish=False, cancelled=True)   # abort：不抛异常、不发起 Provider 调用
        model: ModelConfig = self.model.resolve(self)   # 每次调用前字段级惰性求值
        provider: Provider = self.runtime.provider_registry.get(model.provider)   # 懒获取（未知条目 KeyError）
        context = await self.hooks.before_provider_gen.dispatch(self, context)   # 可改写完整 Context
        execution = Execution(id=self._next_execution_id(), kind="request", tags=[],
                              started_at=datetime.now(),
                              cancel=asyncio.Event(), pause=asyncio.Event())
        self._executions[execution.id] = execution   # 注册，finally 清理
        try:
            if not stream:
                # 非流式：一次性请求（经 _race_cancel 与取消信号竞速，在途
                # 可取消）；拿到完整响应后合成一条全量 delta 同样
                # dispatch（两种路径 delta 数据格式一致，订阅者永远能依赖
                # “每次 provider_gen 至少一条 delta”——在途被取消的调用
                # 除外：无任何 delta、message=None）
                cancelled, result = await self._race_cancel(
                    provider.generate(context, model), execution)
                if cancelled:
                    # cancel 是正常终止不是错误（与预检 abort 同一响应形态）
                    response = ProviderResponse(message=None, finish=False,
                                                cancelled=True)
                else:
                    response = result
                    if response.message is not None:
                        if response.message.id is None:
                            response.message.id = self._next_message_id()   # 铸造先于 delta dispatch（观察者按 id 归组）
                        full_text = "".join(
                            b.text for b in response.message.content
                            if isinstance(b, TextBlock))
                        await self.hooks.on_provider_delta.dispatch(
                            self, ProviderDelta(kind="text", text=full_text,
                                                content_index=0, by=by,
                                                message_id=response.message.id))
            else:
                # 流式：list[ContentBlock] 累积器按
                # content_index 归位——text/thinking delta 逐段拼接进对应块；
                # 结构化内容（工具调用等）由 adapter 在末段以完整块
                # （delta.block）交付，直接归位；usage 由末帧附着进组装消息；
                # 末帧 provider_data（含原始 stop_reason）并入组装
                # 响应，使 completed 结局的 finish_reason 口径在流式主路径生效。
                accumulated: dict[int, ContentBlock] = {}
                final_usage: Usage | None = None
                final_provider_data: dict[str, Any] = {}
                interrupted = False
                resp_id = self._next_message_id()   # agent 预铸本 assistant 消息 id：先于流式，逐 delta 携带
                agen = provider.generate_stream(context, model)
                try:
                    while True:
                        try:
                            cancelled, delta = await self._race_cancel(
                                agen.__anext__(), execution)   # 等待下一 delta 期间同样竞速取消（停滞流可中断）
                        except StopAsyncIteration:
                            break   # 流自然耗尽
                        if cancelled:
                            interrupted = True
                            break
                        # 取消点起不再 dispatch delta（abort/cancel 均为协作式信号）
                        if self._turn_abort.is_set() or execution.cancel.is_set():
                            interrupted = True
                            break
                        delta.by = by   # 来源标记盖写（adapter 不填、无法伪造）
                        delta.message_id = resp_id   # agent 盖写（adapter 不填），观察者实时归组依据
                        if delta.block is not None:
                            accumulated[delta.content_index] = delta.block
                        elif delta.kind == "thinking":
                            existing = accumulated.get(delta.content_index)
                            if isinstance(existing, ThinkingBlock):
                                thinking = existing.thinking + delta.text
                                # 首见签名保留（Anthropic 多轮回放必需）；后续片段不覆盖
                                sig = existing.signature or delta.signature
                            else:
                                thinking = delta.text
                                sig = delta.signature
                            accumulated[delta.content_index] = ThinkingBlock(
                                thinking=thinking, signature=sig)
                        elif delta.text or delta.content_index in accumulated:
                            existing = accumulated.get(delta.content_index)
                            text = ((existing.text if isinstance(existing, TextBlock) else "")
                                    + delta.text)
                            accumulated[delta.content_index] = TextBlock(text=text)
                        if delta.usage is not None:
                            final_usage = delta.usage   # 仅末帧携带
                        if delta.provider_data is not None:
                            final_provider_data = delta.provider_data   # 仅末帧携带
                        await self.hooks.on_provider_delta.dispatch(self, delta)   # 纯观察，返回值丢弃不回写
                finally:
                    aclose = getattr(agen, "aclose", None)
                    if aclose is not None:
                        await aclose()   # 中断时立即关闭底层流（HTTP 连接不滞留）；自然耗尽时为 no-op
                message = Message(
                    kind=MessageKind.PROVIDER,
                    content=[accumulated[i] for i in sorted(accumulated)],
                    id=resp_id,   # 复用预铸 id：挂树后消息 id == delta 实时携带的 id
                    partial=interrupted,   # 流式中断：已累积内容定型为 partial=True 消息（保留落盘）
                    usage=final_usage,
                )
                response = ProviderResponse(
                    message=message,
                    # 默认准则：有 tool_call block -> False；中断的流式没有 finish
                    finish=(not interrupted) and not any(
                        isinstance(b, ToolCallBlock)
                        for b in message.content),
                    cancelled=interrupted,
                    model=model.model if isinstance(model.model, str) else "",
                    provider_data=final_provider_data,   # 末帧 stop_reason 通道
                )
        finally:
            self._executions.pop(execution.id, None)   # finally 清理（不变量：不捕获异常）
        response.by = by   # 响应来源标记盖写（adapter 不填）
        response = await self.hooks.after_provider_gen.dispatch(self, response)   # 可改写 ProviderResponse
        if response.message is not None and response.message.usage is not None:
            # 估算锚点记账：本次实测覆盖了当时 context.tools 的 schema，
            # 名字并入 _measured_tool_names（estimate_context_tokens 的
            # “锚点后新增工具补估”规则以此差集为准；纯内存不持久化；
            # usage 的唯一载体是消息——ProviderResponse 不携带）
            self._measured_tool_names |= {d.name for d in context.tools}
        return response

    async def side_query(
        self,
        content: str | list[ContentBlock],
        kind: MessageKind = MessageKind.USER,
        *,
        model_tag: str | None = None,
        **kwargs: Any,
    ) -> str:
        """副线调用：不 commit、不落盘、固定非流式，返回最终文本。

        .. rubric:: 功能介绍

        旁路快速查询（guardrail、内容检查、摘要、情绪推断）：把散装
        ``content`` / ``kind`` / ``**kwargs`` 打包为一条 ``Message``
        （与 :meth:`query` 相同的打包规则：``str`` →
        ``[TextBlock(text=content)]``），追加到本次临时 context，直接
        ``provider_gen(context, stream=False, by="_side")`` 一次，返回响应
        文本；不产生任何持久化痕迹。``model_tag`` 调用时传参（请求前当场
        解析，不改写 ``self.model``）；``None`` 时复用 ``self.model``。

        .. rubric:: 使用示例

        .. code-block:: python

            emotion = (await self.side_query(
                "推断当前情绪，只输出一个词：\n" + window,
                model_tag="fast")).strip()

        .. rubric:: 行为要点

        - 注册 ``Execution(kind="side_query")`` （可被 ``cancel_by_tag`` /
          级联取消命中）；abort 时返回空文本（不抛异常）。
        - 上下文组装基于当前消息级树（与主路径同一机制，不剥离工具
          schema、不做任何副线特化——保证 prompt 缓存命中率）；但本次
          交换的请求 / 响应消息均不挂树。
        - 响应消费规则（单次交换，无续轮）：返回值 = 仅拼接响应中的
          ``TextBlock.text``；``ThinkingBlock`` 与 ``ToolCallBlock`` 一律
          丢弃——副线消息不落盘、不再进任何上下文，thinking 无 passback
          义务、工具请求无执行机制。``finish`` 字段不读（它是 Turn 循环
          的结束判断，副线不是 Turn）。
        - 空文本二义性：abort 与“响应无 TextBlock”（如 thinking 烧光
          预算、模型只想调工具）都返回 ``""``，不区分——调用方对空值做
          幂等兜底。
        - 截断：不设独立参数，输出上限由本次调用解析出的
          ``ModelConfig.max_output_tokens`` 决定；``stop_reason=length``
          的被截断文本是合法返回值，不报错。
        - 不 dispatch turn 族钩子（``before_turn`` 等——副线不是逻辑
          Turn）；不更新 ``current_head_id``；不触碰 ``_pending_turns``；
          不写 ``last_result``；固定非流式（``stream=True`` 无入口）；
          不记入 turn 用量累加器（``TurnResult.token_usage`` 不含副线
          消耗）。副线用量的观察点是 ``after_provider_gen`` 钩子——副线
          走 ``provider_gen()``，钩子照常触发。
        - 异常：Provider 异常直接上抛调用方（副线无 ``on_provider_error``
          回合层兜底——调用方自行决定重试 / 降级）。

        .. seealso::

            - :meth:`query` —— 主线入口（副线请直接调本方法）。
            - :meth:`provider_gen` —— 底层调用。
        """
        execution = Execution(id=self._next_execution_id(), kind="side_query", tags=[],
                              started_at=datetime.now(),
                              cancel=asyncio.Event(), pause=asyncio.Event())
        self._executions[execution.id] = execution   # 可被 cancel_by_tag / 级联取消命中
        original_model: ModelConfig | None = None
        if model_tag is not None:
            # 请求前当场解析临时 ModelConfig（_resolve_model_tag），
            # 不改写 self.model——经 object.__setattr__ 临时换绑再还原，
            # 不触发 watcher、不影响并发回合的观测面
            original_model = self.model
            object.__setattr__(self, "model", self._resolve_model_tag(model_tag))
        try:
            context: Context = self._assemble_context()   # 与主路径同一机制；本次请求/响应不挂树
            # 散装参数打包（与 query() 同一规则）；副线消息不挂树，但必须作为
            # 本次提问进入模型：追加到本次临时 Context.messages 末尾
            # （不写回 _messages、不落盘）
            blocks: list[ContentBlock]
            if isinstance(content, str):
                blocks = [TextBlock(text=content)]   # str 自动打包
            else:
                blocks = content
            msg = Message(kind=kind, content=blocks, **kwargs)
            context.messages = [*context.messages, msg]
            response = await self.provider_gen(context, stream=False, by="_side")   # 固定非流式；副线来源标记
            if execution.cancel.is_set():
                return ""   # abort 时返回空文本，不抛异常
            text = ""
            if response.message is not None:
                for block in response.message.content:
                    if isinstance(block, TextBlock):
                        text += block.text   # 仅拼 TextBlock；ThinkingBlock / ToolCallBlock 丢弃
            return text   # finish 字段不读（副线不是 Turn）
        finally:
            if original_model is not None:
                object.__setattr__(self, "model", original_model)   # 还原临时换绑
            self._executions.pop(execution.id, None)

    # ────────────────────────── 消息树手术便捷方法 ─────────────────────────
    # head 的维护收口在 Agent 层：MessageChain 五 op 不移动 current_head_id；
    # 以下方法把“删除当前 head 时 head 回退到亲节点”等策略固定在 Agent 上。

    def remove(self, message_id: str) -> None:
        """删除一条消息；若删的是 ``current_head_id``，head 回退到其
        ``parent_id``。

        .. rubric:: 功能介绍

        对 :meth:`MessageChain.remove` 的 Agent 层包装：``MessageChain``
        只做链手术（删除时把直接子自动重挂到其亲节点，链保持连续）；
        本方法补上 head 维护——当被删除的消息正好是 ``current_head_id``
        时，先把 head 回退到该消息的 ``parent_id``，再执行删除。
        ``parent_id`` 为 ``None`` 时 head 变为 ``None``——下一条消息将
        作为新根挂树。

        .. rubric:: 行为要点

        - 同步方法，不 dispatch 钩子。
        - ``message_id`` 不存在时由 :meth:`MessageChain.remove` 抛
          ``KeyError``。
        - 删除非 head 消息时，行为与 :meth:`MessageChain.remove` 完全一致
          （head 不动；被删消息的直接子自动重挂到其亲节点）。

        .. seealso:: :meth:`pop`、:meth:`MessageChain.remove`
        """
        if message_id not in self._messages:
            self.chain.remove(message_id)   # KeyError 由 chain.remove 抛出
            return
        if message_id == self.current_head_id:
            self._core_state["current_head_id"] = self._messages[message_id].parent_id
        self.chain.remove(message_id)

    def pop(self) -> str | None:
        """删除 ``current_head_id`` 指向的消息，head 回退到其亲节点。

        .. rubric:: 功能介绍

        :meth:`remove` 的 head 专用便捷包装：删除当前 head 并返回被删除
        的消息 id。空树（``current_head_id is None``）时返回 ``None``。

        .. rubric:: 使用示例

        .. code-block:: python

            while agent.current_head_id is not None:
                agent.pop()          # 从最新消息一路删到根；head 最终为 None

        .. rubric:: 行为要点

        - 同步方法，不 dispatch 钩子。
        - 连续 ``pop()`` 会把 head 链从新到旧逐条删除；全部删完后
          ``current_head_id is None``，下一条消息成为新根。

        :return: 被删除的消息 id；空树返回 ``None``。

        .. seealso:: :meth:`remove`、:meth:`push`
        """
        if self.current_head_id is None:
            return None
        removed = self.current_head_id
        self.remove(removed)
        return removed

    def push(self, msg: Message) -> str:
        """把一条消息挂到 ``current_head_id`` 之下并落盘，head 前移。

        .. rubric:: 功能介绍

        回合外的消息树追加入口：``msg.parent_id = current_head_id`` →
        挂入 ``_messages`` → 落盘 → ``current_head_id = msg.id`` （写透
        core 袋）。它也是回合内挂树的核心写路径；本方法不 dispatch turn
        族钩子、不写 ``turn.message_ids``——这两部分由回合内的挂树入口
        在调用本方法前后补齐。

        .. rubric:: 行为要点

        - 同步方法，不 dispatch 钩子。
        - ``msg.id`` 已存在于 ``_messages`` 时抛 ``ValueError``。
        - ``current_head_id`` 为 ``None`` 时，``msg`` 成为新根
          （``parent_id=None``）。

        :return: ``msg.id``。

        .. seealso:: :meth:`pop`、:meth:`branch`
        """
        if msg.id is None:
            msg.id = self._next_message_id()   # 挂树前铸造（回合内 PROVIDER/TOOL 消息的主要铸造点）
        if msg.id in self._messages:
            raise ValueError(msg.id)
        msg.parent_id = self.current_head_id
        self._messages[msg.id] = msg
        self._persist_message(msg)
        self._core_state["current_head_id"] = msg.id   # 热路径：head 落盘（D7，末行合并压物理写）
        return msg.id

    def branch(self, msg: Message, parent_id: str | None = None) -> str:
        """把一条消息挂到指定亲节点下（``None`` = 新根），不移动 head。

        .. rubric:: 功能介绍

        :meth:`MessageChain.branch` 的 Agent 层透传。调用方传入新消息
        与基点；只创建节点并落盘，不改变 ``current_head_id``。

        .. rubric:: 行为要点

        - 同步方法，不 dispatch 钩子。
        - ``parent_id`` 为 ``None`` 时开新根；非 ``None`` 时须存在。
        - 不移动 ``current_head_id`` （需要切过去用 :meth:`fork`）。

        :return: ``msg.id``。

        .. seealso:: :meth:`MessageChain.branch`、:meth:`fork`
        """
        return self.chain.branch(parent_id, msg)

    def remove_by_tags(self, tags: set[str]) -> int:
        """按 tags 批量删除消息，并保持 head 语义（链连续、无孤儿）。

        .. rubric:: 功能介绍

        透传 :meth:`MessageChain.remove_by_tags`（按消息链线性顺序**从后
        往前**逐条 :meth:`MessageChain.remove`——每条删除都会把被删消息
        的直接子自动重挂到其亲节点，链保持连续）。head 维护：删除前记录
        ``current_head_id`` 的完整祖先链；删除后若 head 已被删，回退到
        祖先链中**第一个仍存活**的祖先（整链被删光 → ``None``），而不是
        只回退单层亲节点——批量删除可能连删 head 与其亲节点，单层回退会
        指向已删节点。

        .. rubric:: 行为要点

        - 同步方法，不 dispatch 钩子。
        - 匹配 / 删除 / 返回计数语义由
          :meth:`MessageChain.remove_by_tags` 承担。

        :return: 删除条数（透传）。

        .. seealso:: :meth:`remove`、:meth:`MessageChain.remove_by_tags`
        """
        ancestors: list[str] = []
        head_id = self.current_head_id
        if head_id is not None:
            cursor = head_id
            while cursor is not None:
                ancestors.append(cursor)
                m = self._messages.get(cursor)
                if m is None:
                    break
                cursor = m.parent_id
        removed = self.chain.remove_by_tags(tags)
        # head 已被删 → 回退到祖先链首个仍存活者（含 head 本身仍存活的情形）
        if head_id is not None and head_id not in self._messages:
            new_head = next(
                (aid for aid in ancestors if aid in self._messages), None)
            self._core_state["current_head_id"] = new_head
        return removed

    # ────────────────────────── fork 与暂停 ───────────────────────────────

    async def fork(self, target_message_id: str | None) -> None:
        """消息级 fork：把 ``current_head_id`` 切到任意历史消息，或悬置游标。

        .. rubric:: 功能介绍

        消息上下文最基本的组织方式（核心机制，非扩展特性）。时序：
        dispatch ``on_fork`` （value 为 :class:`ForkContext`——换前 /
        换后两个 id；可改写 ``target_message_id`` ／ ``raise
        Intercepted`` 阻止）→ 目标合法性检查 → 切换
        ``current_head_id`` （落盘）。

        ``target_message_id=None``：游标悬置——head 置为 ``None``，上下
        文组装为空路径（``chain.walk(None)`` 空迭代），此后第一条挂树消
        息以 ``parent_id=None`` 成为新根开新链（:meth:`push` 的既有行
        为）；既有树完整保留，不做任何删除。这是清空活跃上下文视角的
        通道。

        fork 是纯上下文操作，不碰执行：正在运行的工具 / 子 Agent 继续
        运行、结果照常交付；执行追踪、生命周期子树、prompt 块、provide
        存储、工具条目、消息队列均不被触碰（共享同一实例，不拷贝、不
        冻结）。全时合法、无守卫：head 即“添加节点的位置”（每条消息挂树
        即前移），回合内 fork 的语义即 seek——本回合后续 append 与
        ``provider_gen`` 改在新基址上继续。

        .. rubric:: 使用示例

        .. code-block:: python

            # 中途改方向的推荐定式：先终止执行，等回合收尾完成，再切上下文
            await agent.cancel()   # 协作式置位——回合自行走向收尾，此调用返回 ≠ 收尾完成
            # …等待回合收尾完成（如 await 该回合 query() 的 TurnResult）…
            await agent.fork(msg_id)

            # 清空活跃上下文视角：旧树完整保留，下一条消息开新根
            await agent.fork(None)

        .. rubric:: 行为要点

        - 回合内 fork（seek 语义）合法、无 ``RuntimeError`` 守卫，但调用方
          须自知三件事：① 嫁接——fork 后的产物挂在目标所在链上，
          ``turn.message_ids`` 可能跨链（消费者均按 id 取消息，无机械
          故障）；② 上下文瞬移——下一轮上下文组装从新 head 上溯；③
          配对断裂——fork 把“共享前缀里带 tool_call 的 PROVIDER 消息”
          留在新分支、其结果消息留在旧分支时，新分支上该调用未配对，
          组装即抛 ``UnpairedToolCallError``（响亮失败）；恢复管线**不会
          自愈**——①b 的合成封闭按全局配对判定（结果在树里即不合成），
          坏分支永久坏。**安全落点**：fork 到 user 消息上、或 provider
          消息上方（不切开 tool_call 与结果之间的链段）——越过未闭合
          的调用-结果段即产生永久坏分支，这是可预想的操作语义而非损坏，
          框架不做家长式禁止。
        - 外部改方向的推荐定式仍是先终止执行再切上下文（时序可控）：
          ``cancel()`` → 等回合收尾完成 → ``fork()`` → 发新消息。回合内
          直接 fork 适合时序可知的钩子内调用者（如 compact）。
        - 上下文压缩 = fork 的应用：压缩把摘要复制为 SYSTEM 新根后经本
          方法切换 head，旧分支保留完整历史；何时压缩、如何摘要是
          Composable / 应用层策略（内置 ``flowing.composables.compact``），
          fork 只提供机制。
        - :raises ValueError: ``target_message_id`` 非 ``None`` 且不在
          ``_messages`` 中（含已被 ``chain.remove`` 移除的 id）。
        - :raises flowing.errors.Intercepted: ``on_fork`` handler
          阻止 fork。
        - 不换 Agent 类型（换类型 = 换身份 = 子 Agent）；不创建第二个
          实例、不在两个分支上同时运行（需要并行用子 Agent）；不复制 /
          截断 tree.jsonl——只是切换游标。
        - 边缘情况：兄弟分支无顺序信息（互斥分支），UI 排序用
          ``Message.timestamp``。

        .. seealso::

            - :attr:`current_head_id` —— 被切换的游标。
            - :class:`flowing.message.MessageChain` —— 修改历史结构的
              手术入口（与 fork 改视角正交）。
        """
        context = await self.hooks.on_fork.dispatch(
            self, ForkContext(previous_head_id=self.current_head_id,
                              target_message_id=target_message_id))   # 可改写 target_message_id / Intercepted 阻止
        if (context.target_message_id is not None
                and context.target_message_id not in self._messages):
            raise ValueError(context.target_message_id)   # 非 None 目标须在树（含已被 chain.remove 移除的 id）
        self._core_state["current_head_id"] = context.target_message_id   # 纯上下文操作：只切换游标（落盘）；None = 悬置

    def pause(self) -> None:
        """协作式暂停：关闭工作循环 gate。

        .. rubric:: 功能介绍

        三个检查点（dequeue 前 / provider_gen 前 / 每个 tool_call 前）均
        等待工作循环 gate——回合之间与回合内关键操作前都可暂停；恢复后
        从暂停点继续（pause 是挂起等待，可恢复；abort 是终止退出，不可
        恢复）。暂停期间队列不丢消息——可继续 ``enqueue_message`` 调整
        方向，恢复后按序消费。

        .. rubric:: 行为要点

        - 同步方法，幂等（重复调用无额外效果）；不 dispatch 钩子。
        - 优先级：``pause()`` 与 abort 同时发生时先挂起——``resume()``
          后才判定终止（每处检查点 pause 在前、abort 在后）。
        - 不置位任何 ``Execution.pause`` （Execution 层暂停是另一个正交
          通道，由 Tool 覆写 / Composable 自管，不级联）。
        - 作用范围：仅控制本 Agent 的工作循环 turn 检查点——不递归子
          Agent（子树暂停用 :meth:`pause_recursive`）。

        .. seealso:: :meth:`resume`、:attr:`paused`、:meth:`abort_turn`
        """
        self._pause_gate.clear()

    def resume(self) -> None:
        """恢复：打开工作循环 gate。

        .. rubric:: 行为要点

        同步、幂等；打开后所有挂在检查点上的等待继续，随后立即判定
        abort 信号（pause 在前、abort 在后的检查点顺序）。仅恢复本
        Agent（不递归子 Agent——子树恢复用 :meth:`resume_recursive`）；
        不触碰任何 ``Execution``。

        .. seealso:: :meth:`pause`、:meth:`resume_recursive`
        """
        self._pause_gate.set()

    def pause_recursive(self) -> None:
        """协作式暂停整棵生命周期子树（本 Agent + 全部后代子 Agent）。

        .. rubric:: 功能介绍

        ``self.pause()`` 后对 ``_children`` 中每个活子 Agent 递归调用
        ``pause_recursive()`` （深度优先）。典型场景：UI 的“暂停整个工作
        流”按钮。

        .. rubric:: 行为要点

        - 同步、幂等；不 dispatch 钩子。
        - 只递归生命周期子树的 turn 循环（``_children`` 只装活实例）；
          不触碰任何 ``Execution``。
        - 与 ``cancel()`` 的级联不同：cancel 经执行注册表逐层传播终止
          信号；本方法只挂起各 Agent 的工作循环 gate。

        .. seealso:: :meth:`pause`、:meth:`resume_recursive`
        """
        self.pause()
        for child in list(self._children.values()):   # 深度优先递归（_children 只装活实例）
            child.pause_recursive()

    def resume_recursive(self) -> None:
        """恢复整棵生命周期子树（与 :meth:`pause_recursive` 对称）。

        .. rubric:: 行为要点

        同步、幂等；``self.resume()`` 后对 ``_children`` 每个活子 Agent
        递归 ``resume_recursive()``；不触碰任何 ``Execution``。

        .. seealso:: :meth:`resume`、:meth:`pause_recursive`
        """
        self.resume()
        for child in list(self._children.values()):
            child.resume_recursive()

    @property
    def paused(self) -> bool:
        """是否暂停中——工作循环 gate 当前为关闭状态的只读派生视图。

        快照层据此展示暂停状态。

        .. seealso:: :meth:`pause`、:meth:`resume`
        """
        return not self._pause_gate.is_set()

    # ────────────────────────── 取消与停止 ────────────────────────────────

    def abort_turn(self) -> None:
        """只置位当前逻辑 Turn 的退出信号。

        .. rubric:: 功能介绍

        最小粒度的终止：当前 Turn 在下一个检查点退出，Agent 继续消费
        队列。收尾由回合执行体的 ``finally`` 接管（幂等置位
        ``turn.aborted`` → abort 钩子 → resolve waiters）。

        .. rubric:: 行为要点

        - 同步、幂等；无活跃 Turn 时置位也安全（下一条消息到达时 Turn
          启动、``provider_gen()`` 立即返回 cancelled，空 Turn 不产生新
          树节点）。
        - 可覆写：子类可改为丢弃 Turn 消息或额外清理。
        - 与 ``cancel()`` 的分工：``abort_turn()`` 只终止当前 Turn；
          ``cancel()`` 终止整个 Agent（幂等拆解；Agent 无生命周期状态机，
          无 ``stopping/stopped`` 中间态可读）。

        .. seealso:: :meth:`cancel`、:meth:`pause`
        """
        self._turn_abort.set()

    def _close_orphan_tool_calls(self, turn: "TurnContext") -> None:
        """回合收尾的配对封闭（树内永远成对的执行期保障）。

        每个回合收尾恒做（无孤儿则空转）——abort / error / 正常结局统一
        走此：本回合 PROVIDER 消息里未配对的每个 ``ToolCallBlock``，按块序经
        :meth:`flowing.message.MessageChain.insert` 逐条补一条
        ``tool_status="cancelled"``、空内容的 TOOL 消息——“因取消未执行”
        是事实记录（非合成占位）：与调用同分支、插在调用的直接后继位置，
        消息行与邻接调整记录一并落盘。已配对的调用（含在途工具返回的
        部分结果）不重复封闭。封闭行在 write-behind 窗口内丢失时，由恢复
        管线的合成封闭（``synthetic=True`` 占位）兜底。

        边界：封闭位置与 ``current_head_id`` 无关（按消息 id 定位，天然
        落在调用所在分支）。head 上移只发生在“head 恰在某条 PROVIDER
        消息上”时——批跳 / 取消响应意味着该消息无任何结果、封闭是新
        链尾；head 在结果消息上时 insert 已把结果重挂到封闭之后（链尾
        不变），head 已 fork 离开时不动。
        """
        answered: set[str] = set()
        for mid in turn.message_ids:
            msg = self._messages.get(mid)
            if msg is not None and msg.kind is MessageKind.TOOL and msg.tool_call_id:
                answered.add(msg.tool_call_id)
        for mid in turn.message_ids:
            msg = self._messages.get(mid)
            if msg is None or msg.kind is not MessageKind.PROVIDER:
                continue
            after_id = msg.id
            for block in msg.content:
                if isinstance(block, ToolCallBlock) and block.id not in answered:
                    closure = Message(kind=MessageKind.TOOL,
                                      tool_call_id=block.id,
                                      tool_status="cancelled", content=[])
                    after_id = self.chain.insert(after_id, closure)
                    turn.message_ids.append(after_id)
            if after_id != msg.id and self.current_head_id == msg.id:
                # 该 provider 有封闭产出且 head 恰停在它上面（批跳 / 取消响应：
                # 无任何结果消息）——封闭是新链尾，head 随之上移（写袋——head
                # 以袋为准）；head 在结果消息上（insert 已重挂、链尾不变）或已
                # fork 离开时不动
                self._core_state["current_head_id"] = after_id

    async def cancel(self) -> None:
        """协作式终止整个 Agent：全部执行条目 abort + 当前回合退出信号。

        .. rubric:: 功能介绍

        时序：dispatch ``before_cancel`` （value 为 :class:`CancelContext`，
        handler ``raise Intercepted`` 阻止取消）→ 置位 ``_executions
        全部 abort + 置位回合退出信号 → dispatch ``after_cancel`` （信号
        置位后立即触发——“取消请求已被接受”的事实事件；纯观察，日志 /
        通知 / 审计）。无状态值迁移（Agent 无生命周期状态机）。

        观察点分工：``after_cancel`` 表达的是“取消已被接受、信号已置
        位”，dispatch 点在本方法体内——协作式取消禁止本方法等待回合退出
        （回合内的代码调 ``cancel()`` 时等待即自死锁）。“回合真正退出”
        的观察归 ``after_turn`` （全路径，handler 读 ``turn.aborted`` 区分
        取消与正常结束）。空闲 Agent（无回合在跑）被 cancel 时
        ``after_cancel`` 照常触发：信号置位是事实，与有无回合无关。

        .. rubric:: 行为要点

        - 协作式：置位是请求不是命令——执行体三选一（立即停止返回已有
          / 空结果 / 忽略信号正常完成 / 关键收尾后返回部分结果）；Agent
          层的前台 awaitable 工具例外：在途执行由竞速中断（见 Execution
          的协作式条款例外）；Agent
          接收所有返回，不因曾被 cancel 丢弃返回值。
        - cancel 是正常终止不是错误：工具返回部分输出作正常
          ``ToolResult``，Provider 返回空 ``ProviderResponse``——不抛异常。
        - 级联取消：亲节点只操作自己的执行注册表与回合退出信号，子 Agent 在
          检查点检测到自己的信号后自行清理——每层只负责自己的执行，无
          中央调度器。
        - 协程（dispatch 钩子）；不接受原因参数（无参）。
        - cancel 后队列中待处理消息不丢弃——交应用层（重新投递 / 记录
          日志 / 通知发送方三选一）。
        - 工具被 cancel ≠ Turn 被 cancel：``cancel_children()`` 影响的
          工具返回正常 ``ToolResult``，Turn 循环照常。
        - :raises flowing.errors.Intercepted: ``before_cancel`` handler
          阻止取消（信号不置位）。

        .. seealso::

            - :meth:`stop` —— 强制式对称入口（默认等价本方法）。
            - :meth:`cancel_children` / :meth:`cancel_by_tag` —— 更细粒度。
            - :meth:`abort_turn` —— 只终止当前 Turn。
        """
        await self.hooks.before_cancel.dispatch(self, CancelContext())   # Intercepted 阻止取消（信号不置位）；普通异常上抛
        for execution in self._executions.values():
            execution.cancel.set()   # 协作式信号：执行体自行决定停止方式
        self._turn_abort.set()
        # after_cancel：信号置位后立即 dispatch——“取消已被接受”的事实事件
        # （回合真正退出的观察归 _run_turn finally 的
        #   after_turn，handler 读 turn.aborted 分流）
        await self.hooks.after_cancel.dispatch(self, CancelContext())

    def cancel_children(self) -> None:
        """只协作式取消全部子执行，不动回合退出信号（自己继续）。

        .. rubric:: 行为要点

        - 置位 ``_executions`` 全部 abort；Turn 循环照常。
        - 典型场景：亲代 Agent 推理到一半想调整方向——终止正在跑的子
          Agent / 工具，更新提示词，重新发起。
        - 同步方法（不 dispatch 钩子）。

        .. seealso:: :meth:`cancel`、:meth:`cancel_by_tag`
        """
        for execution in self._executions.values():
            execution.cancel.set()   # 不动 _turn_abort（自己继续），不 dispatch 钩子

    def cancel_by_tag(self, tag: str) -> None:
        """只置位 ``tags`` 匹配 ``tag`` 的执行条目的取消信号。

        .. rubric:: 行为要点

        精细控制：如只取消 ``"bash"`` 标签的工具执行，留 ``"agent"``
        继续；不动回合退出信号，不匹配任何条目时为空操作。同步方法。

        .. seealso:: :attr:`Execution.tags`、:meth:`cancel_children`
        """
        for execution in self._executions.values():
            if tag in execution.tags:
                execution.cancel.set()   # 不匹配任何条目时为空操作；不动 _turn_abort

    async def stop(self) -> None:
        """强制式停止——框架默认实现等价于 :meth:`cancel`，子类可覆写。

        .. rubric:: 行为要点

        - ``cancel()`` 是协作式（执行体可忽略信号）；执行体卡死 / 超时
          兜底需要更强手段。框架核心不提供强制 kill 通用实现——强制终止
          的手段高度依赖执行体类型（进程 kill、HTTP 连接关闭等），由子类
          覆写本方法注入。
        - 默认实现 = ``await self.cancel()`` （含 before/after_cancel
          钩子）。
        - 覆写约定：先调默认逻辑或自行置位信号，再做强制动作；Agent
          可覆写 ``cancel()`` 为空操作以完全无视协作信号（此时
          ``stop()`` 是唯一的停止通道）。

        .. seealso:: :meth:`cancel`、:meth:`stop_children`
        """
        await self.cancel()

    def stop_children(self) -> None:
        """与 :meth:`cancel_children` 对称的强制式版本；默认等价，可覆写。

        .. rubric:: 行为要点

        默认实现 = ``self.cancel_children()``；自己继续（不动回合退出
        信号）。同步方法。

        .. seealso:: :meth:`cancel_children`、:meth:`stop`
        """
        self.cancel_children()

    def stop_by_tag(self, tag: str) -> None:
        """与 :meth:`cancel_by_tag` 对称的强制式版本；默认等价，可覆写。

        .. rubric:: 行为要点

        默认实现 = ``self.cancel_by_tag(tag)``。同步方法。

        .. seealso:: :meth:`cancel_by_tag`、:meth:`stop`
        """
        self.cancel_by_tag(tag)

    # ────────────────────────── 子 Agent ──────────────────────────────────

    async def create_subagent(self, agent_type: str, *, name: str | None = None,
                              **kwargs: Any) -> Agent:
        """干净构造入口：创建并持有子 Agent 实例。

        .. rubric:: 功能介绍

        直接委托 ``runtime.create_agent``，把 ``agent_type`` 与
        ``**kwargs`` 原样透传、``parent_id`` 固定为 ``self.node_id``
        ——不提供别的，只提供“自己
        的 ``node_id`` 作为 ``parent_id``”。可选 ``name`` 为子代起语义
        名（登记进 ``child_ids``，供 ``invoke_subagent(resume=...)`` 按名
        续接；语义名只存在亲代侧表中，子实例不自持名字）。

        与 :meth:`invoke_subagent` 的分工：本方法不做
        ``SubagentEntry.resolve()``、不经过 ``on_subagent_invoke`` /
        ``on_subagent_returns`` 钩子（仅走创建管线的 ``before_create`` /
        ``after_create``）、不支持 ``resume`` 续接——适合“创建并持有
        实例”的钩子回调 / 外部代码 / 回合内工具。

        .. rubric:: 使用示例

        .. code-block:: python

            child = await self.create_subagent("payment", order_id="456")
            result = await child.query("发起退款")

        .. rubric:: 行为要点

        - ``agent_type`` 是字符串类型名，由 ``get_agent_class`` 惰性解析
          为 Agent 类。
        - 创建即注册（``_nodes``）+ 进入 ``_children`` + provide 链可
          上溯到本实例。
        - ``name`` 非空时创建成功后登记 ``child_ids[name] = node_id``
          并写透核心袋（整表覆写一行，末行合并防膨胀）；未命名子 Agent
          不入表。
        - 生命周期：调用方自行管理——默认存续（agent 池），显式
          ``destroy()`` 或随亲节点销毁。
        - 异常（类型名解析失败 / PENDING 检查失败等）原样上抛，亲代 Agent
          状态不变。

        .. seealso::

            - :meth:`invoke_subagent` —— 带 resolve + 唤起钩子的包装。
            - :meth:`flowing.runtime.Runtime.create_agent` —— 真正执行
              创建的唯一代码路径。
        """
        child = await self.runtime.create_agent(
            agent_type, parent_id=self.node_id, **kwargs)   # 直接委托，仅提供 parent_id
        self._children[child.node_id] = child   # 进入生命周期子树（node_id 为 key；创建即注册由 create_agent 管线完成）
        if name is not None:   # 语义名 -> agent_id 登记并写透（未命名子 Agent 不入表；语义名只存在亲代侧本表，子实例不自持）
            ids = dict(self.child_ids)   # core 袋唯一真值（D7 property 透传）
            ids[name] = child.node_id
            self._core_state["child_ids"] = ids   # 写透整表进核心袋（末行合并防膨胀）
        return child

    async def invoke_subagent(
        self,
        agent_type: str = "",
        *,
        prompt: str | None = None,
        name: str | None = None,
        resume: str | None = None,
        **kwargs: Any,
    ) -> SubagentResult:
        """LLM 工具路径的唤起入口：resolve + 唤起钩子 + 生命周期策略。

        .. rubric:: 功能介绍

        时序（两段式，内部拆 :meth:`_prepare_subagent` /
        :meth:`_run_subagent`）：

        - 准备段（同步 await）：新建路径按别名查 ``_subagent_entries`` →
          ``SubagentEntry.resolve()`` （LLM args → 完整 kwargs：别名映射 +
          specified 求值 + inject 注入）；续接路径跳过条目查找与 resolve
          （实例已存在，``kwargs`` 忽略）。随后构造
          :class:`SubagentInvocation` 并 dispatch 亲代 Agent 的
          ``on_subagent_invoke`` → 注册 ``Execution(kind="agent")`` →
          新建（内部调 :meth:`create_subagent`）或续接（``resume`` 按
          实例名找池中实例）。此段失败（``Intercepted`` / 校验 / 创建抛
          异常）同步上抛——子 Agent 要么成功创建要么未创建，工具路径由
          ``ToolResult(status="error")`` 承载（LLM 可见）。
        - 运行段（可后台）：``await child.query(prompt)`` 等待产出 →
          构造 :class:`SubagentResult` → dispatch
          ``on_subagent_returns`` （先于交付：handler 可改写 result，
          改写对两条路径同时生效）→ 用改写后的 result 构造
          ``Message(kind=SUBAGENT)`` 推入本实例队列（LLM 后续回合感知）
          并返回。运行段结局走 ``TurnResult`` 四态正常通道
          （``subagent_status`` 承载）。

        .. rubric:: 使用示例

        .. code-block:: python

            # 新建 + 命名
            result = await self.invoke_subagent(
                "coder", prompt="审查 auth 模块", name="my-reviewer")
            # 之后续接同一实例（保持原类型；``agent_type`` 缺省留空）
            result2 = await self.invoke_subagent(
                resume="my-reviewer", prompt="继续审查 payment 模块")

        .. rubric:: 行为要点

        - ``resume`` 与新建参数互斥：续接保持原类型，``kwargs`` 忽略
          （实例已存在），``agent_type`` 可缺省留空（工具路径下 LLM 按
          互斥契约只给 ``resume``）；``resume`` 找不到实例名 →
          ``ValueError``。查找
          载体 = ``child_ids`` 语义名表：目标实例活着直接用，已销毁 /
          未恢复则经 ``Runtime.get_agent`` 现场恢复并重新进入
          ``_children``。
        - 子 Agent 注册为 ``Execution(kind="agent")``——``cancel()`` /
          级联取消可命中；子 Agent 被 cancel 的已产出部分结果作为正常
          产物返回亲代 Agent：``SubagentResult.subagent_status`` 标
          ``"cancelled"``，``result`` 按统一填充规则。
        - 唤起失败（创建 / 校验 / 运行抛异常）原样上抛调用方——无专属
          错误钩子；工具路径由 ``ToolResult(status="error")`` 承载。
        - ``on_subagent_returns`` 在结果构造后、交付前 dispatch（value
          为 :class:`SubagentInvocation`，``result`` 已回填）：handler
          可改写 ``invocation.result``，改写对返回值与 SUBAGENT 消息同时
          生效（两条路径同源）；纯观察需求由不改写的 handler 承担。本
          钩子不接 ``Intercepted``——阻断闸在 ``on_subagent_invoke``。
        - 同步交付语义：本方法等待子 Agent 完成后，把改写后的
          ``SubagentResult`` 作为返回值交给调用方；不再另发 SUBAGENT
          消息入亲代队列。调用方（通常是同步工具路径）负责把结果放进自己
          的返回值 / 树节点，避免同源结果二次入队。
        - 外部代码需要异步执行且不入队时，用
          ``asyncio.create_task(agent.invoke_subagent(...))``——本方法是
          协程，可被后台执行；结果只交给该 Task，不会推入 Agent 队列。
        - ``subagent-invoke`` 工具的 ``asynchronized=True`` 是另一条特殊
          路径：它不走本方法，而是拆成 ``_prepare_subagent`` （同步创建）
          + 后台 ``_run_subagent(enqueue_result=True)``；完成后必定 enqueue
          SUBAGENT 消息，工具只返回 ``started`` 收据。外部代码若不想
          入队，不要走该工具异步路径，用
          ``asyncio.create_task(agent.invoke_subagent(...))``。
        - 无论是否入队，``on_subagent_returns`` 都先于交付 dispatch，
          改写后的结果同时是返回值与后续交付内容。
        - 不做 keep_alive 语义——生命周期由 agent 池“默认存续 + 显式
          销毁”管理。

        .. seealso::

            - :class:`SubagentEntry` —— resolve 与 catalog 的载体。
            - :class:`SubagentResult` / :class:`SubagentInvocation` ——
              结果与钩子 value。
            - :meth:`create_subagent` —— 干净构造入口。
        """
        child, invocation, execution = await self._prepare_subagent(
            agent_type, prompt=prompt, name=name, resume=resume, kwargs=kwargs)
        return await self._run_subagent(child, invocation, execution,
                                        enqueue_result=False)

    async def _prepare_subagent(
        self,
        agent_type: str,
        *,
        prompt: str | None,
        name: str | None,
        resume: str | None,
        kwargs: dict[str, Any],
    ) -> tuple[Agent, SubagentInvocation, Execution]:
        """``invoke_subagent`` 准备段（内部 API）：resolve + before 钩子 +
        Execution 注册 + 创建 / 续接。

        同步 await 的唤起前半段。本段返回即保证“子 Agent 已成功创建
        （或续接）”；本段内任何失败（``on_subagent_invoke`` 的
        ``Intercepted`` / 校验错误 / 创建抛异常）同步上抛调用方，且
        Execution 注册被回收——“成功创建或未创建”二态边界，无半登记
        状态。返回 ``(child, invocation, execution)`` 三元组，交由
        :meth:`_run_subagent` 消费；Execution 清理由运行段 finally 承担
        （本段异常路径自清理）。``name`` 经 ``SubagentInvocation.name``
        进创建管线：新建路径透传 :meth:`create_subagent` （登记
        ``child_ids``）；resume 路径忽略（实例已存在，且不查条目、不
        resolve——``kwargs`` 按契约忽略）。
        """
        # 续接路径（resume 非 None）不查条目、不 resolve：实例已存在，
        # kwargs 忽略（契约见 invoke_subagent docstring“resume 与新建参数
        # 互斥：续接保持原类型，kwargs 忽略”）。工具路径下 LLM 按互斥契约
        # 只给 resume、agent_type 为 ""——若无条件查条目会得到
        # dict[""] KeyError（续接必败回归，见 tests/builtins 清单 73 续接
        # 正负例）。
        entry: SubagentEntry | None = None
        init_kwargs: dict[str, Any] = {}
        if resume is None:
            entry = self._subagent_entries[agent_type]   # 按别名查（agent_type 形参承载别名）
            init_kwargs = entry.resolve(self, kwargs)   # LLM args -> 完整 kwargs
        invocation = SubagentInvocation(
            alias=agent_type,
            # spec 未写清处落实：骨架把别名直接当 agent_type 透传，与
            # SubagentInvocation.agent_type“取自 SubagentEntry.name_ori”的
            # 字段契约矛盾——按字段契约落实（别名是 LLM 面，类型名是创建面）
            agent_type=entry.name_ori if entry is not None else None,   # resume 与 agent_type 互斥
            name=name,
            resume=resume, prompt=prompt, args=init_kwargs)
        invocation = await self.hooks.on_subagent_invoke.dispatch(
            self, invocation)   # 可改写 args/prompt；Intercepted 硬阻断唤起（上抛，未创建实例）
        execution = Execution(id=self._next_execution_id(), kind="agent", tags=["subagent"],
                              started_at=datetime.now(),
                              cancel=asyncio.Event(), pause=asyncio.Event())
        self._executions[execution.id] = execution   # cancel()/级联取消可命中
        try:
            child: Agent
            if invocation.resume is not None:
                # 续接：语义名 -> agent_id 翻译（child_ids property，
                # core 袋真值），再经
                # Runtime.get_agent 按 id 取（活着直接用；已销毁/休眠 ->
                # 现场恢复——destroy ≠ 删除，记录保留）；表项不随 destroy 删除
                if invocation.resume not in self.child_ids:
                    raise ValueError(invocation.resume)   # 按名未找到 -> 报错
                child = await self.runtime.get_agent(
                    self.child_ids[invocation.resume])
                self._children[child.node_id] = child   # 重新进入生命周期子树（亲节点级联销毁恢复生效）
            else:
                child = await self.create_subagent(
                    entry.name_ori, name=invocation.name, **invocation.args)   # 新建路径：规范类型名（别名只存在绑定层；name 登记 child_ids）
        except BaseException:
            self._executions.pop(execution.id, None)   # 创建/续接失败：回收注册，不留半登记状态
            raise
        return child, invocation, execution

    async def _run_subagent(
        self,
        child: Agent,
        invocation: SubagentInvocation,
        execution: Execution,
        *,
        enqueue_result: bool = True,
    ) -> SubagentResult:
        """``invoke_subagent`` 运行段（内部 API，可后台）：等待产出 +
        after 钩子 + 交付 + Execution 清理。

        本段内不再有“创建失败”——结局只有 ``TurnResult`` 四态
        （``subagent_status`` 承载）。``on_subagent_returns`` 先于交付
        dispatch：handler 改写 ``invocation.result`` 后，返回值与可选的
        SUBAGENT 消息同源采用改写后的结果；本钩子不接 ``Intercepted``
        （阻断闸在 before，答卷级处置用改写表达）。

        ``enqueue_result``：``True`` （默认）→ dispatch 后构造独立
        ``Message(kind=SUBAGENT, priority=STEER)`` 入本实例队列——长
        turn 可达天级，子代完成推送须即时注入：回合进行中于检查点被
        吸收、当轮 context 可见、不打断。``False`` → 跳过入队，只返回
        结果；同步工具路径用 ``False`` 避免同一结果既作为 TOOL 消息挂
        树、又作为 SUBAGENT 消息再次入队。finally 清理 Execution 注册
        （无论结局）。

        级联取消：亲代侧等待被中断（``Tool.__call__`` 竞速注入 /
        destroy 拆循环）→ 本段捕获 CancelledError，先给子 Agent 自己的
        回合退出信号（``child.abort_turn()``——协作式，子回合自行收尾
        与封闭），再原样上抛。
        """
        try:
            turn_result = await child.query(invocation.prompt or "")   # 等待产出（prompt 可为 None：纯参数唤起）
            result = SubagentResult(name_alias=invocation.resume or invocation.name or invocation.alias,   # 语义名只存在亲代侧
                                    subagent_id=child.node_id,
                                    result=child.last_result,   # 收尾段已写入（resolve waiters 之前，无时序竞争）；finish → dict，普通 → 文本，无产出 → None
                                    subagent_status=turn_result.status)   # 取消/异常信息载体（此前 turn_result 接住未用，自此启用）
            invocation.result = result   # after 阶段回填
            invocation = await self.hooks.on_subagent_returns.dispatch(
                self, invocation)   # 先于交付：可改写 result；不接 Intercepted
            result = invocation.result
            if enqueue_result:
                # 交付：以改写后的 result 构造独立 Message(kind=SUBAGENT,
                # priority=STEER) 推入本实例队列（长 turn 可达天级，须即时
                # 注入：②.5 吸收、当轮可见、不打断；消息路径与代码路径同源）。
                # content 塑形与 output_to_blocks 同口径：
                # str → [TextBlock]；dict → [StructBlock]；None → []。
                from flowing.tool import output_to_blocks   # 局部 import 破环（agent ↔ tool 共享塑形出口，D22）

                await self.enqueue_message(Message(
                    kind=MessageKind.SUBAGENT,
                    source=f"subagent:{result.subagent_id}",
                    content=output_to_blocks(result.result),
                    priority=MessagePriority.STEER))
        except asyncio.CancelledError:
            # 级联取消：亲代侧等待被中断（Tool.__call__ 竞速注入 / destroy
            # 拆循环）——子 Agent 收自己的回合退出信号（协作式，自行收尾
            # 与配对封闭），随后原样上抛
            child.abort_turn()
            raise
        finally:
            self._executions.pop(execution.id, None)
        return result

    # ────────────────────────── 工具调用 ──────────────────────────────────

    async def tool_call(self, tool_call: ToolCall) -> ToolResult:
        """Agent 工具调用方法——完整钩子链的执行包装。

        .. rubric:: 功能介绍

        时序：dispatch ``before_tool_call`` （可改写 ``ToolCall``；
        handler 可设置 ``tool_call.shortcut`` 直接给出结果、跳过工具
        执行；``raise Intercepted`` 硬阻断）→ 按别名查
        ``_tool_entries`` → :meth:`_normalize` （LLM 视角校验 / 别名映射
        与 ``ToolEntry.resolve()`` 聚合 / 默认值填充）→ ``Tool.__call__``
        调度（注册 ``Execution(kind="tool")``，finally 清理）→ 元信息
        接线（``name`` / ``tool_call_id`` / ``production``）→ dispatch
        ``on_tool_yields`` （仅 ``Tool.__call__`` 执行产出的非 blocked
        结果触发——blocked 语义即“没有产物”；shortcut 与 LLM 校验失败
        的产物不经过本点；可改写 ``output`` 原料值）→ dispatch
        ``after_tool_call`` （可改写结果；shortcut 路径照常触发）→ 收尾
        归一（return 前幂等再跑一次 ``normalize_output``，封 shortcut 与
        钩子改写两条缝）→ 返回。

        .. rubric:: 行为要点

        - 仅按别名查找；未命中 → ``UnknownToolError`` （无规范名回退——
          回退会绕开 Agent 级绑定）。
        - ``Intercepted`` → 返回 ``ToolResult.blocked(...)`` （LLM 可见
          形态为 ``[TextBlock(reason)]``），不上抛。
        - ``ToolResult(status="error")`` 是正常产物：LLM 可见、不触发
          任何错误钩子（工具业务错误不走异常通道）。
        - LLM 视角校验失败同属正常产物：包装为
          ``ToolResult(status="error", error=<LLM 命名空间的错误文本>)``，
          ``after_tool_call`` 照常触发，随后正常返回进消息树——LLM 的
          自我修正反馈，不是回合异常。内部校验失败（specified / inject /
          默认值的配置错误，在 ``Tool.__call__`` 触发）例外：上抛框架
          错误通道 + 日志，不包成 ToolResult、不进 LLM 可见文本。
        - abort 于工具调用循环中：批次前检查点③的批次整体跳过（不
          execute）；批次在途的工具由 ``Tool.__call__`` 的取消竞速中断，
          各产 cancelled 结果挂树。
        - 本方法没有同步 / 异步开关：同步还是异步由工具的 ``execute``
          实现决定。``execute`` 是普通 ``async def`` → ``Tool.__call__``
          await 到底，本方法返回最终 ``ToolResult``，不 enqueue；
          ``execute`` 返回 ``asyncio.Task`` → 走异步工具透明化的固定
          enqueue 路径。因此“是否入队”只取决于工具实现形态。
        - 异步工具透明化：``execute()`` 返回 ``asyncio.Task`` 时
          ``Tool.__call__`` 不等待，立即产 ``ToolResult(status="pending")``
          收据（经 ``as_message`` 塑形为 ``tool_status="pending"``、
          ``content=[]`` 的 TOOL 消息挂树，配对一次性封闭）；后台驱动
          经 ``_drive_background`` 接缝在本 Agent 侧启动。终值投递：
          归一 → dispatch ``on_tool_yields`` → ``output_to_blocks`` →
          ``Message(kind=EVENT, source="tool_result", ...)`` 构造
          STEER 消息进队列（长 turn 可达天级，完成推送须即时
          注入——检查点吸收、当轮 context 可见、不打断）；任务异常 →
          标注块 + 错误文本块（与同步 error 同语义，LLM 可见）。

        .. seealso::

            - :class:`flowing.tool.ToolEntry` —— 绑定条目（resolve 三步）。
            - :class:`flowing.tool.ToolResult` —— 四状态结果。
            - :class:`flowing.tool.ToolCall` —— 入参结构。
        """
        # 异常路径：before_tool_call handler raise Intercepted -> 捕获（不上抛）
        # 返回 ToolResult.blocked(reason)（Intercepted 未在本模块具名引入）
        try:
            tool_call = await self.hooks.before_tool_call.dispatch(self, tool_call)   # 可改写 ToolCall
        except Intercepted as exc:
            # 硬阻断：生成 blocked 结果（唯一来源），工具本体不执行；
            # after_tool_call 不触发（hooks 模块异常规则：Intercepted 由调用方保证）
            return ToolResult.blocked(reason=str(exc))
        if tool_call.shortcut is not None:
            # 协商短路：handler 提供的 ToolResult 直接作为本次结果，跳过查表与执行；
            # after_tool_call 照常触发（hooks 模块异常规则：shortcut 不是错误）；
            # shortcut 产物不经 Tool.__call__ 的归一点，归一责任落到本方法收尾（D19）
            try:
                result = await self.hooks.after_tool_call.dispatch(self, tool_call.shortcut)
            except Intercepted as exc:
                result = ToolResult.blocked(reason=str(exc))   # after_tool_call 拦截 → 伪造 blocked result 返回（不传播终结工作循环）
        else:
            if tool_call.name not in self._tool_entries:
                raise UnknownToolError(tool_call.name)   # 仅按别名查找；无规范名回退（回退会绕开 Agent 级绑定）
            entry = self._tool_entries[tool_call.name]
            tool = self.runtime.tool_registry.get(entry.name_ori)   # 按规范名取可执行对象（提前：_normalize 的校验与默认值填充需其 definition.params_schema）
            # _normalize 的 LLM 视角校验失败 -> ToolResult(status="error")
            # 正常产物（错误文本以 LLM 命名空间/别名，LLM 自我修正反馈），
            # after_tool_call 照常触发；内部校验已迁入 Tool.__call__，
            # 失败 -> 上抛框架错误通道 + 日志
            try:
                resolved_args: dict[str, Any] = self._normalize(entry, tool_call, tool)
            except (ValidationError, _LlmViewValidationError) as exc:
                result = ToolResult(status="error", error=str(exc))
            else:
                execution = Execution(id=self._next_execution_id(), kind="tool", tags=[],
                                      started_at=datetime.now(),
                                      cancel=asyncio.Event(), pause=asyncio.Event())
                self._executions[execution.id] = execution
                try:
                    result = await tool(resolved_args, caller=self,
                                        execution=execution,
                                        tool_call=tool_call)   # Tool.__call__ 调度
                    # abort 于工具调用循环中：批次前检查点③整批跳过；在途
                    # 工具由 Tool.__call__ 的取消竞速中断（产 cancelled 结果）
                finally:
                    self._executions.pop(execution.id, None)
                # on_tool_yields 统一点：仅 Tool.__call__ 执行产出的非 blocked
                # 结果触发（blocked 语义即“没有产物”；shortcut 与 LLM 校验
                # 失败的 error 各在自己的分支，结构性不经过本点）
                result.name = tool_call.name
                result.tool_call_id = tool_call.id
                result.production = "receipt" if result.status == "pending" else "sync"
                if result.status != "blocked":
                    try:
                        result = await self.hooks.on_tool_yields.dispatch(self, result)   # 可改写 output 原料值
                    except Intercepted as exc:
                        result = ToolResult.blocked(reason=str(exc))   # 拦截 → blocked（工具已执行，结果被丢弃）
            try:
                result = await self.hooks.after_tool_call.dispatch(self, result)   # 可改写结果；改写产物为原料时归一责任在下方收尾（D19）
            except Intercepted as exc:
                result = ToolResult.blocked(reason=str(exc))   # after_tool_call 拦截 → 伪造 blocked result 返回（不传播终结工作循环）
        # 收尾归一（D19 统一出口）：return 前幂等再跑一次 normalize_output
        # （幂等——Tool.__call__ 归一点产物原样放行，双调用点安全），封
        # shortcut 与 after_tool_call 改写两条缝；不变量：出 Agent.tool_call
        # 的 ToolResult.output 恒为五形态之一
        result.output = await normalize_output(result.output)
        return result

    def _normalize(
        self, entry: ToolEntry, tool_call: ToolCall, tool: "Tool"
    ) -> dict[str, Any]:
        """工具参数规范化（内部 API，不属稳定契约）。

        ``tool_call()`` 管线的中段：把（可能被钩子改写过的）
        ``tool_call.args`` 聚合为 ``execute()`` 的最终参数字典。归属 Agent
        而非 ToolEntry，是因为聚合需要 Agent 上下文：Parsable 渲染上下文、
        provide 链（``self.inject``）、以及 LLM 视角校验失败后的错误通道。

        三步：

        1. LLM 视角校验（先于一切转换）：``tool_call.args`` 按 LLM 可见
           schema 校验（经 ``entry.llm_definition(...).params_schema``
           桥接；``strict=False`` 的工具不施加未知键拒绝）。校验失败：
           错误文本以 LLM 命名空间（别名）进入 ``ToolResult.error``；
           LLM 传出 schema 未定义的参数（幻觉参数）按“未定义参数”校验
           错误处理。
        2. :meth:`flowing.tool.ToolEntry.resolve`——别名映射回规范名 →
           ``specified`` 惰性求值覆盖（固定值 / 注入表达式——注入表达式
           在求值时沿 provide 链上溯）。
        3. schema 默认值填充：按 ``tool.definition.params_schema`` 各
           property 的 ``default`` 补齐前两步均未给的参数。

        内部校验（specified / 默认值的配置错误）不在此处——由
        ``Tool.__call__`` 在 caller 注入之后按创建时的 ``_args_model``
        校验，失败上抛框架错误通道 + 日志，不进 LLM 可见文本。
        """
        # 1. LLM 视角校验（先于一切转换）：校验模型由
        #    entry.llm_definition(self.runtime, self).params_schema 经
        #    params.schema_to_model 桥接（推导归属 ToolEntry）；失败 ->
        #    错误文本以 LLM 命名空间进 ToolResult.error（由 tool_call 包装为正常产物）
        #    ——校验模型按 LLM 视图（别名化 + 隐藏参数排除后的 schema）建模，
        #    错误消息的字段名因此天然落在 LLM 命名空间
        llm_schema = entry.llm_definition(self.runtime, self).params_schema
        llm_model = schema_to_model(f"{entry.name_alias}-llm-args", llm_schema)
        llm_model.model_validate(tool_call.args)   # 类型/必填错误 -> ValidationError 上抛给 tool_call 包装
        unknown = sorted(set(tool_call.args) - set(llm_schema))
        if unknown and tool.definition.strict:
            # 幻觉参数（LLM 传出 schema 未定义的参数）按“未定义参数”校验
            # 错误处理——桥接模型默认忽略多余键，未知键在此显式拒绝；
            # strict=False 工具不施加本拒绝（工具层不限制参数，未知键
            # 原样放行进入下方聚合，由下游自行校验）
            raise _LlmViewValidationError(
                f"tool {entry.name_alias!r} received undefined parameters: {unknown}")
        # 2. 别名映射 -> specified 求值覆盖（固定值/注入表达式；注入表达式
        #    求值时经 self.inject 沿 provide 链上溯）
        resolved = entry.resolve(self, tool_call.args, tool.definition.params_schema)
        # 3. schema 默认值填充：按 tool.definition.params_schema 各 property 的
        #    default 补齐前两步均未给的参数
        for key, prop in tool.definition.params_schema.items():
            if key not in resolved and isinstance(prop, dict) and "default" in prop:
                resolved[key] = prop["default"]
        return resolved

    def source_dir(self) -> Path | None:
        """本 Agent 的文件上下文：``source_file`` 所在目录。

        ``source_file`` 是 ``@/`` 格式字符串，经 ``runtime.resolve_path``
        落地为绝对路径后取 ``.parent``；``source_file`` 为 ``None`` →
        返回 ``None`` （无文件上下文，下游裸名解析退化为纯注册表查询；
        FILE_REF 用 ``./`` 相对路径时由 ``resolve_path`` 报错）。“文件 →
        所在目录”换算的唯一承担者：``get_tool`` / ``get_agent_class``、
        插件挂载的技能工具、以及 Parsable 的 FILE_REF 求值一律经本方法，
        不允许调用方自行对 ``source_file`` 取 parent。
        """
        if type(self).source_file is None:
            return None
        return self.runtime.resolve_path(type(self).source_file).parent   # @/ 格式落地后取所在目录

    def get_tool(self, name_or_path: str) -> Tool:
        """上下文感知的工具解析门面：自动携带本 Agent 的 ``source_dir``。

        .. rubric:: 功能介绍

        薄委托 ``runtime.tool_registry.get`` （``name_or_path`` +
        ``source_dir=self.source_dir()``）——与直接调注册表的区别仅在文件
        上下文：裸名先查本 Agent 定义文件所在目录的定向文件查找链（文件
        覆盖 ``default::`` / ``builtin::``），``./`` / ``../`` 相对路径
        可用。无文件上下文（``source_file`` 为 ``None``）时退化为纯注册
        表查询。

        .. rubric:: 行为要点

        - 未找到 → 抛 :class:`flowing.errors.ToolNotFoundError` （注册表与
          文件链均不命中）。

        .. seealso:: :meth:`flowing.tool.ToolRegistry.get` （完整解析语义
            与候选链）、:meth:`get_agent_class` （同构门面）
        """
        return self.runtime.tool_registry.get(name_or_path, source_dir=self.source_dir())

    async def _prepare_tool_refs(self, refs: "list[EntryRef]") -> "list[EntryRef]":
        """``.fya`` 装配的工具绑定预处理（内部 API，不属稳定契约）。

        装配层生成的 setup 包装器把 ``_FYA_TOOLS`` 经本方法换算为最终
        绑定列表后逐条 ``add_tool``：

        - 裸名条目先经
          :meth:`flowing.tool.registry.ToolRegistry.expand_mcp` 尝试
          MCP 合成名展开（不命中即无操作）；``list_tools()`` 是异步的，
          装配是本管线唯一的异步点，``add_tool`` 保持同步契约；
        - 解析产物为 MCP 组骨架（任何形态——裸名组名 / 路径 / 路径 glob
          命中）→ 经 ``expand_mcp`` 的组展开换成逐工具条目（组引用的
          别名不继承，子代理按合成名落账；覆写体由每个子条目继承）；
        - 裸名模式（含 glob 字符、不含 ``/``）经
          :meth:`flowing.tool.registry.ToolRegistry.glob` 展开为逐条
          条目，模式条目本身不进绑定列表；
        - 展开产物的别名与已收条目重复时：同一资源（同注册表键）跳过，
          不同资源告警并跳过（已收条目胜出——“glob 显式优先”的名字
          空间延伸）。
        """
        registry = self.runtime.tool_registry
        final: list[EntryRef] = []
        claimed: dict[str, EntryRef] = {}

        def _append_hit(key: str, body: "Mapping[str, Any]") -> None:
            alias = key.rsplit("::", 1)[-1]
            existing = claimed.get(alias)
            if existing is not None:
                try:
                    same = self.get_tool(existing.raw).registry_key == key
                except Exception:
                    same = False
                if not same:
                    _logger.warning(
                        "name-glob hit skipped (alias %r already claimed by another resource): %s",
                        alias, key)
                return
            hit = EntryRef(raw=key, alias=alias, body=body)
            claimed[alias] = hit
            final.append(hit)

        for ref in refs:
            raw = ref.raw
            if isinstance(raw, str) and "/" not in raw:
                await registry.expand_mcp(raw, source_dir=self.source_dir())
                if GLOB_META.search(raw):   # 名字模式：注册表展开
                    for key in registry.glob(raw):
                        _append_hit(key, ref.body)
                    continue
            tool = self.get_tool(raw)   # 存在性解析（未命中照常 ToolNotFoundError）
            if isinstance(tool, McpTool) and not getattr(tool, "_server_tool_name", None):
                # 组骨架引用（任意形态）→ 展开为逐工具条目
                for key in await registry._expand_group(tool):
                    _append_hit(key, ref.body)
                continue
            final.append(ref)
            claimed.setdefault(ref.alias, ref)
        return final

    async def _prepare_agent_refs(self, refs: "list[EntryRef]") -> "list[EntryRef]":
        """``.fya`` 装配的子 Agent 绑定预处理（内部 API，不属稳定契约）。

        与 :meth:`_prepare_tool_refs` 同构：路径形态与裸名精确引用原样
        保留；裸名模式经
        :meth:`flowing.agent_registry.AgentRegistry.glob` 展开为逐条条目
        （别名判重规则同工具侧）。Agent 类型无远程目录，本方法不含异步
        展开段。
        """
        final: list[EntryRef] = []
        claimed: dict[str, EntryRef] = {}
        for ref in refs:
            raw = ref.raw
            if not isinstance(raw, str) or "/" in raw:
                final.append(ref)
                claimed.setdefault(ref.alias, ref)
                continue
            if not GLOB_META.search(raw):
                final.append(ref)
                claimed.setdefault(ref.alias, ref)
                continue
            for key in self.runtime.agent_registry.glob(raw):
                alias = key.rsplit("::", 1)[-1]
                existing = claimed.get(alias)
                if existing is not None:
                    try:
                        same = (self.get_agent_class(existing.raw).registry_key == key)
                    except Exception:
                        same = False
                    if not same:
                        _logger.warning(
                            "name-glob hit skipped (alias %r already claimed by another resource): %s",
                            alias, key)
                    continue
                hit = EntryRef(raw=key, alias=alias, body=ref.body)
                claimed[alias] = hit
                final.append(hit)
        return final

    def get_agent_class(self, agent_type: str) -> type[Agent]:
        """上下文感知的 Agent 类型解析门面：自动携带本 Agent 的
        ``source_dir``。

        .. rubric:: 功能介绍

        薄委托 ``runtime.get_agent_class`` （``agent_type`` +
        ``source_dir=self.source_dir()``）——语义与 :meth:`get_tool` 同构
        （裸名文件链优先、文件覆盖注册表；无文件上下文退化为纯注册表
        查询）。Agent 侧只有 ``get_agent_class``，没有 ``get_agent``——
        取活实例 / 现场恢复统一走
        :meth:`flowing.runtime.Runtime.get_agent`。

        .. rubric:: 行为要点

        - 未找到 → 抛 :class:`flowing.errors.AgentTypeNotFoundError`
          （``get_agent_class`` 的失败形态）。

        .. seealso:: :meth:`flowing.runtime.Runtime.get_agent_class`、
            :meth:`flowing.runtime.Runtime.get_agent`
        """
        return self.runtime.get_agent_class(agent_type, source_dir=self.source_dir())

    def add_tool(
        self,
        name: str | EntryRef,
        *,
        alias: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> ToolEntry:
        """向本 Agent 添加一条工具绑定条目（``_tool_entries`` 的公开写入入口）。

        .. rubric:: 功能介绍

        按引用（``name`` 为字符串时）或现成的 :class:`flowing.parser.EntryRef`
        找到工具，判别覆写体（``body``）并构造 :class:`flowing.tool.ToolEntry`，
        以别名（``alias`` / ``ref.alias``，缺省 = 规范名）为 key 写入
        ``self._tool_entries``。从此该工具进入本 Agent 的可见集
        （``visible=True`` 时对 LLM 可见）与可调用集（``tool_call()`` 仅按
        别名查本表）。

        两种调用形态（内部统一归一为 EntryRef 后走同一条管线）：

        - ``.fya`` 装配层（主调用方）：``parse_fya`` 产出的 ``EntryRef``
          直接透传——``add_tool(ref)``，此时 ``alias`` / ``body`` 必须缺省
          （与 ``ref`` 自带字段重复 → :class:`flowing.errors.FormatError`）；
        - 程序化（``setup()`` / 运行期）：调用
          ``add_tool(name, alias=..., body=...)``，``body`` 与 ``.fya``
          单键映射项的覆写映射同构
          （键集 ``description`` / ``args`` / ``output`` / ``visible``；
          不接受 ``inject`` 键——注入写 args 里的
          ``"{{ self.inject('key') }}"`` 表达式）；内部经
          :func:`flowing.parser.normalize_entries` 构造 EntryRef。

        .. rubric:: 使用示例

        .. code-block:: python

            # 插件 setup 期的典型形态（裸名，无覆写）
            self.add_tool("schedule-cron")

            # 程序化带覆写——body 与 .fya 单键映射项的覆写映射同构
            self.add_tool(
                "make-payment", alias="pay",
                body={"args": {"amount": {"description": "支付金额（元）"},
                               "user_id": "{{ self.inject('user_id') }}"}},
            )

            # .fya 装配层：parse_fya 产出的 EntryRef 直接透传
            self.add_tool(entry_ref)

        .. rubric:: 行为要点

        - 同步、立即生效：下一次上下文组装即可见。两个程序化边界：
          MCP 合成名（MCP 组展开产物）须先经
          :meth:`flowing.tool.registry.ToolRegistry.expand_mcp` 异步展开
          注册（``.fya`` 装配路径自动完成，程序化路径自调一次）；名字
          glob（含 ``*`` 的模式）只在 ``.fya`` 装配路径展开
          （``_prepare_tool_refs``），本方法按字面值解析、不展开。
        - 同 alias 重复添加 → :class:`flowing.errors.EntryNameConflictError`。
          每次生命周期（create / recover）都从 ``__init__`` 的空
          ``_tool_entries`` 开始重放 ``setup()``；同一生命周期内重复添加
          同 alias 是笔误，快速失败（tool / skill / subagent 绑定层统一
          语义，“都报错不覆盖”）。
        - glob 显式优先：``tools:`` 装配层展开 glob 时，与已显式声明条目
          规范名相同的同一资源跳过；只有不同资源得到同一 alias 时，才按
          上一条报 ``EntryNameConflictError``。glob 命中经
          :func:`flowing.tool.registry._tool_glob_accept` 过滤（纯名字
          分析：目录探测工具候选链、显式标记仅 ``*.tool.fya``、其余
          ``.fya`` / ``.py`` 直接纳入），纳入命中在创建期 eager 解析，
          非法资源照常 fail-fast。
        - body 判别（本方法体内，单点维护）：键集固定为 ``description`` /
          ``args`` / ``output`` / ``visible``；未知键 →
          :class:`flowing.errors.FormatError` （含旧 ``inject`` 键——已
          删除，注入写 args 里的注入表达式）。各键去向：

          - ``description`` → ``override_description``；``str`` 构造时包装
            为 :class:`flowing.parsable.Parsable` 常量，``Parsable`` 原样
            透传，``_`` （PENDING）→ 空补丁（视为无覆写）；
          - ``args`` → 逐参数判别：dict 值 → ``override_params`` 稀疏补丁
            （JSON Schema 关键字）；键含 ``as`` → ``param_aliases``；
            ``_`` → 空补丁；其它值 → ``specified`` （包装 ``Parsable``，
            惰性求值；注入表达式在此落入）；
          - ``output`` → 独立判别分支：值是“字段名 → JSON Schema 定义”
            映射，逐字段并入 ``override_params``，不经过 args 的关键字
            校验（``type`` 等键在此合法）；“省略字段 = 移除”语义见
            ``FinishTool`` 规约；
          - ``visible`` → 布尔原样。
        - 深层块（``$tools.<alias>.args.<param>.description:``）的填回先于
          本方法调用（装配层时序约束：先 merge 具名块，再逐条目调本方法）
          ——本方法看到的 ``body`` 是已合并的最终形态。
        - 执行时按 ``name_ori`` 现场查注册表，entry 不持有 Tool 实例引用；
          不写持久化状态（entry 表由声明 / ``setup()`` 重建，不落盘）。
        - 边缘情况：``name`` / ``ref.raw`` 未注册 → 经 :meth:`get_tool`
          （携带本 Agent 的 ``source_dir``）先走定向文件查找链惰性解析
          ——文件覆盖 ``default::`` / ``builtin::``；查找链仍不命中才抛
          :class:`flowing.errors.ToolNotFoundError`。

        :param name: 规范名 / 路径 / ``ns::name`` 引用串，或 ``.fya`` 解析
            产出的 ``EntryRef`` （此时 ``alias`` / ``body`` 必须缺省）。
        :param alias: LLM 看到的别名；缺省按 ``normalize_entries`` 推断。
        :param body: 覆写映射，与 ``.fya`` 单键映射项的值同构；``None``
            为无覆写。
        :returns: 新创建的 ``ToolEntry`` （便于链式修改，如置 ``visible``）。
        :raises flowing.errors.ToolNotFoundError: 引用未在注册表 / 查找链。
        :raises flowing.errors.EntryNameConflictError: 同 alias 条目已存在。
        :raises flowing.errors.FormatError: EntryRef 与 ``alias`` / ``body``
            重复给值；``body`` 含未知键或非法形态。

        .. seealso:: :class:`flowing.tool.ToolEntry`、
            :meth:`tool_call`、:class:`flowing.parser.EntryRef`
        """
        # 第 0 步：归一为 EntryRef（唯一构造通道 = normalize_entries）
        if isinstance(name, EntryRef):
            if alias is not None or body is not None:   # 与 ref 自带字段重复 -> 笔误
                raise FormatError("EntryRef and alias/body cannot be given together")
            ref = name
        else:
            item = {f"{name} as {alias}" if alias is not None else name: body or {}}
            ref = normalize_entries([item], naming=TOOL_NAMING)[0]
        tool = self.get_tool(ref.raw)   # 存在性解析（声明期：文件链命中则此刻实例化注册，文件覆盖 default::/builtin::；未命中抛 ToolNotFoundError）
        # 落账 name_ori：文件派生工具记派生限定键（tool.registry_key 含目录
        # 派生命名空间，热路径精确命中）；注册表命中（default::/builtin::）
        # 保持裸名（裸名视图热路径可查）
        name_ori = (tool.registry_key
                    if tool.registry_key not in (f"default::{ref.raw}", f"builtin::{ref.raw}")
                    else ref.raw)
        key = ref.alias
        if key in self._tool_entries:   # 同 alias 重复添加 = 笔误（绑定层统一 fail-fast，见 docstring）
            raise EntryNameConflictError(key, kind="tool")
        # 第 1 步：body 判别（键集校验 + 逐键去向，规则见 docstring；未知键 -> FormatError）
        #   description -> override_description（PENDING->None；str->Parsable 包装；Parsable 透传）
        #   args -> 逐参数 split_as 判别：dict->override_params / as->param_aliases /
        #           PENDING->空补丁 / 其它->specified（Parsable 包装）
        #   output -> 独立分支：逐字段并入 override_params（不经 args 关键字校验）
        #   inject -> FormatError（已删除：注入写 args 里的注入表达式）；visible -> 原样
        override_description, override_params, specified = None, {}, {}
        param_aliases, visible = {}, True
        for body_key, body_val in ref.body.items():
            if body_key == "description":
                override_description = _as_parsable_patch(body_val)
            elif body_key == "args":
                if not isinstance(body_val, Mapping):
                    raise FormatError(f"tool override args must be a mapping: {body_val!r}")
                _classify_override_args(body_val, override_params, specified, param_aliases)
            elif body_key == "output":
                # 独立判别分支（B 方案）：“字段名 -> JSON Schema 定义”映射
                # 逐字段并入 override_params（type 等键在此合法，不经 args 的
                # 关键字校验）——“省略字段 = 移除”语义见 FinishTool 规约。
                # spec 未写清处落实：骨架注释的 {"schema": 定义} 包装与
                # apply_param_overrides 的 property->patch 形态不一致，按行为
                # 规约正文“逐字段并入 override_params”落实（不套 schema 键）
                if not isinstance(body_val, Mapping):
                    raise FormatError(f"tool override output must be a mapping: {body_val!r}")
                for field_name, field_def in body_val.items():
                    override_params[field_name] = dict(field_def)
            elif body_key == "visible":
                visible = bool(body_val)
            else:
                raise FormatError(f"tool override body contains unknown key: {body_key!r}")
        entry = ToolEntry(
            name_alias=key,
            name_ori=name_ori,
            override_description=override_description,
            override_params=override_params,
            specified=specified,
            param_aliases=param_aliases,
            visible=visible,
        )
        self._tool_entries[key] = entry
        return entry

    def add_agent(
        self,
        name: str | EntryRef,
        *,
        alias: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> SubagentEntry:
        """向本 Agent 添加一条子 Agent 绑定条目（``_subagent_entries``
        的公开写入入口）——与 :meth:`add_tool` 同构。

        .. rubric:: 功能介绍

        命名注意：本方法添加的是类型绑定条目（Agent 类 + LLM 可见声明 +
        覆写），不是 Agent 实例——实例创建走 :meth:`create_subagent` /
        :meth:`invoke_subagent`。“add”的对象是“这个 Agent 如何使用某子
        Agent 类型”的声明。按引用找到子 Agent 类型，判别覆写体并构造
        :class:`flowing.subagents.SubagentEntry`，以别名为 key 写入
        ``self._subagent_entries``。从此该类型进入本 Agent 的 catalog
        （``visible=True`` 时对 LLM 可见）与可唤起集
        （``invoke_subagent()`` 仅按别名查本表）。

        两种调用形态（与 ``add_tool`` 同一管线）：

        - ``.fya`` 装配层（主调用方）：``subagents:`` 条目解析产出的
          ``EntryRef`` 直接透传——``add_agent(ref)``，此时 ``alias`` /
          ``body`` 必须缺省（与 ``ref`` 自带字段重复 →
          :class:`flowing.errors.FormatError`）；
        - 程序化（``setup()`` / 运行期）：调用
          ``add_agent(name, alias=..., body=...)``，``body`` 与 ``.fya``
          单键映射项的覆写映射同构
          （键集 ``system_prompt`` / ``description`` / ``args`` /
          ``visible``——无 ``output``：输出 schema 覆写是 Tool 面概念；
          无 ``inject`` 键，注入写 args 里的
          ``"{{ self.inject('key') }}"`` 表达式）；内部经
          :func:`flowing.parser.normalize_entries` 构造 EntryRef。

        ``name`` 接受全部引用形态，与 ``.fya`` 声明完全一致：裸名 /
        ``ns::name`` / 相对路径（``./`` ``../``，相对本 Agent 的
        ``source_dir``）/ 绝对路径 / ``@/`` 锚定路径。

        .. rubric:: 行为要点

        - 同步、立即生效：下一次上下文组装即渲染进 catalog。名字 glob
          （含 ``*`` 的模式）只在 ``.fya`` 装配路径展开
          （``_prepare_agent_refs``），本方法按字面值解析、不展开。
        - 同 alias 重复添加 → :class:`flowing.errors.EntryNameConflictError`
          （绑定层统一 fail-fast，与 tool / skill 同口径）。
        - body 判别（本方法体内，单点维护）：键集固定为 ``system_prompt`` /
          ``description`` / ``args`` / ``visible``；未知键 →
          :class:`flowing.errors.FormatError` （含不接受 ``inject`` 键）。
          各键去向：

          - ``system_prompt`` → ``override_system_prompt``；``str`` 包装为
            :class:`flowing.parsable.Parsable`，``Parsable`` 透传，``_``
            （PENDING）→ 空补丁（视为无覆写）。求值时机：子 Agent 创建时
            一次，上下文为亲代 Agent 实例；
          - ``description`` → ``override_description``，同上包装；求值
            时机：亲代 Agent 路由决策 / catalog 渲染时，上下文为亲代 Agent
            实例；
          - ``args`` → 逐参数判别（规则同 ``add_tool``）：dict 值 →
            ``override_params``；键含 ``as`` → ``param_aliases``；``_`` →
            空补丁；其它值 → ``specified`` （包装 Parsable，
            ``invoke_subagent()`` 内以亲代 Agent 实例上下文求值）。差异：
            ``inject`` 目标是子 Agent 初始化参数而非 ``execute()`` 参数；
          - ``visible`` → 布尔原样。
        - 深层块（``$subagents.<alias>.xxx:``）填回先于本方法调用（装配层
          时序约束，与 tool 侧同律）。
        - 边缘情况：``name`` / ``ref.raw`` 未命中 → 经 :meth:`get_agent_class`
          （携带 ``source_dir``）走文件链惰性解析——文件覆盖
          ``default::`` / ``builtin::``；仍不命中抛
          :class:`flowing.errors.AgentTypeNotFoundError`。
        - entry 不持有子 Agent 类引用以外的任何实例状态；不写持久化
          （条目表由声明 / ``setup()`` 重建）。

        :param name: 引用串（全形态，见上）或 ``.fya`` 解析产出的
            ``EntryRef`` （此时 ``alias`` / ``body`` 必须缺省）。
        :param alias: catalog 与 ``invoke_subagent`` 用的别名；缺省按
            ``normalize_entries`` 推断。
        :param body: 覆写映射，与 ``.fya`` 单键映射项的值同构。
        :returns: 新创建的 ``SubagentEntry``。
        :raises flowing.errors.EntryNameConflictError: 同 alias 条目已存在。
        :raises flowing.errors.FormatError: EntryRef 与 ``alias`` / ``body``
            重复给值；``body`` 含未知键或非法形态。
        :raises flowing.errors.AgentTypeNotFoundError: 引用在注册表与文件链
            均不命中。

        .. seealso:: :class:`flowing.subagents.SubagentEntry`、
            :meth:`invoke_subagent`、:meth:`add_tool` （同构管线）
        """
        # 第 0 步：归一为 EntryRef（唯一构造通道 = normalize_entries）
        if isinstance(name, EntryRef):
            if alias is not None or body is not None:
                raise FormatError("EntryRef and alias/body cannot be given together")
            ref = name
        else:
            from flowing.runtime import AGENT_NAMING   # 局部 import 破环（agent ↔ runtime）
            item = {f"{name} as {alias}" if alias is not None else name: body or {}}
            ref = normalize_entries([item], naming=AGENT_NAMING)[0]
        cls = self.get_agent_class(ref.raw)   # 存在性解析（文件链命中则此刻编译/注册，文件覆盖 default::/builtin::；未命中抛 AgentTypeNotFoundError）
        # 落账 name_ori：文件派生类型记派生限定键（cls.registry_key，注册时回写）；
        # 注册表命中（default::/builtin::）保持裸名；registry_key 缺失（未注册手写类）回退 ref.raw
        name_ori = (
            cls.registry_key
            if cls.registry_key is not None
            and cls.registry_key not in (f"default::{ref.raw}", f"builtin::{ref.raw}")
            else ref.raw
        )
        key = ref.alias
        if key in self._subagent_entries:   # 同 alias 重复添加 = 笔误（绑定层统一 fail-fast）
            raise EntryNameConflictError(key, kind="subagent")
        # 第 1 步：body 判别（键集 system_prompt/description/args/visible——
        # 无 inject 键；未知键 -> FormatError；args 判别与 add_tool 同规则、
        # 无 output 分支；system_prompt/description 包装 Parsable，PENDING -> 空补丁 None）
        override_system_prompt, override_description = None, None
        override_params, specified, param_aliases = {}, {}, {}
        visible = True
        for body_key, body_val in ref.body.items():
            if body_key == "system_prompt":
                override_system_prompt = _as_parsable_patch(body_val)
            elif body_key == "description":
                override_description = _as_parsable_patch(body_val)
            elif body_key == "args":
                if not isinstance(body_val, Mapping):
                    raise FormatError(f"subagent override args must be a mapping: {body_val!r}")
                _classify_override_args(body_val, override_params, specified, param_aliases)
            elif body_key == "visible":
                visible = bool(body_val)
            else:
                raise FormatError(f"subagent override body contains unknown key: {body_key!r}")   # 含 Tool 面 output 键
        entry = SubagentEntry(
            name_alias=key,
            name_ori=name_ori,
            override_system_prompt=override_system_prompt,
            override_description=override_description,
            override_params=override_params,
            specified=specified,
            param_aliases=param_aliases,
            visible=visible,
        )
        self._subagent_entries[key] = entry
        return entry

    # ────────────────────────── provide / inject / 资源 ───────────────────

    def provide(self, key: str | InjectionKey[Any], value: Any) -> None:
        """在本节点注册 provide 值（写入 ``_provided``）。

        .. rubric:: 功能介绍

        ProvideNode 协议实现（Runtime / Workflow / Agent 同一套模式）。
        同名 key 覆盖写——实例运行期覆盖是合法的动态更新（``inject``
        实时查找不缓存，更新即刻可见）。

        .. rubric:: 使用示例

        .. code-block:: python

            async def setup(self, user_id: str):
                self.provide("user_id", user_id)   # 敏感信息走注入通道

        .. rubric:: 行为要点

        - 敏感信息（身份 / 工作区 / 凭证派生值）的正确通道：不进消息流、
          不进 LLM 上下文、不经网络传输、不落盘。
        - 不做深拷贝、不做序列化——存的是对象引用。

        .. seealso:: :meth:`inject`、:class:`flowing.params.InjectionKey`
        """
        self._provided[key] = value   # 同名覆盖写；存对象引用（不深拷贝、不序列化）

    @overload
    def inject(self, key: InjectionKey[T]) -> T: ...
    @overload
    def inject(self, key: str) -> Any: ...
    def inject(self, key: str | InjectionKey[T]) -> T:
        """沿 ``_parent_id`` 链上溯查找 provide 值（终点 = Runtime）。

        .. rubric:: 功能介绍

        统一算法见 :func:`flowing.provide.inject_from`：当前节点
        ``_provided`` → 亲节点 → … → ``Runtime._provided``；都找不到
        → ``MissingProvideError(key)``。

        .. rubric:: 行为要点

        - 上溯沿 UID 链（``runtime.get_node(parent_id)``），节点间不持
          对象引用；亲节点已销毁 / 摘除时链断 → 按找不到处理。
        - 跨层共享的正确机制（args 只向下传一层，inject 沿链自动穿透）。
        - 类型信息不跨节点：``InjectionKey[T]`` 的 ``T`` 是声明侧约定，
          框架不做运行时校验。
        - :raises flowing.errors.MissingProvideError: 链上溯到底仍无
          key。

        .. seealso:: :meth:`provide`、:func:`flowing.provide.inject_from`、
            :class:`flowing.params.InjectionKey` —— 类型安全键。
        """
        # 统一上溯算法 = flowing.provide.inject_from（canonical home；
        # 经 flowing.runtime 再导出亦有效）
        return inject_from(self.runtime, self, key)   # 链底仍无 -> MissingProvideError(key)

    @overload
    def get_resource(self, name: str) -> Any: ...
    @overload
    def get_resource(self, name: str, type_hint: type[T]) -> T: ...
    def get_resource(self, name: str, type_hint: type[T] | None = None) -> Any:
        """获取 Runtime 注册的资源（重资源惰性按需通道）。

        .. rubric:: 功能介绍

        委托 ``Runtime.get_resource``；``setup()`` 轻量约束下，重资源
        （渲染器 / 连接池 / 缓存）经此按需获取而非在声明层构造。资源是
        跨任务复用的对象（区别于 Agent 实例不跨任务复用）。

        .. rubric:: 使用示例

        .. code-block:: python

            async def setup(self):
                self._ui = self.get_resource("ui_renderer", UiRenderer)

        .. rubric:: 行为要点

        - :raises flowing.errors.ResourceNotFoundError: 名称未注册。

        .. seealso:: :meth:`flowing.runtime.Runtime.register_resource`
        """
        if type_hint is None:
            return self.runtime.get_resource(name)
        return self.runtime.get_resource(name, type_hint)   # 委托 Runtime（未注册 -> ResourceNotFoundError）

    # ────────────────────────── watch / parsable ──────────────────────────

    def watch(self, name: str, handler: WatchHandler | None = None) -> WatchHandler:
        """监听实例属性赋值事件——watcher 通道的 ``(new, old)`` 糖。

        .. rubric:: 功能介绍

        回调签名 ``(new_value, old_value) -> None``，返回值忽略；同步或
        async 均可。内部把回调包成 watcher handler ``(agent, fu)``，经
        ``self.hooks.watch(name, wrapped)`` 注册到 watcher 通道——它不是
        普通钩子点，不参与改写 / ``Intercepted`` / ``shortcut``，永远
        fire-and-forget。

        .. rubric:: 行为要点

        - ``name`` 按 ``fnmatch`` pattern 匹配赋值事件的字段名：字面量
          即精确匹配；通配符（``"*"`` 等）按 fnmatch 规则生效。
        - 纯观察 + fire-and-forget：watcher 在后台任务中执行，不阻塞
          赋值；多次赋值的 watcher 执行顺序不保证；无运行中 event loop
          时 watcher 不触发（赋值照常）。
        - ``handler=None`` 时返回装饰器（``@self.watch("x")`` 写法）；
          否则注册并原样返回 handler。
        - 监听的是赋值事件本身，不监听解析值变化：
          ``self.c = Parsable("{{ a == b }}")`` 后改 ``a`` 不触发
          ``watch("c")``；
          重新赋值（换 Parsable、赋非 Parsable 值、赋 ``_``）才触发。
        - 需要完整值对象时，直接经 ``self.hooks.watch`` 注册
          ``(agent, fu)`` 形态的 watcher。

        .. seealso::

            - :class:`FieldUpdate` —— watcher value。
            - :meth:`parsable` —— 惰性求值侧。
        """
        def _wrap(fn: WatchHandler) -> WatchHandler:
            async def _watcher(agent: Agent, fu: FieldUpdate) -> FieldUpdate:
                result = fn(fu.new, fu.old)   # 回调签名 (new, old)，返回值忽略
                if inspect.isawaitable(result):
                    await result
                return fu
            self.hooks.watch(name, _watcher)
            return fn
        if handler is None:
            return _wrap   # 装饰器写法：@self.watch("...")
        return _wrap(handler)

    def __setattr__(self, name: str, value: Any) -> None:
        """实例属性赋值拦截——watcher 通知的触发点。

        .. rubric:: 行为要点

        - 顺序：构造 :class:`FieldUpdate` （``name`` / ``old`` / ``new``
          快照）→ fire-and-forget 通知 watcher 通道（不 await）→ 执行
          写入。赋值语义不受 watcher 影响：无改写、无取消、异常不上抛。
        - 不再拦截状态键：经 ``agent.xxx = v`` 写已注册状态键落普通实例
          属性（与袋值并存由用户自担）；状态量读写统一走
          ``self.state.<key>`` 显式视图。
        - watcher 只管普通实例属性；state 写不触发（watch 不察觉 state）。
        - handler 可为同步或 async（后台任务统一 await）；handler 异常
          终止本次 dispatch 链并记录日志，不影响赋值。
        - 无运行中的 event loop 时 dispatch 静默跳过（赋值照常）。
        - 仅实例属性赋值触发；描述符 / 类属性 / ``_`` 前缀骨架字段的
          初始化不经过本机制。
        - 骨架期护栏：``hooks`` 未建立（管线预绑 / 子类先于
          ``super().__init__()`` 赋值）→ 跳过 dispatch，落普通实例属性。
        - 边缘情况：handler 内再次给同名字段赋值造成递归 dispatch——
          框架不做递归防护，属编程错误。
        - ``model_tag`` 赋值会触发重新解析并覆盖 ``self.model`` （只能
          指向配置已定义模型）；改标签触发的换模型不再二次触发 model
          字段的 watcher（一次语义事件 = 改标签）。

        .. seealso:: :class:`FieldUpdate`、:meth:`watch`
        """
        if not name.startswith("_"):   # 仅实例属性赋值触发；_ 前缀骨架字段初始化不经过本机制
            old: Any = getattr(self, name, None)   # 字段不存在时取 None（getattr 默认值）
            fu = FieldUpdate(name=name, old=old, new=value)   # 写入前构造快照
            # 护栏：hooks 未建立（管线第 2 步预绑 node_id/runtime
            # 早于 __init__）时只经 __dict__.get 探查，缺失即跳过——骨架期
            # 赋值本就不产生观测事件
            hooks = self.__dict__.get("hooks")
            if hooks is not None:
                hooks._notify_watch(self, fu)   # watcher 通道：fire-and-forget，不 await
        object.__setattr__(self, name, value)   # 赋值语义不受 handler 影响
        if name == "model_tag":
            # model_tag 可变路径一（类属性 docstring 规约）：赋值即重新解析
            # 覆盖 self.model，只能指向配置已定义模型；骨架期护栏——袋未建立
            # 或 runtime 未绑定时跳过（落普通实例属性）
            if self.__dict__.get("_state_bag") is not None and self.__dict__.get("runtime") is not None:
                # object.__setattr__ 直写 model：改标签触发的换模型不再
                # 二次触发 model 字段的 watcher（一次语义事件 = 改标签）
                object.__setattr__(self, "model", self._resolve_model_tag(value))

    def __delattr__(self, name: str) -> None:
        """删除拦截——纯透传。

        .. rubric:: 行为要点

        一律走普通实例属性删除（类属性 / 方法删不掉，``AttributeError``
        原样上抛）。不触发 watcher（删除不是赋值事件）。删持久值走
        ``del self.state.<key>``。
        """
        object.__delattr__(self, name)

    def parsable(self, source: Any) -> Parsable:
        """手动创建已绑定本实例的 :class:`flowing.parsable.Parsable`。

        .. rubric:: 功能介绍

        不走 ``__setattr__`` 拦截的手动通道：产物已绑定本实例，``str()``
        / ``resolve()`` 默认以本实例为渲染上下文（合并 ``env`` /
        ``config`` 顶层变量）。

        .. rubric:: 使用示例

        .. code-block:: python

            self.greeting = self.parsable("你好 {{ user_id }}")
            self.greeting.resolved      # "你好 Alice" —— 已绑定，现场求值

        .. rubric:: 行为要点

        框架不为 Parsable 做隐式解包——``_extra`` / entry 覆写等求值面外
        位置拿到的 Parsable 需手动 ``.resolve(context)``。

        .. seealso:: :class:`flowing.parsable.Parsable` （五形式与两步渲染）
        """
        p = Parsable(source)
        p._instance = self   # 绑定渲染上下文（内部字段，见 flowing.parsable 内部 API 清单）
        return p

    def snapshot(self, *, keys: set[str] | None = None) -> AgentSnapshot:
        """一致性只读快照：单个 Agent 状态的观测入口。

        .. rubric:: 功能介绍

        返回 :class:`flowing.snapshot.AgentSnapshot`——``node_id`` /
        ``parent_id`` / 消息树摘要 / ``current_head_id`` / 当前逻辑 Turn
        视图 / ``executions`` / 队列摘要 / 模型视图 / 工具与子 Agent 绑定
        条目 / 上下文占用估计（``context_usage``，:meth:`estimate_context_tokens`
        的快照时刻取值）的一次性只读视图。供测试断言、repl ``/snapshot``、
        观测扩展使用。

        .. rubric:: 使用示例

        .. code-block:: python

            snap = agent.snapshot()
            assert snap.current_turn is None
            assert snap.message_queue.size == 0

        .. rubric:: 行为要点

        - 只读：修改返回对象不影响 Agent；字段为拷贝或 Info 视图。
        - 一致性：单次调用内各字段取同一时刻的读值。
        - ``keys``：``None`` （默认）收集全部切面；指定时只收集指定字段
          （其余为 ``None``——如不需要 ``context_usage`` 的锚点扫描开销，
          可不收它）。需要多切面同一时刻一致 → 同一次调用传入全部所需
          key。
        - 可序列化：全部字段 JSON 可序列化。
        - ``current_turn`` 为 ``None`` 表示空闲（无活跃逻辑 Turn）。
        - 完整字段契约见 :mod:`flowing.snapshot` 模块级 docstring。

        :param keys: 要收集的切面字段名集合；``None`` 表示全部。

        .. seealso:: :meth:`flowing.runtime.Runtime.snapshot`、
            :class:`flowing.snapshot.AgentSnapshot`
        """
        def _want(key: str) -> bool:
            return keys is None or key in keys   # S3：None 收集全部切面；指定时其余为 None

        model = self.__dict__.get("model")
        model_info: ModelInfo | None = None
        if _want("model") and model is not None:
            # 现场求值投影（ModelConfig.resolve 语义字段级求值）；任何字段
            # 求值失败 -> 该字段以 None 投影，快照整体不因此抛异常
            def _field(value: Any) -> Any:
                try:
                    return value.resolve(self) if isinstance(value, Parsable) else value
                except Exception:
                    return None
            model_info = ModelInfo(
                model=_field(model.model), provider=_field(model.provider),
                model_tag=_field(getattr(self, "model_tag", None)),
                context_window=_field(model.context_window),
                max_output_tokens=_field(model.max_output_tokens),
                thinking_budget=_field(model.thinking_budget))
        turn = self.current_turn
        return AgentSnapshot(
            node_id=self.node_id,
            parent_id=self._parent_id,
            agent_type=self.runtime._agent_pool[self.node_id]["agent_type"],   # 池元数据中的类型名字符串（恢复依据；Agent 已无 name 机制字段，契约见 flowing.snapshot）
            paused=self.paused,
            messages=(MessageTreeInfo(count=len(self._messages), head_id=self.current_head_id)
                      if _want("messages") else None),
            current_head_id=self.current_head_id,
            current_turn=(TurnContextInfo(
                started_at=turn.started_at, finished_at=turn.finished_at,
                message_count=len(turn.message_ids), aborted=turn.aborted)
                if _want("current_turn") and turn is not None else None),
            executions=({eid: ExecutionInfo(kind=e.kind, tags=list(e.tags),
                                           started_at=e.started_at)
                         for eid, e in self._executions.items()}
                        if _want("executions") else None),   # 不含 cancel/pause Event
            message_queue=(MessageQueueInfo(size=len(self._message_queue),
                                            pending=len(self._pending_turns))
                           if _want("message_queue") else None),
            model=model_info,
            tool_entries=([EntryInfo(alias=e.name_alias, visible=e.visible,
                                     agent_type=None)
                           for e in self._tool_entries.values()]
                          if _want("tool_entries") else None),
            subagent_entries=([EntryInfo(alias=e.name_alias, visible=e.visible,
                                        agent_type=e.name_ori)
                               for e in self._subagent_entries.values()]
                              if _want("subagent_entries") else None),
            context_usage=(self.estimate_context_tokens()
                           if _want("context_usage") and model is not None else None),   # 上下文占用估计投影（锚点实测+尾部估算，纯观测）；模型未解析为 None
        )   # 单次调用内各字段取同一时刻读值

    # ────────────────────────── 工作循环与逻辑 Turn（内部） ─────────────────

    async def _work_loop(self) -> None:
        """常驻消息工作循环（每个 Agent 一个 Task；内部 API）。

        循环：等待工作循环 gate → 出队（:meth:`_dequeue`，可覆写）→ 把
        出队消息的等待者从 ``_pending_turns`` 摘出 → 执行一个逻辑回合
        （:meth:`_run_turn`）。回合异常记录日志后继续循环（waiters 已由
        回合收尾喂饱）。启动时机：``after_create`` / ``after_recover``
        完成即启动；``destroy()`` 时取消。串行：同一时刻一个逻辑回合，
        活跃回合中入队的消息自然排队。
        """
        while True:
            await self._pause_gate.wait()      # 检查点 ①：dequeue 前（仅 pause）
            msgs = await self._dequeue()       # list[Message]（可覆写 drain/合并策略）
            waiters = [self._pending_turns.pop(m.id, None) for m in msgs]
            try:
                await self._run_turn(msgs, waiters)
            except Exception:
                # 回合级异常的兜底闸（“钩子抛异常不再楔死 agent”的收口点）：
                # _run_turn 的 except 帧保留异常上抛以保逐层审计，工作循环在
                # 此记录后继续消费——waiters 已由 _run_turn 的 finally 喂饱
                # （destroy 的第 1 步另有兜底），此处只保证循环存活
                _logger.exception("agent %s: turn crashed", self.node_id)

    async def _dequeue(self) -> list[Message]:
        """出队扩展点：构造一个逻辑回合的消费批次，可覆写实现 drain /
        合并策略（内部 API）。

        默认批次语义：阻塞到队列非空 → 取队首 INTERRUPT/STEER 连续段
        （``take_while(priority <= STEER)``）→ 再取其后的第一条非紧急
        消息（若有；队首即非紧急时批次为单条）→ dispatch ``on_dequeue``
        （可变换返回的消息列表）。已出队但未进入最终批次的消息不塞回
        队列——其 ``_pending_turns`` 等待者联动 resolve cancelled
        （同 :meth:`cancel_queued` 填充规则）；``on_dequeue`` 把批次
        变换为空 = 丢弃本批，工作循环重新等待下一批。

        覆写管“多条 / 策略”（``drain_all()`` 合并、批量、按来源分组），
        钩子管“观察 / 变换”——分工不混。批次内各消息的等待者随回合
        收尾共享同一 ``TurnResult`` 并全部 resolve。
        """
        while True:   # on_dequeue 空批（丢弃本批）→ 重新等待
            await self._message_queue.wait_not_empty()   # 阻塞到非空
            taken = self._message_queue.take_while(   # 队首 INTERRUPT/STEER 连续段
                lambda m: m.priority <= MessagePriority.STEER)
            nxt = self._message_queue.dequeue_nowait()   # 其后第一条非紧急消息（若有）
            if nxt is not None:
                taken.append(nxt)
            if not taken:
                continue   # 防御：wait_not_empty 与取批之间无 await 窗口，理论不可达
            msgs = await self.hooks.on_dequeue.dispatch(self, taken)   # 可变换批次
            consumed = {m.id for m in msgs}
            for dropped in taken:   # 已出队但未进入最终批次：联动 resolve cancelled，不塞回队列
                if dropped.id not in consumed:
                    self._pop_pending_cancelled(dropped.id)
            if msgs:
                return msgs

    async def _run_turn(
        self,
        msgs: list[Message],
        waiters: list[asyncio.Future[TurnResult] | None],
    ) -> None:
        """逻辑 Turn 执行主体（内部 API；消息级完整时序）。

        时序（顺序为不变量）：创建 ``TurnContext`` → 把
        ``current_turn`` 置为本回合载体 → 新建回合退出信号 →
        ``turn.pending_messages = msgs`` （出队
        批次暂存，未挂树）→ dispatch ``before_turn`` （可改写
        ``pending_messages``——追加为附加式注入；``raise Intercepted``
        硬阻断则批次全部丢弃、不落盘）→ 批次逐条经 :meth:`_append_message`
        挂树 → 内层循环：检查点（pause 在前、abort 判定在后）→ urgent
        吸收（``INTERRUPT`` 在队首则连 drain ``INTERRUPT`` + ``STEER``
        两带挂树并 ``abort_turn()``；仅 ``STEER`` 在队首则只 drain STEER
        挂树、不 abort，当轮 context 可见）→ ``_assemble_context`` →
        ``provider_gen`` （异常 → 构造 ``ProviderErrorContext`` → dispatch
        ``on_provider_error`` → ``can_continue=False`` 则 break，``True``
        则 continue）→ 响应
        消息挂树 → 工具调用循环（同一响应内全部 ``tool_call`` 并行执行，
        批次前同一 pause/abort 检查点）→ ``response.finish`` 或
        ``finish_output`` 置位则 break。三处 abort 判定互斥（各自随即
        break）且 ``turn.aborted`` 幂等置位，``on_turn_abort`` 每回合
        至多触发一次。

        ``finally`` （所有路径）：``current_turn = None`` （释放回合身份牌
        先于一切钩子）→ dispatch ``after_turn`` （所有路径唯一收尾观察点；
        handler 读 ``turn.aborted`` 分流）→ ``build_turn_result`` 组装 →
        写 ``self.last_result`` → resolve 全部 waiters（共享同一
        ``TurnResult``）。``after_turn`` handler 异常不中断 waiters 交付
        ——捕获后先喂饱再原样上抛；异常路径交付的 ``result.turn`` 为合成
        空载体（不暴露真实 turn）。
        """
        # 1. 创建 TurnContext
        turn = TurnContext(started_at=datetime.now(), message_ids=[])
        self.current_turn = turn   # 标记回合物质存活
        self._turn_abort = asyncio.Event()   # 每个逻辑 Turn 独立新建
        turn.pending_messages = msgs   # 出队批次暂存（未挂树）
        intercepted = False                    # 结局信号由 except 帧显式
        error: BaseException | None = None     # 传入 build_turn_result，不落 TurnContext
        last_stop_reason = ""   # 末次 provider_gen 响应的原始停止原因（completed 结局的 finish_reason 来源）
        try:
            # 2. before_turn（附加式注入 / Intercepted 阻断）；
            # 异常路径：Intercepted -> pending_messages 全部丢弃不落盘（显式
            # 丢失语义），TurnResult status="blocked"（Intercepted 未在本模块具名引入）
            turn = await self.hooks.before_turn.dispatch(self, turn)
            # 3. 批次逐条挂树（各条照常触发 on_turn_append）
            for m in turn.pending_messages:
                await self._append_message(m, turn)
            turn.pending_messages.clear()   # 挂树批次完成后清空
            # 4. 内层循环
            while True:
                # 检查点 ②（provider_gen 前）：pause 在前、abort 在后
                await self._pause_gate.wait()
                # 检查点 ②.5：urgent 吸收（interrupt / steer）
                if self._message_queue.peek(MessagePriority.INTERRUPT) is not None:
                    # interrupt：连 drain INTERRUPT+STEER（队首连续段），挂树后 abort
                    for m in self._message_queue.take_while(
                            lambda m: m.priority <= MessagePriority.STEER):
                        waiters.append(self._pending_turns.pop(m.id, None))   # 等待者并入本回合
                        await self._append_message(m, turn)
                    self.abort_turn()   # 置 Event，由紧随的 abort 判定统一收口
                elif self._message_queue.peek(MessagePriority.STEER) is not None:
                    # steer：仅 drain STEER，挂树不 abort——当轮 provider_gen 的 context 即可见
                    for m in self._message_queue.take_while(
                            lambda m: m.priority == MessagePriority.STEER):
                        waiters.append(self._pending_turns.pop(m.id, None))
                        await self._append_message(m, turn)
                if self._turn_abort.is_set():
                    turn.aborted = True   # 幂等置位（语义即此布尔赋值）
                    await self.hooks.on_turn_abort.dispatch(self, turn)   # abort 判定收口处统一触发（每回合至多一次）
                    break
                context = self._assemble_context()
                try:
                    response = await self.provider_gen(context, by="_turn")   # 主 Turn 来源标记
                except Exception as exc:
                    qctx = ProviderErrorContext(error=exc, provider=self.model.provider,
                                             model=self.model)
                    qctx = await self.hooks.on_provider_error.dispatch(self, qctx)
                    if not qctx.can_continue:
                        error = exc   # 回合中断（Agent 存活）——中断原因即本异常，结局 "error"
                        _logger.warning(
                            "agent %s: turn ended with error after on_provider_error "
                            "(no handler continued): %s: %s",
                            self.node_id, type(exc).__name__, exc)
                        break
                    continue    # handler 已完成退避/换模型/abort_turn()
                if response.message is not None:
                    if response.message.usage is not None:
                        # turn 级用量累加（追加消息上同一 Usage 对象的
                        # 引用，收尾由 build_turn_result 聚合；ProviderResponse
                        # 不携带 usage，消息是唯一载体）
                        turn.usages.append(response.message.usage)
                    # turn_end 由 agent 层写入——turn 随本条消息关闭
                    #（自然 finish 或取消/abort）→ True；provider 的 finish
                    # 只是关闭原因之一，adapter 不写 turn_end；finish 工具
                    # 置位路径的 turn_end 落在其配对 TOOL 消息上（见下
                    # “置位转移检测”），本条 PROVIDER 消息不追溯改写
                    #（已挂树落盘）
                    response.message.turn_end = response.finish or response.cancelled
                    await self._append_message(response.message, turn)
                last_stop_reason = response.provider_data.get("stop_reason", "")   # completed 结局的 finish_reason 来源
                if response.cancelled or self._turn_abort.is_set():
                    turn.aborted = True   # cancelled 也走 abort 路径
                    await self.hooks.on_turn_abort.dispatch(self, turn)
                    break
                # 工具调用循环（并行版）：同一响应中的全部 tool_call 并行执行
                tool_blocks = [
                    b for b in (response.message.content if response.message is not None else [])
                    if isinstance(b, ToolCallBlock)
                ]
                if tool_blocks:
                    # 检查点 ③：并行批次前 pause 在前、abort 在后
                    await self._pause_gate.wait()
                    if self._turn_abort.is_set():
                        turn.aborted = True
                        await self.hooks.on_turn_abort.dispatch(self, turn)
                        # 跳过本批全部工具；已执行工具（无）结果仍按不执行处理
                    else:
                        block_finish_was = turn.finish_output   # 置位转移检测：并行块开始前

                        async def _run_one(tc: ToolCall):
                            before = turn.finish_output
                            result = await self.tool_call(tc)
                            after = turn.finish_output
                            return tc, result, before, after

                        # 结果返回即挂树（实时完成序）：崩溃只丢真正在飞的
                        # 结果——“整批完成后按响应块序统一挂树”改为
                        # as_completed 边返回边挂；finish 置位检测的“首个
                        # 触发置位”相应为实时首个（谁先交卷谁标 turn_end）；
                        # 执行中的异常仍在全部结果落树后再上抛
                        errors: list[BaseException] = []
                        marked = False
                        futures = [_run_one(tc) for tc in
                                   (ToolCall.from_block(b) for b in tool_blocks)]
                        for fut in asyncio.as_completed(futures):
                            try:
                                tc, result, before, after = await fut
                            except BaseException as exc:
                                errors.append(exc)
                                continue
                            result_msg = result.as_message(tc.id)   # 配对锚接线（tc.id → tool_call_id）
                            if (block_finish_was is None and not marked
                                    and before is None and after is not None):
                                result_msg.turn_end = True   # finish 置位路径：首个触发置位的 TOOL 消息标 turn_end（实时首个）
                                marked = True
                            await self._append_message(result_msg, turn)   # 结果消息挂树（即完成即挂）
                        if errors:
                            raise errors[0]   # 框架错误：已成功的结果已挂树，异常继续走 _run_turn 的 except 通道
                if turn.aborted:
                    break
                if response.finish or turn.finish_output is not None:
                    break   # 自然结束（含 finish 置位：工具段照常执行完再收尾）
        except Intercepted:   # 拦截走异常通道（捕获结局信号）
            intercepted = True
            raise   # 异常继续上抛（waiters 已由 finally 的 set_result 喂饱）
        except Exception as exc:
            error = exc
            raise   # 同上
        except asyncio.CancelledError:
            # 工作循环 Task 被取消（destroy 第 2 步）：回合按取消结局收尾——
            # 置位 aborted 使 finally 的 build_turn_result 产出 cancelled，
            # 在途 waiters 不挂起、不谎报 completed
            turn.aborted = True
            raise
        finally:
            # 5a. 释放回合身份牌（先于一切钩子）。head 随每条消息挂树即时
            # 前移（空 turn 无 append 自然不动），回合末无结算写入；
            # 钩子抛异常只影响交付段，不再楔死 agent
            self.current_turn = None   # 回合物质终结
            # 配对封闭恒做（无孤儿则空转）：abort / error 结局都可能留下未配对
            # 调用（批跳、批次中途框架异常、destroy）——树内永远成对
            self._close_orphan_tool_calls(turn)

            def _finish(expose_turn: bool = True) -> None:
                # 5c. 交付（独立小函数：正常路径与 after_turn 异常路径共用，
                # 保证 waiters 永不因收尾钩子异常而挂起——已知边界修复）
                turn.finished_at = datetime.now()
                result = build_turn_result(turn, self, intercepted=intercepted,
                                           error=error,
                                           finish_reason=last_stop_reason)   # 信号显式传参（completed 结局的 stop_reason 同通道）
                if not expose_turn:
                    # 异常路径（after_turn 钩子崩）：不暴露真实 turn 对象——
                    # 收尾期间崩过，调用方不应拿到可回溯树/读钩子状态的执行期
                    # 载体；替换为合成空载体（保留真实 started_at、message_ids
                    # 清空——与 destroy 兜底同口径）。组装结果（final_text /
                    # status / token_usage）不受影响——只换 turn 字段。
                    result.turn = TurnContext(
                        started_at=turn.started_at, message_ids=[])
                # last_result 统一写入（在 resolve waiters 之前）：finish 置位 →
                # finish_output dict；否则 final_text（无产出 → None）。abort /
                # cancel / error 同样覆写——载荷已置位照返；有完整 PROVIDER
                # 消息取其文本；皆无 → None（取消/异常信息走
                # SubagentResult.subagent_status，不进内容位）
                self.last_result = (turn.finish_output
                                    if turn.finish_output is not None
                                    else result.final_text or None)
                for fut in waiters:
                    if fut is not None and not fut.done():
                        fut.set_result(result)   # drain 合并共享同一 TurnResult

            # 5b. 观察钩子：turn 对象作为 value 照常传入（身份释放 ≠ 产物消失；
            # handler 契约 (agent, value) 不受影响；此期间 agent.current_turn
            # 已为 None，绕开 value 读它的写法会看到空闲）
            # 已知边界修复：after_turn handler 异常不得
            # 中断 waiters 交付——捕获后先喂饱再**原样上抛**（except 块内裸
            # raise 保留 handler 内层 traceback；普通异常落 _work_loop 的
            # turn crashed 日志，CancelledError 照常传播）；异常路径交付
            # 不暴露真实 turn（expose_turn=False，合成空载体）
            try:
                await self.hooks.after_turn.dispatch(self, turn)   # 所有路径唯一收尾观察点；handler 读 turn.aborted 分流
            except BaseException:
                _finish(expose_turn=False)
                raise
            _finish()

    async def _append_message(self, msg: Message, turn: TurnContext) -> None:
        """消息挂树 + 落盘统一入口（内部 API）。

        时序：dispatch ``on_turn_append`` （可改写 / ``Intercepted``）
        → :meth:`push` （设 ``parent_id = current_head_id`` → 挂入
        ``_messages`` → 落盘 → head 前移）→ ``turn.message_ids.append``。
        消息完整后才经过本方法——流式进行中的增量不经过它，天然
        不落盘；流式被中断时，已累积内容定型为一条 ``partial=True`` 的
        完整消息，照常挂树落盘。副线（``side_query``）消息不经过本方法。
        """
        msg = await self.hooks.on_turn_append.dispatch(self, msg)   # 可改写 / Intercepted
        self.push(msg)                             # 核心写路径：parent_id → _messages → 落盘 → head 前移
        turn.message_ids.append(msg.id)            # 记 turn 索引

    def _persist_message(self, msg: Message) -> None:
        """提交一条完整消息落盘（内部 API；write-behind：同步排队即返）。

        序列化 ``msg`` 为消息行（含 ``id`` / ``parent_id`` / ``turn_end`` /
        ``partial`` 等）→ ``self._tree_store.submit(行)``——同步返回不代表
        已落盘（产生即排队）。墓碑压缩由 ``FileRecordStore`` drain 任务在
        队列排空后自主触发。store 已进入 poison 态（写入过程中首次
        落盘失败后进入的状态，此后每次提交同步重抛同一异常）时本方法
        同步重抛首次落盘异常（错误在挂树现场爆出，而非静默分叉）。副线
        消息不经过本方法。
        """
        # 序列化 msg 为消息行 dict -> self._tree_store.submit(行)
        # （同步排队即返；poison 态时 submit 重抛首次落盘异常）
        # X2 冻结点：to_record 是消息 ↔ 行的唯一序列化点（flowing.message）
        self._tree_store.submit(to_record(msg))

    def _persist_tree_record(self, record: dict) -> None:
        """提交一条变更记录行落盘（内部 API；tombstone / update / move）。

        :class:`flowing.message.MessageChain` 五 op 的落盘通道：与
        :meth:`_persist_message` 共用同一 ``_tree_store`` 队列——消息行与
        变更行在同一 FIFO 中按提交序落盘，重放时按行序应用。``record``
        约定字段：``{"type": "tombstone", "id": ...}`` （tombstone 即
        删除标记行，重放时移除对应消息）/
        ``{"type": "update", "id": ..., "content": [...]}`` /
        ``{"type": "move", "id": ..., "parent_id": ...}``；``insert`` 的
        邻接调整对被重挂的每个子消息各提交一条 ``move`` 行。排队语义 /
        poison 重抛与 :meth:`_persist_message` 相同。不校验 record 语义
        （哪条消息该删、环检测等是 ``MessageChain`` 的职责）；不改变内存
        权威（op 已先改内存）。
        """
        self._tree_store.submit(record)   # 与消息行同一 FIFO，按提交序落盘

    def _render_subagent_catalog(self) -> str:
        """渲染 ``<available_subagents>`` catalog 块（内部 API）。

        对每个 ``visible=True`` 的子 Agent 绑定条目调
        :meth:`flowing.subagents.SubagentEntry.catalog_view` 在 Python 侧
        预计算视图 dict，再经模板一次性渲染（模板只负责排布）。模板取
        模板取 ``subagent_catalog_template`` 属性（缺失时回退
        ``DEFAULT_SUBAGENT_CATALOG_TEMPLATE``）。无 ``visible=True``
        条目 →
        返回 ``""`` （整块不注入）。渲染经 Parsable TEMPLATE 语义（include
        基准为本 Agent 的 ``source_dir``）；渲染异常 fail-fast 上抛，不
        静默降级。每次调用现场渲染，无缓存。
        """
        views: list[dict[str, Any]] = [
            entry.catalog_view(self)   # Python 侧预计算视图（description/params_xml 已解析）
            for entry in self._subagent_entries.values()
            if entry.visible   # visible=False 不进 catalog（调用方负责过滤）
        ]
        if not views:
            return ""   # 空列表渲染为 ""（整块不注入）
        template = (
            getattr(self, "subagent_catalog_template", None)   # Agent 级覆写槽位（类属性 / fya 同名字段落入实例属性或 _extra，getattr 同样命中）
            or DEFAULT_SUBAGENT_CATALOG_TEMPLATE
        )
        return self.parsable(template).resolve({"entries": views, "agent": self})   # Parsable TEMPLATE 语义，include 基准 source_dir；渲染异常 fail-fast 上抛

    def _assemble_context(self) -> Context:
        """组装 ``Context`` （内部 API；每次调用现场求值，无缓存）。

        四部分（``Context`` 不是扁平消息列表）：

        1. 遍历 ``prompt_blocks`` （跳过 ``enabled=False``）逐块
           ``resolve()`` → ``list[PromptSegment]`` （保留 cache 标记——
           仅是 adapter 意图标记，框架本地不缓存）；``prompt_blocks[0]``
           的 ``{{ self.system_prompt }}`` 惰性引用在此触发解析。
        2. 消息路径：从 ``current_head_id`` 沿 ``parent_id`` 上溯到根，
           反转得根 → head 的消息序列；半截 turn 的已落盘消息照常包含。
           树内永远成对（执行期 cancelled 封闭 / 恢复期 synthetic 落盘
           封闭）：装配对配对只做断言、不做读时修补——发现孤立调用即
           ``UnpairedToolCallError``（孤儿的合法来源只剩显式树操作：
           ``chain.remove`` 或 fork 切分未闭合链段——调用方负责封闭）。
        3. ``_visible_tools()`` → ``list[ToolDefinition]`` （仅
           ``visible=True`` 条目）。无隐式附加——``subagent-invoke`` /
           ``finish`` 等内置工具必须由用户显式声明才进入可见面。
        4. 子智能体 catalog：``<available_subagents>`` 块经
           ``_render_subagent_catalog()`` 现场渲染并入 ``system_prompt``
           段（``cache="dynamic"``）。

        现场求值是功能正确性前提（环境变量、实例属性、模式状态永远最新），
        不是性能优化。同步方法：渲染为同步 Jinja2 求值；
        ``before_provider_gen`` 钩子在 ``provider_gen()`` 内对本产物仍可
        改写（但不推荐直接改写 ``messages``——内容增删走持久化路径）。
        """
        # 1. 遍历 prompt_blocks（__iter__ 跳过 enabled=False）逐块现场求值
        # 分段装配（“未见具名符号”落实）：PromptSegment(content=求值文本,
        # cache/name 从来源块原样透传)
        segments: list[PromptSegment] = []
        for block in self.prompt_blocks:
            segments.append(PromptSegment(
                content=str(block.content.resolve(self)),   # prompt_blocks[0] 的 {{ self.system_prompt }} 惰性引用在此触发
                cache=block.cache, name=block.name))
        # 2. 消息路径：从 current_head_id 沿 parent_id 上溯到根（chain.walk，
        # 孤儿链断点容忍——中间消息被 chain.remove 且子树未先 reparent 时
        # 上溯到断点即终止），反转得根 -> head
        messages = list(self.chain.walk(self.current_head_id))[::-1]
        # 配对断言（树内永远成对：执行期 cancelled 封闭 / 恢复期 synthetic
        # 落盘封闭之外的未配对形态只剩显式手术——chain.remove 删掉了调用或
        # 结果消息——此处响亮报错，不做读时修补）：tool_call 与其结果严格
        # 1:1，孤立即 UnpairedToolCallError
        answered = {m.tool_call_id for m in messages if m.kind is MessageKind.TOOL}
        for m in messages:
            if m.kind is not MessageKind.PROVIDER:
                continue
            for b in m.content:
                if isinstance(b, ToolCallBlock) and b.id not in answered:
                    raise UnpairedToolCallError(
                        f"unpaired tool call in message tree: {b.id}"
                        f" (its result message is missing — closed pair is a tree invariant;"
                        " remove() of a call/result message reopens pairing and must be re-closed by the caller)")
        # （半截 turn 已落盘消息照常包含——树内永远成对，见上方配对断言）
        tools = self._visible_tools()
        # 无隐式附加——subagent-invoke / finish 等内置工具
        # 需用户经 tools: / add_tool 显式声明才进入可见面
        # <available_subagents> 块经 _render_subagent_catalog() 现场渲染，
        # 并入 system_prompt 段（cache="dynamic" 语义，无本地缓存）
        subagent_catalog = self._render_subagent_catalog()
        if subagent_catalog:
            # 并入 system_prompt 段（段名“subagent-catalog”为落实命名，
            # 规约未具名）；cache="dynamic"（visible 状态与覆写运行时可变）
            segments.append(PromptSegment(
                content=subagent_catalog, cache="dynamic", name="subagent-catalog"))
        return Context(system_prompt=segments, tools=tools, messages=messages)

    def _visible_tools(self) -> list[ToolDefinition]:
        """当前 ``visible=True`` 工具条目经 ``llm_definition()`` 的定义
        列表（内部 API）。

        每次现场生成，无缓存；``visible`` 运行时可变（模式切换），故不可
        缓存。子 Agent 不走本方法——其 LLM 可见声明是 catalog XML（见
        :meth:`flowing.subagents.SubagentEntry.catalog_view`）。
        """
        defs: list[ToolDefinition] = []
        for entry in self._tool_entries.values():
            if entry.visible:
                defs.append(entry.llm_definition(self.runtime, self))   # 每次现场生成，无缓存
        return defs
