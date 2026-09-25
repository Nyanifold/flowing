"""Data objects used by the scheduling extension.

This module defines :class:`CronJob`, the per-agent persisted record for one
scheduled task, and :class:`CronFireContext`, the value dispatched to the
``on_cron_trigger`` hook for a delivery attempt.

.. seealso:: :mod:`flowing.plugins.cron` (extension contract),
    :mod:`flowing.plugins.cron.jobs` (runtime and module API),
    :mod:`flowing.plugins.cron.cron` (plugin and ``use_cron``)
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class CronJob:
    """Persistent record describing when and what an agent should receive.

    ``cron`` defines the schedule, ``content`` is the message text to deliver
    when due, and ``source`` is a semantic label used by
    ``on_cron_trigger`` source-pattern matching. The agent carries its jobs in
    the persisted ``cron_jobs`` state key as a list of dictionaries. Objects
    are reconstructed on reads; the runtime does not keep a second data copy.

    This is a plain data container and performs no construction-time domain
    validation. The registration API :func:`flowing.plugins.cron.schedule`
    validates non-empty content, the five-field cron expression, placeholder
    formats, and job-ID uniqueness.

    ``source`` is not included in the message text. It is used for hook
    filtering and as the EVENT message source; an empty value becomes
    ``"cron"`` on the EVENT message and does not match non-wildcard hook
    patterns. When ``recurring`` is false, the record is removed after its
    first successful delivery. A skipped delivery does not remove it.

    All times are naive ``datetime`` values in the system's local time; no
    timezone normalization is performed. If ``last_fired_at`` is ``None``,
    ``created_at`` is the starting point for counting coalesced triggers.

    .. seealso:: :class:`CronFireContext`, :mod:`flowing.plugins.cron.jobs`
    """

    id: str
    """Task ID, unique within this agent. Registration generates an
    eight-character random hexadecimal string when none is supplied.
    """
    cron: str
    """Five-field cron expression (minute, hour, day, month, weekday),
    interpreted in local time with minute-level precision.
    """
    content: str
    """Message text to deliver when the task is due; it may contain a
    ``{{current_time}}`` placeholder.
    """
    created_at: datetime
    """Creation time in naive system-local time; it is the coalescing cursor
    when the task has never been delivered.
    """
    source: str = ""
    """Semantic label used by ``on_cron_trigger`` ``match_on="source"``
    fnmatch filtering and as the EVENT message source. An empty value is
    stored as ``""`` and becomes ``"cron"`` for the EVENT message source;
    the label is never included in the message body.
    """
    recurring: bool = True
    """Whether the task repeats. A one-shot task is removed after its first
    successful delivery.
    """
    last_fired_at: datetime | None = None
    """Time of the last successful delivery in naive system-local time. This
    coalescing cursor advances only after successful delivery; ``None`` means
    that no delivery has succeeded yet.
    """

    def to_dict(self) -> dict[str, Any]:
        """Serialize the record as a plain JSON-compatible dictionary.

        Fields are stored flat. Datetimes become ISO 8601 strings, while a
        ``None`` value for ``last_fired_at`` remains ``None``. ``source`` is
        always included, even when it is empty, so the persisted shape is
        explicit. This is the serialization path for the ``cron_jobs`` state
        key.
        """
        ...

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CronJob":
        """Reconstruct a record from persisted fields without domain checks.

        Persisted state is treated as the source of truth, and this method only
        maps fields and parses ISO 8601 timestamps. If ``source`` or
        ``recurring`` is absent, the defaults are ``""`` and ``True``;
        a missing or empty ``last_fired_at`` becomes ``None``. Required fields
        are read directly from the mapping.
        """
        ...


@dataclass
class CronFireContext:
    """Mutable view of one scheduled-job delivery attempt.

    When a task becomes due, either normally or during recovery, the delivery
    routine creates this object and dispatches it through
    ``agent.hooks.on_cron_trigger`` before assembling and enqueueing the
    message. A handler can change ``content`` for this attempt only; the change
    is not persisted. Setting ``shortcut`` to ``True`` skips the delivery.
    Raising ``Intercepted`` has the same effect. In either case the coalescing
    cursor does not advance, so missed triggers remain eligible for a later
    delivery.

    The hook uses ``match_on="source"`` and, for handlers registered with a
    pattern, filters against this value's top-level ``source`` field with
    fnmatch patterns. ``coalesced_count`` is the number of ideal trigger times
    in the interval
    ``(last_fired_at or created_at, fired_at]``. A count of one is an ordinary
    due delivery; a count of two or more produces the missed-trigger notice.
    ``content`` starts as the original task text, before
    ``{{current_time}}`` substitution. Substitution happens after hook
    dispatch, so it also applies to content changed by a handler.

    .. seealso:: :class:`CronJob`,
        :func:`flowing.plugins.cron.use_cron`
    """

    id: str
    """Task ID; equal to ``job.id``."""
    source: str
    """Task label from ``job.source`` and the hook's pattern-matching field."""
    cron: str
    """Read-only task cron expression.
    """
    content: str
    """Text to deliver for this attempt. It starts as the original task text;
    a hook may change it for this attempt without persisting that change.
    """
    recurring: bool
    """Read-only flag indicating whether the task repeats."""
    scheduled_at: datetime
    """The last ideal trigger time in the counted interval, corresponding to
    this delivery attempt.
    """
    fired_at: datetime
    """Actual delivery-attempt time in naive system-local time."""
    coalesced_count: int
    """Number of ideal trigger times in the counted interval, including this
    attempt; the value is at least one for a dispatched context.
    """
    last_fired_at: datetime | None
    """Previous successful delivery time, which is the cursor before this
    attempt; ``None`` means there has been no successful delivery.
    """
    shortcut: bool | None = None
    """Skip flag, initially ``None``. If any hook handler sets it to ``True``,
    this attempt is skipped without advancing the cursor, so missed triggers
    continue to accumulate.
    """
