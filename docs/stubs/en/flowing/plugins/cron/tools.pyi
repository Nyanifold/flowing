"""LLM tools for creating, listing, and cancelling scheduled jobs.

This module defines two globally registered tools: :class:`ScheduleCronTool`
registers a scheduled message for the calling agent, and
:class:`ManageCronTool` lists or cancels that agent's jobs. Installing
:class:`flowing.plugins.cron.CronPlugin` registers both tools in the runtime's
global registry. An agent exposes either tool to an LLM by adding it with
``add_tool(...)``. Both tools are restricted to their injected ``caller``;
the LLM cannot manage another agent's tasks.

.. seealso:: :mod:`flowing.plugins.cron` (extension contract),
    :mod:`flowing.plugins.cron.jobs` (module API and validation)
"""

from typing import Any, Literal

from flowing.agent import Agent
from flowing.tool import Tool, ToolDefinition


class ScheduleCronTool(Tool):
    """Register a scheduled message task for the calling agent.

    The runtime registers this global tool through
    :meth:`flowing.plugins.cron.CronPlugin.install`; an agent must also expose
    it with ``add_tool("schedule-cron")`` before an LLM can call it. The target
    is always the injected ``caller``. Its operation is equivalent to
    :func:`flowing.plugins.cron.jobs.schedule` and returns a receipt.

    Validation errors, including empty content, an invalid cron expression or
    placeholder format, and a conflicting task ID, are wrapped by
    ``Tool.__call__`` as a ``ToolResult`` with ``status="error"``. They do not
    invoke the tool error hook.

    On success, the receipt contains ``job_id``, ``cron``, ``source``,
    ``recurring``, and ``next_fire``. ``next_fire`` is the next ideal trigger
    time as an ISO 8601 string in naive system-local time. ``source`` labels
    the task for ``on_cron_trigger`` pattern filtering and becomes the EVENT
    message source; ``job_id`` can be supplied explicitly for stable
    identification and later cancellation.

    .. seealso:: :class:`flowing.tool.Tool`, :class:`ManageCronTool`,
        :func:`flowing.plugins.cron.jobs.schedule`
    """

    definition: ToolDefinition

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
        """Register a task for ``caller`` and return its receipt.

        :param cron: Five-field cron expression (minute hour day month
            weekday).
        :param content: Message text delivered when the task is due.
        :param source: Optional semantic label; defaults to the empty string.
        :param recurring: Whether the task repeats; ``False`` makes it a
            one-shot task.
        :param job_id: Optional explicit task ID.
        :param caller: Calling agent injected by the framework and not visible
            as an LLM argument.
        :return: A dictionary containing ``job_id``, ``cron``, ``source``,
            ``recurring``, and ``next_fire``.
        :raises ValueError: Registration validation fails. ``Tool.__call__``
            converts this into an error result.
        """
        ...


class ManageCronTool(Tool):
    """List or cancel scheduled jobs belonging to the calling agent.

    ``action="list"`` returns the caller's tasks as dictionaries;
    ``action="cancel"`` removes the task identified by ``job_id``. The target
    is always the injected caller. If cancellation names no existing task, the
    result reports ``False``. Omitting ``job_id`` for cancellation is an input
    error, which ``Tool.__call__`` wraps in a ``ToolResult`` with
    ``status="error"``.

    .. seealso:: :class:`ScheduleCronTool`,
        :func:`flowing.plugins.cron.jobs.jobs`
    """

    definition: ToolDefinition

    async def execute(
        self,
        action: Literal["list", "cancel"],
        job_id: str | None = None,
        *,
        caller: Agent,
    ) -> dict[str, Any]:
        """Perform a task-management action for ``caller``.

        :param action: ``"list"`` to list jobs or ``"cancel"`` to remove one.
        :param job_id: ID of the task to cancel; required for ``"cancel"``.
        :param caller: Calling agent injected by the framework and not visible
            as an LLM argument.
        :return: ``{"jobs": [...]}`` for listing or
            ``{"cancelled": bool}`` for cancellation.
        :raises ValueError: ``action`` is ``"cancel"`` and ``job_id`` is
            missing.
        """
        ...
