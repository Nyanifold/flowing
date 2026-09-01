"""``flowing.plugins.cron.executors`` —— 执行器类型、模板与默认执行器。

本模块定义定时扩展的「第 2 层」（怎么做）：执行器类型
:data:`CronExecutor`、渲染模板类型 :data:`CronTemplate`、三个默认
模板常量与两个默认执行器（:func:`default_message_executor` /
:func:`default_tool_executor`）。执行器是插件级代码配置，不随任务
落盘——任务恢复后自动跟随当前这次加载的执行策略。

.. seealso:: :mod:`flowing.plugins.cron` （扩展的启用方式与整体契约）、
    :mod:`flowing.plugins.cron.cron` （插件与 ``use_cron``）
"""

from collections.abc import Awaitable, Callable
from uuid import uuid4

from flowing.agent import Agent
from flowing.message import Message, MessageKind, MessagePriority, TextBlock
from flowing.tool import ToolCall, output_to_blocks

from .models import CronAction, CronFireContext


CronExecutor = Callable[[Agent, "CronJob", "CronAction", "CronFireContext"], Awaitable[None]]
"""执行器类型：``async (agent, job, action, ctx) -> None``。

``action`` 是本次生效的动作——可能是 ``on_cron_trigger`` handler 改写
后的版本，``job.action`` 始终是落盘的原始定义，执行器应消费前者。
``ctx`` 是只读触发上下文（合并计数、理想 / 实际时刻）。

.. seealso:: :class:`CronPlugin` （``executors`` 参数）、
    :class:`CronFireContext`、:func:`default_message_executor`
"""

CronTemplate = str
"""渲染模板类型：Jinja2 模板源字符串（含 ``$./file.j2`` FILE_REF 形式）。

渲染器槽位只接受模板字符串，不接受可调用对象。渲染经 Parsable TEMPLATE
语义（第二步 Jinja2；include 走框架自定义加载器，基准与 ``$`` 引用同源）。
模板按 kind 分键注册（``CronPlugin(templates={...})``），上下文变量表：

- ``job`` （``CronJob``）、``action`` （``CronAction``，本次生效版本）
- ``scheduled_at`` / ``fired_at`` / ``coalesced_count`` /
  ``last_fired_at`` （``CronFireContext`` 摊平）

模板是默认执行器路径的组件，不是强制管线——自定义执行器可以完全绕开
它。渲染异常（语法错等）fail-fast 上抛，不静默降级（配置错误应尽早
暴露）。

.. seealso:: :data:`DEFAULT_MESSAGE_TEMPLATE`、
    :data:`DEFAULT_TOOL_NOTICE_TEMPLATE`
"""

DEFAULT_MESSAGE_TEMPLATE: str = (
    '<cron-fire jobId="{{ job.id }}" cron="{{ job.cron }}"\n'
    '           recurring="{{ "true" if job.recurring else "false" }}"'
    ' coalescedCount="{{ coalesced_count }}">\n'
    '{{ action.prompt }}\n</cron-fire>'
)
"""默认 message 模板：产出 ``<cron-fire>`` XML 文本（Jinja2 模板源）。

形态：

.. code-block:: text

    <cron-fire jobId="daily-consolidation" cron="0 3 * * *"
               recurring="true" coalescedCount="3">
    请执行每日沉淀
    </cron-fire>

``coalescedCount`` 来自触发上下文的 ``coalesced_count``——Agent 据此
知道这是准时触发（1）还是合并交付（大于 1）。渲染器槽位只收 Jinja2
模板字符串（含 ``$./file.j2`` FILE_REF 形式），不接受可调用对象；
上下文变量表见 :data:`CronTemplate`。

.. seealso:: :data:`DEFAULT_TOOL_NOTICE_TEMPLATE`、
    :func:`default_message_executor`
"""

DEFAULT_TOOL_NOTICE_TEMPLATE: str = (
    '<cron-fire jobId="{{ job.id }}" cron="{{ job.cron }}"'
    ' coalescedCount="{{ coalesced_count }}">\n'
    '休眠期错过了 {{ coalesced_count }} 次对工具 {{ action.tool }}'
    ' 的定时调用（参数：{{ action.args }}），请自行决定是否补做。\n'
    '</cron-fire>'
)
"""默认 tool_call 通知模板：合并降级时的告知文本（Jinja2 模板源）。

默认 tool 执行器在 ``coalesced_count > 1`` 时不真执行工具，改用本模板
渲染一条通知（XML 内说明「错过了 N 次对 ``action.tool`` 的定时调用」及
其参数摘要），Agent 醒来自行决定是否补做。模板上下文变量表与
:data:`DEFAULT_MESSAGE_TEMPLATE` 相同（见 :data:`CronTemplate`）。

.. seealso:: :data:`DEFAULT_MESSAGE_TEMPLATE`、
    :func:`default_tool_executor`
"""

DEFAULT_TOOL_RESULT_TEMPLATE: str = (
    '<cron-fire jobId="{{ job.id }}" cron="{{ job.cron }}"'
    ' coalescedCount="{{ coalesced_count }}"/>\n'
    '<cron-tool-call tool="{{ action.tool }}" args="{{ action.args }}">'
    '以下为本次定时工具调用的结果。</cron-tool-call>'
)
"""默认 tool_call 标注块模板：准时触发真执行工具后，多块 EVENT 第一个
content 块的文本（Jinja2 模板源）。

形态：

.. code-block:: text

    <cron-fire jobId="mail-poll" cron="*/10 * * * *" coalescedCount="1"/>
    <cron-tool-call tool="check-email" args="{'folder': 'INBOX'}">以下为本次定时工具调用的结果。</cron-tool-call>

标注块只承载元信息，工具结果本体由紧随其后的塑形块（
:func:`flowing.tool.output_to_blocks`）承载。覆写槽位
``cron_tool_result_template``，优先链：Agent 属性 >
``CronScheduler._templates["tool_result"]`` > 本常量（见
:func:`default_tool_executor`）。

.. seealso:: :data:`DEFAULT_TOOL_NOTICE_TEMPLATE`、
    :func:`default_tool_executor`
"""

async def default_message_executor(
    agent: Agent, job: "CronJob", action: CronAction, ctx: CronFireContext,
    *, template: CronTemplate = DEFAULT_MESSAGE_TEMPLATE,
) -> None:
    """默认 message 执行器：渲染模板并入队一条 EVENT 消息。

    .. rubric:: 功能介绍

    把本次交付渲染成一条消息文本，再以 ``MessageKind.EVENT`` 入队到
    Agent 的持久化消息队列，与用户输入同构进入逻辑 Turn 循环。文本经
    ``agent.parsable(template).resolve({...})`` 渲染——模板经 Parsable
    TEMPLATE 语义渲染（``$./file.j2`` FILE_REF 与 include 基准规则同样
    适用，基准来自 ``agent``），上下文为 ``job`` / ``action`` /
    ``CronFireContext`` 摊平（变量表见 :data:`CronTemplate`）。模板来自
    ``CronScheduler._templates`` 表按 kind 查得——本函数的 ``template``
    参数即调度器构造期闭包的注入点；自定义执行器可委托本函数并自选
    模板。

    .. rubric:: 行为要点

    - 入队消息 ``kind=MessageKind.EVENT``、``source=job.source``、
      ``priority=MessagePriority.STEER``。STEER 优先级是履约保证：逻辑
      Turn 可达天级（goal 模式），普通优先级入队要等回合收尾才被察觉，
      定时触发的意义即丢失；STEER 在回合进行中被当轮吸收、当轮 context
      可见、不打断。
    - 渲染异常 fail-fast 上抛（不静默降级）。

    :param agent: 目标 Agent（任务所属节点）。
    :param job: 触发来源任务。
    :param action: 本次生效的动作（可能是钩子改写后的版本）。
    :param ctx: 本次交付的只读触发上下文。
    :param template: 渲染模板源；缺省 :data:`DEFAULT_MESSAGE_TEMPLATE`。

    .. seealso:: :class:`CronPlugin`、:func:`default_tool_executor`、
        :data:`CronTemplate`
    """
    content = agent.parsable(template).resolve({
        "job": job, "action": action,
        "scheduled_at": ctx.scheduled_at, "fired_at": ctx.fired_at,
        "coalesced_count": ctx.coalesced_count,
        "last_fired_at": ctx.last_fired_at,
    })  # 模板渲染：Parsable TEMPLATE 语义；渲染异常 fail-fast 上抛（不静默降级）
    await agent.enqueue_message(
        Message(
            kind=MessageKind.EVENT,
            source=job.source,
            content=[TextBlock(text=content)],
            priority=MessagePriority.STEER,   # 长 turn 可达天级，定时注入须当轮可见（当轮吸收，不打断）
        )
    )
    # Message / MessageKind / TextBlock -> flowing.message

async def default_tool_executor(
    agent: Agent, job: "CronJob", action: CronAction, ctx: CronFireContext,
    *, template: CronTemplate = DEFAULT_TOOL_NOTICE_TEMPLATE,
    result_template: CronTemplate | None = None,
) -> None:
    """默认 tool_call 执行器：准时触发真执行工具，合并交付降级为通知。

    .. rubric:: 功能介绍

    按 ``ctx.coalesced_count`` 分两条路径：

    - ``== 1`` （无错过的准时触发）：构造
      ``ToolCall(id=<框架生成>, name=action.tool, args=action.args)``，
      ``await agent.tool_call(tc)`` 执行工具，再把结果以多块 EVENT 入队
      ——``Message`` 的 ``kind=EVENT``、``source=job.source``、``content``
      为 ``[标注块, *结果块]``，Agent 在随后的回合中看到工具结果并
      继续推理。``action.tool`` 是 Agent 侧别名（仅按别名查找，见
      :meth:`flowing.agent.Agent.tool_call`）；编程路径的 ``id`` 由框架
      生成，仅作追踪。EVENT 消息不走 TOOL 配对：本次调用非 LLM 发起，
      消息树中没有配对的 ToolCallBlock，TOOL 消息会造成孤立
      tool_result；EVENT 不参与配对是本路径的既定语义。「五形态 →
      content 块列表」的塑形共享模块级
      ``output_to_blocks(result.output, error=result.error)``——与
      ``ToolResult.as_message``、异步完成回调同一实现，cron 不自行实现
      塑形。
    - ``> 1`` （有错过的合并交付）：不执行工具，渲染通知模板
      （``template`` 参数，来自 ``CronScheduler._templates["tool_call"]``，
      默认 :data:`DEFAULT_TOOL_NOTICE_TEMPLATE`）产一条通知 EVENT 入队。
      理由：错过的 N-1 次的副作用语义已不可还原（多次调用可能不幂等），
      如实告知比猜测执行更安全。

    标注块（准时路径多块 EVENT 的第一个 content 块）：文本由模板渲染
    （Jinja2 加 ``$./file.j2`` FILE_REF，``agent.parsable(...).resolve(...)``），
    默认 XML（``<cron-fire …/>`` 加 ``<cron-tool-call …>…</cron-tool-call>``，
    默认常量 :data:`DEFAULT_TOOL_RESULT_TEMPLATE`）。覆写槽位
    ``cron_tool_result_template``，优先链：Agent 属性 >
    ``_templates`` 表 > 默认常量——Agent 自己有该属性（``str`` 或返回
    ``str`` 的 ``property``，普通方法不被调用）则用，否则查
    ``_templates`` 表（键 ``"tool_result"``，经 ``result_template`` 参数
    由调度器构造期闭包注入），再落默认常量。

    .. rubric:: 行为要点

    - 工具调用抛出的异常由执行器捕获，同样以 EVENT 消息形式推回
      （定时链路无人 await，异常不应逃逸进事件循环回调）。
    - 合并降级时不调用工具，也不推进任何工具侧状态——工具调用与否
      完全由 ``coalesced_count`` 决定。

    :param agent: 目标 Agent（任务所属节点）。
    :param job: 触发来源任务。
    :param action: 本次生效的动作（可能是钩子改写后的版本）。
    :param ctx: 本次交付的只读触发上下文。
    :param template: 合并降级的通知模板；缺省
        :data:`DEFAULT_TOOL_NOTICE_TEMPLATE`。
    :param result_template: 标注块模板（覆写链的「``_templates`` 表」
        一档）：调度器构造期闭包以 ``self._templates.get("tool_result")``
        注入；``None`` 且 Agent 无 ``cron_tool_result_template`` 属性时
        落 :data:`DEFAULT_TOOL_RESULT_TEMPLATE`。

    .. seealso:: :func:`default_message_executor`、
        :meth:`flowing.agent.Agent.tool_call`、:data:`CronTemplate`
    """
    if ctx.coalesced_count == 1:
        # 无错过的准时触发：真执行工具并把结果以多块 EVENT 推回消息队列
        try:
            tc = ToolCall(id=f"cron-{uuid4()}",   # 编程路径 id 框架生成（<来源类型名>-<uuid4>，仅追踪）
                          name=action.tool, args=action.args)
            result = await agent.tool_call(tc)   # Agent.tool_call 唯一正式签名（单收 ToolCall）；结果已归一
            # 标注块模板覆写链：Agent 属性 > result_template 参数
            # （_templates["tool_result"]，闭包注入）> 默认常量；
            # Agent 属性为 str 才采用（property 已求值为 str；普通方法
            # 不被调用——绑定方法非 str，自然落入下一档）
            agent_tpl = getattr(agent, "cron_tool_result_template", None)
            tpl = (agent_tpl if isinstance(agent_tpl, str) and agent_tpl
                   else result_template or DEFAULT_TOOL_RESULT_TEMPLATE)
            annotation = agent.parsable(tpl).resolve({
                "job": job, "action": action,
                "scheduled_at": ctx.scheduled_at, "fired_at": ctx.fired_at,
                "coalesced_count": ctx.coalesced_count,
                "last_fired_at": ctx.last_fired_at,
            })  # 渲染机制与上下文变量表同 default_message_executor；渲染异常 fail-fast
            blocks = [TextBlock(text=annotation)]   # 标注块：多块 EVENT 的第一个 content 块
            blocks.extend(output_to_blocks(result.output, error=result.error))  # 塑形统一出口（与 ToolResult.as_message 同一实现）
        except Exception as exc:
            # 工具调用异常同样以 EVENT 消息推回（docstring 边缘情况：
            # 定时链路无人 await，异常不应逃逸进事件循环回调）
            blocks = [TextBlock(text=f"<cron-tool-error>{exc}</cron-tool-error>")]
    else:
        # 合并降级：不真执行工具，渲染通知模板（模板由构造期闭包从
        # _templates["tool_call"] 注入）
        content = agent.parsable(template).resolve({
            "job": job, "action": action,
            "scheduled_at": ctx.scheduled_at, "fired_at": ctx.fired_at,
            "coalesced_count": ctx.coalesced_count,
            "last_fired_at": ctx.last_fired_at,
        })
        blocks = [TextBlock(text=content)]
    await agent.enqueue_message(
        Message(
            kind=MessageKind.EVENT,
            source=job.source,
            content=blocks,
            priority=MessagePriority.STEER,   # 长 turn 可达天级，定时注入须当轮可见（当轮吸收，不打断）
        )
    )
    # Message / MessageKind / TextBlock -> flowing.message
