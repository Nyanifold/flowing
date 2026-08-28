"""LLM 工具：``ScheduleCronTool`` / ``ScheduleCronMessageTool`` /
``ScheduleCronToolCallTool`` / ``ManageCronTool``。
"""

from typing import Any, Literal

from flowing.agent import Agent
from flowing.tool import Tool

from .models import CronAction
from .scheduler import cron_scheduler_key


class ScheduleCronTool(Tool):
    """全局工具 ``schedule-cron``——LLM 可为**调用方 Agent 自己**注册定时任务（兼容版）。

    .. rubric:: 功能介绍

    由 ``CronPlugin.install()`` 注册进全局工具注册表；任何 Agent 可按需
    ``add_tool("schedule-cron")`` 暴露给 LLM。目标 Agent 固定为
    ``caller``（经 ``execute()`` 的 ``caller`` 参数由框架注入）——LLM
    不能给其他 Agent 排任务。

    本工具是**兼容版**：``prompt`` 与 ``tool``/``tool_args`` 二选一，
    同时支持 message 与 tool_call 两种动作。另有两个受限变体供
    最小权限场景按需暴露：:class:`ScheduleCronMessageTool`（仅消息）、
    :class:`ScheduleCronToolCallTool`（仅工具）。

    .. rubric:: 设计动机

    「让 Agent 给自己设闹钟」是定时扩展的核心交互形态（如「五分钟后提醒
    我继续」）；目标锁定 caller 是最小权限的默认形态，跨 Agent 调度走
    代码路径（``inject(cron_scheduler_key).schedule(...)``）。拆出三个
    工具而非只靠本兼容版，是因为「允许模型定时给自己发提示词」与
    「允许模型定时发起真实工具调用」是两级不同的授权——应用层应能
    分别开闸。

    .. rubric:: 行为规约

    - 期待行为：调用成功返回 ``{"job_id": ..., "cron": ..., "next_fire":
      ...}`` 结构的 dict——基础值原样归一为 ``ToolResult.output``
      （D4），塑形时装 ``StructBlock`` 进 TOOL 消息（D18/D22：对程序
      是结构 ``block.data``，对 LLM 恒投影为 ``json.dumps`` 文本）。
    - 边缘情况：目标 Agent 未 ``use_cron()`` 也可注册——任务仍触发
      并经执行器兑现，仅无 ``on_cron_trigger`` dispatch；cron 非法 →
      ``status="error"`` 的 ``ToolResult``（LLM 可见，不触发错误钩子）。

    .. rubric:: 调用关系（审计）

    - 实例化方：``flowing.plugins.cron.CronPlugin.install`` 第 2 步
      （时机：阶段一安装，每次安装恰好一次）

    .. seealso:: :class:`flowing.tool.Tool`、:class:`ManageCronTool`、
       :class:`ScheduleCronMessageTool`、:class:`ScheduleCronToolCallTool`、
       :meth:`CronScheduler.schedule`
    """

    name: str
    """注册名 ``"schedule-cron"``。
    """
    description: str
    """LLM 可见描述。
    """
    parameters: dict[str, Any]
    """参数 schema：``cron``（必填，五字段表达式）；``prompt`` 与
    ``tool``/``tool_args`` 二选一——传 ``prompt`` 为 ``kind="message"``
    动作，传 ``tool``（可选带 ``tool_args`` dict）为
    ``kind="tool_call"`` 动作；``source``（可选，语义化来源名）；
    ``recurring``（可选 bool，默认 ``True``；``False`` = 一次性任务）。
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

        .. rubric:: 行为规约

        - 期待行为：传 ``tool`` 时构造
          ``CronAction(kind="tool_call", tool=tool, args=tool_args or {})``，
          否则构造 ``CronAction(kind="message", prompt=prompt)``；随后
          等价于 ``caller.inject(cron_scheduler_key).schedule(
          caller.node_id, cron, action=action, source=source,
          recurring=recurring)`` 并包装结果。注：``tool_call`` 动作注册
          的是「到点由 cron 执行器调用该工具」，目标工具须届时仍在
          caller 的工具表中，否则触发时按
          :meth:`flowing.agent.Agent.tool_call` 的未知名称规则处理。
        - 边缘情况：``prompt`` 与 ``tool`` 同时给出或同时缺失 →
          ``status="error"`` 的 ``ToolResult``（LLM 可见，不触发错误
          钩子）；``tool_args`` 在缺 ``tool`` 时给出 → 同上。
        - ``caller`` 由框架在声明了该参数时自动传入，对 LLM 不可见、
          不可被 LLM 参数覆盖（specified 级防篡改通道）。

        .. rubric:: 调用关系（审计）

        - 调用：构造 ``CronAction``（时机：每次执行）；
          ``flowing.agent.Agent.inject`` + ``CronScheduler.schedule``
          （时机：每次执行）
        - 被调：``flowing.tool.Tool.__call__`` 调度层（时机：LLM 每次
          调用 ``schedule-cron``，``caller`` 由框架按签名注入）

        .. seealso:: :meth:`flowing.tool.Tool.execute`、
           :meth:`CronScheduler.schedule`
        """
        # prompt 与 tool 同时给出或同时缺失、tool_args 在缺 tool 时给出
        # → status="error" 的 ToolResult（校验与错误包装细节未见具名规约）
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
        # 收据（P3-12④）：next_fire = 当前时刻后的下一个理想触发点（ISO 8601）
        return {"job_id": job_id, "cron": cron,
                "next_fire": scheduler.next_fire(job_id)}

class ScheduleCronMessageTool(Tool):
    """全局工具 ``schedule-cron-message``——仅消息动作的受限变体。

    .. rubric:: 功能介绍

    与 :class:`ScheduleCronTool` 同链路，但参数面只接受 ``prompt``——
    应用层向 LLM 开放「定时提醒/自提示」而不开放「定时工具调用」时，
    用 ``add_tool("schedule-cron-message")`` 暴露本工具。

    .. rubric:: 行为规约

    - 期待行为：等价于
      ``caller.inject(cron_scheduler_key).schedule(caller.node_id, cron,
      action=CronAction(kind="message", prompt=prompt), source=source)``
      并包装收据 dict。
    - 边缘情况：同 :class:`ScheduleCronTool`（cron 非法 →
      ``status="error"`` 的 ``ToolResult``）。

    .. rubric:: 调用关系（审计）

    - 实例化方：``flowing.plugins.cron.CronPlugin.install`` 第 2 步
      （时机：阶段一安装，每次安装恰好一次）

    .. seealso:: :class:`ScheduleCronTool`、:class:`ScheduleCronToolCallTool`
    """

    name: str
    """注册名 ``"schedule-cron-message"``。
    """
    description: str
    """LLM 可见描述。
    """
    parameters: dict[str, Any]
    """参数 schema：``cron``（必填）、``prompt``（必填）、``source``
    （可选）、``recurring``（可选 bool，默认 ``True``）。
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

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent.inject`` +
          ``CronScheduler.schedule``（时机：每次执行）；构造
          ``CronAction(kind="message")``（时机：每次执行）
        - 被调：``flowing.tool.Tool.__call__`` 调度层（时机：LLM 每次
          调用 ``schedule-cron-message``，``caller`` 由框架注入）

        .. seealso:: :meth:`ScheduleCronTool.execute`
        """
        action = CronAction(kind="message", prompt=prompt)
        scheduler = caller.inject(cron_scheduler_key)
        job_id = scheduler.schedule(
            caller.node_id, cron, action=action, source=source,
            recurring=recurring,
        )
        # -> dict 收据 {"job_id": ..., "cron": ..., "next_fire": ...}
        # （P3-12④：next_fire 经 scheduler.next_fire(job_id)，口径同
        # ScheduleCronTool.execute）
        return {"job_id": job_id, "cron": cron,
                "next_fire": scheduler.next_fire(job_id)}

class ScheduleCronToolCallTool(Tool):
    """全局工具 ``schedule-cron-tool-call``——仅工具动作的受限变体。

    .. rubric:: 功能介绍

    与 :class:`ScheduleCronTool` 同链路，但参数面只接受
    ``tool``/``tool_args``——应用层向 LLM 开放「定时发起工具调用」
    （如每 10 分钟查一次收件箱）而不开放自由提示词注入时，用
    ``add_tool("schedule-cron-tool-call")`` 暴露本工具。

    .. rubric:: 行为规约

    - 期待行为：等价于
      ``caller.inject(cron_scheduler_key).schedule(caller.node_id, cron,
      action=CronAction(kind="tool_call", tool=tool,
      args=tool_args or {}), source=source)`` 并包装收据 dict。
    - 边缘情况：同 :class:`ScheduleCronTool`；目标工具名在**注册时**
      不校验存在性（工具表可在任务存活期内变化），触发时按
      :meth:`flowing.agent.Agent.tool_call` 的未知名称规则处理。

    .. rubric:: 调用关系（审计）

    - 实例化方：``flowing.plugins.cron.CronPlugin.install`` 第 2 步
      （时机：阶段一安装，每次安装恰好一次）

    .. seealso:: :class:`ScheduleCronTool`、
       :class:`ScheduleCronMessageTool`、:func:`default_tool_executor`
    """

    name: str
    """注册名 ``"schedule-cron-tool-call"``。
    """
    description: str
    """LLM 可见描述。
    """
    parameters: dict[str, Any]
    """参数 schema：``cron``（必填）、``tool``（必填，工具注册名）、
    ``tool_args``（可选 dict）、``source``（可选）、``recurring``
    （可选 bool，默认 ``True``）。
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

        .. rubric:: 调用关系（审计）

        - 调用：``flowing.agent.Agent.inject`` +
          ``CronScheduler.schedule``（时机：每次执行）；构造
          ``CronAction(kind="tool_call")``（时机：每次执行）
        - 被调：``flowing.tool.Tool.__call__`` 调度层（时机：LLM 每次
          调用 ``schedule-cron-tool-call``，``caller`` 由框架注入）

        .. seealso:: :meth:`ScheduleCronTool.execute`
        """
        action = CronAction(kind="tool_call", tool=tool, args=tool_args or {})
        scheduler = caller.inject(cron_scheduler_key)
        job_id = scheduler.schedule(
            caller.node_id, cron, action=action, source=source,
            recurring=recurring,
        )
        # -> dict 收据 {"job_id": ..., "cron": ..., "next_fire": ...}
        # （P3-12④：next_fire 经 scheduler.next_fire(job_id)，口径同
        # ScheduleCronTool.execute）
        return {"job_id": job_id, "cron": cron,
                "next_fire": scheduler.next_fire(job_id)}

class ManageCronTool(Tool):
    """全局工具 ``manage-cron``——LLM 查询/取消**调用方 Agent 自己**的定时任务。

    .. rubric:: 功能介绍

    与 ``schedule-cron`` 配对的管理面：``action="list"`` 列出 caller 的
    任务，``action="cancel"`` 按 ``job_id`` 取消。目标同样锁定 caller。

    .. rubric:: 行为规约

    - 期待行为：``list`` 返回 ``{"jobs": [{...CronJob 字段...}]}``；
      ``cancel`` 返回 ``{"cancelled": bool}``（不存在返回 ``False``，
      与 ``unschedule`` 语义一致）。
    - 边缘情况：``action="cancel"`` 缺 ``job_id`` → ``status="error"``
      的 ``ToolResult``。

    .. rubric:: 调用关系（审计）

    - 实例化方：``flowing.plugins.cron.CronPlugin.install`` 第 2 步
      （时机：阶段一安装，每次安装恰好一次）

    .. seealso:: :class:`ScheduleCronTool`、:meth:`CronScheduler.jobs`、
       :meth:`CronScheduler.unschedule`
    """

    name: str
    """注册名 ``"manage-cron"``。
    """
    description: str
    """LLM 可见描述。
    """
    parameters: dict[str, Any]
    """参数 schema：``action``（必填，``"list" | "cancel"``）、
    ``job_id``（``cancel`` 时必填）。
    """

    async def execute(
        self,
        action: Literal["list", "cancel"],
        job_id: str | None = None,
        *,
        caller: Agent,
    ) -> dict[str, Any]:
        """执行管理动作，返回结果 dict。

        .. rubric:: 调用关系（审计）

        - 调用：``CronScheduler.jobs``（时机：``action="list"``）；
          ``CronScheduler.unschedule``（时机：``action="cancel"``）
        - 被调：``flowing.tool.Tool.__call__`` 调度层（时机：LLM 每次
          调用 ``manage-cron``，``caller`` 由框架注入）

        .. seealso:: :meth:`flowing.tool.Tool.execute`
        """
        scheduler = caller.inject(cron_scheduler_key)
        if action == "list":
            return {"jobs": [job.to_dict() for job in scheduler.jobs(caller.node_id)]}  # P3-12①：唯一序列化通道
        # action == "cancel"：缺 job_id → status="error" 的 ToolResult
        # （错误包装细节未见具名规约）
        return {"cancelled": scheduler.unschedule(job_id or "")}
