"""``flowing.plugins.cron`` —— 定时扩展子包。

本包承载定时扩展的全部公开符号：:class:`CronPlugin`（工具注册插件）、
:func:`use_cron`（per-Agent 启用）、:class:`CronJob`（定时任务记录）、
:class:`CronFireContext`（``on_cron_trigger`` 钩子 value），以及模块级
任务 API（:func:`schedule` / :func:`unschedule` / :func:`jobs`）。对外
API 经本 ``__init__`` 统一再导出。

.. seealso:: :mod:`flowing.plugins.cron.cron`（插件与 ``use_cron``）、
    :mod:`flowing.plugins.cron.jobs`（任务运行时与模块 API）、
    :mod:`flowing.plugins.cron.models`（数据对象）、
    :mod:`flowing.plugins.cron.tools`（LLM 工具）
"""

from .cron import CronPlugin, use_cron
from .jobs import jobs, schedule, unschedule
from .models import CronFireContext, CronJob

__all__ = [
    "CronPlugin",
    "use_cron",
    "CronJob",
    "CronFireContext",
    "schedule",
    "unschedule",
    "jobs",
]
