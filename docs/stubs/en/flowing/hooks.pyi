"""Register and dispatch hooks on individual Agent instances.

.. rubric:: Overview

This module provides an instance-scoped extension mechanism. A handler can
observe or modify values at fixed points in an Agent's creation, recovery,
destruction, turn, provider, tool, message-queue, fork, and cancellation
lifecycles. Each Agent owns an independent :class:`HookRegistry`; registering a
handler on one Agent does not affect another.

The public API consists of :class:`HookRegistry` for declaring hook points,
:class:`HookList` for registering and dispatching handlers,
:class:`HookEntry` for a registered handler record,
:class:`PatternRegistrar` for pattern-filtered registration, and :func:`on`
for declarative registration on Agent subclasses and ``.fya`` scripts.
``HookList`` uses the grouped-entry behavior of :mod:`flowing.lists`.

Naming convention: use paired ``before_`` / ``after_`` hooks when a complex
operation's inputs and results both need supervision or modification (for
example, tool calls, provider generation, and creation). Use a single ``on_``
hook for standalone events such as enqueue, dequeue, fork, turn append/abort,
provider errors, and provider deltas. ``on_subagent_invoke`` and
``on_subagent_returns`` are independent events, not a before/after pair: a
subagent may run for an extended period between invocation and result
delivery.

.. rubric:: Core hook points

Every Agent starts with these 24 core hook points. A hook with a value receives
``(agent, value)``; a hook without a value receives only ``agent``. The value
type and handler capability are specific to each hook.

.. list-table:: Core hook points
   :header-rows: 1
   :widths: 22 34 44

   * - Hook
     - When it runs and its value
     - Handler behavior
   * - ``before_create``
     - Before creation-time ``setup()``; the value is the creation ``kwargs``.
     - May replace the keyword arguments passed to ``setup``; sync and async handlers are accepted. Register this hook with ``@on`` because setup has not run yet. Recovery does not trigger it.
   * - ``after_create``
     - After state persistence and node registration, before the work loop starts; no value.
     - Observes or performs cleanup after creation; sync and async handlers are accepted.
   * - ``before_recover``
     - Before recovery-time ``setup()``; the value is the recovery arguments.
     - May replace the arguments passed to ``setup``; sync and async handlers are accepted.
   * - ``after_recover``
     - After restoration and node registration, before the work loop starts; no value.
     - Observes or performs recovery-side effects; sync and async handlers are accepted.
   * - ``before_destroy``
     - During ``destroy()``, before recursive child destruction; no value.
     - Runs before child nodes are destroyed; sync and async handlers are accepted.
   * - ``after_destroy``
     - At the end of ``destroy()``, after node removal; no value.
     - Runs during destruction cleanup; sync and async handlers are accepted.
   * - ``before_turn``
     - At turn start, before the dequeued batch is attached to the tree; the value is ``TurnContext``.
     - May edit ``pending_messages``. Raising ``Intercepted`` discards the batch without persisting it.
   * - ``on_turn_append``
     - Before a message is attached to the tree and persisted; the value is ``Message``.
     - May modify the message. Raising ``Intercepted`` blocks the append.
   * - ``before_provider_gen``
     - Before each provider call; the value is ``Context``.
     - May replace or modify the context passed to the provider adapter.
   * - ``after_provider_gen``
     - After a provider call returns; the value is ``ProviderResponse``.
     - May modify the response. Its ``match_on`` field is ``"by"``, so patterns can select a source such as ``"_turn"`` or ``"_side"``.
   * - ``on_provider_delta``
     - For every streaming delta, or one synthesized full-text delta for a non-streaming call; the value is ``ProviderDelta``.
     - Observation only. Deltas are not persisted and handler return values are ignored. Use ``after_provider_gen`` to change the complete response. Streaming is selected by ``provider_gen(stream=...)``, not by subscribers.
   * - ``before_tool_call``
     - Before tool execution; the value is ``ToolCall``.
     - May modify the call or set ``shortcut`` to bypass normal execution. Raising ``Intercepted`` prevents the tool from running and produces a blocked result.
   * - ``after_tool_call``
     - After tool execution, including the shortcut path; the value is ``ToolResult``.
     - May modify the result before output normalization. Raising ``Intercepted`` produces a blocked result instead of propagating through the work loop.
   * - ``on_tool_yields``
     - Once for each non-blocked result emitted by the tool itself: once for a synchronous result, and once for a background tool's receipt, each segment, final result, and termination notification (error or cancellation); the value is ``ToolResult``.
     - May change the raw ``output`` before normalization. It does not run for blocked results, handler-produced shortcuts, or errors from LLM-facing schema validation when the tool was never executed. Raising ``Intercepted`` blocks a synchronous result; for a background tool it discards that segment and emits an EVENT with the interception reason. Pattern matching uses the tool alias.
   * - ``on_subagent_invoke``
     - When a subagent is invoked, after argument resolution; the value is ``SubagentInvocation``.
     - May modify arguments or prompt. Raising ``Intercepted`` prevents invocation. The hook belongs to the parent Agent.
   * - ``on_subagent_returns``
     - After a subagent result is constructed and before delivery; the value is ``SubagentInvocation``.
     - May modify the result used to build the delivery message. ``Intercepted`` is not handled as a blocking signal here. The hook belongs to the parent Agent.
   * - ``on_turn_abort``
     - Once when a turn is aborted; the value is ``TurnContext``.
     - Observes the abort or performs pre-cleanup intervention.
   * - ``after_turn``
     - During finalization on every path, including exceptional termination; the value is ``TurnContext``.
     - The final observation point. Inspect ``value.aborted`` to distinguish exceptional termination. Waiters are resolved before a handler error is re-raised.
   * - ``on_provider_error``
     - When an LLM call inside ``provider_gen()`` raises; the value is ``ProviderErrorContext``.
     - The only decision hook for provider-call errors. A handler can wait, change the model, call ``abort_turn()``, and set ``ctx.can_continue`` to request a retry in the same turn.
   * - ``on_enqueue``
     - Before a message enters the queue; the value is ``Message``.
     - May modify the message. Raising ``Intercepted`` rejects it without changing the queue.
   * - ``on_dequeue``
     - After dequeue and before turn start; the value is ``list[Message]``.
     - May transform the batch. Messages removed from the final batch are resolved as cancelled; an empty batch is discarded and the caller waits again.
   * - ``on_fork``
     - After fork validation and before the active head changes; the value is ``ForkContext``.
     - May change ``target_message_id``. Raising ``Intercepted`` prevents the switch.
   * - ``before_cancel``
     - Before ``cancel()`` or ``stop()`` sets the cancellation signal; the value is ``CancelContext``.
     - Raising ``Intercepted`` prevents cancellation and leaves the signal unset.
   * - ``after_cancel``
     - Immediately after the cancellation signal is set; the value is ``CancelContext``.
     - Observation only, for example logging, notification, or auditing.

Extensions may declare additional hook points. Examples include
``before_skill_load`` and ``after_skill_load`` from Skills, ``on_signal`` and
``on_event`` from Comm, ``on_cron_trigger`` from Cron, and ``on_compact`` or
``on_retry`` from Composables. Their owners and pattern fields are respectively
``by="skill"`` with ``match_on="name"``; ``by="comm"`` with ``match_on="type"``
for ``on_signal`` and ``match_on="topic"`` for ``on_event``; ``by="cron"`` with
``match_on="source"`` for ``on_cron_trigger``; and ``by="compact"`` or
``by="retry"`` for the Composable hooks. A point that has not been declared on
an Agent cannot be accessed.

``before_`` and ``after_`` hooks mark points before and after an operation
controlled by the Agent; ``on_`` hooks respond to external events. The
``on_`` prefix alone does not imply observation-only behavior: individual
hooks define their own capabilities. For example, ``on_provider_error`` can
make a retry decision, ``on_signal`` can change a payload, and
``on_provider_delta`` is observation-only.

.. rubric:: Example

.. code-block:: python

    from flowing import Agent, on
    from flowing.errors import Intercepted

    class OrderAgent(Agent):
        @on("before_create")
        def setup_kwargs(self, kwargs):
            kwargs.setdefault("locale", "en")
            return kwargs

        @on("before_tool_call")
        def guard_payment(self, tool_call):
            if tool_call.name.startswith("payment-"):
                raise Intercepted("Payment tools require approval.")
            return tool_call

        async def setup(self):
            def audit_turn(agent, turn):
                return turn

            def guard(agent, tool_call):
                return tool_call

            self.hooks.after_turn(audit_turn, by="audit", tags=["security"])
            self.hooks.before_tool_call["payment-*"](guard, by="guardrail")

``.fya`` scripts can use ``@on`` in their ``$script`` block to register handlers
before ``setup()`` runs.

.. code-block:: text

    ---
    $script:
    from flowing import on

    @on("before_tool_call")
    def guard(agent, tool_call):
        tool_call.args["lang"] = agent.locale
        return tool_call

Handlers are not limited to ``setup()``. Dispatch reads the handler list when
the hook fires, so a handler registered during execution can affect later
dispatches. REPL, HTTP-service, test, and plugin code all use the same public
registration API and have the same authority: a handler on a decision-capable
hook may change its value or raise ``Intercepted``. For observation-only work,
use the appropriate ``after_*`` or observational ``on_*`` hook and return the
value unchanged. Hook registration is process-local; there is no HTTP endpoint
for remote clients to add hooks.

.. rubric:: Dispatch and registration behavior

- A value-bearing handler has the form ``(agent, value) -> value``. A hook
  without a value receives only ``agent``. Sync and async handlers can be mixed;
  dispatch awaits awaitable results. Registration does not validate handler
  signatures.
- Each handler's return value is passed to the next handler. Returning
  ``None`` for a value-bearing hook is a programming error and raises
  ``FlowingError``. A non-``None`` ``shortcut`` value stops the chain and is
  returned to the caller.
- Raising ``Intercepted`` stops dispatch and is re-raised to the calling
  operation. How the operation handles the signal depends on the hook, but the
  operation is invalidated and its corresponding ``after_`` hook does not run.
  By contrast, ``shortcut`` skips default logic but does not suppress the
  corresponding ``after_`` hook. Other exceptions propagate immediately; there
  is no generic error hook.
- Handlers run in registration order. Disabled entries are skipped. A pattern
  is matched with ``fnmatch`` against the hook's ``match_on`` field; a
  non-matching value passes unchanged to later handlers. If no handler runs,
  dispatch returns the original value.
- A hook point has one declaring owner, but registration is open after
  declaration. The ``by`` argument to registration identifies handler
  ownership and supports group removal or enable/disable operations. Duplicate
  registrations are allowed and run once per registration.
- Core hooks are dispatched by the framework at their lifecycle points.
  Extensions dispatch the hook points they declare with
  ``await hooks.<name>.dispatch(agent, value)``.
- A hook point has exactly one declaring owner (``by`` is required at
  declaration); the framework uses ``by="core"`` and extensions use their
  own identifier. After declaration, any code may register handlers on it.
  The declaring owner and handler owners are separate concepts: handler
  ``by`` values support grouped removal and enable/disable operations.
- Hook execution order is registration order. Duplicate registrations are not
  deduplicated; registering the same handler twice runs it twice per dispatch.

Creation-time hooks such as ``before_create`` must be registered with ``@on``
because ``setup()`` has not run when they fire. For extension hooks, ``@on``
records handlers until setup declares the corresponding hook point. Declaring
the extension hook flushes those pending handlers onto it before subsequently
registered handlers. An unresolved handler name fails during creation rather
than being silently ignored.

``watch`` is a separate fire-and-forget observation channel for instance
attribute updates, not a hook point. Watchers match ``FieldUpdate.name`` using
``fnmatch``; their return values are ignored, and they cannot modify an
assignment that has already happened. They are scheduled without awaiting, so
their execution order relative to the assignment is not guaranteed. Without a
running event loop, watchers are skipped. Use a property setter when an
assignment must be intercepted. ``self.hooks.watch(name, handler)`` receives
``(agent, FieldUpdate)``; ``Agent.watch`` is a callback convenience that
receives ``(new, old)``. A watcher exception (including ``Intercepted``) stops
that watcher chain and is logged without affecting the completed assignment.

Errors are exceptions, not generic events: there is no catch-all ``on_error``
hook. The sole decision hook for an error is ``on_provider_error``; background
errors can be observed through :mod:`flowing._unstable.logging`, which writes
``logging.jsonl``.

.. seealso:: :class:`flowing.agent.Agent`, :class:`flowing.agent.TurnContext`,
   :class:`flowing.errors.Intercepted`, :class:`flowing.errors.UnknownHookPointError`,
   :class:`flowing.errors.DuplicateHookPointError`, :mod:`flowing.lists`,
   :mod:`flowing.plugins.skills`, :mod:`flowing.plugins.comm`,
   :mod:`flowing.plugins.cron`, :mod:`flowing.composables.retry`,
   :mod:`flowing.composables.compact`
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias
from .lists import ManagedList, Togglable
HookHandler: TypeAlias = Callable[..., Any]
"""Callable used as a hook handler.

Value-bearing hooks conventionally use ``(agent, value) -> value``; hooks
without a value take only ``agent``. Handlers may return synchronously or
return an awaitable, which dispatch awaits, including during lifecycle hooks.
"""
T = TypeVar("T")
@dataclass
class HookEntry:
    """A registered handler with owner, tags, optional pattern, and enabled state.

    Entries are usually created through :meth:`HookList.__call__` or
    :meth:`PatternRegistrar.__call__`, not directly. The pattern is stored with
    the entry, so group operations manage patterned and unpatterned handlers
    in the same way.

    .. rubric:: Behavior

    A non-``None`` pattern is matched against the value's ``match_on`` field
    using ``fnmatch`` before the handler runs. If it does not match, the value
    passes unchanged to the next handler. Disabled entries remain in place but
    are skipped by iteration and dispatch; they can be re-enabled by tag or
    owner.
    """
    handler: HookHandler
    """The callable invoked by dispatch. Value-bearing hooks use
    ``(agent, value) -> value``; hooks without a value pass only ``agent``.
    """
    by: str | None
    """Optional owner or source identifier for group management. This is
    separate from the required ``by`` value that identifies the hook-point
    declarer.
    """
    tags: list[str]
    """Labels used by tag-based batch management."""
    pattern: str | None
    """Optional ``fnmatch`` pattern used to filter values before this handler
    runs. It is set by patterned registration.
    """
    enabled: bool
    """Whether this entry participates in iteration and dispatch. Disabling
    retains its registration position.
    """
class PatternRegistrar:
    """Registrar returned by the ``hook["pattern"](handler)`` shorthand.

    Passing a string to :meth:`HookList.__getitem__` returns this object.
    Calling it registers the handler so that it runs only when the hook
    value's ``match_on`` attribute matches the pattern.

    Filtering by name is a common need, such as applying approval only to
    ``payment-*`` tools. This separate Registrar lets ``hook["pattern"]``
    use the same call shape as ``hook(handler)``. Registration still produces
    an ordinary :class:`HookEntry` with a non-empty ``pattern`` field, so
    grouping, registration order, and dispatch use the same semantics.

    .. rubric:: Example

    .. code-block:: python

        self.hooks.before_tool_call["payment-*"](self._guard, by="guardrail")
        # fnmatch syntax: * any sequence, ? one character, [abc] a character
        # class, and a literal pattern for an exact match.

    .. rubric:: Behavior notes

    - Calling this object constructs and appends a ``HookEntry`` with the
      supplied handler, ``by``, and tags and this pattern. It returns the
      original handler, so decorator use does not hide the decorated name.
      To obtain the ``HookEntry`` handle, use an integer or slice index on the
      ``HookList`` or one of its bulk-management APIs.
    - If the value does not match, the handler is not called and the value
      passes through unchanged.
    - The pattern is evaluated lazily for each entry during dispatch, rather
      than by wrapping the handler at registration. This keeps
      ``entry.pattern`` observable and lets normal enable/disable management
      apply to the entry.
    - Registering a pattern on a hook value without the configured
      ``match_on`` attribute succeeds. Dispatch then raises the resulting
      ``getattr`` error under the normal handler-exception rules; it is a
      programming error and is not caught as a fallback.

    .. seealso:: :meth:`HookList.__getitem__`, :class:`HookEntry`,
        :attr:`HookList.match_on`
    """
    def __init__(self, hook_list: HookList, pattern: str) -> None:
        """Create a registrar for one hook list and pattern.

        :param hook_list: The hook list that will receive the handler.
        :param pattern: The ``fnmatch`` pattern used during dispatch.
        """
        ...
    def __call__(self, handler: HookHandler, *, by: str | None = None, tags: list[str] | None = None) -> HookHandler:
        """Register ``handler`` with this registrar's pattern.

        :param handler: The function to register.
        :param by: Optional handler owner, stored as ``None`` when omitted.
        :param tags: Optional group labels, normalized to an empty list.
        :return: The original handler, so decorator syntax does not replace its name.

        The entry is appended to the hook list. If the value lacks the hook's
        ``match_on`` attribute, registration still succeeds; dispatch then
        raises the resulting attribute error as an ordinary handler error.

        .. seealso:: :meth:`HookList.__call__`
        """
        ...
class HookList(ManagedList[HookEntry]):
    """Ordered handler container for one declared hook point.

    A :class:`HookRegistry` owns one list per hook point. This class provides
    handler registration, integer/slice inspection, pattern registration,
    grouped entry management, and the shared dispatch algorithm. Registration
    order is execution order.

    .. rubric:: Example

    .. code-block:: python

        self.hooks.before_tool_call(self.audit, by="audit", tags=["security"])
        self.hooks.before_tool_call["payment-*"](self.guard, by="guardrail")
        first_entry = self.hooks.before_tool_call[0]
        first_two = self.hooks.before_tool_call[:2]

    Integer and slice access include disabled entries; iteration and dispatch
    skip them. Lifecycle hooks accept both synchronous and asynchronous
    handlers.

    .. seealso:: :class:`HookRegistry`, :class:`HookEntry`,
       :class:`PatternRegistrar`, :class:`flowing.lists.ManagedList`
    """
    name: str
    """Unique hook-point name within its :class:`HookRegistry`."""
    by: str
    """Required owner identifier of the code that declared this hook point.
    Core hooks use ``"core"``; extensions use their own identifier.
    """
    match_on: str
    """Name of the hook value attribute used for pattern matching. The default
    is ``"name"``; Comm hooks may use ``"type"`` or ``"topic"``.
    """
    def __init__(self, name: str, *, by: str, match_on: str = "name") -> None:
        """Create a hook list, normally through :class:`HookRegistry`.

        :param name: The hook-point name.
        :param by: Required declaring owner; ``None`` is not allowed.
        :param match_on: Value attribute used for patterned registrations.
            The default is ``"name"``.

        The method does not validate that values passed to dispatch actually
        have the ``match_on`` attribute. Such an error is raised during
        dispatch.
        """
        ...
    def __call__(self, handler: HookHandler, *, by: str | None = None, tags: list[str] | None = None) -> HookHandler:
        """Append a handler to this hook point and return the original callable.

        :param handler: The function to register.
        :param by: Optional handler owner; omitted values are stored as ``None``.
        :param tags: Optional group labels; omitted values become an empty list.
        :return: The original handler, including when used as a decorator.

        Registration appends to the list, so earlier handlers run first.
        Duplicate registrations are allowed and each registration runs. Handler
        signatures are not checked at registration; a signature error is
        raised as an ordinary exception during dispatch. Async handlers are
        accepted, including for lifecycle hooks during construction, and are
        awaited by the same dispatch path. Watchers also accept async
        handlers; their fire-and-forget notifications are triggered by
        ``__setattr__`` and are independent of hook dispatch.

        .. seealso:: :meth:`dispatch`, :class:`PatternRegistrar`, :func:`on`
        """
        ...
    def __getitem__(self, index: int | slice | str) -> HookEntry | list[HookEntry] | PatternRegistrar:
        """Inspect entries by position or create a pattern registrar.

        :param index: An integer returns one entry, a slice returns a list of
            entries, and a string returns a :class:`PatternRegistrar`.
        :return: The indexed entry, sliced entries, or a patterned registrar.
        :raises IndexError: An integer or slice index is out of range.

        Integer and slice access use the full list, including disabled entries.
        A string is treated as a pattern and is not used to search existing
        entries. Patterns match the hook value's ``match_on`` attribute with
        ``fnmatch``. Watchers use ``HookRegistry.watch`` instead of this list.

        .. seealso:: :class:`PatternRegistrar`, :meth:`HookRegistry.watch`
        """
        ...
    async def dispatch(self, *args: Any, **kwargs: Any) -> None:
        """Run active handlers in registration order and pass each result onward.

        The framework calls this method at core lifecycle points. Extensions
        call it for hook points they declare.

        .. rubric:: Example

        .. code-block:: python

            result = await agent.hooks.on_event.dispatch(agent, payload)

        .. rubric:: Behavior

        Disabled entries are skipped. Each pattern is matched against the
        current value's ``match_on`` field; a non-match passes the value
        unchanged to the next handler. Awaitable handler results are awaited.
        A handler receives ``(agent, value)``. The first argument is the hook
        host: an Agent for Agent hooks, or a Workflow for its own tool hooks.
        Dispatch only passes it through; a handler that needs Agent-specific
        capabilities must check its type itself. Sync and async handlers can
        be mixed.

        For a call with a value, each handler must return a value. Returning
        ``None`` raises ``FlowingError`` with the hook-point name and handler
        identity. Hooks without a value, such as ``after_create``, do not have
        this check. Each result becomes the next value. A non-``None``
        ``shortcut`` immediately returns that value and stops the chain; the
        caller still runs the corresponding ``after_`` hook on this path.

        ``Intercepted`` is re-raised immediately: later handlers do not run,
        and the corresponding ``after_`` hook is not called. Other exceptions
        also propagate without being caught, notified, or passed to a fallback
        hook. If no handler runs (because none are registered, all are
        disabled, or patterns filter them all out), the original value is
        returned unchanged.
        """
        ...
class HookRegistry:
    """Declare hook points and access their per-Agent handler lists.

    Each Agent creates its own registry. It starts with the 24 core hook
    points; extensions declare additional points during setup. Accessing an
    undeclared point raises ``UnknownHookPointError`` rather than creating an
    empty list. The same hook name cannot be declared by different owners.

    .. rubric:: Example

    .. code-block:: python

        def use_comm(agent):
            agent.hooks.declare("on_signal", by="comm", match_on="type")
            agent.hooks.on_signal["permission_request"](handler, by="guard")

    Registration is open after declaration: any code can add a handler without
    declaring the hook again. The code that declares an extension hook is
    responsible for dispatching it.

    .. seealso:: :class:`HookList`, :class:`flowing.errors.UnknownHookPointError`,
       :class:`flowing.errors.DuplicateHookPointError`
    """
    def __init__(self) -> None:
        """Create a registry and populate its core hook points.

        Core lists start without handlers. Their matching field is ``"name"``
        except for ``after_provider_gen`` and ``on_provider_delta``, which use
        ``"by"`` to filter by provider-call source.
        """
        ...
    def declare(self, name: str, *, by: str, match_on: str = "name") -> HookList:
        """Declare an extension hook point; declaration is exclusive, registration is open.

        Extensions normally declare their hook points during ``setup()`` and
        dispatch them where the corresponding event occurs.

        Hook points are created by declaration rather than belonging to a
        fixed core-only set. For example, Skills declares
        ``before_skill_load`` and Comm declares ``on_signal``; an Agent that
        does not install an extension has no corresponding extension hook
        point. The declaring extension owns dispatch, while any code may
        register handlers after declaration without declaring the point
        again.

        .. rubric:: Behavior notes

        - ``by`` is required and cannot be omitted or set to ``None``. Core
          hook points use ``by="core"``; extensions use their own identifier.
        - A repeated declaration with the same name and owner is idempotent
          and returns the existing ``HookList``. The first ``match_on`` value
          remains in effect; later values are not checked for consistency.
        - Declaring the same name under a different owner raises
          ``DuplicateHookPointError``.
        - Declare hook points during ``setup()`` or a ``use_xxx`` setup
          helper. Declaring one during a turn is not prohibited, but timing is
          the extension's responsibility and has no framework guarantee.

        :param name: The hook-point name.
        :param by: Required identifier of the declaring extension.
        :param match_on: Value attribute used by pattern matching; defaults to
            ``"name"``.
        :return: The hook list for this point.
        :raises flowing.errors.DuplicateHookPointError: Another owner has
            already declared the same name.

        The declaring extension, not the framework, dispatches this hook
        point. When a new point is created, all pending ``@on`` registrations
        with the same name (including patterned registrations) are attached
        and removed from the pending list. Since no other handlers are yet
        registered, they are first. The same-owner idempotent path does not
        flush them again; they were settled on first declaration.

        .. seealso:: :class:`HookList`, :meth:`HookList.dispatch`
        """
        ...
    def watch(self, name: str, handler: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:
        """Register a watcher for matching instance-attribute updates.

        This is a separate observation channel, not a hook point.

        :param name: An ``fnmatch`` pattern matched against ``value.name``.
        :param handler: A synchronous or asynchronous ``(agent, value)`` callable.
        :return: The original handler, allowing decorator syntax.

        Watchers are scheduled without awaiting. Their return values are
        ignored; they cannot modify the assignment. Exceptions are logged and
        do not affect the assignment.
        """
        ...
    def __getattr__(self, name: str) -> HookList:
        """Return a previously declared hook list by name.

        :param name: The hook-point name.
        :return: The corresponding :class:`HookList`.
        :raises flowing.errors.UnknownHookPointError: The hook point has not
            been declared on this Agent.

        Attribute access looks up an existing point and never creates one.

        .. seealso:: :meth:`declare`
        """
        ...
class OnRegistrar:
    """Decorator helper returned by :func:`on`.

    Calling it directly marks a handler without a pattern; indexing it with a
    string returns a patterned decorator. Both forms return the original
    function. This helper is normally consumed immediately as part of decorator
    syntax rather than stored or reused.

    .. seealso:: :func:`on`
    """
    def __init__(self, hook_name: str, by: str | None, tags: list[str] | None, pattern: str | None = None) -> None:
        """Create the decorator helper used by :func:`on`.

        :param hook_name: The target hook-point name.
        :param by: Optional handler owner; omitted values are ``None``.
        :param tags: Optional handler labels.
        :param pattern: Optional ``fnmatch`` filter pattern.
        """
        ...
    def __call__(self, handler: HookHandler) -> HookHandler:
        """Mark ``handler`` for registration without a value-matching pattern.

        :param handler: The function to mark.
        :return: The same function, unchanged as a callable.
        """
        ...
    def __getitem__(self, pattern: str) -> Callable[[HookHandler], HookHandler]:
        """Return a decorator that marks a handler with ``pattern``.

        :param pattern: The ``fnmatch`` pattern applied to the hook value's
            ``match_on`` attribute.
        :return: A decorator that returns the original handler.
        """
        ...
def on(hook_name: str, by: str | None = None, tags: list[str] | None = None) -> OnRegistrar:
    """Mark an Agent method for declarative hook registration.

    Use this decorator in an Agent subclass or a ``.fya`` ``$script`` block.
    Agent initialization collects marked methods and registers them before
    ``setup()`` runs. This permits registration for creation-time hooks such as
    ``before_create``, before ``self.hooks`` can be used in setup code.

    .. rubric:: Example

    .. code-block:: python

        from flowing import Agent, on

        class OrderAgent(Agent):
            @on("before_create")
            def setup_kwargs(self, kwargs):
                kwargs.setdefault("locale", "en")
                return kwargs

            @on("before_tool_call", by="audit", tags=["audit"])
            def add_request_id(self, tool_call):
                tool_call.args["request_id"] = self.node_id
                return tool_call

            @on("on_signal")["agent-message-*"]
            def inspect_envelope(self, envelope):
                return envelope

    In a ``.fya`` file, the same decorator can appear in ``$script``:

    .. code-block:: text

        ---
        $script:
        from flowing import on

        @on("before_tool_call")
        def guard(agent, tool_call):
            tool_call.args["lang"] = agent.locale
            return tool_call

    .. rubric:: Behavior

    The decorator marks the function and returns it unchanged; it does not
    register the handler immediately. Multiple ``@on`` decorators can mark one
    method for multiple hook points. During Agent initialization, an override
    in a derived class takes precedence over a marked method with the same
    name in a base class; otherwise registrations are ordered from base class
    to derived class.

    The target hook must be declared by the end of setup. Core hooks already
    exist; extension hooks can be declared by an enabled extension. An
    unresolved name raises ``UnknownHookPointError`` during creation. ``by``
    defaults to ``None``; ``remove_by_owner(None)`` therefore removes every
    anonymous handler, including handlers registered by other sources. Provide
    an explicit owner when handlers need to be managed as a group.

    :param hook_name: The hook-point name.
    :param by: Optional handler owner.
    :param tags: Optional labels for group management.
    :return: A decorator helper for the selected hook.

    .. seealso:: :class:`HookRegistry`, :meth:`HookList.__call__`,
       :meth:`flowing.agent.Agent._init_hooks`
    """
    ...
