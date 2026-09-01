"""``flowing.plugins.cron`` —— 定时扩展子包。

本包承载定时扩展的全部公开符号：:class:`CronPlugin` （阶段一插件）、
:func:`use_cron` （阶段二启用）、:class:`CronScheduler` （调度器）、
:class:`CronJob` / :class:`CronAction` / :class:`CronTrigger` /
:class:`CronFireContext` （数据对象）、:class:`CronExecutor` /
:class:`CronTemplate` 与默认模板、执行器常量，以及四件全局工具
（``schedule-cron`` 系列加 ``manage-cron``）；对外 API 经本 ``__init__``
统一再导出。

.. seealso:: :mod:`flowing.plugins.cron.cron` （插件主模块：启用方式与
    注册面清单）、:mod:`flowing.plugins.cron.scheduler` （调度器）、
    :mod:`flowing.plugins.cron.models` （数据对象）、
    :mod:`flowing.plugins.cron.executors` （执行器与模板）、
    :mod:`flowing.plugins.cron.tools` （LLM 工具）
"""

from .cron import CronPlugin, use_cron
from .executors import (
    CronExecutor,
    CronTemplate,
    DEFAULT_MESSAGE_TEMPLATE,
    DEFAULT_TOOL_NOTICE_TEMPLATE,
    default_message_executor,
    default_tool_executor,
)
from .models import CronAction, CronFireContext, CronJob, CronTrigger
from .scheduler import CronScheduler, cron_scheduler_key
from .tools import (
    ManageCronTool,
    ScheduleCronMessageTool,
    ScheduleCronTool,
    ScheduleCronToolCallTool,
)

__all__ = [
    "CronPlugin",
    "CronScheduler",
    "CronAction",
    "CronJob",
    "CronTrigger",
    "CronFireContext",
    "CronExecutor",
    "CronTemplate",
    "default_message_executor",
    "default_tool_executor",
    "DEFAULT_MESSAGE_TEMPLATE",
    "DEFAULT_TOOL_NOTICE_TEMPLATE",
    "ScheduleCronTool",
    "ScheduleCronMessageTool",
    "ScheduleCronToolCallTool",
    "ManageCronTool",
    "cron_scheduler_key",
    "use_cron",
]
