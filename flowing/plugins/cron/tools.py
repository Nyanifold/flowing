"""``flowing.plugins.cron.tools`` —— 定时扩展的 LLM 工具。

本模块定义四件全局工具：:class:`ScheduleCronTool` （``schedule-cron``，
prompt / tool 二选一的兼容版）、:class:`ScheduleCronMessageTool`
（``schedule-cron-message``，仅消息动作）、
:class:`ScheduleCronToolCallTool` （``schedule-cron-tool-call``，仅工具
动作）与 :class:`ManageCronTool` （``manage-cron``，查询 / 取消）。
由 ``CronPlugin.install()`` 注册进全局工具注册表；任何 Agent 可按需
``add_tool(...)`` 暴露给 LLM。四件工具的目标 Agent 都固定为调用方
（``caller``），LLM 不能给其他 Agent 排任务。

.. seealso:: :mod:`flowing.plugins.cron` （扩展的启用方式与整体契约）、
    :mod:`flowing.plugins.cron.scheduler` （调度器）
"""

from typing import Any, Literal

from flowing.agent import Agent
from flowing.tool import Tool, ToolDefinition

from .models import CronAction
from .scheduler import cron_scheduler_key


class ScheduleCronTool(Tool):
    """全局工具 ``schedule-cron``——为调用方 Agent 自己注册定时任务（兼容版）。

    .. rubric:: 功能介绍

    由 ``CronPlugin.install()`` 注册进全局工具注册表；任何 Agent 可按需
    ``add_tool("schedule-cron")`` 暴露给 LLM。目标 Agent 固定为
    ``caller`` （经 ``execute()`` 的 ``caller`` 参数由框架注入）——LLM
    不能给其他 Agent 排任务。

    本工具是兼容版：``prompt`` 与 ``tool`` / ``tool_args`` 二选一，同时
    支持 message 与 tool_call 两种动作。另有两个受限变体供最小权限场景
    按需暴露：:class:`ScheduleCronMessageTool` （仅消息）、
    :class:`ScheduleCronToolCallTool` （仅工具）。

    .. rubric:: 行为要点

    - 调用成功返回 ``{"job_id": ..., "cron": ..., "next_fire": ...}``
      结构的 dict（``next_fire`` 为 ISO 8601 字符串，naive UTC）。
    - 目标 Agent 未 ``use_cron()`` 也可注册——任务仍触发并经执行器
      兑现，仅无 ``on_cron_trigger`` dispatch；cron 非法 → 返回
      ``status="error"`` 的 ``ToolResult`` （LLM 可见，不触发错误钩子）。

    .. seealso:: :class:`flowing.tool.Tool`、:class:`ManageCronTool`、
        :class:`ScheduleCronMessageTool`、:class:`ScheduleCronToolCallTool`、
        :meth:`CronScheduler.schedule`
    """

    definition: ToolDefinition = ToolDefinition(
        name="schedule-cron",
        description=(
            "为自己注册一条 cron 定时任务（五字段表达式：分 时 日 月 周）。"
            "prompt 与 tool 二选一：传 prompt 到点给自己发一条提示词；"
            "传 tool（可带 tool_args）到点发起一次工具调用。"
        ),
        params_schema={
            "cron": {"type": "string",
                     "description": "五字段 cron 表达式（分 时 日 月 周）"},
            "prompt": {"type": "string",
                       "description": "message 动作的提示词（与 tool 二选一）"},
            "tool": {"type": "string",
                     "description": "tool_call 动作的工具注册名（与 prompt 二选一）"},
            "tool_args": {"type": "object",
                          "description": "tool_call 动作的参数（仅在给出 tool 时有效）"},
            "source": {"type": "string",
                       "description": "语义化来源名（on_cron_trigger 过滤与 EVENT source）"},
            "recurring": {"type": "boolean", "default": True,
                          "description": "False 为一次性任务（首次成功交付后自删）"},
        },
    )
    """类级默认声明：``name="schedule-cron"``；``prompt`` 与
    ``tool`` / ``tool_args`` 二选一（互斥校验在 ``execute`` 内，违约
    包装为 error 结果）；``caller`` 由框架注入，不出现在 LLM 可见声明中。
    """

    async def execute(
        self,
        cron: str,
        prompt: str | None = None,
        tool: str | None = None,
        tool_args: dict[str, Any] | None = None,
        source: str | None = None,
        recurring: bool = True,
        *,
        caller: Agent,
    ) -> dict[str, Any]:
        """以 ``caller.node_id`` 为目标注册任务，返回收据 dict。

        .. rubric:: 行为要点

        - 传 ``tool`` 时构造
          ``CronAction(kind="tool_call", tool=tool, args=tool_args or {})``，
          否则构造 ``CronAction(kind="message", prompt=prompt)``；随后
          等价于 ``caller.inject(cron_scheduler_key).schedule(caller.node_id, cron, action=action, source=source, recurring=recurring)``
          并包装结果。
        - ``tool_call`` 动作注册的是「到点由 cron 执行器调用该工具」，
          目标工具须届时仍在 caller 的工具表中，否则触发时按
          :meth:`flowing.agent.Agent.tool_call` 的未知名称规则处理。
        - ``prompt`` 与 ``tool`` 同时给出或同时缺失、``tool_args`` 在缺
          ``tool`` 时给出 → 抛 ``ValueError``，经 ``Tool.__call__`` 包装
          为 ``status="error"`` 的 ``ToolResult`` （LLM 可见，不触发错误
          钩子）。
        - ``caller`` 由框架在声明了该参数时自动传入，对 LLM 不可见、
          不可被 LLM 参数覆盖。

        :param cron: 五字段 cron 表达式（``分 时 日 月 周``）。
        :param prompt: message 动作的提示词（与 ``tool`` 二选一）。
        :param tool: tool_call 动作的工具注册名（与 ``prompt`` 二选一）。
        :param tool_args: tool_call 动作的参数（仅在给出 ``tool`` 时有效）。
        :param source: 任务来源标识；缺省 ``f"cron:{job_id}"``。
        :param recurring: ``False`` 表示一次性任务（首次成功交付后自删）。
        :param caller: 调用方 Agent（由框架注入，LLM 不可见）。
        :return: 收据 dict，含 ``job_id`` / ``cron`` / ``next_fire``。

        .. seealso:: :meth:`flowing.tool.Tool.execute`、
            :meth:`CronScheduler.schedule`
        """
        # prompt 与 tool 同时给出或同时缺失、tool_args 在缺 tool 时给出
        # → 抛 ValueError，经 Tool.__call__ 包装为 status="error" 的
        # ToolResult（LLM 可见，不触发错误钩子）
        if (prompt is None) == (tool is None):
            raise ValueError("prompt 与 tool 必须二选一（同时给出或同时缺失均非法）")
        if tool is None and tool_args is not None:
            raise ValueError("tool_args 仅在给出 tool 时有效")
        if tool is not None:
            action = CronAction(kind="tool_call", tool=tool,
                                args=tool_args or {})
        else:
            action = CronAction(kind="message", prompt=prompt or "")
        scheduler = caller.inject(cron_scheduler_key)
        job_id = scheduler.schedule(
            caller.node_id, cron, action=action, source=source,
            recurring=recurring,
        )
        # 收据：next_fire = 当前时刻后的下一个理想触发点（ISO 8601）
        return {"job_id": job_id, "cron": cron,
                "next_fire": scheduler.next_fire(job_id)}

class ScheduleCronMessageTool(Tool):
    """全局工具 ``schedule-cron-message``——仅消息动作的受限变体。

    .. rubric:: 功能介绍

    与 :class:`ScheduleCronTool` 同链路，但参数面只接受 ``prompt``——
    应用层向 LLM 开放「定时提醒 / 自提示」而不开放「定时工具调用」时，
    用 ``add_tool("schedule-cron-message")`` 暴露本工具。

    .. rubric:: 行为要点

    - 等价于
      ``caller.inject(cron_scheduler_key).schedule(caller.node_id, cron, action=CronAction(kind="message", prompt=prompt), source=source)``
      并包装收据 dict。
    - 边缘情况同 :class:`ScheduleCronTool` （cron 非法 → 返回
      ``status="error"`` 的 ``ToolResult`` ）。

    .. seealso:: :class:`ScheduleCronTool`、
        :class:`ScheduleCronToolCallTool`
    """

    definition: ToolDefinition = ToolDefinition(
        name="schedule-cron-message",
        description="为自己注册一条 cron 定时消息任务（五字段表达式），到点给自己发一条提示词。",
        params_schema={
            "cron": {"type": "string",
                     "description": "五字段 cron 表达式（分 时 日 月 周）"},
            "prompt": {"type": "string", "description": "到点发送的提示词"},
            "source": {"type": "string",
                       "description": "语义化来源名（on_cron_trigger 过滤与 EVENT source）"},
            "recurring": {"type": "boolean", "default": True,
                          "description": "False 为一次性任务（首次成功交付后自删）"},
        },
    )
    """类级默认声明：``name="schedule-cron-message"``；参数面只接受
    ``prompt`` （仅消息动作的受限变体）。
    """

    async def execute(
        self,
        cron: str,
        prompt: str,
        source: str | None = None,
        recurring: bool = True,
        *,
        caller: Agent,
    ) -> dict[str, Any]:
        """注册 message 动作任务，返回收据 dict。

        :param cron: 五字段 cron 表达式（``分 时 日 月 周``）。
        :param prompt: 到点发送的提示词。
        :param source: 任务来源标识；缺省 ``f"cron:{job_id}"``。
        :param recurring: ``False`` 表示一次性任务（首次成功交付后自删）。
        :param caller: 调用方 Agent（由框架注入，LLM 不可见）。
        :return: 收据 dict，含 ``job_id`` / ``cron`` / ``next_fire``。

        .. seealso:: :meth:`ScheduleCronTool.execute`
        """
        action = CronAction(kind="message", prompt=prompt)
        scheduler = caller.inject(cron_scheduler_key)
        job_id = scheduler.schedule(
            caller.node_id, cron, action=action, source=source,
            recurring=recurring,
        )
        # -> dict 收据 {"job_id": ..., "cron": ..., "next_fire": ...}
        # （next_fire 经 scheduler.next_fire(job_id)，口径同
        # ScheduleCronTool.execute）
        return {"job_id": job_id, "cron": cron,
                "next_fire": scheduler.next_fire(job_id)}

class ScheduleCronToolCallTool(Tool):
    """全局工具 ``schedule-cron-tool-call``——仅工具动作的受限变体。

    .. rubric:: 功能介绍

    与 :class:`ScheduleCronTool` 同链路，但参数面只接受
    ``tool`` / ``tool_args``——应用层向 LLM 开放「定时发起工具调用」
    （如每 10 分钟查一次收件箱）而不开放自由提示词注入时，用
    ``add_tool("schedule-cron-tool-call")`` 暴露本工具。

    .. rubric:: 行为要点

    - 等价于
      ``caller.inject(cron_scheduler_key).schedule(caller.node_id, cron, action=CronAction(kind="tool_call", tool=tool, args=tool_args or {}), source=source)``
      并包装收据 dict。
    - 边缘情况同 :class:`ScheduleCronTool`；目标工具名在注册时不校验
      存在性（工具表可在任务存活期内变化），触发时按
      :meth:`flowing.agent.Agent.tool_call` 的未知名称规则处理。

    .. seealso:: :class:`ScheduleCronTool`、
        :class:`ScheduleCronMessageTool`、:func:`default_tool_executor`
    """

    definition: ToolDefinition = ToolDefinition(
        name="schedule-cron-tool-call",
        description=(
            "为自己注册一条 cron 定时工具调用任务（五字段表达式），"
            "到点以给定参数调用指定工具。"
        ),
        params_schema={
            "cron": {"type": "string",
                     "description": "五字段 cron 表达式（分 时 日 月 周）"},
            "tool": {"type": "string", "description": "工具注册名"},
            "tool_args": {"type": "object", "description": "工具参数"},
            "source": {"type": "string",
                       "description": "语义化来源名（on_cron_trigger 过滤与 EVENT source）"},
            "recurring": {"type": "boolean", "default": True,
                          "description": "False 为一次性任务（首次成功交付后自删）"},
        },
    )
    """类级默认声明：``name="schedule-cron-tool-call"``；参数面只接受
    ``tool`` / ``tool_args`` （仅工具动作的受限变体）。
    """

    async def execute(
        self,
        cron: str,
        tool: str,
        tool_args: dict[str, Any] | None = None,
        source: str | None = None,
        recurring: bool = True,
        *,
        caller: Agent,
    ) -> dict[str, Any]:
        """注册 tool_call 动作任务，返回收据 dict。

        :param cron: 五字段 cron 表达式（``分 时 日 月 周``）。
        :param tool: 工具注册名。
        :param tool_args: 工具参数。
        :param source: 任务来源标识；缺省 ``f"cron:{job_id}"``。
        :param recurring: ``False`` 表示一次性任务（首次成功交付后自删）。
        :param caller: 调用方 Agent（由框架注入，LLM 不可见）。
        :return: 收据 dict，含 ``job_id`` / ``cron`` / ``next_fire``。

        .. seealso:: :meth:`ScheduleCronTool.execute`
        """
        action = CronAction(kind="tool_call", tool=tool, args=tool_args or {})
        scheduler = caller.inject(cron_scheduler_key)
        job_id = scheduler.schedule(
            caller.node_id, cron, action=action, source=source,
            recurring=recurring,
        )
        # -> dict 收据 {"job_id": ..., "cron": ..., "next_fire": ...}
        # （next_fire 经 scheduler.next_fire(job_id)，口径同
        # ScheduleCronTool.execute）
        return {"job_id": job_id, "cron": cron,
                "next_fire": scheduler.next_fire(job_id)}

class ManageCronTool(Tool):
    """全局工具 ``manage-cron``——LLM 查询 / 取消调用方 Agent 自己的定时任务。

    .. rubric:: 功能介绍

    与 ``schedule-cron`` 配对的管理面：``action="list"`` 列出 caller 的
    任务，``action="cancel"`` 按 ``job_id`` 取消。目标同样锁定 caller。

    .. rubric:: 行为要点

    - ``list`` 返回 ``{"jobs": [{...CronJob 字段...}]}`` （dict 形态，
      与落盘结构一致）；
      ``cancel`` 返回 ``{"cancelled": bool}`` （不存在返回 ``False``，
      与 ``unschedule`` 语义一致）。
    - ``action="cancel"`` 缺 ``job_id`` → 抛 ``ValueError``，经
      ``Tool.__call__`` 包装为 ``status="error"`` 的 ``ToolResult``。

    .. seealso:: :class:`ScheduleCronTool`、:meth:`CronScheduler.jobs`、
        :meth:`CronScheduler.unschedule`
    """

    definition: ToolDefinition = ToolDefinition(
        name="manage-cron",
        description="查询或取消自己的 cron 定时任务（action=\"list\" 列出，action=\"cancel\" 按 job_id 取消）。",
        params_schema={
            "action": {"type": "string", "enum": ["list", "cancel"],
                       "description": "管理动作：list 列出任务，cancel 取消任务"},
            "job_id": {"type": "string",
                       "description": "要取消的任务 ID（action=\"cancel\" 时必填）"},
        },
    )
    """类级默认声明：``name="manage-cron"``；``action`` 枚举
    ``list | cancel``，``job_id`` 在 ``cancel`` 时必填（缺失由
    ``execute`` 抛 ValueError → error 结果）。
    """

    async def execute(
        self,
        action: Literal["list", "cancel"],
        job_id: str | None = None,
        *,
        caller: Agent,
    ) -> dict[str, Any]:
        """执行管理动作，返回结果 dict。

        :param action: 管理动作（``"list"`` 列出 / ``"cancel"`` 取消）。
        :param job_id: 要取消的任务 ID（``action="cancel"`` 时必填）。
        :param caller: 调用方 Agent（由框架注入，LLM 不可见）。
        :return: ``{"jobs": [...]}`` （list）或 ``{"cancelled": bool}``
            （cancel）。
        :raises ValueError: ``action="cancel"`` 且未提供 ``job_id`` 时。

        .. seealso:: :meth:`flowing.tool.Tool.execute`
        """
        scheduler = caller.inject(cron_scheduler_key)
        if action == "list":
            return {"jobs": [job.to_dict() for job in scheduler.jobs(caller.node_id)]}  # 唯一序列化通道
        # action == "cancel"：缺 job_id → 抛 ValueError，经 Tool.__call__
        # 包装为 status="error" 的 ToolResult（LLM 可见）
        if not job_id:
            raise ValueError("action='cancel' 需提供 job_id")
        return {"cancelled": scheduler.unschedule(job_id)}
