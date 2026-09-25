"""Register layered system prompts and represent assembled provider context.

.. rubric:: Overview

This module defines the data structures in the pipeline from layered system-
prompt registration through lazy evaluation to Provider-adapter consumption:

- :class:`PromptBlock` is a prompt-block declaration. Its ``content`` is a
  :class:`flowing.parsable.Parsable` value or a string (normalized to
  Parsable during construction); registration does not evaluate it. The
  declaration also carries management metadata: ``cache``, ``tags``, ``by``,
  and ``enabled``.
- :class:`PromptBlockList` is the ordered container owned by one Agent, exposed
  as ``agent.prompt_blocks``. It follows the semantics of
  :class:`flowing.lists.ManagedList`.
- :class:`PromptSegment` is the evaluated result of one block in one assembly:
  a rendered string plus its cache intent. It is an element of
  :attr:`Context.system_prompt`.
- :class:`Context` is the assembled output and the sole input type accepted by
  ``Provider.generate()``.
- :class:`ContextUsageEstimate` is a snapshot estimating current context
  usage, combining anchor measurements with local estimates.

This module defines data structures and synchronous container operations
only. Context assembly never awaits work: ``Parsable.resolve()`` is
synchronous, and collecting messages along the ``parent_id`` chain is an
in-memory traversal. The Agent performs assembly; this module specifies the
shape of its output.

:class:`Context` combines three orthogonal fields; an adapter must not flatten
them into one message stream and then try to split them again:

- ``system_prompt`` — ordered :class:`PromptSegment` objects from registered
  blocks, skipping disabled blocks while preserving registration order. Cache
  intent is retained for adapters to use in caching optimizations.
- ``tools`` — definitions of currently enabled tools
  (:class:`flowing.tool.ToolDefinition`), kept separate from messages.
- ``messages`` — the path from the root to ``current_head_id`` in the
  message-level tree, as :class:`flowing.message.Message` objects in
  root-to-head order.

Prompt blocks are evaluated when a context is assembled, not when they are
registered. ``cache`` is an intent marker provided by the framework for
Provider adapters. ``"static"`` means byte-stable and suitable for prefix
caching; ``"dynamic"`` means the content may change each turn; and
``"session"`` means it is rendered once at session startup and remains
unchanged during that session. Flowing implements no local cache strategy: it
assembles and evaluates the prompt before every ``provider_gen()`` call.
Whether and how to cache is decided by the adapter or server.

.. rubric:: Example

.. code-block:: python

    from flowing import Parsable

    async def setup(self):
        self.prompt_blocks.append(
            "session-info",
            Parsable("Current mode: {{ current_mode }}"),
            cache="dynamic",
            by="session",
            tags=["session"],
        )
        self.prompt_blocks.append(
            "mode-plan",
            Parsable("$./prompts/plan-mode.md"),
            cache="static",
            by="mode-switcher",
            tags=["mode-plan"],
        )

In a ``.fya`` file, the top-level ``system_prompt`` value is supplied through
the framework-owned reference block at ``PromptBlockList[0]``; it does not
need a separate registration (see :class:`PromptBlockList`).

.. rubric:: Behavioral notes

- Every assembly reevaluates Parsable values, recollects the message path, and
  regenerates tool definitions. The result is not cached, so it reflects the
  latest environment variables, instance attributes, and mode state.
- Each assembly creates a new ``Context`` and new lists; two contexts do not
  share mutable state.
- A partial turn does not truncate history. After crash recovery, messages
  persisted after the most recent ``turn_end=True`` still appear in
  ``messages`` as complete history. Execution state is not restored or resumed.
- Tool calls and results are strictly paired 1:1 within one branch: a
  ``ToolCallBlock.id`` in a PROVIDER message must match the ``tool_call_id`` of
  a subsequent TOOL message. The tree is closed at write time: cancellation
  writes a ``tool_status="cancelled"`` result, and recovery writes a
  ``synthetic=True`` placeholder. Assembly only asserts pairing and does not
  repair history while reading; an orphan raises
  :class:`flowing.errors.UnpairedToolCallError`. Adapters neither need nor
  should repair it.
- Messages from a ``side_query`` branch are neither added to the message tree
  nor persisted, so they do not appear in the main-flow ``messages`` field.
- The assembled value passes through the ``before_provider_gen`` hook first.
  A handler can replace any of its three fields before it is passed to
  ``Provider.generate()``.
- SYSTEM information has two distinct channels. Prompt blocks appended to
  ``prompt_blocks`` enter ``system_prompt`` at each assembly without starting
  a new turn, making them suitable for context appended every turn. A SYSTEM
  message delivered with ``enqueue_message`` enters message history and starts
  a new turn, making it suitable for a notification that should be handled
  independently. See :class:`flowing.message.Message` and
  :meth:`flowing.agent.Agent.enqueue_message`.

.. seealso::

    :class:`flowing.parsable.Parsable`
        Type of ``PromptBlock.content``; defines lazy evaluation and the two-
        stage rendering rules.
    :class:`flowing.lists.ManagedList`
        Public container abstraction and element-management contract followed
        by ``PromptBlockList``.
    :class:`flowing.message.Message`
        Element type of ``Context.messages``.
    :class:`flowing.tool.ToolDefinition`
        Element type of ``Context.tools``.
    :meth:`flowing.providers.Provider.generate`
        Final consumer of :class:`Context`.
    :meth:`flowing.agent.Agent._assemble_context`
        Internal method that performs assembly; not a stable public contract.
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, Literal
from .lists import ManagedList
from .message import Message
from .parsable import Parsable

if TYPE_CHECKING:
    from .tool import ToolDefinition
@dataclass
class PromptBlock:
    """A prompt declaration containing lazy content and management metadata.

    A block stores content as a :class:`flowing.parsable.Parsable` value or a
    string. Strings are normalized to ``Parsable`` during construction. The
    content is evaluated when an Agent assembles a context, producing a
    :class:`PromptSegment`; registration does not evaluate or validate it.

    ``cache``, ``tags``, ``by``, and ``enabled`` control how the declaration is
    managed. The ``cache`` value is passed to the provider adapter as an intent
    and does not cause local caching by the framework.

    Layered blocks let the framework, built-in extensions, and application code
    register and manage their own prompt sections independently. Lazy content is
    evaluated only when used because environment variables, configuration, or
    instance attributes may not yet be available when the block is registered.

    The name is a diagnostic label, not a unique key, and is not format-
    validated. Use ``tags`` or ``by`` for group management. Extensions should
    use their own ``by`` value so owner-based cleanup does not affect another
    component's blocks. Removing or disabling the framework-owned ``"core"``
    block removes the Agent's system prompt from assembled context and is a
    usage error.

    .. rubric:: Behavior

    - The evaluated content must be a string. A non-string result from a pure
      expression is a usage error.
    - Disabled blocks remain in the list at their original position and are
      skipped during iteration and context assembly. Re-enabling a block
      restores it in that position.
    - ``tags=None`` is normalized to an empty list, and an omitted ``by`` is
      normalized to ``""``. Block names are not unique keys; blocks with the
      same name can coexist.
    - No separator is inserted between adjacent blocks. Content must include
      its own formatting, or the provider adapter must supply it.
    - Do not resolve content before registering it when it is meant to change
      between assemblies. Registration intentionally preserves lazy content.

    .. rubric:: Example

    .. code-block:: python

        async def setup(self):
            self.prompt_blocks.append(
                "session-info",
                Parsable("Current mode: {{ current_mode }}"),
                cache="dynamic",
                by="session",
                tags=["session"],
            )

    .. seealso:: :class:`PromptSegment`, :class:`PromptBlockList`,
       :class:`flowing.parsable.Parsable`
    """
    name: str
    """A non-unique label used to identify the block in diagnostics and in the
    resulting :class:`PromptSegment`. The value is not format-validated.

    .. seealso:: :attr:`PromptSegment.name`
    """
    content: str | Parsable
    """Lazy content as :class:`flowing.parsable.Parsable` or a string. Strings
    are normalized to Parsable during construction, so constants,
    ``{{ }}`` templates, and ``$`` references use the same assembly-time
    evaluation path. Registration does not resolve the content, allowing it to
    refer to instance attributes, environment variables, or configuration that
    may not exist yet. The framework calls ``resolve()`` during assembly; do
    not resolve dynamic content before registration, or it becomes a static
    string.

    The evaluated value must be a string. Missing files or template variables
    are reported when it is evaluated.

    .. seealso:: :class:`flowing.parsable.Parsable`
    """
    cache: Literal["static", "dynamic", "session"]
    """Cache-intent channel for the Provider adapter. Its three values are
    ``"static"``, ``"dynamic"``, and ``"session"``; see the module docstring
    for their meanings. Flowing does not read or interpret this value and does
    not cache based on it. The adapter may ignore it, and the framework does
    not validate it; an invalid value is reported by the adapter.

    .. seealso:: :attr:`PromptSegment.cache`
    """
    tags: list[str]
    """Group labels used by ``enable_by_tag``, ``disable_by_tag``, and
    ``remove_by_tag``. A block may have multiple tags. The framework does not
    enumerate valid values; tags are unrestricted strings.

    .. seealso:: :meth:`PromptBlockList.disable_by_tag`
    """
    by: str = ""
    """Owner or source identifier used by owner-based management methods.
    The default is ``""`` (no owner). Conventional values include ``"core"``
    for the framework, ``"skill"`` for SkillPlugin, or an application-defined
    value. Extensions must use their own ``by`` value so owner-based disabling
    and removal clean up only their blocks. Removing blocks owned by ``"core"``
    is a usage error because it removes the framework's reference block. This
    value is not required to be unique.

    .. seealso:: :meth:`PromptBlockList.remove_by_owner`
    """
    enabled: bool = True
    """Whether the block participates in iteration and context assembly.
    Disabling a block changes only this field; it remains at its current
    position and can be re-enabled without changing the order. Setting
    ``block.enabled = False`` or ``True`` directly has the same state effect as
    the corresponding ``disable_by_*`` or ``enable_by_*`` operation.

    .. seealso:: :class:`flowing.lists.ManagedList`
    """
@dataclass
class PromptSegment:
    """The evaluated string produced from a prompt block during one assembly.

    A segment is an element of ``Context.system_prompt`` and is passed to the
    provider adapter. It retains the source block's ``name`` and ``cache``
    intent, but not its management metadata (``tags``, ``by``, or ``enabled``).
    Each assembly creates new segments, so adapters do not retain stale prompt
    values through this object.

    .. rubric:: Behavior

    - Segment order is the order of enabled source blocks, and is also the
      order used when composing the system prompt.
    - ``cache`` and ``name`` are passed through from the source block without
      rewriting. The framework does not reuse segments when ``cache`` is
      ``"static"``; a later assembly creates new objects and may produce new
      content.
    - An empty segment list is valid. Block names are not unique, so adapters
      must not use ``name`` as a unique key.
    - If all blocks are disabled, the list is empty. Disabling the framework-
      injected reference block at index ``0`` removes the system prompt from the
      assembled context and is a usage error.

    .. seealso:: :class:`PromptBlock`, :class:`Context`,
       :meth:`flowing.providers.Provider.generate`
    """
    content: str
    """The evaluated prompt text for this assembly. Provider adapters consume
    this final string; a non-string source result violates the content
    contract.
    """
    cache: Literal["static", "dynamic", "session"]
    """The cache intent copied from the source block for the provider adapter.
    Flowing does not interpret it locally.
    """
    name: str
    """The source block's name, preserved for tracing and adapter diagnostics.
    Names are not unique.
    """
class PromptBlockList(ManagedList[PromptBlock]):
    """Ordered container for the prompt blocks registered on one Agent.

    Each Agent starts with a framework-provided reference block owned by
    ``"core"``. Its content is ``Parsable("{{ self.system_prompt }}")``: it
    refers to the Agent's ``system_prompt`` value rather than copying it, so
    changes to that value appear at the next context assembly.
    Blocks added by setup code, Composables, or extensions are processed in
    list order. Iteration yields the block objects; evaluation happens later
    during context assembly.

    Each block carries ``enabled``, ``by``, and ``tags`` management fields.
    Disabling a block changes only ``enabled`` and is reversible; removing a
    block deletes it from the list and is irreversible. Iteration skips
    disabled blocks, and group operations with no matches do nothing.

    This class inherits grouped management from :class:`flowing.lists.ManagedList`
    and specializes ``append`` and ``insert`` to construct blocks from their
    fields. Its tag and owner operations return the number of matching blocks,
    including matches that were already in the requested enabled state.

    The ``system_prompt`` field in a ``.fya`` file automatically supplies the
    content of the reference block; no manual registration is needed. Register
    additional blocks from ``$script`` setup code.

    .. rubric:: Example

    .. code-block:: python

        from flowing import Parsable

        async def setup(self):
            self.prompt_blocks.append(
                "mode-plan", Parsable("$./prompts/plan-mode.md"),
                cache="static", by="mode-switcher", tags=["mode-plan"])
            self.prompt_blocks.append(
                "mode-review", Parsable("$./prompts/review-mode.md"),
                cache="static", by="mode-switcher", tags=["mode-review"])

        def switch_mode(self, mode: str):
            self.prompt_blocks.disable_by_owner("mode-switcher")
            self.prompt_blocks.enable_by_tag(f"mode-{mode}")

    .. rubric:: Behavior

    - The framework-provided ``"core"`` reference block is initially at index
      ``0``. Inserting before it changes its index. Locate it by owner, not by a
      fixed position. Removing or disabling the core block removes the system
      prompt from assembled context and is a usage error.
    - Enabled blocks are evaluated in list order. ``append`` adds to the end;
      ``insert`` places a block at the requested position. Iteration itself
      does not evaluate content.
    - These methods are for managing prompt sections, not for adding and
      removing per-turn content. Put changing values in a ``Parsable``
      template; it is evaluated at every assembly, so low-frequency values
      such as ``{{ current_mode }}`` do not require block changes. Frequently
      changing information, such as the current time, should use the message
      injection path rather than changing prompt content and invalidating
      provider-side prefix caching. For turn-start injection, use
      ``before_turn`` to append to ``TurnContext.pending_messages``; see
      :func:`flowing.composables.reminder.use_system_reminder` for an existing
      implementation.
    - Tag and owner operations do nothing and return ``0`` when there are no
      matches. Fixed indexes can shift after insertion or removal; use tags or
      owners to identify blocks.

    .. seealso:: :class:`flowing.lists.ManagedList`, :class:`PromptBlock`,
       :meth:`flowing.agent.Agent.setup`,
       :attr:`flowing.agent.Agent.prompt_blocks`
    """
    def append(self, name: str, content: str | Parsable, *, cache: Literal["static", "dynamic", "session"] = "dynamic", by: str = "", tags: list[str] | None = None) -> PromptBlock:
        """Construct a prompt block, append it, and return the block.

        This factory-style method appends the new block at the end. It
        specializes the base ``ManagedList.append`` method; the base
        ``append(item)`` form is not available on this class.

        :param name: A diagnostic label for the block; it need not be unique.
        :param content: Lazy content as a ``Parsable`` value or string. Strings
            are normalized during construction and evaluated during assembly.
        :param cache: Cache intent, one of ``"static"``, ``"dynamic"``, or
            ``"session"``. The default is ``"dynamic"``.
        :param by: Owner or source identifier. The default is ``""``.
        :param tags: Group labels. ``None`` is normalized to an empty list.
        :return: The newly constructed block, already present in this list.

        Call this from ``setup()`` or a Composable. The framework inserts the
        ``[0]`` reference block during initialization, before setup appends
        user blocks, so appended blocks follow it.

        .. rubric:: Example

        .. code-block:: python

            block = self.prompt_blocks.append(
                "session-info",
                Parsable("Current mode: {{ current_mode }}"),
                cache="dynamic",
                by="session",
                tags=["session"],
            )
            assert block.enabled

        .. rubric:: Behavior

        The new block starts enabled. This method does not evaluate the
        content, require a unique name, or validate the cache value. Changes to
        the returned block's fields take effect directly.

        .. seealso:: :class:`PromptBlock`, :meth:`disable_by_tag`
        """
        ...
    def insert(self, index: int, name: str, content: str | Parsable, *, cache: Literal["static", "dynamic", "session"] = "dynamic", by: str = "", tags: list[str] | None = None) -> PromptBlock:
        """Construct a prompt block and insert it at ``index``.

        Existing blocks at and after the position move one place to the right.
        Index handling follows ``list.insert``: negative indexes count from the
        end, and out-of-range indexes are clamped to the beginning or end.

        :param index: The insertion position.
        :param name: A diagnostic label for the block; it need not be unique.
        :param content: Lazy content as a ``Parsable`` value or string. Strings
            are normalized during construction and evaluated during assembly.
        :param cache: Cache intent, one of ``"static"``, ``"dynamic"``, or
            ``"session"``. The default is ``"dynamic"``.
        :param by: Owner or source identifier. The default is ``""``.
        :param tags: Group labels. ``None`` is normalized to an empty list.
        :return: The newly constructed block, already present in this list.

        .. rubric:: Example

        .. code-block:: python

            self.prompt_blocks.insert(
                1, "workspace-instructions", Parsable(instructions),
                cache="static", by="workspace-prompt", tags=["workspace"])

        .. rubric:: Behavior

        The new block starts enabled, and its content is not evaluated by this
        method. Inserting before the framework's core block is allowed and
        moves that block to a later position. Index-based identification is
        fragile because later insertions and removals shift positions; prefer
        tags or owners for group management.

        .. seealso:: :meth:`append`, :meth:`flowing.lists.ManagedList.insert`
        """
        ...
    def disable_by_tag(self, tag: str) -> int:
        """Disable every block whose ``tags`` contain ``tag``.

        :param tag: The label to match. A block matches if any element of its
            ``tags`` list equals this value.
        :return: The number of matching blocks, including blocks that were
            already disabled.

        .. rubric:: Example

        .. code-block:: python

            self.prompt_blocks.disable_by_tag("mode-review")

        .. rubric:: Behavior

        Disabling sets ``enabled`` to ``False`` but retains each block at its
        current position. Calling :meth:`enable_by_tag` restores it. Repeating
        the call still counts every match, even when its state does not change.
        No match returns ``0``. This method neither removes blocks nor evaluates
        their content or dispatches hooks.

        .. seealso:: :meth:`enable_by_tag`, :class:`flowing.lists.ManagedList`
        """
        ...
    def enable_by_tag(self, tag: str) -> int:
        """Enable every block whose ``tags`` contain ``tag``.

        :param tag: The label to match. A block matches if any element of its
            ``tags`` list equals this value.
        :return: The number of matching blocks, including blocks that were
            already enabled.

        .. rubric:: Behavior

        Enabling sets ``enabled`` to ``True`` without moving a block. Already
        enabled matches are still counted. No match returns ``0``.

        .. seealso:: :meth:`disable_by_tag`
        """
        ...
    def enable_by_owner(self, owner: str) -> int:
        """Enable every block whose ``by`` value equals ``owner``.

        :param owner: The owner identifier to match. An empty string matches
            blocks whose ``by`` value was omitted.
        :return: The number of matching blocks, including blocks that were
            already enabled.

        .. rubric:: Behavior

        Enabling sets ``enabled`` to ``True`` without moving a block. Already
        enabled matches are still counted. No match returns ``0``.

        .. seealso:: :meth:`disable_by_owner`
        """
        ...
    def disable_by_owner(self, owner: str) -> int:
        """Disable every block whose ``by`` value equals ``owner``.

        :param owner: The owner identifier to match. An empty string matches
            blocks whose ``by`` value was omitted.
        :return: The number of matching blocks, including blocks that were
            already disabled.

        .. rubric:: Behavior

        Disabling sets ``enabled`` to ``False`` but retains each block at its
        current position. Calling :meth:`enable_by_owner` restores it. Already
        disabled matches are still counted, and no match returns ``0``.
        Disabling the framework-owned ``"core"`` block removes the system
        prompt from assembled context and is a usage error.

        .. seealso:: :meth:`enable_by_owner`, :meth:`remove_by_owner`,
           :attr:`PromptBlock.by`
        """
        ...
    def remove_by_tag(self, tag: str) -> int:
        """Permanently remove every block whose ``tags`` contain ``tag``.

        :param tag: The label to match. A block matches if any element of its
            ``tags`` list equals this value.
        :return: The number of removed blocks, or ``0`` if there are no
            matches.

        .. rubric:: Example

        .. code-block:: python

            self.prompt_blocks.remove_by_tag("deprecated-feature")

        .. rubric:: Behavior

        Removal is physical and irreversible; later blocks move forward. A
        removal between context assemblies takes effect on the next assembly;
        an already assembled context is unaffected. This method removes only
        matching blocks and does not cascade to unrelated blocks with other
        tags. For reversible changes, use :meth:`disable_by_tag`.

        .. seealso:: :meth:`remove_by_owner`, :meth:`disable_by_tag`
        """
        ...
    def remove_by_owner(self, owner: str) -> int:
        """Permanently remove every block whose ``by`` value equals ``owner``.

        This is the owner-based cleanup path for all blocks registered by one
        extension or Composable.

        :param owner: The owner identifier to match. An empty string matches
            blocks whose ``by`` value was omitted.
        :return: The number of removed blocks, or ``0`` if there are no
            matches.

        .. rubric:: Example

        .. code-block:: python

            agent.prompt_blocks.remove_by_owner("my-plugin")

        .. rubric:: Behavior

        Removal is physical and irreversible; later blocks move forward.
        Removing blocks owned by ``"core"`` removes the framework's system
        prompt reference and is a usage error.

        .. seealso:: :meth:`disable_by_owner`, :attr:`PromptBlock.by`
        """
        ...
    def __getitem__(self, index: int) -> PromptBlock:
        """Return the block at ``index``, including disabled blocks.

        :param index: A list index, including a negative index.
        :return: The block at that position.

        .. rubric:: Behavior

        Indexing includes disabled blocks, unlike iteration. The framework's
        core prompt reference begins at index ``0`` but can move after an
        insertion; identify it by ``by="core"`` rather than a fixed index.
        This method does not support lookup by name or tag.
        """
        ...
    def __iter__(self) -> Iterator[PromptBlock]:
        """Iterate over enabled blocks in their current order.

        :return: An iterator yielding the block objects themselves, without
            evaluating their content.

        .. rubric:: Behavior

        Disabled blocks are skipped, and the relative order of enabled blocks
        is preserved. Iteration is not a snapshot; modifying the list during
        iteration is unsupported. In the single-process asyncio model,
        synchronous iteration does not yield control to another coroutine.

        .. seealso:: :meth:`__getitem__`, :class:`flowing.lists.ManagedList`
        """
        ...
@dataclass
class Context:
    """The assembled input passed to ``Provider.generate``.

    The Agent assembles a fresh context before each provider call. It contains
    the evaluated prompt segments, definitions of currently visible tools,
    and the active root-to-head message path. Assembly evaluates enabled
    ``prompt_blocks``, follows ``parent_id`` links from ``current_head_id``,
    and obtains each visible tool's ``llm_definition()``. A
    ``before_provider_gen`` hook receives this object and may rewrite it before
    the provider adapter receives it.

    The three fields are orthogonal, so adapter mapping is a field-by-field
    conversion: prompt cache intent maps to provider cache controls, tool
    definitions map to function declarations, and message kinds map to API
    roles. Adapters should not infer these structures from a flattened list.

    .. rubric:: Example

    .. code-block:: python

        from flowing import Agent, on
        from flowing.context import PromptSegment

        class MyAgent(Agent):
            @on("before_provider_gen")
            def add_trace(self, context):
                context.system_prompt.append(
                    PromptSegment(content=f"Request ID: {self.node_id}",
                                  cache="dynamic", name="trace"))
                return context

    .. rubric:: Behavior

    - ``messages`` are collected by following ``parent_id`` from the active
      ``current_head_id`` and reversing the path into root-to-head order. This
      is the same while a turn is running and after recovery: messages already
      appended during the current turn remain in the path, and persisted
      messages after the last completed turn are retained rather than
      truncating the incomplete turn.
    - On each branch, a ``ToolCallBlock.id`` in a PROVIDER message must match
      the ``tool_call_id`` of a later TOOL message. Pairing is closed when the
      messages are written: cancellation persists a result with
      ``tool_status="cancelled"``, while recovery persists a
      ``synthetic=True`` placeholder. Assembly only asserts the pairing; it
      does not repair history on read. An orphan raises
      :class:`flowing.errors.UnpairedToolCallError`.
    - ``tools`` contains definitions for enabled, visible tools only. Built-in
      tools such as ``subagent-invoke`` and ``finish`` appear only when the
      application declares them.
    - System information has two distinct paths. A prompt block enters
      ``system_prompt`` during assembly without starting a turn, making it
      suitable for context appended each turn. Enqueueing a ``SYSTEM`` message
      adds it to message history and starts a turn, making it suitable for an
      independently handled notification.
    - Messages created by ``Agent.side_query`` are not included because they
      are not stored in the message tree.
    - Flowing does not count tokens, trim or validate the context window, or
      map message kinds to provider roles. The provider adapter owns role
      mapping and may raise :class:`flowing.errors.ContextLengthError` for an
      oversized request; like other call-time errors, it is dispatched through
      ``on_provider_error``.
    - Editing ``messages`` in a ``before_provider_gen`` handler changes only
      that context object; it does not persist the change, and the next
      assembly replaces it. Use ``before_turn`` to append to
      ``TurnContext.pending_messages`` before the batch is attached to the tree
      and persisted, or use ``MessageChain`` operations (``insert`` / ``remove``)
      to edit stored history during a turn.
    - Every assembly creates a new context object and new lists; two Contexts
      share no mutable state. Empty lists are valid. A new Agent with no
      messages has ``messages == []``; its ``system_prompt`` contains at least
      the segment from the reference block at index zero, while ``tools`` may
      be empty. Instances are not thread-safe (the framework uses a
      single-process asyncio model).
    - After a fork changes the active head, the next assembly follows the new
      branch automatically.

    .. seealso:: :class:`PromptSegment`, :class:`flowing.tool.ToolDefinition`,
       :class:`flowing.message.Message`,
       :meth:`flowing.providers.Provider.generate`,
       :class:`flowing.agent.TurnContext`,
       :class:`flowing.errors.ContextLengthError`
    """
    system_prompt: list[PromptSegment]
    """The ordered prompt segments evaluated for this assembly. The list is
    newly created, may be empty, and can be rewritten by
    ``before_provider_gen`` handlers. Adapters use the cache intent carried by
    each segment for any cache optimization.
    """
    tools: list[ToolDefinition]
    """Definitions for currently enabled and visible tools. This list is
    independent of ``messages`` and may be empty; adapters map each definition
    to the API's function declaration.
    """
    messages: list[Message]
    """The active message path from root to head. Side-query messages are
    excluded; incomplete turns are retained, and tool-call pairing is checked
    during assembly rather than repaired here. Adapters map each message's
    ``kind`` to the provider's role.
    """
@dataclass
class ContextUsageEstimate:
    """A snapshot separating measured context usage from local estimates.

    This is the return type of ``Agent.estimate_context_tokens``. ``measured``
    is the portion covered by the latest provider measurement
    (``Usage.total_tokens`` at its anchor); ``estimated`` covers messages after
    that anchor, or the whole context, system prompt, and enabled tool schemas
    when no valid anchor exists. ``tokens`` is their sum. With no valid anchor,
    ``measured`` and ``anchor_message_id`` are ``None`` and all usage is
    estimated; this occurs for a new session or when the anchor message has
    been removed from the active path. The estimate is for context-window
    observation only and is never used for billing, which uses the aggregated
    ``TurnResult.token_usage`` values.

    .. rubric:: Example

    .. code-block:: python

        estimate = agent.estimate_context_tokens()
        if (estimate.usage_ratio is not None
                and estimate.usage_ratio > 0.85):
            ...  # The application or a plugin chooses whether to compact.

    .. rubric:: Behavior

    Reading this dataclass has no side effects. ``tokens`` equals
    ``(measured or 0) + estimated``. When
    ``measured`` is ``None``, all usage is estimated and should be treated as
    lower-confidence. An empty message path has zero tokens,
    ``measured is None``, and ``anchor_message_id is None``. When the context
    window is unknown, ``usage_ratio`` is ``None``.
    ``usage_ratio`` is not clamped, so a value greater than ``1.0`` indicates
    that the estimate exceeds the known context window.

    .. seealso:: :meth:`flowing.agent.Agent.estimate_context_tokens`,
       :func:`flowing.message.estimate_message_tokens`,
       :class:`flowing.providers.Usage`
    """
    tokens: int
    """The total estimate, equal to ``(measured or 0) + estimated``."""
    measured: int | None
    """Usage measured by the provider at the anchor. It is ``None`` when the
    active path has no valid anchor.
    """
    estimated: int
    """Local heuristic estimate for messages after the anchor and newly added
    tools, or for the full context when there is no anchor. Without an anchor,
    the estimate also covers the system prompt and all enabled tool schemas.
    """
    anchor_message_id: str | None
    """ID of the latest valid provider message on the active path, or ``None``
    when there is no valid usage anchor.
    """
    context_window: int | None
    """The current model's context-window size, or ``None`` when the model
    does not declare one.
    """
    @property
    def usage_ratio(self) -> float | None:
        """Return ``tokens / context_window``, or ``None`` if the window is unknown.

        Values are not clamped. A ratio greater than ``1.0`` is a valid signal
        that the estimate exceeds the context window; this property does not
        choose a response threshold.
        """
        ...
