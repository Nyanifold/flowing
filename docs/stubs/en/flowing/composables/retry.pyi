"""Built-in retry composable (optional; not enabled by default).

.. rubric:: Overview

This module provides ``use_retry()`` to enable a retry-with-backoff policy for
LLM call failures on one Agent instance. When the Agent's LLM call raises a
retryable exception, the default policy waits according to the selected
backoff model without calling the LLM, then retries the call within the same
turn until it succeeds or reaches the retry limit. Non-retryable and unknown
exceptions are not retried, and the turn ends with an error outcome.

This is an application-layer, built-in Composable. It ships with ``flowing``
but is not enabled automatically; an Agent developer must call
``use_retry(self)`` explicitly from ``setup()``. An Agent that does not enable
it has no retry-related state or handlers. Its error path is byte-for-byte
equivalent to the path without this Composable: an LLM exception is dispatched
through an empty ``on_provider_error`` chain, ``can_continue`` remains
``False``, the turn ends with an ``"error"`` outcome, and the Agent remains
alive.

Retry is a policy, not a mechanism. The framework core provides error
classification (which types are retryable or non-retryable; see
``flowing.errors``), the ``on_provider_error`` hook point, and the
``can_continue`` decision channel. This module supplies a replaceable default
policy for how many times to retry, how long to wait, and which errors to
retry. To replace it, remove the default handlers with
``remove_by_owner("retry")`` and register a custom handler; see the
:func:`use_retry` example.

.. rubric:: Registration surface

- Enablement is stage two only: call ``use_retry(self)`` from ``setup()``.
  During recovery, ``setup()`` runs on the new instance, so registrations do
  not accumulate. If this Composable is not enabled, there are no handlers
  owned by ``by="retry"`` on ``on_provider_error`` or ``before_turn``; the
  ``on_retry`` hook point is not declared, and accessing it raises
  :class:`flowing.errors.UnknownHookPointError`.
- The Composable registers no provide key, tool, or Agent state key.
- It declares the ``on_retry`` hook point with ``by="retry"`` and no
  ``match_on``. This is an observation point dispatched by this module at the
  call site: the code that declares the hook point dispatches it.
- It installs a ``before_turn`` counter-reset handler and an
  ``on_provider_error`` retry-decision handler, both owned by
  ``by="retry"``. ``remove_by_owner("retry")`` removes both handlers as a
  group. The ``on_retry`` hook-point declaration is not a handler and remains
  declared after those handlers are removed.
- Its scope is limited to the Agent instance on which it is called. It does
  not modify ``agent.model``, create messages, or persist state.

.. rubric:: Example

.. code-block:: python

    from flowing import Agent
    from flowing.composables.retry import use_retry

    class MyAgent(Agent):
        async def setup(self) -> None:
            use_retry(self, max_retries=3, base_delay=1.0)

.. rubric:: Behavioral notes

- The default policy retries only the four exception types listed in
  :data:`RETRYABLE_ERRORS`. It does not retry exceptions listed in
  :data:`NON_RETRYABLE_ERRORS` or any unknown exception. Waiting cannot fix
  invalid credentials or make an invalid request body valid.
- :class:`flowing.errors.ContextLengthError`, like other call-time exceptions,
  is dispatched through ``on_provider_error``. The default policy does not
  retry it because replaying the unchanged request would reproduce the error.
  A user handler must implement recovery such as compacting history or
  switching models.
- Retries happen inside the logical turn. They create no messages and add
  nothing to the message-level tree. The retry counter resets at turn
  boundaries, is not persisted, and is not restored after a crash.
- Multiple ``provider_gen()`` calls in the same turn share one retry budget;
  failures consume the same budget regardless of which call failed.

.. seealso::

    :func:`flowing.composables.compact.use_compact` — another built-in
    Composable with a similar structure.
    :class:`flowing.agent.ProviderErrorContext` — the value type received by
    decision handlers and its ``can_continue`` decision field.
    :class:`flowing.hooks.HookRegistry` — the container for the
    ``on_provider_error`` and ``before_turn`` hook points.
    :class:`flowing.agent.TurnContext` — the execution-time carrier for a
    logical turn.
"""

from typing import Literal

from flowing.agent import Agent

__all__: list[str]

MAX_RETRY_DELAY: float
"""Maximum duration, in seconds, of a single default backoff wait.

The value is 60 seconds. The default policy never passes a duration greater
than this value to ``asyncio.sleep``. This cap is especially relevant to
exponential backoff, which otherwise grows by powers of two: waits longer than
one minute can make an interactive turn in a lightweight Agent framework look
stuck.

This limit is part of the replaceable ``use_retry`` policy, not a core
mechanism. It constrains only the default handler; user-registered
``on_provider_error`` handlers are not subject to it.

.. seealso:: :func:`flowing.composables.retry.use_retry`
"""

RETRYABLE_ERRORS: tuple[type[BaseException], ...]
"""Exception types the default policy treats as retryable.

The tuple covers four categories: rate limiting and transient infrastructure
failures. They are :class:`flowing.errors.RateLimitedError`,
:class:`flowing.errors.ServerError`, :class:`flowing.errors.NetworkError`,
and :class:`flowing.errors.ProviderTimeoutError`.

This is a policy list, not a mechanism list. The mechanism ensures that
Provider adapters raise these types for the corresponding failures and
dispatch them through ``on_provider_error``. Choosing to retry them is the
decision made by ``use_retry``; a user handler may use a different list. By
default, exceptions not in this tuple, including all unknown exception types,
are not retried.

.. seealso:: :data:`flowing.composables.retry.NON_RETRYABLE_ERRORS`
"""

NON_RETRYABLE_ERRORS: tuple[type[BaseException], ...]
"""Exception types the default policy explicitly does not retry.

The tuple covers credential, request, quota, and context-length errors. It
contains :class:`flowing.errors.AuthenticationError`,
:class:`flowing.errors.ContextLengthError`,
:class:`flowing.errors.ContentPolicyError`,
:class:`flowing.errors.InvalidRequestError`,
:class:`flowing.errors.QuotaExhaustedError`, and
:class:`flowing.errors.RequestTooLargeError`.

The default handler passes these exceptions through unchanged: it does not
wait, increment the retry counter, or modify any fields. ``can_continue``
remains ``False`` and the turn ends with an error outcome. Retrying cannot
change the result: waiting does not fix credentials, make a request body
valid, add quota, or shorten the context. Recovery from
``ContextLengthError``—for example, compacting history or switching to a
model with a larger context window—is outside the default policy and must be
implemented by a user handler.

.. seealso:: :data:`flowing.composables.retry.RETRYABLE_ERRORS`
"""


def use_retry(
    agent: Agent,
    max_retries: int = 3,
    base_delay: float = 1.0,
    backoff: Literal["exponential", "fixed"] = "exponential",
) -> None:
    """Enable the optional, non-default retry policy for one Agent instance.

    .. rubric:: Overview

    This function registers a retry-decision handler on
    ``agent.hooks.on_provider_error`` (owned by ``by="retry"``) and a paired
    ``before_turn`` handler that resets the retry counter at the start of each
    logical turn. When the Agent's LLM call raises a retryable exception, the
    decision handler waits according to the selected backoff model without
    calling the LLM, then the call is retried within the same turn until it
    succeeds or reaches ``max_retries``. Non-retryable and unknown exceptions
    are not retried; the turn ends with an error outcome and the Agent remains
    alive.

    This is the stage-two entry point in a two-stage enablement model. Call it
    on an initialized instance from ``setup()`` or from other code while the
    instance is alive.

    .. rubric:: Minimal enablement

    .. code-block:: python

        async def setup(self) -> None:
            use_retry(self)

    .. rubric:: Configure the policy

    .. code-block:: python

        async def setup(self) -> None:
            use_retry(self, max_retries=10, base_delay=0.5, backoff="fixed")

    .. rubric:: Replace the default policy

    .. code-block:: python

        import asyncio

        from flowing.errors import RateLimitedError

        async def setup(self) -> None:
            use_retry(self)
            self.hooks.on_provider_error.remove_by_owner("retry")

            async def my_policy(agent, ctx):
                if isinstance(ctx.error, RateLimitedError):
                    await asyncio.sleep(5.0)
                    ctx.can_continue = True
                return ctx

            self.hooks.on_provider_error(my_policy, by="myapp")

    .. rubric:: Behavioral notes

    The function registers two handlers, both of which can be located and
    removed with ``by="retry"``:

    - The ``before_turn`` reset handler sets the retry counter to 0 at the
      start of each logical turn. It returns the value unchanged and is purely
      observational.
    - The ``on_provider_error`` decision handler applies the retry policy
      described below.

    Parameter semantics:

    - ``max_retries`` is the maximum number of retries permitted within one
      logical turn and must be ``>= 0``. The attempt counter starts at 1 on
      the first failure, and a retry is allowed only while
      ``attempt <= max_retries``. Thus, the default of 3 permits an initial
      call plus at most 3 retries, for at most 4 LLM calls. ``max_retries=0``
      is valid: the first failure is not retried, which is equivalent to not
      enabling retries except for one additional empty hook dispatch.
    - ``base_delay`` is the backoff base in seconds and must be ``>= 0``. A
      value of 0 retries without waiting. With ``backoff="fixed"``, it is
      the wait duration for every retry.
    - ``backoff`` selects the backoff model and accepts only
      ``"exponential"`` (the default) or ``"fixed"``.

    Retry decision (when the decision handler receives a
    ``ProviderErrorContext``):

    - If ``ctx.error`` is an instance of a type in
      :data:`NON_RETRYABLE_ERRORS` or is not in :data:`RETRYABLE_ERRORS`,
      including any unknown exception, the handler returns the context
      unchanged. It does not wait or increment the counter, and
      ``can_continue`` remains ``False``. This is conservative: only known,
      explicitly retryable types are allowed to continue.
    - If ``ctx.error`` is in :data:`RETRYABLE_ERRORS`, the handler increments
      the counter. If the counter exceeds ``max_retries``, it abandons the
      retry and returns the context unchanged without emitting an observation
      signal. Otherwise, it first sets ``ctx.can_continue = True``, then waits
      for ``delay`` seconds; the turn machinery subsequently retries the call
      within the same turn.
    - Delay calculation: with ``backoff="exponential"``, a
      :class:`flowing.errors.RateLimitedError` waits for
      ``min(base_delay * 2 ** (attempt - 1), MAX_RETRY_DELAY)``. Each of the
      other three infrastructure errors (``ServerError``, ``NetworkError``,
      and ``ProviderTimeoutError``) always waits for ``base_delay``; these
      transient infrastructure failures do not use exponential growth. With
      ``backoff="fixed"``, every retryable error waits for ``base_delay``.
      No random jitter is added, keeping delays predictable and testable.
      Applications that need jitter can register their own handler.

    Retry counter: the counter is internal state in the handler closure, not
    a framework field; the turn machinery has no retry-counter state machine.
    The counter resets at each logical-turn boundary through the
    ``before_turn`` reset handler. Multiple ``provider_gen()`` calls in the
    same turn share the counter, so failures consume the same budget
    regardless of which call failed. The counter is not persisted, included
    in snapshots, or restored after a crash.

    Retry observation signal (the ``on_retry`` hook point): whenever a retry
    is allowed, after setting ``can_continue`` and before waiting, the handler
    dispatches a snapshot in fire-and-forget mode with four keys:
    ``attempt``, ``max_retries``, ``delay``, and ``error``. The ``error`` value
    is the original exception from that failure. Subscribers can use it to
    display progress such as ``retrying...(1/10)``. Subscriber return values
    are ignored; this is an observation channel and does not participate in
    the decision chain. No signal is sent when the retry limit is reached.
    Subscriber exceptions are logged as warnings and do not affect the retry
    decision.

    Repeated calls: calls are not deduplicated for idempotency. Every call
    registers another independent pair of handlers with its own counter
    closure, so the function may be enabled multiple times with different
    parameters. During recovery, ``setup()`` runs on a new instance with a
    newly built hook registry, so registrations do not accumulate across
    recovery.

    Turn cancellation: if the user calls ``cancel()`` on the current
    execution while it is waiting, the wait itself cannot be interrupted.
    When the wait finishes, ``can_continue=True`` allows the retry path to
    proceed, and the turn machinery then stops normally at its checkpoint
    before the next ``provider_gen()`` call. Cancellation therefore takes
    effect after the current backoff wait finishes.

    This function does not modify ``agent.model`` or ``agent.model_tag``; a
    custom handler may change the model before retrying, but the default
    policy does not. It creates no messages, adds nothing to the
    message-level tree, and does not coordinate across different errors (for
    example, it does not switch policies after three consecutive rate limits).

    :param agent: The target Agent instance. The standard usage is to pass
        ``self`` from ``setup()``.
    :param max_retries: Maximum retries within one logical turn; must be
        ``>= 0``.
    :param base_delay: Backoff base in seconds; must be ``>= 0``. A value of
        0 retries without waiting.
    :param backoff: Backoff model: ``"exponential"`` (the default) or
        ``"fixed"``.
    :raises ValueError: Raised before any handler is registered if
        ``max_retries < 0``, ``base_delay < 0``, or ``backoff`` is not one of
        ``{"exponential", "fixed"}``.

    .. seealso::

        :class:`flowing.agent.ProviderErrorContext` — the value type received
            by the decision handler and its ``can_continue`` decision field.
        :meth:`flowing.lists.ManagedList.remove_by_owner` — the entry point
            for replacing the default policy as a group.
        :data:`RETRYABLE_ERRORS` / :data:`NON_RETRYABLE_ERRORS` — the default
            policy's error-classification lists.
    """
    ...
