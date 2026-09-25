"""In-process point-to-point signals and publish-subscribe events.

The communication extension is built into and shipped with Flowing, but it is
not enabled automatically. The framework core does not depend on it; a
Runtime opts in by installing ``CommPlugin``.

Install ``CommPlugin`` to create one Runtime-scoped ``Communication`` bus and
provide it under ``communication_key``. Call ``use_comm()`` from an Agent's
``setup()`` to register an endpoint, declare the ``on_signal`` and
``on_event`` hook points, and arrange endpoint cleanup before Agent
destruction. Application components that are not Agents can create endpoints
directly through the same bus.

Signals target one endpoint; events are broadcast to the subscribers of a
topic.

Sending a signal returns without waiting for an asynchronous receiver.
Requests wait for a reply correlated by ``correlation_id`` and can time out
or be cancelled when the reply handle is destroyed. Event publication invokes
synchronous subscribers before returning and schedules awaitable subscribers
without waiting for them; callback failures are logged without interrupting
other deliveries. Runtime shutdown clears the bus's endpoint and subscription
tables, while each handle remains responsible for cancelling its own pending
requests.

.. rubric:: Registration surface

``CommPlugin.install()`` provides the bus at Runtime scope. ``use_comm()``
declares ``on_signal`` with ``by="comm"`` and ``match_on="type"``,
``on_event`` with ``by="comm"`` and ``match_on="topic"``, and registers a
``before_destroy`` handler with ``by="comm"`` to destroy the Agent's handle.
The plugin itself registers no tools, configuration namespace, or hook
handlers. Installing another plugin with the same name in one Runtime raises
``ValueError``; a Runtime can have only one ``comm`` plugin.

The provide key is an ``InjectionKey["Communication"]`` named
``"communication"``. The plugin provides the bus at the Runtime root, and an
Agent retrieves it with ``agent.inject(communication_key)`` by searching up
the parent chain. If ``CommPlugin`` has not been installed, injection raises
``MissingProvideError``. Re-providing the same key replaces its current value;
the provide mechanism does not prevent collisions, so plugins should follow
the key-prefix naming convention.

The hook points are declared by ``use_comm()`` and dispatched by the handle's
receive callbacks. They are open for other extensions to register handlers
after declaration; an extension does not need to declare them again. The
``before_destroy`` cleanup handler is owned by ``"comm"`` and can be removed
with ``remove_by_owner("comm")``.

Without the plugin, the bus is not provided. For an Agent that has not opted
in with ``use_comm()``, there is no ``agent.comm_handler``, no ``on_signal``
or ``on_event`` hook point, and no endpoint registered on the bus. Accessing
either undeclared hook point raises ``UnknownHookPointError``. Calling
``use_comm()`` without the plugin raises ``MissingProvideError``.

The two communication channels are isolated from the conversation channel.
Signals and events are delivered to endpoint callbacks and hooks only; they
are not messages, message-tree nodes, persisted records, or LLM context. A
handler can explicitly bridge their contents into the conversation, but the
extension never does so automatically.

Endpoint IDs are semantic, globally unique names within the bus, not UUIDs.
Agent endpoints use the explicit ``name`` passed to ``use_comm()`` or fall
back to ``agent.node_id``; the semantic name belongs to the caller or
application layer rather than being stored as an Agent identity. Application
endpoints are registered with ``create_handle()``. Duplicate registration
raises ``DuplicateEndpointError`` without replacing the existing route. The
bus has no endpoint-enumeration or discovery API; an application-level
directory is responsible for discovery. The Cron extension does not use this
communication bus.

``send()`` returns immediately and cannot be replied to; a missing target
raises ``SignalDeliveryError``. ``request()`` waits for a correlated reply
and times out with ``SignalTimeoutError``. ``publish()`` is a tolerant,
fire-and-forget broadcast: one subscriber's failure does not interrupt other
subscribers or the publisher. ``reply()`` uses the reserved ``"_reply"``
signal type and completes the matching request without dispatching to signal
hooks. The bus fills ``SignalEnvelope.sender``, ``correlation_id``,
``reply_to``, and ``created_at``. For events, the bus fills ``created_at`` and
uses the ``publisher`` value already present in the event mapping, which
``CommHandle.publish()`` supplies from the handle identity. Callers supply the
signal ``type`` and ``payload`` or the event ``topic`` and mapping. Creation
times are timezone-naive UTC values; display code is responsible for
local-time conversion.

This is an in-process bus, not a cross-process or network transport. Envelopes
are passed by reference on the same event loop; the extension does not
promise a serialization format or large-endpoint scalability. The bus lives
for the Runtime lifetime. During normal shutdown, the Agent tree is destroyed
before the bus is closed. Agent handles clean up their own endpoints,
subscriptions, and pending requests on destruction; shutdown clears the bus
tables but cannot cancel pending requests belonging to handles it does not
own. Using ``CommHandle`` is the recommended way to preserve endpoint
identity. Any code can retrieve the bus through
``runtime.inject(communication_key)`` and call it directly with another
sender or publisher identity; this advanced path is permitted, but the caller
is responsible for supplying the correct identity.

.. rubric:: Usage example

.. code-block:: python

    from flowing import Agent, Runtime
    from flowing.plugins.comm import CommPlugin, use_comm

    # flowing.launch() calls this project entry point.
    async def main() -> Runtime:
        runtime = Runtime()
        runtime.install(CommPlugin())

        class CompanionAgent(Agent):
            async def setup(self):
                use_comm(self)

                @self.hooks.on_signal["agent_message"]
                def _(self, envelope):
                    # Signals and conversation messages are separate channels.
                    return envelope

        return runtime

.. seealso:: :mod:`flowing.plugins.comm.models`, :mod:`flowing.hooks`,
    :mod:`flowing.message`, :mod:`flowing.errors`,
    :mod:`flowing.plugins.cron`
"""
from typing import Any, Callable, ClassVar
from flowing.agent import Agent
from flowing.plugins import Plugin
from flowing.runtime import Runtime
from flowing.params import InjectionKey
from .models import EventEnvelope, SignalEnvelope

communication_key: InjectionKey["Communication"]
"""Runtime provide key named ``"communication"`` for the shared
``Communication`` bus. ``CommPlugin`` provides the bus at the Runtime root,
and Agents retrieve it with ``agent.inject(communication_key)`` along the
parent chain. Re-providing the same key replaces the prior value, following
the general provide-inject contract. Key conflicts are not blocked by the
mechanism; plugins avoid them by following the key-prefix convention. If the
plugin has not been installed, injection raises
``flowing.errors.MissingProvideError``.
"""
class Communication:
    """Single-process bus for endpoint signals and topic events.

    ``CommPlugin`` creates and provides the normal bus instance. It maintains
    endpoint callbacks and event subscriptions. It provides addressing,
    routing, correlation, and dispatch, but does not validate payloads or
    interpret business meaning. Subscription storage and broadcast dispatch
    belong to the bus; an endpoint such as ``CommHandle`` performs any further
    dispatch to Agent hooks or application logic. This lets Agent, UI, and
    system-component endpoints share the same mechanism. Direct construction
    creates an empty bus but does not put it in the Runtime's provide chain;
    applications ordinarily do not construct this class directly.

    ``send()`` returns immediately and reports a missing destination as
    ``SignalDeliveryError``. ``request()`` waits for a matching reply and may
    raise ``SignalTimeoutError`` or be cancelled if its reply handle is
    destroyed. ``reply()`` sends the reserved ``"_reply"`` signal to complete
    a matching request without dispatching to hooks. ``publish()`` is
    best-effort: it starts awaitable callbacks without waiting and logs
    callback exceptions. Repeating a subscription
    for the same subscriber and topic replaces its callback; removing a
    nonexistent subscription is a no-op, while unregistering a nonexistent
    endpoint raises ``KeyError``. Runtime shutdown clears endpoint and
    subscription tables; each handle is responsible for cancelling its own
    pending requests. The bus does not distinguish endpoint kinds and does
    not validate signal or event payloads.

    .. rubric:: Usage example

    .. code-block:: python

        from flowing.plugins.comm import CommPlugin, communication_key

        # ``runtime`` is the application's Runtime; the callback is application-defined.
        comm = runtime.inject(communication_key)
        ui_handle = comm.create_handle(endpoint_id="ui-main")
        ui_handle.subscribe("assistant_replied", on_assistant_replied)
        # Define on_assistant_replied as the UI callback.
    """
    def __init__(self) -> None:
        """Create an empty bus that is not automatically provided to a Runtime.

        Normal applications obtain the Runtime-scoped instance installed by
        ``CommPlugin``; application code ordinarily does not construct this
        class directly. A directly constructed bus starts with empty endpoint,
        subscription, and background-task collections and is not provided to a
        Runtime.
        """
        ...
    def register_endpoint(self, endpoint_id: str, handler: Callable[[SignalEnvelope], Any]) -> None:
        """Register a callback under an endpoint ID so the endpoint can receive signals.

        The callback is invoked directly for each delivered signal. If it
        returns an awaitable, the bus schedules it as a background task.
        Registration is not idempotent: an existing ID raises
        ``DuplicateEndpointError`` and its callback remains unchanged.
        The normal registration path is ``create_handle()`` or
        ``use_comm()``, which calls this method internally.

        :param endpoint_id: Unique, addressable endpoint identifier.
        :param handler: Callback receiving one ``SignalEnvelope``.
        :raises flowing.errors.DuplicateEndpointError: The ID is already registered.
        """
        ...
    def unregister_endpoint(self, endpoint_id: str) -> None:
        """Remove an endpoint from the routing table.

        After removal, ``send()`` or ``request()`` to the ID raises
        ``SignalDeliveryError``.
        Removing an unknown ID raises the unwrapped ``KeyError``. Endpoint
        removal is normally part of ``CommHandle.destroy()``; repeating it is
        treated as a programming error, while the handle itself guarantees
        idempotent cleanup. A request already waiting for a reply can still
        complete after its destination endpoint is removed, because replies
        are matched by correlation ID.

        :param endpoint_id: ID to remove.
        :raises KeyError: No endpoint has this ID.
        """
        ...
    def create_handle(self, endpoint_id: str, *, on_signal: Callable[[SignalEnvelope], Any] | None = None, on_event: Callable[[EventEnvelope], Any] | None = None) -> "CommHandle":
        """Create a handle and register its endpoint in one operation.

        The returned handle supplies its own sender and publisher identity.
        Missing callbacks default to no-ops, so received signals or events are
        ignored. A duplicate endpoint ID raises ``DuplicateEndpointError``
        without replacing the existing route; the unused handle is discarded
        and the route table is unchanged. The registered callback is the
        handle's receive entry point, which diverts the reserved ``"_reply"``
        type to pending-request matching and sends other signals to its
        configured signal callback. Both ``use_comm()`` and application-level
        endpoints use this method to create their handles.

        :param endpoint_id: Unique endpoint ID.
        :param on_signal: Optional signal callback; omitted signals are ignored.
        :param on_event: Optional event callback; omitted events are ignored.
        :return: Registered endpoint handle.
        :raises flowing.errors.DuplicateEndpointError: The ID is already registered.
        """
        ...
    def send(self, sender: str, target: str, type: str, payload: dict[str, Any]) -> None:
        """Send a point-to-point signal and return without awaiting processing.

        The bus builds a ``SignalEnvelope``, filling ``sender`` and
        ``created_at`` and setting ``correlation_id`` and ``reply_to`` to
        ``None``, then invokes the target's callback. A
        synchronous callback runs immediately; an awaitable result runs in a
        background task. Callback exceptions are logged rather than returned
        to the sender. The target's synchronous callback portion runs during
        routing; the sender does not wait for its asynchronous work. There is
        no retry or queue, and receiver failure is not compensated. A missing
        target is the only delivery failure reported synchronously to the
        sender. ``sender`` should name a registered endpoint, but the bus does
        not check it; direct bus calls can therefore claim another identity.

        :param sender: Sender endpoint ID.
        :param target: Destination endpoint ID.
        :param type: Application-defined signal type; ``"_reply"`` is reserved.
        :param payload: Opaque application payload.
        :raises flowing.errors.SignalDeliveryError: The target is not registered.
        """
        ...
    async def request(self, sender: str, target: str, type: str, payload: dict[str, Any], reply_handler: "CommHandle", timeout: float | None = None) -> dict[str, Any]:
        """Send a signal and wait for the reply correlated to this request.

        The bus generates an eight-character random hexadecimal
        ``correlation_id``, registers a pending future on ``reply_handler``,
        and sends an envelope with ``reply_to=sender``. The reply payload is
        returned directly. Replies use the reserved ``"_reply"`` type and are
        consumed by the handle instead of entering application signal hooks.
        Correlation uses the explicit ID rather than the call stack, so the
        other endpoint can reply across asynchronous boundaries. A timeout
        removes the pending registration and later replies are dropped with a
        warning; destroying ``reply_handler`` cancels the wait. With
        ``timeout=None``, only destruction of that handle ends the wait.
        Concurrent requests use separate correlation IDs and do not interfere.

        :param sender: Sender endpoint ID.
        :param target: Destination endpoint ID.
        :param type: Application-defined request type.
        :param payload: Opaque request payload.
        :param reply_handler: Handle that receives and matches the reply.
        :param timeout: Maximum wait in seconds, or ``None`` for no timeout.
        :return: Payload from the matching reply.
        :raises flowing.errors.SignalDeliveryError: The target is not registered.
        :raises flowing.errors.SignalTimeoutError: No reply arrives before the timeout.
        :raises asyncio.CancelledError: The reply handler is destroyed while waiting.
        """
        ...
    def reply(self, sender: str, target: str, correlation_id: str, payload: dict[str, Any]) -> None:
        """Deliver a reserved reply envelope to a pending request.

        A matching handle completes its pending request with ``payload``;
        the reply is not dispatched to application hooks. A missing or
        already-finished request, or a target that has been unregistered,
        causes a warning and drops the reply rather than raising a delivery
        error. Reply signals are internal completion events: they are not
        dispatched, enqueued, or exposed to hooks.

        :param sender: Replying endpoint ID.
        :param target: Requesting endpoint ID.
        :param correlation_id: ID from the original request.
        :param payload: Reply payload returned to the requester.
        """
        ...
    def publish(self, topic: str, event: dict[str, Any]) -> None:
        """Broadcast an event to the subscribers currently registered for a topic.

        The bus creates an ``EventEnvelope`` with ``created_at`` and takes
        ``publisher`` from the supplied event mapping, if present. Synchronous
        callbacks run before this method returns; awaitable callbacks are
        scheduled and are not awaited. An exception from one callback is
        logged and does not stop delivery to the others. Publishing to a topic
        with no subscribers is a no-op. The publisher receives no result, and
        the bus does not retry or send failed deliveries to a dead-letter path.

        :param topic: Event topic.
        :param event: Opaque event mapping. Direct bus publication uses its
            existing ``publisher`` value, if any; ``CommHandle.publish()``
            supplies the handle identity before calling this method.
        """
        ...
    def subscribe(self, subscriber_id: str, topic: str, callback: Callable[[EventEnvelope], Any]) -> None:
        """Register or replace one subscriber's callback for a topic.

        The subscription applies to the next publication. Repeating the same
        subscriber/topic pair replaces its previous callback. The subscriber
        ID need not name a registered endpoint when the bus API is used
        directly.

        The bus owns the subscription table and broadcast delivery. Any
        second-stage dispatch performed by the callback, such as dispatch to
        ``agent.hooks`` or custom application logic, belongs to the endpoint.

        :param subscriber_id: Subscriber identity.
        :param topic: Event topic.
        :param callback: Callback receiving an ``EventEnvelope``.
        """
        ...
    def unsubscribe(self, subscriber_id: str, topic: str) -> None:
        """Remove one subscriber's callback for a topic.

        Removing an absent subscription is a no-op, which makes repeated
        cleanup safe.

        :param subscriber_id: Subscriber identity.
        :param topic: Event topic.
        """
        ...
    def unsubscribe_all(self, subscriber_id: str) -> None:
        """Remove all subscriptions owned by one subscriber.

        If the subscriber has no subscriptions, this method does nothing.

        :param subscriber_id: Subscriber identity whose subscriptions are removed.
        """
        ...
    def _close(self) -> None:
        """Clear the endpoint and subscription tables during Runtime shutdown.

        Agent handles normally clean themselves up first. This final bus
        cleanup does not cancel pending requests: the bus does not retain a
        handle list, so each handle is responsible for cancelling its own
        pending calls in ``destroy()``. An application-created handle that
        was not destroyed may remain pending until timeout or event-loop
        cleanup. This is an internal, non-stable API.
        """
        ...
class CommHandle:
    """Identity-bearing operations handle for one communication endpoint.

    Agent and non-Agent endpoints use this same class; it has no subclasses.
    ``Communication.create_handle()`` registers the endpoint and returns its
    handle. Sending methods fill the sender identity, event publication fills
    the publisher identity, and subscription methods use this endpoint as the
    subscriber ID. The methods ``send()``, ``request()``, and ``publish()``
    carry identity; ``subscribe()`` and ``unsubscribe()`` use the handle's
    endpoint ID. Agent handles receive callbacks through closures that dispatch
    to the Agent's hooks; application handles may instead use custom callbacks.

    ``endpoint_id`` is fixed when the handle is created. ``destroy()`` removes
    this endpoint and all of its subscriptions, cancels requests awaiting
    replies on this handle with ``asyncio.CancelledError``, and clears its
    pending-request table. It is idempotent and does not destroy other
    endpoints or invoke hooks. After destruction, sending to this endpoint
    fails, while sending from the destroyed handle to another live endpoint
    still works because the bus does not validate the sender. Reuse of a
    destroyed handle is not recommended.

    .. rubric:: Usage example

    .. code-block:: python

        from flowing import Agent
        from flowing.plugins.comm import CommPlugin, communication_key, use_comm

        # Agent endpoint, enabled by use_comm().
        self.comm_handler.send(
            target="payment-agent",
            type="query",
            payload={"subject": "reconciliation request"},
        )

        # Non-Agent endpoint, such as a UI component; ``runtime`` is its host Runtime.
        comm = runtime.inject(communication_key)
        ui_handle = comm.create_handle(endpoint_id="ui-main")
        ui_handle.subscribe("assistant_replied", on_assistant_replied)
    """
    endpoint_id: str
    """Endpoint identifier assigned when the handle is created. Sending,
    publishing, and subscribing use this value as the handle's identity.
    """
    def __init__(self, endpoint_id: str, comm: Communication, *, on_signal: Callable[[SignalEnvelope], Any] | None = None, on_event: Callable[[EventEnvelope], Any] | None = None) -> None:
        """Create a handle without registering its endpoint.

        ``Communication.create_handle()`` performs endpoint registration.
        Missing callbacks are replaced with no-op callbacks, so the handle
        ignores signals or events for which no callback was supplied.
        This constructor does not register the endpoint or declare hook
        points. Direct construction is an advanced path; normal callers use
        ``Communication.create_handle()`` to register it consistently.

        :param endpoint_id: Endpoint identity for this handle.
        :param comm: Communication bus used by the handle.
        :param on_signal: Optional callback for received signals.
        :param on_event: Optional callback for events to which the handle
            subscribes.
        """
        ...
    def send(self, target: str, type: str, payload: dict[str, Any]) -> None:
        """Send a signal using this handle's endpoint ID as the sender.

        The call returns without waiting for the target to finish processing.
        A missing target raises ``SignalDeliveryError``; callback failures are
        handled by the bus and are not returned to this sender.

        .. rubric:: Usage example

        .. code-block:: python

            self.comm_handler.send(
                target="ui-main",
                type="status_update",
                payload={"state": "thinking"},
            )

        :param target: Destination endpoint ID.
        :param type: Application-defined signal type.
        :param payload: Opaque signal payload.
        :raises flowing.errors.SignalDeliveryError: The target is not registered.
        """
        ...
    async def request(self, target: str, type: str, payload: dict[str, Any], *, timeout: float | None = None) -> dict[str, Any]:
        """Send a request from this endpoint and wait for its matching reply.

        The bus generates an eight-character random hexadecimal
        ``correlation_id`` and sets ``reply_to`` to this endpoint's ID. A reply
        payload is returned directly. The reply envelope does not pass through
        signal hooks. Destroying this handle while the request is pending
        cancels the wait; ``timeout=None`` waits without a time limit, until
        the handle is destroyed.

        .. rubric:: Usage example

        .. code-block:: python

            from flowing import Agent
            from flowing.errors import Intercepted, SignalTimeoutError
            from flowing.plugins.comm import use_comm

            class GuardrailAgent(Agent):
                def setup(self):
                    use_comm(self)
                    self.hooks.before_tool_call(self.check_dangerous, by="guardrail")

                async def check_dangerous(self, tool_call):
                    if not is_dangerous(tool_call):  # Define this policy in your application.
                        return tool_call
                    try:
                        result = await self.comm_handler.request(
                            target="ui-main",
                            type="permission_request",
                            payload={"tool_name": tool_call.name,
                                     "tool_args": tool_call.args},
                            timeout=120.0,
                        )
                    except SignalTimeoutError:
                        # Choose user-facing text for the application's locale.
                        raise Intercepted("Approval timed out")
                    if result.get("approved"):
                        return tool_call
                    raise Intercepted(result.get("reason", "User denied the request"))

        :param target: Destination endpoint ID.
        :param type: Application-defined request type.
        :param payload: Opaque request payload.
        :param timeout: Maximum wait in seconds, or ``None`` for no timeout.
        :return: Payload from the matching reply.
        :raises flowing.errors.SignalDeliveryError: The target is not registered.
        :raises flowing.errors.SignalTimeoutError: No reply arrives before the timeout.
        :raises asyncio.CancelledError: This handle is destroyed while waiting.
        """
        ...
    def reply(self, in_reply_to: SignalEnvelope, payload: dict[str, Any]) -> None:
        """Reply to a request signal received by this endpoint.

        The reply is routed using the request's correlation ID and
        ``reply_to`` address, falling back to ``sender`` when ``reply_to`` is
        ``None``. It completes the requester's pending call without entering
        signal-hook dispatch. The input should be an envelope created by
        ``request()``; replying to a ``send()`` envelope has no matching
        pending call and the reply is dropped with a warning.

        .. rubric:: Usage example

        .. code-block:: python

            from flowing.plugins.comm.models import SignalEnvelope

            @self.hooks.on_signal["permission_request"]
            def _(self, envelope: SignalEnvelope):
                approved = envelope.payload["tool_name"] in self.allowed_tools
                self.comm_handler.reply(envelope, {"approved": approved})
                return envelope
            # self.allowed_tools is application-owned; this snippet shows the reply call.

        The input is expected to be a request envelope with a non-``None``
        ``correlation_id``. The method does not validate the reply payload;
        both endpoints define its structure by agreement. If the target
        endpoint has already been unregistered, the reply is dropped with a
        warning rather than raising a delivery error.

        :param in_reply_to: Request envelope to answer.
        :param payload: Reply payload, whose structure is application-defined.
        """
        ...
    def publish(self, topic: str, event: dict[str, Any]) -> None:
        """Publish an event with this endpoint as its publisher.

        The handle calls ``Communication.publish()`` with a merged mapping
        whose ``publisher`` is this endpoint ID, overriding any value supplied
        by the caller. Delivery is tolerant and fire-and-forget: synchronous
        callbacks run during publication, awaitable callbacks run as background
        tasks, and publishing without subscribers is a no-op.

        :param topic: Event topic.
        :param event: Event data.
        """
        ...
    def subscribe(self, topic: str, callback: Callable[[EventEnvelope], Any] | None = None) -> None:
        """Subscribe this endpoint to a topic.

        If ``callback`` is omitted, the handle uses its configured event
        callback. For an Agent endpoint enabled by ``use_comm()``, that
        callback dispatches to ``agent.hooks.on_event``. Subscribing again to
        the same topic replaces the prior callback.

        :param topic: Event topic.
        :param callback: Optional callback; when omitted, use the handle's
            configured event callback.
        """
        ...
    def unsubscribe(self, topic: str) -> None:
        """Remove this endpoint's subscription to a topic.

        Removing an absent subscription does nothing.

        :param topic: Event topic.
        """
        ...
    def destroy(self) -> None:
        """Remove this handle's endpoint and subscriptions and cancel its pending requests.

        Cleanup cancels all of this handle's subscriptions, unregisters its
        endpoint, cancels requests awaiting replies with
        ``asyncio.CancelledError``, and clears its pending-request table. The
        first call performs the cleanup; later calls are no-ops, including if
        destruction is triggered more than once by lifecycle cleanup. Other
        endpoints and subscriptions are unaffected, and destruction does not
        invoke hooks. The Agent's ``before_destroy`` hook is a separate
        observation point.
        """
        ...
class CommPlugin(Plugin):
    """Create and provide the Runtime-scoped communication bus.

    ``install()`` creates one ``Communication`` instance and provides it
    under ``communication_key``. Agents can then enable endpoint behavior
    with ``use_comm()``. The plugin registers no tools, configuration
    namespace, or hook points. The core package does not install this plugin
    by default; without it, the communication extension is absent. ``use_comm()``
    separately declares Agent hook points and registers Agent-local cleanup.
    Installing a second plugin with this name in the same Runtime raises
    ``ValueError``. During shutdown the plugin clears the bus's endpoint and
    subscription tables; each handle is responsible for cancelling its own
    pending requests.
    """
    name: ClassVar[str]
    """Explicitly declared Runtime-local registration name, set to ``"comm"``.

    The framework does not assign a default name; plugin names follow the
    naming convention documented by ``flowing.plugins``.
    """
    dependencies: ClassVar[list[str]]
    """Declared dependencies. This plugin has none.

    The Runtime checks that declared dependencies exist and form an acyclic
    dependency graph when ``install()`` runs (see
    ``Runtime._check_dependencies()``).
    """
    def install(self, runtime: Runtime) -> None:
        """Create the bus and provide it under ``communication_key``.

        This method only registers a provide value and returns synchronously.
        Once installed, the bus is ready for Agents to retrieve through
        injection; plugin shutdown later closes it.

        :param runtime: Runtime installing the plugin.
        """
        ...
    async def shutdown(self) -> None:
        """Clear the bus's endpoint and subscription tables during shutdown.

        Agent handles are normally destroyed before this method runs. This is
        a final cleanup of the bus tables and does not cancel pending requests
        owned by handles that were not destroyed, such as application-created
        handles.
        """
        ...
def use_comm(agent: Agent, *, name: str | None = None) -> None:
    """Enable an Agent as a communication endpoint during its setup().

    This retrieves the Runtime bus, registers an endpoint named by ``name``
    or ``agent.node_id``, stores its handle as ``agent.comm_handler``,
    declares ``on_signal`` with ``by="comm"`` and ``match_on="type"`` and
    ``on_event`` with ``by="comm"`` and ``match_on="topic"``, and registers
    a ``before_destroy`` cleanup handler owned by ``"comm"``. The handle's
    receive callbacks dispatch signals and events to the corresponding Agent
    hooks. An event reaches ``on_event`` through a subscription to its topic.
    These callbacks dispatch asynchronously; ``send()`` and ``publish()`` do
    not wait for Agent hook handlers to finish. ``Intercepted`` stops the
    current hook chain and is consumed by the communication callback; there is
    no further communication-side action. Other dispatch exceptions are
    logged rather than returned to the sender or publisher. Received envelopes
    are not automatically added to the Agent's conversation; handlers must
    bridge them explicitly when desired. This function does not register
    tools or modify the prompt.

    The ``CommPlugin`` must already be installed. Call this from ``setup()``
    so the hook declarations are made at the supported lifecycle point. If an
    endpoint ID is already registered, setup raises ``DuplicateEndpointError``
    without replacing the existing endpoint. Calling this again with the same
    ID reuses the handle without registering another endpoint or cleanup
    handler; using a different ID creates another handle and
    replaces ``agent.comm_handler`` while the old handle remains registered
    until Agent destruction, at which point both handles are cleaned up.
    Hook-point declaration is idempotent for the same name and owner, so
    setup can declare the points again on a restored instance. Once declared,
    other code can register handlers without declaring the points again.
    Handlers are registered with ``fnmatch`` patterns for ``envelope.type``
    or ``envelope.topic`` (for example,
    ``@agent.hooks.on_signal["<pattern>"]``) and use the signature
    ``(agent, envelope) -> envelope``. A handler can raise ``Intercepted`` to
    stop later handlers in that dispatch chain.

    The handle is always attached as ``agent.comm_handler``; the attribute
    name follows the plugin-name prefix convention and cannot be customized.
    Repeating ``use_comm()`` on the same instance with the same endpoint ID
    reuses its handle. During recovery, ``setup()`` on a new instance can
    register safely: a process restart creates a new bus, while same-process
    recovery requires the old instance to be destroyed first so its endpoint
    is unregistered. After setup, the Agent is addressable through the bus,
    and its endpoint, subscriptions, and pending requests are cleaned up on
    destruction.

    .. rubric:: Usage example

    .. code-block:: python

        from flowing import Agent, Message, MessageKind, TextBlock
        from flowing.plugins.comm.models import SignalEnvelope

        class CompanionAgent(Agent):
            async def setup(self, data_dir: str):
                use_comm(self)

                @self.hooks.on_signal["agent_message"]
                async def _(self, envelope: SignalEnvelope):
                    await self.enqueue_message(Message(
                        kind=MessageKind.PEER,
                        source=f"agent:{envelope.sender}",
                        content=[TextBlock(text=envelope.payload["body"])],
                        tags=["internal_message"],
                    ))
                    return envelope

    :param agent: Agent to enable.
    :param name: Optional endpoint ID; defaults to ``agent.node_id``.
    :raises flowing.errors.MissingProvideError: The communication plugin is not installed.
    :raises flowing.errors.DuplicateEndpointError: The selected endpoint ID is already in use.
    """
    ...
