"""Define messages, queue scheduling, and operations on the message tree.

.. rubric:: Overview

This module defines Flowing's object-level message model, its priority queue,
and the :class:`MessageChain` interface for editing message history. A
:class:`Message` represents input from a user, provider, tool, peer agent,
system event, or extension. Messages are nodes in a forest: ``id`` identifies
the node and ``parent_id`` links it to its parent. ``Agent.current_head_id``
selects the active view of that history.

Messages do not have a provider-specific ``role`` field. Provider adapters map
``Message.kind`` to the role and content shape required by their APIs. A
message's ``content`` is an ordered list of :class:`ContentBlock` objects;
text, reasoning, tool calls, structured values, and media can coexist in one
message.

.. rubric:: Message fields

.. list-table:: Message field meanings
   :header-rows: 1
   :widths: 20 80

   * - Field
     - Meaning
   * - ``id``
     - Agent-scoped increasing numeric string used as the message and tree-node identifier. If omitted, the owning Agent assigns it at its boundary; its sequence continues across recovery.
   * - ``parent_id``
     - Parent message ID set when the message is attached to the tree. ``None`` marks a root; multiple roots are allowed.
   * - ``kind``
     - One of the seven :class:`MessageKind` values. It identifies the source, not a provider API role.
   * - ``content``
     - Ordered :class:`ContentBlock` values; block types may be interleaved.
   * - ``turn_end``
     - Marks the PROVIDER message that closes a logical turn.
   * - ``partial``
     - Marks content retained from an interrupted streaming response.
   * - ``synthetic``
     - Marks a placeholder TOOL message created during recovery to close an orphaned tool call; it is not a real tool result.
   * - ``tool_call_id``
     - Pairing ID on a TOOL message, matching a provider ``ToolCallBlock.id`` on the same branch.
   * - ``tool_status``
     - One of ``completed``, ``pending``, ``blocked``, ``cancelled``, or ``error`` on a TOOL message.
   * - ``source``
     - Free-form secondary classification supplied by the message producer.
   * - ``tags``
     - Labels for grouping, filtering, and cleanup.
   * - ``priority``
     - Queue ordering value from :class:`MessagePriority`.
   * - ``timestamp``
     - Naive UTC time attached at construction for observation and ordering. Adapters decide whether to send it to a provider.
   * - ``usage``
     - Measured provider usage attached to a PROVIDER message and retained across persistence and recovery.

Provider adapters map message kinds to API roles as follows:

.. list-table:: Message-kind to provider-role mapping
   :header-rows: 1

   * - Kind
     - Anthropic
     - OpenAI
     - Gemini
   * - ``USER``
     - ``user``
     - ``user``
     - ``user``
   * - ``PROVIDER``
     - ``assistant``
     - ``assistant``
     - ``model``
   * - ``TOOL``
     - ``user`` with a tool result
     - ``tool``
     - ``tool``
   * - ``SYSTEM``
     - ``user`` with adapter-defined XML wrapping
     - ``system``
     - ``user``
   * - ``PEER`` and ``EVENT``
     - ``user`` with adapter-defined XML wrapping
     - ``user``
     - ``user``
   * - ``SUBAGENT``
     - Separate messages with adapter-defined XML wrapping
     - ``user``
     - ``user``

For OpenAI, extension and subagent results use the ``user`` role because they are
external input; they are not promoted to ``developer`` or fabricated as
``assistant`` output. The adapters choose the XML representation for
``SYSTEM``, ``PEER``, ``EVENT``, and ``SUBAGENT``.

``MessageChain`` is the supported path for editing persisted history. The
queue orders messages by priority and preserves FIFO order within each
priority band. The Agent's default dequeue batch consists of the consecutive
``INTERRUPT``/``STEER`` messages at the queue head followed by the first
non-urgent message; if the head is non-urgent, the batch has one message.
Starvation prevention, source weighting, and broader batching policies belong
to the Agent or application.

.. rubric:: Example

.. code-block:: python

    message = Message(
        kind=MessageKind.USER,
        content=[TextBlock(text="Please check order 4521.")],
        source="chat_input",
        tags=["order"],
    )
    message_id = await agent.enqueue_message(message)

    agent.chain.insert(
        "m5",
        Message(kind=MessageKind.SYSTEM,
                content=[TextBlock(text="Check the latest order status.")]),
    )

.. rubric:: Behavior

- ``MessageKind`` is mapped to provider roles by the adapter. A block-type
  combination is typical, not a restriction enforced by this module; adapters
  validate whether a provider accepts a particular combination.
- USER, EVENT, PEER, SUBAGENT, and optionally SYSTEM messages can enter
  the queue. Asynchronous tool results enter as EVENT messages. PROVIDER
  messages are produced inside a turn and do not enter the queue. Synchronous
  TOOL results are attached directly to the tree.
- ``SYSTEM`` can also be represented as a system-prompt segment during context
  assembly; that path does not enqueue a message or start a turn. A queued
  message kind does not by itself bypass a provider call.
- On streaming interruption, accumulated content is retained with
  ``partial=True``. Provider deltas themselves are not persisted.
- Media blocks contain base64 data. This module does not impose a file-size
  limit; applications can validate uploads, use ``on_enqueue`` hooks, or rely
  on provider adapters to report context-length errors.
- Do not mutate persisted message objects directly. Use :class:`MessageChain`
  operations or change the active branch with ``Agent.fork``.

.. seealso:: :class:`flowing.agent.Agent`, :class:`flowing.agent.TurnContext`,
   :mod:`flowing.tool`, :mod:`flowing.context`, :mod:`flowing.persistence`
"""
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from flowing.agent import Agent
    from flowing.providers import Usage
class MessageKind(Enum):
    """Identify the source of a message with a stable seven-value enum.

    Message kinds describe where information came from, not a conversational
    role. Provider adapters map them to API roles; user interfaces choose their
    own rendering based on the kind and message tags.

    ``PROVIDER`` is the common source for model output, including multimodal
    output. ``SUBAGENT`` identifies delegated subagent results separately from
    tool results, so those messages are not merged into TOOL results. Extensions
    deliver messages as ``EVENT`` with a descriptive ``source``.

    .. rubric:: Behavior

    Enum values serialize as strings, and ``from_record`` restores them
    strictly against the current enum. Typical content is illustrative rather
    than enforced: this module does not reject a block
    type based on its message kind. ``PEER`` is an intentional message from
    another Agent, commonly sent through ``message_to``, ``agent_delegate``, or
    ``agent_steer``. ``EVENT`` records an occurrence outside an Agent, such as
    a scheduled task, plugin event, or asynchronous tool result. Hooks such as
    ``before_turn`` can use this distinction to make source-specific decisions.
    Synchronous tool results are attached directly as paired TOOL messages;
    asynchronous final results are queued as EVENT messages and do not use
    tool-call pairing metadata. SUBAGENT results remain separate messages rather
    than being merged into TOOL results. Extensions can use EVENT messages with
    a descriptive ``source``.

    Typical block combinations are shown below. They are conventions, not
    validation rules: the core accepts any block type, and the provider adapter
    reports combinations unsupported by its API.

    .. list-table:: Typical content blocks by message kind
       :header-rows: 1

       * - Kind
         - Typical blocks
       * - ``USER``
         - ``text``, ``image``, or ``file``; text may be mixed with media
       * - ``PROVIDER``
         - ``thinking``, ``text``, and ``tool_call`` in mixed order
       * - ``TOOL``
         - ``text``, ``struct``, and media blocks; no provider protocol blocks
       * - ``SYSTEM``
         - ``text``
       * - ``PEER``
         - ``text`` or ``file``
       * - ``EVENT``
         - ``text``, ``image``, or ``struct``; tool results may combine a label and result

    ``kind`` does not define UI presentation, and Flowing does not enumerate
    the free-form ``source`` field. The core does not validate which block
    types a kind may contain; the provider adapter validates combinations
    against its API.

    .. rubric:: Example

    .. code-block:: python

        user_message = Message(
            kind=MessageKind.USER,
            content=[TextBlock(text="Hello")],
        )
        event = Message(
            kind=MessageKind.EVENT,
            source="tool_result",
            content=[TextBlock(text="<label>"), StructBlock(data={...})],
        )

    .. seealso:: :class:`Message`, :class:`MessageQueue`, :mod:`flowing.model`
    """
    USER = "user"
    """Input supplied by a user. It is queued and maps to the provider's user role."""
    PROVIDER = "provider"
    """Output returned by a model provider. It is produced within a logical turn and is not queued."""
    TOOL = "tool"
    """Synchronous tool result attached directly to the tree and paired with a provider tool call."""
    SYSTEM = "system"
    """Framework or Composable context, either queued to start a turn or inserted during context assembly."""
    PEER = "peer"
    """A message intentionally sent by another Agent instance."""
    EVENT = "event"
    """An external occurrence, including scheduled work, plugin events, reminders, and asynchronous tool results."""
    SUBAGENT = "subagent"
    """A subagent's result, kept separate from ordinary tool results and queued as a message."""
class MessagePriority(IntEnum):
    """Priority used by :class:`MessageQueue`; lower numeric values come first.

    The order is ``INTERRUPT`` (0), ``STEER`` (1), ``HIGH`` (2), ``NORMAL``
    (3), and ``LOW`` (4). Messages with equal priority are dequeued in
    insertion order.

    Using ``IntEnum`` with smaller values representing higher priority lets
    the queue sort by ordinary numeric value and enqueue sequence. ``INTERRUPT``
    is used to preempt an active turn. ``STEER`` is weaker: a message enqueued
    during a turn is absorbed into that turn and is visible to its provider
    call without interrupting it. ``HIGH`` is suitable for urgent notices that
    do not need to interrupt the current turn.

    .. rubric:: Example

    .. code-block:: python

        # A parent Agent steers a child: the src/ directory changed; reread it.
        # The message is visible in this turn's context without interrupting it.
        steer = Message(
            kind=MessageKind.PEER,
            content=[TextBlock(text="The src/ directory has changed; please reread it before continuing.")],
            source="agent_steer",
            priority=MessagePriority.STEER,
        )
        await child_agent.enqueue_message(steer)

    .. rubric:: Behavior

    - Queue ordering uses ascending enum values and FIFO order within a
      priority; ``Message`` objects themselves are not comparable.
    - The Agent implements active-turn behavior. An ``INTERRUPT`` enqueued
      during a turn interrupts it and is attached to the tree for the next
      turn's context. A ``STEER`` does not interrupt and is visible in the
      current turn's context. Ordinary dequeue operations only observe order.
    - Priority does not prevent starvation or weight messages by source.
      Applications can define those policies by overriding
      ``Agent._dequeue()``.
    - ``Agent.side_query`` messages bypass the queue, and PROVIDER messages
      are produced within a turn rather than enqueued; priority has no
      scheduling effect on either kind.
    - The enum's numeric value is persisted with each message. Changing a
      value therefore changes the scheduling meaning of stored messages.

    .. seealso:: :class:`MessageQueue`, :meth:`flowing.agent.Agent.enqueue_message`
    """
    INTERRUPT = 0
    """Interrupt priority. A message enqueued during an active turn interrupts it."""
    STEER = 1
    """Steering priority, weaker only than ``INTERRUPT``. A message enqueued during an active turn is absorbed into that turn without interrupting it."""
    HIGH = 2
    """Urgent queue priority that does not itself interrupt an active turn."""
    NORMAL = 3
    """Default message priority."""
    LOW = 4
    """Lowest queue priority."""
@dataclass(kw_only=True)
class ContentBlock:
    """Base class for one typed part of a message's content.

    The ``type`` field distinguishes text, reasoning, tool calls, structured
    data, images, video, audio, and files. A message can interleave different
    block types. This common representation is independent of provider request
    formats; adapters translate it to each API.

    Media data is represented as base64 within the message, regardless of
    whether its original source was a path, URL, or buffer. ``mime_type`` is
    optional; adapters may infer it from the name or content when absent.

    .. rubric:: Behavior

    The framework core does not validate that a block's payload matches its
    declared type, impose file-size limits, scan for malware, transcode media,
    or generate thumbnails. Adapters and applications decide how unsupported
    media is handled.

    .. seealso:: :class:`Message`, :mod:`flowing.model`
    """
    type: str = ""
    """Discriminator identifying the concrete content-block type."""
@dataclass(kw_only=True)
class TextBlock(ContentBlock):
    """A plain, directly readable text fragment.

    String input to ``Agent.message`` is wrapped as a ``TextBlock``. Text can
    appear in any message kind and can be interleaved with other block types.
    Empty text is valid; this class does not impose content-length or review
    policies.

    .. rubric:: Example

    .. code-block:: python

        message = Message(
            kind=MessageKind.USER,
            content=[TextBlock(text="Please check my order.")],
        )

    .. seealso:: :class:`ContentBlock`, :meth:`flowing.agent.Agent.message`
    """
    type: Literal["text"] = "text"
    """Discriminator fixed to ``"text"``."""
    text: str
    """Readable text content, serialized as text without transformation."""
@dataclass(kw_only=True)
class ThinkingBlock(ContentBlock):
    """A provider's reasoning or thinking content.

    Providers may return this as an Anthropic ``thinking`` block or OpenAI
    ``reasoning_content``. It is usually part of a PROVIDER message and remains
    a distinct block for persistence and later context assembly. Some providers
    require the original block and signature to be returned unchanged.

    Display choices such as collapsing or dimming this content belong to the
    application.

    .. seealso:: :class:`ContentBlock`, :class:`flowing.providers.ProviderDelta`
    """
    type: Literal["thinking"] = "thinking"
    """Discriminator fixed to ``"thinking"``."""
    thinking: str
    """Provider-supplied reasoning text."""
    signature: str | None = None
    """Provider-issued integrity signature, when the provider uses one; otherwise ``None``."""
@dataclass(kw_only=True)
class ToolCallBlock(ContentBlock):
    """A tool-call request emitted by a provider.

    The provider assigns ``id``. The ID pairs this block with the subsequent
    TOOL result message's ``tool_call_id`` on the same branch. This block is the
    persisted and context-level representation; ``flowing.tool.ToolCall`` is
    the parsed form used by Agent tool-call handling. It is a projection that
    retains only ``id``, ``name``, and ``args``, plus the common ``shortcut``
    field. :meth:`flowing.tool.ToolCall.from_block` performs this one-way
    conversion. The dependency direction is one-way too:
    ``flowing.tool`` depends on ``flowing.message``, while ``flowing.message``
    does not depend on ``flowing.tool``.

    The arguments are the provider's raw dictionary. Parameter merging and
    validation are handled by the tool-entry layer, not by this block. That
    layer gives explicitly specified values precedence over provider arguments,
    and provider arguments precedence over schema defaults. The parsed
    ``flowing.tool.ToolCall`` form can also carry a ``shortcut`` value used to
    skip ordinary execution.

    .. rubric:: Example

    .. code-block:: python

        for block in response.message.content:
            if block.type == "tool_call":
                result = await agent.tool_call(ToolCall.from_block(block))

    .. rubric:: Behavior

    Each call must have a paired TOOL result on the same branch. Cancellation
    closes the pair with ``tool_status="cancelled"``; recovery creates a
    synthetic placeholder when a result is missing. Context assembly checks
    pair completeness and raises ``UnpairedToolCallError`` rather than
    repairing the tree while reading it.

    .. seealso:: :class:`flowing.tool.ToolCall`, :class:`Message`,
       :meth:`flowing.agent.Agent.tool_call`
    """
    id: str
    """Provider-assigned call ID used to pair the request with its TOOL result."""
    name: str
    """LLM-visible tool name, corresponding to a tool entry's alias."""
    type: Literal["tool_call"] = "tool_call"
    """Discriminator fixed to ``"tool_call"``."""
    args: dict[str, Any]
    """Raw argument dictionary supplied by the provider, before parameter merging."""
@dataclass(kw_only=True)
class StructBlock(ContentBlock):
    """Structured JSON-compatible data that remains directly readable by code.

    Programs can read ``data`` without decoding JSON. Provider adapters project
    it to JSON text for the model. Use ``TextBlock`` for text and scalar output,
    ``StructBlock`` for compound structured output, and media blocks for media.

    .. rubric:: Behavior

    Construction checks that ``data`` can be serialized as JSON and raises
    ``ValueError`` otherwise. Adapters project it with
    ``json.dumps(data, ensure_ascii=False)`` rather than provider-native
    structured output. The model sees exactly the same bytes as it would for a
    plain-text block containing that JSON. Token estimation is based on the
    length of the JSON serialization. Tool output normalization ordinarily
    creates this block; tool execution APIs do not accept block classes as
    return types.

    .. seealso:: :class:`ContentBlock`, :class:`TextBlock`,
       :class:`flowing.tool.ToolResult`
    """
    type: Literal["struct"] = "struct"
    """Discriminator fixed to ``"struct"``."""
    data: Any
    """JSON-compatible compound value, validated during construction."""
@dataclass(kw_only=True)
class MediaBlock(ContentBlock):
    """Base class for media represented by required base64 data.

    Images, video, audio, and files share ``data``, ``name``, and
    ``mime_type``. The message is self-contained: ``data`` is authoritative,
    while the name and MIME type are metadata. Adapters convert the base64
    representation to the provider's required inline, URL, or multipart form.

    .. rubric:: Behavior

    ``name`` and ``data`` are required when constructing a block directly; a
    path is never stored instead of the data. Normalization and provider
    adapters may fill a missing name using this precedence: an explicit name,
    the basename of the source path, then the first 12 hexadecimal characters
    of the SHA-256 hash of ``data`` plus an extension inferred from the MIME
    type. Unknown MIME types use ``.bin``. An explicit ``mime_type`` takes
    precedence over adapter inference. The base64 string itself is not
    validated. This class does not enforce size limits or perform scanning,
    transcoding, or thumbnail generation.

    .. rubric:: Example

    .. code-block:: python

        import base64

        with open("chart.png", "rb") as image_file:
            data = base64.b64encode(image_file.read()).decode()
        message = Message(
            kind=MessageKind.USER,
            content=[
        TextBlock(text="What anomalies are visible in this image?"),
                ImageBlock(data=data, name="chart.png", mime_type="image/png"),
            ],
        )

    .. seealso:: :class:`ImageBlock`, :class:`FileBlock`, :class:`ContentBlock`
    """
    data: str
    """Required base64-encoded media payload and its authoritative representation."""
    name: str
    """Required filename or media label. It is metadata and does not replace ``data``."""
    mime_type: str | None = None
    """Optional MIME type. An explicit value takes precedence over adapter inference."""
@dataclass(kw_only=True)
class ImageBlock(MediaBlock):
    """A static image intended for visual understanding.

    Use this block when an uploaded image should be interpreted visually. Use
    :class:`FileBlock` when the image is only being passed as a file. Adapters
    map it to provider-specific image inputs, such as an Anthropic ``image``
    block or an OpenAI ``image_url`` content part. The core does not verify
    that the base64 data actually encodes an image.

    .. seealso:: :class:`MediaBlock`, :class:`VideoBlock`, :class:`FileBlock`
    """
    type: Literal["image"] = "image"
    """Discriminator fixed to ``"image"``."""
@dataclass(kw_only=True)
class VideoBlock(MediaBlock):
    """A video represented as a continuous sequence of frames.

    The core carries the media but does not require providers to support video.
    An adapter or application may reduce unsupported video to a frame, file
    reference, or other suitable representation.

    .. seealso:: :class:`MediaBlock`, :class:`ImageBlock`
    """
    type: Literal["video"] = "video"
    """Discriminator fixed to ``"video"``."""
@dataclass(kw_only=True)
class AudioBlock(MediaBlock):
    """Audio input intended for speech or audio understanding.

    An adapter may represent unsupported audio with placeholder text or a
    transcription. Use :class:`FileBlock` when an audio file is only being
    transferred and need not be understood.

    .. seealso:: :class:`MediaBlock`, :class:`FileBlock`
    """
    type: Literal["audio"] = "audio"
    """Discriminator fixed to ``"audio"``."""
@dataclass(kw_only=True)
class FileBlock(MediaBlock):
    """A file payload that is not intended to be read as an image or audio input.

    A USER message may contain text and multiple file blocks. Tool output may
    also include files alongside structured data. Use :class:`ImageBlock` or
    :class:`AudioBlock` when the provider should interpret the media itself.

    Adapters decide how to represent files to each provider. For example,
    Anthropic may use a text reference or its ``document`` input, while OpenAI
    may use a file attachment or ``data`` content part.

    .. seealso:: :class:`MediaBlock`, :class:`StructBlock`
    """
    type: Literal["file"] = "file"
    """Discriminator fixed to ``"file"``."""
@dataclass
class Message:
    """A message from any source and a node in the message-level tree.

    Users, providers, tools, peer Agents, system events, and extensions all
    express new information as a ``Message``. Its ``id`` identifies the tree
    node, and ``parent_id`` links it to its parent. Context assembly follows
    those links from the Agent's ``current_head_id``. A forest may contain
    multiple roots, and ``Agent.fork`` changes the active head without creating
    a message. Messages have no provider ``role`` field; adapters map ``kind``
    to API roles.

    .. rubric:: Example

    .. code-block:: python

        message = Message(
            kind=MessageKind.USER,
            content=[TextBlock(text="Please check order 4521.")],
            source="chat_input",
            tags=["order"],
        )
        message_id = await agent.enqueue_message(message)

    .. rubric:: Behavior

    - If ``id`` is ``None``, the owning Agent assigns a per-Agent increasing
      numeric string when the message crosses an Agent boundary, such as
      enqueueing or attaching it to the tree. The sequence is persisted and
      continues after recovery. An explicit ID is preserved. The ID returned
      by ``enqueue_message`` is the message's ID. ``Agent.query()`` associates
      its waiting result with that ID.
    - The Agent sets ``parent_id`` when attaching the message: the first
      message follows the active head, and later messages link to the previous
      message in the sequence.
    - ``turn_end`` marks turn closure. The Agent writes it on the PROVIDER
      message when that message closes the turn through normal completion,
      cancellation, or abort. It is distinct from the provider's finish reason:
      a streaming interruption can close a turn without a provider finish.
      After recovery, messages persisted after the last PROVIDER message marked
      ``turn_end=True`` are treated as a partial turn and remain in context;
      they are not truncated or resumed.
    - Interrupted streaming content is retained with ``partial=True``;
      normally completed messages use ``False``. Recovery creates a
      ``synthetic=True`` placeholder TOOL message with error status and
      explanatory text when a tool call has no result.
    - ``tool_call_id`` and ``tool_status`` must both be present on TOOL
      messages and absent on all other kinds. Construction raises
      ``ValueError`` if that condition is violated. The ID pairs with a
      provider ``ToolCallBlock.id`` on the same branch; tree-level pairing is
      checked during context assembly.
    - ``timestamp`` defaults to the current naive UTC time and is retained for
      querying, logging, auditing, and ordering. Adapters decide whether it is
      included in provider context. ``usage`` holds measured Provider usage;
      adapters attach it to PROVIDER messages, and it remains available after
      persistence and recovery. The core does not use it for billing, which is
      aggregated in ``TurnResult.token_usage``.
    - A parent is either ``None`` or an existing persisted message. Multiple
      roots are allowed. ``MessageChain.branch`` can create a new root without
      deleting the old tree. Sibling branches have no defined ordering.
    - A logical turn starts when a message is consumed and ends at a
      PROVIDER message marked ``turn_end=True`` or by an abort. Tool-call
      blocks and result messages pair one-to-one on the same branch.
    - ``Agent.side_query`` messages are not stored in this tree. A message
      carries no execution state or waiter binding; those are runtime-only
      structures.

    .. seealso:: :class:`MessageKind`, :class:`MessageChain`,
       :class:`MessageQueue`, :class:`flowing.agent.TurnContext`
    """
    kind: MessageKind
    """Required source category for the message, not a provider API role; see :class:`MessageKind`."""
    content: list[ContentBlock]
    """Required ordered content blocks. Different block types can be interleaved.
    TOOL results contain ordinary content blocks, not provider protocol blocks.
    """
    tool_call_id: str | None = None
    """Pairing ID for a TOOL result. It matches a provider ``ToolCallBlock.id`` on the same branch and must be set exactly when ``kind`` is TOOL."""
    tool_status: Literal["completed", "pending", "blocked", "cancelled", "error"] | None = None
    """Status of a TOOL result. It must be present exactly when ``kind`` is TOOL; construction enforces this together with ``tool_call_id``."""
    id: str | None = None
    """Message and tree-node ID. ``None`` means the owning Agent assigns an increasing numeric string when the message enters an Agent boundary. Explicit IDs are preserved, and the sequence continues across recovery.
    """
    parent_id: str | None = None
    """Parent message ID. ``None`` marks a root, and multiple roots are allowed. The Agent sets this when appending a message; :class:`MessageChain` operations can set it when editing the tree.
    """
    turn_end: bool = False
    """Logical-turn closure marker (default ``False``); meaningful only on PROVIDER messages. The Agent writes it when the turn closes with this message, whether by normal completion or cancellation/abort. It is distinct from ``ProviderResponse.finish`` and is used to locate completed turn boundaries during recovery."""
    partial: bool = False
    """Whether retained content is incomplete because streaming was interrupted. The accumulated content is kept rather than discarded."""
    synthetic: bool = False
    """Whether recovery created this placeholder TOOL message with error status and explanatory content to close an orphaned tool call."""
    source: str = ""
    """Free-form secondary category supplied by the producer; Flowing does not
    enumerate its values. The default is the empty string.
    """
    tags: list[str] = field(default_factory=list)
    """Labels used for grouping, filtering, and cleanup. The default is an
    empty list.
    """
    priority: MessagePriority = MessagePriority.NORMAL
    """Queue scheduling priority, defaulting to ``MessagePriority.NORMAL``. It affects scheduling only while the message is queued."""
    timestamp: datetime = ...
    """Naive UTC timestamp assigned at construction. Adapters decide whether to include it in provider context."""
    usage: Usage | None = None
    """Measured Provider usage (default ``None``). Only PROVIDER messages carry it; adapters attach it when constructing response messages. It is the authoritative usage value persisted with the message and is retained through recovery. The core does not use it for billing; billing uses ``TurnResult.token_usage``.
    """
def estimate_block_tokens(block: ContentBlock) -> int:
    """Estimate token usage for one content block using a character heuristic.

    This is the single source for per-block estimates and can be used directly
    by observers that need to classify usage by block type.

    :param block: The content block to estimate.
    :return: Estimated token count for the block.

    .. rubric:: Behavior

    This is a synchronous, side-effect-free calculation with no cache. Text
    and thinking content use the character heuristic: ASCII characters count
    at about four per token, while non-ASCII characters count at about one per
    token, rounded up. Tool calls estimate the name and JSON-serialized
    arguments with non-ASCII characters preserved; structured data estimates
    its JSON text on the same basis. Media blocks use
    :data:`MEDIA_TOKEN_ESTIMATE` without inspecting base64 data. Unknown block
    types contribute zero.

    .. rubric:: Example

    .. code-block:: python

        estimate_block_tokens(TextBlock(text="Hello"))

    .. seealso:: :func:`estimate_message_tokens`, :data:`MEDIA_TOKEN_ESTIMATE`
    """
    ...
def estimate_message_tokens(msg: Message) -> int:
    """Estimate token usage for a message for context-window observation.

    The estimate sums :func:`estimate_block_tokens` across the message's
    content. It is intended for decisions such as whether a context may need
    compacting, not for billing; billing uses measured usage aggregated into
    ``TurnResult.token_usage``.

    :param msg: The message to estimate.
    :return: Estimated token count, or ``0`` when the message has no content.

    .. rubric:: Behavior

    The heuristic counts ASCII at about four characters per token and
    non-ASCII characters at about one character per token. It does not use a
    tokenizer. Message metadata such as IDs, source, and tags is not counted.
    The function is synchronous, reads the message without side effects, and
    does not cache results.

    .. rubric:: Example

    .. code-block:: python

        estimate = estimate_message_tokens(msg)

    .. seealso:: :data:`MEDIA_TOKEN_ESTIMATE`, :func:`estimate_block_tokens`,
       :meth:`flowing.agent.Agent.estimate_context_tokens`
    """
    ...
MEDIA_TOKEN_ESTIMATE: int
"""Fixed estimate of ``2000`` tokens assigned to every media block.

Provider token costs depend on media specifications, not on the length of the
base64 representation. ``estimate_message_tokens`` therefore uses this value
instead of counting the encoded payload.
"""
def to_record(msg: Message) -> dict[str, Any]:
    """Serialize a message as one JSON-compatible ``tree.jsonl`` record.

    This is the serialization counterpart to :func:`from_record` and the
    message-to-record mapping used by Agent persistence.

    :param msg: The message to serialize.
    :return: A dictionary with ``type="message"`` and the message fields.

    .. rubric:: Behavior

    ``kind`` is stored as its string value, ``priority`` as its integer value,
    and ``timestamp`` as an ISO-format naive UTC string. Content blocks are
    serialized with their ``type`` discriminator. ``usage`` is either
    ``None`` or a dictionary containing the ``input``, ``fresh_input``,
    ``output``, ``cache_read``, ``cache_write``, ``reasoning``, and
    ``total_tokens`` counters plus ``raw`` data. Older records with
    ``"usage": null`` remain supported.

    .. rubric:: Example

    .. code-block:: python

        {
            "type": "message",
            "id": "...",
            "parent_id": None,
            "kind": "user",
            "content": [{"type": "text", "text": "..."}],
            "tool_call_id": None,
            "tool_status": None,
            "turn_end": False,
            "partial": False,
            "synthetic": False,
            "source": "",
            "tags": [],
            "priority": 3,
            "timestamp": "2026-01-01T00:00:00",
            "usage": None,
        }

    .. seealso:: :func:`from_record`, :meth:`flowing.agent.Agent._persist_message`
    """
    ...
def from_record(record: dict[str, Any]) -> Message:
    """Restore a :class:`Message` from one record dictionary.

    :param record: A persisted message record.
    :return: The reconstructed message, including content blocks, enums,
        timestamp, and optional provider usage.
    :raises ValueError: The record is not a message record, a content block
        has an unknown ``type``, or a stored enum value is invalid.

    The kind and priority values are restored as enums, and the timestamp is
    parsed from ISO format. A non-null usage field is reconstructed as
    ``flowing.providers.Usage``.

    .. seealso:: :func:`to_record`, :meth:`flowing.agent.Agent._restore`
    """
    ...
class MessageQueue:
    """Priority-ordered asynchronous queue for messages entering Agent turns.

    Each Agent has an independent queue. The queue sorts by numeric priority
    and preserves FIFO order within one priority. It does not reject messages
    while a turn is running. The queue itself only orders and removes messages;
    the Agent may consume ``INTERRUPT`` or ``STEER`` messages at checkpoints
    during an active turn. An interrupt aborts that turn, while a steer message
    can be added before the next provider call without aborting it.

    The queue provides primitive batch operations for custom dequeue policies,
    but does not decide starvation prevention, source weighting, or batching
    strategy. These turn-level effects belong to the Agent, not to ordinary
    queue ordering or dequeue operations.

    The Agent's default dequeue policy consumes the consecutive urgent
    ``INTERRUPT``/``STEER`` messages at the head, then at most the next
    non-urgent message. If the head is already non-urgent, that batch contains
    one message. Separately, while a turn is active, the Agent checks for newly
    queued urgent messages at turn checkpoints. A pending ``STEER`` is included
    only if the turn reaches that check before finishing; otherwise the message
    remains queued for a later turn. If an ``INTERRUPT`` is pending at the same
    checkpoint, the Agent consumes both priorities and aborts the turn. Custom
    policies can use ``drain_all`` or ``take_while`` to build wider batches.

    .. rubric:: Example

    .. code-block:: python

        # Override _dequeue to drain and absorb all pending messages.
        def use_message_drain(agent):
            async def _drain_dequeue():
                return await agent._message_queue.drain_all()
            agent._dequeue = _drain_dequeue

        # Override _dequeue to coalesce messages from the same source.
        def use_message_coalescing(agent):
            original = agent._dequeue
            async def _coalescing_dequeue():
                messages = await original()
                extras = agent._message_queue.take_while(
                    lambda message: message.source == messages[0].source
                )
                return messages + extras
            agent._dequeue = _coalescing_dequeue

    .. rubric:: Behavior

    USER, EVENT, PEER, SYSTEM, and SUBAGENT messages may be queued;
    asynchronous tool results enter as EVENT messages. PROVIDER messages are
    produced during a turn and do not enter the queue. This class does not
    validate message kind and does not persist pending queue state.

    The queue is unbounded and intended for a single-process asyncio runtime.
    ``enqueue`` is synchronous and non-blocking. Dequeue and wait operations
    are asynchronous.

    .. seealso:: :class:`MessagePriority`, :meth:`flowing.agent.Agent._dequeue`,
       :meth:`flowing.agent.Agent.enqueue_message`,
       :meth:`flowing.agent.Agent.cancel_queued`
    """
    def __init__(self) -> None: ...
    def enqueue(self, msg: Message) -> None:
        """Insert a message in priority and FIFO order without waiting.

        :param msg: The message to enqueue.

        The method does not modify the message, deduplicate it, limit queue
        capacity, or review its content. Agent-level enqueue hooks provide the
        application interception point.

        .. seealso:: :meth:`dequeue`, :meth:`set_priority`
        """
        ...
    async def wait_not_empty(self) -> None:
        """Wait until the queue is non-empty without removing a message.

        Returns immediately when a message is already queued. Otherwise it
        waits until a later enqueue. The caller remains responsible for
        retrying if subsequent dequeue processing yields an empty batch.
        """
        ...
    def dequeue_nowait(self) -> Message | None:
        """Remove and return the highest-priority message, or ``None`` if empty.

        Among messages with equal priority, the earliest enqueued message is
        returned first.

        This is the non-blocking primitive used after an urgent queue-head
        segment has been removed. When the head was non-urgent, it supplies the
        single message in the default dequeue batch.
        """
        ...
    async def dequeue(self) -> Message:
        """Wait for and remove the highest-priority queued message.

        This combines waiting and removing for consumers that need one message
        at a time. A custom Agent dequeue policy may instead combine
        :meth:`wait_not_empty`, :meth:`take_while`, and
        :meth:`dequeue_nowait` to form a batch.

        :return: The message removed from the queue.

        The call must run inside an event loop. Cancelling its waiting task
        terminates the wait without returning a message.

        .. seealso:: :meth:`wait_not_empty`, :meth:`dequeue_nowait`,
           :meth:`drain_all`, :meth:`take_while`
        """
        ...
    async def drain_all(self) -> list[Message]:
        """Remove all messages currently queued and return them in dequeue order.

        This does not wait for new messages. An empty queue returns an empty
        list. Messages enqueued after the operation observes the current queue
        remain for a later dequeue.

        When a batch is used to merge messages into one turn, every message's
        waiter shares that turn's ``TurnResult`` when it finishes, and all of
        those waiters are resolved.

        .. seealso:: :meth:`dequeue`, :meth:`flowing.agent.Agent._dequeue`
        """
        ...
    def take_while(self, predicate: Callable[[Message], bool]) -> list[Message]:
        """Remove the consecutive queue-head messages that satisfy ``predicate``.

        The predicate is checked in dequeue order, starting at the head. The
        first message that fails the predicate and all later messages remain
        queued. The method does not wait and may return an empty list.

        .. seealso:: :meth:`drain_all`, :meth:`flowing.agent.Agent._dequeue`
        """
        ...
    def peek(self, priority: MessagePriority | None = None) -> Message | None:
        """Return the next queued message without removing it.

        With no priority filter, this returns the global head in dequeue
        order: priority first, then FIFO within a priority. If ``priority`` is
        supplied, it returns the FIFO head within that band. This supports
        observations such as showing one preview per priority in a UI; an
        empty queue or empty selected band returns ``None``.

        ``dequeue()`` blocks and removes a message, while ``drain_all()`` and
        ``take_while()`` remove batches. ``peek()`` adds a read-only
        observation primitive alongside :meth:`__len__`.

        .. rubric:: Behavior notes

        - This is synchronous and non-blocking. It returns immediately and
          leaves the queue unchanged.
        - The returned message remains queued. Later ``dequeue()``,
          ``set_priority()``, and ``remove()`` calls still operate on it; the
          caller does not acquire or reserve it.
        - It provides no synchronization. A value observed across Tasks is
          only a momentary snapshot and must not be used to make mutual
          exclusion decisions, just like :meth:`__len__`.
        - A priority filter selects only the head within that band and does
          not compare across bands. ``None`` returns the global head, the
          message a subsequent ``dequeue()`` would take.

        :param priority: If supplied, inspect only this priority band and return
            its FIFO head. ``None`` inspects the global queue head.
        :return: The selected message, or ``None`` if no message matches.

        This is a synchronous observation with no reservation or locking
        semantics. The returned message remains in the queue and can still be
        removed, reprioritized, or dequeued by another operation.
        """
        ...
    def remove(self, message_id: str) -> bool:
        """Remove a queued message by ID if it has not been dequeued.

        :param message_id: ID of the message to withdraw.
        :return: ``True`` if a queued message was removed; otherwise ``False``.

        If the ID is absent, or the message has already been dequeued and is
        being or has been consumed by a turn, the method returns ``False``
        without side effects. Removing a queued message does not affect a turn
        that is already executing. When this returns ``True``,
        ``Agent.cancel_queued()`` also resolves the corresponding waiter as
        cancelled; the caller does not wait for that resolution.

        .. seealso:: :meth:`flowing.agent.Agent.cancel_queued`
        """
        ...
    def set_priority(self, message_id: str, priority: MessagePriority) -> bool:
        """Change the priority of a queued message and reposition it.

        :param message_id: ID of the message to reprioritize.
        :param priority: The new queue priority.
        :return: ``True`` if the queued message was found and updated;
            otherwise ``False``.

        .. rubric:: Example

        .. code-block:: python

            # User follow-up: promote the queued message to HIGH.
            agent._message_queue.set_priority(msg_id, MessagePriority.HIGH)

        Repositioning preserves the message's original enqueue order within
        its new priority band. An absent or already dequeued message returns
        ``False`` without side effects. The operation also updates
        ``msg.priority`` in place; it does not alter the message ID, timestamp,
        waiter binding, or active turn. The queue does not persist ordering as
        a separate state. Recovery rebuilds the order from the priority on
        each message, so the updated field is the value used for that
        reconstruction.

        .. seealso:: :meth:`enqueue`, :meth:`remove`, :class:`MessagePriority`,
           :meth:`flowing.agent.Agent.set_queued_priority`
        """
        ...
    def __len__(self) -> int:
        """Return the number of messages currently queued.

        This is an observation, not a synchronization primitive. It is exact
        at the instant of the call within one event loop; a value observed from
        another task must not be used to make a mutual-exclusion decision.
        """
        ...
    def is_empty(self) -> bool:
        """Return whether the queue is empty at the time of the call.

        This is equivalent to ``len(self) == 0`` and has no synchronization
        semantics. It is suitable for observation, such as checking whether a
        later turn is likely to have pending input, but not for mutual exclusion.
        """
        ...
class MessageChain:
    """Perform the five structural operations on an Agent's message tree.

    This is the unified entry point for adding, deleting, updating, and
    reconnecting messages already in the tree or persisted history. The
    in-memory chain is authoritative: write operations modify
    ``Agent._messages`` and its ``parent_id`` links, then append one change
    record to ``tree.jsonl``. They do not truncate or rewrite the file during
    normal operation; physical rewrites are deferred to compaction.

    Messages are persisted when they are created, so surgery operates on
    persisted history. A tombstone turns deletion of an interior message from
    a per-line rewrite into an appended marker followed by one later rewrite
    during compaction. Temporary context such as recaps and reminders is also
    attached through this chain and removed with ``remove()`` when no longer
    needed. Additive messages at the start of a turn use
    ``TurnContext.pending_messages`` and are attached and persisted as a batch.

    The two read operations do not change the tree: :meth:`get` looks up one
    message by ID, and :meth:`walk` follows ``parent_id`` links from a message
    toward its root, as context assembly does. Both read memory only, do not
    persist data, and dispatch no hooks. Access this view through
    ``Agent.chain``.

    ``insert`` adds one message and reparents existing direct children beneath
    it. ``branch`` adds a parallel child without moving existing children; it
    is also the persistent new-root operation when ``parent_id=None``.
    ``remove`` deletes one message and reparents its direct children to its
    parent; it does not delete their subtrees. ``update`` changes one message's
    content without changing links. ``reparent`` moves a complete subtree.

    .. rubric:: Example

    .. code-block:: python

        agent.chain.insert("m5", Message(
            kind=MessageKind.SYSTEM,
            content=[TextBlock(text="...")],
        ))
        agent.chain.remove("m7")
        agent.chain.reparent("m8", to="m3")
        message = agent.chain.get("m5")
        active_path_reversed = list(agent.chain.walk(agent.current_head_id))

    .. rubric:: Behavior

    - The five write operations are orthogonal and form the minimal complete
      set. ``insert`` reparents existing children at a branch point;
      ``branch`` is its counterpart when adding a parallel branch without
      merging existing children. ``branch(None, message)`` is the only
      persistent way to create a new root in this forest model.
    - Each write changes memory and appends its corresponding record:
      ``insert`` adds a message plus adjacency changes, ``branch`` adds only
      the new message, ``remove`` adds a tombstone, and ``update`` or
      ``reparent`` adds its respective change record. Replaying the records
      reconstructs the same authoritative tree.
    - ``remove`` does not cascade. It reparents the removed node's direct
      children to its parent (or makes them roots), preserving their
      subtrees. To remove a whole subtree, reparent it elsewhere first and
      then remove its messages. Removing a TOOL result or a PROVIDER message
      with a ``ToolCallBlock`` can break the one-to-one call/result pairing;
      this class does not repair it.
    - These operations do not move ``current_head_id``. They edit history;
      changing the active view is ``Agent.fork()``'s responsibility. Removing
      or moving the current head or one of its ancestors is the caller's
      responsibility. Use ``Agent.remove()`` or ``Agent.pop()`` to remove the
      current head and maintain the head together.
    - The in-memory tree after every operation must match replay of the
      persisted records. Compaction (tombstone cleanup and tail rewrite) is
      triggered by the persistence layer's drain task after the queue is empty
      and the tombstone count reaches its threshold (256 by default); it is
      not a synchronous side effect of these methods.
    - This class does not add convenience policies such as cascading delete,
      automatic fork, or automatic head updates. It also has no batch-operation
      wrapper; a batch is a loop over the five operations and appends one
      record for each operation.

    .. seealso:: :meth:`flowing.agent.Agent.fork`,
       :class:`flowing.persistence.FileRecordStore`
    """
    def __init__(self, agent: "Agent") -> None:
        """Bind a message-chain view to its owning Agent.

        Applications normally access it through ``Agent.chain``.

        :param agent: The Agent whose message tree is read or edited.
        """
        ...
    def get(self, msg_id: str) -> Message:
        """Return the message with ``msg_id`` without changing the tree.

        :param msg_id: The message ID to find.
        :return: The message object held in the tree, not a copy.
        :raises KeyError: The ID is absent or the message has been removed.

        The lookup reads the in-memory mapping and does not access persistence.
        Mutating the returned object directly bypasses persistence; use a write
        operation to change history.

        .. seealso:: :meth:`walk`
        """
        ...
    def walk(self, from_id: str | None) -> Iterator[Message]:
        """Yield ``from_id`` and its ancestors, ending at the root.

        :param from_id: Starting message ID. ``None`` produces an empty iterator.
        :return: Messages in child-to-parent order, including the starting node.

        A missing starting ID produces an empty iterator. If an ancestor link
        points to a missing message, traversal stops at that break. The method
        does not consult the Agent's active head; pass ``agent.current_head_id``
        explicitly to walk the active branch. Returned objects are the live
        tree objects, not copies. Traversal follows parent links and does not
        sort messages by their timestamps.

        .. seealso:: :meth:`get`, :meth:`flowing.agent.Agent.fork`
        """
        ...
    def insert(self, after_id: str, msg: Message) -> str:
        """Insert ``msg`` after ``after_id`` and return its assigned ID.

        The new message becomes a child of ``after_id``. Any existing direct
        children of ``after_id`` are reparented under the new message, so
        inserting in a linear chain preserves the sequence. At a branch point,
        the existing branches are placed below the new message; use
        :meth:`branch` to add a parallel branch instead.

        :param after_id: Existing message after which to insert.
        :param msg: The message to insert. An absent ID is assigned by the Agent.
        :return: The inserted message's ID.
        :raises KeyError: ``after_id`` is not in the tree.
        :raises ValueError: ``msg.id`` conflicts with an existing node.

        The operation persists the new message and records each reparented
        child. It does not move ``current_head_id`` and does not validate the
        message kind.

        .. seealso:: :meth:`remove`, :meth:`reparent`,
           :meth:`flowing.agent.Agent.fork`
        """
        ...
    def branch(self, parent_id: str | None, msg: Message) -> str:
        """Add ``msg`` as a new child of ``parent_id`` without moving siblings.

        This attaches ``msg`` directly under ``parent_id`` by setting
        ``msg.parent_id``. Unlike :meth:`insert`, which reparents existing
        children beneath the inserted message, ``branch()`` leaves every
        existing child unchanged. With ``parent_id=None``, ``msg`` becomes a
        new root in the forest, and existing roots remain unchanged.

        This is the unambiguous primitive for adding a parallel branch, such
        as extending a fork without merging the existing branch. Creating a
        new root also provides the persistence-aware entry point for starting
        a new chain after context compaction while retaining the old tree; it
        does not require a virtual root or a separate ``add_root()`` method.

        .. rubric:: Behavior notes

        - ``parent_id`` must be ``None`` or identify a message in the tree.
          ``msg.id`` must not conflict with an existing node; the framework
          assigns an ID when it is absent.
        - With a parent, ``msg`` becomes its direct child and all existing
          children retain their parent IDs and positions. With no parent,
          ``msg.parent_id`` is ``None`` and it becomes a new root without
          affecting other root chains. Persistence appends only the new
          message row and makes no adjacency-adjustment records.
        - The method does not move ``current_head_id``; use
          :meth:`flowing.agent.Agent.fork` to switch the head after creating a
          root. It does not validate ``msg.kind``.

        :param parent_id: Existing parent ID, or ``None`` to create a new root.
        :param msg: The message to attach. An absent ID is assigned by the Agent.
        :return: The new message's ID.
        :raises KeyError: A non-``None`` parent ID is absent from the tree.
        :raises ValueError: ``msg.id`` conflicts with an existing node.

        The operation persists only the new message. It does not move the
        active head or validate the message kind.

        .. seealso:: :meth:`insert`, :meth:`flowing.agent.Agent.fork`
        """
        ...
    def remove(self, msg_id: str) -> None:
        """Remove one message and reparent its direct children to its parent.

        Before deleting the node, the method reparents its direct children to
        the removed node's parent (or promotes them to roots if the removed
        node was a root). Each moved child gets a persisted ``move`` change
        row; the removed node is then removed from memory and a ``tombstone``
        row is appended. Physical deletion is deferred until compaction.

        :param msg_id: ID of the message to remove.
        :raises KeyError: The ID is absent or has already been removed.

        This operation does not move ``current_head_id``. It also does not
        restore tool-call/result pairing if the removed message was one side of
        a pair. Callers must preserve that invariant or later context assembly
        raises ``UnpairedToolCallError``. Use Agent-level removal operations
        when the current head must be maintained.

        Only the removed node is deleted; the subtrees beneath its direct
        children remain intact. To delete an entire subtree, first reparent it
        elsewhere or remove its messages individually. Removing a tail message
        is a pure truncation with no child moves; removing an interior message
        writes one move per direct child plus one tombstone (runtime cost is
        proportional to the number of direct children). Deleting a TOOL
        result or a PROVIDER message containing a ``ToolCallBlock`` can break
        tool-call/result pairing. This method does not repair the pair, so the
        caller must close it (for example, by inserting a replacement result)
        or context assembly will fail its pairing assertion.

        .. seealso:: :meth:`reparent`, :meth:`insert`
        """
        ...
    def remove_by_tags(self, tags: set[str]) -> int:
        """Remove messages whose message or content-block tags match ``tags``.

        A message matches if its own ``tags`` intersect the requested set, or
        if any content block's ``tags`` intersect it. Each match is removed
        through :meth:`remove`.

        :param tags: Tags used to select messages.
        :return: Number of removed messages; an empty set returns ``0``.

        Matches are removed in reverse message-creation order, from the end of
        the message chain toward its root. Because each parent is created
        before its children, children are removed first. Each deletion uses
        :meth:`remove`, so surviving direct children are reparented to the
        deleted node's parent. This method edits messages already attached to
        the tree, does not edit pending turn messages, and does not move
        ``current_head_id``. Use ``Agent.remove_by_tags`` when head maintenance
        is required.

        .. seealso:: :meth:`remove`, :meth:`flowing.agent.Agent.remove_by_tags`
        """
        ...
    def update(self, msg_id: str, content: list[ContentBlock]) -> None:
        """Replace one message's content without changing its tree position.

        :param msg_id: ID of the message to update.
        :param content: Replacement content-block list.
        :raises KeyError: The ID is absent from the tree.

        The operation persists the replacement. It does not change the
        message's kind, ID, or parent link, and it does not validate the new
        block combination against provider rules. Updating a PROVIDER message
        does not recalculate ``turn_end``.

        .. seealso:: :meth:`remove`, :meth:`reparent`
        """
        ...
    def reparent(self, msg_id: str, *, to: str) -> None:
        """Move the subtree rooted at ``msg_id`` beneath ``to``.

        Only the selected message's parent link changes; its descendants move
        with it and keep their own parent links.

        .. rubric:: Example

        .. code-block:: python

            agent.chain.reparent("m8", to="m3")

        :param msg_id: Root of the subtree to move.
        :param to: New parent message ID.
        :raises KeyError: Either ID is absent from the tree.
        :raises ValueError: ``to`` is ``msg_id`` or a descendant of it.

        Reparenting a root is allowed and moves its whole tree beneath another
        node. This operation moves rather than copies the subtree and does not
        update ``current_head_id``.

        .. seealso:: :meth:`remove`, :meth:`insert`,
           :meth:`flowing.agent.Agent.fork`
        """
        ...
