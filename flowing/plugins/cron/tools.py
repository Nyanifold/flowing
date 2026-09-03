"""``flowing.plugins.cron.tools`` —— 定时扩展的 LLM 工具。

本模块定义两件全局工具：:class:`ScheduleCronTool`（``schedule-cron``，
为调用方注册一条定时消息任务）与 :class:`ManageCronTool`
（``manage-cron``，查询 / 取消）。由 ``CronPlugin.install()`` 注册进
全局工具注册表；任何 Agent 可按需 ``add_tool(...)`` 暴露给 LLM。两件
工具的目标都固定为调用方（``caller``），LLM 不能为其他 Agent 操作任务。

.. seealso:: :mod:`flowing.plugins.cron`（扩展整体契约）、
    :mod:`flowing.plugins.cron.jobs`（模块 API 与校验语义）
"""

from typing import Any, Literal

from flowing.agent import Agent
from flowing.tool import Tool, ToolDefinition

from .jobs import _next_ideal_fire, _now, jobs, schedule, unschedule


class ScheduleCronTool(Tool):
    """全局工具 ``schedule-cron``——为调用方 Agent 注册一条定时消息任务。

    .. rubric:: 功能介绍

    由 ``CronPlugin.install()`` 注册；任何 Agent 可按需
    ``add_tool("schedule-cron")`` 暴露给 LLM。目标锁定 ``caller``
    （经 ``execute()`` 的 ``caller`` 参数由框架注入）。等价于
    ``schedule(caller, cron, content, job_id=..., source=..., recurring=...)``
    并包装收据。校验失败（content 空 / cron 非法 / 占位符格式非法 /
    job_id 冲突）由 ``Tool.__call__`` 包装为 ``status="error"`` 的
    ``ToolResult``（LLM 可见，不触发错误钩子）。

    .. rubric:: 行为要点

    - 调用成功返回收据 dict：``{"job_id","cron","source","recurring",
      "next_fire"}``（``next_fire`` 为下一理想点 ISO 8601，本地 naive）。
    - ``source`` 为可选语义标签（``on_cron_trigger`` 钩子 pattern 过滤
      与 EVENT source）；``job_id`` 可选（显式语义化 id 便于钩子按
      精确 id 过滤与 manage-cron 取消）。

    .. seealso:: :class:`flowing.tool.Tool`、:class:`ManageCronTool`、
        :func:`flowing.plugins.cron.schedule`
    """

    definition: ToolDefinition = ToolDefinition(
        name="schedule-cron",
        description=(
            "Register a recurring cron message task for yourself (five-field expression: "
            "minute hour day month weekday); when due, push the content text to yourself."
        ),
        params_schema={
            "cron": {"type": "string",
                     "description": "five-field cron expression (minute hour day month weekday)"},
            "content": {"type": "string",
                        "description": "message text to push when due; may reference the current time "
                                       "with {{current_time}} or {{current_time:format}}"},
            "source": {"type": "string",
                       "description": "optional semantic tag (on_cron_trigger filters by source)"},
            "recurring": {"type": "boolean", "default": True,
                          "description": "False makes it a one-shot task (self-removes after one "
                                         "successful delivery)"},
            "job_id": {"type": "string",
                       "description": "optional task id (auto-generated when omitted)"},
        },
    )

    async def execute(
        self,
        cron: str,
        content: str,
        source: str | None = None,
        recurring: bool = True,
        job_id: str | None = None,
        *,
        caller: Agent,
    ) -> dict[str, Any]:
        """以 ``caller`` 为目标注册任务，返回收据 dict。

        :param cron: 五字段 cron 表达式。
        :param content: 到点推送的消息文本。
        :param source: 语义标签；缺省 ``""``。
        :param recurring: ``False`` = 一次性任务。
        :param job_id: 显式任务 id（可选）。
        :param caller: 调用方 Agent（框架注入，LLM 不可见）。
        :return: ``{"job_id","cron","source","recurring","next_fire"}``。
        :raises ValueError: 校验失败（经 ``Tool.__call__`` 包装为 error
            结果）。
        """
        final_id = schedule(caller, cron, content,
                            job_id=job_id, source=source or "",
                            recurring=recurring)
        return {
            "job_id": final_id,
            "cron": cron,
            "source": source or "",
            "recurring": bool(recurring),
            "next_fire": _next_ideal_fire(cron, _now()).isoformat(),
        }


class ManageCronTool(Tool):
    """全局工具 ``manage-cron``——查询 / 取消调用方自己的定时任务。

    .. rubric:: 功能介绍

    ``action="list"`` 列出 caller 的任务，``action="cancel"`` 按
    ``job_id`` 取消。目标锁定 caller。校验失败（``cancel`` 缺
    ``job_id``）经 ``Tool.__call__`` 包装为 ``status="error"`` 结果。

    .. rubric:: 行为要点

    - ``list`` 返回 ``{"jobs": [CronJob dict…]}``（仅 caller 的任务）；
    - ``cancel`` 返回 ``{"cancelled": bool}``（不存在返回 ``False``，
      与 :func:`flowing.plugins.cron.unschedule` 语义一致）。

    .. seealso:: :class:`ScheduleCronTool`、:func:`flowing.plugins.cron.jobs`
    """

    definition: ToolDefinition = ToolDefinition(
        name="manage-cron",
        description=(
            "Query or cancel your own cron tasks (action=\"list\" lists them; "
            "action=\"cancel\" cancels one by job_id)."
        ),
        params_schema={
            "action": {"type": "string", "enum": ["list", "cancel"],
                       "description": "management action: list lists tasks, cancel cancels a task"},
            "job_id": {"type": "string",
                       "description": "job id of the task to cancel (required when action=\"cancel\")"},
        },
    )

    async def execute(
        self,
        action: Literal["list", "cancel"],
        job_id: str | None = None,
        *,
        caller: Agent,
    ) -> dict[str, Any]:
        """执行管理动作，返回结果 dict。

        :param action: ``"list"`` 列出 / ``"cancel"`` 取消。
        :param job_id: 要取消的任务 id（``cancel`` 时必填）。
        :param caller: 调用方 Agent（框架注入，LLM 不可见）。
        :return: ``{"jobs": [...]}``（list）或 ``{"cancelled": bool}``
            （cancel）。
        :raises ValueError: ``cancel`` 未提供 ``job_id`` 时。
        """
        if action == "list":
            return {"jobs": [job.to_dict() for job in jobs(caller)]}
        if not job_id:
            raise ValueError("action='cancel' requires job_id")
        return {"cancelled": unschedule(caller, job_id)}
