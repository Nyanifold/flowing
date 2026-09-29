"""The read-only observation API for consistent Runtime and Agent snapshots.

.. rubric:: Overview

This module defines two consistent snapshot structures,
:class:`RuntimeSnapshot` and :class:`AgentSnapshot`, together with read-only
views for nodes, agents, executions, capability entries, models, turns, the
message tree, and the message queue. These values let user interfaces,
monitoring tools, tests, and operations code inspect the current state of a
Runtime or Agent without entering the framework's execution flow.

Snapshots provide the pull-based observation channel: a caller requests a
snapshot through ``Runtime.snapshot()`` or ``Agent.snapshot()``. Hooks provide
the complementary push-based channel by notifying handlers at framework
events. The channels can be combined; for example, a hook handler can request
a snapshot after an event, and callers can request snapshots without
subscribing to hooks.

Snapshots are observation values, not persistence files. Message-tree
operations are recorded in ``tree.jsonl``, and state is stored through the
write-through ``StateView`` in ``state.jsonl``. Recovery reconstructs objects by
replaying those records; it does not load a serialized full-state snapshot.
Snapshot fields have no conversion relationship with these persistence formats
and carry no compatibility guarantee with them: snapshot fields are driven by
what is useful to observe now, while persistence formats are driven by
compatibility requirements.

Snapshots do not include plugin state. A plugin that exposes state should
provide its own read-only query API, which observers can call on the plugin
instance returned by ``runtime.get_plugin(...)``.

.. rubric:: Usage example

.. code-block:: python

    from flowing import launch

    async def inspect_snapshots() -> None:
        runtime = await launch("@/")
        root = await runtime.get_agent("root")

        runtime_view = runtime.snapshot()
        print(runtime_view.plugins)
        print(runtime_view.agents["root"].loaded)

        agent_view = root.snapshot()
        print(agent_view.paused, agent_view.messages.count, agent_view.current_head_id)
        for execution in agent_view.executions.values():
            print(execution.kind, execution.tags, execution.started_at)

    def install_monitor(agent):
        agent.hooks.after_turn(on_turn_end, by="monitor", tags=["observe"])

    def on_turn_end(agent, turn):
        print(agent.snapshot().message_queue.size)
        return turn

.. rubric:: Behavior notes

- Both snapshot entry points are synchronous. They collect their values in the
  current event-loop task without awaiting long-running work.
- Each call is internally consistent: all requested fields reflect one
  framework-state observation, rather than values collected at different
  times. If several views must be compared consistently, request their keys in
  one call, such as ``keys={"tool_entries", "executions"}``. Separate calls
  can observe different states.
- Passing ``keys=None`` (the default) collects every snapshot field. Passing a
  set of field names collects only those fields; unrequested fields are
  ``None``. This avoids the cost of unrelated collection work, such as context
  usage estimation, when polling one view frequently. Valid keys are snapshot
  field names.
- Consistency applies to one call only. State can change between calls, so an
  old snapshot is not a control input and must not be used to make decisions
  that require current state.
- Returned values are copies or read-only views isolated from framework
  storage. Mutating a snapshot does not mutate the framework. Snapshots do not
  expose mutable internal objects, control ``Event`` instances, queue deques,
  or the ``_provided`` mapping. Use public APIs such as ``provide()`` and
  ``Agent.register_state()`` to change framework state.
- Snapshot collection is defensive and does not raise on normal paths,
  including while an Agent has been destroyed or Runtime shutdown is in
  progress; it returns the consistent state that is available.
- All snapshot fields, including nested info views, contain only
  JSON-serializable scalar values, ``datetime`` values, lists, dictionaries,
  and info views. They do not contain arbitrary class instances, callables, or
  references to internal structures. The ``serve`` endpoint's direct JSON
  serialization of ``GET /snapshot`` relies on this guarantee; new fields
  must preserve it.
- Taking a snapshot does not change framework state or behavior. Hooks are
  consumers of the push channel, while snapshots are read-only projections.

.. seealso::

    - ``flowing.runtime.Runtime.snapshot``
    - ``flowing.agent.Agent.snapshot``
    - ``flowing.agent.TurnContext`` and ``flowing.agent.Execution``
    - ``flowing.hooks.HookRegistry``
    - ``flowing.agent.Agent.state``
    - ``flowing.runtime.Runtime.get_plugin``
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from flowing.context import ContextUsageEstimate

class NodeInfo:
    """A read-only view of one node's metadata in the Runtime object graph.

    .. rubric:: Overview

    This is the value type of ``RuntimeSnapshot.nodes``. It projects a node's
    type and parent pointer from the shared ``runtime-*``, ``workflow-*``, and
    ``agent-*`` ID space. It exposes scalar identity data, not the mutable node
    instance, so reading a snapshot cannot bypass the read-only boundary.

    .. rubric:: Behavior notes

    - Both fields are scalar copies of values fixed when the node is created;
      changing this view does not change the framework.
    - ``parent_id`` is ``None`` only for the Runtime itself. A root Agent or
      Workflow points to the Runtime's ``node_id``; its parent is not
      represented by ``None``.
    - This view does not expose hooks, provided values, children, or other
      mutable structures.
    - The view remains unchanged if the node is destroyed after the snapshot
      was taken. Call ``snapshot()`` again to observe current state.

    .. seealso:: ``flowing.runtime.ProvideNode`` and
        ``flowing.snapshot.RuntimeSnapshot.nodes``
    """
    type: str
    """Node type name, such as ``"runtime"``, ``"agent"``, or
    ``"workflow"``. It is derived from the node-ID prefix and fixed in this
    snapshot.
    """
    parent_id: str | None
    """Parent node ID. It is ``None`` for the Runtime, the end of the object
    graph. This value is an identifier only; use APIs such as
    ``Runtime.get_agent`` to retrieve an object.
    """

class AgentInfo:
    """A read-only view of one Agent-pool registry entry.

    .. rubric:: Overview

    This is the value type of ``RuntimeSnapshot.agents``. It projects one
    ``agent_id`` and its metadata from the Agent pool. The pool persists
    registry entries rather than live instances, so ``loaded`` distinguishes
    an entry with a live instance from one whose instance is currently absent.

    .. rubric:: Behavior notes

    - ``loaded=False`` is not an error. The instance may not have been created
      yet, or it may have been destroyed while its session remains available
      for recovery. ``Runtime.get_agent(agent_id)`` can recover it.
    - ``agent_type`` is the string type name used to rebuild the instance, not
      an Agent class object.
    - ``created_at`` comes from the pool metadata and remains the original
      creation time after recovery; it is not the latest load time. Recovery
      preserves identity, so ``node_id`` equals ``agent_id``.
    - Physically deleting the session removes the registry entry, so its key
      no longer appears in later snapshots.

    .. seealso:: ``flowing.runtime.Runtime.get_agent``,
        ``flowing.runtime.Runtime.create_agent``,
        ``flowing.runtime.Runtime.recover_agent``, and
        ``flowing.agent.Agent.destroy``
    """
    agent_type: str
    """String type name used to reconstruct the Agent. This is not a class
    object or module path.
    """
    parent_agent_id: str
    """ID of the parent Agent, or the Runtime's ``node_id`` for a root Agent.
    This is always a string, never ``None``. Parent-child relationships are
    logical; their session directories are siblings.
    """
    created_at: datetime
    """Original creation timestamp from the pool metadata. Recovery does not
    change it.
    """
    loaded: bool
    """Whether the Agent instance is currently in memory. ``True`` means it
    is live; ``False`` means its pool entry exists but its instance has not
    been loaded or has been destroyed. ``Runtime.get_agent`` can recover it.
    """

class ExecutionInfo:
    """A read-only view of one active execution entry.

    .. rubric:: Overview

    This is the value type of ``AgentSnapshot.executions``. It lets observers
    see which executions are active, their tags, and their start times.

    .. rubric:: Behavior notes

    - ``kind`` is an open string. Built-in values include ``"tool"``,
      ``"agent"``, ``"request"``, and ``"side_query"``; extensions may use
      additional values, which the snapshot preserves without enum validation.
    - ``tags`` is copied for grouping observations and corresponds to the
      grouping semantics of ``cancel_by_tag``. Later changes to the live
      execution's tags do not change this view.
    - An execution appears only while it is active; cleanup in a ``finally``
      block prevents completed entries from remaining in later snapshots.
    - The view remains a record of snapshot time after the execution ends. Do
      not use it to decide whether that execution is still running.
    - This view has no elapsed-time or progress field. Observers can compute
      elapsed time from ``started_at`` and the current time.
    - The cancellation and pause ``Event`` objects are not exposed. Use the
      Agent control methods to cancel or pause work.

    .. seealso:: ``flowing.agent.Execution``,
        ``flowing.agent.Agent.cancel``, ``flowing.agent.Agent.cancel_by_tag``,
        and ``flowing.snapshot.AgentSnapshot.executions``
    """
    kind: str
    """Open execution-kind string. Built-in values include ``"tool"``,
    ``"agent"``, ``"request"``, and ``"side_query"``. It describes the
    execution and does not control dispatch.
    """
    tags: list[str]
    """Copy of the execution's free-form tags for grouping observations.
    Changes to the live execution's tags after the snapshot do not affect this
    list.
    """
    started_at: datetime
    """Execution start timestamp, for monitoring, logging, and elapsed-time
    calculations.
    """

class EntryInfo:
    """A read-only view of one tool or subagent binding.

    .. rubric:: Overview

    This is the element type of ``AgentSnapshot.tool_entries`` and
    ``AgentSnapshot.subagent_entries``. It projects the Agent-level binding,
    not the global registry. The same executable object can have a different
    alias or visibility on different Agents.

    .. rubric:: Behavior notes

    - ``alias`` is the lookup name within this Agent; ``tool_call()`` uses the
      alias. One tool may therefore have different aliases in different Agent
      snapshots.
    - ``visible=False`` means the binding exists but is hidden from the LLM.
      Programmatic invocation remains possible; visibility and executability
      are separate. The entry itself remains present and can be made visible
      again.
    - ``agent_type`` is set only for a subagent binding. It is ``None`` for a
      tool binding.
    - This view does not include schema overrides, fixed values, or injected
      parameters. Use the entry's public LLM-definition API to inspect the
      rendered declaration.
    - Removing or hiding a binding after the snapshot does not change this
      view.

    .. seealso:: ``flowing.tool.ToolEntry``, ``flowing.agent.Agent.tool_call``,
        ``flowing.snapshot.AgentSnapshot.tool_entries``, and
        ``flowing.snapshot.AgentSnapshot.subagent_entries``
    """
    alias: str
    """Alias used to look up this binding within its Agent. It may differ
    from the global registry name, for example when overridden for one Agent.
    """
    visible: bool
    """Whether the binding is visible to the LLM. ``False`` does not prevent
    programmatic invocation and does not remove the binding. This mirrors the
    ``visible`` field on Tool, Subagent, and Skill entries.
    """
    agent_type: str | None
    """Target type name for a subagent binding. This is ``None`` for a tool
    binding.
    """

class ModelInfo:
    """A read-only projection of the Agent's resolved model configuration.

    .. rubric:: Overview

    This is the value type of ``AgentSnapshot.model``. An Agent holds a
    ``ModelConfig`` and passes it to the provider; it does not interpret the
    configuration fields. This view likewise projects those fields without
    interpreting them.

    .. rubric:: Behavior notes

    - The model is resolved when a snapshot is taken. Parsable fields follow
      ``ModelConfig.resolve()`` field-by-field; values are not cached, so a
      later snapshot reflects a changed ``self.model`` or ``model_tag``.
    - If resolving a field fails, that field is projected as ``None`` and the
      snapshot as a whole is still returned.
    - ``model_tag`` is the current tag. It is ``None`` when the Agent's model
      was replaced directly with a ``ModelConfig`` that does not correspond to
      a configured tag.
    - Provider credentials, including ``api_key``, are never included. They do
      not enter messages or persistence and are not exposed by this snapshot.
    - Before model resolution finishes during creation, ``AgentSnapshot.model``
      is ``None`` rather than a partially populated view.

    .. seealso:: ``flowing.model.ModelConfig``,
        ``flowing.model.ModelConfig.resolve``,
        ``flowing.providers.Provider`` (one-to-one binding and lazy
        instantiation are not represented in the snapshot), and
        ``flowing.snapshot.AgentSnapshot.model``
    """
    model: str | None
    """Model ID sent to the Provider API, or ``None`` if resolving the value
    failed.
    """
    provider: str | None
    """Name of the single bound Provider entry. There is no candidate list or
    fallback chain in this view.
    """
    model_tag: str | None
    """Current model tag, or ``None`` when the Agent's ``ModelConfig`` was
    assigned directly rather than resolved from a configured tag.
    """
    context_window: int | None
    """Context-window size in tokens, or ``None`` if it is undeclared or
    could not be resolved.
    """
    max_output_tokens: int | None
    """Maximum output-token count, or ``None`` if it is undeclared or could
    not be resolved.
    """
    thinking_budget: int | None
    """Thinking budget, or ``None`` if it is undeclared or could not be
    resolved.
    """

class TurnContextInfo:
    """A read-only view of the Agent's current logical turn.

    .. rubric:: Overview

    This is the value type of ``AgentSnapshot.current_turn``. A turn consumes
    a message until the Provider finishes or a turn-completion flag is set.
    It is an execution phase, not a message-tree node. Its ``TurnContext`` is
    transient, is not persisted, and is not recovered after a crash; this view
    exists only while the turn is running.

    .. rubric:: Behavior notes

    - ``AgentSnapshot.current_turn`` is non-``None`` only during turn
      execution. It may already be ``None`` during the ``after_turn`` hook and
      result-delivery phase; its absence does not prove that the hook has
      completed.
    - ``message_count`` includes the triggering message. The first message ID
      can identify the turn's first tree message, but this view does not
      provide a separate turn ID.
    - ``aborted`` reports the turn's cooperative-abort flag. It is an
      observation, not a cancellation control; use the Agent's cancellation
      or stop methods to request cancellation.
    - The mutable ``message_ids`` list and intermediate model outputs are not
      exposed. Subscribe to ``on_provider_delta`` to observe streamed output.
    - Recovery does not restore an in-progress turn. After recovery,
      ``current_turn`` remains ``None`` until another turn starts.

    .. seealso:: ``flowing.agent.TurnContext``, ``flowing.agent.TurnResult``,
        ``flowing.agent.Agent.cancel``, and ``flowing.agent.Agent.stop``
    """
    started_at: datetime
    """Timestamp when the logical turn began, for elapsed-time calculations.
    """
    finished_at: datetime | None
    """Projected turn-finalization timestamp. Because this snapshot exists
    only while the turn is active, this field is always ``None``. Use
    ``TurnResult.turn.finished_at`` for the completed value.
    """
    message_count: int
    """Number of messages produced by the turn, including its triggering
    message. This view provides a count, not the message-ID list.
    """
    aborted: bool
    """Read-only projection of the turn's cooperative-cancellation flag.
    ``True`` means a cancellation request has been made; this field is not a
    cancellation control.
    """

class MessageTreeInfo:
    """A read-only view of message-tree size and cursor position.

    .. rubric:: Overview

    This is the value type of ``AgentSnapshot.messages``. It projects the
    number of message nodes and the current cursor. A tree node is a message
    identified by ``Message.id`` and its ``parent_id``; ``head_id`` points to
    the current message ID.

    .. rubric:: Behavior notes

    - ``count`` includes all branches. Deleted messages are excluded; deletion
      is recorded as a tombstone, or deletion marker. Side-query messages are
      not added to the tree and are not counted.
    - ``head_id`` is the ``current_head_id`` message ID, or ``None`` when no
      message has been attached to the tree.
    - ``fork`` moves the cursor without changing the tree, so ``head_id`` can
      change while ``count`` remains the same.
    - After crash recovery, the cursor points to the last persisted tree
      message. Messages from an interrupted turn remain in the tree and count,
      although context assembly may truncate them at the last
      ``turn_end=True`` boundary. The snapshot reports the tree, not the
      assembled context.
    - This view does not enumerate branches, expose message content, or look up
      messages by ID. Use ``MessageChain`` or the persistence API for those
      operations.

    .. seealso:: ``flowing.message.Message``,
        ``flowing.message.MessageChain``, ``flowing.agent.Agent.current_head_id``,
        and ``flowing.snapshot.AgentSnapshot.current_head_id``
    """
    count: int
    """Total number of message nodes in the tree across all branches. Deleted
    messages marked by tombstones and side-query messages are excluded.
    """
    head_id: str | None
    """Current tree cursor value, equal to ``Agent.current_head_id``. It is a
    message ID, or ``None`` when the tree is empty.
    """

class MessageQueueInfo:
    """A read-only view of message-queue size and pending waiters.

    .. rubric:: Overview

    This is the value type of ``AgentSnapshot.message_queue``. It projects two
    counts for the per-Agent message queue: messages awaiting consumption and
    waiters still awaiting a ``message()`` result. The queue's deque and its
    pending futures are mutable internal objects; these two counts are enough
    to answer whether messages are backlogged and whether callers are waiting
    for results.

    .. rubric:: Behavior notes

    - ``size`` counts messages that have been enqueued but not dequeued.
      Cancellation does not remove queued messages, so the count need not
      become zero after cancellation. Handling those messages is the
      application's responsibility.
    - ``pending`` counts unresolved waiters for ``message()`` results. It
      decreases when the logical turn resolves those waiters.
    - Do not call ``snapshot()`` on an Agent after ``destroy()``. Use
      ``RuntimeSnapshot.agents[id].loaded`` to observe its pool state.
    - This view does not enumerate queued messages. Subscribe to
      ``on_enqueue`` or ``on_dequeue``, or read the message tree, to inspect
      message content.

    .. seealso:: ``flowing.message.MessageQueue``,
        ``flowing.agent.Agent.message``,
        ``flowing.agent.Agent.enqueue_message``, and
        ``flowing.agent.Agent.cancel_queued``
    """
    size: int
    """Number of enqueued messages not yet dequeued. Cancellation does not
    discard queued messages, so it does not necessarily become zero after a
    cancellation request.
    """
    pending: int
    """Number of unresolved waiters for ``message()`` results.
    """

class RuntimeSnapshot:
    """A consistent, read-only snapshot returned by ``runtime.snapshot()``.

    .. rubric:: Overview

    This is a single-time slice of Runtime-wide storage. It contains the node
    graph (``nodes``), installed plugin names (``plugins``), Agent-pool
    entries (``agents``), provider candidate names (``providers``), a
    read-only copy of configuration overrides (``config_overrides``), and
    registered resource names (``resources``). Each Runtime store has exactly
    one corresponding field in the snapshot; no stores are omitted or
    represented by extra fields. The core does not provide a snapshot
    namespace for plugin state; query that state through the plugin's own
    public API, for example through the plugin instance returned by
    ``runtime.get_plugin(...)``.

    .. rubric:: Usage example

    .. code-block:: python

        from flowing.runtime import Runtime

        async def inspect_runtime(runtime: Runtime) -> None:
            view = runtime.snapshot()
            for node_id, node in view.nodes.items():
                print(node_id, node.type, node.parent_id)
            for agent_id, info in view.agents.items():
                if not info.loaded:
                    agent = await runtime.get_agent(agent_id)

    .. rubric:: Behavior notes

    - All fields in one call describe the same observation time and are
      isolated copies or read-only views. Mutating this value does not change
      Runtime state.
    - By default, all fields are collected. Passing ``keys`` collects only
      the named snapshot fields; every uncollected field is ``None``. Valid
      keys are field names. Request all views that must be compared at the
      same observation time in one call.
    - Snapshot collection has no side effects and, on normal paths including
      shutdown, returns the state that is available without raising.
    - The snapshot excludes node, Agent, and Provider instances; the
      ``_provided`` mapping and its values; sensitive values in the original
      configuration namespace, such as API credentials; and
      ``asyncio.Event``, Task, or Future objects.
    - ``providers`` lists candidate names, not instantiated Provider objects.
      It is an empty list when no model entries have been loaded.
    - There is no precondition beyond having a Runtime, and the call has no
      postcondition because it only observes state.

    .. seealso:: ``flowing.runtime.Runtime.snapshot``,
        ``flowing.snapshot.AgentSnapshot``,
        ``flowing.runtime.Runtime.get_agent``, and
        ``flowing.runtime.Runtime.shutdown``
    """
    nodes: dict[str, NodeInfo]
    """Read-only projection of every node in the shared ``runtime-*``,
    ``workflow-*``, and ``agent-*`` ID space, keyed by ``node_id``. Runtime
    has no lifecycle ``status`` field; ``shutdown()`` returning indicates
    that shutdown has completed.
    """
    plugins: list[str]
    """Names of installed plugins from each plugin's ``name`` attribute.
    Extensions that have not been installed through ``runtime.install(...)``
    do not appear; their absence means they were not installed, not that they
    were installed and then skipped.
    """
    agents: dict[str, AgentInfo]
    """Projection of the Agent-pool registry, keyed by ``agent_id``. It
    includes entries whose instances are not loaded, as indicated by
    ``AgentInfo.loaded``. It does not include per-instance state; use
    ``Agent.snapshot()`` for that separate view.
    """
    providers: list[str]
    """Names of Provider candidates. The list reports names only; lazy
    Provider instantiation is not represented.
    """
    config_overrides: dict[str, Any]
    """Read-only copy of values written through ``set_config``. Mutating the
    copy does not update Runtime configuration. Credential values are not
    included in the copied configuration overrides.
    """
    resources: list[str]
    """Names of registered Resources. The snapshot reports names only.
    """

class AgentSnapshot:
    """A consistent, read-only snapshot returned by ``agent.snapshot()``.

    .. rubric:: Overview

    This is a single-time slice of one Agent's state. It includes identity and
    parent information; message-tree state and cursor; the current logical
    turn; active executions; queue counts; the resolved model view; tool and
    subagent bindings; and an optional context-usage estimate.

    ``RuntimeSnapshot`` answers pool-level questions such as whether an Agent
    has a live instance. ``AgentSnapshot`` answers instance-level questions
    about what a loaded Agent is doing. If the instance may not be loaded,
    inspect ``RuntimeSnapshot.agents[id].loaded`` first.

    .. rubric:: Usage example

    .. code-block:: python

        view = agent.snapshot()
        if view.current_turn is not None:
            print("Messages in current turn:", view.current_turn.message_count)
        for execution in view.executions.values():
            print("Active:", execution.kind, execution.tags)
        hidden_tools = [entry.alias for entry in view.tool_entries if not entry.visible]

    .. rubric:: Behavior notes

    - This snapshot has the same single-call consistency, isolation, and
      observation-only semantics as ``RuntimeSnapshot``.
    - It does not expose cancellation or pause Events; the mutable
      ``TurnContext.message_ids`` or ``pending_messages`` lists; the queue's
      internal deque; pending-turn futures; internal provided values,
      children, or messages; or model credentials.
    - Do not call this method after ``Agent.destroy()``. Use
      ``RuntimeSnapshot.agents[id].loaded`` to inspect pool state. A recovered
      Agent's first snapshot has ``current_turn is None`` because recovery
      does not restore an in-progress turn.
    - It does not contain message content, the LLM-visible schema, or history
      of previous states. Collect history through an application or plugin
      that subscribes to hooks.

    .. seealso:: ``flowing.agent.Agent.snapshot``,
        ``flowing.agent.Agent.current_head_id``,
        ``flowing.message.MessageChain``, ``flowing.agent.TurnContext``,
        ``flowing.agent.Execution``, and ``flowing.snapshot.RuntimeSnapshot``
    """
    node_id: str
    """Node ID of the Agent. It is the same value as ``agent_id`` and
    ``session_id`` and remains unchanged across recovery.
    """
    parent_id: str | None
    """Parent node ID. It is ``None`` only for the Runtime itself; a root
    Agent points to its Runtime's ``node_id``.
    """
    agent_type: str
    """String Agent type name copied from the pool metadata used for recovery.
    """
    paused: bool
    """Projection of ``Agent.paused``. Agent has no lifecycle state machine;
    its current activity is represented by fields such as ``current_turn``,
    ``executions``, and ``paused`` rather than one aggregate status.
    """
    messages: MessageTreeInfo
    """Read-only view of message-tree size and cursor.
    """
    current_head_id: str | None
    """Current tree cursor value in message-ID form, sampled at the same time
    as ``messages.head_id``. It is ``None`` when the tree is empty. This field
    mirrors ``Agent.current_head_id`` for direct access.
    """
    current_turn: TurnContextInfo | None
    """Read-only view of the active logical turn, or ``None`` when no turn is
    executing.
    """
    executions: dict[str, ExecutionInfo]
    """Mapping from active execution IDs to their read-only views. It omits
    cancellation and pause Events and contains only executions still active at
    snapshot time.
    """
    message_queue: MessageQueueInfo
    """Read-only view of message-queue size.
    """
    model: ModelInfo | None
    """Read-only projection of the resolved model configuration, or ``None``
    before model resolution has completed during creation.
    """
    tool_entries: list[EntryInfo]
    """Tool bindings on this Agent, projected by their Agent-local aliases.
    """
    subagent_entries: list[EntryInfo]
    """Subagent bindings on this Agent. Their ``EntryInfo.agent_type`` field
    contains the target Agent type name.
    """
    context_usage: ContextUsageEstimate | None
    """Observation-only estimate returned by ``Agent.estimate_context_tokens``
    at snapshot time. It combines measured anchors with estimates for the
    remaining context; ``usage_ratio`` is not clamped, so a value above one
    indicates overflow. It is not a billing value and is ``None`` before the
    model has been resolved.
    """
