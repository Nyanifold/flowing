"""执行器类型与默认执行器：``CronExecutor`` / ``CronTemplate`` /
``DEFAULT_MESSAGE_TEMPLATE`` / ``DEFAULT_TOOL_NOTICE_TEMPLATE`` /
``default_message_executor`` / ``default_tool_executor``。
"""

from collections.abc import Awaitable, Callable
from uuid import uuid4

from flowing.agent import Agent
from flowing.message import Message, MessageKind, MessagePriority, TextBlock
from flowing.tool import ToolCall, output_to_blocks

from .models import CronAction, CronFireContext


CronExecutor = Callable[[Agent, "CronJob", "CronAction", "CronFireContext"], Awaitable[None]]
"""执行器类型：``async (agent, job, action, ctx) -> None``。``action`` 为
本次生效的动作（可能是 ``on_cron_trigger`` handler 改写后的版本），
``job.action`` 始终是落盘的原始定义——执行器应消费前者。``ctx``
为只读触发上下文（合并计数、理想/实际时刻）。

.. seealso:: :class:`CronPlugin`（``executors`` 参数）、
:class:`CronFireContext`、:func:`default_message_executor`
"""

CronTemplate = str
"""渲染模板类型：Jinja2 模板源字符串（含 ``$./file.j2`` FILE_REF 形式）。

渲染器模板化裁决：**渲染器槽位只收模板字符串，不再支持 callable**。
渲染经 Parsable TEMPLATE 语义（第二步 Jinja2；include 走框架自定义
加载器、基准与 ``$`` 引用同源，P3-08），上下文变量表（按 kind 分键
注册，``CronPlugin(templates={...})``）：

- ``job``（`CronJob`）、``action``（`CronAction`，本次生效版本）
- ``scheduled_at`` / ``fired_at`` / ``coalesced_count`` /
  ``last_fired_at``（`CronFireContext` 摊平）

模板是默认执行器路径的组件，不是强制管线——自定义执行器可以完全
绕开它。渲染异常（语法错等）fail-fast 上抛，不静默降级（配置错误
应尽早暴露）。

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

形态（与「定时触发 = 带结构化元信息的 user 消息」的共识一致）：

.. code-block:: text

    <cron-fire jobId="daily-consolidation" cron="0 3 * * *"
               recurring="true" coalescedCount="3">
    请执行每日沉淀
    </cron-fire>

``coalescedCount`` 来自触发上下文的 ``coalesced_count``——Agent 据此知道
这是准时触发（1）还是合并交付（>1）。

**模板即契约**（渲染器模板化裁决）：渲染器槽位只收 Jinja2 模板字符串
（含 ``$./file.j2`` FILE_REF 形式），不再支持 callable。渲染经
Parsable TEMPLATE 语义（含 include 加载器同源基准，P3-08），上下文
变量表见 :class:`CronScheduler` 的 ``templates`` 参数。

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

默认 tool 执行器在 ``coalesced_count > 1`` 时**不真执行**工具，
改用本模板渲染一条通知（XML 内说明「错过了 N 次对
``action.tool`` 的定时调用」及其参数摘要），Agent 醒来自行决定
是否补做。模板上下文变量表与 :data:`DEFAULT_MESSAGE_TEMPLATE`
相同（见 :class:`CronScheduler` 的 ``templates`` 参数）。

.. seealso:: :data:`DEFAULT_MESSAGE_TEMPLATE`、
   :func:`default_tool_executor`
"""

async def default_message_executor(
    agent: Agent, job: "CronJob", action: CronAction, ctx: CronFireContext,
    *, template: CronTemplate = DEFAULT_MESSAGE_TEMPLATE,
) -> None:
    """默认 message 执行器：渲染模板并入队一条 EVENT 消息。

    ``content`` = ``agent.parsable(template).resolve({...})``——模板经
    Parsable TEMPLATE 语义渲染（``$./file.j2`` FILE_REF 与 include
    基准规则同样适用，基准来自 ``agent``），上下文为 ``job`` /
    ``action`` / ``CronFireContext`` 摊平（变量表见
    :data:`CronTemplate`）；模板来自 ``CronScheduler._templates``
    表按 kind 查得（P3-12② 裁决：默认执行器是调度器构造期闭包，
    查表通道为闭包捕获 ``self._templates``——本函数的 ``template``
    参数即闭包注入点；自定义执行器可委托本函数并自选模板）。随后
    ``enqueue_message(Message(kind=MessageKind.EVENT,
    source=job.source, priority=STEER, ...))``——**STEER 优先级是
    履约保证**：逻辑 Turn 可达天级（goal 模式），NORMAL 入队要等回合
    收尾才被察觉，定时触发的意义即丢失；STEER 在检查点 ②.5 被吸收、
    当轮 context 可见、不打断。走持久化队列，与用户输入同构进入逻辑
    Turn 循环。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.agent.Agent.parsable`` +
      ``flowing.parsable.Parsable.resolve``（时机：每次交付，渲染
      模板文本）；``flowing.agent.Agent.enqueue_message``
      （时机：每次交付，渲染后入队 EVENT 消息）
    - 被调：``flowing.plugins.cron.CronScheduler._fire`` 第 6 步经
      ``_executors`` 表（时机：message 动作到点触发）；
      ``CronScheduler._sweep``（时机：恢复回顾）；可被自定义执行器
      委托（docstring 明示）

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
            priority=MessagePriority.STEER,   # 长 turn 可达天级，定时注入须当轮可见（②.5 吸收，不打断）
        )
    )
    # Message / MessageKind / TextBlock -> flowing.message

async def default_tool_executor(
    agent: Agent, job: "CronJob", action: CronAction, ctx: CronFireContext,
    *, template: CronTemplate = DEFAULT_TOOL_NOTICE_TEMPLATE,
) -> None:
    """默认 tool_call 执行器：准时执行，合并降级为通知。

    - ``ctx.coalesced_count == 1``（无错过的准时触发）：构造
      ``ToolCall(id=<框架生成>, name=action.tool, args=action.args)``
      （C-17 裁决——``Agent.tool_call`` 唯一正式签名单收 ``ToolCall``；
      ``action.tool`` 是 Agent 侧**别名**；编程路径的 ``id`` 由框架生成，
      仅作追踪），``result = await agent.tool_call(tc)``（出
      ``tool_call`` 的 ``result.output`` 已归一为五形态之一，D19），
      随后将结果以**多块 EVENT** 入队——``Message(kind=MessageKind.EVENT,
      source=job.source, content=[标注块, *结果块])``，Agent 在随后的
      回合中看到工具结果并继续推理。EVENT 消息**不走 TOOL 配对**：
      本次调用非 LLM 发起，消息树中没有配对的 ToolCallBlock，TOOL
      消息会造成孤立 tool_result（``Message.__post_init__`` 双向强制
      后更是构造即错）；EVENT 不参与配对是本路径的既定语义（D14）。
      「五形态 → content 块列表」的塑形共享模块级
      ``output_to_blocks(result.output, error=result.error)``——与
      ``ToolResult.as_message``、异步完成回调同一实现（D22），cron
      不自行实现塑形。
    - ``ctx.coalesced_count > 1``（有错过的合并交付）：**不执行**，
      渲染通知模板（``template`` 参数，来自
      ``CronScheduler._templates["tool_call"]``，默认
      :data:`DEFAULT_TOOL_NOTICE_TEMPLATE`；渲染机制与上下文变量表同
      :func:`default_message_executor`）产一条通知 EVENT 入队。
      理由：错过的 N-1 次的副作用语义已不可还原（多次调用可能不
      幂等），如实告知比猜测执行更安全。

    标注块（D15）：多块 EVENT 的第一个 content 块，一个 TextBlock，
    文本由模板渲染（Jinja2 + ``$./file.j2`` FILE_REF，
    ``agent.parsable(...).resolve(...)``），默认 XML
    （``<cron-fire …/>`` + ``<cron-tool-call …>…</cron-tool-call>``）。
    覆写槽位 ``cron_tool_result_template``，优先链：**Agent 属性 >
    cron ``_templates`` 表 > 默认常量**——Agent 自己有该属性
    （``str | None`` 或返回 ``str`` 的 ``property``，普通方法不被
    调用）则用，否则查 ``_templates`` 表，再落默认常量（沿用
    ``subagent_catalog_template`` 先例）。

    - 边缘情况：工具调用抛出的异常由执行器捕获并同样以 EVENT 消息
      形式推回（定时链路无人 await，异常不应逃逸进事件循环回调）。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.agent.Agent.tool_call``（时机：``coalesced_count
      == 1`` 准时触发；C-17 裁决后内部构造 ``ToolCall`` 再调，别名语义
      不变）；``flowing.tool.output_to_blocks``（时机：准时触发，
      结果塑形——D22 塑形统一出口）；``Agent.parsable`` +
      ``Parsable.resolve``
      （时机：准时触发渲染标注模板、合并降级渲染通知模板）；EVENT 消息入队
      （时机：每次交付或降级通知）
    - 被调：``flowing.plugins.cron.CronScheduler._fire`` 第 6 步经
      ``_executors`` 表（时机：tool_call 动作到点触发）；
      ``CronScheduler._sweep``（时机：恢复回顾）

    .. seealso:: :func:`default_message_executor`、
       :meth:`flowing.agent.Agent.tool_call`、:data:`CronTemplate`
    """
    if ctx.coalesced_count == 1:
        # 无错过的准时触发：真执行工具并把结果以多块 EVENT 推回消息队列（D14）
        try:
            tc = ToolCall(id=f"cron-{uuid4()}",   # 编程路径 id 框架生成（S-41 裁决②：<来源类型名>-<uuid4> 全量，仅追踪）
                          name=action.tool, args=action.args)
            result = await agent.tool_call(tc)   # Agent.tool_call 唯一正式签名（单收 ToolCall）；出 tool_call 已归一（D19）
            blocks = [TextBlock(text=渲染标注模板)]   # 标注块：默认 XML，覆写槽位 cron_tool_result_template（D15，见 docstring）
            blocks.extend(output_to_blocks(result.output, error=result.error))  # 塑形统一出口（D22，§5.4 同一实现）
        except Exception as exc:
            # 工具调用异常同样以 EVENT 消息推回（docstring 边缘情况：
            # 定时链路无人 await，异常不应逃逸进事件循环回调）
            blocks = [TextBlock(text=f"<cron-tool-error>{exc}</cron-tool-error>")]
    else:
        # 合并降级：不真执行工具，渲染通知模板（模板由构造期闭包从
        # _templates["tool_call"] 注入，P3-12②）
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
            priority=MessagePriority.STEER,   # 长 turn 可达天级，定时注入须当轮可见（②.5 吸收，不打断）
        )
    )
    # Message / MessageKind / TextBlock -> flowing.message
