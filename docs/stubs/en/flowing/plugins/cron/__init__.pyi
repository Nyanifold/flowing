"""Public API for the per-agent scheduling extension.

The package exports :class:`CronPlugin` for registering its LLM tools,
:func:`use_cron` for enabling scheduling on an agent, the persisted
:class:`CronJob` record and :class:`CronFireContext` hook value, and the
module-level :func:`schedule`, :func:`unschedule`, and :func:`jobs` functions.
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
