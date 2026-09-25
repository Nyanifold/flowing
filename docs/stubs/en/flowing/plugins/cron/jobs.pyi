"""Per-agent scheduling runtime and module-level task API.

The runtime parses cron expressions, counts ideal trigger times for missed-job
coalescing, substitutes ``{{current_time}}`` placeholders, and uses one
delivery path for timer fires and recovery catch-up. The public functions
:func:`schedule`, :func:`unschedule`, and :func:`jobs` take an agent first and
operate on its ``cron_jobs`` state table.

.. rubric:: Persistence model

The agent's ``state.cron_jobs`` list is the only source of truth. The runtime
does not keep a data mirror: a change reads the list, modifies it, and assigns
the new list back so that the state is persisted. Because delivery awaits hook
dispatch and message enqueue, it rereads the current list afterward and
replaces or removes only its own record by ID. Concurrent deliveries therefore
do not overwrite one another's cursors.

.. rubric:: Time and placeholders

- The module's only clock entry point is ``_now()``, which calls
  ``datetime.now()`` and returns system-local naive time. Cron expressions are
  interpreted against the local wall clock, with no timezone normalization.
- Message text supports ``{{current_time}}`` using the default format and
  ``{{current_time:<strftime>}}`` using a supplied Python ``strftime`` format.
  Substitution is mechanical and occurs before a missed-trigger notice is
  assembled.
- Invalid placeholder directives are rejected with ``ValueError`` when the
  task is registered, not when its timer fires.
- On recovery, each task is checked from its persisted cursor (``last_fired_at``),
  or from ``created_at`` if it has never been delivered. The runtime counts ideal
  trigger times in ``(cursor, now]`` and, if one or more have accumulated,
  attempts one delivery for that interval. When at least two trigger times have
  accumulated, the message is wrapped in the missed-trigger notice. After the
  catch-up pass, remaining tasks are armed for their next ideal trigger.
- For recurring tasks, the cursor advances to the current time only after the
  EVENT message is successfully enqueued; a one-shot task is removed instead.
  An intercepted or short-circuited delivery, a hook dispatch failure, or an
  enqueue failure leaves the record and cursor unchanged, so those trigger
  times can be counted again by a later timer fire or recovery pass.

.. seealso:: :mod:`flowing.plugins.cron` (extension contract),
    :mod:`flowing.plugins.cron.models` (data objects),
    :mod:`flowing.plugins.cron.cron` (plugin and ``use_cron``)
"""

from datetime import datetime
from typing import Any
from flowing.agent import Agent
from flowing.plugins.cron.models import CronJob


MISSED_NOTICE: str = (
    "Scheduled job {cron} missed {count} trigger(s) "
    "(last successful delivery: {last_fired_at}); delivering once now:\n{content}"
)
"""Complete notice used when at least two ideal triggers have coalesced.

The template is self-contained and has no injection point. Its renderer uses
ordered ``str.replace`` calls rather than ``str.format``, so arbitrary braces
and ``{{current_time}}`` tokens in the message content require no escaping.
The supported tokens are ``{count}`` (the number of missed triggers),
``{cron}`` (the original expression), ``{last_fired_at}`` (the previous
successfully enqueued delivery in local ``%Y-%m-%d %H:%M`` format, or
``never delivered``), and ``{content}`` (the message after current-time
substitution). The notice does not name a single expected trigger time because
multiple triggers may have been missed.
"""


class _CronRuntime:
    def schedule(
        self,
        cron: str,
        content: str,
        *,
        job_id: str | None = None,
        source: str = "",
        recurring: bool = True,
    ) -> str: ...

    def unschedule(self, job_id: str) -> bool: ...

    def jobs(self) -> list[CronJob]: ...

    def cancel_all(self) -> None: ...

    async def rebuild_after_recover(self) -> None: ...


def substitute_current_time(text: str, now: datetime) -> str:
    """Replace each supported current-time token in text with a formatted time.

    ``{{current_time}}`` uses ``%Y-%m-%d %H:%M:%S``. A token may provide a
    format after a colon, as in ``{{current_time:%Y-%m-%d %H:%M}}``. This is a
    regular-expression substitution, not Parsable or Jinja rendering. An
    invalid format can make ``strftime`` raise ``ValueError``; registration
    validates placeholder directives first so this should not occur at fire
    time for a task accepted by :func:`schedule`.

    :param text: Message text that may contain current-time tokens.
    :param now: Naive system-local time to substitute.
    :return: Text with matched tokens replaced.
    """
    ...


def validate_placeholders(content: str) -> None:
    """Validate strftime directives in all recognized time placeholders.

    A directive outside Python's supported whitelist, including a trailing
    isolated ``%``, raises ``ValueError``. This check is necessary because some
    platform ``strftime`` implementations leave unknown directives such as
    ``%Q`` unchanged instead of reporting an error. The validation runs at
    registration time so a configuration error is reported at its boundary.

    :param content: Task message text to inspect.
    :raises ValueError: A recognized placeholder contains an invalid format.
    """
    ...


def schedule(
    agent: Agent,
    cron: str,
    content: str,
    *,
    job_id: str | None = None,
    source: str = "",
    recurring: bool = True,
) -> str:
    """Register a scheduled message for one agent and return its task ID.

    The agent must first be enabled with :func:`flowing.plugins.cron.use_cron`.
    Registration rejects empty content, expressions that do not contain five
    valid cron fields, invalid current-time placeholder formats, and IDs that
    already exist for that agent. If ``job_id`` is omitted, an eight-character
    hexadecimal string is generated. The new record is written to the agent's
    persistent state table before its timer is armed.

    Cron fields are interpreted as minute, hour, day, month, and weekday in
    system-local wall-clock time. All scheduling timestamps are naive local
    ``datetime`` values. A one-shot task (``recurring=False``) is removed only
    after its EVENT message has been successfully enqueued; an intercepted or
    short-circuited delivery, a hook dispatch failure, or an enqueue failure
    leaves it registered. A failure before enqueue also leaves the persisted
    cursor unchanged.

    :param agent: Agent whose task table will receive the job.
    :param cron: Five-field cron expression (minute hour day month weekday).
    :param content: Non-empty message text; may contain
        ``{{current_time}}`` or ``{{current_time:<strftime>}}``.
    :param job_id: Optional ID unique within this agent. A random ID is
        generated when omitted.
    :param source: Optional semantic label used for hook filtering and as the
        EVENT message source.
    :param recurring: Whether the task remains after a successful delivery.
    :return: The registered task ID.
    :raises ValueError: The agent has not been enabled, content is empty, the
        cron expression or placeholder format is invalid, or the ID conflicts.

    .. seealso:: :class:`flowing.plugins.cron.models.CronJob`,
        :class:`flowing.plugins.cron.models.CronFireContext`
    """
    ...


def unschedule(agent: Agent, job_id: str) -> bool:
    """Cancel a task and remove its record from the agent's state table.

    Any armed timer for the task is cancelled. If the ID is absent from the
    agent's table, the function returns ``False``; otherwise it removes the
    record, writes the updated table back, and returns ``True``. The agent must
    have been enabled with :func:`flowing.plugins.cron.use_cron`.

    :param agent: Agent whose task should be removed.
    :param job_id: ID of the task to cancel.
    :return: Whether a matching task record existed and was removed.
    :raises ValueError: The agent has no cron runtime because
        :func:`flowing.plugins.cron.use_cron` was not called.
    """
    ...


def jobs(agent: Agent) -> list[CronJob]:
    """Return a read-only snapshot of the agent's scheduled jobs.

    Each result is reconstructed from the persisted state table. Modifying a
    returned ``CronJob`` does not modify the stored record. The agent must have
    been enabled with :func:`flowing.plugins.cron.use_cron`.

    :param agent: Agent whose jobs to retrieve.
    :return: A list of reconstructed :class:`CronJob` objects.
    :raises ValueError: The agent has no cron runtime because
        :func:`flowing.plugins.cron.use_cron` was not called.
    """
    ...
