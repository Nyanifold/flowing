"""Optional composables that validate JSON payloads against a JSON Schema.

This module ships two ``use_xxx()`` functions that check the two directions of
JSON traffic:

* :func:`use_validate_turn_result` — final-reply validation. Whenever a
  main-turn final response arrives (``after_provider_gen``, ``finish=True``,
  no tool call blocks), the handler joins the ``TextBlock`` content of the
  response message into the final text, parses it with ``json.loads``, and
  validates the result against the Pydantic model compiled from the schema at
  mount time. Text that is not valid JSON and JSON that does not conform to
  the schema both count as validation failures, handled per ``on_fail``:
  ``"steer"`` (default) rewrites ``response.finish`` to ``False`` and queues a
  rendered repair-template message so the turn's inner loop continues within
  the same logical turn; ``"warn"`` emits an English ``warnings.warn`` and
  passes through; ``"raise"`` raises
  :class:`flowing.errors.OutputValidationError`, which propagates through the
  turn's error channel (``on_provider_error`` dispatches as usual and the turn
  ends with status ``"error"``; the Agent survives).
* :func:`use_validate_enqueued_message` — inbound-message validation. Before
  a message is enqueued (``on_enqueue``), the handler filters by ``kinds`` /
  ``source`` / ``match`` (AND conjunction; messages that fail any dimension
  pass through unchecked), then validates the joined text of all
  ``TextBlock`` blocks and the ``data`` of every ``StructBlock`` directly
  against the Pydantic model. Failures are handled per ``on_fail``:
  ``"reject"`` (default) raises :class:`flowing.errors.Intercepted` so the
  message is not enqueued; ``"warn"`` emits an English ``warnings.warn`` and
  passes through; ``"raise"`` raises
  :class:`flowing.errors.InputValidationError`, which propagates verbatim to
  the enqueueing caller (dispatch does not catch ordinary exceptions, and
  enqueueing usually happens outside a turn — there is no turn error channel
  and no ``on_provider_error`` fallback).

Both functions are shipped with the ``flowing`` package but are not enabled
by default: the Agent developer must call them explicitly in ``setup()``.
An Agent that does not call them holds no validation handler or state.

What counts as conforming and what happens on failure are policies: the
validation model, the filter dimensions, and the three-state handling are a
default implementation that can be replaced as a whole. The replacement path
is the same as ``use_retry``: ``remove_by_owner(by)`` removes the default
handler, then register your own.

.. rubric:: Registration surface (use_validate_turn_result)

- Enablement: phase two only — call ``use_validate_turn_result(self, ...)``
  in ``setup()`` (recovery runs ``setup()`` on a new instance, so
  registrations do not accumulate). Zero overhead when not enabled: no
  handler owned by this function on the ``after_provider_gen`` chain.
- Registered resources: no provide key, no tool registration, no Agent state
  key, no prompt-block changes. The schema falls back to the Agent's
  ``output`` attribute (read-only probe; a top-level ``output:`` declaration
  in the Agent's ``.fya`` or an instance attribute both work), and the
  template to the Agent's ``validate_output_error`` attribute (also
  read-only).
- Declared hook points: none (pure attachment onto core hook points).
- Attached hooks: one ``after_provider_gen["_turn"]`` handler (pattern
  registration on ``match_on="by"``; the literal ``"_turn"`` matches main-turn
  responses only; ``by`` defaults to ``"validate-turn-result"``).
  ``remove_by_owner(by)`` removes the whole set.
- Scope: affects only the Agent instance it is called on; repair messages are
  persisted with the turn batch, while the function itself persists no state.

.. rubric:: Registration surface (use_validate_enqueued_message)

- Enablement: phase two only — call
  ``use_validate_enqueued_message(self, ...)`` in ``setup()`` (recovery runs
  ``setup()`` on a new instance, so registrations do not accumulate). Zero
  overhead when not enabled: no handler owned by this function on the
  ``on_enqueue`` chain.
- Registered resources: no provide key, no tool registration, no Agent state
  key, no prompt-block changes. The schema falls back to the Agent's
  ``input`` attribute (read-only probe; a top-level ``input:`` declaration in
  the Agent's ``.fya`` or an instance attribute both work).
- Declared hook points: none (pure attachment onto core hook points).
- Attached hooks: one ``on_enqueue`` handler (plain registration, no
  pattern; ``by`` defaults to ``"validate-enqueued-message"``).
  ``remove_by_owner(by)`` removes the whole set.
- Scope: affects only the Agent instance it is called on; no counting, no
  retrying, no persisted state.

.. rubric:: Example

.. code-block:: yaml

    # my-agent.fya — outbound minimal enablement: the schema is read from the
    # top-level output: declaration; on failure a same-turn steer repair runs
    output:
      answer:
        type: string

    ---
    $script:
    from flowing.composables import use_validate_turn_result


    async def setup(self):
        use_validate_turn_result(self)

.. code-block:: yaml

    # peer-inbox.fya — inbound minimal enablement: the schema is read from the
    # top-level input: declaration; only peer-sourced messages (glob peer_*)
    # are validated, non-conforming ones are rejected
    input:
      question:
        type: string

    ---
    $script:
    from flowing.composables import use_validate_enqueued_message


    async def setup(self):
        use_validate_enqueued_message(self, source="peer_*")

.. rubric:: Behavior notes

- One validation measure for text: all ``TextBlock`` content is joined (the
  same measure as ``final_text`` in
  :func:`flowing.agent.build_turn_result`), parsed with ``json.loads``, and
  validated against the Pydantic model compiled at mount time;
  ``json.JSONDecodeError`` and ``pydantic.ValidationError`` both count as
  failures. The inbound direction additionally validates each
  ``StructBlock``'s ``data`` directly (already JSON-compatible; no
  ``json.loads`` round-trip).
- Same schema fallback chain: the ``schema`` argument → the Agent attribute
  of the same name (``output`` / ``input``; a top-level declaration in the
  Agent's ``.fya`` lands in the instance's ``_extra`` mapping and resolves
  through ``__getattr__``, equivalent to an instance attribute; read-only
  probe) → a built-in ``ValueError`` at mount time (a programming error,
  raised before any handler is registered). The schema is compiled once via
  :func:`flowing.params.schema_to_model` at mount and reused by the closure;
  keywords unknown to the compiler surface as
  :class:`flowing.errors.FormatError` at mount time.
- Different error channels: the outbound ``raise`` policy travels the turn's
  error channel and ``on_provider_error`` dispatches as usual; the inbound
  ``raise`` policy propagates verbatim through the ``on_enqueue`` dispatch
  chain to the enqueueing caller — dispatch does not catch ordinary
  exceptions, enqueueing usually happens outside a turn, and there is no turn
  error channel and no ``on_provider_error`` fallback.
- The two functions are independent: neither registers the other's handler,
  and both can be mounted on the same Agent. Inbound validation only gates
  enqueued messages; PROVIDER responses produced inside a turn never enter
  the queue. With the default filters (all three dimensions unset) every
  enqueued message is gated — EVENT messages, async tool results, and steer
  repair messages included. No implicit exclusion is made for other hooks or
  tools; exemptions are an explicit application-level choice via ``kinds`` /
  ``source`` / ``match``.

.. seealso::
    :func:`flowing.params.schema_to_model` — compile entry of the validation
    model. :class:`flowing.errors.OutputValidationError` — the exception of
    the outbound ``raise`` policy. :class:`flowing.errors.InputValidationError`
    — the exception of the inbound ``raise`` policy.
    :class:`flowing.errors.Intercepted` — the blocking signal of the inbound
    ``reject`` policy. :func:`flowing.composables.prompt_until.use_prompt_until`
    — the isomorphic steer continuation composable.
"""

from typing import Any, Callable, Literal

from flowing.agent import Agent
from flowing.errors import InputValidationError, Intercepted, OutputValidationError
from flowing.message import Message, MessageKind
from flowing.parsable import Parsable

__all__ = ["use_validate_turn_result", "use_validate_enqueued_message",
           "DEFAULT_VALIDATE_TEMPLATE"]

DEFAULT_VALIDATE_TEMPLATE: str
"""Default repair-prompt template (last fallback of the ``template`` chain; English runtime text).

Rendered with Jinja2 before each steering attempt with the context variables:
``error`` (validation error text), ``schema`` (the properties dict resolved at
mount time), ``attempt`` (the 1-based failure count of the current turn), and
``max_retries``.

This is part of the replaceable policy of ``use_validate_turn_result``: the
``template`` argument or the Agent's ``validate_output_error`` attribute takes
precedence.

.. seealso:: :func:`flowing.composables.validate.use_validate_turn_result`
"""


def use_validate_turn_result(
    agent: Agent,
    schema: dict[str, dict[str, Any]] | None = None,
    *,
    template: str | None = None,
    on_fail: Literal["steer", "warn", "raise"] = "steer",
    max_retries: int = 3,
    by: str = "validate-turn-result",
) -> None:
    """Install an optional final-reply JSON Schema validation policy on one Agent.

    The handler runs on the ``after_provider_gen`` hook point filtered to
    main-turn final responses. When the joined text of the response message
    fails validation, the configured policy applies: ``"steer"`` (default)
    continues the same turn with a repair request, ``"warn"`` warns and passes
    through, and ``"raise"`` raises
    :class:`flowing.errors.OutputValidationError`.

    :param agent: Initialized Agent instance to configure. The usual call site
        is the Agent's ``setup()`` method.
    :param schema: JSON Schema in properties form (field name to property
        dict). When omitted, the Agent's ``output`` attribute is read — a
        top-level ``output:`` declaration in the Agent's ``.fya`` or an
        instance attribute both land there; when neither is available a
        built-in ``ValueError`` is raised at mount time.
    :param template: Jinja2 repair-prompt template. When omitted, the Agent's
        ``validate_output_error`` attribute is read, then
        :data:`DEFAULT_VALIDATE_TEMPLATE` applies.
    :param on_fail: Failure policy: ``"steer"`` (default, repair within the
        same turn), ``"warn"`` (warn and pass through), or ``"raise"`` (raise
        :class:`flowing.errors.OutputValidationError`).
    :param max_retries: Maximum number of same-turn repair attempts under the
        ``steer`` policy, ``>= 0``, default 3. The count starts at 1: a
        failure steers only while ``attempt <= max_retries``; ``0`` gives up
        on the first failure. Unused under ``warn`` / ``raise``.
    :param by: Owner identifier of the handler (lowercase-with-hyphens
        convention), default ``"validate-turn-result"``. ``remove_by_owner(by)``
        removes the whole set; the repair message's ``source`` and ``tags``
        also take this value.
    :return: ``None``.
    :raises ValueError: ``on_fail`` is not one of ``{"steer", "warn", "raise"}``,
        ``max_retries < 0``, or neither the argument nor ``agent.output``
        provides a schema — all raised before any handler is registered
        (built-in ``ValueError``, a programming error).
    :raises flowing.errors.FormatError: The schema contains keywords unknown
        to :func:`flowing.params.schema_to_model` (surfaced while compiling
        the model at mount time).

    .. rubric:: Behavior

    - Only main-turn final responses are checked: pattern registration
      filters the source to ``by == "_turn"``, and the handler additionally
      requires ``finish=True``, a non-``None`` message, no ``ToolCallBlock``,
      and ``current_turn.finish_output is None``. Everything else passes
      through unchanged.
    - The checked text is the joined text of all ``TextBlock`` blocks in the
      response message, the same measure as ``final_text`` in
      :func:`flowing.agent.build_turn_result`. ``json.JSONDecodeError`` and
      ``pydantic.ValidationError`` both count as validation failures.
    - Under ``steer``, the failed response gets ``finish = False`` and the
      repair message (``kind=EVENT`` / ``priority=STEER`` / ``source=by`` /
      ``tags=[by]``) is queued via ``Agent.steer``; the inner loop drains it
      before the next ``provider_gen`` call, so the repair request enters the
      same turn's context. The re-emitted response is validated again by the
      same handler until it passes or the budget is exhausted.
    - The failure counter is internal closure state of the mounting call; it
      is not persisted and not snapshotted. It counts per turn: a pass or a
      give-up resets it to 0, so a failure in a later turn counts from 1
      again.
    - Repeated calls do not deduplicate: each call installs an independent
      handler (with its own counter closure and compiled model). Remove the
      whole set with
      ``agent.hooks.after_provider_gen.remove_by_owner(by)``.
    - The finish structured hand-in path is skipped automatically: when
      ``finish_output`` is set, no text validation runs. The finish tool's
      ``.fya`` ``output:`` declaration and the ``agent.output`` attribute are
      independent declaration channels the framework does not cross-check.

    .. seealso::
        :func:`flowing.params.schema_to_model` — compile entry of the
        validation model.
        :meth:`flowing.agent.Agent.steer` — the queueing channel of repair
        messages.
        :class:`flowing.errors.OutputValidationError` — the exception type of
        the ``raise`` policy.
    """
    ...


def use_validate_enqueued_message(
    agent: Agent,
    schema: dict[str, dict[str, Any]] | None = None,
    *,
    on_fail: Literal["reject", "warn", "raise"] = "reject",
    kinds: tuple[MessageKind, ...] | None = None,
    source: str | list[str] | None = None,
    match: Callable[[Message], bool] | None = None,
    by: str = "validate-enqueued-message",
) -> None:
    """Install an optional inbound-message JSON Schema validation policy on one Agent.

    The handler runs on the ``on_enqueue`` hook point (plain registration,
    owned by ``by``). Before a message is enqueued, it is filtered by
    ``kinds`` / ``source`` / ``match`` (AND conjunction — only messages that
    pass every dimension are validated; everything else passes through
    unchanged). Validated messages are checked with ``json.loads`` plus the
    Pydantic model compiled at mount time; on failure the configured policy
    applies: ``"reject"`` (default) raises
    :class:`flowing.errors.Intercepted` so the message is not enqueued,
    ``"warn"`` emits an English warning and passes through, and ``"raise"``
    raises :class:`flowing.errors.InputValidationError`.

    The validation model is compiled once at mount and reused by the closure.
    The function keeps no counter, retries nothing, and persists no state. It
    is the phase-two entry of two-phase enablement and can only be called on
    an initialized instance from ``setup()`` (or any code while the instance
    is alive).

    :param agent: Initialized Agent instance to configure. The usual call site
        is the Agent's ``setup()`` method.
    :param schema: JSON Schema in properties form (field name to property
        dict). When omitted, the Agent's ``input`` attribute is read — a
        top-level ``input:`` declaration in the Agent's ``.fya`` or an
        instance attribute both land there; when neither is available a
        built-in ``ValueError`` is raised at mount time.
    :param on_fail: Failure policy: ``"reject"`` (default, raise
        :class:`flowing.errors.Intercepted` so the message is not enqueued),
        ``"warn"`` (English warning, then pass through), or ``"raise"`` (raise
        :class:`flowing.errors.InputValidationError`).
    :param kinds: Kind filter: ``None`` (default) disables this dimension, so
        messages of every kind are gated — EVENT messages, async tool results,
        and steer repair messages included. Pass a tuple to validate only
        those kinds; an explicit empty tuple is a programming error and raises
        ``ValueError`` at mount time.
    :param source: Source filter: ``None`` (default) disables this dimension;
        a ``str`` or a list of ``str`` is matched against the message's
        ``source`` field with ``fnmatch`` — matching any one pattern is
        enough.
    :param match: Custom predicate ``Callable[[Message], bool]``; when given,
      ``match(msg)`` must be true for the message to be validated.
    :param by: Owner identifier of the handler (lowercase-with-hyphens
        convention), default ``"validate-enqueued-message"``.
        ``remove_by_owner(by)`` removes the whole set.
    :return: ``None``.
    :raises ValueError: ``on_fail`` is not one of ``{"reject", "warn", "raise"}``,
        ``kinds`` is empty, or neither the argument nor ``agent.input``
        provides a schema — all raised before any handler is registered
        (built-in ``ValueError``, a programming error).
    :raises flowing.errors.FormatError: The schema contains keywords unknown
        to :func:`flowing.params.schema_to_model` (surfaced while compiling
        the model at mount time).

    .. rubric:: Behavior

    - Filtering is an AND conjunction: when ``kinds`` is given, ``msg.kind``
      must be in the tuple; when ``source`` is given it is normalized to a
      pattern tuple and the message passes only when ``fnmatch`` matches its
      ``source`` against any one pattern (``source=""`` matches only messages
      with an unset ``source`` — the default for externally delivered
      messages); when ``match`` is given, ``match(msg)`` must be true. Any
      failed dimension returns the message unchanged, with no validation.
      With all three dimensions unset (the default), every enqueued message is
      gated: no implicit exclusion is made for other hooks or tools, and
      exemptions are an explicit application-level choice.
    - Checked payload: all ``TextBlock`` content joined into text (the same
      measure as ``final_text`` in
      :func:`flowing.agent.build_turn_result`), parsed with ``json.loads``
      then validated; each ``StructBlock``'s ``data`` is validated directly
      via ``model.model_validate`` (already JSON-compatible; no
      ``json.loads`` round-trip). ``json.JSONDecodeError`` and
      ``pydantic.ValidationError`` both count as failures, and the first
      error text is collected. Messages without ``TextBlock`` /
      ``StructBlock`` payload (pure media, for example) pass unchecked.
    - Handling: ``warn`` emits an English ``warnings.warn`` (including the
      error text) and returns the message unchanged; ``raise`` raises
      :class:`flowing.errors.InputValidationError` (whose ``error`` field
      carries the validation error text) — the exception propagates verbatim
      to the enqueueing caller: dispatch does not catch ordinary exceptions,
      enqueueing usually happens outside a turn, and there is no turn error
      channel and no ``on_provider_error`` fallback; ``reject`` (default)
      raises :class:`flowing.errors.Intercepted` with an English reason, so
      the message is not enqueued and the queue is unchanged.
    - Schema fallback chain: the ``schema`` argument → the Agent's ``input``
      attribute (a top-level ``input:`` declaration in the Agent's ``.fya``
      lands in the instance's ``_extra`` mapping and resolves through
      ``__getattr__``, equivalent to an instance attribute; read-only probe)
      → a built-in ``ValueError`` at mount time (fail-fast); keywords unknown
      to :func:`flowing.params.schema_to_model` surface as
      :class:`flowing.errors.FormatError` at mount time.
    - Repeated calls do not deduplicate: each call installs an independent
      handler (with its own compiled model). Remove the whole set with
      ``agent.hooks.on_enqueue.remove_by_owner(by)``.
    - Relationship to framework-internal messages: PROVIDER responses
      produced inside a turn never enter the queue and never pass through
      ``on_enqueue``; async tool results and steer repair messages do pass
      through ``on_enqueue`` (EVENT kind) and are therefore gated by default
      as well. No implicit exclusion is made for other hooks or tools: when
      ``use_validate_turn_result`` is mounted on the same Agent, its steer
      repair messages are also subject to this validation, and an exemption
      must be given explicitly at mount time (a ``kinds`` / ``source`` /
      ``match`` filter).

    .. seealso::
        :func:`flowing.params.schema_to_model` — compile entry of the
        validation model.
        :class:`flowing.errors.InputValidationError` — the exception type of
        the ``raise`` policy.
        :class:`flowing.errors.Intercepted` — the blocking signal of the
        ``reject`` policy.
    """
    ...
