"""The Agent object model, message tree, and logical-turn execution.

.. rubric:: Overview

This module defines Flowing's central :class:`Agent` type and the value
objects used by turn execution, hooks, and background work. It also provides
the Agent-level message-tree operations and tool/subagent invocation APIs.
Subagent binding entries and results are defined in ``flowing.subagents``;
persistent state views are defined in ``flowing.persistence``; hook-point
contracts are defined in ``flowing.hooks``.

Flowing provides mechanisms rather than application policies. The core
classifies errors, dispatches hooks, moves messages, persists state, and
coordinates cancellation. Retry, compaction, approval, and similar policies
belong to extensions or application code.

.. rubric:: Global behavior

- Create Agents with ``Runtime.create_agent`` and recover them with
  ``Runtime.recover_agent``. Do not call the Agent constructor to create a
  usable instance. Both pipelines call ``setup`` once on a fresh instance.
  An Agent has its own message queue, work-loop task, message tree, hook
  registry, tool and subagent bindings, and position in the provide-inject
  chain.
- A message tree is a tree of ``Message`` objects linked by ``id`` and
  ``parent_id``. ``current_head_id`` points to a message ID, or is ``None``
  when the active context has no head. ``fork`` moves this cursor; it does not
  create or copy a node. Tree edits are performed through the Agent's message
  chain.
- A turn is a logical execution phase, not a persisted object. Its
  ``TurnContext`` is not inserted into the message tree or written to disk and
  is not restored after a crash. Recovery replays persisted messages and
  state; it does not resume an interrupted turn.
- Each Agent has a session directory containing its persisted message tree,
  core state, application state, and metadata. Persistence uses a
  write-behind store: a successful enqueue for persistence does not mean that
  bytes have already been written. Normal destruction drains and closes the
  store before returning.
- Public asynchronous methods that dispatch hooks must be awaited. This
  includes message submission, turn execution, provider calls, cancellation,
  destruction, and subagent invocation. Operations that do not dispatch hooks,
  such as ``pause``, ``resume``, ``provide``, ``inject``, ``watch``, and
  ``snapshot``, are synchronous.
- ``provide`` registers a value at this Agent. ``inject`` searches the current
  Agent and then each parent up to the Runtime. A later value for the same key
  replaces the earlier value, and lookups are not cached. Credentials and
  other secrets must not be placed in messages, model context, or persisted
  state; use provide-inject for runtime values that must not be persisted.
- Hooks are instance-local. ``watch`` observes instance-attribute
  assignments asynchronously and does not block or alter the assignment.
- Awaiting ``query`` from inside the current turn's hook, tool, or provider
  call deadlocks: the current turn cannot finish until the caller returns. A
  cycle of Agents waiting on one another has the same problem and is not
  detected by the framework. Use ``steer`` to add direction that the current
  turn can consume.
- Agent does not expose a lifecycle ``status`` field. Use ``snapshot`` to
  observe the current derived state.

.. rubric:: Usage example

The declarative ``.fya`` form and the Python subclass form describe the same
Agent shape. This Python example fixes one language intent throughout: setup
uses an English locale, the system prompt requests English, and the query is
in English.

.. code-block:: python

    from flowing import Agent, Runtime, on
    from flowing.parsable import Parsable

    class OrderAgent(Agent):
        system_prompt = Parsable(
            "You are an order assistant. "
            "When locale is en-US, reply in English."
        )

        @on("before_tool_call")
        def add_language(self, tool_call):
            tool_call.args["lang"] = self.locale
            return tool_call

        async def setup(self, locale: str = "en-US"):
            self.locale = locale
            self.add_tool("make-payment", alias="pay")

    async def configure(runtime: Runtime) -> None:
        runtime.register_agent_type(OrderAgent)
        agent = await runtime.create_agent("order-agent", locale="en-US")
        result = await agent.query("Look up order 4521")
        print(result.status, result.final_text)

.. seealso::

    - ``flowing.runtime.Runtime`` for creation and recovery
    - ``flowing.message.Message`` and ``flowing.message.MessageChain`` for the
      message tree
    - ``flowing.hooks.HookRegistry`` for hook registration and dispatch
    - ``flowing.model`` for model and provider call values
"""

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal, TypeVar, overload

from flowing.context import Context, ContextUsageEstimate
from flowing.message import ContentBlock, Message, MessageKind, MessagePriority
from flowing.model import ModelConfig
from flowing.params import InjectionKey
from flowing.parsable import Parsable
from flowing.parser import EntryRef
from flowing.persistence import StateView
from flowing.providers import ProviderResponse, Usage
from flowing.snapshot import AgentSnapshot
from flowing.subagents import SubagentEntry, SubagentResult
from flowing.tool import Tool, ToolCall, ToolEntry, ToolResult

T = TypeVar("T")
WatchHandler = Callable[[Any, Any], None]
"""Callback type for ``watch``: ``(new_value, old_value) -> None``. Its return
value is ignored.
"""


class TurnContext:
    """Transient data passed to the hooks for one logical turn.

    .. rubric:: Overview

    A logical turn consumes one message, or multiple messages when the
    dequeue policy is overridden, and continues until the Provider returns
    ``finish=True`` or a hook requests completion on this object. The turn
    runner creates this value when the turn starts and discards it after
    finalization. It is exposed as ``TurnResult.turn``
    and ``Agent.current_turn``. It is temporary: it is not added to the message
    tree, persisted, or restored after a process crash.

    .. rubric:: Usage example

    A ``before_turn`` handler can append a message to the pending batch. It is
    attached after the message that triggered the turn and persisted with the
    batch.

    .. code-block:: python

        async def add_reminder(agent, turn):
            turn.pending_messages.append(
                Message(
                    kind=MessageKind.EVENT,
                    source="reminder",
                    content=[TextBlock(text="Please verify the amount first.")],
                )
            )
            return turn

        agent.hooks.before_turn(add_reminder)

    An ``after_tool_call`` handler can request completion without a result
    payload:

    .. code-block:: python

        def finish_after_peer(agent, result):
            if result.status == "completed" and agent.current_turn is not None:
                agent.current_turn.finish = True
            return result

        agent.hooks.after_tool_call["message-peer"](finish_after_peer)

    .. rubric:: Behavior notes

    - ``message_ids`` contains IDs of messages in the message tree, appended
      in production order. It contains no message copies. The first ID is the
      first message of the turn; use it as a logical turn identifier when
      needed because this object has no separate ID.
    - ``pending_messages`` is intended for use while ``before_turn`` handlers
      run. Appending injects messages after the triggering message. Clearing
      the list leaves an empty turn. The list is cleared after the batch is
      attached to the tree; changes made later have no effect. If
      ``before_turn`` is blocked by ``Intercepted``, the pending batch is
      discarded without being persisted.
    - ``aborted`` is set by the abort path, including ``abort_turn``,
      cancellation, parent cancellation, or a hook setting the flag. Setting
      it repeatedly has no additional effect, and ``on_turn_abort`` runs at
      most once per turn. ``after_turn`` handlers can inspect it to distinguish
      normal completion from cancellation.
    - ``finish`` requests normal completion without a return payload. A tool
      hook can set it to ``True``; the current tool batch completes before the
      turn ends. It does not change the value written to ``Agent.last_result``.
    - ``usages`` accumulates the ``Usage`` objects reported by successful
      ``provider_gen`` calls in this turn. The entries are the same objects
      attached to their messages, not copies; ``Message.usage`` remains the
      authoritative per-message value. The turn result aggregates these
      values, after which this temporary list is discarded. Usage from
      ``side_query`` is not included.
    - ``finish_output`` contains the structured payload returned by the finish
      tool. Setting it requests natural turn completion after the current
      tool batch finishes, as if the Provider had returned ``finish=True``.
      Finalization stores the payload in ``Agent.last_result``. Set ``finish``
      when completion needs no payload. This field is transient and is not
      persisted.
    - An empty turn, such as one aborted immediately after starting, creates
      no new tree node. ``message_ids`` may contain only the triggering
      message or may be empty, and ``current_head_id`` does not change.

    .. seealso:: :class:`TurnResult` and :meth:`Agent.query`
    """

    started_at: datetime
    """Timestamp when this logical turn began, for monitoring and duration
    measurements.
    """
    finished_at: datetime | None
    """Turn-finalization timestamp. This is ``None`` while the turn is active;
    finalization sets it. The snapshot projection is available only during
    execution, so its ``finished_at`` is always ``None``; use
    ``TurnResult.turn.finished_at`` for the completed value.
    """
    message_ids: list[str]
    """IDs of messages added to the message tree during this turn, appended in
    production order. The first ID is the ID of the turn's first message.
    """
    aborted: bool
    """Abort flag. Setting it is idempotent, and ``on_turn_abort`` fires at
    most once for each turn.
    """
    pending_messages: list[Message]
    """Initially contains the dequeued messages for this turn, including the
    message that triggered it. A ``before_turn`` handler can append messages;
    those messages are added after the triggering batch and persisted with it.
    The list is readable and writable while ``before_turn`` handlers run, then
    cleared after the batch is attached to the tree. If a handler raises
    ``Intercepted``, the entire pending batch is discarded without persistence.
    """
    usages: list[Usage]
    """Usage objects reported by successful ``provider_gen`` calls in this
    turn. The list contains the same ``Usage`` objects attached to messages,
    not copies. It is an in-memory accumulator, is not persisted, and is
    aggregated into ``TurnResult.token_usage`` by finalization.
    Calls through ``side_query`` are excluded.
    """
    finish_output: dict[str, Any] | None
    """Structured payload returned by the finish tool. Setting this field
    requests natural turn completion after the current tool batch finishes,
    as if the Provider returned ``finish=True``. Finalization stores the
    payload in ``last_result``. This field is transient and is not persisted.
    """
    finish: bool
    """Requests normal completion without a return payload. The current tool
    batch completes before the turn ends; this flag does not change the value
    written to ``Agent.last_result``.
    """


class TurnResult:
    """The result returned to callers waiting for a logical turn.

    .. rubric:: Overview

    ``build_turn_result`` assembles this value after ``after_turn`` handlers
    finish. All callers waiting on messages consumed by the same turn receive
    the same result object. Normal completion, hook blocking, cancellation,
    and an uncaught error all resolve the waiting callers; they do not remain
    suspended indefinitely.

    .. rubric:: Usage example

    .. code-block:: python

        result = await agent.query("Look up order 4521")
        if result.status == "completed":
            print(result.final_text)
            print(result.token_usage)

    .. rubric:: Behavior notes

    - ``status`` is ``"completed"`` when a Provider response ends the turn or
      ``TurnContext.finish`` / ``finish_output`` requests completion,
      ``"blocked"`` when the turn runner intercepts the turn, ``"cancelled"`` when
      the turn is cancelled, destroyed, or withdrawn through ``cancel_queued``,
      and ``"error"`` when an uncaught exception terminates the turn. All four
      outcomes resolve the waiters. An ``on_enqueue`` rejection occurs before
      a turn exists and propagates from the submission method instead.
    - ``final_text`` concatenates the text of all ``TextBlock`` values in the
      last Provider message of the turn. It is an empty string if the turn has
      no Provider message, such as when it is aborted before the first
      ``provider_gen`` call.
    - ``token_usage`` aggregates the reported usage fields from successful
      ``provider_gen`` calls in this turn, except that ``raw`` data is not
      aggregated. It is ``None`` only when no successful call reported usage;
      a reported total of zero is not the same as missing usage. If only some
      calls report usage, only those calls contribute.
    - ``finish_reason`` is informational. It contains the raw stop reason,
      such as ``"end_turn"``, ``"cancelled"``, or ``"intercepted"``; it does
      not control execution. The Provider's ``ProviderResponse.finish`` field
      is the turn-ending signal and is distinct from this field.
    - This result is not persisted. Its ``turn`` value is transient and is
      discarded when the turn ends, although the message IDs it contains can
      be used to locate persisted tree nodes.

    .. seealso:: :meth:`Agent.query`, :func:`build_turn_result`, and
        ``flowing.providers.Usage``
    """

    turn: TurnContext
    """Transient data for the logical turn that produced this result. Its
    ``message_ids`` refer to nodes in the message tree.
    """
    final_text: str
    """Final reply text, formed from the last Provider message. It is an empty
    string when the turn produced no Provider message.
    """
    status: Literal["completed", "blocked", "error", "cancelled"]
    """Outcome of the turn. All four outcomes resolve callers waiting for the
    turn.
    """
    token_usage: Usage | None
    """Aggregated token usage for this turn, or ``None`` if no successful
    ``provider_gen`` call reported usage.
    """
    finish_reason: str
    """Informational stop reason; it does not control execution.
    """


class Execution:
    """A record for one active asynchronous execution owned by an Agent.

    .. rubric:: Overview

    Tool calls, subagent calls, Provider requests, and side queries register an
    execution while they run and remove it on completion, failure, or
    cancellation. Agent cancellation methods use active records to signal the
    work. Snapshots expose only ``kind``, ``tags``, and ``started_at``.

    .. rubric:: Behavior notes

    - ``kind`` is an open string. Built-in values include ``"tool"``,
      ``"agent"``, ``"request"``, and ``"side_query"``; extensions may use
      other values.
    - ``tags`` support grouped cancellation through ``cancel_by_tag`` and
      ``stop_by_tag``.
    - Registration and removal are paired, with removal performed even when
      execution exits exceptionally. Completed executions therefore do not
      remain available for later cancellation.
    - Cancellation is cooperative: setting the cancel signal requests that
      the execution stop. The execution may stop at a checkpoint, ignore the
      request, or finish necessary cleanup and return a partial result. The
      framework does not generally force-terminate execution. A foreground
      awaitable tool is an exception: ``Tool.__call__`` races its in-flight
      awaitable against cancellation and can inject ``CancelledError`` at an
      await point without waiting for a cooperative checkpoint.
    - The pause signal is also cooperative and reserved for Tool overrides or
      Composables. Core code does not set or clear it, and it affects only
      this execution; it does not pause an Agent's work loop or cascade to
      other executions.
    - ``AgentSnapshot.executions`` omits the cancel and pause Events. Use the
      Agent's control APIs rather than snapshot data to control work.

    .. seealso:: :meth:`Agent.cancel`, :meth:`Agent.cancel_by_tag`, and
        ``flowing.tool.Tool``
    """

    id: str
    """Numeric string from this Agent's increasing sequence; this is the key
    in ``Agent._executions``.
    """
    kind: str
    """Open string identifying the execution kind. Built-in values include
    ``"tool"``, ``"agent"``, ``"request"``, and ``"side_query"``; extensions
    may use other values.
    """
    tags: list[str]
    """Free-form tags used to group executions for ``cancel_by_tag`` and
    ``stop_by_tag``.
    """
    started_at: datetime
    """Timestamp when the execution started, for monitoring, logging, and
    duration checks.
    """
    cancel: asyncio.Event
    """Cooperative cancellation signal; it is initially unset. The execution
    decides whether and how to respond. The execution APIs and hooks use the
    term ``cancel``; turn-level interruption uses ``abort``.
    """
    pause: asyncio.Event
    """Cooperative pause signal, initially unset. It is reserved for Tool
    overrides and Composables; core code does not set or clear it, and it
    affects only this execution rather than cascading to other work.
    """


class FieldUpdate:
    """A snapshot of one instance-attribute assignment sent to watchers.

    .. rubric:: Overview

    Before ``Agent.__setattr__`` writes an instance attribute, it creates this
    value and schedules fire-and-forget notification to matching watchers.
    The watcher pattern matches ``name``; a literal pattern such as
    ``agent.watch("locale", ...)`` matches that attribute name exactly. The
    ``old`` and ``new`` values describe the assignment that triggered the
    notification even if the watcher runs later.

    .. rubric:: Behavior notes

    - Watchers observe the assignment; they cannot change it. Their return
      values are ignored. ``Intercepted`` and ordinary exceptions stop the
      current watcher chain and are logged without undoing the assignment or
      propagating to the assigning caller.
    - Watchers may be synchronous or asynchronous; the notification task
      awaits either form.
    - Notification timing and watcher execution order are not guaranteed. A
      watcher may run after the assignment completes, and watchers for
      consecutive assignments may run in either order. The snapshot values
      remain tied to the triggering assignment.
    - If no event loop is running, notification is skipped and the assignment
      still succeeds. This can occur during the synchronous initialization
      skeleton.
    - Only instance-attribute assignments are observed. Descriptors,
      class attributes, and underscore-prefixed initialization fields do not
      use this mechanism. Assigning the same watched field from its watcher
      causes recursive notifications; the framework does not prevent this.
    - For plugin-managed objects, implement attribute interception on the
      plugin-owned object's class. Rebinding the Agent's top-level slot can be
      observed with ``watch`` but cannot be intercepted without triggering
      another watcher notification.

    .. seealso:: :meth:`Agent.watch` and
        ``flowing.hooks.HookRegistry.watch``
    """

    name: str
    """Name of the assigned attribute. Watcher patterns are matched against
    this value.
    """
    old: Any
    """Previous value, or ``None`` if the attribute did not previously exist.
    """
    new: Any
    """Value about to be assigned. This is a snapshot; changing it does not
    change the assignment.
    """


class ProviderErrorContext:
    """The decision value delivered to ``on_provider_error`` handlers.

    .. rubric:: Overview

    When a Provider call raises, the turn runner constructs this value and
    dispatches ``on_provider_error``. A handler can perform policy actions,
    such as waiting before retrying, changing ``agent.model``, or calling
    ``abort_turn``, and then set ``can_continue`` to indicate whether the turn
    should issue another Provider call. The core supplies error classification
    and dispatch, not a retry policy. The optional ``use_retry`` Composable
    provides one such policy. Without a handler that opts in,
    ``can_continue`` remains false and the turn ends with ``"error"``; the
    Agent itself remains available for later messages.

    .. rubric:: Usage example

    .. code-block:: python

        from flowing.errors import RateLimitedError

        async def retry_rate_limit(agent, context):
            if isinstance(context.error, RateLimitedError):
                await asyncio.sleep(2)
                context.can_continue = True
            return context

    .. rubric:: Behavior notes

    - ``can_continue=False`` is the default. If a handler leaves it false, the
      turn ends with ``status="error"``. Setting it to true retries the
      Provider call; the handler must first perform any needed delay, model
      change, or other preparation.
    - A handler may call ``agent.abort_turn()`` and set ``can_continue``.
      The next Provider-call checkpoint observes the abort and returns a
      cancelled response, so the turn finishes with ``status="cancelled"``.
    - Provider-call exceptions, including ``ContextLengthError``, are
      dispatched through this hook. Retrying an unchanged token-limit or
      credential error is usually ineffective; handlers may instead compact
      history, select a larger-context model, or observe the error without
      retrying.
    - ``provider`` is the provider entry's name from ``agent.model``; it is
      not a Provider instance.

    .. seealso:: :meth:`Agent.provider_gen` and
        ``flowing.composables.retry``
    """

    error: Exception
    """Original exception raised by ``provider_gen``.
    """
    provider: str
    """Name of the provider entry that failed, from ``ModelConfig.provider``.
    """
    model: ModelConfig
    """The ``ModelConfig`` in effect when the error occurred.
    """
    can_continue: bool
    """Decision field. A handler sets this to ``True`` to retry within the
    turn; the default ``False`` means the turn ends with an error.
    """


class CancelContext:
    """The value delivered to the hooks around an Agent cancellation request.

    .. rubric:: Overview

    ``Agent.cancel`` dispatches ``before_cancel`` before setting any
    cancellation signals. A handler can raise ``Intercepted`` to prevent the
    request, for example while an operation must not be interrupted. After
    the signals have been set, ``after_cancel`` is dispatched as an
    observation that the request was accepted.

    .. rubric:: Behavior notes

    - ``reason`` describes the cancellation. ``Agent.cancel`` and ``stop`` do
      not accept a reason argument, so their dispatch uses the empty string.
      An extension that dispatches ``before_cancel`` itself can provide a
      custom reason.
    - If a before handler raises ``Intercepted``, cancellation is blocked and
      neither execution signals nor the turn-abort signal are set.
    - An ordinary exception from a handler propagates to the ``cancel``
      caller, and cancellation signals remain unset.

    .. seealso:: :meth:`Agent.cancel` and
        ``flowing.errors.Intercepted``
    """

    reason: str
    """Cancellation reason. Framework calls use the empty string because the
    cancel APIs take no reason argument; extensions may provide one when they
    dispatch the hook themselves.
    """


class ForkContext:
    """The value delivered to ``on_fork`` before the message-tree cursor moves.

    .. rubric:: Overview

    ``Agent.fork`` dispatches this value before validating and applying the
    target. A handler can replace ``target_message_id`` to select another
    message, or set it to ``None`` to detach the cursor. It can raise
    ``Intercepted`` to block the switch. Observers can read the previous and
    requested IDs to describe the change.

    .. rubric:: Behavior notes

    - ``previous_head_id`` is the cursor value before the switch. It is
      informational; changing it does not affect the operation.
    - ``target_message_id`` is mutable and its final value is used for both
      validation and the cursor update. ``None`` is valid and has the same
      meaning as ``fork(None)``.
    - Dispatch occurs before persistence. Blocking or failing validation
      leaves the cursor and tree unchanged. An ordinary handler exception
      propagates to the ``fork`` caller.

    .. seealso:: :meth:`Agent.fork` and
        ``flowing.errors.Intercepted``
    """

    previous_head_id: str | None
    """Value of ``current_head_id`` immediately before the switch, or ``None``
    when the tree is empty or the cursor is detached. This field is
    informational; changing it does not affect the operation.
    """
    target_message_id: str | None
    """Message ID requested as the new cursor value, or ``None`` to detach the
    cursor. A handler may change it; the changed value is used for both
    validation and the cursor update.
    """


def build_turn_result(
    turn: TurnContext,
    agent: "Agent",
    *,
    intercepted: bool = False,
    error: BaseException | None = None,
    finish_reason: str = "",
) -> TurnResult:
    """Assemble a ``TurnResult`` from the completed turn and its messages.

    The turn runner calls this after ``after_turn`` handlers. It derives the
    result text, status, usage, and stop reason from ``turn`` and the Agent's
    message tree, then gives the same result to all waiters for that turn.

    .. rubric:: Behavior notes

    - This function only reads the turn and message tree. It does not modify
      the tree, persist data, or dispatch hooks.
    - Status is selected in this order: ``turn.aborted`` gives
      ``"cancelled"``; otherwise ``intercepted=True`` gives ``"blocked"``;
      otherwise a non-``None`` ``error`` gives ``"error"``; otherwise the
      status is ``"completed"``.
    - If ``turn.usages`` is empty, ``token_usage`` is ``None``. Otherwise the
      seven numeric usage fields are summed and ``raw`` is an empty mapping;
      consumers that need per-call raw usage can observe
      ``after_provider_gen``.
    - For cancelled, blocked, or error outcomes, ``finish_reason`` is set to
      the corresponding literal. For normal completion it uses the supplied
      Provider stop reason. An empty turn uses an empty string. This value is
      informational and does not control the turn.
    - An empty ``turn.message_ids`` produces empty ``final_text`` and
      ``token_usage=None``.

    :param turn: Transient execution data for this turn.
    :param agent: Agent whose message tree supplies the turn's messages.
    :param intercepted: Whether a hook blocked the turn with ``Intercepted``.
    :param error: Uncaught exception that ended the turn, if any.
    :param finish_reason: Raw Provider stop reason for normal completion. It
        is ignored for cancelled, blocked, and error outcomes.
    :return: The assembled turn result.

    .. seealso:: :class:`TurnResult` and :meth:`Agent.query`
    """
    ...


class Agent:
    """Base class shared by declarative and Python-defined Agents.

    .. rubric:: Overview

    Each Agent instance owns its message queue and work-loop task, message
    tree, instance-local hook registry, tool and subagent bindings, and
    position in the provide-inject chain. The instance is the application
    handle; metadata is declared on its class.

    Create Agents through ``Runtime.create_agent`` and recover them through
    ``Runtime.recover_agent``. Do not call the constructor to create a usable
    Agent. Subagent and Workflow creation helpers use the same Runtime
    lifecycle. Both creation and recovery call ``setup`` once on a fresh
    instance.

    .. rubric:: Usage example

    The declarative ``.fya`` form and the Python subclass form describe the
    same Agent shape. This example consistently requests English output when
    the application supplies the ``en-US`` locale.

    .. code-block:: text

        name: order-agent
        description: "Handle order lookup, refunds, and shipment tracking."
        model_tag: default
        args:
          user_id:
            type: integer
            description: "User ID"
          order_id: str
          locale:
            type: string
            default: en-US
        subagents:
          - payment as pay
        ---
        $system_prompt:
        You are an order assistant.
        {% if locale == 'en-US' %}Reply in English.{% endif %}
        ---
        $script:
        from flowing import on

        @on('before_tool_call')
        def _(self, tool_call):
            tool_call.args['lang'] = self.locale
            return tool_call

        async def setup(self, user_id: int, order_id: str, locale: str = "en-US"):
            self.user_id = user_id
            self.order_id = order_id
            self.locale = locale

    .. code-block:: python

        from pydantic import BaseModel, Field
        from flowing import Agent, Runtime, on
        from flowing.parsable import Parsable

        class OrderArgs(BaseModel):
            user_id: int = Field(description="User ID")
            order_id: str
            locale: str = "en-US"

        class OrderAgent(Agent):
            description = "Handle order lookup, refunds, and shipment tracking."
            system_prompt = Parsable(
                "You are an order assistant. "
                "{% if locale == 'en-US' %}Reply in English.{% endif %}"
            )
            model_tag = "default"
            args_model = OrderArgs

            @on('before_tool_call')
            def _(self, tool_call):
                tool_call.args['lang'] = self.locale
                return tool_call

            async def setup(self, user_id: int, order_id: str, locale: str = "en-US"):
                self.user_id = user_id
                self.order_id = order_id
                self.locale = locale
                self.add_agent("payment", alias="pay")

        async def main() -> Runtime:
            runtime = Runtime()
            runtime.register_agent_type(OrderAgent)
            # Register PaymentAgent here if it is not provided by a plugin.
            runtime.register_agent_type(PaymentAgent)
            agent = await runtime.create_agent(
                "order-agent", user_id=42, order_id="4521"
            )
            result = await agent.query("Look up order 4521")
            return runtime

    .. rubric:: Behavior notes

    - If a ``.fya`` definition and a Python subclass have the same inferred
      name, the ``.fya`` definition takes precedence and a warning is emitted.
      The two forms produce equivalent class models and are alternative
      definitions.
    - Keep ``setup`` lightweight: assign state, register hooks, read injected
      values, and enable Composables there. Defer network requests, file I/O,
      and expensive computation to tool calls or an on-demand stage. Code
      before the first ``await`` is not interrupted by another coroutine.
    - ``self.model`` is a ``ModelConfig``. The Agent holds and passes it to
      the Provider without interpreting its fields.
    - Creation registers the Agent. Destroying a parent recursively destroys
      its live descendants and clears capability bindings, but retains session
      records for recovery. A destroyed instance must not be used.

    .. seealso:: ``flowing.runtime.Runtime.create_agent``,
        ``flowing.runtime.Runtime.recover_agent``,
        ``flowing.message.Message``, and
        ``flowing.message.MessageChain``
    """

    class_name: ClassVar[str]
    """Python class name in PascalCase. A ``.fya`` definition may set
    ``class_name`` explicitly. Otherwise the framework derives it from the
    Agent's inferred identity name, converts kebab case to PascalCase, and
    ensures that the result ends in ``Agent``. A hand-written subclass uses
    its Python ``__name__``.

    The identity name is inferred; Agent has no ``name`` mechanism field. For
    a ``.fya`` or hand-written ``.py`` file, the framework removes the
    ``.agent.fya``, ``.fya``, or ``.py`` suffix. A file named ``agent.fya`` or
    ``AGENT.fya`` uses its parent directory name. A bare registered name uses
    the registry key, while a hand-written subclass derives its identity from
    its class name (for example, ``OrderAgent`` becomes ``order-agent``).
    A declared ``name`` is only checked against the inferred value; a
    mismatch raises ``NameMismatchError`` and a match has no effect.
    """
    description: ClassVar[Parsable]
    """Optional one-line description used for parent-Agent routing decisions
    and catalog rendering. A ``Parsable`` value is resolved with the parent
    Agent as its context. Missing or ``null`` values become ``None`` and are
    treated as empty text by consumers; a plain string is also accepted and
    used as-is.
    """
    metadata: ClassVar[dict[str, Any]]
    """Static key-value metadata passed through as ``self.metadata`` for
    extensions and Composables to read. The framework does not interpret it.
    """
    system_prompt: ClassVar[Parsable]
    """Required system prompt. Instance initialization adds a lazy reference
    to ``prompt_blocks[0]`` with ``by="core"``. The prompt is resolved during
    each context assembly, so changes made in ``setup`` are visible to the
    next ``provider_gen`` call. After ``setup``, the creation pipeline raises
    ``MissingFieldError`` if this value is still ``PENDING``.
    """
    source_file: ClassVar[str | None]
    """Source path in ``@/`` form. For Python subclasses, the framework
    derives it from ``__module__.__file__`` when the class is created. The
    declarative loader supplies the ``.fya`` file's ``@/`` path explicitly,
    taking precedence over module-path inference. An explicit ``None`` or a
    failed inference produces ``None``. This value is read-only and fixed
    after instance creation.
    """
    registry_key: ClassVar[str | None]
    """Fully qualified key (``ns::name``) written when ``AgentRegistry``
    registers this class, either directly or from a file. A hand-written
    subclass that has never been registered has ``None``. This internal value
    is used when ``add_agent`` records the original type name.
    """
    subagent_catalog_template: ClassVar[str | None]
    """Agent-level override for the ``<available_subagents>`` catalog
    template. The Agent's value is used when present; otherwise the framework
    uses ``DEFAULT_SUBAGENT_CATALOG_TEMPLATE``. A same-named ``.fya`` field is
    also accepted through normal attribute lookup. The value is a Jinja2
    template string; its context is documented with the default template.
    """

    node_id: str
    """Globally unique ID. If omitted by the creation pipeline, it is
    generated from the class-level ``_id_prefix`` (``"agent"`` on ``Agent``)
    plus six random hexadecimal digits, with registry
    collision checks and retries. It cannot change during the instance's
    lifetime. Recovery reuses the original ``agent_id`` as ``node_id``.
    """
    runtime: "Runtime"
    """Reference to the Runtime at the root of this object's graph. Runtime
    shutdown traverses the graph to destroy descendants; this is an internal
    relationship rather than a user-managed reference.
    """
    child_ids: dict[str, str]
    """Read-only mapping from semantic child names to Agent IDs. The parent
    Agent uses it to find a child selected by ``invoke_subagent(resume=...)``;
    the child does not store its semantic name. Unnamed children are omitted.
    Destroying a child leaves the mapping entry intact for later resume;
    ``Runtime.archive_agent`` removes it when the child is explicitly
    forgotten. Assigning to this property raises ``AttributeError``.
    """
    current_head_id: str | None
    """Read-only cursor into the message-level tree. It contains the message
    ID to which a new message will attach, and advances when messages are
    added. ``fork`` changes this cursor without creating a message. It is
    ``None`` for an empty tree. The framework persists updates and restores
    the stored value without validating it against the tree.
    """
    current_turn: TurnContext | None
    """Transient object for the active logical turn, or ``None`` after turn
    finalization and before ``after_turn`` handlers run. ``None`` means that
    the turn is no longer active and will produce no further messages. A
    snapshot exposes it only while execution is in progress.
    """
    last_result: Any
    """Final output of the most recent logical turn: reply text or the
    structured payload returned by the finish tool. The parent Agent reads
    this value after subagent invocation to populate ``SubagentResult.result``.
    It is ``None`` before any turn has produced a result. Finalization writes
    it before resolving
    waiters, including for aborted, cancelled, and failed turns. A non-``None``
    ``turn.finish_output`` takes precedence; otherwise the value is the text
    of the last ``PROVIDER`` message in the turn. A control-only
    ``turn.finish`` request does not add a payload. If no Provider message
    exists or its text is empty, the value is ``None``.
    ``side_query`` does not update it.
    """
    model: ModelConfig
    """Mutable, resolved model configuration held by this Agent. The initial
    value is resolved from ``model_tag``. At runtime, assigning a configured
    tag replaces the model with that configuration, while assigning a
    ``ModelConfig`` directly can provide any model specification. A model
    change replaces the complete configuration rather than exposing an
    intermediate state.
    """
    args_model: ClassVar[type[Any] | None]
    """Pydantic model declaring Agent initialization parameters. It supplies
    both the schema shown by the ``subagent-invoke`` tool and runtime
    validation.

    A ``.fya`` ``args:`` JSON Schema is converted to a model and takes
    precedence. The ``setup`` signature is checked against that model: every
    setup parameter must have a corresponding field, and any type annotation
    must be compatible with the field type. A Python subclass may provide
    ``args_model`` directly. If neither form is supplied, the framework derives
    a model from ``setup`` at class creation: parameter annotations determine
    field types, defaults make fields optional, and parameters without defaults
    are required. A missing type annotation raises ``ValueError``. A
    ``setup(**kwargs)`` signature alone yields ``None``, meaning that no
    initialization parameters are accepted. The derived model is stored on
    the class and is not recomputed for each instance.
    """
    chain: Any
    """Entry point for the five message-tree operations: ``insert``,
    ``branch``, ``remove``, ``update``, and ``reparent``. Each operation
    updates the in-memory chain and appends a change record; compaction later
    rewrites the persisted representation.
    """
    hooks: Any
    """Instance-local hook registry. Its predeclared core hook points and
    declared extension points apply only to this Agent. ``@on`` declarations
    are registered during ``__init__``; registrations made through
    ``self.hooks.<point>(...)`` in ``setup`` follow them.
    """
    model_tag: str | None
    """Provider-independent model intent, such as ``"fast"``, ``"high"``, or
    ``"default"``; these are conventions, not an enum. The value may be a
    lazily evaluated Jinja2 template and is resolved again before each
    ``provider_gen`` call. Assigning a tag at runtime replaces ``model`` with
    the matching configured model. An unknown tag falls back to ``default``;
    if ``default`` is also undefined, resolution raises an error rather than
    silently selecting another model.
    """
    prompt_blocks: Any
    """Ordered blocks used to assemble the system prompt. Appending adds a
    block at the end, and insertion can place one at any index. The framework
    places a lazy ``system_prompt`` reference at index zero with ``by="core"``.
    Put dynamic content in a block template so it is resolved on each
    assembly instead of appending and removing blocks on every turn.
    """

    def __init__(self) -> None: ...

    async def setup(self, **kwargs: Any) -> None:
        """Assemble application-specific state and capabilities for this instance.

        Runtime calls this in both creation and recovery. Creation calls it
        after ``before_create`` and before pending-field validation. Recovery
        supplies persisted arguments, possibly overridden through
        ``recover_agent``, and uses the ``before_recover``/``after_recover``
        hook pair. The method signature defines setup arguments and should
        agree with ``args_model`` when one is declared.

        Core initialization belongs in the synchronous constructor. Use
        ``setup`` for application assembly that depends on arguments or
        injection, such as instance values, business hooks, and Composables.

        .. rubric:: Usage example

        .. code-block:: python

            async def setup(self, user_id: int, order_id: str,
                            locale: str = "en-US"):
                self.user_id = user_id
                self.order_id = order_id
                self.locale = locale
                self.provide("user_id", user_id)
                use_retry(self, max_retries=2)

        .. rubric:: Behavior notes

        - Every fresh instance runs setup once. Creation and recovery each
          begin with a new instance's hook registry and state view. Recovery
          replays state before setup, so setup writes are not overwritten by
          another replay afterward.
        - Typical setup work includes assigning instance values, declaring
          hook points, registering handlers, reading injected values, and
          enabling Composables. Ordinary instance attributes are runtime
          configuration and are not persisted. Register a state key through
          ``agent.state.register`` when it needs a default. Direct writes to
          the state view can persist an unregistered key; registration is not
          a schema requirement.
        - Defer network requests, file I/O, and large computations to a tool or
          on-demand stage. Code before the first ``await`` is not interrupted
          by another coroutine, so essential synchronous initialization can be
          placed there.
        - After setup returns, Runtime checks pending declarations. An
          unfilled required field, such as ``system_prompt``, raises
          ``MissingFieldError``.

        :param kwargs: Initialization values supplied by the creation or
            recovery pipeline.
        :raises flowing.errors.MissingFieldError: If required pending fields
            remain unset.
        :raises flowing.errors.UnknownHookPointError: If an ``@on`` hook
            declaration remains unresolved after setup.

        .. seealso:: ``flowing.runtime.Runtime.recover_agent`` and
            :meth:`destroy`
        """
        ...

    async def destroy(self) -> None:
        """Destroy this Agent and its live descendants while retaining their sessions.

        Destruction discards live instances; it does not delete session
        records. It resolves pending query waiters as cancelled, cancels the
        work loop and background tasks, drains and closes persistence stores,
        dispatches ``before_destroy``, recursively destroys live descendants,
        removes this instance from the Runtime's live-node table, and then
        dispatches ``after_destroy``.

        .. rubric:: Behavior notes

        - Destruction is idempotent. A repeated call on the removed instance
          returns safely.
        - Pool entries and session files remain, so a later
          ``Runtime.get_agent`` can recover the Agent. This differs from
          archiving, which also removes the pool entry.
        - Calling destroy during the ``after_turn`` observation window is
          allowed. Cancelling the work-loop task can interrupt later
          finalization, but pending query waiters are resolved as cancelled;
          an ``after_turn`` handler may be skipped if interrupted.
        - The persistence store is already closed when ``before_destroy`` is
          dispatched. State writes from that handler will not be persisted.
        - After destruction, this instance has no supported behavior; do not
          call methods such as ``inject``, ``message``, or ``tool_call`` on it.
          Its parent ID remains the historical parent assigned at creation.

        .. seealso:: ``flowing.runtime.Runtime.get_agent`` and
            ``flowing.runtime.Runtime.shutdown``
        """
        ...

    def register_state(self, name: str, backend: str = "file") -> StateView:
        """Open a named persistent state space for this Agent.

        Each space is stored in ``<session_dir>/<name>.jsonl``. It is an
        extension channel parallel to the default ``state`` view: each space
        has an independent file and persistence boundary, and is not mounted
        as an Agent attribute. The caller retains the returned ``StateView``.
        Opening the space replays persisted values immediately. Register keys
        and defaults through the returned view's ``register`` method.

        :param name: State-space name. It must be a valid filename, contain no
            path separators or ``..``, and must not be one of ``state``,
            ``core``, ``tree``, or ``meta``.
        :param backend: Persistence backend. The only supported value is
            ``"file"``.
        :return: The named ``StateView``. Repeating the same name returns the
            same view.
        :raises ValueError: If the name or backend is invalid.

        .. seealso:: :attr:`state` and ``flowing.persistence.StateView``
        """
        ...

    @property
    def state(self) -> StateView:
        """Return this Agent's default persistent state view.

        This is the standard Agent-scoped channel for hooks, tools, and
        plugins to read or write persistent values. It supports attribute
        access, such as ``agent.state.cron_jobs``, and mapping access, such as
        ``agent.state["weird-key"]``. Write-through persistence, default
        registration, and validation behavior come from
        ``flowing.persistence.StateView``. State writes do not trigger
        attribute watchers. Use ``register_state`` for additional independent
        spaces. Reading this property has no side effects.

        .. seealso:: :meth:`register_state`, :meth:`get`, :meth:`set`, and
            :meth:`delete`
        """
        ...

    def get(self, key: str, default: Any = None) -> Any:
        """Read a string key from registered state or Agent attributes.

        This is useful when the key is selected dynamically, such as in
        plugin code, a slash command, or a debugging tool. Lookup checks
        registered state first and returns its persisted value; if the key is
        absent there, it checks instance attributes, class attributes, and
        then extra values. If nothing is found, it returns ``default``.

        :param key: String key to read.
        :param default: Value returned when no state key or attribute exists.
            Defaults to ``None``.
        :return: The resolved value or ``default``.

        .. seealso:: :meth:`set`, :meth:`delete`, and :attr:`state`
        """
        ...

    def set(self, key: str, value: Any) -> None:
        """Write a string key through its matching Agent storage route.

        A registered state key is written through ``agent.state``, including
        its write-through persistence and JSON validation. Updating an
        existing extra value changes that value without an attribute
        assignment event. Any other key uses normal instance-attribute
        assignment.

        State writes do not trigger watchers; ordinary instance-attribute
        assignments follow the normal ``__setattr__`` watcher behavior.

        :param key: String key to write.
        :param value: Value to store.

        .. seealso:: :meth:`get` and :meth:`delete`
        """
        ...

    def delete(self, key: str) -> None:
        """Delete a string key from state or Agent attributes.

        Deleting a registered state key removes its persisted value; a later
        read raises the state view's missing-key error rather than restoring
        the registration default. Deleting an extra value removes that entry.
        Otherwise the method deletes an instance attribute. State deletion is
        not an assignment event and does not notify watchers. Class attributes
        and methods cannot be deleted through this route.

        :param key: String key to delete.
        :raises AttributeError: If the key is neither a state key, an extra
            value, nor a deletable instance attribute.

        .. seealso:: :meth:`get` and :meth:`set`
        """
        ...

    async def query(
        self,
        content: str | list[ContentBlock],
        kind: MessageKind = MessageKind.USER,
        **kwargs: Any,
    ) -> "TurnResult":
        """Enqueue one message and wait for the turn that consumes it.

        A string is packaged as one ``TextBlock``; a list is used directly as
        the content-block list. ``kind`` defaults to ``USER``. Additional
        keyword arguments are passed to ``Message`` construction, such as
        ``source``, ``priority``, and ``tags``.

        .. code-block:: python

            result = await agent.query("Look up order 4521")
            if result.status == "completed":
                print(result.final_text)

        The returned ``TurnResult`` represents the logical turn containing
        this message. If several messages are dequeued together, their waiters
        share one result. All four outcomes—completed, blocked, cancelled,
        and error—resolve the waiter.

        Awaiting ``query`` from within the current turn's hook, tool, or
        Provider call deadlocks because the current turn must finish before
        the next can start. A cycle of Agents waiting on one another also
        deadlocks and is not detected. Ordinary non-STEER messages queued with
        ``enqueue_message`` or ``message`` are not visible within the current
        turn; they are consumed only between turns. Use ``steer`` to add input
        visible to the current turn. Cancelling the caller's await does not
        cancel the turn; it only stops waiting for its result. Use
        ``cancel_queued`` to withdraw a message that has not been dequeued. The
        Agent must not have been destroyed.

        :param content: Text or a list of content blocks.
        :param kind: Message kind. Defaults to ``MessageKind.USER``.
        :param kwargs: Additional fields passed to ``Message`` construction.
        :return: The result for the turn that includes this message.

        .. seealso:: :meth:`message`, :meth:`enqueue_message`,
            :meth:`cancel_queued`, and :meth:`side_query`
        """
        ...

    async def message(
        self,
        content: str | list[ContentBlock],
        kind: MessageKind = MessageKind.USER,
        **kwargs: Any,
    ) -> str:
        """Enqueue a message without waiting for a turn result.

        This convenience method packages text or content blocks into a
        ``Message`` and delegates to ``enqueue_message``. A string becomes a
        single ``TextBlock``; a list is used as the content-block list.
        ``kind`` defaults to ``USER``, and additional keyword arguments are
        passed to ``Message`` construction.

        .. code-block:: python

            message_id = await agent.message("Remind me to drink water later")
            result = await agent.query("Look up order 4521")

        This is fire-and-forget: it does not register a result waiter and does
        not return a ``TurnResult``. The returned message ID can be passed to
        ``cancel_queued`` if the message has not yet been dequeued. Use
        ``query`` when the caller needs the turn result. The enqueue hook runs
        as it does for ``enqueue_message``; ``Intercepted`` propagates to the
        caller.

        :param content: Text or a list of content blocks.
        :param kind: Message kind. Defaults to ``MessageKind.USER``.
        :param kwargs: Additional fields passed to ``Message`` construction.
        :return: The ID of the enqueued message.

        .. seealso:: :meth:`query`, :meth:`steer`, and
            :meth:`enqueue_message`
        """
        ...

    async def steer(self, content: str | list[ContentBlock], **kwargs: Any) -> str:
        """Enqueue a ``STEER`` message without waiting for a response.

        Use this to add direction, constraints, corrections, or information
        while an Agent is working. ``steer`` fixes the message priority to
        ``MessagePriority.STEER`` and returns the new message ID.

        .. code-block:: python

            message_id = await agent.steer(
                "The budget is now 500; do not place the order yet."
            )

        A steer message may be included in the next dequeued batch or drained
        by the current turn before its next Provider call. It does not
        interrupt the current turn. If the turn has already finished, the
        next turn consumes it. The method does not wait for a turn result.

        :param content: Text or content blocks for the steer message.
        :param kwargs: Additional message fields.
        :return: The ID of the enqueued message.

        .. seealso:: :meth:`message` and :meth:`query`
        """
        ...

    async def enqueue_message(self, msg: Message) -> str:
        """Enqueue an already-constructed message and return its ID.

        This is the common delivery path for remote users, third-party code,
        scheduled jobs, and asynchronous tool results. The caller constructs
        the ``Message``; use ``query`` or ``message`` when automatic packaging
        is preferred.

        .. code-block:: python

            message_id = await agent.enqueue_message(
                Message(
                    kind=MessageKind.EVENT,
                    source="tool_result",
                    content=[TextBlock(text="The asynchronous task finished.")],
                    priority=MessagePriority.HIGH,
                )
            )

        Before queue insertion, ``on_enqueue`` handlers can inspect or modify
        the message or raise ``Intercepted`` to reject it. Once enqueued, the
        Agent's work loop consumes it without requiring a separate wake-up
        operation. ``PROVIDER`` messages are produced inside a turn and cannot
        be enqueued. Queue priority changes consumption order but does not
        change waiter association.

        :param msg: Message to enqueue.
        :return: The message ID.
        :raises flowing.errors.Intercepted: If an ``on_enqueue`` handler
            rejects the message.

        .. seealso:: :meth:`enqueue_messages`, :meth:`query`,
            :meth:`message`, and ``flowing.message.MessageQueue``
        """
        ...

    async def enqueue_messages(
        self, msgs: Message | list[Message], **kwargs: Any
    ) -> list[str]:
        """Enqueue one message or a batch and return IDs in input order.

        Each message is processed through ``enqueue_message`` and therefore
        receives its own ``on_enqueue`` dispatch. If a handler raises
        ``Intercepted`` for one message, the exception propagates and messages
        already enqueued are not rolled back.

        :param msgs: One ``Message`` or a list of messages.
        :return: Message IDs in the same order as the input.

        .. seealso:: :meth:`enqueue_message`
        """
        ...
    def cancel_queued(self, message_id: str) -> bool:
        """Remove a message that has not yet been dequeued.

        If the message has a waiting ``query`` caller, this also resolves that
        caller with a cancelled ``TurnResult`` containing a synthetic empty
        ``TurnContext``. The caller therefore does not remain suspended.

        This is synchronous: it dispatches no hooks and does not await.

        :param message_id: ID of the queued message to withdraw.
        :return: ``True`` if the message was removed; ``False`` if it was
            already dequeued or does not exist.

        .. seealso:: :meth:`query`, :meth:`abort_turn`, and
            :meth:`set_queued_priority`
        """
        ...

    def set_queued_priority(self, message_id: str, priority: MessagePriority) -> bool:
        """Change the priority of a message that has not been dequeued.

        The queue is reordered immediately. Within the new priority band, the
        message keeps its original enqueue order. This can promote or defer a
        queued message without changing the active turn.

        .. code-block:: python

            agent.set_queued_priority(message_id, MessagePriority.HIGH)

        :param message_id: ID of the queued message.
        :param priority: New queue priority.
        :return: ``True`` if the queued message was reprioritized; ``False``
            if it was already dequeued or does not exist.

        The method is synchronous, dispatches no hooks, leaves
        ``current_turn`` unchanged, and does not modify any other message
        fields.

        .. seealso:: :meth:`cancel_queued` and :meth:`enqueue_message`
        """
        ...

    def track_background_task(self, task: asyncio.Task) -> str:
        """Register a background task and return its registry ID.

        Framework background-task paths use this method to retain and track a
        Task. When it completes, fails, or is cancelled, a completion callback
        removes its entry. The returned ID can be used to cancel or look up the
        task and may be included in a pending tool-result receipt.

        The method is synchronous. Registering the same Task twice creates
        two different IDs; callers are responsible for avoiding duplicate
        registration. The registry also keeps a strong reference to tasks
        until their completion callback runs.

        :param task: Background task to register.
        :return: Unique ID for this registration.

        .. seealso:: :meth:`cancel_background_task` and
            :meth:`cancel_all_background_tasks`
        """
        ...

    def cancel_background_task(self, task_id: str) -> bool:
        """Request cancellation of one registered background task.

        Cancellation is cooperative: the Task receives the normal
        ``task.cancel()`` request and can handle ``CancelledError`` at an
        await point to perform cleanup.

        :param task_id: ID returned by ``track_background_task``.
        :return: ``True`` if the ID was present in the registry, regardless of
            the Task's own ``cancel()`` return value; ``False`` if it was not
            registered.
        """
        ...

    def cancel_all_background_tasks(self) -> None:
        """Request cancellation of every registered background task.

        This calls ``cancel()`` on each Task but does not await them; awaiting
        unbounded work could prevent ``destroy()`` from completing. The method
        is safe to call repeatedly and does nothing when the registry is
        empty. Entries are removed by task-completion callbacks rather than by
        this method.
        """
        ...

    def get_background_tasks(self) -> dict[str, asyncio.Task]:
        """Return a shallow copy of the background-task registry.

        Mutating the returned dictionary does not change the Agent's registry.
        The values are the original Task objects and are not wrapped.

        :return: A mapping from task IDs to their Task objects.
        """
        ...

    def get_background_task(self, task_id: str) -> asyncio.Task | None:
        """Return a registered background task, or ``None`` if it is absent.

        :param task_id: ID returned by ``track_background_task``.
        :return: The Task for this ID, or ``None`` when it is not registered.
        """
        ...

    def estimate_context_tokens(self) -> ContextUsageEstimate:
        """Estimate the token size of the context that would be sent now.

        The method walks the message path from ``current_head_id`` and uses
        the nearest valid Provider-usage anchor when one exists. With an anchor,
        ``measured`` starts from ``anchor.usage.total_tokens``, including
        ``cache_read`` tokens because cached tokens still occupy the context
        window. The estimate also includes schemas for currently enabled tools
        absent from ``_measured_tool_names``, such as tools added after the
        anchor, and estimates messages after the anchor individually. Without
        an anchor, the estimate includes the system prompt, all enabled tool
        schemas, and all messages on the path. The scan is performed on demand,
        so forks and tree edits do not leave a stale anchor cache.

        .. code-block:: python

            estimate = agent.estimate_context_tokens()
            print(estimate.tokens, estimate.usage_ratio)

        This is synchronous, read-only, and has no side effects. It does not
        set a threshold, trigger compaction, emit warnings, cache a result, or
        provide a billing value. If the model has no context-window size,
        ``usage_ratio`` is ``None``; the ratio is not clamped and may exceed
        one. With an empty message path, the result is zero and
        ``measured`` is ``None``. A zero-token usage report is not a valid
        anchor.

        :return: A ``ContextUsageEstimate`` separating measured anchor usage
            from estimated additional tokens.

        .. seealso:: ``flowing.context.ContextUsageEstimate``,
            ``flowing.message.estimate_message_tokens``, and
            ``flowing.providers.Usage``
        """
        ...

    async def provider_gen(
        self, context: Context, *, stream: bool = True, by: str | None = None
    ) -> ProviderResponse:
        """Make one streaming or non-streaming Provider request.

        This is the Agent's only Provider-call entry point. It checks the
        current turn's abort signal, resolves the model, obtains the Provider,
        dispatches ``before_provider_gen``, makes the request, dispatches
        ``after_provider_gen``, and returns the resulting response.

        .. code-block:: python

            response = await agent.provider_gen(context, stream=False)

        .. rubric:: Behavior notes

        - With ``stream=True`` (the default), content deltas are accumulated
          into the response message and each delta is sent to
          ``on_provider_delta``. That hook is observational: its return value
          does not replace the Provider's delta. To change the completed
          response, use ``after_provider_gen``. With ``stream=False``, the
          complete response is delivered as one delta, using the same delta
          shape. A normally completed call emits at least one delta; a call
          cancelled while in flight may emit none and return no message.
        - ``by`` identifies the call's origin and is copied to each delta and
          the response. The main turn uses ``"_turn"`` and ``side_query`` uses
          ``"_side"``; underscore-prefixed values are reserved for the
          framework. Hooks can filter registrations by this field.
        - If cancellation interrupts a stream, accumulated content is
          returned as a partial message and retained, with ``cancelled=True``.
          No further deltas are dispatched, and the underlying stream is
          closed. Cancellation is checked between deltas and while waiting for
          the next one.
        - Exceptions are not caught or retried here. Provider errors
          propagate to the turn runner, which dispatches
          ``on_provider_error``. Usage is not aggregated here; the response's
          ``message.usage`` is passed to the hook and caller.
        - If cancellation occurs during a non-streaming request, the in-flight
          call is cancelled and the method returns a cancelled response with
          ``message=None`` instead of raising. Streaming interruption follows
          the partial-message behavior above.

        :param context: Context sent to the Provider. The before hook may
            replace it.
        :param stream: Whether to request streaming output. Defaults to
            ``True``.
        :param by: Optional origin label copied to deltas and the response.
        :return: The Provider response after ``after_provider_gen`` handlers.
        :raises Exception: Provider exceptions propagate without wrapping.

        .. seealso:: :meth:`side_query`,
            ``flowing.providers.Provider.generate``,
            ``flowing.providers.Provider.generate_stream``, and
            :class:`ProviderErrorContext`
        """
        ...

    async def side_query(
        self,
        content: str | list[ContentBlock],
        kind: MessageKind = MessageKind.USER,
        *,
        model_tag: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Run one side-channel Provider query and return its text.

        This is intended for a quick auxiliary query, such as a guardrail,
        content check, summary, or classification. It packages ``content``
        into a message like ``query``, appends that message to a temporary
        context assembled from the current message tree, makes one
        non-streaming Provider call, and returns the response text. Neither
        request nor response is added to the message tree or persisted.
        ``model_tag`` selects a model for this call without changing
        ``self.model``; if omitted, the current model is used.

        .. rubric:: Usage example

        .. code-block:: python

            label = await agent.side_query(
                "Infer the current mood and return one English word:\\n" + text,
                model_tag="fast",
            )

        .. rubric:: Behavior notes

        - The call registers an ``Execution(kind="side_query")`` and can be
          cancelled by tag or parent cancellation. If aborted, it returns an
          empty string instead of raising.
        - Context assembly uses the current message tree and includes the
          normal tool schemas. Only the temporary request/response exchange is
          omitted from the tree.
        - The return value concatenates only response ``TextBlock`` text.
          Thinking and tool-call blocks are discarded; the side query does
          not execute requested tools or pass thinking content back into later
          context. The Provider's ``finish`` flag does not drive another
          turn.
        - An empty return string does not distinguish abort from a response
          with no text blocks. The caller should handle either case.
        - There is no separate output-limit parameter. The selected model's
          ``max_output_tokens`` applies, and truncated text is still a valid
          return value.
        - This is not a logical turn: it does not dispatch turn hooks, move
          ``current_head_id``, change pending query waiters, or set
          ``last_result``. It is always non-streaming, and its usage is not
          included in ``TurnResult.token_usage``. The Provider hooks still
          run, so ``after_provider_gen`` can observe its usage.
        - Provider exceptions propagate directly to the caller. There is no
          turn-level ``on_provider_error`` recovery path for this call.

        :param content: Text or content blocks to add to the temporary context.
        :param kind: Message kind for this temporary request.
        :param model_tag: Optional model tag to resolve for this call only.
        :param kwargs: Additional Message fields, as with ``query``.
        :return: Concatenated text from response text blocks, or ``""`` when
            no text is returned or the call is aborted.
        :raises Exception: Provider exceptions propagate without wrapping.

        .. seealso:: :meth:`query` and :meth:`provider_gen`
        """
        ...

    def remove(self, message_id: str) -> None:
        """Remove a message and reconnect its direct children to its parent.

        If the removed message is the current head, the head moves to that
        message's parent. If it has no parent, the head becomes ``None`` and a
        later append starts a new root. Removing any other message leaves the
        head unchanged.

        :param message_id: ID of the message to remove.
        :raises KeyError: If the message ID is not in the tree.

        .. seealso:: :meth:`pop` and ``flowing.message.MessageChain.remove``
        """
        ...

    def pop(self) -> str | None:
        """Remove the current head and move the head to its parent.

        Repeated calls remove the active path from newest to oldest. Once the
        tree is empty, the next pushed message becomes a new root.

        :return: The removed message ID, or ``None`` if the tree has no head.

        .. seealso:: :meth:`remove` and :meth:`push`
        """
        ...

    def push(self, msg: Message) -> str:
        """Append a message below the current head and move the head to it.

        This is the message-tree append operation. The message is linked below
        ``current_head_id``, added to the tree, and persisted before the head
        advances. When the head is ``None``, the message becomes a new root.
        This method does not dispatch turn hooks or add the ID to
        ``TurnContext.message_ids``; the turn runner handles those concerns.

        :param msg: New message to attach.
        :return: ``msg.id``.
        :raises ValueError: If ``msg.id`` already exists in the tree.

        .. seealso:: :meth:`pop` and :meth:`branch`
        """
        ...

    def branch(self, msg: Message, parent_id: str | None = None) -> str:
        """Attach a message below a selected parent without moving the head.

        Passing ``None`` creates a new root. A non-``None`` parent ID must
        already exist. The message is added and persisted, but
        ``current_head_id`` is unchanged; use ``fork`` to move the active
        cursor to it.

        :param msg: New message to attach.
        :param parent_id: Existing parent message ID, or ``None`` for a new
            root.
        :return: ``msg.id``.

        .. seealso:: ``flowing.message.MessageChain.branch`` and
            :meth:`fork`
        """
        ...

    def remove_by_tags(self, tags: set[str]) -> int:
        """Remove messages selected by tags while preserving a valid head.

        Matching and the number of removed messages follow
        ``MessageChain.remove_by_tags``. If the current head is removed, this
        method moves it to the first surviving ancestor in the original head
        path, or to ``None`` if that path has no surviving ancestor. This
        avoids leaving the head on a message removed by the same batch.

        The method is synchronous and dispatches no hooks.

        :param tags: Tags used to select messages for removal.
        :return: Number of removed messages.

        .. seealso:: :meth:`remove` and
            ``flowing.message.MessageChain.remove_by_tags``
        """
        ...

    async def fork(self, target_message_id: str | None) -> None:
        """Move the message-tree cursor to a message or detach it.

        This core message-level operation changes the active context path
        without creating another Agent, copying a branch, or deleting old
        messages. Its sequence is: dispatch ``on_fork`` with a
        :class:`ForkContext` containing the old and requested head IDs (a
        handler may change ``target_message_id`` or raise ``Intercepted``),
        validate the final target, then persist the new ``current_head_id``.
        Passing ``None`` detaches the cursor. Context assembly then sees an
        empty path, and the next message pushed with ``parent_id=None`` starts
        a new root; the old tree remains intact. A successful change persists
        the new ``current_head_id``.

        Forking changes context, not execution. Running tools and subagents
        continue and deliver their results normally. Execution traces, child
        lifecycle trees, prompt blocks, provide values, tool bindings, and the
        message queue are not changed, copied, or frozen. Fork is always
        allowed, including during a turn; it acts like seeking the base for
        the rest of that turn, so subsequent appends and provider generation
        use the new path.

        .. rubric:: Usage example

        .. code-block:: python

            await agent.fork(target_message_id)
            await agent.fork(None)  # Keep the old tree; the next message starts a new root.

        The ``on_fork`` hook can change the requested target or raise
        ``Intercepted`` before the switch. A non-``None`` target must exist.
        Forking during a turn is allowed, but later messages in that turn are
        attached to the new path. This can make the turn's message IDs span
        branches. Do not split a Provider tool-call message from its result:
        if the call remains on the new branch while its result stays on the
        old one, context assembly raises ``UnpairedToolCallError``. Recovery
        does not repair this; the invalid branch remains invalid. Fork at a
        user message or above a Provider message, without cutting between a
        tool call and its result. This is an allowed operation, not a
        condition the framework blocks.

        For a predictable external direction change, cancel the active work,
        wait for the turn to finish (for example, await its ``query()``
        ``TurnResult``), then fork and send the new message. Forking directly
        inside a hook is appropriate when the timing is known, such as during
        compaction.

        Forking does not change the Agent type or run two branches at once. Use
        a subagent for parallel work. Compaction can use fork to switch to a
        summary branch; the decision to compact and the summary policy belong
        to an extension or application.

        Compaction can create a new SYSTEM summary root and switch the head to
        it while retaining the complete old branch. Sibling branches have no
        implied order; UI ordering should use ``Message.timestamp``.

        :param target_message_id: Existing message ID to use as the head, or
            ``None`` to detach the cursor.
        :raises ValueError: If a non-``None`` target is not in the tree.
        :raises flowing.errors.Intercepted: If an ``on_fork`` handler blocks
            the switch.

        .. seealso:: :attr:`current_head_id` and
            ``flowing.message.MessageChain``
        """
        ...

    def pause(self) -> None:
        """Pause this Agent's work loop at its next checkpoint.

        The work loop checks the pause gate before dequeueing, before a
        Provider call, and before each tool call. Pausing suspends work at a
        checkpoint rather than terminating the turn; after ``resume``, the
        work continues from that point. Queued messages remain queued.

        This method is synchronous, idempotent, and dispatches no hooks. If a
        pause and turn-abort request are both pending, the pause is observed
        first; the abort is handled after resumption. This affects only this
        Agent's work loop, not its child Agents or individual execution pause
        signals.

        .. seealso:: :meth:`resume`, :attr:`paused`,
            :meth:`abort_turn`, and :meth:`pause_recursive`
        """
        ...

    def resume(self) -> None:
        """Resume this Agent's work loop and release checkpoint waiters.

        The method is synchronous and idempotent. Once released, the work loop
        checks any pending turn-abort request. It resumes only this Agent, not
        its descendants, and does not change individual execution objects.

        .. seealso:: :meth:`pause` and :meth:`resume_recursive`
        """
        ...

    def pause_recursive(self) -> None:
        """Pause this Agent and every live descendant Agent.

        This recursively closes the work-loop gate for the lifecycle subtree,
        which is useful for operations such as pausing an entire workflow.
        It is synchronous, idempotent, and dispatches no hooks. It pauses turn
        loops only; it does not set the pause signal on individual executions.
        Unlike cancellation, it suspends work rather than requesting that it
        terminate.

        .. seealso:: :meth:`pause` and :meth:`resume_recursive`
        """
        ...

    def resume_recursive(self) -> None:
        """Resume this Agent and every live descendant Agent.

        This is the recursive counterpart to ``resume``. It is synchronous,
        idempotent, and does not change individual execution objects.

        .. seealso:: :meth:`resume` and :meth:`pause_recursive`
        """
        ...

    @property
    def paused(self) -> bool:
        """Whether this Agent's work-loop pause gate is currently closed.

        This is a read-only derived view used by snapshot and monitoring code.

        .. seealso:: :meth:`pause` and :meth:`resume`
        """
        ...

    def abort_turn(self) -> None:
        """Request that only the current logical turn exit at its next checkpoint.

        The turn runner performs finalization, dispatches abort hooks, and
        resolves waiters. The Agent remains available to consume later queued
        messages. This synchronous operation is idempotent and safe when no
        turn is active; a subsequently started turn observes the request and
        can end without creating a message-tree node. Subclasses may override
        this method to discard turn messages or perform additional cleanup.

        .. seealso:: :meth:`cancel` and :meth:`pause`
        """
        ...

    async def cancel(self) -> None:
        """Request cooperative cancellation of this Agent's active work.

        ``before_cancel`` runs first and can raise ``Intercepted`` to block
        cancellation. Otherwise the method signals active executions and the
        current turn, then dispatches ``after_cancel`` to report that the
        request was accepted. The method does not wait for the turn or
        executions to finish; waiting from within the turn could deadlock.

        Cancellation is a request, not a forced kill. Executions can stop,
        ignore the request, or finish cleanup and return partial results.
        Cancelled tools and Providers return normal cancelled/partial result
        values rather than turning cancellation into an exception. A parent
        signals its own tracked work; child Agents respond through their own
        checkpoints. Queued messages are retained for the application to
        handle.

        This method has no reason parameter. Cancelling child executions does
        not by itself abort the parent's turn.

        :raises flowing.errors.Intercepted: If a ``before_cancel`` handler
            blocks the request.

        .. seealso:: :meth:`stop`, :meth:`cancel_children`,
            :meth:`cancel_by_tag`, and :meth:`abort_turn`
        """
        ...

    def cancel_children(self) -> None:
        """Request cancellation of all active executions without aborting this turn.

        This signals every execution tracked by this Agent, but leaves its
        turn-abort flag unchanged so the Agent can continue. A parent can use
        it to stop a tool or child Agent and then issue revised work. The
        method is synchronous and dispatches no hooks.

        .. seealso:: :meth:`cancel` and :meth:`cancel_by_tag`
        """
        ...

    def cancel_by_tag(self, tag: str) -> None:
        """Request cancellation only for executions carrying the given tag.

        This does not abort the current turn. If no active execution has the
        tag, the method does nothing. It is synchronous.

        :param tag: Exact tag used to select active executions.

        .. seealso:: :attr:`Execution.tags` and :meth:`cancel_children`
        """
        ...

    async def stop(self) -> None:
        """Stop this Agent using the overridable strong-stop hook.

        The default implementation is equivalent to ``await cancel()`` and
        includes the before/after cancellation hooks. Cancellation itself is
        cooperative, so subclasses can override ``stop`` to add a
        type-specific forceful action, such as terminating a process or
        closing a connection. An override should either call the default
        logic or signal cancellation itself before applying that action.

        The core does not provide a general forced-kill implementation.

        .. seealso:: :meth:`cancel` and :meth:`stop_children`
        """
        ...

    def stop_children(self) -> None:
        """Apply the overridable strong-stop operation to this Agent's executions.

        The default implementation delegates to ``cancel_children`` and does
        not abort this Agent's turn. Subclasses can override it to add a
        type-specific forceful action. This method is synchronous.

        .. seealso:: :meth:`cancel_children` and :meth:`stop`
        """
        ...

    def stop_by_tag(self, tag: str) -> None:
        """Apply the strong-stop operation to executions with the given tag.

        The default implementation delegates to ``cancel_by_tag``. Subclasses
        may override it with type-specific termination behavior. This method
        is synchronous.

        :param tag: Exact tag used to select active executions.

        .. seealso:: :meth:`cancel_by_tag` and :meth:`stop`
        """
        ...

    async def create_subagent(
        self, agent_type: str, *, name: str | None = None, **kwargs: Any
    ) -> "Agent":
        """Create a child Agent and register it under this Agent.

        This delegates to ``Runtime.create_agent``, passes ``agent_type`` and
        ``kwargs`` through, and sets this Agent as the parent. It does not
        resolve a ``SubagentEntry`` or dispatch the subagent invocation hooks;
        it only uses the normal Agent creation lifecycle. Use it when code
        needs to create and hold a child instance directly. Use
        ``invoke_subagent`` for alias resolution, invocation hooks, or resuming
        a named child.

        .. code-block:: python

            child = await agent.create_subagent("payment", order_id="456")
            result = await child.query("Submit the refund")

        If ``name`` is supplied, it is stored in the parent's child-name map
        and can later be used by ``invoke_subagent(resume=...)``. The child
        does not store this semantic name itself. The child is registered
        immediately, becomes a live descendant, and can inject from this
        parent. Its lifetime is managed by the caller, the Agent pool, or
        destruction of its parent.

        :param agent_type: String type name resolved by
            ``get_agent_class``.
        :param name: Optional parent-side name used to resume this child.
        :param kwargs: Arguments forwarded to the child's creation setup.
        :return: The newly created child Agent.
        :raises Exception: Type resolution and creation-pipeline failures
            propagate to the caller; the parent remains unchanged.

        .. seealso:: :meth:`invoke_subagent` and
            ``flowing.runtime.Runtime.create_agent``
        """
        ...

    async def invoke_subagent(
        self,
        agent_type: str = "",
        *,
        prompt: str | None = None,
        name: str | None = None,
        resume: str | None = None,
        **kwargs: Any,
    ) -> SubagentResult:
        """Resolve, create or resume, and run a subagent.

        For a new child, ``agent_type`` is an alias in this Agent's subagent
        entries. The entry resolves LLM arguments into child initialization
        arguments. For a resumed child, ``resume`` is the semantic name
        previously supplied through ``create_subagent`` or ``invoke_subagent``;
        entry lookup is skipped and the child's existing type is retained.

        The preparation phase constructs ``SubagentInvocation`` and dispatches
        ``on_subagent_invoke`` on the parent before creating or resuming the
        child. It then waits for ``child.query(prompt)`` and dispatches
        ``on_subagent_returns`` after filling ``invocation.result``. A return
        handler can modify the result before this method returns it.

        .. code-block:: python

            result = await agent.invoke_subagent(
                "coder", prompt="Review the authentication module", name="reviewer"
            )
            follow_up = await agent.invoke_subagent(
                resume="reviewer", prompt="Now review the payment module"
            )

        .. rubric:: Behavior notes

        - ``resume`` is mutually exclusive with creating a new child. On the
          resume path, ``kwargs`` are ignored and ``agent_type`` may be empty.
          An unknown child name raises ``ValueError``. A destroyed or unloaded
          child is recovered through ``Runtime.get_agent``.
        - The child is tracked as an ``Execution(kind="agent")`` and can be
          cancelled through the parent's cancellation mechanisms. A child
          cancelled after producing output still returns that output; its
          ``subagent_status`` reports ``"cancelled"``.
        - Creation, validation, and run failures propagate without a
          subagent-specific error hook. When invoked through the synchronous
          ``subagent-invoke`` tool path, the failure is represented by an
          error ``ToolResult``.
        - ``on_subagent_returns`` runs before this method returns. The value
          it writes to ``invocation.result`` is both the direct return value
          and the source for any corresponding subagent result delivery. This
          hook is not a blocking point; blocking belongs to
          ``on_subagent_invoke``.
        - The direct method call returns the result to its caller and does
          not enqueue a second ``SUBAGENT`` message on the parent. If external
          code wants to run it in a background Task without enqueueing a
          result, it can schedule ``invoke_subagent`` with
          ``asyncio.create_task``.
        - The asynchronous ``subagent-invoke`` tool mode is a separate path:
          it prepares the child, runs it in the background, and enqueues a
          ``SUBAGENT`` message when complete while returning a start receipt.
          Use the direct method instead when background execution should not
          enqueue a result.
        - Child lifetime is controlled by the Agent pool and explicit
          destruction; this method has no ``keep_alive`` option.

        :param agent_type: Subagent-entry alias for a new child. It may be
            empty when ``resume`` is supplied.
        :param prompt: Prompt sent to the child through ``query``.
        :param name: Optional semantic name assigned to a newly created child.
        :param resume: Semantic name of an existing child to continue.
        :param kwargs: New-child initialization arguments; ignored when
            resuming.
        :return: The child invocation result after return hooks have run.
        :raises Exception: Resolution, hook, creation, validation, or execution
            errors propagate to the caller.

        .. seealso:: :class:`SubagentEntry`, :class:`SubagentResult`,
            :class:`SubagentInvocation`, and :meth:`create_subagent`
        """
        ...

    async def tool_call(self, tool_call: ToolCall) -> ToolResult:
        """Run a bound tool call through the Agent's tool-hook pipeline.

        The call sequence is: dispatch ``before_tool_call`` (the handler may
        edit the ``ToolCall``, set ``shortcut`` to bypass execution, or raise
        ``Intercepted``); look up ``_tool_entries`` by alias; normalize
        arguments through Agent validation and ``ToolEntry.resolve()``; invoke
        ``Tool.__call__`` while registering and finally clearing a tool
        ``Execution``; attach ``name``, ``tool_call_id``, and ``production``
        metadata to executed results; dispatch ``on_tool_yields`` where
        applicable; attach the call name and ID to LLM argument-validation
        errors before dispatching ``after_tool_call``; normalize output again
        idempotently; and return.

        .. rubric:: Behavior notes

        - Tool lookup uses the Agent-level alias only. An unknown alias raises
          ``UnknownToolError``; there is no fallback to a canonical name.
        - If ``before_tool_call`` raises ``Intercepted``, this method returns
          ``ToolResult.blocked(...)`` whose LLM-visible content is
          ``[TextBlock(reason)]`` rather than raising the signal to the caller.
          LLM-facing argument-validation errors carry the call name and ID in
          ``after_tool_call``. The ``on_tool_yields`` hook runs only for
          non-blocked results produced by ``Tool.__call__``. It does not run
          for a shortcut or an LLM-facing argument-validation error;
          ``after_tool_call`` does run
          for a shortcut and may modify the result before final normalization.
        - A Tool returning ``ToolResult(status="error")`` is a normal result,
          not an exception-channel error. LLM-facing argument validation
          failures are also returned as error results for correction. Internal
          binding/configuration errors, such as invalid injected values, raise
          through the framework error channel instead.
        - An abort before a tool batch skips that batch. A tool already in
          flight is cancelled through the Tool call's cancellation race and
          produces a cancelled result.
        - Whether a call completes inline or returns a pending receipt depends
          on the Tool's ``execute`` implementation. An ordinary async
          ``execute`` is awaited to completion and returns a final result
          without queueing. If ``execute`` returns an ``asyncio.Task``, the
          Task is not awaited: a pending receipt is returned immediately and
          the Agent starts background delivery. The receipt is attached as a
          TOOL message with ``tool_status="pending"`` and empty content,
          closing the tool-call pair once. When the Task finishes, its result
          is normalized, passed through ``on_tool_yields`` and converted to
          blocks, then queued as a STEER-priority EVENT message with
          ``source="tool_result"``. This lets a long-running turn absorb the
          result at a checkpoint without interruption. A failed Task produces
          annotation and error-text blocks visible to the LLM, like a
          synchronous error result.
        - A normal ``ToolResult(status="error")`` is a result, not an
          exception-hook event; business errors from a tool do not use the
          exception channel. LLM-facing validation failures become error
          results for self-correction. Internal configuration errors in
          specified values, injection, or defaults instead propagate through
          the framework error channel and logs; they are not exposed as
          ``ToolResult`` text.
        - If an abort is observed before the tool-call batch starts, the whole
          batch is skipped. Calls already in flight are interrupted by
          ``Tool.__call__``'s cancellation race and produce cancelled results.

        :param tool_call: Call containing the alias, arguments, and call ID.
        :return: Final, blocked, error, or pending Tool result.
        .. seealso:: ``flowing.tool.ToolEntry``,
            ``flowing.tool.ToolResult``, and ``flowing.tool.ToolCall``
        """
        ...

    def source_dir(self) -> Path | None:
        """Return the directory containing this Agent's source definition.

        If ``source_file`` is an ``@/`` path, this resolves it through the
        Runtime and returns its parent directory. If there is no source file,
        it returns ``None``; bare-name references then use only the registry,
        and relative ``./`` references cannot be resolved. This method is the
        common source-directory calculation used by tool, Agent, skill, and
        Parsable file references.

        :return: The source file's directory, or ``None`` if the Agent has no
            file context.
        """
        ...

    def get_tool(self, name_or_path: str) -> Tool:
        """Resolve a tool reference using this Agent's file context.

        This delegates to the Runtime tool registry and supplies
        ``source_dir()``. A bare name first searches definition files relative
        to this Agent's source directory, which can override registry lookup;
        ``./`` and ``../`` references also use that directory. Without a file
        context, lookup falls back to the registry.

        :param name_or_path: Tool registry name or definition path.
        :return: Resolved Tool instance.
        :raises flowing.errors.ToolNotFoundError: If neither the file search
            nor the registry can resolve the reference.

        .. seealso:: ``flowing.tool.registry.ToolRegistry.get`` and
            :meth:`get_agent_class`
        """
        ...

    def get_agent_class(self, agent_type: str) -> type["Agent"]:
        """Resolve an Agent type using this Agent's file context.

        This delegates to ``Runtime.get_agent_class`` and supplies
        ``source_dir()``. A bare name searches files relative to this Agent
        before registry lookup; without a file context, lookup uses the
        registry only. This method resolves a class, not a live Agent
        instance. Use ``Runtime.get_agent`` to retrieve or recover an instance.

        :param agent_type: Registered type name or definition reference.
        :return: Resolved Agent class.
        :raises flowing.errors.AgentTypeNotFoundError: If neither the file
            search nor the registry resolves the name.

        .. seealso:: ``flowing.runtime.Runtime.get_agent_class`` and
            ``flowing.runtime.Runtime.get_agent``
        """
        ...

    def add_tool(
        self,
        name: str | EntryRef,
        *,
        alias: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> ToolEntry:
        """Bind a tool to this Agent under an optional alias.

        The method resolves ``name`` or an ``EntryRef``, interprets the
        optional override mapping, creates a ``ToolEntry``, and stores it by
        alias. The entry makes the tool available to this Agent; it is visible
        to the LLM when ``visible`` is true and callable through
        ``tool_call`` by alias.

        .. rubric:: Usage example

        .. code-block:: python

            self.add_tool("schedule-cron")
            self.add_tool(
                "make-payment",
                alias="pay",
                body={
                    "args": {
                        "amount": {"description": "Payment amount."},
                        "user_id": "{{ self.inject('user_id') }}",
                    }
                },
            )

        An ``EntryRef`` produced by declarative assembly must be passed without
        separate ``alias`` or ``body`` values. Programmatic ``body`` supports
        ``description``, ``args``, ``output``, and ``visible``; injection
        expressions belong inside ``args`` rather than in an ``inject`` key.
        The method is synchronous, and a new entry is available to the next
        context assembly.

        ``body`` is normalized to an ``EntryRef`` and handled by one pipeline.
        A description string becomes a constant ``Parsable``; an existing
        ``Parsable`` passes through; ``_`` means no override. Under ``args``,
        mapping values are sparse JSON Schema overrides, keys containing
        ``as`` define parameter aliases, ``_`` is an empty override, and
        other values become lazily evaluated ``specified`` parameters (which
        is where an injection expression belongs). ``output`` is handled
        separately as a field-name-to-JSON-Schema mapping and merged into
        output overrides; it does not use the ``args`` keyword validation.
        Omitting an output field removes it under the ``FinishTool`` contract.
        ``visible`` is passed through as a boolean. Deep ``$tools.<alias>``
        blocks are merged before this method runs, so it sees the final body.

        An alias already used by another entry raises
        ``EntryNameConflictError``; the later entry does not replace the first.
        Name globs are expanded only during declarative assembly, not by this
        method. Explicit declarations take precedence over a glob result for
        the same resource; a collision between different resources using the
        same alias raises ``EntryNameConflictError``. Glob matches are filtered
        by name: directory candidates follow the tool lookup chain, only
        ``*.tool.fya`` files require that explicit marker, and other ``.fya``
        or ``.py`` files are included. Included resources are eagerly resolved
        at creation, so an invalid resource fails immediately. MCP group definitions must first be
        expanded through ``ToolRegistry.expand_mcp()`` when adding
        programmatically; declarative assembly performs that step for you.
        Tool entries are rebuilt from declarations and ``setup``; this method
        neither persists an entry nor retains a Tool instance. At execution
        time, the original reference is resolved using this Agent's source
        directory. If a name is not registered, ``get_tool()`` first performs
        targeted file lookup using that directory (which can override
        ``default::`` and ``builtin::``); ``ToolNotFoundError`` is raised only
        if that search also misses, with no fuzzy name matching.

        :param name: Tool name, path, namespace-qualified name, or an
            ``EntryRef`` from declarative parsing.
        :param alias: LLM-visible binding alias. If omitted, it is inferred
            from the reference.
        :param body: Override mapping corresponding to a declarative tool
            entry. ``None`` means no overrides.
        :return: The new ``ToolEntry``.
        :raises flowing.errors.ToolNotFoundError: If the tool cannot be found
            in the registry or file search path.
        :raises flowing.errors.EntryNameConflictError: If this alias is already
            bound to another entry.
        :raises flowing.errors.FormatError: If ``EntryRef`` fields are also
            supplied separately, or if ``body`` has an unknown key or invalid
            shape.

        .. seealso:: ``flowing.tool.ToolEntry``, :meth:`tool_call`, and
            ``flowing.parser.EntryRef``
        """
        ...

    def add_agent(
        self,
        name: str | EntryRef,
        *,
        alias: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> SubagentEntry:
        """Bind a subagent type to this Agent's catalog and invocation table.

        This adds a type binding, not a child instance. It resolves the type,
        interprets the optional override mapping, creates a
        ``SubagentEntry``, and stores it by alias. The binding is visible in
        the subagent catalog when ``visible`` is true and can be invoked by
        alias through ``invoke_subagent``. Use ``create_subagent`` to create
        and hold an instance directly.

        .. rubric:: Usage example

        .. code-block:: python

            self.add_agent(
                "payment",
                alias="pay",
                body={
                    "description": "Submit a payment.",
                    "args": {"currency": "USD"},
                },
            )

        An ``EntryRef`` from declarative assembly must not be combined with
        separate ``alias`` or ``body`` values. Programmatic ``body`` supports
        ``system_prompt``, ``description``, ``args``, and ``visible``; there
        is no output-schema override or top-level ``inject`` key. Injected
        argument expressions resolve in the parent Agent's context and become
        child initialization arguments. Name globs are expanded only during
        declarative assembly, not by this method.

        ``body`` is normalized to an ``EntryRef`` and processed as follows:
        ``system_prompt`` and ``description`` accept a string (wrapped as a
        ``Parsable`` constant), an existing ``Parsable``, or ``_`` for no
        override. A system-prompt override is evaluated once when the child is
        created, using the parent Agent as context. A description override is
        evaluated for parent-side routing or catalog rendering, also using
        the parent Agent. Under ``args``, mapping values are sparse JSON
        Schema overrides, keys containing ``as`` define parameter aliases,
        ``_`` means no override, and other values become lazily evaluated
        ``specified`` parameters. Those values are resolved by
        ``invoke_subagent()`` against the parent Agent. The ``visible`` value
        is passed through as a boolean. Deep ``$subagents.<alias>`` blocks are
        merged before this method runs, so it sees the final body.

        Entries take effect immediately for later catalog assembly. Duplicate
        aliases raise ``EntryNameConflictError`` rather than replacing an
        existing binding. Entries are rebuilt from declarations and
        ``setup()`` and are not persisted. If a reference is not registered,
        ``get_agent_class()`` uses this Agent's ``source_dir`` to perform
        targeted file lookup, which can override ``default::`` or
        ``builtin::``; ``AgentTypeNotFoundError`` is raised if lookup still
        fails. This method does not expand name globs.

        :param name: Type name, path, qualified name, or parsed ``EntryRef``.
        :param alias: Catalog and invocation alias. If omitted, it is inferred
            from the reference.
        :param body: Override mapping corresponding to a declarative subagent
            entry.
        :return: The new ``SubagentEntry``.
        :raises flowing.errors.EntryNameConflictError: If the alias is already
            bound.
        :raises flowing.errors.FormatError: If reference fields are duplicated
            or ``body`` contains an unknown key or invalid value.
        :raises flowing.errors.AgentTypeNotFoundError: If the type is not
            found in the registry or file search path.

        .. seealso:: ``flowing.subagents.SubagentEntry``,
            :meth:`invoke_subagent`, and :meth:`add_tool`
        """
        ...

    def provide(self, key: str | InjectionKey[Any], value: Any) -> None:
        """Register or replace a value in this Agent's provide scope.

        Descendant Agents can retrieve this value through ``inject``. Providing
        the same key again replaces its value, and subsequent lookups see the
        update immediately.

        .. code-block:: python

            async def setup(self, user_id: str):
                self.provide("user_id", user_id)

        Provide-inject is the runtime channel for values that must not enter
        messages, model context, or persistence. It does not serialize or copy
        the value; the scope stores the object reference.

        :param key: String key or an ``InjectionKey`` for static typing.
        :param value: Value available to this Agent and its descendants.

        .. seealso:: :meth:`inject` and ``flowing.params.InjectionKey``
        """
        ...

    @overload
    def inject(self, key: InjectionKey[T]) -> T: ...
    @overload
    def inject(self, key: str) -> Any: ...
    def inject(self, key: str | InjectionKey[T]) -> T:
        """Find a provided value on this Agent or one of its ancestors.

        Lookup starts at this Agent and continues through its parent nodes to
        the Runtime. If no scope contains the key, ``MissingProvideError`` is
        raised. This is the standard way to share an application value across
        multiple levels of the Agent tree without passing it through every
        intermediate child.

        The parent relationship is resolved through Runtime node IDs. If a
        parent has been removed, lookup treats the chain as missing. The type
        parameter in ``InjectionKey[T]`` is a declaration-side convention;
        the framework does not perform runtime type validation.

        :param key: Provide key to find.
        :return: The closest matching provided value.
        :raises flowing.errors.MissingProvideError: If no scope in the chain
            contains the key.

        .. seealso:: :meth:`provide`, ``flowing.provide.inject_from``, and
            ``flowing.params.InjectionKey``
        """
        ...

    @overload
    def get_resource(self, name: str) -> Any: ...
    @overload
    def get_resource(self, name: str, type_hint: type[T]) -> T: ...
    def get_resource(self, name: str, type_hint: type[T] | None = None) -> Any:
        """Get a Runtime-registered shared resource from this Agent.

        This delegates to ``self.runtime.get_resource``. Use it to retrieve
        resources with an expensive initialization cost, such as a renderer,
        connection pool, or cache, on demand rather than constructing them
        during Agent setup. Resources can be shared across tasks; Agent
        instances are not shared this way.

        .. code-block:: python

            async def setup(self):
                self._renderer = self.get_resource("ui_renderer", UiRenderer)

        :param name: Resource registry name.
        :param type_hint: Optional IDE/type-checker hint. Runtime lookup does
            not verify this type.
        :return: Registered resource instance.
        :raises flowing.errors.ResourceNotFoundError: If the name is not
            registered with the Runtime.

        .. seealso:: ``flowing.runtime.Runtime.register_resource``
        """
        ...

    def watch(
        self, name: str, handler: WatchHandler | None = None
    ) -> WatchHandler:
        """Observe assignments to matching instance attributes.

        A callback receives ``(new_value, old_value)`` and its return value is
        ignored. It may be synchronous or asynchronous. This convenience API
        wraps the callback for the lower-level watcher channel; it is not a
        regular hook point and cannot rewrite assignments or block them with
        ``Intercepted``. Notifications run as fire-and-forget background work.

        .. code-block:: python

            @agent.watch("locale")
            def on_locale_change(new_value, old_value):
                print(old_value, "->", new_value)

        ``name`` is a ``fnmatch`` pattern, so a literal name matches exactly
        and wildcards such as ``"*"`` match according to ``fnmatch`` rules.
        Without a running event loop, assignment still succeeds but the
        watcher does not run. Notifications for multiple assignments are not
        ordered. Watching observes assignment events, not changes to an
        already-assigned lazy ``Parsable`` value. Reassign the attribute to
        trigger another notification.

        Pass ``handler=None`` to get a decorator; otherwise the callback is
        registered and returned. For the complete update object, register
        directly through ``self.hooks.watch`` and receive ``(agent, field_update)``.

        :param name: Attribute-name pattern to observe.
        :param handler: Optional callback. If omitted, return a decorator.
        :return: The registered callback, or a decorator when ``handler`` is
            ``None``.

        .. seealso:: :class:`FieldUpdate` and :meth:`parsable`
        """
        ...

    def parsable(self, source: Any) -> Parsable:
        """Create a ``Parsable`` value bound to this Agent as its context.

        This is the explicit alternative when a value is not assigned through
        ``__setattr__``. Calling ``str()`` or ``resolve()`` on the result uses
        this Agent as the rendering context, including its ``env`` and
        ``config`` values.

        .. code-block:: python

            self.greeting = self.parsable("Hello {{ user_id }}")
            print(self.greeting.resolved)

        Parsable values are not implicitly resolved in every location. If a
        value is stored in an extra attribute or an entry override that is not
        itself a resolution point, call ``resolve(context)`` explicitly.

        :param source: Source value used to construct the ``Parsable``.
        :return: A Parsable bound to this Agent.

        .. seealso:: ``flowing.parsable.Parsable``
        """
        ...

    def snapshot(self, *, keys: set[str] | None = None) -> AgentSnapshot:
        """Return a consistent, read-only snapshot of this Agent's state.

        The snapshot includes identity, message-tree summary and cursor,
        current turn, active executions, queue counts, model view, tool and
        subagent bindings, and an optional context-usage estimate.

        .. code-block:: python

            view = agent.snapshot()
            assert view.current_turn is None
            assert view.message_queue.size == 0

        Fields are copies or read-only views, and one call observes a
        consistent point in time. Passing ``keys=None`` collects every field;
        a set collects only the named fields and leaves the others as
        ``None``. Request related fields together when they must represent the
        same time. Omitting ``context_usage`` avoids its estimation cost.
        Snapshot fields are serializable, and the method does not modify Agent
        state. ``current_turn=None`` means there is no active logical turn.

        :param keys: Snapshot field names to collect, or ``None`` for all
            fields.
        :return: An ``AgentSnapshot`` detached from mutable Agent internals.

        .. seealso:: ``flowing.runtime.Runtime.snapshot`` and
            ``flowing.snapshot.AgentSnapshot``
        """
        ...
