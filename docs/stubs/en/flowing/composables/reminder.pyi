"""Built-in, optional Composable for injecting system reminders per turn.

This module provides ``use_system_reminder()``. It enables a policy for one
Agent instance that injects system reminders before each logical turn. At the
start of an eligible turn, the policy combines the currently visible reminder
contents into one ``Message(kind=EVENT, ...)`` with multiple content blocks.
The ``before_turn`` handler appends it to the pending batch that will be
attached to the message tree and persisted, after the message that triggered
the turn.

Typical reminder contents include frequently updated values such as the
current time, working directory, and task status. They should not be placed
in prompt blocks, where they would disrupt Provider prefix caching; the
message channel appends them to the conversation history instead.

This is an application-layer Composable shipped with the ``flowing`` package,
but it is not enabled automatically. Agent developers must call it explicitly
from ``setup()``. An Agent that does not call it has no reminder handlers or
reminder-related state.

.. rubric:: Registration surface

- Enablement has two phases. Call ``use_system_reminder(self)`` from
  ``setup()`` in phase two. During recovery, ``setup()`` runs on the new
  instance, so the handlers do not accumulate across instances. When this
  Composable is not enabled, neither the ``before_turn`` nor the ``after_turn``
  chain contains a handler owned by ``"system-reminder"``; there is no runtime
  overhead.
- The Composable registers no provide key, tool, or Agent state key, and it
  does not change prompt blocks.
- It declares no hook points. It only attaches handlers to hook points
  already provided by the core.
- It attaches a ``before_turn`` injection handler owned by
  ``"system-reminder"``. With ``clean=True``, it also attaches an
  ``after_turn`` cleanup handler with the same owner. A single call to
  ``remove_by_owner("system-reminder")`` removes both handlers.
- Its scope is the Agent instance passed to it. Injected reminders are
  persisted with their message batch when ``clean=False``; interval-tracking
  state is not persisted.

.. rubric:: Example

.. code-block:: python

    async def setup(self) -> None:
        use_system_reminder(
            self,
            contents=[lambda agent: f"Current node: {agent.node_id}"],
            message_interval=4,  # Inject only after at least four new messages.
        )

.. rubric:: Behavior notes

- Each eligible logical turn receives one ``Message`` with
  ``kind=EVENT``, ``source="system-reminder"``, and
  ``tags=["system-reminder"]``. Multiple ``TextBlock`` objects hold the
  reminder text. The message is appended to the end of ``pending_messages``,
  after the triggering message, and is persisted with the batch.
- Each ``contents`` entry is one of three forms: a ``(agent) -> str | None``
  callback (``None`` and empty strings skip that entry), a
  :class:`flowing.parsable.Parsable` resolved with the current Agent at
  injection time, or a string. Strings are normalized to Parsable values
  during initialization, so ``{{ }}`` templates and ``$`` references are
  evaluated at each injection; strings without markers remain literal
  constants. Empty results are discarded. An empty list, including the
  default ``None``, or a list whose entries all evaluate to empty values
  produces no reminder for that turn.
- The supplied ``contents`` list is held by reference, not copied. A common
  pattern is ``use_system_reminder(self, self.system_reminders)``; subsequent
  additions and removals from that list affect injection. Initialization
  normalizes its existing entries in place and does not wrap existing
  Parsable values again. Bare strings appended later are handled lazily as
  Parsable values during injection. A fourth entry type (anything other than
  a callback, Parsable, or string) raises ``TypeError`` during initialization.
- ``message_interval`` and ``time_interval`` are combined with AND semantics:
  if either condition is unmet, injection is skipped. Both default to zero,
  which injects on every turn. The first injection is not limited by either
  interval.
- With ``clean=True``, the cleanup handler removes each turn's injected
  reminder by its ``tags`` at turn end; the Agent layer handles moving the
  message-tree head back. This adds a cleanup write at the end of every turn
  (roughly doubling write volume), and the removed reminder is absent from
  later history replay, including after crash recovery. With ``clean=False``
  (the default), reminders remain in the message tree.
- The reminder shares the triggering message's batch. If ``before_turn`` is
  blocked by ``Intercepted``, the entire batch is discarded without
  persistence, so the reminder is not injected either.

.. seealso:: :class:`flowing.agent.TurnContext` carries the appended
    ``pending_messages``; :func:`flowing.composables.retry.use_retry` uses a
    similar installation pattern.
"""

from flowing.agent import Agent


def use_system_reminder(
    agent: Agent,
    contents: list | None = None,
    *,
    clean: bool = False,
    message_interval: int = 0,
    time_interval: float = 0,
) -> None:
    """Enable the optional policy that injects system reminders per turn.

    This registers a ``before_turn`` injection handler owned by
    ``"system-reminder"``. At the start of each logical turn, the handler
    combines the currently visible reminder contents into one
    ``Message(kind=EVENT, source="system-reminder", ...)`` and appends it to
    the end of ``pending_messages``, after the triggering message. It is
    persisted with that batch. With ``clean=True``, an ``after_turn`` handler
    with the same owner removes the reminder injected for that turn by its
    ``tags``.

    The three supported ``contents`` entry forms are callbacks, Parsable
    values, and ordinary strings. The supplied list is held by reference, so
    reminder contents can be maintained as plain data on the Agent instance
    (for example, assign ``self.system_reminders = [...]`` and pass that list
    here). Templates are resolved at injection time.

    This is the second-phase entry point in the two-phase enablement model.
    Call it on an initialized instance, from ``setup()`` or other code while
    that instance is alive.

    .. rubric:: Callback-based reminder

    .. code-block:: python

        async def setup(self) -> None:
            use_system_reminder(
                self,
                contents=[
                    lambda agent: f"Current node: {agent.node_id}",
                    "Please verify the amount first.",
                ],
                clean=True,           # Remove it at turn end.
                message_interval=4,   # Wait for four new messages.
            )

    .. rubric:: Keep reminder contents on an instance attribute

    .. code-block:: python

        async def setup(self) -> None:
            self.system_reminders = ["Current mode: {{ current_mode }}"]
            use_system_reminder(self, self.system_reminders)

    .. rubric:: Declare reminder contents in ``.fya``

    The assembler places unknown fields in ``_extra`` before user ``setup()``
    runs, and ``Agent.__getattr__`` makes the value available as
    ``self.system_reminders``.

    .. code-block:: yaml

        system_reminders:
          - "Current mode: {{ current_mode }}"

    .. code-block:: python

        async def setup(self) -> None:
            use_system_reminder(self, self.system_reminders)

    .. rubric:: Behavior notes

    - ``contents`` is a reminder list whose entries are callbacks returning
      ``str | None``, :class:`flowing.parsable.Parsable` values resolved with
      ``resolve(agent)``, or ordinary strings normalized to Parsable values
      when this function is called. ``None`` or an empty string from a
      callback skips that entry. Empty results are discarded; an empty list
      (including ``None``) or all-empty results produce no reminder. The list
      is held by reference, so later additions and removals take effect. A
      fourth type raises ``TypeError`` when this function is called.
    - Injection occurs only when the number of new messages since the previous
      injection is at least ``message_interval`` and the elapsed wall-clock time
      is at least ``time_interval`` seconds. Both conditions must hold. They
      both default to zero, which allows injection on every turn, and the first
      injection is unrestricted. Interval state lives only in the handler
      closure; it is not persisted or stored in the Agent state bag.
    - The message-count watermark is ``len(agent._messages)``, the number of
      nodes in the message-level tree. When ``before_turn`` runs,
      ``turn.message_ids`` is always empty because the current batch has not yet
      been attached to the tree; it cannot be used for this count.
    - With ``clean=True``, the reminder is removed at the end of each turn.
      This roughly doubles write volume, and the deleted reminder is absent
      from later history replay, including after crash recovery.
    - The reminder shares its batch with the triggering message. If
      ``before_turn`` is blocked by ``Intercepted``, the entire batch is
      discarded without persistence, so the reminder is not injected.
    - Repeated calls are not deduplicated. Each call adds another pair or
      single handler set with an independent interval-state closure, so
      multiple policies with different settings are allowed. On recovery,
      ``setup()`` runs on a new instance whose hook registry is rebuilt, so
      registrations do not accumulate.
    - This function does not register template globals, change prompt blocks or
      ``agent.model``, or persist any reminder-specific state.

    :param agent: The target Agent instance. The usual call site passes
        ``self`` from ``setup()``.
    :param contents: Reminder entries as callbacks, Parsable values, or
        strings. ``None`` means an empty list. The list is held by reference,
        so runtime changes to an attribute such as ``agent.system_reminders``
        are reflected in subsequent injections.
    :param clean: If ``True``, remove the reminder injected during each turn
        at turn end. If ``False`` (the default), it remains in the message tree
        and can be recovered after a crash.
    :param message_interval: Minimum number of new messages since the previous
        injection; must be ``>= 0`` and defaults to ``0`` (inject every turn).
    :param time_interval: Minimum wall-clock interval in seconds since the
        previous injection; must be ``>= 0`` and defaults to ``0`` (no delay).

    .. seealso:: :class:`flowing.agent.TurnContext` carries the appended
        ``pending_messages``.
    """
    ...
