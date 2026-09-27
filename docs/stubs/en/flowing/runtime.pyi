"""The Runtime object-graph root, project entry point, and ``@/`` paths.

.. rubric:: Overview

This module provides :class:`Runtime`, the root container for one running
Flowing project, together with :func:`launch` and :func:`resolve`. It also
re-exports the ``ProvideNode`` protocol and ``inject_from`` helper defined in
``flowing.provide``.

``launch(path, **kwargs)`` is the only supported way to create a Runtime. It
registers the project root in task-local context, imports the project's
``main.py``, and awaits ``main(**kwargs)``. That project entry point constructs
the Runtime, installs plugins, provides application values, mounts or recovers
agents, and returns the Runtime. Calling ``Runtime()`` outside a ``launch``
context raises ``RuntimeError``.

``launch`` only starts the project entry point. It does not parse project
configuration, install plugins, choose between creating and recovering an
Agent, or start a service. The caller chooses how to expose the returned
Runtime, and the project decides whether to create or recover its agents.

.. rubric:: Behavior notes

- Public methods that run hooks or asynchronous lifecycle work are coroutines
  and must be awaited, including ``launch``, ``mount``, ``create_agent``,
  ``recover_agent``, ``get_agent``, ``archive_agent``, ``archive_orphans``,
  and ``shutdown``. Registry, configuration, provide-inject, and snapshot
  methods are synchronous.
- The ``@`` project-root context is isolated by asyncio task. Multiple
  projects can be launched in one process without sharing their roots. A child
  task inherits the context even after ``launch`` resets it when ``main``
  returns. ``resolve("@/...")`` is available inside ``main`` before a Runtime
  instance has been constructed. The host application's own root is an
  application value, not the Flowing ``@/`` root.
- Runtime is the endpoint of the provide-inject chain. Values registered with
  ``Runtime.provide`` are visible to descendants, and later registration of
  the same key replaces the earlier value. Credentials and other secrets must
  not be passed through ``provide`` because descendants can read them.
- Configuration reads prefer values written by ``set_config`` over the
  shallow-merged configuration loaded at construction. The merged sources are
  user configuration, project configuration, and framework defaults, in that
  priority order. Environment variables are read by their consumers, and
  command-line overrides enter this layer through ``set_config``. Before
  merging completes, ``get_config`` raises ``ConfigNotReadyError``. Registering
  a namespace declares which extension owns its validation; it does not
  restrict reads.
- Plugin installation has two phases. ``Runtime.install`` installs global
  registrations; each Agent opts into instance-level behavior from ``setup``.
  Dependency cycles raise ``DependencyError`` when introduced, while missing
  dependencies produce warnings and do not prevent installation.
- Agent creation and recovery are separate pipelines. Each invokes
  ``setup`` once on a new instance, but creation dispatches the create hooks
  and recovery replays the session before dispatching the recovery hooks.
- ``Runtime.snapshot`` is the pull-based, consistent read-only view of global
  state. It omits plugin state and provided values, including credentials.
- ``shutdown`` destroys nodes, shuts down plugins, closes global state views,
  and then releases tasks awaiting the Runtime. The framework exposes this
  through ``shutdown`` and ``await runtime`` rather than a lifecycle-status
  field.
- One process can run multiple Runtimes in separate task contexts. Strong
  process-level isolation, such as for tenants, requires separate processes.

.. seealso::

    - ``flowing.agent`` for Agent creation, recovery, and setup
    - ``flowing.provide`` for the provide-inject protocol and lookup algorithm
    - ``flowing.errors`` for exception types
    - ``flowing.plugins`` for the plugin base class and extension contract
"""

import asyncio
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Generator, TypeVar, overload

from flowing.agent import Agent
from flowing.agent_registry import AGENT_NAMING, AgentRegistry
from flowing.params import ConfigKey, InjectionKey
from flowing.persistence import StateView
from flowing.provide import ProvideNode, inject_from
from flowing.providers import ProviderRegistry
from flowing.snapshot import RuntimeSnapshot
from flowing.tool import Tool, ToolRegistry

T = TypeVar("T")

__all__ = [
    "AGENT_NAMING",
    "ProvideNode",
    "Runtime",
    "inject_from",
    "launch",
    "resolve",
]


async def launch(
    path: str | Path, main_file: str | None = None, **kwargs: Any
) -> "Runtime":
    """Import a Flowing project entry point and return its configured Runtime.

    The entry file must define ``async def main(**kwargs) -> Runtime``. By
    default it is ``<path>/main.py``; ``main_file`` selects another file and
    accepts an absolute path or an ``@``-relative path. The same entry point is
    used whether the caller later exposes the Runtime through a CLI, HTTP,
    Web, tests, or an embedding host. Whether an Agent is created or recovered
    follows from how the project assembles it: ``mount()`` with a fixed
    ``agent_id`` is an idempotent mount — an id already in the pool goes
    through the recovery pipeline, otherwise a new Agent is created. Arguments
    such as ``resume`` are ordinary values forwarded to its ``main`` function.

    .. rubric:: Usage example

    .. code-block:: python

        # @/main.py
        from flowing import Runtime

        async def main() -> Runtime:
            runtime = Runtime()
            # Fixed agent_id → idempotent mount: a second launch recovers
            # the same root instead of creating a new one.
            await runtime.mount("@/root.fya", agent_id="agent-main")
            return runtime

        runtime = await launch("/path/to/flowing-project")
        await runtime

    .. rubric:: Behavior notes

    - The function registers the project root before importing the entry
      module, then awaits ``main(**kwargs)`` and resets the context after
      ``main`` returns. ``flowing.resolve("@/...")`` can therefore be used
      inside ``main`` before constructing the Runtime.
    - The context is task-local. Concurrent calls can launch separate projects
      without mixing their roots. Child tasks inherit the value established by
      their launching task.
    - ``kwargs`` are forwarded to ``main`` without transformation. Parsing
      CLI ``--key value`` arguments belongs to ``flowing.interfaces.cli``.
    - The return value is the Runtime returned by ``main``. Agents mounted by
      that entry point are active and their work-loop tasks are ready. The
      caller should keep the process or task alive, commonly by awaiting the
      Runtime.
    - This function does not load configuration, install plugins, decide
      whether an Agent is new or recovered, or open a service port.

    :param path: Filesystem path to the Flowing project directory.
    :param main_file: Optional replacement entry-file path. The default is
        ``<path>/main.py``.
    :param kwargs: Arguments forwarded to the project's ``main(**kwargs)``.
    :return: The configured Runtime returned by the project entry point.
    :raises FileNotFoundError: If the entry file is missing. Import-time
        exceptions, including a missing ``main`` attribute, propagate without
        being wrapped.

    .. seealso:: :func:`resolve`, :class:`Runtime`, and :meth:`Runtime.mount`
    """
    ...


def resolve(path: str) -> Path:
    """Resolve an ``@/`` path using the project root registered by ``launch``.

    This module-level helper reads the same task-local project-root context as
    ``Runtime.__init__``. It is useful in project ``main`` code that needs to
    load files before it has constructed or retained a Runtime.

    .. rubric:: Usage example

    .. code-block:: python

        config_path = resolve("@/config.yaml")

    .. rubric:: Behavior notes

    - A path beginning with ``@/`` resolves under the current project's root.
    - Other paths use ordinary ``Path`` behavior; a relative path is based on
      the current working directory. The ``./`` and ``../`` source-directory
      rules belong to ``Runtime.resolve_path``.
    - If no project root has been registered by ``launch``, this function
      raises ``RuntimeError`` instead of falling back silently.

    :param path: Path string to resolve.
    :return: The resolved ``Path``.
    :raises RuntimeError: If there is no ``@`` context registered by
        ``flowing.launch``.

    .. seealso:: :func:`launch` and :meth:`Runtime.resolve_path`
    """
    ...


class Runtime(ProvideNode):
    """The object-graph root and global container for one Flowing project.

    .. rubric:: Overview

    A Runtime owns the project's Agent and Workflow nodes, installed plugins,
    global tool registry, Agent pool, configuration, and shared resources. It
    also provides extension registration, node management, provide-inject,
    and snapshot APIs.

    A Runtime is constructed by the project's ``main`` function while it is
    running under ``launch``. Constructing it directly without the registered
    ``@`` context raises ``RuntimeError``. Construction fixes its project and
    persistence roots, registers builtin tools and standard subagents, and
    scans provider candidates and the Agent pool without instantiating
    Providers or Agents.

    Runtime is the endpoint of the provide-inject chain. Values provided here
    are visible to every descendant; ``Runtime.inject`` only reads the
    Runtime-level store.

    .. rubric:: Behavior notes

    - An Agent is registered as it is created. Destroying it removes its live
      instance but retains its pool entry and session, allowing recovery.
      Archiving it removes the live node and pool/catalog entries for its
      subtree but retains the session files; it can no longer be recovered
      automatically. Physical session deletion is a separate application or
      operations task and is not provided by the framework.
    - Shutdown and ``await runtime`` communicate that shutdown work is
      complete; Runtime does not expose a separate lifecycle-status field.
    - Multiple Runtimes can coexist in one process because their project-root
      contexts are isolated by asyncio task.

    .. seealso:: :class:`flowing.agent.Agent` and
        :meth:`flowing.agent.Agent.register_state`
    """

    node_id: str
    """Fixed value ``"runtime-0"`` in the shared node-ID space. This Runtime
    is the provide-inject endpoint and the final parent of its nodes. It
    registers itself as the first entry in ``_nodes`` during initialization.
    """
    runtime: "Runtime"
    """Self-reference required by the ``ProvideNode`` protocol. The Runtime's
    ``runtime`` attribute points to the Runtime itself, marking the end of the
    provide-inject chain.
    """
    project_root: Path
    """Project root captured from the ``@`` context during ``__init__``. It
    is the base for ``resolve_path("@/...")`` and cannot be changed after
    construction.
    """
    tool_registry: ToolRegistry
    """Global registry mapping canonical tool names to ``Tool`` objects.
    ``register_tool`` writes to this registry; Agent tool-call lookup then
    resolves the Agent-local alias through its own binding entries.
    """
    provider_registry: ProviderRegistry
    """Lazy Provider-instance registry keyed by ``providers.yaml`` entry
    names. It is the lookup used by ``Agent.provider_gen`` after provider
    candidates have been scanned during Runtime construction. This is
    separate from the process-level registry of Provider adapter classes.
    """
    agent_registry: AgentRegistry
    """Registry mapping Agent type names, including ``ns::name``, to Agent
    classes. Bare-name lookups can resolve entries in ``default::`` and
    ``builtin::`` namespaces according to the registry precedence rules. It is
    the write target of ``register_agent_type`` and the lookup
    source for ``get_agent_class``. Each Runtime owns its own registry.
    """
    env: MappingProxyType
    """Read-only live view of ``os.environ`` used as the ``env`` entry in
    Parsable rendering contexts. It is created during Runtime initialization
    and reflects the current environment mapping.
    """

    def __init__(self, *, persist_dir: str | Path | None = None) -> None:
        """Initialize a Runtime inside the project context established by ``launch``.

        The constructor fixes ``project_root`` and the persistence root,
        initializes empty stores, and scans provider candidates and the Agent
        pool. These scans do not instantiate Providers or Agents.

        .. rubric:: Usage example

        .. code-block:: python

            class MyRuntime(Runtime):
                ...

            async def main() -> Runtime:
                return MyRuntime()

        .. rubric:: Behavior notes

        - The ``@`` project-root context must already be registered by
          ``flowing.launch``. Otherwise construction raises ``RuntimeError``.
        - Construction initializes the built-in tools, including
          ``subagent-invoke`` and ``finish``, and scans configuration for
          provider candidates and persisted Agent entries. It does not create
          Provider or Agent instances.
        - Multiple Runtime instances may be constructed in separate task
          contexts; they do not share storage.
        - The constructor does not start an Agent work loop or open a service
          port.

        :param persist_dir: Optional persistence root. ``None`` selects
            ``<cwd>/.flowing`` and defers directory creation until the first
            persistence operation. An explicit value is resolved using
            ``resolve_path`` and its directory is created immediately. The
            selected path cannot be changed after construction.
        :raises RuntimeError: If called without the ``@`` context registered
            by ``flowing.launch``.

        .. seealso:: :func:`launch`
        """
        ...

    @property
    def persist_dir(self) -> Path:
        """Return the persistence root fixed when this Runtime was constructed.

        The path is read-only. Whether its directory already exists depends on
        construction: an explicitly supplied path is created immediately,
        while the default path is created at the first persistence operation.
        Hosts, plugins, and subclasses may use this path for their own files;
        a ``FileRecordStore`` does not create its parent directory.
        """
        ...

    def install(self, *plugins: Any) -> None:
        """Install plugins in argument order and register their global capabilities.

        This is the first phase of plugin enablement. Each plugin's
        ``install(runtime)`` method can register global tools, provided values,
        configuration namespaces, Agent types, resources, and global state
        spaces. An Agent opts into instance-level behavior separately, usually
        from its ``setup`` method through a ``use_xxx(agent)`` function.

        .. rubric:: Usage example

        .. code-block:: python

            runtime.install(SkillPlugin())
            runtime.install(CommPlugin(), GuardrailPlugin())

        .. rubric:: Behavior notes

        - Install all plugins before the first ``mount``, ``create_agent``, or
          ``recover_agent`` when their registrations must be available during
          Agent setup. Installing later is allowed, but already created Agents
          do not receive the late plugin's capabilities retroactively.
        - Registering the same provided key again replaces its value.
        - After each installation, the current dependency graph is checked.
          A cycle raises ``DependencyError``; a missing dependency emits a
          warning and does not prevent installation, so plugins can be
          installed in batches.
        - Installing a plugin must not create Agents. The framework does not
          prevent such a call, but its behavior is unsupported.
        - A Runtime can contain only one plugin with a given name. Installing
          a duplicate name raises ``ValueError``.

        :param plugins: Plugin instances to install, in installation order.
        :raises flowing.errors.ConfigNamespaceConflictError: If a plugin
            registers a configuration namespace that is already occupied.
        :raises flowing.errors.DependencyError: If installation creates a
            dependency cycle.
        :raises ValueError: If a plugin with the same name is already
            installed.

        .. seealso:: :meth:`mount` and ``flowing.plugins.Plugin``
        """
        ...

    def get_plugin(self, name: str, *, strict: bool = True) -> Any | None:
        """Return an installed plugin, with strict lookup enabled by default.

        Use the default strict form when the plugin is required, or set
        ``strict=False`` when checking whether it is installed.

        .. code-block:: python

            plugin_dir = runtime.get_plugin("cron").plugin_dir
            if runtime.get_plugin("skill", strict=False) is not None:
                enable_skills()

        This is a synchronous, read-only lookup. It does not instantiate a
        plugin, run ``install``, or resolve dependencies.

        :param name: The plugin's ``name`` class attribute, such as ``"cron"``.
        :param strict: If ``True``, raise ``KeyError`` when the name is
            unknown. If ``False``, return ``None`` for an unknown name.
        :return: The installed plugin, or ``None`` when it is missing and
            ``strict`` is ``False``.
        :raises KeyError: If the plugin is missing and ``strict`` is ``True``.

        .. seealso:: :meth:`install`, ``flowing.plugins.Plugin``, and
            :meth:`snapshot`
        """
        ...

    async def mount(
        self, node: str, *, agent_id: str | None = None, **kwargs: Any
    ) -> ProvideNode:
        """Create or recover an Agent root from a ``.fya`` file or directory.

        ``node`` identifies an Agent definition file, such as
        ``@/root.fya``, or a directory containing ``agent.fya``. This method
        handles Agent roots; Workflow roots are created through
        ``WorkflowPlugin.launch``. It can be called more than once to mount
        multiple roots, and it is not required when an application creates
        Agents directly with ``create_agent``.

        If ``agent_id`` is supplied and already exists in the Agent pool,
        ``mount`` recovers that Agent instead of creating another one. If the
        ID is new, it creates the root with that ID. With ``agent_id=None``,
        each call creates a new root with a generated ID. The ``node``
        reference determines the Agent type; ``agent_id`` only selects
        identity.

        .. rubric:: Usage example

        .. code-block:: python

            await runtime.mount("@/root.fya")
            await runtime.mount("@/agents/coding-agent")
            await runtime.mount("@/root.fya", agent_id="agent-main")

        ``kwargs`` are passed to the Agent's ``setup(**kwargs)`` and persisted
        with its pool metadata, so they are part of its identity and should be
        JSON-serializable.

        :param node: Path to an Agent ``.fya`` file or to a directory
            containing an Agent entry file. Directory candidates are probed in
            this order: ``AGENT.fya`` > ``agent.fya`` > ``<name>.agent.fya`` >
            ``<name>.fya``.
        :param agent_id: Optional fixed ID. If already in the pool, the Agent
            is recovered; otherwise it is created with this ID. ``None``
            generates a new ID for each call.
        :param kwargs: Initialization arguments passed to ``setup``.
        :return: The created or recovered Agent root.
        :raises FileNotFoundError: If the path does not exist.
        :raises ValueError: If ``node`` is neither a valid Agent definition
            file nor a directory containing an Agent entry file. Use
            ``create_agent`` for a Python Agent definition and
            ``WorkflowPlugin.launch`` for a Workflow root.

        .. seealso:: :meth:`create_agent`, :meth:`recover_agent`, and
            ``flowing.plugins.workflow.WorkflowPlugin.launch``
        """
        ...

    async def create_agent(
        self,
        agent_type: str,
        *,
        parent_id: str | None = None,
        agent_id: str | None = None,
        session_dir: str | Path | None = None,
        **kwargs: Any,
    ) -> Agent:
        """Create and register a new Agent through its creation pipeline.

        This is the new-instance path: it resolves the string ``agent_type``,
        creates a fresh ID unless ``agent_id`` is supplied, dispatches the
        creation lifecycle hooks, calls ``setup(**kwargs)``, validates pending
        fields and hook declarations, registers the Agent, and starts its work
        loop. It does not restore a previous session; use ``recover_agent`` for
        that path.

        .. rubric:: Usage example

        .. code-block:: python

            agent = await runtime.create_agent(
                "order-agent", parent_id=None, order_id="123"
            )

        The arguments passed through ``kwargs`` are delivered to
        ``before_create`` and then ``setup`` and are persisted as Agent
        arguments. They should be JSON-serializable.

        .. rubric:: Behavior notes

        - ``parent_id=None`` is stored as the Runtime node ID; a root Agent
          therefore has the Runtime as its actual parent.
        - If ``agent_id`` is supplied, it must be absent from both the pool
          and live-node table. A collision raises ``ValueError``. Generated
          IDs retry on collisions.
        - If the selected session directory already exists, a fixed ID or an
          explicit ``session_dir`` raises ``FileExistsError``. When both the
          ID and directory are automatic, the Runtime generates another ID
          and retries.
        - The Runtime writes the identity metadata, including the original
          arguments, before dispatching ``before_create``. That hook may
          rewrite the arguments passed to ``setup``; those changes do not
          rewrite ``meta.json`` and must be expressed again through the
          separate ``before_recover`` hook during recovery. The in-memory pool
          entry records the final arguments from
          ``before_create``. Non-JSON-serializable arguments are not preserved
          for recovery.
        - ``before_create`` runs only on the creation path, not on recovery.
          Its handlers must come from class-level ``@on`` registrations:
          instance hooks and extension or Composable ``use_xxx`` calls are
          attached during ``setup()``, too late for this hook. It covers all
          creation paths, unlike ``before_tool_call["subagent-invoke"]``,
          which covers only tool-driven subagent creation. At this point the
          instance skeleton and hooks already exist, ``_extra`` is writable,
          and ``register_state()`` can declare keys; writes to the state view
          are immediately available.
        - After ``setup``, the pipeline checks that all pending fields were
          supplied and that every deferred ``@on`` registration found its
          declared hook point. It then writes the final pool entry, registers
          the instance in ``_nodes``, dispatches ``after_create``, and starts
          the resident work-loop Task. An unsettled pending field raises
          ``MissingFieldError``; an ``@on`` hook point still undeclared at the
          end of setup raises ``UnknownHookPointError`` with the pending hook
          and method names.

        :param agent_type: Agent type name, resolved lazily from a registry or
            definition path. This parameter is a string, not a class object.
        :param parent_id: Parent node ID. ``None`` creates a root Agent whose
            actual parent is the Runtime.
        :param agent_id: Optional fixed ``node_id``. If omitted, the Runtime
            generates a unique ID. A supplied ID must not already exist in the
            pool or live-node table.
        :param session_dir: Optional directory for the Agent's persisted
            session. Relative paths are resolved under the Runtime persistence
            root; absolute paths are used as given. ``None`` selects
            ``persist_dir / node_id``. The selected directory is recorded in
            pool metadata for later recovery.
        :param kwargs: Agent initialization arguments passed through the
            creation hook to ``setup``. Arguments should be JSON-serializable;
            session metadata stores the original values, while the in-memory
            pool entry stores the values after ``before_create``.
        :return: The newly created, registered, and active Agent.
        :raises flowing.errors.AgentTypeNotFoundError: If the type cannot be
            resolved.
        :raises ValueError: If a supplied ``agent_id`` is already registered.
        :raises FileExistsError: If the selected session directory already
            exists while the ID or directory is fixed. Fully automatic ID and
            directory collisions are handled by generating another ID.
        :raises flowing.errors.MissingFieldError: If required pending fields
            remain unset after setup.
        :raises flowing.errors.UnknownHookPointError: If a hook declared with
            ``@on`` was not settled by the end of setup.

        .. seealso:: :meth:`recover_agent`,
            ``flowing.agent.Agent.create_subagent``, and
            ``flowing.agent.Agent.setup``
        """
        ...

    async def recover_agent(self, agent_id: str, **override_args: Any) -> Agent:
        """Rebuild an Agent from its pool entry and persisted session.

        Recovery preserves identity: the restored Agent's ``node_id`` is the
        existing ``agent_id``. The Runtime loads its saved arguments, applies
        ``override_args``, restores the session, runs the recovery lifecycle
        hooks and ``setup``, validates pending declarations, registers the
        instance, and starts its work loop. Recovery uses
        ``before_recover``/``after_recover`` rather than the create hooks.

        .. rubric:: Usage example

        .. code-block:: python

            agent = await runtime.recover_agent("agent-1")
            agent = await runtime.recover_agent("agent-1", order_id="789")

        .. rubric:: Behavior notes

        - The existing pool entry is required; a missing ID raises ``KeyError``.
        - The parent chain is recovered as needed so provide-inject lookup can
          reach the Runtime. A missing parent produces a warning and recovery
          of this Agent continues.
        - Persisted messages and state are restored before ``setup``. Arguments
          from the pool are used unless replaced by ``override_args``.
        - Completed messages and queued, unconsumed messages are preserved.
          An in-progress logical turn is not restored; unpersisted messages and
          a torn final record are discarded. Queued messages are processed in
          a new turn after recovery.
        - Recovery does not eagerly restore the Agent's children. A child is
          rebuilt when requested; recovering a child can recursively recover
          its ancestors.
        - ``_restore()`` runs before ``before_recover`` and ``setup``, so
          setup reads replayed state and its changes are not overwritten by a
          later replay. After validation and model resolution, the Agent is
          registered before ``after_recover``; the work loop starts only after
          that hook completes.
        - ``override_args`` replaces matching saved arguments, but the
          framework does not validate the replacements against the arguments
          used during creation.

        :param agent_id: Existing Agent ID in the pool registry.
        :param override_args: Values that override the persisted initialization
            arguments.
        :return: The recovered and registered Agent.
        :raises KeyError: If ``agent_id`` is not in the pool registry.
        :raises flowing.errors.MissingFieldError: If a pending field remains
            unset after ``setup``.
        :raises flowing.errors.UnknownHookPointError: If a hook declared with
            ``@on`` is not settled by the end of ``setup``.

        .. seealso:: :meth:`create_agent`, :meth:`get_agent`,
            ``flowing.agent.Agent.setup``, and ``flowing.agent.Agent._restore``
        """
        ...

    def get_node(self, node_id: str, *, strict: bool = True) -> ProvideNode | None:
        """Look up a live node in the Runtime's shared node table.

        This lookup does not recover destroyed nodes. It is used by provide-
        inject traversal and subtree operations; Agent, Workflow, and Runtime
        IDs share the same table.

        :param node_id: Node ID, including its type prefix.
        :param strict: If ``True``, raise ``KeyError`` when no live node is
            registered. If ``False``, return ``None``.
        :return: The live node, or ``None`` when ``strict`` is ``False`` and
            the ID is absent.
        :raises KeyError: If the node is absent and ``strict`` is ``True``.

        .. seealso:: :meth:`get_agent` and ``flowing.provide.inject_from``
        """
        ...

    async def get_agent(self, agent_id: str, *, strict: bool = False) -> Agent | None:
        """Return a live Agent or recover a registered Agent on demand.

        If the Agent is live, this returns that instance. If its pool entry
        exists but its instance is absent, the Runtime calls
        ``recover_agent(agent_id)`` and returns the rebuilt instance. Callers
        do not need to distinguish a child that has never been loaded from one
        that was destroyed while its session was retained.

        .. rubric:: Usage example

        .. code-block:: python

            agent = await runtime.get_agent(child_id)
            result = await agent.query("Continue the task")

        .. rubric:: Behavior notes

        - Recovery is asynchronous and restores this Agent's session, but does
          not recursively load its children. They are restored lazily when
          requested.
        - The lookup does not use fuzzy matching.
        - ``strict`` applies only when the ID is absent from the pool. Its
          default, ``False``, returns ``None``; ``True`` raises ``KeyError``.
          A live or recoverable pool entry is returned either way.

        :param agent_id: Agent ID in the pool registry.
        :param strict: Whether to raise ``KeyError`` if the ID is not in the
            pool. Defaults to ``False``.
        :return: The live or recovered Agent, or ``None`` when it is absent
            and ``strict`` is ``False``.
        :raises KeyError: If the ID is absent and ``strict`` is ``True``.

        .. seealso:: :meth:`recover_agent` and
            ``flowing.agent.Agent.destroy``
        """
        ...

    async def archive_agent(self, node_id: str) -> list[str]:
        """Archive a node and its entire subtree from Runtime while retaining the files.

        This core Runtime operation removes ``node_id`` and all descendants
        from ``_nodes`` (destroying live instances), ``_agent_pool`` (which
        contains Agent entries only), and the global core catalog at
        ``states["core"]["agents"]``. It leaves every session directory and
        file (``tree.jsonl``, ``core.jsonl``, ``state.jsonl``, and ``meta.json``)
        intact. Any registered node may be supplied, including an Agent or a
        Workflow; both appear in ``_nodes`` and have ``_parent_id`` and
        ``destroy()``.

        Unlike ``destroy()``, which drops only the instance while retaining
        its pool key and catalog entry for on-demand recovery, archiving also
        removes those keys. Afterward, ``get_agent()`` returns ``None`` and
        ``recover_agent()`` cannot find the node. Runtime has forgotten it;
        the files remain for audit or manual recovery by external operations.

        .. rubric:: Behavior notes

        - Runtime collects the subtree from two sources: the pool's
          ``parent_agent_id`` links (including destroyed Agent entries) and
          ``_nodes``' ``_parent_id`` links (including nodes not in the pool),
          then deduplicates the results. It removes each archived node from
          its parent's ``child_ids`` and persists that change, destroys each
          still-live node (a Workflow's ``destroy()`` also destroys its child
          Agents), then removes pool and core-catalog entries. Removing an
          entry absent from the pool is idempotent.
        - The returned IDs include ``node_id`` and every descendant. After
          archiving, none of those nodes remain in ``_nodes`` or
          ``_agent_pool``, and none remain in the core catalog; their session
          files remain.
        - No session directory or file is deleted, and this method does not
          recursively recover anything. Archived nodes are absent from
          ``_nodes``, so ``shutdown()`` naturally skips them.
        - The Runtime root (``parent_id == "runtime-0"``) may be archived.
          Its session directory remains in an archived state: with no catalog
          entry it is not automatically recoverable. A later explicit
          ``create_agent()`` that collides with that directory raises
          ``FileExistsError``.

        :param node_id: ID of the node to archive.
        :return: IDs of the archived node and all of its descendants.
        :raises KeyError: If ``node_id`` is absent from both ``_nodes`` and
            ``_agent_pool``; no fuzzy matching is performed.

        .. seealso:: :meth:`get_agent`, :meth:`recover_agent`, and
            :meth:`archive_orphans`
        """
        ...

    async def archive_orphans(self) -> list[str]:
        """Archive every pool entry with a dangling parent, including its subtree.

        This core Runtime operation finds Agent entries in ``_agent_pool``
        whose ``parent_agent_id`` is absent from both ``_nodes`` and
        ``_agent_pool``. It archives each such orphan recursively through
        :meth:`archive_agent`, including any descendants, and returns all
        archived IDs.

        Orphans can arise when a parent is not persisted (for example, a
        Workflow's runtime state) but a child Agent is persisted through the
        standard creation pipeline. If the process crashes and the parent
        disappears, the child's ``parent_agent_id`` points nowhere. Normal
        shutdown lets the parent destroy its descendants; after a crash that
        cascade cannot run, so this method provides explicit cleanup after
        recovery.

        .. rubric:: Behavior notes

        - A dangling parent is non-empty and absent from both ``self._nodes``
          and ``self._agent_pool``. The Runtime root (``runtime-0``), live
          Workflow children, and entries whose parent remains in the pool are
          not orphans.
        - Each orphan is archived recursively. Orphan roots cannot overlap
          as parent and child, because a child whose parent remains in the
          pool is not itself selected.
        - If there are no orphans, this method returns ``[]`` and may be
          called at any time.
        - Runtime does not determine why a parent is missing. It also removes
          remnants of a previously archived parent, although archiving a
          parent normally archives its whole subtree and should not leave
          such remnants.
        - When recovery encounters an orphaned parent,
          ``recover_agent()`` only emits ``warnings.warn`` and continues; it
          does not archive automatically. Archiving remains an explicit
          operations action performed here.

        :return: IDs of all archived nodes, or ``[]`` if there are no orphans.

        .. seealso:: :meth:`archive_agent` and
            ``flowing.plugins.workflow.Workflow.destroy``
        """
        ...

    def provide(self, key: str | InjectionKey[Any], value: Any) -> None:
        """Register a value visible from every node in this Runtime's tree.

        ``Runtime.provide`` is the root provider in the provide-inject chain.
        Application values such as a host workspace root belong here rather
        than in the Flowing ``@/`` project-path system. ``InjectionKey[T]``
        supplies static type information; the stored key is normalized to a
        string and its type does not travel through the node tree.

        .. rubric:: Usage example

        .. code-block:: python

            runtime.provide("workspace_root", workspace_root)

        .. rubric:: Behavior notes

        - Provide values needed during Agent setup before calling ``mount`` or
          ``create_agent``. A missing value during setup raises
          ``MissingProvideError``. Providing or replacing a value later is
          allowed, and subsequent ``inject`` calls see the new value.
        - Providing the same key again replaces the previous value. Keys do
          not have collision protection; extensions should use distinct naming
          conventions.
        - Values are not serialized or persisted. Do not provide API keys or
          other credentials: every descendant can read values from this scope.

        :param key: String key or an ``InjectionKey`` used for static typing.
        :param value: Value to make available to descendants.

        .. seealso:: ``flowing.provide.ProvideNode``,
            ``flowing.provide.inject_from``, and
            ``flowing.params.InjectionKey``
        """
        ...

    def inject(self, key: str | InjectionKey[T]) -> T:
        """Look up a value in the Runtime-level provide store.

        Runtime is the end of the parent chain, so this method checks only the
        Runtime's own provided values. It does not read configuration
        overrides or registered resources; those are separate stores.

        :param key: Provide key to look up.
        :return: The value registered for ``key``.
        :raises flowing.errors.MissingProvideError: If the key is not present.

        .. seealso:: ``flowing.provide.inject_from`` and
            ``flowing.errors.MissingProvideError``
        """
        ...

    def get_config(
        self, key: str | ConfigKey[T], default: T | None = None
    ) -> T | None:
        """Read a value from the merged configuration or runtime overrides.

        The lookup prefers a value written with ``set_config``. Otherwise, it
        reads the shallow-merged configuration: user configuration takes
        precedence over project configuration, which takes precedence over
        framework defaults. Environment variables are read by their
        individual consumers; command-line overrides enter this lookup
        through ``set_config``. ``ConfigKey[T]`` also lets type checkers verify
        that ``default`` matches the key's type.

        .. rubric:: Usage example

        .. code-block:: python

            timeout = runtime.get_config("agent.timeout", default=60)
            language = runtime.get_config("i18n.default_lang")

        .. rubric:: Behavior notes

        - Configuration must have finished merging during Runtime
          construction. Before then, this method raises
          ``ConfigNotReadyError``. After construction it can be called from
          setup, hooks, tools, or application code, and each read is evaluated
          at call time rather than cached.
        - Merging is shallow: a higher-priority value replaces a value with
          the same key, unoverridden values remain, and lists are replaced as
          whole values rather than merged.
        - This method does not validate which namespace a key belongs to and
          does not write to configuration files. Use ``set_config`` for a
          process-local override.
        - Built-in defaults include ``agent.timeout`` (60),
          ``agent.max_turns`` (20), ``agent.max_depth`` (10), and
          ``runtime.log_level`` (``"info"``).

        :param key: Dotted configuration key or ``ConfigKey[T]``.
        :param default: Value returned when the key is absent. Its type must
            match ``ConfigKey[T]`` when that typed key form is used.
        :return: The configured value or ``default``.
        :raises flowing.errors.ConfigNotReadyError: If called before
            configuration merging has completed.

        .. seealso:: :meth:`register_config_namespace`,
            :meth:`set_config`, and ``flowing.params.ConfigKey``
        """
        ...

    def set_config(self, key: str, value: Any) -> None:
        """Set a process-local configuration override.

        This is the runtime override channel for application configuration.
        For example, a project's ``main`` function can read its own config
        file and use this method to override a framework setting.

        .. code-block:: python

            if "agent.timeout" in product_config:
                runtime.set_config("agent.timeout", product_config["agent.timeout"])

        A later value for the same key replaces the earlier one. Overrides are
        not persisted and are lost when the process exits. They are visible to
        later ``get_config`` calls; the framework does not broadcast changes
        through a reactive notification system.

        :param key: Dotted configuration key, such as ``"agent.timeout"``.
        :param value: Override value.

        .. seealso:: :meth:`get_config`
        """
        ...

    @property
    def config(self) -> dict[str, Any]:
        """Return the nested view of the merged project configuration.

        Parsable templates use this nested mapping, for example
        ``{{ config.agent.timeout }}``. ``get_config`` instead reads the
        flattened dotted-key form. This property reconstructs a read-only
        view; mutating the returned dictionary does not change Runtime state.
        It does not include the separate runtime override layer managed by
        ``set_config``.
        """
        ...

    @overload
    def get_resource(self, name: str) -> Any: ...
    @overload
    def get_resource(self, name: str, type_hint: type[T]) -> T: ...
    def get_resource(self, name: str, type_hint: type[T] | None = None) -> Any:
        """Return a Runtime-wide shared resource by its registered name.

        Resources can be shared across tasks and Agents, such as a knowledge
        base or connection pool. They are managed by Runtime and do not follow
        the Agent parent chain, so they are accessed through a separate
        registry rather than provide-inject. Agents and tool callables can use
        their corresponding convenience API to reach this lookup.

        .. code-block:: python

            runtime.register_resource("kb", knowledge_base)
            kb = runtime.get_resource("kb", KnowledgeBase)

        ``type_hint`` is for IDE and static type-checker inference only; this
        method does not perform a runtime type check. Resources must be
        registered explicitly before use and are not created lazily.

        :param name: Resource registry name.
        :param type_hint: Optional expected type for static inference only.
        :return: The registered resource instance.
        :raises flowing.errors.ResourceNotFoundError: If ``name`` is not
            registered.

        .. seealso:: :meth:`register_resource` and
            ``flowing.agent.Agent.get_resource``
        """
        ...

    def register_tool(self, tool: Tool | str, *, namespace: str | None = None) -> None:
        """Register a global tool for later Agent bindings.

        The global registry stores the executable Tool and its default
        LLM-facing declaration. Agent-specific aliases and overrides belong
        to the Agent-level ``ToolEntry``. The argument can be an existing Tool
        instance or a ``.fya``/``.py`` definition path, which is resolved and
        loaded before registration.

        Namespace-qualified keys have the form ``ns::name``. A duplicate key
        raises an error. For an instance, the default namespace is
        ``default``; for a file, the namespace is derived from its directory
        unless ``namespace`` is supplied. Explicit ``namespace`` is
        independent of the file path. Custom-namespace resources must be
        referenced by their qualified name. A tool registered under
        ``default`` can take precedence in bare-name lookup over a builtin;
        the builtin remains addressable as ``builtin::name``.

        :param tool: Tool instance or path to a Tool definition file.
        :param namespace: Explicit namespace. If omitted, an instance uses
            ``default`` and a file uses the namespace derived from its path.

        .. seealso:: ``flowing.tool.Tool``, ``flowing.tool.ToolEntry``, and
            ``Runtime.tool_registry``
        """
        ...
    def register_agent_type(
        self,
        agent: type[Agent] | str,
        *,
        name: str | None = None,
        namespace: str | None = None,
    ) -> None:
        """Register an Agent class or definition for use as a subagent type.

        This delegates to the Agent registry and resolves the class lazily
        when an Agent is created or invoked. The argument can be an Agent
        subclass or a ``.fya``/``.py`` path. If ``name`` is omitted for a class,
        its class name is normalized to kebab case. A file path is registered
        under the namespace inferred from its directory unless an explicit
        name or namespace is provided.

        .. code-block:: python

            runtime.register_agent_type(PaymentAgent, namespace="billing")
            # References use billing::payment-agent.

        Duplicate qualified keys raise ``AgentTypeConflictError``. A class's
        declared ``name`` must agree with the registered name or
        ``NameMismatchError`` is raised. The default namespace is ``default``;
        extensions are encouraged to use their own namespace. Types in a
        custom namespace must be referenced by a qualified name.

        :param agent: Agent subclass or definition-file path.
        :param name: Optional registration-name override. If omitted, the
            class name or file identity determines the name.
        :param namespace: Namespace. Class registrations default to
            ``default``; file registrations use their directory when omitted.

        .. seealso:: :meth:`get_agent_class`,
            ``flowing.agent_registry.AgentRegistry``, and
            ``flowing.agent.Agent``
        """
        ...

    def register_config_namespace(self, name: str, schema: Any) -> None:
        """Declare which extension owns validation for a config namespace.

        The framework interprets its built-in ``agent`` and ``runtime``
        namespaces. Other keys pass through to the extension that declares
        their namespace. The extension defines the schema's validation,
        defaults, and type conversion. Declaration is not access control:
        unregistered namespaces remain readable through ``get_config``.

        .. code-block:: python

            runtime.register_config_namespace("i18n", I18nSchema)

        Multiple extensions cannot register the same namespace. Registration
        occurs while the plugin is installed.

        :param name: Namespace name, which is the first segment of a dotted
            configuration key.
        :param schema: Extension-defined validation/default/conversion
            declaration.
        :raises flowing.errors.ConfigNamespaceConflictError: If the namespace
            is already registered.

        .. seealso:: :meth:`get_config` and
            ``flowing.errors.ConfigNamespaceConflictError``
        """
        ...

    def register_resource(self, name: str, instance: Any) -> None:
        """Register a shared resource managed by this Runtime.

        Resources are shared across Agents and tasks, for example a large
        index or database connection pool. Use provide-inject for values that
        follow an Agent's lifetime; use a resource for an object shared beyond
        one Agent tree. Resources do not need to inherit from a framework base
        class.

        .. code-block:: python

            runtime.register_resource("kb", knowledge_base)
            runtime.register_resource("orders", database_pool)

        Names are unique within a Runtime. Register resources before mounting
        the root Agent. The framework does not manage the resource instance's
        disposal.

        :param name: Resource registry name.
        :param instance: Any object to register.
        :raises flowing.errors.ResourceNameConflictError: If the name is
            already registered.

        .. seealso:: :meth:`get_resource` and
            ``flowing.errors.ResourceNameConflictError``
        """
        ...

    def snapshot(self, *, keys: set[str] | None = None) -> RuntimeSnapshot:
        """Return a consistent read-only view of Runtime-wide state.

        The result includes node and Agent-pool entries, plugin names,
        provider candidates, configuration overrides, and registered resource
        names. It is intended for observation by tests, the REPL snapshot
        command, and the ``serve`` snapshot endpoint. Plugin state is queried
        through the plugin's own read-only API.

        :param keys: Snapshot field names to collect. ``None`` collects all
            fields; unrequested fields are ``None``. Request fields that must
            be compared at the same observation time in one call.
        :return: A ``RuntimeSnapshot`` isolated from Runtime storage.

        .. seealso:: ``flowing.agent.Agent.snapshot`` and
            ``flowing.snapshot.RuntimeSnapshot``
        """
        ...

    def set_model_tags(self, path: str | Path) -> None:
        """Select the source file for model-tag mappings.

        This optional initialization step should be called before mounting an
        Agent. It overrides the default ``model-tags.yaml`` location. Source
        priority is this explicit path, then ``FLOWING_MODEL_TAGS``, then the
        default under ``$FLOWING_CONFIG_HOME``. Only one file is selected;
        files are not merged. The file is parsed when a model configuration is
        resolved, without caching.

        .. code-block:: python

            runtime.set_model_tags("@/model-tags.yaml")

        Paths accept the Runtime path-prefix rules. An undefined model tag has
        no fallback and causes model resolution to fail.

        :param path: Path to the model-tag mapping file.

        .. seealso:: ``flowing.model.ModelConfig`` and
            :meth:`resolve_path`
        """
        ...

    def set_models(self, path: str | Path) -> None:
        """Select the source file for model definitions.

        This optional initialization step should be called before mounting an
        Agent. It overrides the default ``models.yaml`` source selected by
        ``FLOWING_MODELS_PATH`` or ``$FLOWING_CONFIG_HOME``. The path is
        registered here and parsed when model configuration is resolved; the
        result is not cached.

        .. code-block:: python

            runtime.set_models("@/models.yaml")

        :param path: Path to the models file.

        .. seealso:: :meth:`set_model_tags` and :meth:`resolve_path`
        """
        ...

    def set_providers(self, path: str | Path) -> None:
        """Select a providers file and rebuild the provider candidate registry.

        This optional initialization step should be called before mounting an
        Agent. It overrides the default ``providers.yaml`` source selected by
        ``FLOWING_PROVIDERS_PATH`` or ``$FLOWING_CONFIG_HOME``. Unlike model
        and tag files, the provider candidate registry is rebuilt immediately.
        This does not instantiate Providers. Credentials in the selected file
        remain subject to the provider configuration's security rules.

        .. code-block:: python

            runtime.set_providers("@/providers.yaml")

        :param path: Path to the providers file.

        .. seealso:: :meth:`set_models`, :meth:`set_model_tags`, and
            ``flowing.providers.load_provider_candidates``
        """
        ...

    def register_state(self, namespace: str, backend: str = "file") -> StateView:
        """Open or retrieve a persistent state space owned by the Runtime.

        Runtime-level state is independent of any Agent and is stored in a
        file named for its namespace under the persistence root. The built-in
        ``core`` namespace contains the Agent registry and installed-plugin
        list; ``default`` is available through ``state`` for Runtime and small
        plugin values. Plugins can use their own namespace for global
        persistent data. Opening a space replays its persisted values, while
        deciding when to derive plugin runtime structures from those values is
        the plugin's responsibility. Re-registering the same namespace returns
        the same ``StateView`` without opening another store.

        :param namespace: State-space name. Extensions commonly use a prefix
            such as ``"comm.routes"``.
        :param backend: Persistence backend. The only supported value is
            ``"file"``.
        :return: The namespace's ``StateView``.
        :raises ValueError: If ``backend`` is not ``"file"``.

        .. seealso:: :attr:`states`, :attr:`state`, and
            ``flowing.agent.Agent.register_state``
        """
        ...

    @property
    def states(self) -> Mapping[str, StateView]:
        """Return a read-only mapping of registered global state spaces.

        The mapping supports key lookup, membership tests, and ``get``. State
        read/write behavior is provided by each ``StateView``.

        .. seealso:: :meth:`register_state` and :attr:`state`
        """
        ...

    @property
    def state(self) -> StateView:
        """Return the Runtime's default persistent state space.

        This is equivalent to ``states["default"]`` and stores Runtime
        lifecycle values and small plugin-owned keys.
        """
        ...

    def resolve_path(self, path: str, *, source_dir: Path | None = None) -> Path:
        """Resolve a Flowing resource path according to its path prefix.

        ``@/`` paths are relative to ``project_root``; ``./`` paths are
        relative to ``source_dir``; and ``../`` paths are relative to its
        parent. Ordinary relative paths containing a slash or backslash also
        use ``source_dir``. Backslashes are accepted as path separators, and
        ``~`` and ``~/...`` expand to the user's home directory. Absolute paths
        are accepted. Bare names are registry names, not paths. This method is
        used by definition references and template includes; it does not check
        whether a path exists or expand globs.

        Paths outside the project root are allowed. When paths are represented
        in framework objects or messages, paths inside the project use
        ``@/``-relative form and paths outside it remain absolute.

        .. code-block:: python

            runtime.resolve_path("@/tools/search.py")
            runtime.resolve_path("./subagents/*", source_dir=agent_dir)

        :param path: Path string using ``@/``, ``./``, ``../``, a home-relative
            prefix, an absolute path, or a relative path containing a separator.
        :param source_dir: Base directory for relative paths.
        :return: The resolved ``Path``.
        :raises ValueError: If a relative path requires ``source_dir`` and no
            source directory was supplied.

        .. seealso:: :meth:`to_project_path`, :func:`resolve`, and
            ``flowing.paths.resolve_path``
        """
        ...

    def to_project_path(self, absolute: Path) -> str:
        """Represent an absolute path relative to the Flowing project when possible.

        This is the display-side counterpart to ``resolve_path``. A path under
        the project root becomes an ``@/`` path; a path outside the root is
        returned as its absolute string. This is lexical conversion and does
        not access the filesystem.

        :param absolute: Absolute path to represent.
        :return: An ``@/`` project path or the unchanged absolute path string.

        .. seealso:: :meth:`resolve_path`
        """
        ...

    async def shutdown(self) -> None:
        """Shut down Runtime-owned nodes, plugins, and global state views.

        Shutdown requests cooperative cleanup; it does not synchronously wait
        for the whole process to exit. This method completes the cleanup below
        before returning and does not use ``os._exit`` or force-kill the
        process (an application may use such a fallback if shutdown itself
        hangs). A typical flow is a signal handler calling this method,
        ``await runtime`` being released, and then the process exiting.

        .. rubric:: Behavior notes

        Cleanup order is an invariant: recursively destroy all nodes (Agent
        work-loop Tasks are cancelled and their tree/state backends are drained
        and closed by ``destroy()``; Workflow destruction cascades to its
        child Agents); await each plugin's ``shutdown()`` in installation
        order; close all global state views; then set ``_shutdown_event``.
        Plugin shutdown failures are logged and cleanup continues. Global
        state views are closed after plugins so plugin shutdown methods may
        still write global state. When ``await runtime`` is released, all
        cleanup has finished.

        Repeated calls are safe: a call after cleanup has completed returns
        immediately. An empty Runtime proceeds directly to plugin shutdown.
        Session directories are not deleted (pool keys remain until their
        directories are explicitly deleted), and this method does not wait
        for a blocked ``await runtime`` waiter to be scheduled after the event
        is set.

        .. rubric:: Usage example

        .. code-block:: python

            runtime = await flowing.launch(path)
            # A separate task or signal handler calls await runtime.shutdown().
            await runtime

        .. seealso:: :meth:`__await__` and
            ``flowing.agent.Agent.destroy``
        """
        ...

    def __await__(self) -> Generator[Any, None, None]:
        """Wait until ``shutdown`` has completed its cleanup.

        Awaiting a Runtime keeps the host task alive; it does not process
        messages or run periodic work. An empty Runtime can also be awaited.
        The wait ends only after node destruction, plugin shutdown, and global
        state-store closure have completed.

        .. code-block:: python

            runtime = await flowing.launch(path)
            await runtime

        .. seealso:: :meth:`shutdown`
        """
        ...

    def get_agent_class(
        self, agent_type: str, *, source_dir: Path | None = None
    ) -> type[Agent]:
        """Resolve an Agent type name to an Agent class.

        This is the public type-resolution entry point used by creation and
        recovery. It accepts a namespace-qualified name, a bare name, or a
        definition path. Qualified names such as ``myplugin::payment`` only
        look in the registry. A bare name can search files relative to
        ``source_dir`` before registry lookup; without ``source_dir`` it uses
        the registry, preferring ``default`` over ``builtin`` names. Paths
        such as ``./``, ``@/``, absolute paths, and ``file.py::ClassName`` are
        resolved and loaded directly. Glob expansion is performed by the
        assembly layer, not here.

        For exact file candidate order and disambiguation rules, see
        ``AgentRegistry.get``.

        :param agent_type: Qualified name, bare name, or definition path.
        :param source_dir: Base directory for bare-name file lookup and
            ``./``/``../`` paths.
        :return: The resolved Agent class.
        :raises flowing.errors.AgentTypeNotFoundError: If no registry entry or
            file candidate resolves.
        :raises flowing.errors.FormatError: If a Python module contains other
            than exactly one Agent subclass and no ``::ClassName`` disambiguates it.
        :raises flowing.errors.NameMismatchError: If the declared Agent name
            conflicts with the name inferred from its reference.

        .. seealso:: :meth:`register_agent_type`, :meth:`create_agent`,
            ``flowing.agent_registry.AgentRegistry``, and
            ``flowing.tool.ToolRegistry.get``
        """
        ...
