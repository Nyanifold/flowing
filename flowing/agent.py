"""flowing.agent —— Agent 对象模型、逻辑 Turn 执行与异步执行管理（最终 API 规约）。

.. rubric:: 功能介绍

框架核心层。本模块承载 Flowing 的中心对象 :class:`Agent`（智能体基类，
``.fya`` 声明式与手写 Python 子类生成的**完全相同的类模型**），以及围绕它的
逻辑 Turn 执行载体（:class:`TurnContext`）、回合产物（:class:`TurnResult`）、
异步执行追踪条目（:class:`Execution`）、钩子上下文（:class:`ProviderErrorContext` /
:class:`CancelContext`）、持久化状态袋视图（:class:`flowing.persistence.StateView`，声明经 ``Agent.register_state``、
读写统一经 ``Agent.state`` 显式视图——P3-03 配套裁决；机制层——
``FileRecordStore`` write-behind 落盘——在 :mod:`flowing.persistence`）与收尾组装函数
:func:`build_turn_result`。子 Agent 绑定的三正交条目与唤起/结果结构
（:class:`flowing.subagents.SubagentEntry` /
:class:`flowing.subagents.SubagentInvocation` /
:class:`flowing.subagents.SubagentResult`）已拆至
:mod:`flowing.subagents`——「Agent 核心逻辑」与「子智能体管理」分文件，
本模块只 import 使用。

.. rubric:: 设计动机

- **消息级树 + 逻辑 Turn**（文档 14 落锤）：树节点 = 消息
  （``Message.id`` + ``Message.parent_id`` 链），``current_head_id`` 指向
  **消息 id**；Turn 只是「消费消息 → ``finish=True``」的逻辑执行阶段，执行期
  载体是 :class:`TurnContext`（不落盘、不进树、崩溃后不恢复）。goal 模式
  （一条指令连续工作数天）证明 Turn 粒度物理树的代价不可接受。
- **机制 vs 策略**：核心只做错误分类（``on_provider_error`` 分发）、钩子点、
  消息流转；重试（``use_retry``）、压缩、审批策略全在扩展 / Composable /
  应用层。
- **三大正交维度**（不可合并）：生命周期子树 ``_children``（谁随我销毁）、
  执行追踪 ``_executions``（谁在运行、谁可取消）、Provide 链 ``_parent_id``
  上行（``inject`` 沿谁上溯）。同一子 Agent 实例同时出现在三个结构中，
  语义各自独立。
- **能力三正交**（Tool 模式推广到子 Agent / Skill）：可执行对象（Agent 类）、
  LLM 可见声明（catalog XML 条目）、Agent 级绑定（:class:`SubagentEntry`）。
  绑定层存在的理由：同一 Agent 类型在不同父 Agent 上应有不同的 LLM 可见
  描述与参数默认值——覆写发生在 entry 层，不改全局类。

.. rubric:: 模块级行为规约（本模块必须整体满足的时序与不变量）

**async 约定（最终裁决）**：钩子 ``dispatch`` 是异步的（handler 可以是
``async`` 可等待对象，见 :mod:`flowing.hooks`），因此**一切在内部 dispatch
钩子的公开方法一律为协程**：``enqueue_message`` / ``enqueue_messages`` /
``fork`` / ``cancel`` / ``stop`` / ``destroy`` / ``invoke_subagent`` /
``message`` / ``steer`` 等。
设计草稿中这些方法的同步写法一律视为速记。不触发钩子的方法保持同步：
``pause`` / ``resume`` / ``pause_recursive`` / ``resume_recursive`` /
``paused`` / ``abort_turn`` / ``cancel_queued`` /
``provide`` / ``inject`` / ``watch`` / ``parsable`` / ``get_resource`` /
``register_state`` / ``add_tool`` / ``snapshot`` / ``estimate_context_tokens`` /
``set_queued_priority`` / ``cancel_children`` / ``cancel_by_tag`` /
``stop_children`` / ``stop_by_tag``。
``__init__`` 必须同步（创建管线不可 await 骨架阶段）。

**无生命周期状态机**（M-77 最终裁决）：Agent 不提供 ``status`` 字段。
「在干什么」的观测由快照层从内部状态现场派生（``current_turn``
是否在 → 回合执行中；取消信号是否已置位；``destroy()`` 是否完成），
各观测方不共享任何预定义状态值——防止投影被误当作控制结构加码。

**创建 / 恢复管线**（宿主是 ``Runtime.create_agent`` / ``Runtime.recover_agent``，
本模块规约 Agent 侧职责）：``__init__``（同步骨架，**含** ``_open_stores``
建立持久化后端与 ``_extra``，P3-03 裁决）→ ``before_create``（可改写
kwargs）→ ``setup(**kwargs)`` → **PENDING 检查** → 池元数据 +
初始 state 写盘 → ``_nodes`` 注册 → ``after_create`` → 工作循环 Task 启动。
恢复管线对称：``before_recover``（可改写 args）→ ``setup(**args)``（args 为
持久化值，可被 ``override_args`` 覆盖；触发的是 before/after_recover 钩子对）
→ PENDING 检查 → ``instance._restore()``（消息级重建 +
状态重放，完成后解开 :class:`flowing.persistence.StateView` 写闸门）→ ``_nodes``
注册 → ``after_recover`` → 工作循环启动；``node_id = agent_id``（身份连续、
可重现）。两条管线在**各自的实例**上各跑一次 ``setup()``（recover 是新建
实例，非同一实例重复执行），故 ``setup()`` 必须可重入（见
:meth:`setup`）。不变量：**创建即注册**；任意 Agent 诞生（``after_create`` /
``after_recover`` 完成）即启动常驻工作循环 Task，``destroy()`` 时取消。

**持久化状态的职责三方分立**（最终裁决）：**机制归 Agent**——每 agent 一个
session 目录（``agent_id`` 命名，父子平级），内含 ``tree.jsonl`` /
``state.jsonl`` 两文件，物理读写由 Agent 持有的
:class:`flowing.persistence.FileRecordStore` 实例执行（write-behind：提交
同步排队、drain 任务串行落盘；三条契约与末行合并 / 墓碑
压缩策略见 :mod:`flowing.persistence` 模块规约），不经 Runtime 中转；
**声明归 setup**——``register_state(key, default)``
逐键声明（**一个 Agent 一袋**，无命名空间；插件键带注册名 underscore
前缀约定，框架核心键裸名），setup 中**禁写** state（写闸门在管线到位前
锁定，初始值走 default）；**内容归插件/用户代码**——钩子 handler 里经
``agent.state`` 视图写透（读写统一通道，写透后触发 watcher 通道）。
Runtime 只保留全局视野职能：``set_persist_dir``、池扫描
（目录 → 池 key）、管线编排；Runtime 自身的全局状态保留命名空间
（``Runtime.state(ns)``）。

**逻辑 Turn 时序**（``_run_turn``，消息级）：创建 ``TurnContext`` →
``pending_messages`` 暂存出队批次 → ``before_turn``（可附加式注入 /
``Intercepted`` 阻断，阻断或窗口内崩溃 = 批次丢弃不落盘）→ 批次逐条经
``_append_message`` 挂树 → 内循环（检查点
``_pause_gate.wait()`` 在前、urgent 吸收居中、``_turn_abort`` 判定在后——
peek 到 ``INTERRUPT`` 则连 drain ``INTERRUPT``+``STEER`` 两带挂树并
``abort_turn()``；仅 ``STEER`` 在队首则只 drain STEER 这一优先级带的
消息挂树，不置位 abort，当轮组装的 context 即可见；abort 判定收口处
统一 dispatch ``before_turn_abort``，每回合至多
一次 → ``_assemble_context``
→ ``provider_gen`` → ``_append_message(response.message)`` → 工具调用循环（并行版：
同一响应内全部 tool_call 前同一个 pause/abort 检查点，随后 ``asyncio.gather``
并行执行）→ ``finally`` 收尾（
**释放回合身份牌先于一切钩子**：``current_turn = None``（head 随每条
消息挂树即时前移，回合末无结算写入；钩子抛异常不再楔死 agent）→
``after_turn``（所有路径**唯一**收尾观察点；handler 读 ``turn.aborted``
区分正常结束 / 取消——abort 专用收尾钩子已删除，观察是改写的子集）→
``build_turn_result`` → resolve 全部 waiters）。

**``_append_message`` 五步**（挂树 + 落盘统一入口）：``before_turn_append``
dispatch → ① 设 ``parent_id = current_head_id`` → ② 挂入 ``_messages`` →
③ ``_persist_message`` 提交落盘（write-behind：同步排队即返，
墓碑压缩由 FileRecordStore drain 任务自主触发）→
④ 更新 ``current_head_id = msg.id``（head 即时前移——head 即「添加节点
的位置」，空 turn 无 append 自然不动）→ ⑤ 记入 ``turn.message_ids`` →
``after_turn_append`` dispatch。**消息完整后才经过它**——流式进行中的
增量（尚未定型为消息的 delta 累积态）不经过它，天然不落盘；流式被
中断时，已累积内容定型为一条 ``partial=True`` 的完整消息，照常挂树
落盘。

**``_pending_turns`` 出队绑定**：``dict[message_id → Future[TurnResult]]``，
纯运行时不持久化。``query()`` **注册先于入队**（enqueue 之前，P3-01——
否则 enqueue 内部的钩子 await 窗口里工作循环可能先出队收尾，句柄永不
resolve）；工作循环出队时逐条
``pop(m.id)`` 绑定到当前逻辑 Turn；回合收尾 resolve 回合内**所有**消息的
等待者（drain 合并时共享同一 ``TurnResult``）。取消等待 ≠ 取消回合；撤回未
出队消息用 ``cancel_queued``；``destroy()`` 与 ``cancel_queued`` 均联动
resolve（``status="cancelled"``），调用方永不挂起。**死锁禁止**：回合
调用栈内 ``await query()`` 必死锁，跨 Agent 等待图须无环（框架不检测），
回合内驱动走 ``steer()``（STEER 优先级，检查点 ②.5 吸收、当轮 context
可见）；``enqueue_message`` / ``message()`` 入队的非 STEER 消息回合内
不可察觉，只在回合间消费。

**fork 全时合法（无守卫）**：fork 是纯上下文操作，不碰执行（不触碰
``_executions`` / ``_children`` / ``prompt_blocks`` /
``_provided`` / ``_tool_entries`` / ``_message_queue``，共享不拷贝）。
head 即「添加节点的位置」（每条消息挂树即前移），回合内 fork 的语义
即 seek——本回合后续 append 与 provider_gen 改在新基址上继续。这是可预想的
操作语义而非损坏，框架不做家长式禁止；调用方责任（嫁接 /
``turn.message_ids`` 跨链 / 上下文瞬移 / 工具执行相位的配对断裂）
见 :meth:`Agent.fork` 的设计动机段落。外部改方向的推荐定式仍是
``await agent.cancel()`` → 等回合收尾完成 → ``await agent.fork(msg_id)``；
回合内直接 fork 适合时序可知的钩子内调用者（如 compact 在
``after_provider_gen`` 的干净点换链）。

**side_query 边界**：不 commit、不落盘、固定非流式（走
``provider_gen(context, stream=False, by="_side")``；``by`` 透写到 delta 与
响应，钩子可按来源过滤）；token 用量由调用方自行记录；agent 实例
不是用完即弃——一个实例承载多次副线调用。

**cancel/stop 族与 Execution**：``cancel`` 族协作式（置 ``cancel`` Event，
执行体可自行决定停止方式，返回值不丢弃）；``stop`` 族默认等价 ``cancel``、
可覆写强制。``Execution`` 注册/清理严格成对且清理在 ``finally``（杜绝幽灵
条目）。级联取消：每层只负责自己的 ``_executions``，自动逐层传播。

**销毁与记录保留**：``destroy()`` 递归销毁子树（深度优先）、resolve 所有
pending（cancelled）、取消工作循环、从 ``_nodes`` 摘除；但 **destroy ≠ 删除**
——session（tree.jsonl + state.jsonl）与池 key 保留到显式删除目录，
「有 key 无 value → 现场恢复」（``Runtime.get_agent``）。

.. rubric:: 参见

- :class:`flowing.runtime.Runtime` —— 对象图根、唯一创建/恢复入口宿主。
- :class:`flowing.message.Message` / :class:`flowing.message.MessageChain` ——
  消息级树节点与树手术。
- :class:`flowing.hooks.HookRegistry` —— 实例级钩子注册表（钩子点全集与
  dispatch 规则见其规约）。
- :mod:`flowing.model` —— ``ModelConfig`` / ``ProviderResponse`` /
  ``Usage`` / ``ProviderDelta``。
"""

from __future__ import annotations   # S-43 裁决③：注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

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
from uuid import uuid4

from pydantic import ValidationError

from flowing.context import Context, ContextUsageEstimate, PromptBlockList, PromptSegment
from flowing.errors import (
    ContextLengthError,
    EntryNameConflictError,
    FlowingError,
    FormatError,
    Intercepted,
    StateKeyError,
    ToolNotFoundError,
    UnknownToolError,
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
    Tool,
    ToolCall,
    ToolDefinition,
    ToolEntry,
    ToolResult,
    normalize_output,
)

if TYPE_CHECKING:
    from flowing.runtime import Runtime

T = TypeVar("T")

_logger = logging.getLogger(__name__)
"""模块级 logger：工作循环回合异常等的记录点（规约只要求「记日志」，未具名
logger 符号）。"""

# R-12 落实：核心保留状态键清单——以 _open_stores 实际登记的键（child_ids）
# 加「由框架写透语义承载的核心裸名」（current_head_id，见 register_state 的
# 测试案例）为准；插件声明撞之报错。
_CORE_STATE_KEYS = frozenset({"child_ids", "current_head_id"})


class _LlmViewValidationError(FlowingError):
    """LLM 视角校验失败的内部信号（**内部 API，不属稳定契约**）。

    spec 未写清处落实：``schema_to_model`` 的桥接模型默认忽略未定义键
    （Pydantic 默认 extra 行为），而「幻觉参数按未定义参数校验错误处理」
    要求显式拒绝——未知键检查在 ``Agent._normalize`` 内以本异常报出，
    键名即 LLM 自己提供的别名（回指不构成泄漏）；``Agent.tool_call``
    捕获后包装为 ``ToolResult(status="error")`` 正常产物。
    """


def _estimate_tool_schema_tokens(definition: ToolDefinition) -> int:
    """工具 schema 的 token 补估（``llm_definition()`` JSON 序列化 ÷ 4）。

    **内部 API，不属稳定契约。** ``estimate_context_tokens`` 的「锚点后新增
    工具补估 schema」与「无锚点全估」共用；与消息估算的字符启发式（4 字符
    /token）同口径。
    """
    from dataclasses import asdict

    return len(json.dumps(asdict(definition), ensure_ascii=False)) // 4


def _as_parsable_patch(value: Any) -> Parsable | None:
    """覆写体的文本类补丁值归一（**内部 API**）：``_``（PENDING）→ 空补丁
    （``None``）；``Parsable`` 原样透传；其余包装为 ``Parsable`` 常量。

    ``add_tool`` 的 ``description`` 与 ``add_agent`` 的 ``system_prompt`` /
    ``description`` 共用（求值面内字段的手动包装点）。
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
    """覆写体 ``args:`` 映射的逐参数判别（**内部 API**）。

    ``add_tool`` / ``add_agent`` 共用（SubagentEntry 行为规约：同一套代码
    路径）。规则（ToolEntry 行为规约本体）：

    - 值是 dict → ``override_params`` 稀疏补丁（JSON Schema 关键字，零糖）；
    - 键含 ``<name> as <alias>`` → ``param_aliases``，值部分照常判别；
    - 值是 ``_``（PENDING）→ **空补丁**（``override_params[name] = {}``，
      深层块可逐字段填充，未填充则合成时全量回填，不报错）；
    - 其它值 → ``specified``（包装 ``Parsable``；``"{{ self.inject('key') }}"``
      注入表达式在此落入，R-4）。
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
"""``watch`` 的回调类型：``(new_value, old_value) -> None``，返回值忽略。
"""


@dataclass
class TurnContext:
    """逻辑 Turn 的执行期临时对象——所有 turn 族钩子的 value。

    .. rubric:: 功能介绍

    逻辑 Turn（消费一条（或多条，若覆写 ``_dequeue``）消息 →
    ``ProviderResponse.finish=True`` 的执行过程）在执行期间的唯一载体。
    turn 开始时由 ``_run_turn`` 创建，收尾后丢弃；同时是
    :attr:`TurnResult.turn` 的类型与 ``Agent.current_turn`` 的类型。

    .. rubric:: 设计动机

    取代旧物理 Turn 结构：消息级树下「本 turn 产生了哪些消息」只是对树中
    消息节点的**引用集**（``message_ids``），不再需要一个拥有消息副本的
    物理容器。Turn 降级为纯执行概念后，需要一个对象承载：① turn 族钩子的
    value；② 回合物质存活性标记（``current_turn``；快照投影据此判定）；
    ③ 出队后挂树前的待发批次
    （``pending_messages``，M-29 最终裁决：原 ``inject`` 临时注入区删除，
    临时内容改由 ``before_turn`` 向待发批次**附加式**注入，随回合挂树
    持久化——一切内容走持久化路径）；④ finish 工具的结构化交卷载荷暂存
    （``finish_output``——置位即请求本回合自然结束，turn loop 工具段
    照常执行完后视同 ``finish=True`` 走统一收尾；载荷经此传递到收尾段
    写入 ``last_result``）。

    .. rubric:: 使用示例

    .. code-block:: python

        # before_turn 钩子：安全扫描（命中即硬阻断本 turn，
        # 待发消息随阻断丢弃、不进树）
        async def _scan(agent, turn: TurnContext) -> TurnContext:
            for msg in turn.pending_messages:
                if msg.kind == MessageKind.USER and (await scan(msg)).dangerous:
                    raise Intercepted("内容违规")
            return turn

        # before_turn 钩子：附加式注入 reminder（排在触发消息之后，
        # 随批次挂树持久化、崩溃可恢复；擦除用 chain.remove）。
        # 通用版本（含 clean / 间隔参数）见 composables.use_system_reminder
        async def _reminder(agent, turn: TurnContext) -> TurnContext:
            turn.pending_messages.append(Message(
                kind=MessageKind.EVENT, source="reminder",
                content=[TextBlock(text=f"当前时间：{datetime.now()}")],
            ))
            return turn

    .. rubric:: 行为规约

    期待行为：

    - turn 开始（``_run_turn`` 入口）创建；``message_ids`` 初始为空，
      每条经 ``_append_message`` 挂树的消息把 id 追加进来（含触发本 turn
      的首条消息——它在 ``before_turn`` 之后的挂树批次中）。
    - ``pending_messages``：出队后、挂树前的待发批次（含触发消息）。
      **仅 ``before_turn`` 期间可读写**（追加 = 附加式注入，排在触发消息
      之后；清空 = 空 turn）；挂树批次完成后清空，此后读写无意义。
      ``before_turn`` 被 ``Intercepted`` 阻断时批次整体丢弃（不落盘、
      不留痕，显式丢失语义）。
    - ``aborted`` 由幂等的 abort 标记路径置位（cancel / 超时 / 父级联 /
      Composable 直接置位均走同一路径），``before_turn_abort`` 只 dispatch
      一次；``after_turn`` handler 经 ``aborted`` 区分正常结束与取消。
    - 无物理 turn 标识字段；需要逻辑标识时用 ``message_ids[0]``（turn
      首条消息 id）。

    非行为：

    - 不是持久化单位：本身及其任何字段都不写入 tree.jsonl / state.jsonl。
    - 不做消息去重、不维护消息对象副本——只有 id 引用。

    边缘情况：

    - 空 turn（启动即被 abort）：
      ``message_ids`` 可能只含触发消息或为空；空 turn 不产生新树节点，
      ``current_head_id`` 不变。
    - 崩溃后``TurnContext`` 随进程消失，恢复流程**不重建**它（恢复三条
      规则只处理消息级树）。

    .. rubric:: 测试案例

    - 前置：Agent 空闲。操作：``await agent.query("你好")`` → 期望：
      ``TurnResult.turn`` 为 ``TurnContext``，``message_ids`` 依序含 USER
      消息与 PROVIDER 消息的 id，``aborted is False``。
    - 前置：turn 进行中。操作：``agent.abort_turn()`` → 期望：本 turn 的
      ``TurnContext.aborted is True``，``before_turn_abort`` 恰好触发一次；
      ``after_turn`` handler 读到 ``turn.aborted is True``。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent._run_turn()``（时机：逻辑 turn 开始创建、
      收尾后丢弃）；快照投影见 ``flowing.snapshot.TurnContextInfo``
      （时机：每次 ``Agent.snapshot()``）
    - 实例化方：``flowing.agent.Agent._run_turn``（每次逻辑 turn）

    .. seealso::

        - :class:`TurnResult` —— ``turn`` 字段类型即本类。
        - :meth:`Agent._run_turn` —— 创建与收尾本对象的位置。
        - :class:`flowing.message.MessageChain` —— 持久化手术入口
          （M-29 后，回合中追加内容的唯一通道）。
    """

    started_at: datetime
    """turn 开始时间戳（监控 / 日志 / 耗时统计用）。
    """
    finished_at: datetime | None = None
    """turn 收尾完成时间戳；执行期间为 ``None``，``finish`` 收尾时由
    ``_run_turn`` 落位。快照投影（``TurnContextInfo``）只在执行期间可见，
    故其中该字段恒为 ``None``；完整取值见 ``TurnResult.turn.finished_at``。
    """
    message_ids: list[str] = field(default_factory=list)
    """本 turn 已挂树消息的 id（**引用**消息级树节点，按产生顺序追加）。
    逻辑标识约定：``message_ids[0]`` 即本 turn 的首条消息 id。
    """
    aborted: bool = False
    """abort 标记；幂等路径只置位一次，``before_turn_abort`` 随之只触发一次。
    """
    pending_messages: list[Message] = field(default_factory=list)
    """出队后挂树前的待发批次（含触发消息）；仅 ``before_turn`` 期间可读写，
    挂树批次完成后清空。被 ``Intercepted`` 阻断时整体丢弃（不落盘、不留痕）。
    """
    usages: list[Usage] = field(default_factory=list)
    """本 turn 各次成功 provider_gen 上报的用量（``_run_turn`` 内层循环在
    ``response.message is not None and response.message.usage is not None``
    时追加——追加的是消息上附着的**同一 Usage 对象的引用**，不是第二份
    事实；usage 的唯一权威是 ``Message.usage``）。纯内存运行期累加器——
    不落盘（与本类「不是持久化单位」一致），收尾时由 ``build_turn_result``
    聚合进 ``TurnResult.token_usage`` 后随本对象丢弃。副线调用
    （``side_query``）不经 ``_run_turn``，其用量不记入本字段（S-13 裁决）。
    """
    finish_output: dict[str, Any] | None = None
    """finish 工具的结构化交卷载荷——``FinishTool.execute`` 置位（**置位即
    请求本回合自然结束**：turn loop 工具段照常执行完，随后视同
    ``finish=True`` 走统一收尾段；载荷由收尾段写入 ``last_result``）。
    瞬态字段：随回合丢弃、不落盘（与本类「不是持久化单位」一致）——从
    子 Agent 角度它就是一次性传递介质。配对 TOOL 消息正常挂树（无特例），
    本回合的 ``turn_end=True`` 落在该消息上（置位转移检测，见
    ``_run_turn`` 工具调用循环）。任何工具置位本字段语义相同（不限
    finish）。
    """


@dataclass
class TurnResult:
    """逻辑 Turn 的产物——``query()`` 等待语义的 resolve 值。

    .. rubric:: 功能介绍

    「Agent 下一个逻辑 turn 边界返回的东西」。由 :func:`build_turn_result`
    在回合收尾（``after_turn`` 之后）组装；同一回合
    内所有消息的等待者（``_pending_turns`` 出队绑定）**共享同一实例**。

    .. rubric:: 设计动机

    等待语义与 fire-and-forget 分离：``query()`` 需要同步请求-响应的产物
    （Workflow 编排、进程内 UI 适配器两个驱动场景），``enqueue_message()``
    不需要。``TurnResult`` 是该产物的统一结构，边界状态全 resolve——
    调用方永不挂起。

    .. rubric:: 使用示例

    .. code-block:: python

        result = await agent.query("帮我查订单 ORD-12345")
        if result.status == "completed":
            print(result.final_text)
        print(result.token_usage)   # Usage | None

    .. rubric:: 行为规约

    - ``status`` 四值：``"completed"``（``finish=True`` 自然结束）/
      ``"blocked"``（被 ``Intercepted`` 阻断）/ ``"cancelled"``（cancel /
      destroy / ``cancel_queued`` 联动）/ ``"error"``（未捕获异常终止）。
      **四种状态都 resolve**（回合确实结束了），调用方不挂起。
    - ``final_text``：本 turn 最后一条 PROVIDER 消息的文本拼接；无
      PROVIDER 消息（如 abort 于首次 provider_gen 前）时为空字符串。
    - ``token_usage``：本 turn 各次成功 ``provider_gen`` 上报用量的聚合
      （``TurnContext.usages`` 累加器逐字段求和——累加器持有消息上
      同一 ``Usage`` 对象的引用，非第二份事实；``raw`` 不聚合；字段名
      是 ``token_usage``，不是 ``usage``）。为 ``None`` 当且仅当无任何
      成功 provider_gen **上报**用量——区分「provider 未上报」与「真用了 0」；
      部分 provider_gen 上报时只就上报者求和（S-13 裁决）。边缘：abort 于
      消息成形前的调用（``message=None``）其用量无载体，不进入聚合
      （见 ``flowing.providers.ProviderResponse`` 类 docstring 的边缘代价
      声明）。
    - ``finish_reason``：**信息字段**（原始停止原因，如 ``"end_turn"`` /
      ``"cancelled"`` / ``"intercepted"``），不参与控制流；Provider 侧的
      结束判定字段是 ``ProviderResponse.finish: bool``，二者勿混。
    - 非行为：``TurnResult`` 不是持久化结构的投影——``turn`` 是执行期
      临时对象（:class:`TurnContext`），读取其 ``message_ids`` 可回溯树中
      消息，但本对象自身不落盘。

    .. rubric:: 测试案例

    - 前置：drain 覆写下两条消息合并为一个回合。操作：两个
      ``query()`` 并发等待 → 期望：两个 future resolve 到**同一**
      ``TurnResult`` 实例。
    - 前置：``before_turn`` handler ``raise Intercepted`` → 操作：
      ``await agent.query(...)`` → 期望：返回 ``status="blocked"``
      的 ``TurnResult``，不抛 ``Intercepted``。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.query()`` 的等待语义 resolve 值
      （时机：回合收尾，四种结局均 resolve）；``Agent._pending_turns``
      的 Future 结果类型
    - 实例化方：``flowing.agent.build_turn_result``（时机：``_run_turn``
      的 finally 收尾，``after_turn`` 之后）

    .. seealso::

        - :meth:`Agent.query` —— 本结构的获取入口。
        - :func:`build_turn_result` —— 组装函数。
        - :class:`flowing.providers.Usage` —— ``token_usage`` 的类型。
    """

    turn: TurnContext
    """产生本结果的逻辑 Turn 的执行期临时对象（文档 14 后类型为
    ``TurnContext``）；其 ``message_ids`` 引用消息级树中的节点。
    """
    final_text: str
    """最终回复文本；无 PROVIDER 消息时为空字符串。
    """
    status: Literal["completed", "blocked", "error", "cancelled"]
    """回合结局；四值均会 resolve 给等待者。
    """
    token_usage: Usage | None
    """本 turn 聚合 token 用量；无成功 provider_gen 时为 ``None``。
    """
    finish_reason: str
    """信息性停止原因（不占控制流字段）。
    """


@dataclass
class Execution:
    """活跃异步执行条目——「谁正在运行、谁可取消」的追踪单位。

    .. rubric:: 功能介绍

    Agent 发起的每个异步执行（工具调用、子 Agent、LLM 请求、副线查询）启动时
    注册一条 ``Execution`` 入 ``agent._executions``，完成/异常/取消/超时后
    **在 ``finally`` 中移除**。``cancel`` / ``stop`` 族通过遍历该注册表
    置位控制信号。

    .. rubric:: 设计动机

    取消是**协作式**的：``cancel.set()`` 是请求不是命令——执行体在检查点
    检测信号后自行决定立即停止（返回已有/空结果）、忽略信号正常完成、或做
    关键收尾后返回部分结果。框架不设「kill -9」语义（那是可覆写 ``stop()``
    的职责）。``pause`` Event 与 ``cancel`` 对称，保留给 Tool 覆写与
    Composable——**框架核心不主动 set/clear 它**，且 Execution 层暂停只影响
    单个执行、不级联（区别于 Agent 层 ``pause()`` 工作循环 gate）。

    .. rubric:: 使用示例

    .. code-block:: python

        execution = Execution(
            id=str(uuid4()), kind="tool", tags=self._build_tool_tags(tool),
            started_at=datetime.now(), cancel=asyncio.Event(),
            pause=asyncio.Event(),
        )
        self._executions[execution.id] = execution
        try:
            return await tool(resolved_args, caller=self, execution=execution)
        finally:
            self._executions.pop(execution.id, None)

    .. rubric:: 行为规约

    - ``kind`` 是**开放字符串**（非封闭枚举）：内置四值 ``"tool"`` /
      ``"agent"`` / ``"request"`` / ``"side_query"``；扩展可引入新值
      （``"workflow"`` / ``"cron"`` 等），注册/冲突规则由扩展自管。
    - ``tags`` 供分组取消（``cancel_by_tag``）；典型用法
      ``tags=["bash", "long-running"]``、``tags=["subagent", "explore"]``。
    - 不变量：注册与清理严格成对，清理一定在 ``finally``——执行条目永不
      留残留，杜绝「幽灵条目」让后续 ``cancel()`` 误伤已完成的执行。
    - 非行为：快照（``AgentSnapshot.executions``）只暴露
      ``kind`` / ``tags`` / ``started_at``——**不暴露** ``cancel`` / ``pause``
      Event，避免绕过控制 API 与钩子。

    .. rubric:: 测试案例

    - 前置：工具执行抛异常。操作：等待 ``tool_call`` 返回 → 期望：
      ``agent._executions`` 中对应条目已被移除（finally 清理）。
    - 前置：``Execution(kind="tool", tags=["bash"])`` 运行中。操作：
      ``agent.cancel_by_tag("bash")`` → 期望：仅该条目 ``cancel.is_set()``，
      其它条目与 ``_turn_abort`` 不动。

    .. rubric:: 调用关系（审计）

    - 被调：``flowing.agent.Agent.cancel()`` / ``cancel_children()`` /
      ``cancel_by_tag()``（时机：每次取消，遍历 ``_executions`` 置位
      信号）；快照投影见 ``flowing.snapshot.ExecutionInfo``（时机：每次
      ``Agent.snapshot()``）
    - 实例化方：``flowing.agent.Agent.tool_call``（``kind="tool"``）、
      ``Agent.provider_gen``（``kind="request"``）、``Agent.side_query``
      （``kind="side_query"``）、``Agent.invoke_subagent``
      （``kind="agent"``）（时机：各执行启动时注册，finally 清理）

    .. seealso::

        - :meth:`Agent.cancel` / :meth:`Agent.cancel_children` /
          :meth:`Agent.cancel_by_tag` —— 信号置位入口。
        - :class:`flowing.tool.ScriptTool` —— 工具执行的宿主（cancel 注入）。
    """

    id: str
    """UUID，注册表 ``_executions`` 的 key。
    """
    kind: str
    """开放字符串：内置 ``"tool"`` / ``"agent"`` / ``"request"`` /
    ``"side_query"``；扩展可引入新 kind。
    """
    tags: list[str]
    """自由标签，``cancel_by_tag`` / ``stop_by_tag`` 的分组依据。
    """
    started_at: datetime
    """启动时间戳（监控 / 日志 / stop 超时判断）。
    """
    cancel: asyncio.Event
    """置位 = 请求取消（初始未 set）；协作式信号，执行体可自行决定是否响应。
    词汇约定：**「取消 Execution」一侧统一叫 cancel**（方法
    ``cancel()`` / ``cancel_children()`` / ``cancel_by_tag()``、钩子
    ``before_cancel`` / ``after_cancel`` 与本字段）；**Turn 循环一侧保留
    abort**（``_turn_abort`` / ``abort_turn()`` / ``before_turn_abort`` /
    ``turn.aborted``）——两个域各自的词汇内部自洽，不跨域混用。
    """
    pause: asyncio.Event
    """置位 = 请求暂停（初始未 set）；保留给 Tool 覆写与 Composable，框架
    核心不主动 set/clear；只影响单个执行，不级联。
    """


@dataclass
class FieldUpdate:
    """``watch`` watcher 通道的 value——一次实例属性赋值事件的快照
    （M-41 最终裁决：watcher 通道 + fire-and-forget，纯观察语义）。

    .. rubric:: 功能介绍

    ``Agent.__setattr__`` 拦截实例属性赋值时，在**写入前**构造本对象
    并以 fire-and-forget 方式通知 watcher 通道（``hooks._notify_watch``，
    pattern 匹配本对象的 ``name`` 字段——``agent.watch('locale', ...)``
    即字面量精确匹配）。watcher 收到的是赋值事件的**自洽快照**：
    无论 watcher 何时真正执行，``old`` / ``new`` 都是触发那一刻的值。

    .. rubric:: 设计动机

    watcher 的触发者是**用户的赋值语句**而非框架管线，且赋值是
    同步原语——若 watcher 参与赋值语义（改写 / 取消），watcher 就
    必须是同步函数。最终裁决放弃拦截语义（对齐 Vue / MobX 的「只
    通知」哲学）：通知改为 fire-and-forget 后台任务，赋值不等待
    watcher，watcher 因此可以是 sync 或 async——同步约束随拦截语义
    一起消失。想拦截赋值请用 Python 原生手段（property setter）。

    .. rubric:: 使用示例

    .. code-block:: python

        # 低层注册（收完整快照）
        self.hooks.watch('locale',
            lambda agent, fu: self.logger.info(f"{fu.name}: {fu.old} -> {fu.new}"))

        # 更常用的是语法糖：
        self.watch('locale', lambda new, old: self.reload_prompt())

    .. rubric:: 行为规约

    - **纯观察**：改写 ``new`` 无效（watcher 返回值被忽略）；
      ``raise Intercepted`` 与普通异常同处理——终止本次 watcher 链、
      记录日志，**不影响赋值**（fire-and-forget 无上抛对象）。
    - watcher 可为同步或 async（后台任务统一 await）。
    - **时序不保证**：通知任务在写入前入队，但 watcher 的实际执行
      可能晚于写入完成；连续多次赋值的多个 watcher 间执行顺序
      亦不保证——依赖时序的逻辑应自行序列化，快照数据始终自洽。
    - 无运行中的 event loop 时（如 ``__init__`` 骨架阶段的赋值），
      通知**静默跳过**——赋值照常，watcher 不触发。这是
      fire-and-forget 的已知边界。
    - 触发范围：仅**实例属性赋值**；描述符 / 类属性 / ``_`` 前缀骨架
      字段的初始化不经过本机制。
    - 边缘情况：watcher 内再次给同名字段赋值造成递归通知——
      框架不做递归防护，属编程错误。
    - 插件约定：托管变量（如 i18n 插件注入的 ``agent.i18n``）应以
      插件自有对象为载体，属性级拦截在该对象自己的类里实现；顶层
      槽位的重绑定只能经 ``watch`` 观察、不能拦截（可纠正性
      回写，会二次触发监听）。

    .. rubric:: 测试案例

    - 前置：``calls = []``；``agent.watch('locale', lambda new, old:
      calls.append((new, old)))``。操作：``agent.locale = "en"`` 后
      让出 event loop。期望：``calls == [("en", <旧值>)]``。
    - 前置：``hooks.watch('locale', lambda a, fu: 1/0)``。操作：
      ``agent.locale = "zh"``。期望：赋值成功、无异常传播、异常被
      记录日志。
    - 前置：无运行中 event loop。操作：``agent.locale = "zh"``。
      期望：赋值成功，handler 未触发。

    .. rubric:: 调用关系（审计）

    - 被调：watcher 通道的通知 value（时机：fire-and-forget
      后台任务，赋值写入前入队）
    - 实例化方：``flowing.agent.Agent.__setattr__``（时机：每次实例属性
      赋值，写入前构造快照）

    .. seealso::

        :meth:`Agent.watch`、:class:`flowing.hooks.HookList`
    """

    name: str
    """被赋值的字段名；``match_on`` 锚点（pattern 匹配本字段）。
    """
    old: Any
    """旧值（字段不存在时为 ``_UNSET`` 哨兵）。
    """
    new: Any
    """即将写入的值（快照，只读语义——改写不影响赋值）。
    """


@dataclass
class ProviderErrorContext:
    """``on_provider_error`` 钩子的 value——LLM 调用异常的唯一决策上下文。

    .. rubric:: 功能介绍

    ``_run_turn`` 内 ``provider_gen()`` 抛出的异常被回合层捕获后，构造本对象并
    dispatch ``on_provider_error``。handler 在内部执行动作（sleep 退避 / 改
    ``self.model`` / 调 ``abort_turn()``）并写 ``can_continue`` 表达决策。

    .. rubric:: 设计动机

    机制 vs 策略：核心只做错误分类与分发（本对象是机制）；「该不该重试、
    重试几次」是策略——内置、可选、非默认的 ``use_retry()`` Composable 以
    ``by="retry"`` 注册 handler 提供重试，不调用则 ``can_continue`` 保持
    ``False``，错误直接终止回合（Agent 存活）。

    .. rubric:: 使用示例

    .. code-block:: python

        async def _retry(agent, ctx: ProviderErrorContext) -> ProviderErrorContext:
            if isinstance(ctx.error, (RateLimitedError, ServerError)):
                await asyncio.sleep(2)
                ctx.can_continue = True     # 触发 continue（重试）
            return ctx

    .. rubric:: 行为规约

    - ``can_continue=False``（默认 / 无 handler / handler 未改写）→ 回合
      中断（break）；``True`` → 内层循环 ``continue``（handler 须已完成
      退避 / 换模型等动作）。
    - handler 内调 ``agent.abort_turn()`` 是合法出口：``continue`` 后下一次
      ``provider_gen()`` 开头检测信号 → 返回 ``cancelled`` 响应 → 回合走 abort
      收尾。
    - 非行为：``ContextLengthError`` **不经过** ``on_provider_error``——不可
      重试，直接上抛（需钩子层压缩/截断的场景由 ``before_provider_gen`` 等机制
      处理）。
    - ``provider`` 是 provider 条目名字符串（``self.model.provider``），
      不是 Provider 实例。

    .. rubric:: 测试案例

    - 前置：provider 抛 ``RateLimitedError``，无 handler → 操作：消息触发
      回合 → 期望：回合中断，``TurnResult.status == "error"``，Agent 存活
      可继续消费后续消息。
    - 前置：handler 写 ``can_continue=True`` → 期望：同一 ``TurnContext``
      内重新发起 ``provider_gen``。

    .. rubric:: 调用关系（审计）

    - 被调：``on_provider_error`` 钩子 dispatch 的 value（时机：``provider_gen()``
      异常被回合层捕获后）；``flowing.composables.retry.use_retry`` 的
      handler 消费本对象（时机：启用重试的 Agent 每次 LLM 调用失败）
    - 实例化方：``flowing.agent.Agent._run_turn``（时机：``provider_gen()`` 抛
      异常后、dispatch ``on_provider_error`` 前）

    .. seealso::

        - :meth:`Agent.provider_gen` —— 异常来源（``provider_gen`` 本身不捕获、不重试）。
        - :mod:`flowing.composables.retry` —— 可选重试策略。
        - :class:`flowing.errors.ContextLengthError` —— 不经过本钩子的例外。
    """

    error: Exception
    """``provider_gen()`` 上抛的原始异常。
    """
    provider: str
    """发生错误的 provider 条目名（``ModelConfig.provider``，字符串）。
    """
    model: ModelConfig
    """发生错误时的模型结构体。
    """
    can_continue: bool = False
    """决策字段：handler 写 ``True`` 触发回合内重试；默认 ``False`` =
    回合中断。取代了旧 ``QueryErrorAction`` 枚举与 ``shortcut`` 写法。
    """


@dataclass
class CancelContext:
    """``before_cancel`` 钩子的 value——取消操作的拦截上下文。

    .. rubric:: 功能介绍

    ``cancel()`` 在置位任何 ``abort`` 信号**之前** dispatch
    ``before_cancel``，handler 可 ``raise Intercepted`` 阻止取消——适用于
    当前操作不可中断的场景（支付已提交、关键事务进行中）。

    .. rubric:: 设计动机

    审批 / 阻断是机制（钩子点 + ``Intercepted``），「什么时候不许取消」是
    策略（应用层 handler）。框架不内置任何不可取消规则。

    .. rubric:: 行为规约

    - ``reason``：取消原因说明。``Agent.cancel()`` / ``stop()`` 均**不接受
      原因参数**（定稿：无参）；框架内部 dispatch 时填默认值 ``""``。扩展
      若自行 dispatch ``before_cancel`` 可携带自定义原因。
    - handler ``raise Intercepted`` → 取消被阻止，``_executions`` 与
      ``_turn_abort`` 均不置位。
    - handler 普通异常 → 直接上抛给 ``cancel()`` 调用方，信号不置位。

    .. rubric:: 调用关系（审计）

    - 被调：``before_cancel`` 钩子 dispatch 的 value（时机：置位任何
      ``abort`` 信号之前）
    - 实例化方：``flowing.agent.Agent.cancel``（时机：每次取消；
      ``Agent.stop`` 默认实现经 ``cancel`` 间接触发）

    .. seealso::

        - :meth:`Agent.cancel` —— 唯一触发点。
        - :class:`flowing.errors.Intercepted` —— 阻断信号。
    """

    reason: str = ""
    """取消原因；框架内部调用恒为 ``""``（cancel 族无参定稿），供扩展
    自行 dispatch 时携带。
    """


def build_turn_result(turn: TurnContext, agent: "Agent", *,
                      intercepted: bool = False,
                      error: BaseException | None = None,
                      finish_reason: str = "") -> TurnResult:
    """回合收尾组装 :class:`TurnResult`（模块级函数）。

    .. rubric:: 功能介绍

    ``_run_turn`` 的 ``finally`` 中、``after_turn`` 之后调用：从
    :class:`TurnContext` 与消息级树聚合 ``final_text`` / ``status`` /
    ``token_usage`` / ``finish_reason``，产物 resolve 给本回合全部 waiters
    （共享同一实例）。

    .. rubric:: 设计动机

    组装逻辑独立于 ``_run_turn`` 主体：收尾路径有多条（正常 / abort /
    异常 / Intercepted），统一出口保证四种结局都产出合法 ``TurnResult``，
    调用方永不挂起。

    **信号来源（S-28 裁决）**：结局信号分两类载体——执行期状态在
    ``turn`` 上（``aborted`` / ``usages`` / ``message_ids``）；一次性
    结局信号（拦截 / 异常对象）由调用点 ``_run_turn`` 的 except 帧
    **显式传参**（``intercepted`` / ``error``），不落入 ``TurnContext``
    字段（它们只在收尾瞬间有意义，不该污染 turn 族钩子 value 与快照
    投影）。树访问经 ``agent`` 参数（``Agent._messages``）。

    .. rubric:: 行为规约

    - 纯函数式组装：读 ``turn.message_ids`` 指向的树中消息，不修改树、
      不落盘、不 dispatch 钩子。
    - ``status`` 判定（优先级从上到下）：``turn.aborted`` →
      ``"cancelled"``；``intercepted=True`` → ``"blocked"``；
      ``error is not None`` → ``"error"``；否则 ``"completed"``。
    - ``token_usage`` 聚合（S-13 具名规约）：``turn.usages`` 为空 →
      ``None``（区分「provider 未上报」与「真用了 0」）；非空 →
      七个计数字段逐字段求和，聚合体 ``raw = {}``（逐次原始字段的
      消费方走 ``after_provider_gen``）。
    - 边缘情况：``turn.message_ids`` 为空（空 turn）→ ``final_text=""``、
      ``token_usage=None``。

    :param turn: 本回合的执行期载体。
    :param agent: 属主 Agent——树访问载体（``agent._messages``）。
    :param intercepted: 本回合是否被钩子 ``Intercepted`` 硬阻断
        （``_run_turn`` 的 except 帧捕获后传入）。
    :param error: 本回合未捕获的异常对象（同上；无为 ``None``）。
    :param finish_reason: 自然完成时末次 ``provider_gen`` 响应的原始停止原因
        （``provider_data.get("stop_reason", "")``，由 ``_run_turn`` 显式
        传入；R-09 落实——取消 / 拦截 / 异常结局由本函数按 ``status`` 填
        对应字面量，本参数仅 ``completed`` 结局采用）。

    .. rubric:: 测试案例

    - 前置：正常完成的 turn（含一条 USER + 一条 PROVIDER ``turn_end=True``
      消息）→ 操作：``build_turn_result(turn, agent)`` → 期望：
      ``status="completed"``，``final_text`` 为 PROVIDER 文本。
    - 前置：``before_turn`` handler 抛 ``Intercepted`` → 期望：
      ``status="blocked"``，``final_text=""``（批次未挂树）。
    - 前置：内层循环未捕获异常 → 期望：``status="error"``。

    .. rubric:: 调用关系（审计）

    - 调用：读 ``Agent._messages``（每次收尾，经 ``agent`` 参数访问
      树中消息）；其余为纯函数式聚合，不改树、不落盘、不 dispatch
    - 被调：``flowing.agent.Agent._run_turn``（时机：finally 收尾、
      ``after_turn`` 之后；``intercepted`` / ``error`` 由其
      except 帧传入）

    .. seealso::

        - :meth:`Agent._run_turn` —— 唯一调用点。
        - :class:`TurnResult` —— 产物类型。
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
    # final_text（S-28：树访问经 agent._messages）：turn.message_ids 逆序找
    # 最后一条 PROVIDER 消息，取其 content 中 TextBlock 的拼接文本
    final_text: str = ""   # 空 turn / 无 PROVIDER 消息 → ""
    for mid in reversed(turn.message_ids):
        m = agent._messages[mid]
        if m.kind is MessageKind.PROVIDER:
            final_text = "".join(b.text for b in m.content
                                 if isinstance(b, TextBlock))
            break
    # token_usage 聚合（S-13 具名规约）：usages 空 -> None（区分未上报与真零）；
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
    # finish_reason（R-09 落实）：取消 / 拦截 / 异常结局填对应字面量；
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


class Agent:
    """智能体基类——``.fya`` 声明式与手写子类共用的同一类模型。

    .. rubric:: 功能介绍

    Flowing 的中心对象：拥有自己的消息队列、常驻工作循环 Task、消息级树、
    实例级钩子注册表、工具 / 子 Agent 绑定条目与 provide-inject 链位置。
    实例即 handle（无 ``AgentHandle``）；元信息直接作为类属性（无
    ``AgentSpec``）。**实例化不直接进行**——唯一创建路径是
    ``Runtime.create_agent(agent_type, *, parent_id=None, **kwargs)``
    （``Agent.create_subagent`` / ``Workflow.create_agent`` / ``mount``
    全部委托它）；恢复路径是 ``Runtime.recover_agent(agent_id)``。

    .. rubric:: 设计动机

    - **无 AgentSpec**：``.fya`` 元信息直接作为类属性——类型层面反映行为
      差异（traceback 显示 ``OrderAgent``）、手写子类与 ``.fya`` 同一套
      类模型、消除双份元数据源。
    - **不持父对象引用**：只存 ``_parent_id: str``——生命周期定位与
      provide 上溯共用一条 UID 链，节点间不持有对象引用。
    - **双哨兵不混用**：``PENDING``（``.fya`` 的 ``_``，字段位 =
      「延迟定义承诺，``setup()`` 后检查」；override 位 = 空补丁语义，
      见 :data:`flowing.parsable.PENDING` 与装配层导航规则）与
      ``_UNSET``（未设置，参数默认值判定）语义不同。
    - **Agent 不只是树中节点**：消息队列、异步执行条目、常驻工作循环使其
      具备自治能力（对等 Agent 通信、池遍历、UI 挂载三场景佐证）。

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
          - payment as pay   # 运行期示例中 create_subagent("pay") 的声明依据
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

        class OrderArgs(BaseModel):   # 参数声明（B1：声明即模型）
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

        child = await self.create_subagent("payment", order_id="456")   # 类型名引用（fya 示例的 subagents: [- payment as pay] 声明，见上方）
        result = await child.query("发起退款")
        await child.pause()      # 协作式暂停工作循环
        await child.destroy()    # 显式销毁；session 记录保留

    .. rubric:: 行为规约

    - 同名 ``.fya`` 与手写子类并存时 ``.fya`` 优先并告警；两者生成的类
      结构完全相同（等价且互斥）。
    - ``setup()`` 轻量约束：只做状态赋值、钩子注册、inject 读取、
      Composable 调用；网络请求 / 文件 I/O / 大量计算移到工具调用或按需
      阶段。``setup()`` 第一个 ``await`` 之前的代码不被其它协程打断。
    - 不变量：``self.model`` 永远是 ``ModelConfig``（不做 isinstance
      分支）；Agent 对模型结构体只做「持有 + 机械传递」，不解释字段。
    - 不变量：创建即注册（``_nodes``）；父销毁 → 子递归销毁；destroy
      后实例不再可用（从 ``_nodes`` 摘除、三正交结构均清空——死活由
      在册与否表达，``_parent_id`` 作为历史事实不改写），但
      session 记录保留可现场恢复。

    .. rubric:: 调用关系（审计）

    - 调用：无（骨架职责见 ``__init__``；行为入口见各方法条目）
    - 被调：无（用户子类化的基类；框架内以实例方法形式被调，见各方法）
    - 实例化方：``flowing.runtime.Runtime.create_agent``（新建管线）、
      ``flowing.runtime.Runtime.recover_agent``（恢复管线）；
      ``flowing.agent.Agent.create_subagent`` /
      ``flowing.plugins.workflow.Workflow.create_agent`` /
      ``Runtime.mount`` 均委托 ``create_agent``

    .. seealso::

        - :meth:`flowing.runtime.Runtime.create_agent` —— 唯一创建入口。
        - :meth:`flowing.runtime.Runtime.recover_agent` —— 恢复入口。
        - :class:`flowing.runtime.ProvideNode` —— 本类实现的协议。
    """

    # ────────────────────────── 类属性（.fya 元信息即类属性） ──────────────

    class_name: ClassVar[str]
    """Python 类名（PascalCase）。``.fya`` 可显式声明 ``class_name:``；缺省
    从**身份名**推断——格式转换经 :func:`flowing.paths.kebab_to_pascal`，
    其后由装配层叠加**后缀策略**：**始终保证以一个 ``Agent`` 结尾**
    （已结尾则原样（``pay-agent`` → ``PayAgent``），否则补上
    （``pay`` → ``PayAgent``））。手写子类即 ``__name__``，无需声明。

    .. rubric:: 身份名的推断（``name`` 非机制字段）

    Agent 的身份名（注册表 key、``agent_type`` 裸名、引用别名缺省、
    ``class_name`` 推断的输入）**一律由推断产生**，框架不存在 ``name``
    机制字段。路径形态的身份名推断机制本体在
    :func:`flowing.paths.infer_name`（去后缀、通用名取目录名、
    snake→kebab；规则表 ``AGENT_NAMING`` 见 ``flowing.runtime``）：

    - 路径形态 ``.fya`` / 手写 ``.py``：文件名去 ``.agent.fya`` /
      ``.fya`` / ``.py`` 后缀；文件名是 ``agent.fya`` / ``AGENT.fya``
      这两个通用名 → 取目录名；
    - 裸名：注册表 key 本身（``register_agent_type`` 的显式参数）；
    - 手写子类：类名 kebab 化（``OrderAgent`` → ``order-agent``，经
      :func:`flowing.paths.pascal_to_kebab`）。

    ``.fya`` 与手写子类中**不禁止**写 ``name``，但仅作一致性断言：与
    推断值不符抛 :class:`flowing.errors.NameMismatchError`（消息含声明
    值 / 推断值 / 来源）；一致时无任何效果。
    """
    description: ClassVar[Parsable]
    """Parsable；**实例创建前**由父 Agent 读取（路由决策），渲染上下文为
    父 Agent 实例。可选；缺失 / ``null`` 落 ``None``。
    """
    metadata: ClassVar[dict[str, Any]]
    """任意静态键值对，框架不解释，透传 ``self.metadata``，供扩展 /
    Composable 读取。
    """
    system_prompt: ClassVar[Parsable]
    """**唯一必填**类属性；实例化后进入 ``prompt_blocks[0]`` 的惰性引用块
    （``by="core"``，``content=Parsable("{{ self.system_prompt }}")``——
    类属性不在实例 ``__dict__`` 摊平里，经渲染上下文的 ``self`` 入口
    访问；EXPRESSION
    求值得到本属性（描述符 ``__get__`` 已绑定实例的 Parsable），按
    ``EXPRESSION`` 的结果收尾规则经 ``.resolved`` 渲染一层，内层模板
    随之渲染——单一数据源，``setup()`` 中改它下次 provider_gen 自动
    反映）。创建管线 PENDING 检查点仍为 PENDING → ``MissingFieldError``。
    """
    model_tag: str = "default"
    """声明层模型意图（``"fast" / "high" / "default"`` 是语义约定非枚举），
    与 Provider 完全解耦；支持 Jinja2 模板（惰性求值，每次 ``provider_gen()``
    前重新求值）；运行时可变（``agent.model_tag = "high"`` → 重新解析
    覆盖 ``self.model``，只能指向配置已定义模型）。标签未定义回退
    ``default``；``default`` 也未定义 → 报错（不静默回退）。
    """
    args_model: ClassVar[type[BaseModel] | None]
    """实例化参数声明（B1 裁决：Pydantic BaseModel 子类，声明即模型——
    同时是 ``subagent-invoke`` 工具 LLM 可见 schema 与执行层校验的来源）。
    三种来源形态：

    - ``.fya`` 显式写 ``args:``（纯 JSON Schema 展开式，糖见
      :mod:`flowing.params`）→ 经 :func:`flowing.params.schema_to_model`
      桥接成模型，以其为准（YAML 优先），``setup()`` 签名仅作校验
      对照（参数必须有对应字段、类型标注必须兼容）；
    - 手写子类显式声明 ``args_model = MyArgs``（BaseModel 子类）→
      直接使用（声明即模型）；
    - 两者皆无 → **类创建时从 ``setup()`` 签名推导**（装配层 /
      ``__init_subclass__`` 执行）：逐参数 ``inspect`` 签名，类型标注 →
      字段类型（标注缺失 → 构造期抛 ``ValueError``）；带默认值 →
      字段默认值（可选）；无默认值 → 必填。``setup(**kwargs)`` 全
      kwarg 吸收形态 → 推导产物为 ``None``（空模型不接受构造参数）。

    推导发生在类创建期而非实例化期：产物是确定的类属性，运行时无
    重复推导。
    """
    _extra: dict[str, Any]
    """``.fya`` 中框架不认识的字段（如 ``skills:`` / ``modes:``）的静默
    仓库；``__getattr__`` 回退查找。**实例属性**（非 ClassVar）：
    ``__init__`` 建立为空表，``.fya`` 装配层在生成 ``setup()`` 的前置段
    合入未知字段。框架不解析其中 Parsable——扩展自行
    ``.resolve(render_context)``。
    """
    source_file: ClassVar[str | None]
    """``@/`` 格式源文件路径；类创建时经 ``__init_subclass__`` 从
    ``__module__.__file__`` 推算；``.fya`` 来源的类由**装配层（编译
    管线）在生成类时显式注入**——值为该 ``.fya`` 文件的 ``@/`` 路径，
    优先级最高（不走 ``__module__`` 推算，生成代码的模块路径没有
    意义）；显式写 ``None`` 或推算失败 → ``None``。只读，实例化后
    不可变。
    """
    registry_key: ClassVar[str | None] = None
    """注册表键回写——``Runtime`` 注册本类时写入（``register_agent_type``
    与文件派生注册两处）：``ns::name`` 全限定键；从未注册的手写子类为
    ``None``。与 ``Tool.registry_key`` / ``Skill.registry_key`` 同构，
    供 ``add_agent`` 落账 ``name_ori``（文件派生记派生限定键，热路径
    精确命中）。内部 API，不属稳定契约。
    """
    subagent_catalog_template: ClassVar[str | None] = None
    """Agent 级 catalog 模板覆写槽位（``<available_subagents>`` 块）。

    **Agent 对象自己有该属性则用其值，否则用
    :data:`flowing.subagents.DEFAULT_SUBAGENT_CATALOG_TEMPLATE`**（用户
    原话裁决）；``.fya`` 中同名字段亦可（落入实例属性 / ``_extra``，
    ``getattr`` 同样命中）。值是 Jinja2 模板源字符串（含
    ``$./file.j2`` FILE_REF 形式），上下文变量表见
    :data:`flowing.subagents.DEFAULT_SUBAGENT_CATALOG_TEMPLATE`。
    """
    _id_prefix: ClassVar[str] = "agent"
    """``node_id`` 前缀（``runtime-*`` / ``workflow-*`` / ``agent-*``
    共享 ID 空间，看 ID 即知节点类型）。内部 API，不属稳定契约。
    """

    # ────────────────────────── 实例属性：标识与关系 ──────────────────────

    node_id: str
    """全局唯一 ID（``agent-`` + UUID），生命周期内不可变；恢复路径
    ``node_id = agent_id``（身份连续、可重现）。
    """
    runtime: Runtime
    """返指 Runtime（对象图根）；不构成循环引用——关闭时 Runtime 主动
    遍历销毁子树。内部 API，不属稳定契约。
    """
    _parent_id: str
    """父节点 ID **字符串**（非对象引用）。双重用途：① 销毁时父→子
    定位；② provide 链子→父上溯（``inject_from``）。创建时绑定、终身
    不变的历史事实——根 Agent 指向 Runtime 的 ``node_id``
    （``"runtime-0"``）；id 不分死活，节点存活与否由 ``_nodes`` 在册
    与否表达，不由本字段（S-12 衍生裁决）。内部 API。
    """
    _children: dict[str, Agent]
    """生命周期子树（``node_id → 子 Agent 实例``），**静态结构**：创建加入、
    销毁移除；回答「谁该随我销毁」。与 ``_executions`` / provide 链
    正交，不可合并。内部 API。
    """
    _child_ids: dict[str, str]
    """语义名 → agent_id 翻译表（S-34 裁决）：``invoke_subagent(
    resume=...)`` 的按名查找载体——**语义名只存在于唤起方（父 Agent）
    的这张表里**（simplename 字段已删除，子实例不自持名字；A15 裁决）。
    与 ``_children`` 的分工：``_children`` 只装**活着的**实例（生命周期，
    以 node_id 为 key），本表装**历史事实**
    （创建登记、destroy 不删——destroy ≠ 删除，记录保留，带记忆续接
    依赖它）。持久化为框架核心状态键 ``child_ids``（写透整表）。
    未命名子 Agent（创建时 ``name=None``）不入表（无法按名续接，
    语义自洽）。**只增不改**（destroy 路径）：不提供删除通道（彻底遗忘
    一个子代 = 删除其 session 目录的外部运维动作）；显式遗忘经
    ``Runtime.archive_agent`` 受控清理——父存活时同步移除指向被归档
    子代的条目并写透（对「只增不改」的唯一例外）。内部 API。
    """
    _provided: dict[str, Any]
    """provide 存储；``inject(key)`` 沿 ``_parent_id`` 链上溯至此（终点
    为 ``Runtime._provided``）。敏感信息（``user_id`` / 凭证派生值）
    走本通道——不进消息流、不进 LLM 上下文、不落盘。内部 API。
    """

    # ────────────────────────── 实例属性：核心结构 ────────────────────────

    hooks: HookRegistry
    """实例级钩子注册表——全部钩子只对当前实例生效；预填核心钩子点 +
    ``declare()`` 扩展点。``@on()`` 声明在 ``_init_hooks()``
    （``__init__`` 阶段）注册，``setup()`` 中 ``self.hooks.<point>(...)``
    注册的在其后。
    """
    prompt_blocks: PromptBlockList
    """system prompt 分层组装列表（ManagedList 语义）；注册顺序即拼接
    顺序；``[0]`` 是 ``system_prompt`` 的惰性引用块（``by="core"``）。
    动态内容的正确做法是块内模板引用（惰性求值保证最新），而非每
    回合 append + remove_by_tag。
    """
    _message_queue: MessageQueue
    """每实例独立的消息队列；按 ``priority`` 排序、同级 FIFO 消费；
    ``dequeue()`` 阻塞。内部 API。
    """
    _messages: dict[str, Message]
    """消息级树的内存索引（``id → Message``）——**唯一权威**；持久化
    文件是它的 append 投影。fork 目标合法性（``in self._messages``）
    与 ``_assemble_context`` 的 ``parent_id`` 上溯都经它。内部 API。
    """
    chain: MessageChain
    """消息级树手术入口（``insert`` / ``branch`` / ``remove`` / ``update`` /
    ``reparent`` 五 op，最小完备集）；运行期改内存链 + append 变更
    记录，压缩期重写。
    """
    current_head_id: str | None
    """消息级树游标——指向**某条消息的 id**，即「添加节点的位置」：
    ``_append_message`` 把新消息链到它并随即将它前移（``_assemble_context``
    从它沿 ``parent_id`` 上溯，回合内新消息当轮可见）；fork 即切换它。
    空树（新 Agent）为 ``None``。
    """
    current_turn: TurnContext | None
    """当前活跃逻辑 Turn 的执行期临时对象；``_run_turn`` 入口赋值、
    **finally 开头**（``after_turn`` 钩子之前）置 ``None``。
    ``None`` 语义 = 回合物质上不存活（不再产生消息）；快照投影
    （``TurnContextInfo``）只在执行期间可见。destroy 不以它为守卫
    （见 :meth:`destroy`）。
    """
    last_result: Any
    """最近一次逻辑 Turn 的最终产出（文本或 finish 结构化输出）；父
    Agent 侧由 ``invoke_subagent`` 运行段读取它构造
    ``SubagentResult.result``。未产生过结果为 ``None``。

    填充规则（``_run_turn`` 统一收尾段，resolve waiters **之前**写入，
    等待方醒来即见本轮产物）：``turn.finish_output`` 非 None → 该 dict
    （finish 结构化交卷）；否则 → ``TurnResult.final_text``（本轮最后一
    条 PROVIDER 消息的 TextBlock 拼接），为空串则写 ``None``。abort /
    cancel / error 路径同样覆写——已置位 finish_output → 载荷照返；
    有完整本轮 PROVIDER 消息 → 其文本；皆无 → ``None``（取消/异常信息
    由 ``SubagentResult.subagent_status`` 承载，不进内容位）。
    ``side_query`` 不写本字段（:meth:`side_query`）。
    """
    _executions: dict[str, Execution]
    """执行追踪注册表（**动态结构**：仅「有正在运行的异步操作」时有
    条目）；注册/清理 finally 成对；``cancel`` / ``stop`` 族遍历它
    置位信号。回答「谁在运行、谁可取消」。内部 API。
    """
    _pending_turns: dict[str, asyncio.Future[TurnResult]]
    """``message_id → Future`` 等待句柄，**纯运行时不持久化**（future
    不可序列化；恢复后的回合没有等待者）；命名沿用历史，与物理
    turn 无关——key 是消息 id。出队绑定 / 收尾 resolve 规则见模块
    docstring。内部 API。
    """
    _measured_tool_names: set[str]
    """本进程内已被实测覆盖过的工具规范名集——``provider_gen()`` 每次收到带
    ``usage`` 的响应时，把当时 ``Context.tools`` 的名字并入。仅服务于
    :meth:`estimate_context_tokens` 的「锚点后新增工具补估 schema」
    规则；**不持久化**（重启后清空，首次估计把全部工具算进
    ``estimated`` 而暂时偏高，下一次带实测的 provider_gen 自愈）；纯内存、
    不进快照。内部 API。
    """

    # ────────────────────────── 实例属性：资源绑定与模型 ──────────────────

    _tool_entries: dict[str, ToolEntry]
    """工具绑定条目（key 为**别名**）；``tool_call()`` 仅按别名查找。
    内部 API。
    """
    _subagent_entries: dict[str, SubagentEntry]
    """子 Agent 绑定条目（key 为**别名**）；catalog 渲染与
    ``invoke_subagent`` 的 resolve 依据。内部 API。
    """
    model: ModelConfig
    """模型结构体（运行时成员，可解析、可变）；实例化时由 ``model_tag``
    解析填充初始值。两条动态修改路径：① 改 ``model_tag``（只能指向
    配置已定义模型）；② 直接赋 ``ModelConfig(...)``（任意模型）。
    「换模型」= 换一份完整规格，无中间态。
    """

    # ────────────────────────── 实例属性：控制信号（内部） ────────────────

    _pause_gate: asyncio.Event
    """工作循环协作式 gate，初始 set（放行）；``pause()`` clear /
    ``resume()`` set。三个检查点（dequeue / provider_gen / toolcall 前）
    均 ``await`` 它，且每处 pause 在前、abort 在后。内部 API。
    """
    _turn_abort: asyncio.Event
    """每个逻辑 Turn 独立的退出信号（``_run_turn`` 入口新建）；置位后
    由三检查点 / ``provider_gen()`` 开头 / 工具包装层检测。内部 API。
    """
    _loop_task: asyncio.Task
    """常驻工作循环 Task 的具名句柄。**构造不在 ``__init__``**——由
    create / recover 管线第 10 步赋值（``instance._loop_task =
    asyncio.create_task(instance._work_loop())``）；``destroy()``
    第 2 步经本句柄 ``cancel()`` 它。内部 API。
    """

    # ─────────────────── 实例属性：持久化（内部，S-31 裁决） ──────────────

    _session_dir: Path
    """本 Agent 的 session 持久化目录（``tree.jsonl`` / ``state.jsonl`` 所在目录）。

    **管线预绑**：``Runtime.create_agent(session_dir=...)`` 指定（绝对路径原样 /
    相对 ``runtime._persist_dir``），缺省 ``persist_dir / node_id``；恢复路径从池
    元数据 ``session_dir`` 字段回绑。``__init__`` 的 :meth:`_open_stores` 使用；
    子类可在 ``super().__init__()`` 之前覆写本字段实现「初始化时指定」。
    内部 API，不属稳定契约。
    """
    _tree_store: RecordStore
    """``tree.jsonl`` 的落盘后端（write-behind：``submit`` 同步排队、
    drain 任务串行落盘；墓碑压缩在 store 内部自主
    触发）。构造在 ``__init__``（经 :meth:`_open_stores`，P3-03：
    session 目录由管线第 2 步预绑的 ``runtime`` / ``node_id``（或
    ``create_agent(session_dir=...)``）给出，骨架期即可知）。内部 API，不属稳定契约。
    """
    _state_bag: StateView
    """唯一状态袋（``state.jsonl`` 后端内嵌，``merge_last_line=True``）；
    :attr:`state` property 的落点；``__setattr__`` / ``__delattr__``
    的防遮蔽检查也查本袋（撞键抛 ``StateKeyError``）。构造点同为
    ``__init__``（经 :meth:`_open_stores`）。内部 API。
    """

    # ────────────────────────── 构造与内部初始化 ──────────────────────────

    def __init__(self) -> None:
        """同步骨架（不可 await）——只做赋值，不做任何 I/O。

        **内部 API，不属稳定契约。** 实例化不由用户直接调用：创建走
        ``Runtime.create_agent`` 管线（前处理已分配 ``node_id`` /
        ``runtime`` / ``_parent_id``），恢复走 ``Runtime.recover_agent``。

        **初始化分工总原则（S-26 用户裁决）**：一切**核心必要**的 Agent
        初始化（框架自身运转必需的空结构、队列、树手术入口、钩子注册
        等）全部放在 ``__init__``；只有**开发者自己引入**的逻辑（装配
        args、注册业务钩子、启用 Composable）放在 :meth:`setup`。
        ``__init__`` 不依赖任何外部输入，因此 create 与 recover 两条
        管线得到完全相同的骨架。

        .. rubric:: 行为规约

        职责清单（顺序不敏感，全部同步）：

        - 赋值 ``_children`` / ``_provided`` / ``hooks`` /
          ``prompt_blocks`` / ``_message_queue`` / ``_messages`` /
          ``chain`` / ``_executions`` / ``_pending_turns`` /
          ``_tool_entries`` / ``_subagent_entries`` 等空结构；
          ``current_turn = None``、``current_head_id = None``、
          ``_pause_gate`` 初始 set。
        - 建立 ``_extra``（实例级静默仓库）与**持久化后端**——经
          :meth:`_open_stores`（换装点）。管线第 2 步（``__new__`` 绑
          ``node_id`` / ``runtime`` / ``_parent_id`` / ``_session_dir``）先于
          ``__init__`` 执行，session 目录（``_session_dir``——
          ``create_agent(session_dir=...)`` 指定或默认
          ``persist_dir / node_id``）骨架期即可知（P3-03 裁决，
          「骨架阶段尚不可知」的旧表述作废）。
          两后端建立即完成，``register_state`` 在随后的 ``setup()`` 中
          天然可用（写闸门仍锁，解锁归管线完成点）。
        - 注入 ``system_prompt`` 惰性引用块（``prompt_blocks[0]``，
          ``by="core"``）。
        - 调 ``_init_hooks()``（同步注册 ``@on()`` 声明的钩子）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent._init_hooks()``（时机：``__init__``
          阶段，同步注册 ``@on()`` 声明的钩子）
        - 被调：``flowing.runtime.Runtime.create_agent`` /
          ``flowing.runtime.Runtime.recover_agent`` 管线（时机：同步
          骨架阶段，不可 await；前处理已分配 ``node_id`` / ``runtime``
          / ``_parent_id``）

        .. seealso:: :meth:`setup`、:meth:`_init_hooks`、
            :meth:`flowing.runtime.Runtime.create_agent`
        """
        # P3-03 裁决（用户）：_extra 与状态袋在 __init__ 建立——管线第 2 步
        # （__new__ 绑 node_id / runtime / _parent_id）先于 __init__，
        # session 目录骨架期即可知，「尚不可知」的旧表述作废
        self._extra = {}   # 实例级静默仓库；fya 装配层在生成 setup() 前置段合入未知字段
        self._open_stores(self._session_dir)   # 持久化后端（换装点，见 _open_stores；session 目录由管线预绑——create_agent(session_dir=...) 或默认 persist_dir/node_id，子类可于 super().__init__() 前覆写 self._session_dir）
        self._children = {}
        self._child_ids = {}   # 语义名 -> agent_id 翻译表（S-34）；持久化镜像见 _open_stores
        self._provided = {}
        self.hooks = HookRegistry()
        self.prompt_blocks = PromptBlockList()
        self._message_queue = MessageQueue()   # 无参：纯内存优先级队列
        self._messages = {}
        self.chain = MessageChain(self)   # S-26：构造收属主 Agent——五 op
        # 直接操作 Agent._messages 并经 Agent._persist_message 落盘
        self._executions = {}
        self._pending_turns = {}
        self._tool_entries = {}
        self._subagent_entries = {}
        self.current_turn = None
        self.current_head_id = None
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

        类创建时执行：``source_file is _UNSET`` → 从 ``__module__.__file__``
        推算为 ``@/`` 格式（失败 → ``None``）；非 ``_UNSET`` → 尊重显式值
        （含 ``None``；``.fya`` 装配层显式注入的值优先级最高）。

        .. rubric:: 调用关系（审计）

        - 调用：无
        - 被调：Python 类创建机制（时机：每次子类定义；``.fya`` 装配层
          显式注入 ``source_file`` 优先级最高）

        .. seealso:: :attr:`source_file`
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
        # Runtime 不存在 / 测试直接定义子类）时推算失败 -> None（R-03 相邻
        # 占位：runtime 批次的 _current_project_root 就绪后本路径自动生效）
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
        """回退查找链：``_extra``（内部 API，不属稳定契约）。

        ``name in self._extra`` → 返回 ``_extra[name]``（原值返回，不做
        隐式 Parsable 解包）；否则 ``AttributeError``。**状态量不在回退
        链上**（P3-03 配套裁决：读写统一走 ``agent.state`` 显式视图，
        ``agent.xxx`` 只对应普通实例属性与 ``_extra``）。

        .. rubric:: 调用关系（审计）

        - 调用：无（仅查 ``_extra`` 表）
        - 被调：无（属性查找失败时由 Python 机制触发；扩展读 ``_extra``
          字段的通道）

        .. rubric:: 递归护栏（P3-03 裁决）

        体内探表**只经 ``self.__dict__.get``，不经属性查找**——子类
        ``__init__`` 在 ``super().__init__()`` 之前赋值等场景下
        ``_extra`` 尚未建立，护栏使回退路径整体短路，干净抛
        ``AttributeError`` 而非 RecursionError。
        """
        extra = self.__dict__.get("_extra")   # 护栏：未建立 -> 跳过（不经属性查找，天然无递归）
        if extra is not None and name in extra:
            return extra[name]   # 原值返回，不做隐式 Parsable 解包
        raise AttributeError(name)

    def _init_hooks(self) -> None:
        """把 ``@on()`` 声明的钩子注册到实例 ``hooks``（同步）。

        **内部 API，不属稳定契约。** ``__init__`` 阶段执行，早于
        ``setup()``——``before_create`` 只能经 ``@on('before_create')``
        声明的原因（setup 尚未运行时无法 ``self.hooks`` 注册）。

        .. rubric:: 行为规约（收集原语，S-29 定稿）

        - 扫 ``type(self).__mro__`` 逐名解析：每个属性名以**派生优先**
          取最终定义（子类覆写未标记的同名方法，基类的标记**不生效**——
          覆写即覆盖，与 Python 方法解析一致）。
        - 最终定义带 ``__flowing_hooks__`` 的成员：按绑定方法逐条记录
          注册，顺序**基类 → 派生类**（同名钩子点上父类 handler 先挂）。
        - **两段式注册**：钩子点已存在（核心预填点）→ 立即注册；尚未
          声明（插件点）→ 记入 ``self.hooks._pending_on``，待
          :meth:`flowing.hooks.HookRegistry.declare` 冲刷（``@on``
          handler 天然排最前）。
        - 结算：``setup()`` 后 PENDING 检查发现 ``_pending_on`` 非空 →
          ``UnknownHookPointError``（见 ``Runtime.create_agent`` 管线
          第 6 步）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.hooks.HookList.__call__`` /
          ``PatternRegistrar.__call__``（时机：立即注册路径，每条记录
          一次）；``HookRegistry._pending_on`` 暂记（时机：钩子点未
          声明路径）
        - 被调：``flowing.agent.Agent.__init__``（时机：同步骨架阶段，
          早于 ``setup()``）

        .. seealso:: :func:`flowing.hooks.on`、:attr:`hooks`
        """
        # S-29 收集原语：MRO 派生→基类方向判覆写（每个名字只认最派生的
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
            # spec 未写清处落实（「按绑定方法注册」与 dispatch 的 (agent, value)
            # 统一签名冲突——绑定方法会多收一个位置参数）：注册**未绑定函数**，
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

    def _open_stores(self, session_dir: Path) -> None:
        """建立本 Agent 的两个持久化后端（**内部 API，不属稳定契约**）。

        .. rubric:: 功能介绍

        构造点为 ``__init__``（P3-03 裁决：管线第 2 步已绑 ``node_id`` /
        ``runtime`` 与 ``_session_dir``——经 ``create_agent(session_dir=...)``
        指定或默认 ``persist_dir / node_id``，session 目录骨架期即可知，故
        后端随骨架建立，不再独立成管线阶段）：

        - ``_tree_store = FileRecordStore(session_dir / "tree.jsonl")``
          （``session_dir`` 即管线预绑的 :attr:`_session_dir`）；
        - ``_state_bag = StateView(FileRecordStore(session_dir /
          "state.jsonl", merge_last_line=True))``。

        .. rubric:: 行为规约

        - 调用时点固定：``__init__`` 首段（两管线同一骨架，各一次）。
        - 建立后 ``register_state`` 的声明落进 ``_state_bag`` 的
          defaults 表；写闸门仍锁（解锁归管线完成点，见
          :class:`flowing.persistence.StateView`）。
        - 幂等性不要求（骨架保证单次）；重复调用属用法错误。
        - 本方法是后端**换装点**（模块 docstring「后端演进缝」）：
          未来非文件后端在此替换 ``FileRecordStore`` 构造（字段标注
          保持契约形态 :class:`flowing.persistence.RecordStore`）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.persistence.FileRecordStore()`` 两次与
          ``flowing.persistence.StateView()``（时机：两后端建立）
        - 被调：``Agent.__init__``（时机：骨架首段，session 目录由
          管线第 2 步预绑的 :attr:`_session_dir` 给出——``create_agent(
          session_dir=...)`` 指定或默认 ``persist_dir / node_id``）

        .. seealso:: :meth:`_restore`、:meth:`register_state`、
            :class:`flowing.persistence.FileRecordStore`
        """
        self._tree_store = FileRecordStore(session_dir / "tree.jsonl")
        self._state_bag = StateView(
            FileRecordStore(session_dir / "state.jsonl", merge_last_line=True),
            owner=self)   # owner 回引用：写透后通知 watcher 通道
        # 框架核心键登记（裸名保留，插件声明撞之报错）：child_ids =
        # 语义名 -> agent_id 翻译表（S-34），默认空表、不落盘
        self._state_bag._register("child_ids", {})

    def register_state(self, key: str, default: Any = None) -> StateView:
        """声明一个持久化状态键（setup 中的「建表」动作；单袋化最终裁决）。

        .. rubric:: 功能介绍

        声明「本 Agent 有一个叫 ``key`` 的状态量」及其默认值。**一个
        Agent 一袋状态键**（无命名空间）；插件键名按约定带注册名
        underscore 前缀（如 cron 插件的 ``cron_jobs``），框架核心键
        裸名。声明后 ``agent.state.key`` 即刻可读
        （default 回退），但**写闸门锁定**——create 管线在「初始
        state 写盘」后解锁，recover 管线在 :meth:`_restore` 完成后
        解锁。**声明是可选的**（P3-04 裁决：袋无 schema——闸门开后
        直接 ``agent.state.x = v`` 即写入，无需声明；声明的价值是
        default、声明期冲突检测与自我文档化）。

        .. rubric:: 设计动机

        「这个 agent 有哪些状态量」是**这个 agent 类的定义的一部分**，
        与它的工具、钩子点同级，所以声明长在 ``setup()`` 里（两管线
        都跑、都先于恢复重放，声明永远先于重放就位）。Runtime 不再有
        全局注册表——每 agent 一个 state.jsonl、单 writer，无需跨
        agent 协调。

        .. rubric:: 使用示例

        .. code-block:: python

            async def setup(self, **args):
                self.register_state("tracker_count", 0)

        .. rubric:: 行为规约

        - **不支持重复声明**：同 key 再次声明 → 报错（setup 每实例只跑
          一次——recover 管线在新实例上重跑 setup，注册表随实例重建，
          天然无重复）。
        - **声明期冲突检测**：key 撞 Agent 类属性 / 方法、``_extra``
          键、已注册状态键 → 立即报错（同名歧义挡在声明期；运行期
          读写唯一通道是 ``agent.state``）。
        - 框架核心键裸名保留（如 ``current_head_id``），插件声明撞
          核心键 → 报错。
        - ``default`` **不落盘**（不产生 ``state.jsonl`` 行）；读出时
          按 ``持久值 ?? default`` 回退；recover 时落盘值覆盖默认值。
        - 调用时点约定为 ``setup()`` 内（default 在重放前就位）；管线
          之外（after_create 之后）声明新键同样允许，无可见性缺口
          （P3-04：重放无 schema 装袋，迟到声明不丢当次旧值）。

        :param key: 状态键名（插件按约定带注册名 underscore 前缀）。
        :param default: 默认值；不落盘，读出回退，recover 时被落盘值覆盖。
        :return: 本 Agent 的 :class:`StateView`（单袋，写闸门状态随管线）。

        .. rubric:: 测试案例

        - 前置：setup 声明 ``"tracker_count"`` → 操作：同 key 再次
          ``register_state`` → 期望：报错。
        - 前置：声明与 Agent 方法同名的键（如 ``"message"``）→ 期望：
          报错。
        - 前置：声明撞框架核心键（如 ``"current_head_id"``）→ 期望：
          报错。

        .. rubric:: 调用关系（审计）

        - 调用：无（``_state_bag`` 由 ``__init__`` 经 ``_open_stores``
          建立，本方法仅向 defaults 表落声明）
        - 被调：无框架内调用方（公共 API，约定在 ``setup()`` 中由用户/
          插件调用；``flowing.plugins.cron.use_cron`` 于 setup 中经
          ``self.register_state("cron_jobs", [])`` 调用，时机：插件启用）

        .. seealso:: :attr:`state`、:class:`StateView`、:meth:`_restore`
        """
        # 冲突检测：撞类属性/方法、_extra 键、已注册状态键、核心保留键 -> 报错
        # （撞类属性/方法与核心键清单的判定在本方法；已注册状态键查
        #   _state_bag._defaults）
        # R-12 落实：类属性/方法用 hasattr(type(self), key) 探测；核心保留键
        # 清单为模块级 _CORE_STATE_KEYS（与 _open_stores 实际登记对照）
        if hasattr(type(self), key):
            raise ValueError(f"状态键与类属性/方法同名: {key!r}")
        if key in _CORE_STATE_KEYS:
            raise ValueError(f"状态键撞框架核心保留键: {key!r}")
        extra = self.__dict__.get("_extra")
        if extra is not None and key in extra:
            raise ValueError(f"状态键撞 _extra 键: {key!r}")
        self._state_bag._register(key, default)   # 声明落进单袋 defaults 表（重复声明报错）
        return self._state_bag

    @property
    def state(self) -> StateView:
        """本 Agent 唯一状态袋的视图（显式读写通道）。

        .. rubric:: 功能介绍

        钩子 handler / 工具 / 插件代码里以 **agent 视角**读写持久化状态的
        **唯一通道**（P3-03 配套裁决：读写统一走显式视图，不再经
        ``agent.xxx`` 回退）：``agent.state.cron_jobs`` 属性式，
        ``agent.state["weird-key"]`` 字典式。写透后 dispatch 属主
        watcher 通道（``watch`` 照常生效，触发点在 :class:`StateView`）。

        .. rubric:: 设计动机

        语义上每个状态量就是该智能体的一个特殊属性；把访问做成
        「Agent 身上的视图」而非 Runtime 上的 ``store[agent_id][key]``，
        是因为插件改状态的现场几乎都在钩子里、手里拿的是 agent——不应
        再回插件对象找句柄、拼 id。

        .. rubric:: 行为规约

        读写语义（写透、defaults 回退、写闸门、fail fast）全部继承
        :class:`StateView` 的类级契约。本 property 自身无副作用。

        .. rubric:: 测试案例

        - 前置：setup 声明 ``register_state("tracker_count", 0)`` →
          操作：钩子中 ``agent.state.tracker_count += 1`` → 期望：写透
          落盘，崩溃恢复后值在；``watch("tracker_count")`` 照常触发。
        - 前置：未声明 ``"tracke"``（typo）→ 操作：``agent.state[
          "tracke"]`` → 期望：``KeyError``；``agent.tracke`` →
          ``AttributeError``。

        .. rubric:: 调用关系（审计）

        - 调用：无（返回单袋视图，无副作用）
        - 被调：无框架内固定调用方（插件钩子 handler 内调用，如
          ``flowing.plugins.cron`` 经 ``agent.state`` 读写，时机：每次
          状态读写）；:meth:`get` / :meth:`set` / :meth:`delete`
          的状态键分支（时机：key 为已注册状态键）

        .. seealso:: :meth:`register_state`、:meth:`get`、:meth:`set`、
            :meth:`delete`、:class:`StateView`
        """
        return self._state_bag   # 单袋视图（_open_stores 建立，无副作用）

    @property
    def _state(self) -> dict[str, Any]:
        """已注册状态键 → 现场值的字典视图（**内部 API，不属稳定契约**）。

        R-6 承接：``flowing.parsable.Parsable._do_resolve`` 摊平渲染上下文时
        经 ``getattr(agent, "_state", None)`` 读取状态键袋——本 property 是真
        Agent 侧的对齐落点（骨架期袋未建立时返回空表）。键集 = 已声明键 ∪
        已持久键；写闸门未开时读仅见 defaults（StateView 读语义）。
        """
        bag = self.__dict__.get("_state_bag")
        if bag is None:
            return {}
        keys = set(bag._defaults) | set(bag._persisted)
        return {k: bag[k] for k in keys}

    def get(self, key: str, default: Any = None) -> Any:
        """动态键统一读——状态键 / 实例与类属性 / ``_extra`` 三域兼容。

        .. rubric:: 功能介绍

        字符串键的通用读取入口（key 是变量的场景：插件写通用逻辑、
        slash 命令、调试工具）。查找序：已注册状态键 → ``agent.state``
        读出（``持久值 ?? default``）；否则 ``getattr``（实例 ``__dict__``
        → 类属性 → ``_extra`` 回退，与属性协议完全同序）；仍无 →
        返回 ``default``。

        .. rubric:: 行为规约

        - 三域互斥由 ``register_state`` 声明期冲突检测保证（状态键撞
          类属性 / 方法 / ``_extra`` 键 → 声明即报错），查找序无歧义。
        - 骨架期护栏（P3-03）：``_state_bag`` 只经 ``__dict__.get``
          探查，未建立 → 跳过状态域。
        - 读``_extra`` 原值返回，不做隐式 Parsable 解包（同
          ``__getattr__``）。

        :param key: 字符串键。
        :param default: 三域均未命中时的返回值（默认 ``None``）。

        .. rubric:: 测试案例

        - 前置：声明 ``register_state("count", 0)`` 且实例属性
          ``self.mode = "x"`` → 期望：``get("count") == 0``、
          ``get("mode") == "x"``、``get("nope", -1) == -1``。

        .. seealso:: :meth:`set`、:meth:`delete`、:attr:`state`
        """
        bag = self.__dict__.get("_state_bag")   # 护栏（P3-03）
        if bag is not None and key in bag:
            return bag[key]   # 状态域：持久值 ?? default
        return getattr(self, key, default)   # 属性域 + _extra 回退（__getattr__）

    def set(self, key: str, value: Any) -> None:
        """动态键统一写——状态键 / ``_extra`` / 实例属性三域路由。

        .. rubric:: 行为规约

        - 已注册状态键 → ``self.state[key] = value``（写透落盘 + 写闸门
          + JSON 校验，写后由 :class:`StateView` dispatch
          watcher 通道——watch 照常）。
        - ``key in _extra`` → 原地更新 ``_extra[key]``（静默仓库，不
          触发 watcher 通道）。
        - 否则 ``setattr(self, key, value)``——普通实例属性路径
          （``__setattr__`` 的 watcher 通知照常）。
        - 骨架期护栏同 :meth:`get`。

        .. rubric:: 测试案例

        - 前置：声明 ``"count"`` → 操作：``set("count", 5)`` → 期望：
          ``state.count == 5`` 且 ``watch("count")`` 触发；
          ``set("mode", "y")`` → 实例属性，watcher 通知照常。

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

        .. rubric:: 行为规约

        - 已注册状态键 → ``del self.state[key]``（删持久值，读回退
          default；不触发 watcher——删除不是赋值事件）。
        - ``key in _extra`` → 移除该键。
        - 否则 ``delattr(self, key)``（普通实例属性删除；类属性 /
          方法删不掉，``AttributeError`` 原样上抛）。
        - 三域均未命中 → ``AttributeError``（经 delattr 末路）。
        - 骨架期护栏同 :meth:`get`。

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
        **恢复管线同样调用本方法**（args 为持久化值，可被 ``recover_agent``
        的 ``override_args`` 覆盖，触发的是 ``before_recover`` /
        ``after_recover`` 钩子对）。签名即 args 来源（与 ``args_model``
        对照：``.fya`` 显式 ``args:`` 优先，签名仅作校验对照——参数必须
        有对应字段、类型标注必须兼容；``.fya`` 未写 ``args:`` 时签名是
        ``args_model`` 的推导来源，见 :attr:`args_model`）。

        .. rubric:: 设计动机

        ``setup()`` ↔ ``destroy()`` 是对等对（各自被 before/after 钩子
        包裹）；``create_agent()`` 不是 destroy 的对偶——它是工厂管线。
        轻量约束让创建 / 恢复廉价（agent 池现场恢复、并行编排依赖大量
        快速创建）。创建与恢复共用同一装配入口：恢复不另立 ``recover()``
        方法，两条管线的差异全部由钩子对与管线步骤表达。

        **与 ``__init__`` 的分工（S-26 用户裁决）**：核心必要的初始化
        全部在 ``__init__``（无外部输入，同步骨架）；``setup`` 只承载
        **开发者引入**的装配逻辑——依赖 args 与 inject 的赋值、业务钩子
        注册、Composable 启用。

        .. rubric:: 使用示例

        .. code-block:: python

            async def setup(self, user_id: int, order_id: str, locale: str = "zh"):
                self.user_id = user_id
                self.order_id = order_id
                self.locale = locale
                self.provide("user_id", user_id)     # 敏感信息走注入通道
                use_retry(self, max_retries=2)       # 可选 Composable（幂等注册）
                self.watch("locale", self._on_locale_change)

        .. rubric:: 行为规约

        - **可重入（统一契约）**：``setup()`` 在 create 与 recover 两条管线
          **各跑一次**——两次一定是**不同实例**（recover 管线是新建实例上
          重跑，注册表随实例重建），因此契约是「不同实例上每次调用作用
          相同」而非「同一实例上重复执行不出错」：
          ① ``setup()`` 重建一切运行期结构（declare 钩子点、注册
          handler、provide、Composable），这些都幂等——``declare`` 同名
          + 同 ``by`` 幂等返回已有 ``HookList``（见
          :meth:`flowing.hooks.HookRegistry.declare`），幂等 Composable
          （如 ``use_retry``）同参数重复调用去重；
          ② 普通实例属性赋值（``self.user_id = user_id``）是运行期配置，
          **不落盘、不加限制**；
          ③ 持久化 state 经 :meth:`register_state` 声明、:meth:`state`
          访问；**setup 中禁写 state**——写闸门在管线到位前锁定（写则
          抛错），初始值一律走 ``defaults``（不落盘、读出回退，
          recover 时落盘值天然优先，从机制上不存在「setup 写入覆盖
          未重放记录」的窗口）；
          ④ 副作用操作（创建文件、操作外部对象）由用户自行做存在性校验，
          框架不兜底。
        - 期待行为：状态赋值、钩子注册、inject 读取、Composable 调用。
        - 非行为：网络请求 / 文件 I/O / 大量计算——移到工具调用或按需
          阶段；重资源获取走 Resource（惰性按需）。
        - 边缘情况：第一个 ``await`` 之前的代码不被其它协程打断——关键
          初始化放第一个 ``await`` 前；需让出控制权显式
          ``await asyncio.sleep(0)``。
        - 后置条件：返回后框架做 PENDING 检查——仍有 PENDING 字段（如
          必填 ``system_prompt`` 未赋值）→ ``MissingFieldError``。

        .. rubric:: 测试案例

        - 前置：``.fya`` 声明 ``system_prompt: _`` 且 ``setup`` 未赋值
          → 操作：``create_agent`` → 期望：PENDING 检查点抛
          ``MissingFieldError``，``after_create`` 不触发。
        - 前置：``setup()`` 内 ``declare`` + ``use_retry`` → 操作：
          ``create_agent`` 后 ``recover_agent``（新建实例上重跑
          ``setup()``）→ 期望：钩子点与 handler 均不重复注册（幂等），
          行为与单次执行一致。

        .. rubric:: 调用关系（审计）

        - 调用：子类覆写自定（框架无固定调用；轻量约束见行为规约）
        - 被调：``flowing.runtime.Runtime.create_agent`` 管线（时机：
          ``before_create`` 之后、PENDING 检查之前）、
          ``flowing.runtime.Runtime.recover_agent`` 管线（时机：
          ``before_recover`` 之后，重跑本方法，可重入契约）

        .. seealso::

            - :meth:`flowing.runtime.Runtime.recover_agent` —— 恢复管线
              宿主（重跑本方法）。
            - :meth:`destroy` —— 对等销毁入口。
            - :class:`flowing.parsable.PENDING` —— 延迟定义哨兵
              （见 :mod:`flowing.parsable`）。
        """
        # 基类空实现（契约注释）：子类覆写承载装配逻辑——状态赋值、钩子注册、
        # inject 读取、Composable 调用；**禁写 state**（写闸门在管线到位前
        # 锁定）；可重入语义见上文「行为规约」（create 与 recover 各跑一次、
        # 必为不同实例）。
        ...

    async def destroy(self) -> None:
        """递归销毁管线入口——实例丢弃，session 记录保留。

        .. rubric:: 功能介绍

        销毁本实例及其整个生命周期子树。时序（不变量顺序）：

        1. resolve 所有 ``_pending_turns``（``TurnResult(status="cancelled")``）
           ——调用方不挂起；
        2. 取消工作循环 Task → **关闭两个持久化后端**（``_tree_store`` 与
           ``_state_bag`` 的 ``close()`` = drain 排空 + 停写任务——
           write-behind 契约②钉死的排空屏障点，不排空即销毁会静默丢
           尾部记录）；
        3. dispatch ``before_destroy``；
        4. 深度优先递归 ``child.destroy()``（子树收集双来源：``_children``
           ∪ ``_nodes`` 按 ``_parent_id`` 扫描——覆盖「destroy 后现场
           恢复」重新注册的同 id 新实例），清空 ``_children``；
        5. 从 ``_nodes`` 摘除（池移除实例值）——死活由在册与否表达；
        6. dispatch ``after_destroy``。

        ``_parent_id`` **不**在销毁时改写：id 是创建时绑定的历史事实
        （「父是谁」不因销毁变成「无父」），本字段不承担死活语义
        （S-12 衍生裁决）。

        .. rubric:: 设计动机

        - **destroy ≠ 删除**：只丢实例；session（tree.jsonl +
          state.jsonl）与池 key 保留到显式删除目录——「有 key 无 value
          → 现场恢复」（``Runtime.get_agent`` 触发），匿名子 Agent
          销毁后仍可带记忆恢复。
        - 生命周期严格绑定：父销毁 → 子递归销毁；Agent 实例不跨任务
          复用（跨任务复用的是 Resource）。

        .. rubric:: 行为规约

        - 幂等：重复调用安全（二次调用时子树已空、已摘除，直接返回）。
        - 与回合收尾窗口的关系：destroy 不以 ``current_turn`` 为守卫，
          回合收尾观察窗口（``after_turn`` 钩子运行期间）调用 destroy
          合法——取消工作循环 Task 可能中断 finally 的交付段，但第 1 步
          会把 ``_pending_turns`` 全部 resolve（cancelled），等待者
          不挂起；``after_turn`` 钩子可能被中断跳过，调用方须自知。
        - 销毁后实例不可用：``inject`` / ``message`` / ``tool_call`` 等
          行为无契约保证（三正交结构均已清空）。
        - 非行为：不删除 session 目录、不移除池 key、不持久化任何
          「已销毁」标记——记录保留是显式语义。

        .. rubric:: 测试案例

        - 前置：两个 ``query()`` 等待中。操作：``await agent.destroy()``
          → 期望：两个 future 均 resolve ``status="cancelled"``，不挂起。
        - 前置：父含两级子树。操作：``await parent.destroy()`` → 期望：
          深度优先全部销毁，``_nodes`` 中三个 id 均无实例值，池 key 保留。

        .. rubric:: 调用关系（审计）

        - 调用：``before_destroy`` / ``after_destroy`` dispatch（时机：
          管线第 3/6 步）、子 Agent ``destroy()``（时机：第 4 步深度
          优先递归）
        - 被调：``flowing.runtime.Runtime.shutdown``（时机：进程级收尾
          递归 destroy）、父 Agent ``destroy()``（时机：父销毁时递归
          销毁子树）

        .. seealso::

            - :meth:`flowing.runtime.Runtime.get_agent` —— 现场恢复入口。
            - :meth:`flowing.runtime.Runtime.shutdown` —— 进程级收尾
              进程级收尾（递归 destroy 的调用方）。
        """
        # 幂等守卫（spec 行为规约：重复调用安全，二次调用「直接返回」）：
        # **本实例**已摘除即二次调用，直接返回——按身份比较而非 id：本 id
        # 可能已被「有 key 无 value → 现场恢复」重建为新实例重新注册，
        # 旧实例（如父级 _children 里的过期引用）不得再操作已关闭的后端
        if self.runtime._nodes.get(self.node_id) is not self:
            return
        for fut in list(self._pending_turns.values()):   # 1. resolve 所有 pending
            if not fut.done():
                fut.set_result(TurnResult(
                    turn=TurnContext(started_at=datetime.now(), message_ids=[]),
                    final_text="", status="cancelled", token_usage=None,
                    finish_reason="cancelled"))
        self._pending_turns.clear()
        # 2. 取消工作循环 Task（具名句柄 _loop_task，由管线第 10 步赋值）；
        # 幂等与骨架期护栏：loop 未启动（__dict__ 无句柄）时跳过
        loop_task = self.__dict__.get("_loop_task")
        if loop_task is not None:
            loop_task.cancel()   # cancel 注入点 = 工作循环当前悬停的 await
            with contextlib.suppress(asyncio.CancelledError):
                await loop_task   # 等循环 finally 落地后再关后端（防关库后提交）
        await self._tree_store.close()   # 2b. 排空屏障 + 停写任务（契约②钉死点）
        self._state_bag._maybe_compact(force=True)   # 2c. 压缩三时点②：destroy 收尾（请求随 _close 排空一并执行）
        await self._state_bag._close()
        await self.hooks.before_destroy.dispatch(self)   # 3.
        # 4. 深度优先递归（双来源收集：_children 生命周期子树 ∪ _nodes 按
        #    _parent_id 扫描——后者覆盖「destroy 后现场恢复」重新注册的同 id
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
        """从自己 session 目录重放持久化记录（**只有 recover 管线调用**）。
        **内部 API，不属稳定契约**。

        功能与动机：读自己目录的 ``tree.jsonl`` 逐行重建**消息级树**
        （``Message.id`` + ``parent_id`` 链；撕裂末行丢弃——重放前
        按首行 ``format_version`` 判读版本，低版本经
        :data:`flowing.persistence.MIGRATIONS` 迁移链升级并回写，
        见 :mod:`flowing.persistence`「格式版本与迁移」）→ 读
        ``state.jsonl`` 恢复框架核心键最小集（``current_head_id``
        指向最后持久化消息 + 队列待消费消息 + 池元数据）→ 将全部
        持久化行重放进单袋状态（逐键覆盖 default）。恢复边界 =
        已持久化消息；进行中的逻辑 Turn 丢弃不续跑。完成后解开
        :class:`StateView` 的写闸门。纯数据之外派生的运行时结构
        （如 cron 的定时器）由随后的 ``after_recover`` 钩子重建——
        恢复回调不再是 ``register_state`` 的参数（单袋化最终裁决）。

        行为边界：调用时点固定在 recover 管线的 ``setup()`` / PENDING
        检查之后、``after_recover`` 与 ``_nodes`` 注册之前；session 目录
        不存在（池元数据与目录不一致）时按空 session 处理并报出可诊断
        错误。物理格式逻辑（行结构 / 末行合并）在内部
        persistence 模块，本方法只是驱动者。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.persistence.RecordStore.replay()``（时机：
          tree.jsonl 与 state.jsonl 两路重放，经 ``_tree_store`` 与
          ``_state_bag._store``）
        - 被调：``flowing.runtime.Runtime.recover_agent`` 管线（时机：
          ``setup()`` / PENDING 检查之后、``after_recover`` 与
          ``_nodes`` 注册之前——只有 recover 管线调用）

        .. seealso:: :meth:`flowing.runtime.Runtime.recover_agent`、
            :meth:`register_state`、:class:`StateView`、
            :class:`flowing.message.Message`
        """
        # ① self._tree_store.replay() 逐行重建消息级树（Message.id +
        #    parent_id 链；消息行建树、tombstone/update/move 变更行按序
        #    做手术；撕裂末行由 replay 截断丢弃；悬空变更行（墓碑压缩后
        #    目标可缺失）跳过容忍——不当作 corruption）
        if not self._session_dir.exists():
            # 池元数据与目录不一致：按空 session 处理 + 可诊断告警
            _logger.warning("agent %s: session 目录不存在，按空 session 恢复: %s",
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
        # 框架核心键最小集：current_head_id 指向最后持久化消息（由树重放
        # 行序推导；state.jsonl 无对应存储键——推导规则见 spec 措辞，
        # 「队列待消费消息」无持久化记录源，恢复后为空队）
        self.current_head_id = order[-1] if order else None
        # ①b 孤立 tool_call 合成占位（M-25 恢复扫描，配对锚为消息字段）：
        #    逐分支扫描 PROVIDER 消息的 ToolCallBlock.id，同分支后续无
        #    tool_call_id 匹配的 TOOL 消息者，合成占位消息挂树封闭配对：
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
                    text=f"工具调用 {call_id} 的结果缺失（工具执行中崩溃），"
                         "恢复时合成的占位消息。")])
            placeholder.parent_id = provider_id
            # insert 式挂树（纯内存，不落盘——下次恢复以同一确定性 id 重新
            # 合成，语义幂等且 parent 链自愈）：provider 消息的既有直接子消息
            # 重挂到占位消息之下，保证配对占位出现在「调用之后、既有后续之前」
            # 的链上位置；上次恢复后追加的消息（parent_id 已是占位 id）经
            # 确定性 id 天然接回
            for child in list(self._messages.values()):
                if child.parent_id == provider_id and child.id != placeholder.id:
                    child.parent_id = placeholder.id
            self._messages[placeholder.id] = placeholder
            if self.current_head_id == provider_id:
                # provider 消息本是分支尾：占位消息成为新尾，head 随之上移
                self.current_head_id = placeholder.id
        # ②③ 读 state.jsonl（_state_bag._store.replay()）逐键重放进单袋
        #    （写闸门未开，直写 _persisted 绕过写通道；无 schema：持久化键
        #    无论声明与否一律装袋，P3-04 裁决——逐键覆盖 register_state 的
        #    default 是读出回退序的天然结果）
        persisted = self._state_bag._persisted
        for record in list(self._state_bag._store.replay()):
            op = record.get("op")
            if op == "set":
                persisted[record["key"]] = record["value"]
            elif op == "delete":
                persisted.pop(record["key"], None)
            # 未知行形态（meta 已被 replay 吸收）静默跳过
        # ③b S-34：重放出的 child_ids 直接装入 _child_ids 内存镜像
        #    （S-34 追加裁决：闸门未开不支持创建子代 -> setup 阶段镜像
        #    必为空，装入无合并冲突）
        replayed_child_ids = persisted.get("child_ids")
        if isinstance(replayed_child_ids, dict):
            self._child_ids.update(replayed_child_ids)
        # ④ 完成后解开 StateView 写闸门（_state_bag._write_gate_open = True）
        object.__setattr__(self._state_bag, "_write_gate_open", True)
        # ④b 压缩三时点①：恢复重放后请求 state.jsonl 全量压缩
        #    （_state_bag._maybe_compact(force=True)；物理重写在 drain 任务）
        self._state_bag._maybe_compact(force=True)
        # ⑤ 派生运行时结构（cron 定时器等）由随后的 after_recover 钩子重建

    # ────────────────────────── 消息进入与等待 ────────────────────────────

    async def query(
        self,
        content: str | list[ContentBlock],
        kind: MessageKind = MessageKind.USER,
        **kwargs: Any,
    ) -> TurnResult:
        """统一入口：打包 + 入队 + 等待「包含我这条消息的回合」产物。

        .. rubric:: 功能介绍

        ``content: str`` 自动打包为 ``[TextBlock(text=content)]``；
        ``list`` 直接作为 ContentBlock 列表。``kind`` 默认 ``USER``，可传
        ``SYSTEM`` / ``EVENT`` / ``PEER`` 等。``**kwargs`` 透传给 Message
        构造（``source`` / ``priority`` / ``tags`` 等）。

        .. rubric:: 设计动机

        两个驱动场景：① Workflow 代码编排需要同步请求-响应（免自拼
        ``after_turn`` 钩子 + id 关联 + 等待清理的样板，避免并发误删 /
        钩子改写 id 错位）；② 进程内同步式 UI 适配器可直接 ``await``。
        等待语义（``query``）与 fire-and-forget（``enqueue_message`` /
        ``message``）分离。

        .. rubric:: 使用示例

        .. code-block:: python

            result = await agent.query("帮我查订单 ORD-12345")
            assert result.status == "completed"

            # 副线（不 commit、不落盘，返回 str）走 side_query：
            text = await agent.side_query("总结以上对话")

            # PEER 消息（对等 Agent 有意图地主动发送）
            await peer.query("库存已变更", kind=MessageKind.PEER,
                             source=f"agent:{self.node_id}")

        .. rubric:: 行为规约

        - 等待语义：等「包含我这条消息」的逻辑回合完成，返回该回合
          ``TurnResult``；空闲时可能被 drain 合并（共享产物），活跃回合
          中排队等当前回合完成。
        - 副线不走本方法——副线唯一入口是 :meth:`side_query`（
          ``Message.side`` 字段已删除，「副线」是调用路径属性而非
          消息属性）。
        - 取消等待 ≠ 取消回合：``await`` 被取消时消息已在队列（可能已
          执行），取消只是不领结果（``finally`` 清理 ``_pending_turns``
          条目）；撤回未出队消息用 :meth:`cancel_queued`。
        - **死锁禁止（P3-01 裁决）**：在当前回合的调用栈内（任何钩子、
          工具 ``execute``、provider_gen 期间的 await 点）``await query()``
          必死锁——回合收尾要等钩子返回，钩子要等下一回合产物，下一
          回合要等当前回合收尾。跨 Agent 等待同理：等待图成环（A 等
          B、B 等 A）即分布式死锁，**框架不做环检测**。
        - 回合内需要驱动，用 :meth:`steer`（STEER 优先级，检查点 ②.5
          吸收、当轮 context 可见）；:meth:`enqueue_message` /
          :meth:`message` 入队的非 STEER 消息回合内不可察觉，只在
          回合间消费。确需跨 Agent ``await query()`` 的，等待图无环
          （DAG）由开发者保证。
        - 崩溃 / ``destroy()`` 都不挂起调用方（resolve cancelled）。
        - 前置条件：实例未被 ``destroy()``。

        .. rubric:: 测试案例

        - 前置：Agent 空闲。操作：``await agent.query("hi")`` → 期望：
          返回 ``TurnResult``，``_pending_turns`` 最终为空（finally
          清理）。
        - 前置：活跃回合中。操作：两个 ``query()`` 先后调用 → 期望：
          各自 resolve 到**各自**回合的 ``TurnResult``（逐条 pop 按
          各自 id，插队不影响关联）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent.enqueue_message()``（时机：打包后
          入队）
        - 被调：``flowing.agent.Agent.invoke_subagent``（时机：
          ``await child.query(prompt)`` 等待产出）、
          ``flowing.plugins.workflow.Workflow`` 编排代码（时机：每次
          驱动 Agent）、``flowing.plugins.cron``（时机：cron 触发投递
          ``action.prompt``）

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
        fut: asyncio.Future[TurnResult] = asyncio.get_running_loop().create_future()
        self._pending_turns[msg.id] = fut   # P3-01：注册先于入队——消息对外可见时句柄必已存在
        try:
            message_id = await self.enqueue_message(msg)   # 打包 + 入队
            return await fut   # 等待「包含我这条消息的回合」产物（四结局均 resolve）
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

        .. rubric:: 设计动机

        与 :meth:`query` 的等待语义互补：不需要回合产物的投递（通知、
        事件、steer 注入）不应付出 ``_pending_turns`` future 注册成本；
        但调用方手上多半只有散装 content，手工打包是纯样板。本方法只
        封装打包一步，入队语义与 :meth:`enqueue_message` 完全一致。

        .. rubric:: 使用示例

        .. code-block:: python

            message_id = await agent.message("稍后提醒我喝水")
            # 需要回合产物（最终文本 / token 用量）时改用：
            result = await agent.query("帮我查订单 ORD-12345")

        .. rubric:: 行为规约

        - fire-and-forget：不注册 ``_pending_turns``、不等待任何回合；
          返回值仅是消息 id，可用于 :meth:`cancel_queued` 撤回。
        - 入队时序（``before_enqueue`` → enqueue → ``after_enqueue``）与
          :meth:`enqueue_message` 完全一致；``Intercepted`` 原样上抛。
        - 非行为：不返回 ``TurnResult``——想要回合产物请用
          :meth:`query`。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent.enqueue_message()``（时机：打包后
          入队）
        - 被调：``flowing.agent.Agent.steer``（时机：固定
          ``priority=MessagePriority.STEER`` 委托）；应用层通知 / 事件
          投递

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
        """注入 steer 消息（不等回应）：``message(..., priority=MessagePriority.STEER)`` 的特例。

        .. rubric:: 功能介绍

        回合进行中向 Agent 追加导向信息（补充约束、修正方向、追加资料）。
        固定 ``priority=MessagePriority.STEER``，fire-and-forget，返回
        ``message_id``。

        .. rubric:: 设计动机

        「不打断、只递话」是 agent 交互的高频定式；优先级常量由框架
        钉死，调用方不必记忆枚举值。

        .. rubric:: 使用示例

        .. code-block:: python

            await agent.steer("预算上限改为 500，别直接下单")

        .. rubric:: 行为规约

        - 吸收语义（见 :meth:`_run_turn` 检查点 ②.5）：STEER 消息在内层
          循环每轮 ``provider_gen`` 前被 drain 挂树，**当轮** context
          即可见；不打断当前回合（对比 ``INTERRUPT`` 的 abort 语义）。
        - fire-and-forget：不注册 ``_pending_turns``、不等待回合产物。
        - 非行为：不保证被哪个回合消费——若当前回合已收尾，由下一
          回合的首轮吸收。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent.message()``（时机：每次调用，
          固定 ``priority=MessagePriority.STEER``）
        - 被调：应用层「不中断的途中干预」入口

        .. seealso::

            - :meth:`message` —— 散装直发的一般形。
            - :meth:`_run_turn` —— STEER 吸收语义（检查点 ②.5）。
        """
        return await self.message(content, priority=MessagePriority.STEER, **kwargs)

    async def enqueue_message(self, msg: Message) -> str:
        """纯入队（fire-and-forget），返回 ``message_id``，不等待结果。

        .. rubric:: 功能介绍

        接收**已构造的** ``Message`` 对象（打包责任上移到 ``query()`` /
        ``message()`` 或调用方）。远程用户 / 第三方 / Cron / 异步工具最终结果的统一
        投递入口。

        .. rubric:: 设计动机

        与 ``query()`` 的等待语义分离：不需要结果的投递（Cron 广播、
        通知、异步最终结果）不应付出 future 注册成本。本方法是协程——
        入队前 dispatch ``before_enqueue``（handler 可为 async）。

        .. rubric:: 使用示例

        .. code-block:: python

            msg = Message(kind=MessageKind.EVENT, source="tool_result",
                          content=[TextBlock(text="异步任务完成")],
                          priority=MessagePriority.HIGH)
            message_id = await agent.enqueue_message(msg)

        .. rubric:: 行为规约

        - 时序：dispatch ``before_enqueue``（可检查 / 修改 /
          ``raise Intercepted`` 拒绝——内容审核、速率限制、文件过大）→
          ``_message_queue.enqueue(msg)`` → dispatch ``after_enqueue``
          （纯观察，日志 / 审计）→ 返回 ``msg.id``。
        - 消费保证：入队即会被消费（常驻工作循环），无需「入队触发」逻辑。
        - 七类入队：USER / EVENT / SYSTEM / PLUGIN / SUBAGENT / TOOL
          （仅异步最终结果，以 ``EVENT`` kind 入队——content = 标注块 +
          结果块列表，结果块由 ``flowing.tool.output_to_blocks`` 塑形，
          EVENT 不参与 TOOL 配对）/ PEER；``PROVIDER`` **永远不进队列**
          （回合内产生）。
        - 优先级插队只影响消费顺序，不影响 ``_pending_turns`` 关联
          （逐条按 id pop）。
        - :raises flowing.errors.Intercepted: ``before_enqueue`` handler
          拒绝入队时原样上抛。

        .. rubric:: 测试案例

        - 前置：``before_enqueue`` handler 对含违规词的消息 ``raise
          Intercepted`` → 操作：``await agent.enqueue_message(msg)`` →
          期望：抛出 ``Intercepted``，队列长度不变，``after_enqueue``
          不触发。

        .. rubric:: 调用关系（审计）

        - 调用：``before_enqueue`` / ``after_enqueue`` dispatch 与
          ``flowing.message.MessageQueue.enqueue()``（时机：见本方法时序
          规约）
        - 被调：``flowing.agent.Agent.enqueue_messages``（时机：逐条
          委托）、``flowing.agent.Agent.query`` / ``flowing.agent.Agent.message``
          （时机：打包后）

        .. seealso::

            - :meth:`enqueue_messages` —— 批量入队。
            - :meth:`query` —— 等待语义的封装。
            - :meth:`message` —— 散装箱糖。
            - :class:`flowing.message.MessageQueue` —— 排序与阻塞语义。
        """
        msg = await self.hooks.before_enqueue.dispatch(self, msg)   # 可检查/修改；Intercepted 原样上抛
        self._message_queue.enqueue(msg)
        await self.hooks.after_enqueue.dispatch(self, msg)   # 纯观察（日志/审计）
        return msg.id

    async def enqueue_messages(
        self, msgs: Message | list[Message], **kwargs: Any
    ) -> list[str]:
        """批量入队（接受单条或列表），返回 ``message_id`` 列表。

        .. rubric:: 行为规约

        - 逐条委托 :meth:`enqueue_message`（每条独立经过
          ``before_enqueue`` / ``after_enqueue``）；任一条被
          ``Intercepted`` 时异常上抛，已入队的不回滚。
        - 返回顺序与输入顺序一致。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent.enqueue_message()``（时机：逐条
          委托，每条独立经过 ``before_enqueue`` / ``after_enqueue``）
        - 被调：无

        .. seealso:: :meth:`enqueue_message`
        """
        if isinstance(msgs, Message):
            msgs = [msgs]
        ids: list[str] = []
        for m in msgs:
            ids.append(await self.enqueue_message(m))   # 逐条委托；Intercepted 上抛，已入队不回滚
        return ids

    def cancel_queued(self, message_id: str) -> bool:
        """撤回一条**未出队**的排队消息。

        .. rubric:: 功能介绍

        从 ``_message_queue`` 移除指定消息；若该消息有 ``query()``
        等待者（``_pending_turns`` 条目），联动 resolve
        （``TurnResult(status="cancelled")``，turn 字段为框架合成的空
        ``TurnContext``）并移除条目——调用方不挂起。

        .. rubric:: 设计动机

        「取消等待 ≠ 取消回合」的配套撤回通道：消息还没被消费时允许
        反悔；已出队则木已成舟，走 ``abort_turn()`` / ``cancel()``。

        .. rubric:: 行为规约

        - 返回 ``True``：消息在队列中，已移除（等待者已联动 resolve）。
        - 返回 ``False``：消息已出队（正在或已被回合消费）或不存在——
          不做任何动作。
        - 同步方法：不 dispatch 钩子、不 await；future 的 ``set_result``
          同步完成。
        - 非行为：不影响其它排队消息；不触碰 ``current_turn``。

        .. rubric:: 测试案例

        - 前置：Agent 活跃回合中，消息 m 排队且有等待者 → 操作：
          ``agent.cancel_queued(m.id)`` → 期望：返回 ``True``，等待者
          resolve ``cancelled``，队列中无 m。
        - 前置：m 已被出队 → 操作：同上 → 期望：返回 ``False``，
          等待者仍由回合收尾正常 resolve。

        .. rubric:: 调用关系（审计）

        - 调用：无（``_message_queue`` 移除与 future ``set_result`` 同步
          完成，不 dispatch 钩子）
        - 被调：无

        .. seealso:: :meth:`query`、:meth:`abort_turn`、:meth:`set_queued_priority`
        """
        removed = self._message_queue.remove(message_id)   # 同步方法，不 dispatch 钩子
        if not removed:
            return False   # 已出队或不存在：不做任何动作
        fut = self._pending_turns.pop(message_id, None)
        if fut is not None and not fut.done():
            fut.set_result(TurnResult(   # 联动 resolve cancelled（框架合成空 TurnContext）
                turn=TurnContext(started_at=datetime.now(), message_ids=[]),
                final_text="", status="cancelled", token_usage=None,
                finish_reason="cancelled"))
        return True

    def set_queued_priority(self, message_id: str, priority: MessagePriority) -> bool:
        """重设一条**未出队**排队消息的优先级（队列立即重排）。

        .. rubric:: 功能介绍

        :meth:`flowing.message.MessageQueue.set_priority` 的 Agent 层入口：
        把排队中的消息提升 / 降低优先级，影响下一轮 ``_dequeue()`` 的消费
        顺序。与 :meth:`cancel_queued` 对称——那个管「反悔撤回」，这个管
        「催办 / 降级」。

        .. rubric:: 设计动机

        重排**保留原入队序号**（用户裁决）：被改优先级的消息插入新优先级带
        时按原入队早晚定位，如同它入队时就带着新优先级——而非排到该带
        末尾。改优先级无等待者牵连（消息仍会被正常消费、正常 resolve），
        故本方法是纯转发，无联动逻辑。

        .. rubric:: 使用示例

        .. code-block:: python

            agent.set_queued_priority(msg_id, MessagePriority.HIGH)   # 催办

        .. rubric:: 行为规约

        - 返回 ``True``：消息在队列中，``priority`` 已改写并重排。
        - 返回 ``False``：消息已出队（正在或已被回合消费）或不存在——
          不做任何动作。
        - 同步方法：不 dispatch 钩子、不 await。
        - 非行为：不影响正在执行的回合与 ``current_turn``；不改写消息
          其它字段（``id`` / ``timestamp`` 等）。

        .. rubric:: 测试案例

        - 前置：NORMAL 消息 m 排队中，其后无其它排队消息 → 操作：
          ``agent.set_queued_priority(m.id, MessagePriority.HIGH)`` →
          期望：返回 ``True``，下一轮 ``_dequeue`` 先于其它 NORMAL
          消息消费 m。
        - 前置：m 已出队 → 操作：同上 → 期望：返回 ``False``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.message.MessageQueue.set_priority``（时机：
          每次调用本方法，纯转发）
        - 被调：无

        .. seealso:: :meth:`cancel_queued`、:meth:`enqueue_message`
        """
        return self._message_queue.set_priority(message_id, priority)   # 纯转发，无等待者牵连

    def estimate_context_tokens(self) -> ContextUsageEstimate:
        """估计当前上下文占用的 token 数（锚点实测 + 尾部估算，纯观测）。

        .. rubric:: 功能介绍

        回答「现在把上下文发给 LLM 大约多大」：沿 ``current_head_id`` 上溯
        的当前路径上，找最近一条**有效锚点**（``kind == PROVIDER`` 且
        ``usage is not None`` 且 ``usage.total_tokens > 0`` 的消息），
        锚点覆盖部分用实测值（``measured``），之后的新内容用
        :func:`flowing.message.estimate_message_tokens` 逐条估算
        （``estimated``）；无有效锚点时全段估算（含 system prompt 与工具
        schema，见行为规约）。

        .. rubric:: 设计动机

        混合计数模型（kimi-code 锚点账本 / pi 锚点+尾部估算的 Flowing 化，
        用户确认引入）：实测管「已发送的过去」，估算管「从未被实测的现在」
        ——锚点到下一次 provider_gen 之间积累的内容（工具结果、新入队消息、注入）
        没有实测可用，而余量显示与（未来）压缩判断恰恰落在这个窗口。
        **不维护持久锚点账本**：每次现场沿链扫描，锚点随 fork / 树手术
        自动迁移（锚点消息被 ``chain.remove`` 后次近者自然顶上），无陈旧
        锚点问题；代价是 O(路径长) 扫描，符合「无本地缓存、现场求值」
        原则。

        .. rubric:: 使用示例

        .. code-block:: python

            est = agent.estimate_context_tokens()
            print(est.tokens, est.usage_ratio)   # 比率不 clamp，>1 即溢出信号

        .. rubric:: 行为规约

        - 同步、纯读取、无副作用：不 dispatch 钩子、不改任何状态。
        - 路径收集口径与 ``_assemble_context()`` 相同（沿 ``current_head_id``
          上溯）。
        - 锚点命中时：``measured = 锚点.usage.total_tokens``（恒等式保证
          含 cache_read——缓存读的 token 也占窗口）；``estimated`` 追加
          「当前启用工具中不在 ``_measured_tool_names`` 的 schema 估算」
          （``llm_definition()`` JSON 序列化 ÷ 4）——锚点后新增工具的
          补估规则。
        - 无锚点时：``measured = None``，``estimated`` = system prompt 各
          segment 文本估算 + 当前全部启用工具 schema 估算 + 路径全部
          消息估算。
        - ``context_window`` 取 ``self.model.resolve(self).context_window``
          （现场求值；模型未声明为 ``None``，此时 ``usage_ratio`` 为
          ``None``）。
        - 非行为：不设阈值、不触发压缩、不告警（策略归插件）；不缓存
          结果；估算值**永不用于计费**；不做「溢出后学习收紧上限」
          （kimi-code 的 overflow 学习属压缩策略，不进核心）。
        - 边缘情况：路径为空 → 全零且 ``measured is None``；锚点消息的
          ``usage.total_tokens == 0``（异常响应）不算有效锚点，继续上溯。

        .. rubric:: 测试案例

        - 前置：路径上有一条 PROVIDER 消息（``usage.total_tokens == 5000``），
          其后挂了两条工具结果 → 期望：``measured == 5000``、
          ``anchor_message_id`` 指向该消息、``tokens == 5000 + 两条估算``。
        - 前置：全新会话（路径无 PROVIDER 消息）→ 期望：
          ``measured is None``，``estimated`` 含 system prompt 与全部
          启用工具 schema。
        - 前置：锚点消息被 ``chain.remove`` → 期望：下次调用锚点落到
          次近的有效 PROVIDER 消息（无陈旧值）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.message.estimate_message_tokens``（时机：对
          估算范围内每条消息）；``flowing.model.ModelConfig.resolve``
          （时机：取 ``context_window``）；当前启用工具条目的
          ``llm_definition()``（时机：schema 估算）
        - 被调：``flowing.agent.Agent.snapshot``（时机：每次快照，结果
          投影进 ``AgentSnapshot.context_usage``）

        .. seealso::

            :class:`ContextUsageEstimate`、
            :func:`flowing.message.estimate_message_tokens`、
            :class:`flowing.providers.Usage`、``Agent.snapshot``。
        """
        # 沿 current_head_id 上溯收集（与 _assemble_context 同口径；
        # 孤儿链断点容忍——上溯到断点即终止，同 MessageChain.remove 的语义）
        path: list[Message] = []
        cursor = self.current_head_id
        while cursor is not None:
            msg = self._messages.get(cursor)
            if msg is None:
                break
            path.append(msg)
            cursor = msg.parent_id
        path.reverse()
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
                if entry.enabled and entry.name_alias not in self._measured_tool_names:
                    estimated += _estimate_tool_schema_tokens(
                        entry.llm_definition(self.runtime, self))
        else:
            # 无锚点全估：system prompt 各 segment 文本 + 全部启用工具
            # schema + 路径全部消息
            for block in self.prompt_blocks:
                estimated += _text_tokens(str(block.content.resolve(self)))
            for entry in self._tool_entries.values():
                if entry.enabled:
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
        """模型标签 → ``ModelConfig`` 的现场解析（**内部 API，不属稳定契约**）。

        R-08 落实（解析器未见具名符号）：``model_tag`` →
        :func:`flowing.model.load_model_tags` 标签映射 → 模型条目名 →
        :func:`flowing.model.load_models` 条目表 → ``ModelConfig``，两跳
        现场求值无缓存。来源路径读 ``runtime._model_tags_path`` /
        ``runtime._models_path``（Runtime 的登记字段，见
        ``Runtime.set_model_tags`` / ``set_models``）；未设定按 Runtime 侧
        默认路径语义属 runtime 批次职责——本方法在两者缺失时报错（fail
        fast，不静默回退）。标签未定义回退 ``default``；``default`` 也未
        定义 → 报错（与 ``model_tag`` 类属性规约一致）。

        本方法是 ``self.model`` 初始解析与 ``side_query(model_tag=...)`` 的
        同一代码路径（R-08 推测方案）；调用方负责把产物落到
        ``self.model``（创建管线 / ``model_tag`` 赋值）或仅作一次性使用
        （副线）。
        """
        tags_path = getattr(self.runtime, "_model_tags_path", None)
        models_path = getattr(self.runtime, "_models_path", None)
        if tags_path is None or models_path is None:
            raise FlowingError(
                f"模型标签 {tag!r} 无法解析：Runtime 未登记 model-tags/models "
                "来源路径（_model_tags_path / _models_path）")
        tags = load_model_tags(Path(tags_path))
        entry_name = tags.get(tag) or tags.get("default")
        if entry_name is None:
            raise FlowingError(
                f"模型标签 {tag!r} 未定义且 model-tags 缺 default 兜底")
        models = load_models(Path(models_path))
        config = models.get(entry_name)
        if config is None:
            raise FlowingError(
                f"模型标签 {tag!r} 指向的模型条目 {entry_name!r} 不在 models 表中")
        return config

    async def provider_gen(
        self, context: Context, *, stream: bool = True, by: str | None = None
    ) -> ProviderResponse:
        """单次模型调用——双模式（流式 / 非流式）的唯一入口。

        .. rubric:: 功能介绍

        时序：``_turn_abort`` 检查（置位 → 直接返回
        ``ProviderResponse(message=None, finish=False, cancelled=True)``，
        **不抛异常**）→ ``self.model.resolve(self)`` 字段级惰性求值 →
        provider 懒获取（``provider_registry.get(model.provider)``）→
        dispatch ``before_provider_gen``（可改写完整 ``Context``）→ Provider
        调用（注册 ``Execution(kind="request")``）→ dispatch
        ``after_provider_gen``（可改写 ``ProviderResponse``）→ 返回。

        .. rubric:: 设计动机

        - **流式挡在回合循环外**：无论底层是否流式，本方法返回**完整**
          ``ProviderResponse``——``_run_turn`` 只见完整消息，工具调用 /
          abort / 收尾逻辑与流式正交。
        - **模型当场解析**：每次调用前重新 ``resolve`` + 懒取 provider——
          钩子在回合中改 ``model_tag`` / ``self.model`` 后，同 Turn 内
          下一次请求立即生效。
        - **无候选列表、无内置重试、无 fallback**：单模型、单 provider、
          单次调用；错误处理在回合层面（``on_provider_error``）。

        .. rubric:: 使用示例

        .. code-block:: python

            # 默认流式；显式关闭
            response = await self.provider_gen(context, stream=False)

        .. rubric:: 行为规约

        - ``stream=True``（默认，S-17 最终裁决：显式参数决定，不由
          订阅者存在性决定）：流式路径——逐 delta 累积进
          ``Message.content`` 并经 ``on_provider_delta`` dispatch
          （**纯观察，调用时序保证**：累积只认 Provider 原始 delta，
          dispatch 的返回值被本方法丢弃、不回写——改写单条 delta
          无意义，需改写走 ``after_provider_gen`` 改整条消息；delta 本身
          volatile 不落盘）；
          ``stream=False``：非流式一次性请求，拿到完整响应后合成
          **一条**全量 delta 同样 dispatch——两种路径的 delta 数据
          格式完全一致（用户裁决），订阅者永远能依赖「每次 provider_gen
          至少一条 delta」。
        - ``by``：来源标记，透写到每条 ``ProviderDelta`` 与返回的
          ``ProviderResponse``（adapter 不填，由本方法盖写）。主 Turn
          内层循环传 ``"_turn"``，``side_query`` 传 ``"_side"``；
          下划线开头为框架保留值，插件自定义来源勿用。``after_provider_gen``
          与 ``on_provider_delta`` 钩子点以 ``match_on="by"`` 声明，
          handler 可按来源模式过滤注册；``before_provider_gen`` 的 value 是
          ``Context``，暂不携带 ``by``（如需过滤，后续给 Context 加
          字段再开——当前注释约定）。
        - 流式中断（abort）：已累积内容保留为 ``partial=True`` 的消息
          随响应返回（**保留落盘**，对应中断保留行为），
          ``cancelled=True``；取消点起不再 dispatch delta。
        - 不变量：不捕获任何异常——Provider 异常分类（``RateLimitedError``
          等）原样上抛给 ``_run_turn``；``ContextLengthError`` 同样上抛
          （且不经过 ``on_provider_error``）。
        - 非行为：不重试、不缓存、不聚合用量——turn 级聚合是
          ``_run_turn`` 的职责（追加 ``TurnContext.usages``）；本方法
          只保证响应消息上附着的 ``message.usage`` 原样抵达
          ``after_provider_gen`` 钩子与调用方（S-13 裁决：无消费者不等于丢弃）。
        - 边缘情况：abort 于 Provider 调用期间——adapter 检测信号返回
          空响应而非抛异常（cancel 是正常终止不是错误）。

        .. rubric:: 测试案例

        - 前置：无 ``on_provider_delta`` 订阅者 → 操作：``provider_gen(context)``
          → 期望：仍走流式路径（默认 ``stream=True``），delta dispatch
          空表为空转。
        - 前置：``provider_gen(context, stream=False)`` → 期望：非流式请求，
          且 ``on_provider_delta`` 恰好收到一条全量 delta（格式与流式
          路径一致）。
        - 前置：``_turn_abort`` 已置位 → 操作：``provider_gen(context)`` →
          期望：返回 ``finish=False, cancelled=True, message=None``，未
          发起 Provider 调用。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.model.ModelConfig.resolve()``（时机：每次调用
          前当场求值）、provider 懒获取（``provider_registry.get``，
          时机：每次调用）、``before_provider_gen`` / ``after_provider_gen`` /
          ``on_provider_delta`` dispatch（时机：见本方法时序规约）、
          ``Execution(kind="request")`` 注册
        - 被调：``flowing.agent.Agent._run_turn``（时机：内层循环每轮）、
          ``flowing.agent.Agent.side_query``（时机：每次副线调用，
          ``stream=False``）

        .. seealso::

            - :meth:`side_query` —— 副线封装（强制非流式）。
            - :meth:`flowing.providers.Provider.generate` /
              :meth:`flowing.providers.Provider.generate_stream` —— 底层契约。
            - :class:`ProviderErrorContext` —— 异常上抛后的决策上下文。
        """
        if self._turn_abort.is_set():
            return ProviderResponse(message=None, finish=False, cancelled=True)   # abort：不抛异常、不发起 Provider 调用
        model: ModelConfig = self.model.resolve(self)   # 每次调用前字段级惰性求值
        provider: Provider = self.runtime.provider_registry.get(model.provider)   # 懒获取（C-04 裁决：ProviderRegistry，未知条目 KeyError）
        context = await self.hooks.before_provider_gen.dispatch(self, context)   # 可改写完整 Context
        execution = Execution(id=uuid4().hex, kind="request", tags=[],
                              started_at=datetime.now(),
                              cancel=asyncio.Event(), pause=asyncio.Event())
        self._executions[execution.id] = execution   # 注册，finally 清理
        try:
            if not stream:
                # 非流式：一次性请求；拿到完整响应后合成一条全量 delta 同样
                # dispatch（两种路径 delta 数据格式一致，订阅者永远能依赖
                # 「每次 provider_gen 至少一条 delta」）
                response = await provider.generate(context, model)
                if response.message is not None:
                    full_text = "".join(
                        b.text for b in response.message.content
                        if isinstance(b, TextBlock))
                    await self.hooks.on_provider_delta.dispatch(
                        self, ProviderDelta(kind="text", text=full_text,
                                            content_index=0, by=by))
            else:
                # 流式（R-11 落实）：list[ContentBlock] 累积器按
                # content_index 归位——text/thinking delta 逐段拼接进对应块；
                # 结构化内容（工具调用等）由 adapter 在末段以完整块
                # （delta.block）交付，直接归位；usage 由末帧附着进组装消息。
                accumulated: dict[int, ContentBlock] = {}
                final_usage: Usage | None = None
                interrupted = False
                async for delta in provider.generate_stream(context, model):
                    # 取消点起不再 dispatch delta（abort/cancel 均为协作式信号）
                    if self._turn_abort.is_set() or execution.cancel.is_set():
                        interrupted = True
                        break
                    delta.by = by   # 来源标记盖写（adapter 不填、无法伪造）
                    if delta.block is not None:
                        accumulated[delta.content_index] = delta.block
                    elif delta.kind == "thinking":
                        existing = accumulated.get(delta.content_index)
                        thinking = ((existing.thinking if isinstance(existing, ThinkingBlock) else "")
                                    + delta.text)
                        accumulated[delta.content_index] = ThinkingBlock(
                            thinking=thinking)
                    elif delta.text or delta.content_index in accumulated:
                        existing = accumulated.get(delta.content_index)
                        text = ((existing.text if isinstance(existing, TextBlock) else "")
                                + delta.text)
                        accumulated[delta.content_index] = TextBlock(text=text)
                    if delta.usage is not None:
                        final_usage = delta.usage   # 仅末帧携带
                    await self.hooks.on_provider_delta.dispatch(self, delta)   # 纯观察，返回值丢弃不回写
                message = Message(
                    kind=MessageKind.PROVIDER,
                    content=[accumulated[i] for i in sorted(accumulated)],
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
                )
        finally:
            self._executions.pop(execution.id, None)   # finally 清理（不变量：不捕获异常）
        response.by = by   # 响应来源标记盖写（adapter 不填）
        response = await self.hooks.after_provider_gen.dispatch(self, response)   # 可改写 ProviderResponse
        if response.message is not None and response.message.usage is not None:
            # 估算锚点记账：本次实测覆盖了当时 context.tools 的 schema，
            # 名字并入 _measured_tool_names（estimate_context_tokens 的
            # 「锚点后新增工具补估」规则以此差集为准；纯内存不持久化；
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
        ``content`` / ``kind`` / ``**kwargs`` 打包为一条 ``Message``（与
        :meth:`query` 外部视角同构的打包规则：``str`` →
        ``[TextBlock(text=content)]``），追加到本次临时 context，直接
        ``provider_gen(context, stream=False, by="_side")`` 一次，返回响应文本；
        不产生任何持久化痕迹。``model_tag`` 调用时传参（请求前当场解析）；
        ``None`` 时复用 ``self.model``（即 ``model_tag`` 声明的解析结果）。

        .. rubric:: 设计动机

        与主路径（队列 → 回合 → 挂树 → 落盘）完全分离：副线消息（本方法
        内部打包的普通 ``Message``，无 ``side`` 标记——「副线」
        是**调用路径属性**而非消息属性，裁决后 ``Message.side`` 字段删除）
        不进树、不写 tree.jsonl、用完即弃；token 用量由调用方（插件 /
        日志）自行记录，核心不为其持久化任何内容。**对象（agent 实例）
        不是用完即弃**——一个实例承载多次副线调用。固定非流式：副线对
        实时渲染无需求；非流式路径同样合成一条全量 delta（
        ``by="_side"``）经 ``on_provider_delta`` 发出——订阅者可按
        ``by`` 过滤副线来源。

        .. rubric:: 使用示例

        .. code-block:: python

            emotion = (await self.side_query(
                "推断当前情绪，只输出一个词：\\n" + window,
                model_tag="fast")).strip()

        .. rubric:: 行为规约

        - 注册 ``Execution(kind="side_query")``（可被 ``cancel_by_tag`` /
          级联取消命中）；abort 时返回空文本（不抛异常）。
        - 上下文组装基于当前消息级树（``_assemble_context`` 同一机制），
          **与主路径完全一致——不剥离工具 schema、不做任何副线特化**
          （保证 prompt 缓存命中率）；但本次交换的请求 / 响应消息
          **均不挂树**。
        - 响应消费规则（单次交换，无续轮）：返回值 = 仅拼接响应中的
          ``TextBlock.text``；``ThinkingBlock`` 与 ``ToolCallBlock``
          一律丢弃——副线消息不落盘、不再进任何上下文，thinking 无
          passback 义务、工具请求无执行机制。``finish`` 字段不读
          （它是 Turn 循环的结束判断，副线不是 Turn）。
        - 空文本二义性：abort 与「响应无 TextBlock」（如 thinking 烧光
          预算、模型只想调工具）都返回 ``""``，不区分——调用方对空值
          做幂等兜底。
        - 截断：不设独立参数，输出上限由本次调用解析出的
          ``ModelConfig.max_output_tokens`` 决定；``stop_reason=length``
          的被截断文本是合法返回值，不报错。
        - 非行为：不 dispatch turn 族钩子（``before_turn`` 等——副线不是
          逻辑 Turn）；不更新 ``current_head_id``；不触碰
          ``_pending_turns``；不写 ``last_result``；固定非流式
          （``stream=True`` 无入口）；**不记入** turn 用量累加器
          （``TurnContext.usages``——副线不经 ``_run_turn``，
          ``TurnResult.token_usage`` 不含副线消耗；S-13 边缘语义。
          副线用量的观察点是 ``after_provider_gen`` 钩子——副线走
          ``provider_gen()``，钩子照常触发）。
        - 异常：Provider 异常直接上抛调用方（副线无 ``on_provider_error``
          回合层兜底——调用方自行决定重试 / 降级）。

        .. rubric:: 测试案例

        - 前置：Agent 有历史消息 → 操作：``await agent.side_query("……")``
          → 期望：``_messages`` / tree.jsonl / ``current_head_id`` /
          ``last_result`` 均无变化，返回非空字符串。
        - 前置：``model_tag="fast"`` → 期望：本次调用使用 fast 解析的
          模型，``self.model`` 不被改写。
        - 前置：响应为 ``[ThinkingBlock, TextBlock("ok")]`` → 期望：
          返回 ``"ok"``（thinking 不参与返回值）。
        - 前置：响应含 ``ToolCallBlock``（``finish=False``）→ 期望：
          不执行工具、不续轮，返回其中 TextBlock 拼接或 ``""``。
        - 前置：响应仅含 ThinkingBlock（预算烧光）→ 期望：返回 ``""``，
          不抛异常（与 abort 返回空文本同值，调用方兜底）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent._assemble_context()`` 与
          ``flowing.agent.Agent.provider_gen()``（时机：每次副线调用，
          ``stream=False``）；``Execution(kind="side_query")`` 注册
        - 被调：应用层与各插件直接调用（副线唯一入口）

        .. seealso::

            - :meth:`query` —— 主线入口（副线请直接调本方法）。
            - :meth:`provider_gen` —— 底层调用。
        """
        execution = Execution(id=uuid4().hex, kind="side_query", tags=[],
                              started_at=datetime.now(),
                              cancel=asyncio.Event(), pause=asyncio.Event())
        self._executions[execution.id] = execution   # 可被 cancel_by_tag / 级联取消命中
        original_model: ModelConfig | None = None
        if model_tag is not None:
            # R-08 落实：请求前当场解析临时 ModelConfig（_resolve_model_tag），
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
    # 以下方法把「删除当前 head 时 head 回退到父节点」等策略固定在 Agent 上。

    def remove(self, message_id: str) -> None:
        """删除一条消息；若删的是 ``current_head_id``，head 回退到其 ``parent_id``。

        .. rubric:: 功能介绍

        对 :meth:`MessageChain.remove` 的 Agent 层包装。``MessageChain``
        保持纯手术语义（不移动 head）；本方法补上 head 维护：当被删除的
        消息正好是 ``current_head_id`` 时，先把 head 回退到该消息的
        ``parent_id``，再执行删除。``parent_id`` 为 ``None`` 时 head 变为
        ``None``——下一条消息将作为新根挂树。

        .. rubric:: 设计动机

        head 是 Agent 的字段，不应由 MessageChain 直接维护；把「删除当前
        head 时怎么办」的策略收口在 Agent 方法里，MessageChain 继续只做
        「改内存链 + 提交变更记录」的机制。

        .. rubric:: 使用示例

        .. code-block:: python

            agent.remove(msg_id)          # 删普通消息，head 不动
            agent.remove(agent.current_head_id)   # 删当前 head，head 回退

        .. rubric:: 行为规约

        - 同步方法，不 dispatch 钩子。
        - ``message_id`` 不存在时由 :meth:`MessageChain.remove` 抛
          ``KeyError``。
        - 删除非 head 消息时，行为与 :meth:`MessageChain.remove` 完全一致
          （head 不动，子树不级联）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.message.MessageChain.remove``（时机：每次调用，
          完成实际删除与 tombstone 提交）
        - 被调：:meth:`pop`、应用 / 策略层（时机：消息树手术）

        .. seealso:: :meth:`pop`、:meth:`MessageChain.remove`
        """
        if message_id not in self._messages:
            self.chain.remove(message_id)   # KeyError 由 chain.remove 抛出
            return
        if message_id == self.current_head_id:
            self.current_head_id = self._messages[message_id].parent_id
        self.chain.remove(message_id)

    def pop(self) -> str | None:
        """删除 ``current_head_id`` 指向的消息，head 回退到其父节点。

        .. rubric:: 功能介绍

        :meth:`remove` 的 head 专用便捷包装：删除当前 head 并返回被删除
        的消息 id。空树（``current_head_id is None``）时返回 ``None``。

        .. rubric:: 使用示例

        .. code-block:: python

            while agent.current_head_id is not None:
                agent.pop()          # 从最新消息一路删到根；head 最终为 None

        .. rubric:: 行为规约

        - 同步方法，不 dispatch 钩子。
        - 连续 ``pop()`` 会把 head 链从新到旧逐条删除；全部删完后
          ``current_head_id is None``，下一条消息成为新根。

        :return: 被删除的消息 id；空树返回 ``None``。

        .. rubric:: 调用关系（审计）

        - 调用：:meth:`remove`（时机：每次调用，删除当前 head）
        - 被调：应用 / 策略层（时机：上下文重置、消息树回退）

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
        挂入 ``_messages`` → 落盘 → ``current_head_id = msg.id``。
        它也是回合内 :meth:`_append_message` 的**核心写路径**；
        本方法不 dispatch turn 族钩子、不写 ``turn.message_ids``——
        这两部分由 :meth:`_append_message` 在调用本方法前后补齐。

        .. rubric:: 行为规约

        - 同步方法，不 dispatch 钩子。
        - ``msg.id`` 已存在于 ``_messages`` 时抛 ``ValueError``。
        - ``current_head_id`` 为 ``None`` 时，``msg`` 成为新根
          （``parent_id=None``）。

        :return: ``msg.id``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent._persist_message``（时机：每次调用，
          提交新消息行）
        - 被调：应用 / 策略层（时机：回合外挂树）

        .. seealso:: :meth:`pop`、:meth:`_append_message`
        """
        if msg.id in self._messages:
            raise ValueError(msg.id)
        msg.parent_id = self.current_head_id
        self._messages[msg.id] = msg
        self._persist_message(msg)
        self.current_head_id = msg.id
        return msg.id

    def branch(self, msg: Message, parent_id: str | None = None) -> str:
        """把一条消息挂到指定父节点下（``None`` = 新根），不移动 head。

        .. rubric:: 功能介绍

        :meth:`MessageChain.branch` 的 Agent 层透传。调用方传入新消息
        与基点；只创建节点并落盘，不改变 ``current_head_id``。

        .. rubric:: 行为规约

        - 同步方法，不 dispatch 钩子。
        - ``parent_id`` 为 ``None`` 时开新根；非 ``None`` 时须存在。
        - 不移动 ``current_head_id``（需要切过去用 :meth:`fork`）。

        :return: ``msg.id``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.message.MessageChain.branch``（时机：每次调用）
        - 被调：应用 / 策略层（时机：开分支 / 开新根）

        .. seealso:: :meth:`MessageChain.branch`、:meth:`fork`
        """
        return self.chain.branch(parent_id, msg)

    def remove_by_tags(self, tags: set[str]) -> int:
        """按 tags 批量删除消息；透传 :meth:`MessageChain.remove_by_tags`，
        并保持 head 语义。

        .. rubric:: 功能介绍

        先记录当前 head 及其父节点，再透传批量删除；若当前 head 被删除，
        则把 ``current_head_id`` 回退到它删除前的父节点。其余被删消息
        不触发 head 变化。

        .. rubric:: 行为规约

        - 同步方法，不 dispatch 钩子。
        - 匹配 / 删除 / 返回计数语义由
          :meth:`MessageChain.remove_by_tags` 承担。

        :return: 删除条数（透传）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.message.MessageChain.remove_by_tags``
          （时机：每次调用）
        - 被调：应用 / 策略层（时机：按 tags 清洗临时注入消息）

        .. seealso:: :meth:`remove`、:meth:`MessageChain.remove_by_tags`
        """
        head_id = self.current_head_id
        head_parent = None
        if head_id is not None and head_id in self._messages:
            head_parent = self._messages[head_id].parent_id
        removed = self.chain.remove_by_tags(tags)
        if head_id is not None and head_id not in self._messages:
            self.current_head_id = head_parent
        return removed

    # ────────────────────────── fork 与暂停 ───────────────────────────────

    async def fork(self, target_message_id: str) -> None:
        """消息级 fork：把 ``current_head_id`` 切到任意历史消息。

        .. rubric:: 功能介绍

        消息上下文最基本的组织方式（核心机制，非扩展特性）。时序：
        dispatch ``before_fork``（可改写 target /
        ``raise Intercepted`` 阻止）→ 目标合法性检查 → 记录
        ``previous_head_id`` → 切换 ``current_head_id`` → dispatch
        ``after_fork``（纯观察，日志 / 通知 UI 刷新分支列表）。

        .. rubric:: 设计动机

        - **fork 是纯上下文操作，不碰执行**：正在运行的工具 / 子 Agent
          继续运行、结果照常交付；``_executions`` / ``_children`` /
          ``prompt_blocks`` / ``_provided`` / ``_tool_entries`` /
          ``_message_queue`` 均不被触碰（共享同一实例，不拷贝、不冻结）。
        - **全时合法，无守卫**（裁决）：head 即「添加节点的位置」（每条
          消息挂树即前移），回合内 fork 的语义即 seek——本回合后续
          append 与 provider_gen 改在新基址上继续。这是可预想的操作语义而非
          损坏，框架不做家长式禁止；但调用方须自知三件事：① 嫁接——
          fork 后的产物挂在目标所在链上，``turn.message_ids`` 可能跨链
          （消费者均按 id 取消息，无机械故障）；② 上下文瞬移——下一轮
          ``_assemble_context`` 从新 head 上溯；③ 配对断裂——仅当
          fork 落在「tool_call 已挂树、结果未 append」的工具执行相位，
          下一轮组装出「有 result 无 call」，provider 会显式报错（响亮、
          可恢复，append-only 保证数据完好）。
        - **外部改方向的推荐定式**仍是先终止执行再切上下文（时序可控）：
          ``cancel()`` → 等回合收尾完成 → ``fork()`` → 发新消息。
          回合内直接 fork 适合时序可知的钩子内调用者（如 compact）。
        - 上下文压缩 = fork 的应用：压缩把摘要复制为 SYSTEM 新根
          （``chain.branch(None, 副本)``，森林模型）后经本方法切换 head，
          旧分支保留完整历史；何时压缩、如何摘要是 Composable / 应用层
          策略（内置 ``flowing.composables.compact.use_compact``——
          在 ``after_provider_gen`` 的干净点调用：此前配对完整、响应未挂树），
          fork 只提供机制。**开发者责任**：挂 ``before_fork`` handler
          时须显式考虑 compact 换链会经过它，拦截（``Intercepted``）或
          改写 target 会打断 compact 的收尾（建了根没切过去 / head 被
          指向别处）；框架不为此设强制保护，「是否压缩」的拦截点应挂在
          ``on_compact`` 而非 ``before_fork``。
        - 无 LLM 可调 fork 工具：「回合内不分支」约定 + LLM 推理必在
          回合内 → fork 由代码触发（用户 / UI 分支导航、compact 换链）。

        .. rubric:: 使用示例

        .. code-block:: python

            # goal 模式中途改方向的推荐定式：先终止执行，等回合收尾完成，再切上下文
            await agent.cancel()   # 协作式置位——回合自行走向收尾，此调用返回 ≠ 收尾完成
            # …等待回合收尾完成（如 await 该回合 query() 的 TurnResult）…
            await agent.fork(msg_id)

        .. rubric:: 行为规约

        - :raises ValueError: ``target_message_id`` 不在 ``_messages``
          中（含已被 ``chain.remove`` 移除的 id）。
        - :raises flowing.errors.Intercepted: ``before_fork`` handler
          阻止 fork。
        - 回合内 fork（seek 语义）：合法，无 ``RuntimeError`` 守卫；
          后果见设计动机——嫁接 / 上下文瞬移 / 工具相位配对断裂均属
          调用方责任。
        - 非行为：不换 Agent 类型（换类型 = 换身份 = 子 Agent：先 fork
          切回历史点，再 ``create_subagent`` 从该点继承上下文）；不创建
          第二个实例、不在两个分支上同时运行（需要并行用子 Agent）；
          不复制 / 截断 tree.jsonl——只是切换游标。
        - 边缘情况：兄弟分支无顺序信息（互斥分支），UI 排序用
          ``Message.timestamp``。

        .. rubric:: 测试案例

        - 前置：m6 有两条子消息 m7 / m8（分支）→ 操作：
          ``await agent.fork(m8.id)`` → 期望：``current_head_id ==
          m8.id``，``_assemble_context()`` 的路径含 m8 不含 m7。
        - 前置：回合内（钩子中）fork 到历史点 x → 期望：合法；后续
          append 挂在 x 所在链上，下一轮 ``_assemble_context`` 从 x
          上溯；``turn.message_ids`` 跨链（按 id 消费不受影响）。
        - 前置：fork 落在工具执行相位（tool_call 已挂树、结果未
          append）→ 期望：下一轮 provider_gen 组装出配对断裂序列，provider
          显式报错走 ``on_provider_error``；旧链数据完好。

        .. rubric:: 调用关系（审计）

        - 调用：``before_fork`` / ``after_fork`` dispatch（时机：见本
          方法时序规约）
        - 被调：无（回合外由用户 / UI 分支导航触发；回合内由时序可知的
          钩子调用者触发——compact 换链在 ``after_provider_gen`` 调用，见
          ``flowing.composables.compact.use_compact``）

        .. seealso::

            - :attr:`current_head_id` —— 被切换的游标。
            - :class:`flowing.message.MessageChain` —— 修改历史结构的
              手术入口（与 fork 改视角正交）。
        """
        target_message_id = await self.hooks.before_fork.dispatch(self, target_message_id)   # 可改写 target / Intercepted 阻止
        if target_message_id not in self._messages:
            raise ValueError(target_message_id)   # 含已被 chain.remove 移除的 id
        previous_head_id = self.current_head_id
        self.current_head_id = target_message_id   # 纯上下文操作：只切换游标
        await self.hooks.after_fork.dispatch(self, previous_head_id)   # 纯观察（value 为原 head id）

    def pause(self) -> None:
        """协作式暂停：关闭工作循环 gate（``_pause_gate.clear()``）。

        .. rubric:: 功能介绍

        三个检查点（dequeue 前 / provider_gen 前 / 每个 tool_call 前）均
        ``await self._pause_gate.wait()``——回合之间与回合内关键操作前
        都可暂停；**恢复后从暂停点继续**（pause 是挂起等待，可恢复；
        abort 是终止退出，不可恢复）。

        .. rubric:: 行为规约

        - 同步方法，幂等（重复调用无额外效果）；不 dispatch 钩子。
        - 暂停期间队列不丢消息——可继续 ``enqueue_message`` 调整方向，
          恢复后按序消费；暂停期间可查看上下文（快照 / 消息树遍历）。
        - 优先级：``pause()`` 与 abort 同时发生时**先挂起**——
          ``resume()`` 后才判定终止（每处检查点 pause 在前、abort 在后）。
        - 非行为：不置位任何 ``Execution.pause``（Execution 层暂停是
          另一个正交通道，由 Tool 覆写 / Composable 自管，不级联）。
        - **作用范围声明**：仅控制**本 Agent** 的工作循环 turn 检查点——
          不递归子 Agent（子树暂停用 :meth:`pause_recursive`）；不影响
          任何 ``Execution``——执行条目的框架级控制信号只有取消
          （``abort``），``Execution.pause`` 是保留给执行体自管的通道。

        .. rubric:: 测试案例

        - 前置：回合进行中（工具调用循环内）→ 操作：``agent.pause()`` →
          期望：下一个 tool_call 前挂起；``agent.resume()`` 后从该
          tool_call 继续，已执行工具的结果不丢。

        .. rubric:: 调用关系（审计）

        - 调用：``_pause_gate.clear()``（时机：每次调用，同步幂等）
        - 被调：无

        .. seealso:: :meth:`resume`、:attr:`paused`、:meth:`abort_turn`
        """
        self._pause_gate.clear()

    def resume(self) -> None:
        """恢复：打开工作循环 gate（``_pause_gate.set()``）。

        .. rubric:: 行为规约

        同步、幂等；打开后所有挂在检查点上的等待继续，随后立即判定
        abort 信号（pause 在前、abort 在后的检查点顺序）。仅恢复
        **本 Agent**（不递归子 Agent——子树恢复用
        :meth:`resume_recursive`）；不触碰任何 ``Execution``。

        .. rubric:: 调用关系（审计）

        - 调用：``_pause_gate.set()``（时机：每次调用，同步幂等）
        - 被调：无

        .. seealso:: :meth:`pause`、:meth:`resume_recursive`
        """
        self._pause_gate.set()

    def pause_recursive(self) -> None:
        """协作式暂停整棵生命周期子树（本 Agent + 全部后代子 Agent）。

        .. rubric:: 功能介绍

        ``self.pause()`` 后对 ``_children`` 中每个活子 Agent 递归调用
        ``pause_recursive()``（深度优先，与 :meth:`destroy` 的递归
        先例同构）。典型场景：UI 的「暂停整个工作流」按钮。

        .. rubric:: 行为规约

        - 同步、幂等；不 dispatch 钩子。
        - 只递归**生命周期子树**的 turn 循环（``_children`` 只装活
          实例）；不触碰任何 ``Execution``（执行条目的框架级控制
          信号只有取消）。
        - 与 ``cancel()`` 的级联不同：cancel 经 ``_executions`` 逐层
          传播终止信号；本方法只挂起各 Agent 的 ``_pause_gate``。

        .. rubric:: 调用关系（审计）

        - 调用：``self.pause()`` 与子 Agent ``pause_recursive()``
          （时机：每次调用，深度优先递归）
        - 被调：应用 / UI 层「暂停整个工作流」入口

        .. seealso:: :meth:`pause`、:meth:`resume_recursive`
        """
        self.pause()
        for child in list(self._children.values()):   # 深度优先递归（_children 只装活实例）
            child.pause_recursive()

    def resume_recursive(self) -> None:
        """恢复整棵生命周期子树（与 :meth:`pause_recursive` 对称）。

        .. rubric:: 行为规约

        同步、幂等；``self.resume()`` 后对 ``_children`` 每个活子 Agent
        递归 ``resume_recursive()``；不触碰任何 ``Execution``。

        .. rubric:: 调用关系（审计）

        - 调用：``self.resume()`` 与子 Agent ``resume_recursive()``
          （时机：每次调用，深度优先递归）
        - 被调：应用 / UI 层「恢复整个工作流」入口

        .. seealso:: :meth:`resume`、:meth:`pause_recursive`
        """
        self.resume()
        for child in list(self._children.values()):
            child.resume_recursive()

    @property
    def paused(self) -> bool:
        """是否暂停中（``not self._pause_gate.is_set()``）。

        只读派生视图；快照层据此展示暂停状态。

        .. rubric:: 调用关系（审计）

        - 调用：无（只读派生：``not self._pause_gate.is_set()``）
        - 被调：``flowing.snapshot.AgentSnapshot.paused`` 投影（时机：
          每次 ``snapshot()``）

        .. seealso:: :meth:`pause`、:meth:`resume`
        """
        return not self._pause_gate.is_set()

    # ────────────────────────── 取消与停止 ────────────────────────────────

    def abort_turn(self) -> None:
        """只置位当前逻辑 Turn 的退出信号（``_turn_abort.set()``）。

        .. rubric:: 功能介绍

        最小粒度的终止：当前 Turn 在下一个检查点退出，Agent 继续消费
        队列。收尾由 ``_run_turn`` 的 ``finally`` 接管
        （幂等置位 ``turn.aborted`` → abort 钩子 → resolve waiters）。

        .. rubric:: 行为规约

        - 同步、幂等；无活跃 Turn 时置位也安全（下一条消息到达时 Turn
          启动、``provider_gen()`` 立即返回 cancelled，空 Turn 不产生新树节点）。
        - 可覆写：子类可改为丢弃 Turn 消息或额外清理。
        - 与 ``cancel()`` 的分工：``abort_turn()`` 只终止当前 Turn；
          ``cancel()`` 终止整个 Agent（幂等拆解；Agent 无生命周期状态机，
          无 ``stopping/stopped`` 中间态可读，见 M-77）。

        .. rubric:: 测试案例

        - 前置：``on_provider_error`` handler 决定放弃本回合 → 操作：
          handler 内 ``agent.abort_turn()`` + ``can_continue=True`` →
          期望：下一次 ``provider_gen()`` 开头检测信号返回 cancelled，回合走
          abort 收尾，``TurnResult.status == "cancelled"``。

        .. rubric:: 调用关系（审计）

        - 调用：``_turn_abort.set()``（时机：每次调用，同步幂等）
        - 被调：``flowing.agent.Agent._run_turn``（时机：检查点 ②.5
          吸收到 ``INTERRUPT`` 消息时，置 Event 由紧随的 abort 判定统一
          收口）；规约场景为 ``on_provider_error`` handler 内由用户 /
          Composable 调用

        .. seealso:: :meth:`cancel`、:meth:`_run_turn`
        """
        self._turn_abort.set()

    async def cancel(self) -> None:
        """协作式终止整个 Agent：全部 ``_executions`` abort + ``_turn_abort``。

        .. rubric:: 功能介绍

        时序：dispatch ``before_cancel``（value 为 :class:`CancelContext`，
        handler ``raise Intercepted`` 阻止取消）→ 置位 ``_executions``
        全部 ``abort`` + 置位 ``_turn_abort``
        → dispatch ``after_cancel``（**信号置位后**——「取消请求已被
        接受」的事实事件；纯观察，日志 / 通知 / 审计）。无状态值迁移
        （M-77：无生命周期状态机）。

        **观察点分工（S-35 裁决）**：``after_cancel`` 表达的是「取消已
        被接受、信号已置位」，dispatch 点在本方法体内——协作式取消禁止
        本方法等待回合退出（回合内的代码调 ``cancel()`` 时等待即自
        死锁）。「回合真正退出」的观察归 ``after_turn``（全路径，
        handler 读 ``turn.aborted`` 区分取消与正常结束——abort 专用
        收尾钩子已删除，路径分流由 ``aborted`` 字段承担），在
        ``_run_turn`` 的 finally 中触发。空闲 Agent（无回合在跑）被
        cancel 时 ``after_cancel`` 照常触发：信号置位是事实，与有无
        回合无关。

        .. rubric:: 设计动机

        - 协作式：``abort.set()`` 是请求不是命令——执行体三选一（立即
          停止返回已有/空结果 / 忽略信号正常完成 / 关键收尾后返回部分
          结果）；**Agent 接收所有返回**，不因曾被 cancel 丢弃返回值。
        - cancel 是正常终止不是错误：工具返回部分输出作正常
          ``ToolResult``，Provider 返回空 ``ProviderResponse``——不抛
          异常。
        - 级联取消：父只操作自己的 ``_executions`` 与 ``_turn_abort``，
          子 Agent 在检查点检测到自己的 abort 后自行清理——每层只负责
          自己的 ``_executions``，无中央调度器。

        .. rubric:: 行为规约

        - 协程（dispatch 钩子）；**不接受原因参数**（无参定稿）。
        - cancel 后队列中**待处理消息不丢弃**——交应用层（重新投递 /
          记录日志 / 通知发送方三选一）。
        - 工具被 cancel ≠ Turn 被 cancel：``cancel_children()`` 影响的
          工具返回正常 ``ToolResult``，Turn 循环照常。
        - :raises flowing.errors.Intercepted: ``before_cancel`` handler
          阻止取消（信号不置位）。

        .. rubric:: 测试案例

        - 前置：一个 ``kind="tool"`` 与一个 ``kind="agent"`` 执行中 →
          操作：``await agent.cancel()`` → 期望：两条 ``Execution.cancel``
          与 ``_turn_abort`` 均置位，随后实例经 ``destroy()`` 清理。
        - 前置：``before_cancel`` handler ``raise Intercepted`` → 期望：
          异常上抛，所有信号未置位。

        .. rubric:: 调用关系（审计）

        - 调用：``before_cancel`` / ``after_cancel`` dispatch（时机：
          见本方法时序规约）、``_executions`` 遍历置位 + ``_turn_abort``
          置位
        - 被调：``flowing.agent.Agent.stop``（时机：默认实现等价
          ``cancel``）

        .. seealso::

            - :meth:`stop` —— 强制式对称入口（默认等价本方法）。
            - :meth:`cancel_children` / :meth:`cancel_by_tag` —— 更细
              粒度。
            - :meth:`abort_turn` —— 只终止当前 Turn。
        """
        await self.hooks.before_cancel.dispatch(self, CancelContext())   # Intercepted 阻止取消（信号不置位）；普通异常上抛
        for execution in self._executions.values():
            execution.cancel.set()   # 协作式信号：执行体自行决定停止方式
        self._turn_abort.set()
        # after_cancel：信号置位后立即 dispatch——「取消已被接受」的事实事件
        # （S-35 裁决；回合真正退出的观察归 _run_turn finally 的
        #   after_turn，handler 读 turn.aborted 分流）
        await self.hooks.after_cancel.dispatch(self, CancelContext())

    def cancel_children(self) -> None:
        """只协作式取消全部子执行，不动 ``_turn_abort``（自己继续）。

        .. rubric:: 行为规约

        - 置位 ``_executions`` 全部 ``abort``；Turn 循环照常。
        - 典型场景：父 Agent 推理到一半想调整方向——终止正在跑的子
          Agent / 工具，更新提示词，重新发起。
        - 同步方法（不 dispatch 钩子）。

        .. rubric:: 调用关系（审计）

        - 调用：无（置位 ``_executions`` 全部 ``abort``，不 dispatch
          钩子）
        - 被调：``flowing.agent.Agent.stop_children``（时机：默认实现
          等价本方法）

        .. seealso:: :meth:`cancel`、:meth:`cancel_by_tag`
        """
        for execution in self._executions.values():
            execution.cancel.set()   # 不动 _turn_abort（自己继续），不 dispatch 钩子

    def cancel_by_tag(self, tag: str) -> None:
        """只置位 ``tags`` 匹配 ``tag`` 的执行条目的 ``abort``。

        .. rubric:: 行为规约

        精细控制：如只取消 ``"bash"`` 标签的工具执行，留 ``"agent"``
        继续；不动 ``_turn_abort``，不匹配任何条目时为空操作。同步方法。

        .. rubric:: 调用关系（审计）

        - 调用：无（置位 ``tags`` 匹配条目的 ``abort``，不 dispatch
          钩子）
        - 被调：``flowing.agent.Agent.stop_by_tag``（时机：默认实现
          等价本方法）

        .. seealso:: :attr:`Execution.tags`、:meth:`cancel_children`
        """
        for execution in self._executions.values():
            if tag in execution.tags:
                execution.cancel.set()   # 不匹配任何条目时为空操作；不动 _turn_abort

    async def stop(self) -> None:
        """强制式停止——框架默认实现**等价于** :meth:`cancel`，子类可覆写。

        .. rubric:: 设计动机

        ``cancel()`` 是协作式（执行体可忽略信号）；执行体卡死 / 超时
        兜底需要更强手段。框架核心**不提供**强制 kill 通用实现——强制
        终止的手段高度依赖执行体类型（进程 kill、HTTP 连接关闭等），
        由子类覆写本方法注入。

        .. rubric:: 行为规约

        - 默认实现 = ``await self.cancel()``（含 before/after_cancel
          钩子）。
        - 覆写约定：先调默认逻辑或自行置位信号，再做强制动作；Agent
          可覆写 ``cancel()`` 为空操作以完全无视协作信号（此时
          ``stop()`` 是唯一的停止通道）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent.cancel()``（时机：默认实现 =
          ``await self.cancel()``）
        - 被调：无（强制式兜底入口，供用户 / 子类覆写）

        .. seealso:: :meth:`cancel`、:meth:`stop_children`
        """
        await self.cancel()

    def stop_children(self) -> None:
        """与 :meth:`cancel_children` 对称的强制式版本；默认等价，可覆写。

        .. rubric:: 行为规约

        默认实现 = ``self.cancel_children()``；自己继续（不动
        ``_turn_abort``）。同步方法。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent.cancel_children()``（时机：默认
          实现等价）
        - 被调：无

        .. seealso:: :meth:`cancel_children`、:meth:`stop`
        """
        self.cancel_children()

    def stop_by_tag(self, tag: str) -> None:
        """与 :meth:`cancel_by_tag` 对称的强制式版本；默认等价，可覆写。

        .. rubric:: 行为规约

        默认实现 = ``self.cancel_by_tag(tag)``。同步方法。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent.cancel_by_tag()``（时机：默认实现
          等价）
        - 被调：无

        .. seealso:: :meth:`cancel_by_tag`、:meth:`stop`
        """
        self.cancel_by_tag(tag)

    # ────────────────────────── 子 Agent ──────────────────────────────────

    async def create_subagent(self, agent_type: str, *, name: str | None = None,
                              **kwargs: Any) -> Agent:
        """干净构造入口：创建并持有子 Agent 实例。

        .. rubric:: 功能介绍

        直接委托 ``runtime.create_agent(agent_type,
        parent_id=self.node_id, **kwargs)``——不提供别的，只提供「自己
        的 ``node_id`` 作为 ``parent_id``」。

        .. rubric:: 设计动机（与 :meth:`invoke_subagent` 的分工）

        - 谁调用：钩子回调、外部代码、回合内工具（要「创建并持有
          实例」）。**``setup()`` 内不可调**（写闸门未开，见行为规约
          前置约束）。
        - 参数来源：调用方直接传完整 kwargs（``agent_type`` + 类型 args）；
          可选 ``name`` 为子代起语义名（登记进 ``_child_ids``，供
          ``invoke_subagent(resume=...)`` 按名续接；语义名只存在父侧
          表中，子实例不自持名字——A15 裁决）。
        - **不做** ``SubagentEntry.resolve()``，**不经过**
          ``before/after_subagent_invoke`` 钩子（仅走创建管线的
          ``before/after_create``）；不支持 ``resume`` 续接。
        - 生命周期：调用方自行管理——默认存续（agent 池），显式
          ``destroy()`` 或随父销毁。

        .. rubric:: 使用示例

        .. code-block:: python

            child = await self.create_subagent("payment-agent", order_id="456")
            result = await child.query("发起退款")

        .. rubric:: 行为规约

        - ``agent_type`` 是**字符串类型名**（统一裁决），由
          ``get_agent_class`` 惰性解析为 Agent 类；与资源引用语法
          一致（裸名引用）。
        - 创建即注册（``_nodes``）+ 进入 ``_children`` + provide 链可
          上溯到本实例。
        - **语义名登记（S-34）**：``name`` 非空时创建成功后登记
          ``_child_ids[name] = node_id`` 并同步
          写透核心键 ``child_ids``（整表覆写一行，末行合并防膨胀）。
          未命名子 Agent 不入表。
        - **前置约束（S-34 追加裁决）**：写闸门未开（``setup()`` 执行
          期间及之前）**不支持**创建子智能体——登记依赖写透通道，
          闸门锁定时调用本方法 → 立即抛错（fail fast，不留半登记
          状态）。setup 里需要的子代在 ``after_create`` /
          ``after_recover`` 钩子或首个回合中创建。
        - 异常（类型名解析失败 / PENDING 检查失败等）原样上抛，父
          Agent 状态不变。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.runtime.Runtime.create_agent()``（时机：每次
          调用直接委托，仅提供 ``parent_id=self.node_id``）
        - 被调：``flowing.agent.Agent.invoke_subagent``（时机：新建
          路径内部调用）

        .. seealso::

            - :meth:`invoke_subagent` —— 带 resolve + 唤起钩子的包装。
            - :meth:`flowing.runtime.Runtime.create_agent` —— 真正执行
              创建的唯一代码路径。
        """
        # S-34 追加裁决：写闸门未开不支持创建子智能体（登记依赖写透通道）
        if not self._state_bag._write_gate_open:
            raise RuntimeError(
                "create_subagent 不可用：写闸门未开（setup 期间及之前）"
                "——请在 after_create / after_recover 钩子或首个回合中创建子代")
        child = await self.runtime.create_agent(
            agent_type, parent_id=self.node_id, **kwargs)   # 直接委托，仅提供 parent_id
        self._children[child.node_id] = child   # 进入生命周期子树（node_id 为 key；创建即注册由 create_agent 管线完成）
        if name is not None:   # S-34：语义名 -> agent_id 登记并写透（未命名子 Agent 不入表；语义名只存在父侧本表，子实例不自持）
            self._child_ids[name] = child.node_id
            self._state_bag["child_ids"] = dict(self._child_ids)   # 写透整表（末行合并防膨胀）
        return child

    async def invoke_subagent(
        self,
        agent_type: str,
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

        - **准备段**（``:meth:`_prepare_subagent`，同步 await）：按别名查
          ``_subagent_entries`` → ``SubagentEntry.resolve()``（LLM args →
          完整 kwargs：别名映射 + specified 求值 + inject 注入）→ 构造
          :class:`SubagentInvocation` 并 dispatch 父 Agent 的
          ``before_subagent_invoke`` → 注册 ``Execution(kind="agent")`` →
          新建（内部调 :meth:`create_subagent`）或续接（``resume`` 按
          实例名找池中实例）。此段失败（``Intercepted`` / 校验 / 创建抛
          异常）**同步上抛**——子 Agent 要么成功创建要么未创建，工具路径
          由 ``ToolResult(status="error")`` 承载（LLM 可见）。
        - **运行段**（``:meth:`_run_subagent`，可后台）：``await child.
          message(prompt)`` 等待产出 → 构造 :class:`SubagentResult` →
          dispatch ``after_subagent_invoke``（**先于交付**：handler 可
          改写 result，改写对两条路径同时生效）→ 用改写后的 result
          构造 ``Message(kind=SUBAGENT)`` 推入本实例队列（LLM 后续回合
          感知）并 return（消息路径与代码路径同源）→ finally 清理
          Execution。运行段结局走 ``TurnResult`` 四态正常通道
          （``subagent_status`` 承载），只剩框架 bug 走框架错误通道。

        .. rubric:: 设计动机

        与 :meth:`create_subagent` 的分工见后者 docstring。「带 resolve +
        钩子 + 生命周期策略的包装」 vs 「干净构造」。``subagent-invoke``
        工具（LLM 调用面）是本方法的工具封装。本方法本身是同步 API：
        调用方会同步拿到 ``SubagentResult``，因此**不**把结果作为
        ``SUBAGENT`` 消息入队——真实产出由调用方决定如何交付（例如同步
        工具把全字段填进自己的返回值）。需要“结果稍后以 SUBAGENT 消息
        到达”的路径是 ``SubagentInvokeTool`` 的 ``asynchronized=True``，
        它不走本方法，而是拆分 ``_prepare_subagent`` + 后台
        ``_run_subagent(enqueue_result=True)``。

        .. rubric:: 使用示例

        .. code-block:: python

            # 新建 + 命名
            result = await self.invoke_subagent(
                "coder", prompt="审查 auth 模块", name="my-reviewer")
            # 之后续接同一实例（保持原类型）
            result2 = await self.invoke_subagent(
                "coder", resume="my-reviewer", prompt="继续审查 payment 模块")

            # 并行编排（各自注册为 Execution）
            rec, discount = await asyncio.gather(
                self.tool_call(ToolCall(name="recommend-products",
                                        args={"category": "电子产品"})),
                self.invoke_subagent("store-discount", store="京东"),
            )

        .. rubric:: 行为规约

        - ``resume`` 与新建参数互斥：续接保持原类型，``kwargs`` 忽略
          （实例已存在）；``resume`` 找不到实例名 → ``ValueError``。
          查找载体 = ``_child_ids`` 语义名表（S-34）：目标实例活着
          直接用，已销毁 / 未恢复则经 ``Runtime.get_agent`` 现场恢复
          并重新进入 ``_children``。
        - 子 Agent 注册为 ``Execution(kind="agent")``——``cancel()`` /
          级联取消可命中；子 Agent 被 cancel 的已产出部分结果作为正常
          产物返回父 Agent：``SubagentResult.subagent_status`` 标
          ``"cancelled"``，``result`` 按统一填充规则（``finish_output``
          已置位 → 载荷照返；有完整本轮 PROVIDER 消息 → 其文本；皆无
          → ``None``，「仅返回取消信息」即由 ``subagent_status`` 承载）。
        - 唤起失败（创建 / 校验 / 运行抛异常）原样上抛调用方——无专属
          错误钩子（``on_subagent_error`` 已删除）；工具路径由
          ``ToolResult(status="error")`` 承载。
        - ``after_subagent_invoke`` 在结果构造后、**交付前** dispatch
          （value 为 :class:`SubagentInvocation`，``result`` 已回填）：
          handler 可改写 ``invocation.result``，改写对 return 值与
          SUBAGENT 消息**同时生效**（两条路径同源）；纯观察需求由不改写
          的 handler 承担（``on_subagent_return`` 已删除并入）。本钩子
          不接 ``Intercepted``——阻断闸在 ``before_subagent_invoke``，
          答卷级处置用改写表达。
        - 同步交付语义：本方法等待子 Agent 完成后，把改写后的
          ``SubagentResult`` 作为返回值交给调用方；**不**再另发
          ``SUBAGENT`` 消息入父队列。调用方（通常是同步工具路径）负责
          把结果放进自己的返回值/树节点，避免同源结果二次入队。
        - 外部代码需要“异步执行且不入队”时，用
          ``asyncio.create_task(agent.invoke_subagent(...))``——本方法
          是协程，可被后台执行；结果只交给该 Task，不会推入 Agent 队列。
        - ``subagent-invoke`` 工具的 ``asynchronized=True`` 是**另一条
          特殊路径**：它不走本方法，而是拆成 ``_prepare_subagent``（同步
          创建）+ 后台 ``_run_subagent(enqueue_result=True)``；完成后
          **必定** enqueue ``SUBAGENT`` 消息，工具只返回 ``started`` 收据。
          外部代码若不想入队，不要走该工具异步路径，用
          ``asyncio.create_task(agent.invoke_subagent(...))``。
        - 无论是否入队，``after_subagent_invoke`` 都先于交付 dispatch，
          改写后的结果同时是 return 值与后续交付内容。
        - 非行为：不做 keep_alive 语义（已废弃）——生命周期由 agent 池
          「默认存续 + 显式销毁」管理。

        .. rubric:: 测试案例

        - 前置：``.fya`` 声明 ``- payment as pay`` 且其 ``args:`` 含
          ``user_id: "{{ self.inject('user_id') }}"``（注入表达式，R-4）
          → 操作：``await self.invoke_subagent("pay", prompt="收款 99 元")``
          → 期望：子 Agent ``setup`` 收到 ``user_id``（沿 provide 链），
          LLM 的 catalog 中无 ``user_id`` 参数。
        - 前置：``before_subagent_invoke`` handler ``raise Intercepted``
          → 期望：异常上抛，未创建任何实例。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.subagents.SubagentEntry.resolve()``、
          ``flowing.agent.Agent.create_subagent()``（新建路径）、
          ``child.query()``（等待产出）、``before_subagent_invoke`` /
          ``after_subagent_invoke`` dispatch（时机：均见本方法时序规约）；
          ``Execution(kind="agent")`` 注册
        - 被调：``subagent-invoke`` 工具（LLM 调用面封装；本体为
          :class:`flowing.builtins.SubagentInvokeTool`，``Runtime.__init__``
          经 :func:`flowing.builtins.register_builtins` 核心注册；注册保证在场，
          但可见性须 Agent 级显式声明——核心特性 ≠ 静默附加）。
          ``asynchronized=True`` 时工具经 :meth:`_prepare_subagent` +
          ``asyncio.ensure_future(_run_subagent)`` 拆段调用

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
        Execution 注册 + 创建/续接。

        .. rubric:: 功能介绍

        同步 await 的唤起前半段。本段返回即保证「子 Agent 已成功创建
        （或续接）」；本段内任何失败（``before_subagent_invoke`` 的
        ``Intercepted`` / 校验错误 / 创建抛异常）**同步上抛**调用方，
        且 Execution 注册被回收——「成功创建或未创建」二态边界，无
        半登记状态。``subagent-invoke`` 工具的 ``asynchronized=True``
        分支在本段完成后才把 :meth:`_run_subagent` 包进后台任务，故
        创建失败对该分支同样 LLM 可见（``ToolResult(status="error")``）。

        .. rubric:: 行为规约

        - 返回 ``(child, invocation, execution)`` 三元组，交由
          :meth:`_run_subagent` 消费；Execution 清理由运行段 finally
          承担（本段异常路径自清理）。
        - ``name`` 经 ``SubagentInvocation.name`` 进创建管线：新建路径
          透传 :meth:`create_subagent`（登记 ``_child_ids``）；resume
          路径忽略（实例已存在）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.subagents.SubagentEntry.resolve()``、
          ``before_subagent_invoke`` dispatch、:meth:`create_subagent`
          （新建路径）、``flowing.runtime.Runtime.get_agent``（续接路径）
        - 被调：:meth:`invoke_subagent`；``subagent-invoke`` 工具
          ``asynchronized=True`` 分支（创建保证的同步边界）
        """
        entry = self._subagent_entries[agent_type]   # 按别名查（agent_type 形参承载别名）
        init_kwargs = entry.resolve(self, kwargs)   # LLM args -> 完整 kwargs
        invocation = SubagentInvocation(
            alias=agent_type,
            # spec 未写清处落实：骨架把别名直接当 agent_type 透传，与
            # SubagentInvocation.agent_type「取自 SubagentEntry.name_ori」的
            # 字段契约矛盾——按字段契约落实（别名是 LLM 面，类型名是创建面）
            agent_type=entry.name_ori if resume is None else None,   # resume 与 agent_type 互斥
            name=name,
            resume=resume, prompt=prompt, args=init_kwargs)
        invocation = await self.hooks.before_subagent_invoke.dispatch(
            self, invocation)   # 可改写 args/prompt；Intercepted 硬阻断唤起（上抛，未创建实例）
        execution = Execution(id=uuid4().hex, kind="agent", tags=["subagent"],
                              started_at=datetime.now(),
                              cancel=asyncio.Event(), pause=asyncio.Event())
        self._executions[execution.id] = execution   # cancel()/级联取消可命中
        try:
            child: Agent
            if invocation.resume is not None:
                # 续接（S-34）：语义名 -> agent_id 翻译（_child_ids），再经
                # Runtime.get_agent 按 id 取（活着直接用；已销毁/休眠 ->
                # 现场恢复——destroy ≠ 删除，记录保留）；表项不随 destroy 删除
                if invocation.resume not in self._child_ids:
                    raise ValueError(invocation.resume)   # 按名未找到 -> 报错（C-02 口径）
                child = await self.runtime.get_agent(
                    self._child_ids[invocation.resume])
                self._children[child.node_id] = child   # 重新进入生命周期子树（父级联销毁恢复生效）
            else:
                child = await self.create_subagent(
                    entry.name_ori, name=invocation.name, **invocation.args)   # 新建路径：规范类型名（别名只存在绑定层；name 登记 _child_ids）
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

        .. rubric:: 功能介绍

        本段内不再有「创建失败」——结局只有 ``TurnResult`` 四态
        （``subagent_status`` 承载）与框架 bug（走框架错误通道）。
        ``asynchronized=True`` 时本方法整体被 ``asyncio.ensure_future``
        包进后台任务，完成时 SUBAGENT 消息与 ``after_subagent_invoke``
        照常发生，cancel 经 Execution 注册照常可命中。

        .. rubric:: 行为规约

        - ``after_subagent_invoke`` **先于交付** dispatch：handler 改写
          ``invocation.result`` 后，return 值与可选的 SUBAGENT 消息
          **同源**采用改写后的 result。本钩子不接 ``Intercepted``
          （阻断闸在 before，答卷级处置用改写表达）。
        - ``enqueue_result``：``True``（默认）→ dispatch 后构造独立
          ``Message(kind=SUBAGENT, priority=STEER)`` 入本实例队列——
          长 turn 可达天级，子代完成推送须即时注入：回合进行中于
          检查点 ②.5 被吸收、当轮 context 可见、不打断（LLM 当轮
          即可感知，不再等回合间）。``False`` → 跳过入队，只 return
          结果；同步工具路径用 ``False`` 避免同一结果既作为 TOOL
          消息挂树、又作为 SUBAGENT 消息再次入队。
        - finally 清理 Execution 注册（无论结局）。

        .. rubric:: 调用关系（审计）

        - 调用：``child.query()``（等待产出）、``after_subagent_invoke``
          dispatch（时机：结果构造后、交付前）
        - 被调：:meth:`invoke_subagent`；``subagent-invoke`` 工具
          ``asynchronized=True`` 分支经 ``asyncio.ensure_future`` 间接调
        """
        try:
            turn_result = await child.query(invocation.prompt or "")   # 等待产出（prompt 可为 None：纯参数唤起）
            result = SubagentResult(name_alias=invocation.resume or invocation.name or invocation.alias,   # 语义名只存在父侧（A15：simplename 已删除）
                                    subagent_id=child.node_id,
                                    result=child.last_result,   # 收尾段已写入（resolve waiters 之前，无时序竞争）；finish → dict，普通 → 文本，无产出 → None
                                    subagent_status=turn_result.status)   # 取消/异常信息载体（此前 turn_result 接住未用，自此启用）
            invocation.result = result   # after 阶段回填
            invocation = await self.hooks.after_subagent_invoke.dispatch(
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
        finally:
            self._executions.pop(execution.id, None)
        return result

    # ────────────────────────── 工具调用 ──────────────────────────────────

    async def tool_call(self, tool_call: ToolCall) -> ToolResult:
        """Agent 工具调用方法——完整钩子链的执行包装。

        .. rubric:: 功能介绍

        时序：dispatch ``before_tool_call``（可改写 ``ToolCall`` /
        ``shortcut`` 短路 / ``raise Intercepted`` 硬阻断）→ 按**别名**
        查 ``_tool_entries`` → :meth:`_normalize`（LLM 视角校验 /
        别名映射与 ``ToolEntry.resolve()`` 聚合 / 默认值）→ ``Tool.__call__`` 调度（内部
        校验（S-33 落点）/ 注册 ``Execution(kind="tool")``，finally 清理）→ dispatch
        ``after_tool_call``（可改写结果；shortcut 路径照常触发）→
        收尾归一（return 前幂等再跑一次 ``normalize_output``，封
        shortcut 与钩子改写两条缝，D19）→ 返回。

        .. rubric:: 行为规约

        - 仅按别名查找；未命中 → ``UnknownToolError``（无规范名回退——
          回退会绕开 Agent 级绑定）。
        - ``Intercepted`` → 返回 ``ToolResult.blocked(...)``（LLM 可见
          形态为 ``[TextBlock(reason)]``——``as_message`` 塑形的文本块，
          不再是 ``{"status": "blocked", "reason": ...}`` JSON），不上抛。
        - ``ToolResult(status="error")`` 是**正常产物**：LLM 可见、不
          触发任何错误钩子（工具业务错误不走异常通道）。
        - ``_normalize`` 的 **LLM 视角校验失败**同属正常产物：包装为
          ``ToolResult(status="error", error=<LLM 命名空间的错误文本>)``，
          ``after_tool_call`` **照常触发**（handler 可观察/改写），随后
          正常返回进消息树——LLM 的自我修正反馈，不是回合异常。
          **内部校验失败**（specified/inject/默认值的配置错误，在
          `Tool.__call__` 触发）例外：
          上抛框架错误通道 + 日志，不包成 ToolResult、不进 LLM 可见
          文本。
        - abort 于工具调用循环中：正在执行的工具检测信号返回部分结果；
          剩余工具被本包装层跳过（不 execute）。
        - **本方法没有同步/异步开关**：同步还是异步由工具的 ``execute``
          实现决定。``execute`` 是普通 ``async def`` → ``Tool.__call__``
          await 到底，本方法返回最终 ``ToolResult``，**不 enqueue**；
          ``execute`` 返回 ``asyncio.Task`` → 走下方「异步工具透明化」
          的固定 enqueue 路径。因此“是否入队”只取决于工具实现形态，
          不取决于本方法或调用上下文。
        - 异步工具透明化：``execute()`` 返回 ``asyncio.Task`` 时
          ``Tool.__call__`` 不等待，立即产 ``ToolResult(status="pending",
          output=None)`` 收据（经 ``as_message`` 塑形为
          ``tool_status="pending"``、``content=[]`` 的 TOOL 消息挂树，
          **配对一次性封闭**）；同时给 Task 挂 ``add_done_callback``
          ——**watcher 为框架固定行为，非扩展点**（不可覆写、不可注册，
          要定制的插件走别的事件通道）。完成回调：取终值 →
          ``normalize_output`` → ``output_to_blocks``（与
          ``as_message`` 同一塑形实现）→ ``Message(kind=EVENT,
          source="tool_result", content=[标注块, *结果块],
          priority=STEER)`` 进队列（长 turn 可达天级，完成推送须
          即时注入——检查点 ②.5 吸收、当轮 context 可见、不打断）；
          任务异常 → 标注块 + 错误文本块（与同步 error 同语义，LLM
          可见）——Turn 循环中不存在 ``if async`` 分支。

        .. rubric:: 测试案例

        - 前置：``before_tool_call`` handler 改写
          ``tool_call.args["lang"] = "zh"`` → 操作：``await
          agent.tool_call(tc)`` → 期望：``execute()`` 收到改写后的参数。

        .. rubric:: 调用关系（审计）

        - 调用：``before_tool_call`` / ``after_tool_call`` dispatch、
          ``flowing.agent.Agent._normalize()``、``Tool.__call__`` 调度
          （时机：见本方法时序规约）；``Execution(kind="tool")`` 注册
        - 被调：``flowing.agent.Agent._run_turn``（时机：工具调用循环
          每个 tool_call，检查点 ③ 之后）

        .. seealso::

            - :class:`flowing.tool.ToolEntry` —— 绑定条目（resolve 三步）。
            - :class:`flowing.tool.ToolResult` —— 四状态结果。
            - :class:`flowing.tool.ToolCall` —— 入参结构。
        """
        # 异常路径：before_tool_call handler raise Intercepted -> 捕获（不上抛）
        # 返回 ToolResult.blocked(reason)（Intercepted 未在本模块具名引入，见
        # flowing.errors 与 v2 存疑 S2-02）
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
            # after_tool_call 照常触发；内部校验已迁入 Tool.__call__（S-33），
            # 失败 -> 上抛框架错误通道 + 日志
            try:
                resolved_args: dict[str, Any] = self._normalize(entry, tool_call, tool)
            except (ValidationError, _LlmViewValidationError) as exc:
                result = ToolResult(status="error", error=str(exc))
            else:
                execution = Execution(id=uuid4().hex, kind="tool", tags=[],
                                      started_at=datetime.now(),
                                      cancel=asyncio.Event(), pause=asyncio.Event())
                self._executions[execution.id] = execution
                try:
                    result = await tool(resolved_args, caller=self,
                                        execution=execution)   # Tool.__call__ 调度
                    # abort 于工具调用循环中：工具检测信号返回部分结果（协作式），
                    # 剩余工具由 _run_turn 检查点 ③ 跳过
                finally:
                    self._executions.pop(execution.id, None)
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
        """工具参数规范化（内部 API，不属稳定契约；M-60 裁决归属 Agent）。

        .. rubric:: 功能介绍

        ``tool_call()`` 管线的中段：把（可能被钩子改写过的）
        ``tool_call.args`` 聚合为 ``execute()`` 的最终参数字典。
        归属 Agent 而非 ToolEntry，是因为聚合需要 Agent 上下文：
        Parsable 渲染上下文、provide 链（``self.inject``）、以及
        LLM 视角校验失败后的错误通道。

        .. rubric:: 行为规约（LLM 视角校验在此；内部校验已迁出）

        1. **LLM 视角校验**（先于一切转换）：``tool_call.args`` 按 LLM
           可见 schema 校验——校验模型由
           ``entry.llm_definition(self.runtime, self).params_schema``
           经 :func:`flowing.params.schema_to_model` 桥接（C-09 裁决：
           推导**归属 ToolEntry**——隐藏参数（specified）排除、
           ``override_params`` 覆写与 ``param_aliases`` 别名化都是
           entry 级绑定，注册表 `ToolDefinition` 上没有无 entry 上下文
           的 LLM 视图可言；允许实现层缓存桥接产物）。
           校验失败：错误文本以 **LLM 命名空间**（别名）
           进入 ``ToolResult.error``，LLM 可据以自我修正；LLM 传出
           schema 未定义的参数（幻觉同名隐藏参数）→ 按「未定义参数」
           校验错误处理（参数名由 LLM 自己提供，回指不构成泄漏）。
        2. :meth:`flowing.tool.ToolEntry.resolve` —— 别名映射回规范名
           → ``specified`` 惰性求值覆盖（固定值/注入表达式——注入
           表达式在求值时沿 provide 链上溯，R-4：无独立 inject 步骤）；
           ``params_schema`` 实参取 ``tool.definition.params_schema``
           （注册表规范定义）。
        3. **schema 默认值填充**（前两步均未给的参数）：按
           ``tool.definition.params_schema`` 各 property 的 ``default``
           补齐。

        **内部校验不在此处**（S-33 裁决）：specified/默认值的
        配置错误由 `Tool.__call__` 在 caller 注入之后、``try`` 之外
        按创建时的 ``_args_model`` 校验，失败上抛框架错误通道 +
        日志，不进 LLM 可见文本。

        子智能体唤起（``SubagentEntry.resolve``）与技能加载
        （``skill_load``）遵循同一规则：校验以调用方（LLM）可见的参数
        集与命名进行，specified（含注入表达式）参数不进调用方校验空间。

        :param entry: 本 Agent 的工具绑定条目（别名/覆写/specified 等）。
        :param tool_call: 钩子链之后的 `ToolCall`（args 键为 LLM 命名）。
        :param tool: 注册表中的规范 `Tool` 实例（S-33 裁决：本方法持
          tool 以完成第 1、3 步；ToolEntry 保持不依赖 Tool 的分层）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.tool.ToolEntry.resolve()``（时机：管线第 2
          步：别名映射 → specified 求值覆盖——注入表达式在求值时经
          ``self.inject`` 沿 provide 链上溯）
        - 被调：``flowing.agent.Agent.tool_call``（时机：
          ``before_tool_call`` 钩子之后、``Tool.__call__`` 调度之前）

        .. seealso::

            - :meth:`tool_call` —— 所属管线。
            - :meth:`flowing.tool.ToolEntry.resolve` —— 第 2 步本体。
            - :meth:`flowing.tool.Tool.__call__` —— 内部校验落点。
        """
        # 1. LLM 视角校验（先于一切转换）：校验模型由
        #    entry.llm_definition(self.runtime, self).params_schema 经
        #    params.schema_to_model 桥接（C-09：推导归属 ToolEntry）；失败 ->
        #    错误文本以 LLM 命名空间进 ToolResult.error（由 tool_call 包装为正常产物）
        #    ——校验模型按 LLM 视图（别名化 + 隐藏参数排除后的 schema）建模，
        #    错误消息的字段名因此天然落在 LLM 命名空间
        llm_schema = entry.llm_definition(self.runtime, self).params_schema
        llm_model = schema_to_model(f"{entry.name_alias}-llm-args", llm_schema)
        llm_model.model_validate(tool_call.args)   # 类型/必填错误 -> ValidationError 上抛给 tool_call 包装
        unknown = sorted(set(tool_call.args) - set(llm_schema))
        if unknown:
            # 幻觉参数（LLM 传出 schema 未定义的参数）按「未定义参数」校验
            # 错误处理——桥接模型默认忽略多余键，未知键在此显式拒绝
            raise _LlmViewValidationError(
                f"工具 {entry.name_alias!r} 收到未定义参数: {unknown}")
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

        ``source_file`` 是 ``@/`` 格式字符串（``__init_subclass__`` 推算），
        经 ``runtime.resolve_path`` 落地为绝对路径后取 ``.parent``；
        ``source_file is None`` → ``None``（无文件上下文，下游裸名解析
        退化为纯注册表查询；FILE_REF 用 ``./`` 相对路径时由
        ``resolve_path`` 报错）。

        「文件 → 所在目录」换算的**唯一承担者**（P3-11 裁决，公共 API）：
        ``get_tool`` / ``get_agent_class``、插件挂载的 ``skill_get`` /
        ``skill_add``、以及 Parsable 的 FILE_REF 求值（两处）一律经
        本属性，不允许调用方自行对 ``source_file`` 取 parent。
        """
        if type(self).source_file is None:
            return None
        return self.runtime.resolve_path(type(self).source_file).parent   # @/ 格式落地后取所在目录

    def get_tool(self, name_or_path: str) -> Tool:
        """上下文感知的工具解析门面：自动携带本 Agent 的 ``source_dir``。

        .. rubric:: 功能介绍

        薄委托 ``runtime.tool_registry.get(name_or_path,
        source_dir=self.source_dir())``——与直接调注册表的区别仅在
        文件上下文：裸名先查**本 Agent 定义文件所在目录**的定向文件
        查找链（文件覆盖 ``default::``/``builtin::``），``./``/``../``
        相对路径可用。无文件上下文（``source_file is None``）时退化为
        纯注册表查询。

        .. rubric:: 设计动机

        「文件覆盖默认」需要一个锚定目录，而锚定目录天然属于 Agent
        （``source_file``）。把门面放在 Agent 上，调用方不必关心
        ``source_dir`` 的换算；Runtime 侧注册表保持无上下文（裸名只查
        ``default::``/``builtin::``，相对路径报错）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.tool.ToolRegistry.get``（每次调用，携带
          ``source_dir``）；``self.source_dir()``（每次调用）
        - 被调：``.fya`` ``tools:`` 条目装配（声明期）；
          :meth:`add_tool`（运行期 / ``setup()`` 期）

        .. seealso:: :meth:`flowing.tool.ToolRegistry.get`（完整解析
            语义与候选链）、:meth:`get_agent_class`（同构门面）
        """
        return self.runtime.tool_registry.get(name_or_path, source_dir=self.source_dir())

    def get_agent_class(self, agent_type: str) -> type[Agent]:
        """上下文感知的 Agent 类型解析门面：自动携带本 Agent 的
        ``source_dir``。

        薄委托 ``runtime.get_agent_class(agent_type,
        source_dir=self.source_dir())``——语义与 :meth:`get_tool`
        同构（裸名文件链优先、文件覆盖注册表；无文件上下文退化为
        纯注册表查询）。Agent 侧**只有** ``get_agent_class``，没有
        ``get_agent``——取活实例/现场恢复统一走
        :meth:`flowing.runtime.Runtime.get_agent`（用户裁决）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.runtime.Runtime.get_agent_class``（每次调用，
          携带 ``source_dir``）；``self.source_dir()``（每次调用）
        - 被调：``invoke_subagent`` 的类型解析路径（每次唤起子 Agent）
          ；插件/用户代码

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
        """向本 Agent 添加一条工具用法条目（``_tool_entries`` 的公开写入入口）。

        .. rubric:: 功能介绍

        按引用（``name`` 为字符串时）或现成的 :class:`flowing.parser.EntryRef`
        找到工具，判别覆写体（``body``）并构造 :class:`flowing.tool.ToolEntry`，
        以别名（``alias`` / ``ref.alias``，缺省 = 规范名）为 key 写入
        ``self._tool_entries``。从此该工具进入本 Agent 的
        可见集（``enabled=True`` 时经 ``_assemble_context()`` 对 LLM 可见）
        与可调用集（``tool_call()`` 仅按别名查本表）。

        **两种调用形态**（内部统一归一为 EntryRef 后走同一条管线）：

        - **``.fya`` 装配层**（主调用方）：``parse_fya`` 产出的
          ``EntryRef`` 直接透传——``add_tool(ref)``，此时 ``alias`` /
          ``body`` 必须缺省（与 ``ref`` 自带字段重复 →
          :class:`flowing.errors.FormatError`）；
        - **程序化**（``setup()`` / 运行期）：``add_tool(name, alias=...,
          body=...)``，``body`` 与 ``.fya`` 单键映射项的覆写映射**同构**
          （键集 ``description`` / ``args`` / ``output`` / ``enabled``——
          R-4：``inject`` 键已删除，注入写 args 里的
          ``"{{ self.inject('key') }}"`` 表达式）；内部经
          :func:`flowing.parser.normalize_entries` 构造 EntryRef
          （保持「唯一构造通道」声明）。

        .. rubric:: 设计动机

        ``_tool_entries`` 有两条填充路径：``.fya`` ``tools:`` 声明（解析期）
        与本方法（运行期/``setup()`` 期，C-02 裁决补声明）。

        **S-01 裁决：工具绑定不经任何 use composable**——「启用某能力」
        （``use_skill()`` / ``use_cron()`` 等 Composable 的钩子、状态、
        prompt block 装配）与「该能力的工具对 LLM 可见」（``.fya``
        ``tools:`` 或显式 ``add_tool``）是两个独立动作；工具本体
        的 Runtime 级注册在 .fya 解析收集或插件 ``install()``（如
        ``CronPlugin`` 注册 schedule-cron 系列）或核心（``subagent-invoke``
        / ``finish`` 随 ``Runtime.__init__``）完成。

        .. rubric:: 使用示例

        .. code-block:: python

            # 插件 setup 期的典型形态（裸名，无覆写）
            self.add_tool("schedule-cron")

            # 程序化带覆写——body 与 .fya 单键映射项的覆写映射同构：
            #   - make-payment as pay:
            #       args:
            #         amount:                        # 稀疏补丁（覆写端零糖）
            #           description: 支付金额（元）
            #         user_id: "{{ self.inject('user_id') }}"   # 注入表达式（R-4）
            self.add_tool(
                "make-payment", alias="pay",
                body={"args": {"amount": {"description": "支付金额（元）"},
                               "user_id": "{{ self.inject('user_id') }}"}},
            )

            # .fya 装配层：parse_fya 产出的 EntryRef 直接透传
            self.add_tool(entry_ref)

        .. rubric:: 行为规约

        - 同步、立即生效：下一次 ``_assemble_context()`` 即可见。
        - **同 alias 重复添加 → :class:`flowing.errors.EntryNameConflictError`**。
          每次生命周期（create / recover）都从 ``__init__`` 的空
          ``_tool_entries`` 开始重放 ``setup()``，恢复不感知上一次的
          效果；同一生命周期内重复添加同 alias 是笔误，快速失败
          （tool / skill / subagent 绑定层统一语义，「都报错不覆盖」）。
        - **glob 显式优先**：``tools:`` 装配层展开 glob 时，与已显式声明
          条目**规范名相同**的同一资源跳过（先解析显式条目，再展开
          glob；与 skills/subagents 同构）。只有**不同资源**得到同一
          alias 时，才按上一条报 ``EntryNameConflictError``。
        - **body 判别（本方法体内，单点维护）**：键集固定为
          ``description`` / ``args`` / ``output`` / ``enabled``；
          **未知键 → :class:`flowing.errors.FormatError`**（含旧
          ``inject`` 键——R-4 已删除，注入写 args 里的注入表达式）
          （与 args 级「非法子属性 fail-fast」同口径——笔误不静默吞）。
          各键去向：

          - ``description`` → ``override_description``；``str`` 构造时
            包装为 :class:`flowing.parsable.Parsable` 常量，
            ``Parsable`` 原样透传（P1-17：求值面内，
            ``llm_definition()`` 时以本 Agent 为上下文自动求值）；
            ``_``（PENDING）→ 空补丁，视为无覆写（``None``）；
          - ``args`` → 逐参数判别（规则本体见
            :class:`flowing.tool.ToolEntry` 行为规约）：dict 值 →
            ``override_params`` 稀疏补丁（JSON Schema 关键字，零糖——
            类型必须写 ``type:``）；键含 ``as`` → ``param_aliases``；
            ``_`` → 空补丁；其它值 → ``specified``，包装为
            ``Parsable``（惰性求值；``"{{ self.inject('key') }}"``
            注入表达式在此落入 specified——R-4）；
          - ``output`` → **独立判别分支**（B 方案：承认差异而非伪装成
            args 补丁）：值是「字段名 → JSON Schema 定义」映射，逐字段
            并入 ``override_params``，不经过 args 的关键字校验
            （``type`` 等键在此合法）；「省略字段 = 移除」语义见
            ``FinishTool`` 规约；
          - ``enabled`` → 布尔原样。
        - 深层块（``$tools.<alias>.args.<param>.description:``）的填回
          **先于**本方法调用（装配层时序约束：先 merge 具名块，再逐条目
          调本方法）——本方法看到的 ``body`` 是已合并的最终形态。
        - 非行为：执行时按 ``name_ori`` 现场查注册表，entry 不持有 Tool
          实例引用；不写持久化状态（entry 表由声明/``setup()`` 重建，
          不落盘）。（声明期的文件链命中会实例化并注册 Tool——一次性
          声明期行为，与本条不冲突。）
        - 边缘情况：``name``/``ref.raw`` 未注册 → 经 :meth:`get_tool`
          （携带本 Agent 的 ``source_dir``）先走定向文件查找链惰性解析
          ——文件覆盖 ``default::``/``builtin::``（插件工具在阶段一
          ``install`` 注册，``setup()`` 时必已存在；文件形态工具此时被
          发现并注册）；查找链仍不命中才抛
          :class:`flowing.errors.ToolNotFoundError`（笔误，快速失败）。

        :param name: 规范名 / 路径 / ``ns::name`` 引用串，或
            ``.fya`` 解析产出的 ``EntryRef``（此时 ``alias``/``body``
            必须缺省）。
        :param alias: LLM 看到的别名；缺省按 ``normalize_entries`` 推断
            （裸名 = 裸名本身等）。
        :param body: 覆写映射，与 ``.fya`` 单键映射项的值同构；``None``
            为无覆写。
        :returns: 新创建的 ``ToolEntry``（便于链式修改，如置 ``enabled``）。
        :raises flowing.errors.ToolNotFoundError: 引用未在注册表/查找链。
        :raises flowing.errors.EntryNameConflictError: 同 alias 条目已存在。
        :raises flowing.errors.FormatError: EntryRef 与 ``alias``/``body``
            重复给值；``body`` 含未知键或非法形态。

        .. rubric:: 测试案例

        - 前置：``runtime.tool_registry`` 已注册 ``finish`` → 操作：
          ``add_tool("finish")`` → 期望：``_tool_entries["finish"]``
          的 ``name_ori == "finish"``；再次同名调用 → ``EntryNameConflictError``。
        - 前置：未注册 ``"ghost"`` → 期望：``ToolNotFoundError``，
          ``_tool_entries`` 不变。
        - 前置：``add_tool("make-payment", alias="pay", body={"args":
          {"currency": "USD", "working_dir as cwd": _},
          "inject": ["user_id"]})`` → 期望：条目 ``specified`` 含
          ``Parsable("USD")``，``param_aliases == {"cwd": "working_dir"}``
          且 ``override_params["working_dir"] == {}``（空补丁），
          ``inject == ["user_id"]``。
        - 前置：``body={"descripton": "..."}``（笔误键）→ 期望：
          ``FormatError``。
        - 前置：EntryRef 与 ``alias=`` 同传 → 期望：``FormatError``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.tool.ToolEntry`` 构造（每次调用一次）；
          :func:`flowing.parser.normalize_entries` / :func:`flowing.parser.split_as`
          （程序化形态的 EntryRef 归一与 args 键 ``as`` 切分）；
          :meth:`get_tool`（存在性解析）；``flowing.parsable.Parsable``
          构造（``description`` 与 ``specified`` 的包装——**本方法即
          「理解 args 语义的手动包装点」**，见
          :mod:`flowing.parsable` 求值面内字段集合）
        - 被调：``.fya`` ``tools:`` 条目装配（声明路径，EntryRef 透传）
          与运行期/``setup()`` 期的显式调用（如 cron 示例 ``setup()``
          中的 ``add_tool("schedule-cron")`` / ``add_tool("manage-cron")``）。
          S-01 裁决后 Composable 体内不再调用本方法

        .. seealso:: :class:`flowing.tool.ToolEntry`、
            :meth:`flowing.agent.Agent.tool_call`、
            :meth:`flowing.tool.ToolRegistry.register`、
            :class:`flowing.parser.EntryRef`
        """
        # 第 0 步：归一为 EntryRef（唯一构造通道 = normalize_entries）
        if isinstance(name, EntryRef):
            if alias is not None or body is not None:   # 与 ref 自带字段重复 -> 笔误
                raise FormatError("EntryRef 与 alias/body 不可同传")
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
        if key in self._tool_entries:   # 同 alias 重复添加 = 笔误（绑定层统一 fail-fast，见行为规约）
            raise EntryNameConflictError(key, kind="tool")
        # 第 1 步：body 判别（键集校验 + 逐键去向，规则见行为规约；未知键 -> FormatError）
        #   description -> override_description（PENDING->None；str->Parsable 包装；Parsable 透传）
        #   args -> 逐参数 split_as 判别：dict->override_params / as->param_aliases /
        #           PENDING->空补丁 / 其它->specified（Parsable 包装）
        #   output -> 独立分支：逐字段以 {"schema": 定义} 并入 override_params
        #   inject -> 原样（PENDING->[]）；enabled -> 原样
        override_description, override_params, specified = None, {}, {}
        param_aliases, enabled = {}, True
        # body 判别（键集校验 + 逐键去向，规则见行为规约；未知键 -> FormatError）
        #   description -> override_description（PENDING->None；str->Parsable 包装；Parsable 透传）
        #   args -> 逐参数 split_as 判别：dict->override_params / as->param_aliases /
        #           PENDING->空补丁 / 其它->specified（Parsable 包装）
        #   output -> 独立分支：逐字段并入 override_params（不经 args 关键字校验）
        #   inject -> 原样（PENDING->[]）；enabled -> 原样
        for body_key, body_val in ref.body.items():
            if body_key == "description":
                override_description = _as_parsable_patch(body_val)
            elif body_key == "args":
                if not isinstance(body_val, Mapping):
                    raise FormatError(f"工具覆写 args 必须是映射: {body_val!r}")
                _classify_override_args(body_val, override_params, specified, param_aliases)
            elif body_key == "output":
                # 独立判别分支（B 方案）：「字段名 -> JSON Schema 定义」映射
                # 逐字段并入 override_params（type 等键在此合法，不经 args 的
                # 关键字校验）——「省略字段 = 移除」语义见 FinishTool 规约。
                # spec 未写清处落实：骨架注释的 {"schema": 定义} 包装与
                # apply_param_overrides 的 property->patch 形态不一致，按行为
                # 规约正文「逐字段并入 override_params」落实（不套 schema 键）
                if not isinstance(body_val, Mapping):
                    raise FormatError(f"工具覆写 output 必须是映射: {body_val!r}")
                for field_name, field_def in body_val.items():
                    override_params[field_name] = dict(field_def)
            elif body_key == "enabled":
                enabled = bool(body_val)
            elif body_key == "inject":
                # R-4 已删除：注入写 args 里的 "{{ self.inject('key') }}" 表达式
                raise FormatError("工具覆写体的 inject 键已删除（R-4）：注入请写 "
                                  "args 里的 {{ self.inject('key') }} 表达式")
            else:
                raise FormatError(f"工具覆写体含未知键: {body_key!r}")
        entry = ToolEntry(
            name_alias=key,
            name_ori=name_ori,
            override_description=override_description,
            override_params=override_params,
            specified=specified,
            param_aliases=param_aliases,
            enabled=enabled,
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

        **命名注意**：本方法添加的是**类型绑定条目**（Agent 类 + LLM 可见
        声明 + 覆写），不是 Agent 实例——实例创建走 :meth:`create_subagent`
        / :meth:`invoke_subagent`。「add」的对象是「这个 Agent 如何使用
        某子 Agent 类型」的声明。

        .. rubric:: 功能介绍

        按引用找到子 Agent 类型，判别覆写体并构造
        :class:`flowing.subagents.SubagentEntry`，以别名为 key 写入
        ``self._subagent_entries``。从此该类型进入本 Agent 的 catalog
        （``enabled=True`` 时对 LLM 可见）与可唤起集
        （``invoke_subagent()`` 仅按别名查本表）。

        **两种调用形态**（与 ``add_tool`` 同一管线）：

        - **``.fya`` 装配层**（主调用方）：``subagents:`` 条目解析产出的
          ``EntryRef`` 直接透传——``add_agent(ref)``，此时 ``alias`` /
          ``body`` 必须缺省（与 ``ref`` 自带字段重复 →
          :class:`flowing.errors.FormatError`）；
        - **程序化**（``setup()`` / 运行期）：``add_agent(name, alias=...,
          body=...)``，``body`` 与 ``.fya`` 单键映射项的覆写映射**同构**
          （键集 ``system_prompt`` / ``description`` / ``args`` /
          ``enabled``——**无 ``output``**：输出 schema 覆写是 Tool 面
          概念；R-4：无 ``inject`` 键，注入写 args 里的
          ``"{{ self.inject('key') }}"`` 表达式）；内部经
          :func:`flowing.parser.normalize_entries` 构造 EntryRef。

        ``name`` 接受全部引用形态，与 ``.fya`` 声明完全一致：裸名 /
        ``ns::name`` / 相对路径（``./`` ``../``，相对本 Agent 的
        ``source_dir``）/ 绝对路径 / ``@/`` 锚定路径 / 路径带裸名
        （目录形态按候选链探测）。

        .. rubric:: 行为规约

        - 同步、立即生效：下一次 ``_assemble_context()`` 即渲染进 catalog。
        - **同 alias 重复添加 →
          :class:`flowing.errors.EntryNameConflictError`**（绑定层统一
          fail-fast，与 tool / skill 同口径）。
        - **body 判别（本方法体内，单点维护）**：键集固定为
          ``system_prompt`` / ``description`` / ``args`` / ``enabled``；
          **未知键 → :class:`flowing.errors.FormatError`**（含旧
          ``inject`` 键，R-4 已删除）。
          各键去向：

          - ``system_prompt`` → ``override_system_prompt``；``str``
            包装为 :class:`flowing.parsable.Parsable`，``Parsable``
            透传；``_``（PENDING）→ 空补丁，视为无覆写（``None``）。
            求值时机：子 Agent 创建时一次，上下文为父 Agent 实例；
          - ``description`` → ``override_description``，同上包装；
            求值时机：父 Agent 路由决策 / catalog 渲染时，上下文为
            父 Agent 实例；
          - ``args`` → 逐参数判别（规则本体见
            :class:`flowing.tool.ToolEntry` 行为规约，与本方法同一套
            代码路径）：dict 值 → ``override_params``；键含 ``as`` →
            ``param_aliases``；``_`` → 空补丁；其它值 → ``specified``
            （包装 Parsable，``invoke_subagent()`` 内以父 Agent 实例
            上下文求值）。**差异**（SubagentEntry 行为规约）：
            ``inject`` 目标是子 Agent **初始化参数**而非 ``execute()``
            参数；
          - ``inject`` → ``list[str]`` 原样；``_`` → 空列表；
          - ``enabled`` → 布尔原样。
        - 深层块（``$subagents.<alias>.xxx:``）填回**先于**本方法调用
          （装配层时序约束，与 tool 侧同律）；导航规则见
          :class:`SubagentEntry` 行为规约（含 as 的键仅以别名段寻址）。
        - 边缘情况：``name``/``ref.raw`` 未命中 → 经 :meth:`get_agent_class`
          （携带 ``source_dir``）走文件链惰性解析——文件覆盖
          ``default::``/``builtin::``；仍不命中抛 ``KeyError``
          （``get_agent_class`` 的失败形态）。
        - 非行为：entry 不持有子 Agent 类引用以外的任何实例状态；不写
          持久化（条目表由声明/``setup()`` 重建）。

        :param name: 引用串（全形态，见上）或 ``.fya`` 解析产出的
            ``EntryRef``（此时 ``alias``/``body`` 必须缺省）。
        :param alias: catalog 与 ``invoke_subagent`` 用的别名；缺省按
            ``normalize_entries`` 推断（路径形态经 ``infer_name``
            / ``AGENT_NAMING``）。
        :param body: 覆写映射，与 ``.fya`` 单键映射项的值同构。
        :returns: 新创建的 ``SubagentEntry``。
        :raises flowing.errors.EntryNameConflictError: 同 alias 条目已存在。
        :raises flowing.errors.FormatError: EntryRef 与 ``alias``/``body``
            重复给值；``body`` 含未知键或非法形态。
        :raises KeyError: 引用在注册表与文件链均不命中。

        .. rubric:: 测试案例

        - 前置：``runtime.register_agent_type("payment", PaymentAgent)``
          → 操作：``add_agent("payment", alias="pay")`` → 期望：
          ``_subagent_entries["pay"].name_ori == "payment"``；再次同名
          → ``EntryNameConflictError``。
        - 前置：``body={"system_prompt": "你是收款员", "args":
          {"user_id": _, "trace_id": "{{ self.inject('trace_id') }}"}}``
          → 期望：``override_system_prompt`` 为 Parsable；
          ``override_params`` 含 ``user_id`` 空补丁；``specified`` 含
          ``trace_id`` 的注入表达式（R-4：无独立 inject 通道）。
        - 前置：``body={"output": {...}}``（Tool 面键）→ 期望：
          ``FormatError``（未知键）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.subagents.SubagentEntry`` 构造；
          :func:`flowing.parser.normalize_entries` /
          :func:`flowing.parser.split_as`；:meth:`get_agent_class`；
          ``flowing.parsable.Parsable`` 构造（包装点，与 ``add_tool``
          同律）
        - 被调：``.fya`` ``subagents:`` 条目装配（声明路径，EntryRef
          透传——**本方法即 agent 装配层对 subagents 列表的具名落点**）
          与运行期/``setup()`` 期的显式调用

        .. seealso:: :class:`flowing.subagents.SubagentEntry`、
            :meth:`invoke_subagent`、:meth:`add_tool`（同构管线）
        """
        # 第 0 步：归一为 EntryRef（唯一构造通道 = normalize_entries）
        if isinstance(name, EntryRef):
            if alias is not None or body is not None:
                raise FormatError("EntryRef 与 alias/body 不可同传")
            ref = name
        else:
            from flowing.runtime import AGENT_NAMING   # 局部 import 破环（agent ↔ runtime）
            item = {f"{name} as {alias}" if alias is not None else name: body or {}}
            ref = normalize_entries([item], naming=AGENT_NAMING)[0]
        cls = self.get_agent_class(ref.raw)   # 存在性解析（文件链命中则此刻编译/注册，文件覆盖 default::/builtin::；未命中抛 KeyError）
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
        # 第 1 步：body 判别（键集 system_prompt/description/args/enabled——
        # R-4：无 inject 键；未知键 -> FormatError；args 判别与 add_tool 同规则、
        # 无 output 分支；system_prompt/description 包装 Parsable，PENDING -> 空补丁 None）
        override_system_prompt, override_description = None, None
        override_params, specified, param_aliases = {}, {}, {}
        enabled = True
        for body_key, body_val in ref.body.items():
            if body_key == "system_prompt":
                override_system_prompt = _as_parsable_patch(body_val)
            elif body_key == "description":
                override_description = _as_parsable_patch(body_val)
            elif body_key == "args":
                if not isinstance(body_val, Mapping):
                    raise FormatError(f"子 Agent 覆写 args 必须是映射: {body_val!r}")
                _classify_override_args(body_val, override_params, specified, param_aliases)
            elif body_key == "enabled":
                enabled = bool(body_val)
            elif body_key == "inject":
                raise FormatError("子 Agent 覆写体的 inject 键已删除（R-4）：注入请写 "
                                  "args 里的 {{ self.inject('key') }} 表达式")
            else:
                raise FormatError(f"子 Agent 覆写体含未知键: {body_key!r}")   # 含 Tool 面 output 键
        entry = SubagentEntry(
            name_alias=key,
            name_ori=name_ori,
            override_system_prompt=override_system_prompt,
            override_description=override_description,
            override_params=override_params,
            specified=specified,
            param_aliases=param_aliases,
            enabled=enabled,
        )
        self._subagent_entries[key] = entry
        return entry

    # ────────────────────────── provide / inject / 资源 ───────────────────

    def provide(self, key: str | InjectionKey[Any], value: Any) -> None:
        """在本节点注册 provide 值（写入 ``_provided``）。

        .. rubric:: 功能介绍

        ProvideNode 协议实现（Runtime / Workflow / Agent 同一套模式）。
        同名 key 覆盖写——实例运行期覆盖是合法的动态更新（spec-draft
        §13.4；``inject`` 实时查找不缓存即刻可见，S-37 裁决统一
        「覆盖、无冲突异常」口径，防插件静默覆盖靠 ``InjectionKey``
        前缀约定）。

        .. rubric:: 使用示例

        .. code-block:: python

            async def setup(self, user_id: str):
                self.provide("user_id", user_id)   # 敏感信息走注入通道

        .. rubric:: 行为规约

        - 敏感信息（身份 / 工作区 / 凭证派生值）的正确通道：**不进消息
          流、不进 LLM 上下文、不经网络传输、不落盘**。
        - 非行为：不做深拷贝、不做序列化——存的是对象引用。

        .. rubric:: 调用关系（审计）

        - 调用：无（写入 ``_provided``）
        - 被调：无框架内调用方（公共 API，setup / 钩子中由用户与插件
          调用）

        .. seealso:: :meth:`inject`、:class:`flowing.runtime.ProvideNode`
        """
        self._provided[key] = value   # 同名覆盖写；存对象引用（不深拷贝、不序列化）

    @overload
    def inject(self, key: InjectionKey[T]) -> T: ...
    @overload
    def inject(self, key: str) -> Any: ...
    def inject(self, key: str | InjectionKey[T]) -> T:
        """沿 ``_parent_id`` 链上溯查找 provide 值（终点 = Runtime）。

        .. rubric:: 功能介绍

        统一算法见 ``inject_from(runtime, node, key)``：当前节点
        ``_provided`` → 父节点 → … → ``Runtime._provided``；都找不到
        → ``MissingProvideError(key)``。

        .. rubric:: 行为规约

        - 上溯沿 UID 链（``runtime.get_node(parent_id)``），节点间不持
          对象引用；父已销毁 / 摘除时链断 → 按找不到处理。
        - 跨层共享的正确机制（args 只向下传一层，inject 沿链自动穿透）。
        - :raises flowing.errors.MissingProvideError: 链上溯到底仍无
          key。
        - 类型信息不跨节点：``InjectionKey[T]`` 的 ``T`` 是声明侧约定，
          框架不做运行时校验。

        .. rubric:: 测试案例

        - 前置：祖父 Agent provide ``workspace_root``，中间层未 provide
          → 操作：孙 Agent ``inject("workspace_root")`` → 期望：穿透
          中间层命中祖父的值。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.runtime.inject_from()``（时机：每次调用，
          统一上溯算法）
        - 被调：``flowing.subagents.SubagentEntry.resolve``（时机：inject
          参数沿 provide 链上溯）、``flowing.agent.Agent._normalize``
          （时机：聚合需 provide 链，见其 docstring 归属说明）

        .. seealso::

            - :meth:`provide`、:func:`flowing.runtime.inject_from`
            - :class:`flowing.params.InjectionKey` —— 类型安全键。
        """
        # 统一上溯算法 = flowing.provide.inject_from（S-43 裁决①后 canonical home；
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
        （渲染器 / 连接池 / 缓存）经此按需获取而非在声明层构造。

        .. rubric:: 使用示例

        .. code-block:: python

            async def setup(self):
                self._ui = self.get_resource("ui_renderer", UiRenderer)

        .. rubric:: 行为规约

        - 资源是**跨任务复用**的对象（区别于 Agent 实例不跨任务复用）。
        - :raises flowing.errors.ResourceNotFoundError: 名称未注册。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.runtime.Runtime.get_resource()``（时机：每次
          调用委托）
        - 被调：无框架内调用方（公共 API，setup / 按需阶段由用户调用）

        .. seealso:: :meth:`flowing.runtime.Runtime.register_resource`
        """
        if type_hint is None:
            return self.runtime.get_resource(name)
        return self.runtime.get_resource(name, type_hint)   # 委托 Runtime（未注册 -> ResourceNotFoundError）

    # ────────────────────────── watch / parsable ──────────────────────────

    def watch(self, name: str, handler: WatchHandler | None = None) -> WatchHandler:
        """监听实例属性**赋值**事件——watcher 通道的 ``(new, old)`` 糖。

        .. rubric:: 功能介绍

        回调签名 ``(new_value, old_value) -> None``，**返回值忽略**；
        同步或 async 均可。内部把回调包成 watcher handler
        ``(agent, fu)``，经 ``self.hooks.watch(name, wrapped)`` 注册到
        **watcher 通道**——它不是普通钩子点，不参与改写 /
        ``Intercepted`` / ``shortcut``，永远 fire-and-forget。

        .. rubric:: 设计动机（响应式边界）

        Flowing 无响应式系统——惰性求值 + 正确求值时机。``watch`` 只
        监听**赋值**，不监听解析值变化：``self.c = Parsable("{{ a == b }}")``
        后改 ``a`` 不触发 ``watch("c")``；重新赋值（换 Parsable、赋非
        Parsable 值、赋 ``_``）才触发。「a 变导致 c 重算」两条路径：
        ① 放进求值面让框架自动 resolve；② 自己 watch 显式联动。

        回调不携带 ``agent`` 与 :class:`FieldUpdate`；需要完整值对象时，
        直接经 ``self.hooks.watch`` 注册 ``(agent, fu)`` 形态的 watcher。

        .. rubric:: 行为规约

        - ``name`` 按 ``fnmatch`` pattern 匹配 :class:`FieldUpdate` 的
          ``name`` 字段：字面量即精确匹配；通配符（``"*"`` 等）按
          fnmatch 规则生效。
        - **纯观察 + fire-and-forget**：watcher 在后台任务中执行，
          不阻塞赋值；多次赋值的 watcher 执行顺序不保证；无运行中
          event loop 时 watcher 不触发（赋值照常）。
        - ``handler=None`` 时返回装饰器（``@self.watch("x")``
          写法）；否则注册并原样返回 handler。
        - 语义属外部触发：赋值来源不一定是 Agent 内部；多个 watcher
          被动响应、互不干扰。

        .. rubric:: 调用关系（审计）

        - 调用：``self.hooks.watch(name, wrapped)``（时机：注册时）
        - 被调：无框架内调用方（公共 watcher 糖，用户 / Composable 调用）

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
        """实例属性赋值拦截——watcher 通知的触发点（M-41 最终裁决）
        兼状态键防遮蔽护栏（P3-03 配套裁决「B：检查并报错」）。

        .. rubric:: 行为规约

        - 顺序：构造 :class:`FieldUpdate`（``name`` / ``old`` / ``new``
          快照）→ **fire-and-forget** 通知 watcher 通道
          （``hooks._notify_watch``，不 await）→ 执行写入。赋值
          语义不受 watcher 影响：无改写、无取消、异常不上抛。
        - **状态键防遮蔽护栏**：``name`` 为已注册状态键时**抛**
          :class:`flowing.errors.StateKeyError`——状态量读写统一走
          ``self.state.<key>`` 显式视图（P3-03 配套裁决）；经实例属性
          语法写会与状态键同名并存，造成两处真值静默漂移。状态键的
          写透与 watcher 通知均在 :class:`StateView` 写路径完成。
        - handler 可为同步或 async（后台任务统一 await）；handler 异常
          终止本次 dispatch 链并记录日志，不影响赋值。
        - 无运行中的 event loop 时 dispatch **静默跳过**（赋值照常）。
        - 仅**实例属性赋值**触发；描述符 / 类属性 / ``_`` 前缀骨架字段
          的初始化不经过本机制。
        - 骨架期护栏（P3-03）：``hooks`` / ``_state_bag`` 只经
          ``__dict__.get`` 探查，未建立（管线第 2 步预绑 / 子类先于
          ``super().__init__()`` 赋值）→ 跳过 dispatch / 状态键检查，
          落普通实例属性。
        - 边缘情况：handler 内再次给同名字段赋值造成递归 dispatch——
          框架不做递归防护，属编程错误。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.FieldUpdate`` 构造与
          ``hooks._notify_watch``（时机：写入前，fire-and-forget）；
          ``StateView.__contains__`` / ``StateView.__setitem__``
          （时机：name 为已注册状态键时的写透落盘）
        - 被调：无（Python 实例属性赋值机制触发）

        .. seealso::

            :class:`FieldUpdate`、:meth:`watch`、:class:`StateView`
        """
        if not name.startswith("_"):   # 仅实例属性赋值触发；_ 前缀骨架字段初始化不经过本机制
            old: Any = getattr(self, name, None)   # 字段不存在时规约为 _UNSET 哨兵（flowing.parsable）
            fu = FieldUpdate(name=name, old=old, new=value)   # 写入前构造快照
            # 护栏（P3-03）：hooks 未建立（管线第 2 步预绑 node_id/runtime
            # 早于 __init__）或袋未建立（子类在 super().__init__() 前赋值）
            # 时一律只经 __dict__.get 探查，缺失即跳过对应机制——骨架期
            # 赋值本就不产生观测事件
            hooks = self.__dict__.get("hooks")
            if hooks is not None:
                hooks._notify_watch(self, fu)   # watcher 通道：fire-and-forget，不 await
            # 状态键防遮蔽护栏（P3-03 配套裁决「B：检查并报错」）：
            # 已注册状态键经实例属性语法写会在 __dict__ 落一份普通实例
            # 属性，与状态袋中的持久值形成两份真值静默漂移——fail-fast
            # 指引显式视图。只经 __dict__.get 探袋：
            # 袋未建立（子类在 super().__init__() 前赋值）时落普通实例属性
            bag = self.__dict__.get("_state_bag")
            if bag is not None and name in bag:
                raise StateKeyError(name)
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
        """删除拦截——状态键防遮蔽护栏的删除侧（P3-03 配套裁决）。

        .. rubric:: 行为规约

        ``name`` 为已注册状态键 → 抛 :class:`flowing.errors.StateKeyError`
        （删持久值走 ``del self.state.<key>``）；否则走普通实例属性
        删除。非行为：不触发 watcher（删除不是赋值事件）。

        .. rubric:: 调用关系（审计）

        - 调用：``StateView.__delitem__``（时机：删除已注册状态键）
        - 被调：无（Python ``del`` 语句机制触发）
        """
        bag = self.__dict__.get("_state_bag")   # 护栏（P3-03）：同 __getattr__/__setattr__
        if not name.startswith("_") and bag is not None and name in bag:
            raise StateKeyError(name)
        object.__delattr__(self, name)

    def parsable(self, source: Any) -> Parsable:
        """手动创建已绑定本实例的 :class:`flowing.parsable.Parsable`。

        .. rubric:: 功能介绍

        不走 ``__setattr__`` 拦截的手动通道：产物已绑定 ``_instance=self``，
        ``str()`` / ``resolve()`` 默认以本实例为渲染上下文（合并 ``env``
        / ``config`` 顶层变量）。

        .. rubric:: 使用示例

        .. code-block:: python

            self.greeting = self.parsable("你好 {{ user_id }}")
            self.greeting.resolved      # "你好 Alice" —— 已绑定，现场求值
            # （str() 只展示模板源不求值——定稿，见 parsable.Parsable.__str__）

        .. rubric:: 行为规约

        非行为：框架不为 Parsable 做隐式解包——``_extra`` / entry 覆写
        等求值面外位置拿到的 Parsable 需手动 ``.resolve(context)``。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.parsable.Parsable`` 构造（时机：每次调用，
          绑定 ``_instance=self``）
        - 被调：无框架内调用方（公共手动通道，用户代码调用）

        .. seealso:: :class:`flowing.parsable.Parsable`（五形式与两步渲染）
        """
        p = Parsable(source)
        p._instance = self   # 绑定渲染上下文（内部字段，见 flowing.parsable 内部 API 清单）
        return p

    def snapshot(self, *, keys: set[str] | None = None) -> AgentSnapshot:
        """一致性只读快照：单个 Agent 状态的观测入口。

        .. rubric:: 功能介绍

        返回 :class:`flowing.snapshot.AgentSnapshot`——``node_id`` /
        ``parent_id`` / 消息树摘要 / ``current_head_id`` /
        当前逻辑 Turn 视图 / ``executions`` / 队列摘要 / 模型视图 /
        工具与子 Agent 绑定条目 / 上下文占用估计（``context_usage``，
        :meth:`estimate_context_tokens` 的快照时刻取值）的一次性只读
        视图。供测试断言、repl ``/snapshot``、观测扩展使用。

        .. rubric:: 设计动机

        观测走只读 Info 视图而非直接读内部结构：``_executions`` 的控制
        信号（``cancel`` / ``pause`` Event）、``_pending_turns`` 的
        Future、``_provided`` 的值内容（凭证等敏感值）均不暴露。

        .. rubric:: 使用示例

        .. code-block:: python

            snap = agent.snapshot()
            assert snap.current_turn is None
            assert snap.message_queue.size == 0

        .. rubric:: 行为规约

        - 只读：修改返回对象不影响 Agent；字段为拷贝或 Info 视图。
        - 一致性：单次调用内各字段取同一时刻的读值。
        - ``keys``（S3）：``None``（默认）收集全部切面；指定时只收集
          指定字段（其余为 ``None``——如不需要 ``context_usage`` 的
          锚点扫描开销，可不收它）。需要多切面同一时刻一致 → 同一次
          调用传入全部所需 key。
        - **可序列化**：全部字段 JSON 可序列化（与
          ``Runtime.snapshot`` 同一总括不变量，见 :mod:`flowing.snapshot`）。
        - ``current_turn`` 为 ``None`` 表示空闲（无活跃逻辑 Turn）。
        - 完整字段契约见 :mod:`flowing.snapshot` 模块级 docstring。

        .. rubric:: 测试案例

        - 前置：新建 Agent 未投递消息 → 操作：``snapshot()`` → 期望：
          ``current_turn is None``、``messages.count == 0``。

        .. rubric:: 调用关系（审计）

        - 调用：无（只读组装 ``AgentSnapshot`` 与 Info 视图）
        - 被调：无框架内调用方（观测入口；契约定稿见
          :mod:`flowing.snapshot` 模块级 docstring）

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
            tool_entries=([EntryInfo(alias=e.name_alias, enabled=e.enabled,
                                     agent_type=None)
                           for e in self._tool_entries.values()]
                          if _want("tool_entries") else None),
            subagent_entries=([EntryInfo(alias=e.name_alias, enabled=e.enabled,
                                        agent_type=e.name_ori)
                               for e in self._subagent_entries.values()]
                              if _want("subagent_entries") else None),
            context_usage=(self.estimate_context_tokens()
                           if _want("context_usage") and model is not None else None),   # 上下文占用估计投影（锚点实测+尾部估算，纯观测）；模型未解析为 None
        )   # 单次调用内各字段取同一时刻读值

    # ────────────────────────── 工作循环与逻辑 Turn（内部） ─────────────────

    async def _work_loop(self) -> None:
        """常驻消息工作循环（每个 Agent 一个 Task）。

        **内部 API，不属稳定契约。**

        .. rubric:: 行为规约

        .. code-block:: python

            while True:
                await self._pause_gate.wait()      # 检查点 ①：dequeue 前（仅 pause）
                msgs = await self._dequeue()       # list[Message]（可覆写）
                waiters = [self._pending_turns.pop(m.id, None) for m in msgs]
                await self._run_turn(msgs, waiters)

        - 启动时机：``after_create`` / ``after_recover`` 完成即启动；
          ``destroy()`` 时取消。消费保证：入队即会被消费。
        - 串行：一个 Agent 一个工作循环、同一时刻一个逻辑回合；活跃回合
          中入队的消息自然排队。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent._dequeue()`` 与
          ``flowing.agent.Agent._run_turn()``（时机：每轮循环）、
          ``_pause_gate.wait()``（检查点 ①）
        - 被调：创建 / 恢复管线（时机：``after_create`` /
          ``after_recover`` 完成即启动常驻 Task；``destroy()`` 时取消）

        .. seealso:: :meth:`_dequeue`、:meth:`_run_turn`
        """
        while True:
            await self._pause_gate.wait()      # 检查点 ①：dequeue 前（仅 pause）
            msgs = await self._dequeue()       # list[Message]（可覆写 drain/合并策略）
            waiters = [self._pending_turns.pop(m.id, None) for m in msgs]
            try:
                await self._run_turn(msgs, waiters)
            except Exception:
                # 回合级异常的兜底闸（「钩子抛异常不再楔死 agent」的收口点）：
                # _run_turn 的 except 帧保留异常上抛以保逐层审计，工作循环在
                # 此记录后继续消费——waiters 已由 _run_turn 的 finally 喂饱
                # （destroy 的第 1 步另有兜底），此处只保证循环存活
                _logger.exception("agent %s: turn crashed", self.node_id)

    async def _dequeue(self) -> list[Message]:
        """出队扩展点：核心默认**一条**，可覆写实现 drain / 合并策略。

        **内部 API，不属稳定契约。**

        .. rubric:: 行为规约

        - 默认实现（R-13 裁决：「等消息」与「取消息」拆分）：
          ``await self._message_queue.wait_not_empty()``（阻塞到队列非空）
          → dispatch ``before_dequeue``（此刻确实即将出队）→
          ``self._message_queue.dequeue_nowait()``（非阻塞取；**返回
          ``None`` = 钩子在此窗口扔掉了消息**（``cancel_queued`` /
          ``remove``）→ 回到等待、重新派发 before——每条真正出队的
          消息之前恰好一次 before 派发）→ dispatch ``after_dequeue``
          （可**变换**返回的消息列表）。
        - 覆写管「多条 / 策略」（``drain_all()`` / ``take_while()`` 合并、
          批量、按来源分组），钩子管「观察 / 变换」——分工不混。
        - 覆写批量后核心通用收尾天然兼容（resolve 回合内所有等待者，
          共享同一 ``TurnResult``）。

        .. rubric:: 使用示例

        .. code-block:: python

            def use_message_drain(agent):
                async def _drain_dequeue():
                    return await agent._message_queue.drain_all()
                agent._dequeue = _drain_dequeue

        .. rubric:: 调用关系（审计）

        - 调用：``before_dequeue`` / ``after_dequeue`` dispatch 与
          ``flowing.message.MessageQueue.dequeue()``（时机：默认实现
          每次出队）
        - 被调：``flowing.agent.Agent._work_loop``（时机：每轮循环）

        .. seealso:: :class:`flowing.message.MessageQueue`、:meth:`_work_loop`
        """
        while True:   # R-13：等消息与取消息拆分；钩子扔消息则重新等待
            await self._message_queue.wait_not_empty()   # 阻塞到非空
            await self.hooks.before_dequeue.dispatch(self)   # 即将出队时派发（观察队列，无 value）
            msg = self._message_queue.dequeue_nowait()   # 非阻塞取；None = 钩子扔掉了 -> 重等
            if msg is not None:
                break
        msgs = await self.hooks.after_dequeue.dispatch(self, [msg])   # 可变换返回的消息列表
        return msgs

    async def _run_turn(
        self,
        msgs: list[Message],
        waiters: list[asyncio.Future[TurnResult] | None],
    ) -> None:
        """逻辑 Turn 执行主体（消息级完整时序）。

        **内部 API，不属稳定契约。**

        .. rubric:: 行为规约（时序不变量）

        1. 创建 ``TurnContext`` → ``self.current_turn = turn``（标记回合
           物质存活）→ 新建 ``self._turn_abort`` →
           ``turn.pending_messages = msgs``（出队批次暂存，**未挂树**）。
        2. dispatch ``before_turn``（value 为 ``TurnContext``）：handler 可读 /
           改写 ``turn.pending_messages``——追加 reminder 为**附加式**（排在
           触发消息之后，随批次一起挂树持久化）；handler ``raise
           Intercepted`` 硬阻断本回合，``pending_messages`` **全部丢弃**
           （不落盘、不留痕——M-29 后续裁决：出队后挂树前中断 = 丢弃刚出队
           的消息，此丢失语义为显式契约；崩溃落在同一窗口同理）。
        3. ``pending_messages`` 逐条经 :meth:`_append_message` 挂树（是树节点，
           不再是「turn 内部首元素」；各条照常触发 ``before_turn_append``）；
           挂毕 ``pending_messages`` 清空。
        4. 内层循环 ``while True``：

           - **检查点 ②（provider_gen 前）**：``await self._pause_gate.wait()``
             **在前**，``_turn_abort.is_set()`` 判定**在后**（置位则幂等
             置位 ``turn.aborted`` → dispatch ``before_turn_abort`` →
             ``break``）。
           - **检查点 ②.5（urgent 吸收，夹在 ② 的 pause 与 abort 判定
             之间）**：``peek(INTERRUPT)`` 命中 → ``take_while`` 连 drain
             ``INTERRUPT``+``STEER`` 两带（优先级有序保证二者构成队首
             连续段），逐条 ``_append_message`` 挂树（其等待者从
             ``_pending_turns`` 并入本回合 waiters，收尾共享本回合
             ``TurnResult``）→ ``self.abort_turn()`` 置 Event，由紧随的
             abort 判定统一收口；否则 ``peek(STEER)`` 命中 → 仅 drain
             ``STEER`` 这一优先级带、挂树**不** abort——紧随的
             ``_assemble_context`` 当轮即可见这些消息。②.5 吸收的消息
             **不经过** ``before_turn``——这不构成拦截绕过：Guardrail 类
             内容审核/速率拦截的位置在**入队时**的 ``before_enqueue``
             （一切入队消息的必经闸），``before_turn`` 只管「出队批次的
             附加注入与整批阻断」，两类闸门分工不同。
           - ``context = self._assemble_context()``；``response = await
             self.provider_gen(context)``；异常 → 构造 ``ProviderErrorContext`` →
             dispatch ``on_provider_error`` → ``can_continue=False`` 则
             ``break``，``True`` 则 ``continue``（handler 内已 sleep /
             改模型 / ``abort_turn()``）。
           - ``response.message is not None`` → ``_append_message``。
           - ``response.cancelled or _turn_abort.is_set()`` →
             幂等置位 ``turn.aborted`` → dispatch ``before_turn_abort`` →
             ``break``（cancelled 也走 abort 路径）。
           - 工具调用循环（并行版）：收集本响应的全部 ``tool_call``
             block；**并行批次前是检查点 ③**（pause 在前、abort 在后；
             置位则幂等置位 ``turn.aborted`` → dispatch
             ``before_turn_abort`` → 跳过本批全部工具）；非 ``tool_call``
             block 跳过；对全部 ``tool_call`` 以 ``asyncio.gather`` 并行
            执行 :meth:`tool_call`，随后按响应中的原始顺序逐条
             ``result.as_message(tc.id)`` → ``_append_message`` 挂树。
             finish 置位转移检测在并行块收口后统一处理（同一批内多个工具
             置位 ``finish_output`` 时，仅首个在原始顺序中触发置位的 TOOL
             消息标 ``turn_end=True``）。
           - ``response.finish or turn.finish_output is not None`` →
             ``break``（自然结束；后者 = finish 工具置位——工具段已照常
             执行完，同响应的并行工具不受影响）。

           ``before_turn_abort`` 每回合至多触发一次：三处 abort 判定互斥
           （各自随即 ``break``），且 ``turn.aborted`` 幂等置位。

        5. ``finally``（所有路径——释放回合身份牌先于一切钩子）：
           ``current_turn = None``（回合物质终结；钩子抛异常不再楔死
           agent。head 随每条消息挂树即时前移，回合末无结算写入；
           空 turn 无 append，head 自然不动）→ dispatch ``after_turn``
           （所有路径**唯一**收尾观察点——``after_turn_abort`` /
           ``after_turn_finished`` 已删除：路径分流由 handler 读
           ``turn.aborted`` 承担，「最后一刻」无具名场景支撑，不为
           无场景的区分预留钩子点）→ ``result = build_turn_result(
           turn, self, intercepted=..., error=...)``（S-28：拦截/异常
           信号由 except 帧显式传入）→ **写 ``self.last_result``**
           （``turn.finish_output`` 非 None → 该 dict；否则
           ``result.final_text or None``——在 resolve waiters 之前，
           等待方醒来即见本轮产物；abort/cancel/error 同样覆写）→
           遍历 waiters，
           ``fut is not None and not fut.done()`` → ``set_result``
           （drain 合并共享同一 ``TurnResult``）。

        .. rubric:: 边缘情况

        - 空 Turn（启动即 abort）：不产生
          新树节点，``current_head_id`` 不变。
        - 异步工具：对 Turn 循环完全透明——「消息来得晚一些」。
        - 子 Agent = 特殊工具调用：同步阻塞等结果或异步收据 + EVENT
          消息，两种模式对父 Turn 循环同构。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.TurnContext`` 构造、
          ``flowing.agent.Agent._append_message()``、
          ``flowing.agent.Agent._assemble_context()``、
          ``flowing.agent.Agent.provider_gen()``、
          ``flowing.agent.Agent.tool_call()``、
          ``flowing.agent.Agent.abort_turn()``（时机：检查点 ②.5 吸收到
          ``INTERRUPT`` 消息时）、``flowing.message.MessageQueue.peek()``
          / ``take_while()``（时机：检查点 ②.5 urgent 吸收）、
          ``flowing.agent.build_turn_result()`` 与 turn 族钩子 dispatch
          （含 ``before_turn_abort``，时机：均见本方法时序不变量）
        - 被调：``flowing.agent.Agent._work_loop``（时机：每批出队
          消息）

        .. seealso::

            - :meth:`_append_message` —— 挂树 + 落盘统一入口（五步）。
            - :meth:`provider_gen` / :meth:`_assemble_context` —— 内循环两步。
            - :class:`TurnContext` —— 执行期载体。
        """
        # 1. 创建 TurnContext
        turn = TurnContext(started_at=datetime.now(), message_ids=[])
        self.current_turn = turn   # 标记回合物质存活
        self._turn_abort = asyncio.Event()   # 每个逻辑 Turn 独立新建
        turn.pending_messages = msgs   # 出队批次暂存（未挂树）
        intercepted = False                    # S-28：结局信号由 except 帧显式
        error: BaseException | None = None     # 传入 build_turn_result，不落 TurnContext
        last_stop_reason = ""   # R-09：末次 provider_gen 响应的原始停止原因（completed 结局的 finish_reason 来源）
        try:
            # 2. before_turn（附加式注入 / Intercepted 阻断）；
            # 异常路径：Intercepted -> pending_messages 全部丢弃不落盘（显式
            # 丢失语义），TurnResult status="blocked"（Intercepted 未在本模块
            # 具名引入，见 v2 存疑 S2-02）
            turn = await self.hooks.before_turn.dispatch(self, turn)
            # 3. 批次逐条挂树（各条照常触发 before_turn_append）
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
                    turn.aborted = True   # 幂等置位（C-03 裁决：不立 _mark_abort 方法，语义即此布尔赋值）
                    await self.hooks.before_turn_abort.dispatch(self, turn)   # abort 判定收口处统一触发（每回合至多一次）
                    break
                context = self._assemble_context()
                try:
                    response = await self.provider_gen(context, by="_turn")   # 主 Turn 来源标记
                except ContextLengthError:
                    raise   # 不可重试例外：不经过 on_provider_error，直接上抛
                except Exception as exc:
                    qctx = ProviderErrorContext(error=exc, provider=self.model.provider,
                                             model=self.model)
                    qctx = await self.hooks.on_provider_error.dispatch(self, qctx)
                    if not qctx.can_continue:
                        error = exc   # 回合中断（Agent 存活）——中断原因即本异常，结局 "error"
                        break
                    continue    # handler 已完成退避/换模型/abort_turn()
                if response.message is not None:
                    if response.message.usage is not None:
                        # S-13：turn 级用量累加（追加消息上同一 Usage 对象的
                        # 引用，收尾由 build_turn_result 聚合；ProviderResponse
                        # 不携带 usage，消息是唯一载体）
                        turn.usages.append(response.message.usage)
                    # S-14：turn_end 由 agent 层写入——turn 随本条消息关闭
                    #（自然 finish 或取消/abort）→ True；provider 的 finish
                    # 只是关闭原因之一，adapter 不写 turn_end；finish 工具
                    # 置位路径的 turn_end 落在其配对 TOOL 消息上（见下
                    # 「置位转移检测」），本条 PROVIDER 消息不追溯改写
                    #（已挂树落盘）
                    response.message.turn_end = response.finish or response.cancelled
                    await self._append_message(response.message, turn)
                last_stop_reason = response.provider_data.get("stop_reason", "")   # R-09：completed 结局的 finish_reason 来源
                if response.cancelled or self._turn_abort.is_set():
                    turn.aborted = True   # cancelled 也走 abort 路径
                    await self.hooks.before_turn_abort.dispatch(self, turn)
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
                        await self.hooks.before_turn_abort.dispatch(self, turn)
                        # 跳过本批全部工具；已执行工具（无）结果仍按不执行处理
                    else:
                        block_finish_was = turn.finish_output   # 置位转移检测：并行块开始前

                        async def _run_one(tc: ToolCall):
                            before = turn.finish_output
                            result = await self.tool_call(tc)
                            after = turn.finish_output
                            return tc, result, before, after

                        items = await asyncio.gather(
                            *(_run_one(tc) for tc in (ToolCall.from_block(b) for b in tool_blocks)),
                            return_exceptions=True,
                        )
                        # 按响应中的原始顺序挂树；执行中的异常在全部结果落树后再上抛
                        errors: list[BaseException] = []
                        marked = False
                        for item in items:
                            if isinstance(item, BaseException):
                                errors.append(item)
                                continue
                            tc, result, before, after = item
                            result_msg = result.as_message(tc.id)   # 配对锚接线（tc.id → tool_call_id）
                            if (block_finish_was is None and not marked
                                    and before is None and after is not None):
                                result_msg.turn_end = True   # finish 置位路径：首个触发置位的 TOOL 消息标 turn_end
                                marked = True
                            await self._append_message(result_msg, turn)   # 结果消息挂树
                        if errors:
                            raise errors[0]   # 框架错误：已成功的结果已挂树，异常继续走 _run_turn 的 except 通道
                if turn.aborted:
                    break
                if response.finish or turn.finish_output is not None:
                    break   # 自然结束（含 finish 置位：工具段照常执行完再收尾）
        except Intercepted:   # S-28：拦截走异常通道（捕获结局信号）
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
            # 5b. 观察钩子：turn 对象作为 value 照常传入（身份释放 ≠ 产物消失；
            # handler 契约 (agent, value) 不受影响；此期间 agent.current_turn
            # 已为 None，绕开 value 读它的写法会看到空闲）
            await self.hooks.after_turn.dispatch(self, turn)   # 所有路径唯一收尾观察点；handler 读 turn.aborted 分流
            # 5c. 交付
            turn.finished_at = datetime.now()
            result = build_turn_result(turn, self, intercepted=intercepted,
                                       error=error,
                                       finish_reason=last_stop_reason)   # S-28：信号显式传参（R-09：completed 结局的 stop_reason 同通道）
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

    async def _append_message(self, msg: Message, turn: TurnContext) -> None:
        """消息级持久化统一入口：turn 钩子 + :meth:`push` + turn 记账。

        **内部 API，不属稳定契约。**

        .. rubric:: 行为规约

        时序：dispatch ``before_turn_append``（可改写 / ``Intercepted``）
        → :meth:`push`（核心写路径：① 设 ``parent_id = current_head_id``
        → ② 挂入 ``_messages`` → ③ ``_persist_message`` 落盘 → ④
        ``current_head_id`` 前移）→ ⑤ ``turn.message_ids.append(msg.id)``
        （记 turn 索引）→ dispatch ``after_turn_append``（观察；此后改写
        不进树——消息已落盘）。

        - **消息完整后才经过本方法**——流式进行中的增量（尚未定型为
          消息的 delta 累积态）不经过它，天然不落盘（持久化时机的
          自然结果，非主动丢弃）；流式被中断时，已累积内容定型为
          一条 ``partial=True`` 的完整消息，照常挂树落盘。
        - 副线（``side_query``）消息不经过本方法（副线不挂树不落盘）。
        - ``push`` 不 dispatch turn 钩子、不写 ``turn.message_ids``，
          由本方法补齐这两部分。

        .. rubric:: 调用关系（审计）

        - 调用：``before_turn_append`` / ``after_turn_append`` dispatch、
          :meth:`push`（时机：见本方法时序规约）
        - 被调：``flowing.agent.Agent._run_turn``（时机：挂树批次与
          内层循环每条完整消息）

        .. seealso:: :meth:`push`、:meth:`_persist_message`、
            :attr:`current_head_id`
        """
        msg = await self.hooks.before_turn_append.dispatch(self, msg)   # 可改写 / Intercepted
        self.push(msg)                             # 核心写路径：parent_id → _messages → 落盘 → head 前移
        turn.message_ids.append(msg.id)            # 记 turn 索引
        await self.hooks.after_turn_append.dispatch(self, msg)   # 观察（此后改写不进树）

    def _persist_message(self, msg: Message) -> None:
        """提交一条完整消息落盘（write-behind：同步排队即返）。

        **内部 API，不属稳定契约。**

        .. rubric:: 行为规约

        序列化 ``msg`` 为消息行（含 ``id`` / ``parent_id`` /
        ``turn_end`` / ``partial`` 等）→ ``self._tree_store.submit(行)``
        ——**同步返回不代表已落盘**（契约①「产生即排队」）。墓碑阈值压缩
        不再由本方法触发——它是 :class:`flowing.persistence.FileRecordStore`
        drain 任务在「队列排空后」的自主行为（S-31 裁决；append-only
        约束与崩溃安全语义不变，见
        :mod:`flowing.persistence` 模块规约）。

        - poison 传染：store 已进入 poison 态时本方法**同步重抛**首次
          落盘异常（错误在挂树现场爆出，而非静默分叉）。
        - 副线（``side_query``）消息不经过本方法（不落盘语义不变）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.persistence.RecordStore.submit()``（时机：
          每次提交；序列化 ``msg`` 为消息行 dict）
        - 被调：``flowing.agent.Agent._append_message``（时机：五步
          第 ③ 步）、``flowing.message.MessageChain.insert`` /
          ``branch``（时机：手术新增消息行）

        .. seealso::

            - :meth:`_append_message` —— 调用方（五步规约）。
            - :meth:`_persist_tree_record` —— 变更记录行通道（对偶）。
            - :class:`flowing.persistence.RecordStore` —— 落盘后端。
        """
        # 序列化 msg 为消息行 dict -> self._tree_store.submit(行)
        # （同步排队即返；poison 态时 submit 重抛首次落盘异常）
        # X2 冻结点：to_record 是消息 ↔ 行的唯一序列化点（flowing.message）
        self._tree_store.submit(to_record(msg))

    def _persist_tree_record(self, record: dict) -> None:
        """提交一条**变更记录行**落盘（tombstone / update / move /
        邻接调整；write-behind 同步排队）。

        **内部 API，不属稳定契约。**

        .. rubric:: 功能介绍

        :class:`flowing.message.MessageChain` 五 op 的落盘通道（S-31
        裁决新增）：与 :meth:`_persist_message` 共用同一
        ``_tree_store`` 队列——消息行与变更行在同一 FIFO 中按提交序
        落盘，重放时按行序应用（消息行建树、变更行做手术）。

        .. rubric:: 行为规约

        - ``record`` 约定字段：``{"type": "tombstone", "id": ...}`` /
          ``{"type": "update", "id": ..., "content": [...]}`` /
          ``{"type": "move", "id": ..., "parent_id": ...}``；
          ``insert`` 的邻接调整对被重挂的每个子消息各提交一条
          ``move`` 行。
        - 排队语义 / poison 重抛与 :meth:`_persist_message` 相同
          （同一 store，同一契约）。
        - 非行为：不校验 record 语义（哪条消息该删、环检测等是
          ``MessageChain`` 的职责）；不改变内存权威（op 已先改内存）。

        .. rubric:: 测试案例

        - ``chain.remove("m2")`` → drain 后文件尾部含 tombstone 行；
          重放后权威链不含 ``m2``。
        - ``chain.reparent("m4", to="m1")`` → 文件尾部含 move 行；
          重放后 ``m4.parent_id == "m1"``。
        - poison：注入落盘异常后调 ``chain.update(...)`` → 期望：
          同步重抛同一异常。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.persistence.RecordStore.submit()``（时机：
          每次变更记录提交）
        - 被调：``flowing.message.MessageChain.remove`` / ``update`` /
          ``reparent`` / ``insert``（邻接调整）（时机：五 op 完成内存
          修改后）

        .. seealso:: :meth:`_persist_message`（消息行通道）、
            :class:`flowing.message.MessageChain`（变更行的产生者）。
        """
        self._tree_store.submit(record)   # 与消息行同一 FIFO，按提交序落盘

    def _render_subagent_catalog(self) -> str:
        """渲染 ``<available_subagents>`` catalog 块（子智能体 catalog 的
        唯一渲染槽位）。

        **内部 API，不属稳定契约。**

        .. rubric:: 功能介绍

        对每个 ``enabled=True`` 的
        :class:`flowing.subagents.SubagentEntry` 调
        :meth:`flowing.subagents.SubagentEntry.catalog_view` 在 Python 侧
        预计算视图 dict，再经模板一次性渲染（模板只负责排布）。模板取
        ``getattr(self, "subagent_catalog_template", None) or
        DEFAULT_SUBAGENT_CATALOG_TEMPLATE``——Agent 对象自己有
        ``subagent_catalog_template`` 属性则用其值，否则用内置缺省。

        .. rubric:: 行为规约

        - 无 ``enabled=True`` 条目 → 返回 ``""``（整块不注入）。
        - 渲染经 Parsable TEMPLATE 语义（include 基准为本 Agent 的
          ``source_dir``，P3-08）；渲染异常 fail-fast 上抛，不静默降级。
        - 每次调用现场渲染，无缓存（``cache="dynamic"`` 语义——enabled
          状态与覆写运行时可变）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.subagents.SubagentEntry.catalog_view()``（时机：
          每个 ``enabled=True`` 条目）；``self.parsable(...).resolve(...)``
          （时机：模板渲染，上下文 ``{"entries": views, "agent": self}``）
        - 被调：``flowing.agent.Agent._assemble_context`` 的子智能体
          catalog 块（时机：每次上下文组装现场渲染，
          ``cache="dynamic"`` 语义）

        .. seealso::

            :data:`flowing.subagents.DEFAULT_SUBAGENT_CATALOG_TEMPLATE`、
            :attr:`subagent_catalog_template`。
        """
        views: list[dict[str, Any]] = [
            entry.catalog_view(self)   # Python 侧预计算视图（description/params_xml 已解析）
            for entry in self._subagent_entries.values()
            if entry.enabled   # enabled=False 不进 catalog（调用方负责过滤）
        ]
        if not views:
            return ""   # 空列表渲染为 ""（整块不注入）
        template = (
            getattr(self, "subagent_catalog_template", None)   # Agent 级覆写槽位（类属性 / fya 同名字段落入实例属性或 _extra，getattr 同样命中）
            or DEFAULT_SUBAGENT_CATALOG_TEMPLATE
        )
        return self.parsable(template).resolve({"entries": views, "agent": self})   # Parsable TEMPLATE 语义，include 基准 source_dir（P3-08）；渲染异常 fail-fast 上抛

    def _assemble_context(self) -> Context:
        """组装 ``Context``：每次调用现场求值（无缓存）。

        **内部 API，不属稳定契约。**

        .. rubric:: 行为规约

        三部分（``Context`` 不是扁平消息列表）：

        1. 遍历 ``prompt_blocks``（跳过 ``enabled=False``）逐块
           ``resolve()`` → ``list[PromptSegment]``（保留 cache 标记——
           仅是 adapter 意图标记，框架本地不缓存）；``prompt_blocks[0]``
           的 ``{{ self.system_prompt }}`` 惰性引用在此触发解析。
        2. 消息路径：从 ``current_head_id`` 沿 ``parent_id`` 上溯到根，
           反转得根 → head 的消息序列；半截 turn 的已落盘消息照常
           包含（M-28 裁决，不再截断；孤立 tool_call 由恢复时合成的
           ``synthetic`` 占位 TOOL 消息封闭成对——``tool_call_id=<孤立
           调用 id>``、``tool_status="error"``、``content=[TextBlock(占位
           说明)]``，不再是合成 ToolResultBlock）。
        3. ``_visible_tools()`` → ``list[ToolDefinition]``（仅
           ``enabled=True`` 条目，经 ``llm_definition()``）。**无隐式
           附加**——``subagent-invoke`` / ``finish`` 等内置工具必须由
           用户显式声明（``.fya`` 的 ``tools:`` 或
           ``add_tool("subagent-invoke")``）才进入可见面
           （S-19 最终裁决：一切工具——含技能、子智能体——以用户
           声明为准，框架不隐式添加）。
        4. 子智能体 catalog：``<available_subagents>`` 块经
           ``_render_subagent_catalog()`` 现场渲染并入
           ``system_prompt`` 段（``cache="dynamic"``；此前该装配无
           具名代码点，本次补上）。

        - 现场求值是功能正确性前提（环境变量、实例属性、模式状态永远
          最新），不是性能优化。
        - 同步方法：渲染为同步 Jinja2 求值；``before_provider_gen`` 钩子在
          ``provider_gen()`` 内对本产物仍可改写（但**不推荐**直接改写
          ``messages``——内容增删走持久化路径，M-29 裁决）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.context.PromptBlockList`` 逐块 ``resolve()``
          与 ``flowing.agent.Agent._visible_tools()``（时机：每次调用
          现场求值，无缓存）
        - 被调：``flowing.agent.Agent._run_turn``（时机：内层循环检查
          点 ② 之后每轮）、``flowing.agent.Agent.side_query``（时机：
          同一机制，每次副线调用）

        .. seealso::

            - :class:`flowing.context.Context` —— 产物结构。
            - :class:`flowing.context.PromptBlockList` —— 块管理语义。
            - :attr:`TurnContext.pending_messages` —— 回合开头附加式
              注入的载体（M-29 裁决）。
        """
        # 1. 遍历 prompt_blocks（__iter__ 跳过 enabled=False）逐块现场求值
        # 分段装配（「未见具名符号」落实）：PromptSegment(content=求值文本,
        # cache/name 从来源块原样透传)
        segments: list[PromptSegment] = []
        for block in self.prompt_blocks:
            segments.append(PromptSegment(
                content=str(block.content.resolve(self)),   # prompt_blocks[0] 的 {{ self.system_prompt }} 惰性引用在此触发
                cache=block.cache, name=block.name))
        # 2. 消息路径：从 current_head_id 沿 parent_id 上溯到根，反转得根 -> head
        messages: list[Message] = []
        cursor = self.current_head_id
        while cursor is not None:
            msg = self._messages.get(cursor)
            if msg is None:
                break   # 孤儿链断点（中间消息被 chain.remove 且子树未先 reparent）：上溯到断点即终止
            messages.append(msg)
            cursor = msg.parent_id
        messages.reverse()
        # （半截 turn 已落盘消息照常包含；孤立 tool_call 由恢复时合成的
        # synthetic 占位 TOOL 消息封闭成对——tool_status="error"、
        # content=[TextBlock(占位说明)]，见 _restore 步骤 ①b）
        tools = self._visible_tools()
        # S-19 最终裁决：无隐式附加——subagent-invoke / finish 等内置工具
        # 需用户经 tools: / add_tool 显式声明才进入可见面
        # <available_subagents> 块经 _render_subagent_catalog() 现场渲染，
        # 并入 system_prompt 段（cache="dynamic" 语义，无本地缓存）
        subagent_catalog = self._render_subagent_catalog()
        if subagent_catalog:
            # 并入 system_prompt 段（段名「subagent-catalog」为落实命名，
            # 规约未具名）；cache="dynamic"（enabled 状态与覆写运行时可变）
            segments.append(PromptSegment(
                content=subagent_catalog, cache="dynamic", name="subagent-catalog"))
        return Context(system_prompt=segments, tools=tools, messages=messages)

    def _visible_tools(self) -> list[ToolDefinition]:
        """当前 ``enabled=True`` 工具条目经 ``llm_definition()`` 的定义列表。

        **内部 API，不属稳定契约。** 每次现场生成，无缓存；``enabled``
        运行时可变（模式切换），故不可缓存。子 Agent 不走本方法——其
        LLM 可见声明是 catalog XML（见
        :meth:`flowing.subagents.SubagentEntry.catalog_view`）。

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.tool.ToolEntry.llm_definition()``（时机：每个
          ``enabled=True`` 条目，每次现场生成）
        - 被调：``flowing.agent.Agent._assemble_context``（时机：组装
          第 3 步）

        .. seealso:: :meth:`flowing.tool.ToolEntry.llm_definition`
        """
        defs: list[ToolDefinition] = []
        for entry in self._tool_entries.values():
            if entry.enabled:
                defs.append(entry.llm_definition(self.runtime, self))   # 每次现场生成，无缓存
        return defs
