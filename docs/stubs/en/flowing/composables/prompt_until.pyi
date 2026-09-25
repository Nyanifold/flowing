"""Optional composable that steers an Agent to continue until a predicate passes.

Call :func:`use_prompt_until` for one initialized Agent to register an
``after_turn`` handler. At the end of each non-aborted turn, the handler calls
the supplied predicate. A truthy result does nothing; a false result evaluates
the steering content and queues it as an ``EVENT`` message with ``STEER``
priority. The current turn is not interrupted; the next logical turn consumes
the message.

This composable supplies a mechanism, not a completion policy. The predicate
defines when work is complete, and the message defines what to ask the Agent
to do next. Cancelled, interrupted, or destroyed turns marked as aborted are
skipped. Blocked and error turns are still checked, so the predicate can
decide whether either outcome warrants another turn. If the predicate always
returns false and the message remains non-empty, the Agent continues without
an automatic limit; callers must provide a stopping condition or remove the
handler with ``remove_by_owner("prompt-until")``.

The composable is not enabled by default and affects only the Agent instance
passed to it. Enable it explicitly during that Agent's ``setup()``; recovery
runs ``setup()`` on a new instance, so registrations do not accumulate. It
adds only an ``after_turn`` handler owned by ``"prompt-until"``. It declares
no hook point, provide key, tool, or Agent state key, changes no prompt blocks,
and persists no state. Remove all of its handlers with
``remove_by_owner("prompt-until")``. Calling it more than once on one instance
installs independent handlers. A
callable message must be synchronous and may return ``None`` or an empty
string to stop steering; a string is converted to a
:class:`flowing.parsable.Parsable` and
resolved against the current Agent whenever steering is needed. Other message
types raise ``TypeError``. Exceptions from the predicate, message evaluation,
or steering follow the ``after_turn`` hook's exception behavior and are not
silently swallowed.

.. rubric:: Example

.. code-block:: python

    from flowing.message import MessageKind, TextBlock

    def is_complete(agent, turn):
        for message_id in reversed(turn.message_ids):
            message = agent.chain.get(message_id)
            if message.kind is MessageKind.PROVIDER:
                text = "".join(
                    block.text for block in message.content
                    if isinstance(block, TextBlock)
                )
                return "DONE" in text
        return True  # No provider message means there is nothing to continue.

    use_prompt_until(
        self,
        predicate=is_complete,
        message=(
            "The acceptance criteria are not met yet (the final reply must "
            "include DONE). Please continue."
        ),
    )

.. rubric:: Template-based message example

.. code-block:: python

    async def setup(self) -> None:
        # The template is evaluated against this Agent on each steering attempt.
        self.continue_hint = "Attempt {{ retry_no }}: continue the unfinished task."
        use_prompt_until(self, self._not_done_yet, self.continue_hint)

.. seealso:: :func:`flowing.composables.reminder.use_system_reminder` for a
    related callback/``Parsable``/string content shape, and
    :meth:`flowing.agent.Agent.steer` for the queueing API.
"""

from typing import Callable, TypeAlias, Union

from flowing.agent import Agent, TurnContext
from flowing.parsable import Parsable

__all__ = ["use_prompt_until"]

PromptPredicate: TypeAlias = Callable[[Agent, TurnContext], bool]
"""Synchronous callback that receives the Agent and completed turn context.

A truthy return value permits the turn sequence to stop; a false value
requests evaluation of the steering message. The runtime checks truthiness and
does not require the callback to return the literal ``bool`` object.
"""

PromptMessage: TypeAlias = Union[Callable[[Agent], str | None], Parsable, str]
"""Steering content: a synchronous Agent callback, a :class:`flowing.parsable.Parsable`, or a string.

The callback may return ``None`` or an empty string to skip this steering
attempt. A plain string is converted to a :class:`flowing.parsable.Parsable` when the composable
is installed, so its template is evaluated against the current Agent for each
steering attempt.
"""


def use_prompt_until(
    agent: Agent,
    predicate: PromptPredicate,
    message: PromptMessage,
) -> None:
    """Install an optional turn-end predicate and steering handler on one Agent.

    The handler runs after each logical turn. If ``predicate(agent, turn)`` is
    truthy, it leaves the message queue unchanged. Otherwise it evaluates
    ``message`` and, when the result is non-empty, calls
    :meth:`flowing.agent.Agent.steer` to queue an ``EVENT`` with ``STEER``
    priority for the next logical turn. This does not interrupt the turn that
    just ended.

    :param agent: Initialized Agent instance to configure. The usual call site
        is the Agent's ``setup()`` method.
    :param predicate: Synchronous callback receiving ``(agent, turn)``. The
        turn argument is a :class:`flowing.agent.TurnContext`; its truthy result
        marks the work complete, and a false result requests steering.
    :param message: Synchronous callback, :class:`flowing.parsable.Parsable`,
        or string used as steering content. ``None`` or an empty evaluated
        string skips queueing and can end the continuation sequence.
    :return: ``None``.
    :raises TypeError: ``message`` is not callable, a ``Parsable``, or a string.

    .. rubric:: Behavior

    - A turn whose ``TurnContext.aborted`` is true is neither checked nor
      steered. This covers cancellation, interruption, and destruction-driven
      aborts. Blocked and error turns are checked normally.
    - The callback's return value is tested for truthiness. It need not be the
      literal ``True`` or ``False`` object.
    - If a predicate remains false and every evaluated message is non-empty,
      continuation has no built-in turn limit. The caller is responsible for
      termination, for example by making the predicate pass, returning
      ``None`` from a message callback, or removing this owner's handlers.
    - Each call installs a separate handler. To remove all handlers installed
      under this composable's owner name, call
      ``agent.hooks.after_turn.remove_by_owner("prompt-until")``.
    - Exceptions raised by the predicate, content evaluation, or
      ``Agent.steer`` follow ``after_turn`` hook dispatch behavior.

    .. rubric:: Example

    .. code-block:: python

        from flowing.message import MessageKind, TextBlock

        def is_complete(agent, turn):
            for message_id in reversed(turn.message_ids):
                message = agent.chain.get(message_id)
                if message.kind is MessageKind.PROVIDER:
                    text = "".join(
                        block.text for block in message.content
                        if isinstance(block, TextBlock)
                    )
                    return "DONE" in text
            return True

        use_prompt_until(
            self,
            predicate=is_complete,
            message="The acceptance criteria are not met yet. Please continue.",
        )

        # A callback can build steering content from the Agent's current state.
        async def setup(self) -> None:
            use_prompt_until(
                self,
                self._tests_not_green,
                lambda agent: (
                    f"There are still {agent.state.remaining} unfinished items. "
                    "Please continue."
                ),
            )

    .. seealso:: :func:`flowing.composables.reminder.use_system_reminder` for
        the same content forms used at a different turn hook, and
        :meth:`flowing.agent.Agent.steer` for queueing steering messages.
    """
    ...
