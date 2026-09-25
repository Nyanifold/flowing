"""Optional built-in context-compaction composables; neither is enabled by default.

This module provides two alternative policies:

* :func:`use_compact` runs **after** a successful main-turn provider response.
  When the estimated context-usage ratio is over its threshold, it asks the
  model for a handover summary and starts a new message-tree root from that
  summary. The previous chain remains intact as history.
* :func:`use_auto_compact` runs **before** a main-turn provider request. It
  preserves a bounded head and tail of the chain, replaces the middle with a
  system message containing an optional assembly of user instructions and a
  handover summary, and sends the current request with the resulting context.

Both policies use ``estimate_context_tokens`` to decide whether to compact,
``side_query`` to generate the summary, and a message-tree branch followed by
``fork`` to change the active chain. They are application-level policies, not
core behavior: an agent enables one explicitly during ``setup()``. An agent
that enables neither has no compaction handler or compaction state.

The policies are alternatives in normal use. ``use_compact`` creates a new
root from a full-history summary and does not retain the recent working
messages in the active context. ``use_auto_compact`` retains the opening
context and recent messages, including completed tool-call pairs where needed.
The framework supplies mechanisms; thresholds, summary instructions, and
message layout are replaceable policy choices. A default handler group can be
removed with ``remove_by_owner`` before registering a replacement.
"""

from flowing.agent import Agent

DEFAULT_COMPACT_PROMPT: str
"""Default instruction used by both composables to request a handover summary.

The instruction asks the model to summarize the current conversation so the
task can continue after the old history is no longer in the active context. It
requires exactly these sections: ``Goal``, ``Progress``, ``Key decisions``,
``Next steps``, and ``Key context``. The output must contain only the summary,
not a continuation of the conversation.

``use_compact`` binds this value as ``agent.compact_prompt`` only when the
agent does not already have that attribute; a developer-provided value takes
precedence. ``use_auto_compact`` follows the same rule.
"""

DEFAULT_COMPACT_TEMPLATE: str
"""Default template for the system message created by ``use_auto_compact``.

The rendered message begins with ``following are compacted:``. If
``user_instructions`` is truthy, the template adds a ``User instructions``
section containing it; it always adds a ``Compaction summary`` section
containing ``summary``. The template context also provides ``self`` and
``agent``, both referring to the current agent, so a replacement template can
read agent state.

The template is rendered through ``agent.compact_template`` (a
``Parsable``). The default is bound only if the agent does not already have a
``compact_template`` attribute. Rendering uses a mapping as its context base;
under Jinja2's default behavior, references to undefined variables render as
empty text.
"""

def use_compact(agent: Agent, threshold: float = 0.8) -> None:
    """Enable post-request, full-history compaction for one initialized agent.

    This optional policy registers an ``after_provider_gen`` handler for the
    main-turn source ``"_turn"``. After a successful main-turn provider
    response, it estimates context usage and, when the ratio is strictly
    greater than ``threshold``, performs the following steps within that
    handler call:

    1. It dispatches the ``on_compact`` hook with the
       ``ContextUsageEstimate``. A hook handler can raise ``Intercepted`` to
       cancel this compaction attempt.
    2. It calls ``side_query`` with ``agent.compact_prompt`` resolved against
       the agent. The side query uses the current full context, but its own
       request and response are not added to the message tree or persisted.
    3. If the returned summary is non-empty, it adds a new ``SYSTEM`` root
       message with ``source="compact"`` and forks the agent to that root.

    The old chain is not deleted or rewritten; it remains physically intact
    in the message tree and record stream. The usage estimate is taken before
    the current provider response is attached to the tree, so it excludes
    that response and is based on the preceding context state.

    ``on_compact`` is declared under owner ``"compact"`` and can be used as an
    observation or interception point. The default ``compact_prompt`` is
    bound as a ``Parsable`` only if the agent has no such attribute already;
    developer-defined values take precedence. The handler is registered with
    the ``"_turn"`` pattern, so side-query responses are filtered by hook
    dispatch and cannot recursively trigger this handler. The handler group
    can be removed with ``agent.hooks.after_provider_gen.remove_by_owner(
    "compact")``; the hook-point declaration itself is not removed.

    If ``usage_ratio`` is ``None`` because the model has no declared context
    window, compaction is skipped. A ratio equal to the threshold also does
    not trigger compaction. An ``Intercepted`` raised from ``on_compact``
    cancels the attempt without making a side query or changing the active
    chain. If the side query fails or returns empty text, the error is
    suppressed, the chain is unchanged, and a later over-threshold main-turn
    response can trigger another attempt. If ``on_fork`` intercepts the fork,
    the new root has already been persisted while the active head remains
    unchanged.

    Calling this function more than once is not idempotent: each call adds a
    separate handler with its own threshold. During normal operation, turns
    for one agent run serially. The function does not change ``agent.model``,
    the message queue, or turn cancellation state; it registers no tools and
    persists no separate compaction state.

    .. rubric:: Usage examples

    .. code-block:: python

        # Enable with the default threshold of 0.8.
        async def setup(self) -> None:
            use_compact(self)

        # Compact only after the estimated context ratio exceeds 1.0.
        async def setup(self) -> None:
            use_compact(self, threshold=1.0)

        # Replace the default policy after removing its handler.
        async def setup(self) -> None:
            use_compact(self)
            self.hooks.after_provider_gen.remove_by_owner("compact")
            # Register a custom detection and chain-switching handler here.

    :param agent: The target agent instance. The standard usage is to pass
        ``self`` from ``setup()`` after initialization.
    :param threshold: Start compaction only when ``usage_ratio`` is strictly
        greater than this value. It must satisfy ``0 < threshold <= 1.0`` and
        defaults to ``0.8``. The ratio itself is not capped at ``1.0``, so a
        value above ``1.0`` indicates that the context is over its window;
        setting ``threshold=1.0`` therefore means to compact only after that
        point.
    :raises ValueError: If ``threshold`` is outside ``(0, 1.0]``. Validation
        occurs before any hook is declared or registered.
    """
    ...

def use_auto_compact(
    agent: Agent,
    *,
    threshold: float = 0.8,
    tail_tokens: int = 20_000,
    head_tokens: int = 10_000,
    user_instruction_tokens: int = 2_000,
) -> None:
    """Enable pre-request compaction that preserves bounded head and tail.

    This optional policy registers a ``before_provider_gen`` handler owned by
    ``"auto_compact"``. Before each main-turn provider request, it checks the
    estimated context-usage ratio. If the ratio is strictly greater than
    ``threshold``, it keeps a bounded prefix and suffix of the active message
    chain, summarizes the middle through ``side_query``, and replaces that
    middle with a rendered ``SYSTEM`` message. The agent then forks to the new
    branch, and the current provider request is assembled and sent with the
    compacted context.

    Unlike :func:`use_compact`, this policy runs before a request and retains
    the beginning of the chain and the most recent messages in the active
    context. The two policies may both be installed, but they act at different
    hook points and are normally used as alternatives.

    The handler distinguishes main-turn contexts from side-query contexts by
    checking whether the final message in ``context.messages`` is already in
    the agent's message tree. Side queries use an unpersisted context and are
    passed through unchanged. This provides recursion protection without
    maintaining a separate recursion flag.

    When selecting the prefix, the handler takes the longest prefix whose
    estimated token count is at most ``head_tokens`` and places the cut at a
    provider-message boundary. When selecting the suffix, it works backward
    under the ``tail_tokens`` budget and uses the same kind of boundary. The
    final contiguous run of non-provider messages is always retained, even
    when that exceeds the tail budget, so the request does not lose the latest
    user instruction or newly persisted tool result. If that run contains a
    tool result, its paired provider message is retained as well, even if this
    exceeds the budget. If the prefix and suffix overlap, the suffix is
    shortened so the prefix takes precedence. These boundaries keep provider
    and tool-call pairs intact.

    From the middle, the handler collects user messages from the beginning,
    up to ``user_instruction_tokens``, and joins their text blocks with
    ``---`` separators. If there are no such messages, ``user_instructions``
    is ``None``. It passes ``user_instructions``, the side-query ``summary``,
    and ``self``/``agent`` (the current agent) to ``agent.compact_template``.
    The default template is :data:`DEFAULT_COMPACT_TEMPLATE`; a developer's
    existing ``compact_template`` takes precedence. The resulting message has
    kind ``SYSTEM`` and source ``"auto_compact"`` and is branched from the last
    message of the retained prefix (or becomes a new root if the prefix is
    empty). Retained suffix messages are deep-copied and reattached with new
    message IDs; the old chain remains intact.

    The composable also declares ``on_compact`` under owner ``"compact"`` and
    binds ``DEFAULT_COMPACT_PROMPT`` as ``agent.compact_prompt`` only when no
    such attribute exists. Before summarizing, it dispatches ``on_compact``
    with the ``ContextUsageEstimate``; a handler may raise ``Intercepted`` to
    cancel the attempt. If the model has no declared context window, if the
    side query fails or returns empty text, or if the retained prefix and
    suffix leave no middle to compact, the original context is used. Side-query
    failures are suppressed so a later request can retry. If the fork is
    intercepted, the new branch has already been persisted but the agent's
    active head is unchanged, and the current request uses the original
    context. A successful fork causes the current request to use the newly
    assembled context. If that context still exceeds the model window, the
    request proceeds normally and the ``ContextLengthError`` channel handles
    the failure.

    Calling this function more than once adds a separate handler each time;
    each handler closes over its own parameters. The handlers can be removed
    together with ``remove_by_owner("auto_compact")``. The function does not
    change ``agent.model`` or the message queue, abort a turn, register tools,
    or persist separate compaction state. Message-tree changes use the normal
    persistence mechanism.

    .. rubric:: Usage examples

    .. code-block:: python

        async def setup(self) -> None:
            # Defaults: threshold 0.8, head 10k, tail 20k, instructions 2k.
            use_auto_compact(self)

        async def setup(self) -> None:
            use_auto_compact(self, threshold=0.9, tail_tokens=40_000)

    :param agent: The target agent instance. The standard usage is to pass
        ``self`` from ``setup()`` after initialization.
    :param threshold: Start compaction only when ``usage_ratio`` is strictly
        greater than this value. It must satisfy ``0 < threshold <= 1.0`` and
        defaults to ``0.8``.
    :param tail_tokens: The token budget for retaining the chain suffix. It
        must be non-negative and defaults to ``20_000``. The final non-provider
        segment and any required tool-call pairing may exceed this budget.
    :param head_tokens: The token budget for retaining the chain prefix. It
        must be non-negative and defaults to ``10_000``.
    :param user_instruction_tokens: The token budget for assembling user
        instructions from the middle of the chain. It must be non-negative and
        defaults to ``2_000``.
    :raises ValueError: If ``threshold`` is outside ``(0, 1.0]`` or any token
        budget is negative. Validation occurs before hook declaration or
        registration.
    """
    ...
