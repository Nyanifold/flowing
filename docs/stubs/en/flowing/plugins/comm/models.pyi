"""Signal and event envelopes used by the in-process communication extension.

``SignalEnvelope`` carries one point-to-point signal, while ``EventEnvelope``
carries one publish-subscribe event. The communication bus creates these
objects for delivery to callbacks and Agent hooks. They are transport values,
not conversation ``Message`` objects: they do not enter an Agent's message
tree, persistence stream, or LLM context unless application code explicitly
bridges their contents.

.. seealso:: :mod:`flowing.plugins.comm`,
    :mod:`flowing.plugins.comm.comm`, :class:`flowing.message.Message`
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SignalEnvelope:
    """Carry a point-to-point signal and its optional reply-correlation data.

    The bus supplies the sender identity and creation time when it constructs
    a signal. A request also carries an eight-character random hexadecimal
    correlation ID and a reply address; a
    plain send carries neither. Replies use the reserved ``"_reply"`` type
    and are consumed by the receiving handle rather than dispatched to
    application signal hooks.

    The bus treats ``payload`` as opaque application data. It does not
    validate, modify, or serialize the mapping. Constructed envelopes are
    passed by reference to the receiving callback. If application code
    constructs an envelope directly, it is responsible for its metadata; the
    bus does not validate or repair it.

    .. rubric:: Usage example

    .. code-block:: python

        @agent.hooks.on_signal["permission_request"]
        def review_request(agent, envelope: SignalEnvelope):
            approved = envelope.payload["tool_name"] in agent.allowed_tools
            agent.comm_handler.reply(envelope, {"approved": approved})
            return envelope
        # allowed_tools is application-defined; this illustrates reading and replying.

    .. seealso:: :class:`EventEnvelope`, :class:`flowing.plugins.comm.CommHandle`,
        :meth:`flowing.plugins.comm.CommHandle.request`
    """

    sender: str
    """Sending endpoint ID. A ``CommHandle`` supplies its own endpoint ID;
    callers using ``Communication.send()`` directly choose this value and the
    bus does not verify that it names a registered endpoint.
    """
    type: str
    """Application-defined signal type, used by the ``on_signal`` hook's
    ``match_on="type"`` filter with ``fnmatch`` matching. The value
    ``"_reply"`` is reserved for request replies and is not dispatched to
    application hooks.
    """
    payload: dict[str, Any]
    """Opaque application payload. The bus does not validate, modify, or
    serialize it.
    """
    correlation_id: str | None = None
    """Request/reply correlation identifier: an eight-character random
    hexadecimal string generated for ``request()`` envelopes. Plain
    ``send()`` envelopes have ``None``. A reply carries the request's
    identifier so its waiting handle can match the response.
    """
    reply_to: str | None = None
    """Endpoint to which a reply should be routed. The bus sets this to the
    request sender for ``request()`` envelopes. ``CommHandle.reply()`` falls
    back to ``sender`` when this field is ``None``.
    """
    created_at: datetime = ...
    """Creation time, filled by the dataclass default factory as a
    timezone-naive UTC ``datetime``. It supports audit, ordering, and latency
    measurements; display code is responsible for converting to local time.
    """


@dataclass
class EventEnvelope:
    """The only carrier for an Event-channel publish/subscribe event.

    .. rubric:: What it does

    It carries all information for one topic broadcast: the topic, event data,
    publisher identity, and creation time. It is the value type passed to
    ``on_event`` hook handlers and the sole argument to subscriber callbacks.

    Unlike :class:`SignalEnvelope`, an Event is a one-to-many broadcast with
    no reply. It has no ``correlation_id`` or ``reply_to``. Its additional
    ``publisher`` field is injected by ``CommHandle.publish()``, allowing
    subscribers to identify the publisher without trusting the payload.

    .. rubric:: Usage example

    .. code-block:: python

        # Register the UI endpoint in the application after phase-one
        # setup with runtime.install(CommPlugin()). ``runtime`` is a Runtime.
        comm = runtime.inject(communication_key)
        ui_handle = comm.create_handle(endpoint_id="ui-main")

        def on_assistant_replied(envelope: EventEnvelope) -> None:
            render_message(envelope.event["text"])  # Application UI callback.

        ui_handle.subscribe("assistant_replied", on_assistant_replied)

    .. rubric:: Behavior notes

    - The bus or handle fills ``publisher`` and ``created_at`` on the publish
      path. Publishing through ``CommHandle.publish()`` sets ``publisher`` to
      that handle's ``endpoint_id``, overriding a caller-supplied
      ``publisher`` key in ``event``. Direct bus publication may leave it as
      ``None``; that advanced use is responsible for identity correctness.
    - All subscribers receive the same envelope by reference for one
      ``publish()`` call. Subscribers should not mutate ``event`` because
      later subscribers would observe the mutation.
    - Publishing to a topic with no subscribers is a valid no-op.
    - No ordering guarantee is made beyond traversal of the subscription
      table: callbacks run synchronously in that order, while callbacks that
      return awaitables are scheduled as background tasks and are not awaited
      by the publisher.
    - ``created_at`` is a timezone-naive UTC ``datetime``.

    .. seealso:: :class:`SignalEnvelope`,
        :meth:`CommHandle.publish`,
        :meth:`flowing.plugins.comm.Communication.subscribe`
    """

    topic: str
    """Event topic, used by the ``on_event`` hook's ``match_on="topic"``
    filter.
    """
    event: dict[str, Any]
    """Opaque event mapping. Direct ``Communication.publish()`` uses the
    mapping's existing ``publisher`` value, if any. ``CommHandle.publish()``
    supplies a merged mapping whose ``publisher`` value is the handle's
    endpoint ID.
    Subscribers receive the same envelope and mapping for one publication.
    """
    publisher: str | None = None
    """Publishing endpoint ID, when supplied. ``CommHandle.publish()`` sets
    this to the handle's endpoint ID; direct bus publication uses the mapping's
    existing ``publisher`` value, if any.
    """
    created_at: datetime = field(default_factory=...)
    """Creation time, filled by the dataclass default factory as a
    timezone-naive UTC ``datetime``. It supports audit, ordering, and latency
    measurements; display code is responsible for converting to local time.
    """
