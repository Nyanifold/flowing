"""Named exception types and their public contracts in ``flowing.errors``.

All named framework exceptions except :class:`Intercepted` derive from
:class:`FlowingError`. The hierarchy groups failures by responsibility:
configuration, dependency injection, hooks, tools, registries, resources,
providers, plugin dependencies, communication, declarative formats, and
compilation. Several types also inherit from a shared registry category:
:class:`ToolNotFoundError` is both a tool and registry lookup error, while
:class:`ToolNameConflictError` is both a tool and registry conflict error.

Four exceptions attach directly to ``FlowingError`` rather than to a category:
:class:`EntryNameConflictError` describes an alias collision in one Agent's
binding layer; :class:`UnpairedToolCallError` reports a message-tree pairing
invariant violation; and :class:`FormatVersionError` and
:class:`CorruptionError` describe JSON Lines persistence files. Persistence
errors have no intermediate base class, which keeps them distinct from
:class:`FormatError` for declarative files. ``EntryNameConflictError`` is
available as ``flowing.errors.EntryNameConflictError`` but is intentionally
not listed in the module's ``__all__``.

``Intercepted`` is a separate control-flow signal. It inherits directly from
Python's ``Exception``, not ``FlowingError``, so broad handling of framework
errors does not swallow an intentional hook interception. Import-time
programming mistakes, such as registering an object that is not a
``Provider`` subclass or has no non-empty ``name`` class attribute, remain
built-in ``ValueError`` exceptions; recoverable registration-name collisions
use :class:`ProviderNameConflictError` instead.

The inheritance structure is summarized below. ``ToolNotFoundError`` and
``ToolNameConflictError`` each inherit from both their tool-specific class and
the corresponding shared registry class.

.. code-block:: text

    Exception
    ├── Intercepted                         # intentional hard-stop signal
    └── FlowingError                        # common framework exception root
        ├── ConfigError
        │   ├── ConfigNotReadyError
        │   └── ConfigNamespaceConflictError
        ├── ProvideError
        │   └── MissingProvideError
        ├── HookError
        │   ├── UnknownHookPointError
        │   └── DuplicateHookPointError
        ├── ToolError
        │   ├── MissingSchemaError
        │   ├── ToolNotFoundError            # also a RegistryNotFoundError
        │   ├── ToolNameConflictError        # also a RegistryConflictError
        │   ├── UnknownToolError
        │   ├── AmbiguousToolError
        │   ├── AmbiguousMcpSourceError
        │   └── MissingMcpSourceError
        ├── RegistryNotFoundError
        │   ├── AgentTypeNotFoundError
        │   ├── SkillNotFoundError
        │   └── ToolNotFoundError
        ├── RegistryConflictError
        │   ├── AgentTypeConflictError
        │   ├── SkillNameConflictError
        │   └── ToolNameConflictError
        ├── ResourceError
        │   ├── ResourceNameConflictError
        │   └── ResourceNotFoundError
        ├── ProviderError
        │   ├── ContextLengthError
        │   ├── RequestTooLargeError
        │   ├── RateLimitedError
        │   ├── QuotaExhaustedError
        │   ├── ServerError
        │   ├── NetworkError
        │   ├── ProviderTimeoutError
        │   ├── AuthenticationError
        │   ├── InvalidRequestError
        │   ├── ContentPolicyError
        │   ├── MissingEnvironmentVariableError
        │   └── ProviderNameConflictError
        ├── DependencyError
        ├── CommError
        │   ├── DuplicateEndpointError
        │   ├── SignalDeliveryError
        │   └── SignalTimeoutError
        ├── EntryNameConflictError
        ├── FormatVersionError
        ├── CorruptionError
        ├── UnpairedToolCallError
        ├── FormatError
        │   ├── MissingFieldError
        │   ├── MissingContextError
        │   ├── NameMismatchError
        │   └── ReservedAttributeError
        └── CompileError
            └── ArtifactModifiedError

Provider-call exceptions are classified and dispatched through
``on_provider_error``. The hierarchy classifies rate limits, server failures,
network failures, and provider timeouts as retryable; context-length,
oversized-request, exhausted-quota, authentication, invalid-request, and
content-policy failures are not retryable as-is. These are classifications,
not retry decisions: the framework core does not retry by default, and a hook
handler determines recovery. In particular, quota exhaustion is different
from transient rate limiting even though both may use HTTP 429.

Exception attributes are the machine-readable contract. Message text is for
people and must not be parsed by callers. The special ``Intercepted.reason``
is intentionally used as the blocked Tool result on the ``tool_call()`` path;
arbitrary exception messages are not thereby made part of the LLM interface.
Named exception fields are set from constructor arguments unless an exception
documents an explicit default.
Public exception types are stable across framework versions.

.. rubric:: Example

.. code-block:: python

    import asyncio

    from flowing.errors import Intercepted, RateLimitedError

    async def retry_policy(agent, ctx):
        if isinstance(ctx.error, RateLimitedError):
            await asyncio.sleep(ctx.error.retry_after or 1.0)
            ctx.can_continue = True  # Retry within the same turn.
        return ctx

    async def approval_policy(agent, tool_call):
        if not await approval_service.approve(tool_call):
            raise Intercepted("The user denied this action", payload={"tool": tool_call.name})
        return tool_call

    async def setup(self):
        self.hooks.on_provider_error(retry_policy, by="retry-policy")
        self.hooks.before_tool_call(approval_policy, by="approval-policy")

.. rubric:: Behavior

- Catching ``FlowingError`` catches framework-defined exceptions without
  catching built-in Python exceptions or third-party exceptions. Intermediate
  classes such as ``ProviderError`` and ``ToolError`` group related failures;
  callers can catch a category or a concrete subclass.
- ``Intercepted`` is an intentional hard stop, such as approval denial,
  security blocking, or a permission check. It is not a ``FlowingError`` and
  is not dispatched as an ordinary error.
- Import-time authoring mistakes use ``ValueError`` and should not be caught
  by recovery logic.
- All provider-call failures pass through ``on_provider_error``. Setting
  ``can_continue=True`` retries within the same logical turn; otherwise the
  turn ends with an error outcome while the Agent remains alive. The
  ``after_turn`` hook still runs on every path, and messages already produced
  are persisted. Errors such as ``ContextLengthError`` are dispatched even
  though repeating the same request will fail again; handlers may compress
  context, select another model, or only record the failure.
- The built-in ``providers.yaml`` loader does not raise
  ``MissingEnvironmentVariableError`` for a missing ``{{env.X}}`` value. It
  substitutes an empty string and emits ``warnings.warn``; the missing
  credential's effect appears on the first call through
  ``on_provider_error``. Applications may raise the exception in their own
  strict configuration validation.
- ``on_provider_error`` is the only error hook. There are no
  ``on_tool_error``, ``on_subagent_error``, ``on_error``, or ``before_error``
  hooks. A tool business error is a normal ``ToolResult(status="error")``
  visible to the LLM and triggers no error hook. Tool-code crashes,
  subagent-call exceptions, and ordinary exceptions raised by hook handlers
  propagate directly; dispatch does not catch them or continue to later
  handlers.
- Built-in ``KeyError``, ``asyncio.CancelledError``, and ``RuntimeError`` are
  used according to Python semantics and are not wrapped in this hierarchy.

.. seealso:: :class:`flowing.hooks.HookRegistry`,
    :class:`flowing.agent.TurnContext`, :class:`flowing.tool.ToolResult`,
    :class:`flowing.providers.Provider`, and :mod:`flowing.composables.retry`.
"""
from pathlib import Path
from typing import Any


class FlowingError(Exception):
    """Common root for named Flowing framework exceptions.

    Catching this class catches framework-defined errors without also catching
    built-in Python or third-party exceptions. It does not imply that every
    failure is recoverable. The framework does not silently wrap third-party
    exceptions; a provider adapter must explicitly classify and raise a
    concrete :class:`ProviderError` subclass. :class:`Intercepted` is
    deliberately outside this hierarchy. This class defines no constructor
    arguments or structured fields; subclasses define their own data.
    """


class ConfigError(FlowingError):
    """Category for configuration-readiness and configuration-namespace errors.

    The concrete cases are reading configuration before it is ready and two
    extensions registering the same namespace. These deployment- or
    declaration-time errors fail immediately rather than silently degrading.
    Catch this category with ``except ConfigError``; the framework raises its
    concrete subclasses. Configuration errors are not retryable: fix the
    configuration or the call site before restarting.

    .. seealso:: :class:`ConfigNotReadyError`,
        :class:`ConfigNamespaceConflictError`,
        :meth:`flowing.runtime.Runtime.get_config`.
    """


class ConfigNotReadyError(ConfigError):
    """Raised when ``Runtime.get_config()`` is called before configuration is ready.

    Configuration becomes ready after the precedence layers (environment
    variables, command line, user-level, project-level, and defaults) have
    been shallow-merged. A typical premature call occurs at module import
    time. After the merge, configuration may be read from ``setup()``, hook
    callbacks, tool callables, and code after ``main()``.

    This error has no structured fields and uses a fixed English message. It
    indicates a programming mistake: move the read to a ready phase instead
    of catching the exception. Readiness is tracked independently by each
    Runtime.

    .. rubric:: Example

    .. code-block:: python

        # At module scope, before configuration is ready: raises this error.
        TIMEOUT = runtime.get_config("agent.timeout")

        # In setup(), after configuration is ready: this is allowed.
        async def setup(self):
            self.timeout = self.runtime.get_config("agent.timeout", default=60)

    .. seealso:: :meth:`flowing.runtime.Runtime.get_config`,
        :class:`ConfigError`.
    """
    def __init__(self) -> None: ...


class ConfigNamespaceConflictError(ConfigError):
    """Raised when multiple extensions register the same configuration namespace.

    ``Runtime.register_config_namespace(name, schema)`` declares which
    extension validates a namespace, supplies its defaults, and converts its
    values. Two extensions claiming one namespace would impose conflicting
    validation rules, so registration fails immediately. A namespace that no
    extension has registered may still be read without raising this error.

    Registration declares validation responsibility; it does not restrict
    access. Any code may read any namespace.

    .. rubric:: Example

    .. code-block:: python

        runtime.register_config_namespace("i18n", I18nSchema)
        runtime.register_config_namespace("i18n", OtherSchema)  # Raises this error.

    ``namespace`` contains the conflicting name for diagnostics; the
    framework does not arbitrate between registrants. This is an installation-
    time programming error and should not be caught.

    .. seealso:: :meth:`flowing.runtime.Runtime.register_config_namespace`,
        :class:`ConfigError`.
    """
    namespace: str
    """Conflicting configuration namespace name, for diagnostics only."""
    def __init__(self, namespace: str) -> None: ...


class ProvideError(FlowingError):
    """Category for errors in the provide-inject scope chain.

    ``inject`` searches from the current Agent subtree through Workflow and
    Runtime, which is the end of the chain. The current concrete error occurs
    when no value is found at that endpoint. Lookup is live rather than
    cached, so a later ``provide`` update is visible to the next ``inject``.

    Catch this category with ``except ProvideError``; the framework raises a
    concrete subclass.

    .. seealso:: :class:`MissingProvideError`,
        :meth:`flowing.agent.Agent.inject`, :func:`flowing.runtime.inject_from`.
    """


class MissingProvideError(ProvideError):
    """Raised when ``inject(key)`` reaches Runtime without finding the key.

    The lookup walks from the current node toward the root. It is the only
    current error raised for a failed runtime injection, and it is not cached:
    providing a value later makes a subsequent lookup succeed.

    ``key`` stores the missing provide key. An ``InjectionKey[T]`` is
    represented by its ``name`` string; the exception does not guess a nearby
    key. ``inject`` has no default-value argument, so callers wanting a
    fallback must catch this error and supply one themselves. This is a
    structural, non-retryable error. It is the runtime counterpart to
    ``DependencyError``'s static plugin-dependency validation.

    .. rubric:: Example

    .. code-block:: python

        async def setup(self):
            user_id = self.inject("user_id")  # Raises if no ancestor provides it.

    .. seealso:: :meth:`flowing.agent.Agent.inject`,
        :class:`flowing.params.InjectionKey`, :class:`DependencyError`.
    """
    key: str
    """Missing provide key; an ``InjectionKey`` is represented by its ``name``."""
    def __init__(self, key: str) -> None: ...


class HookError(FlowingError):
    """Category for hook-point declaration and lookup errors.

    The concrete cases are accessing an undeclared hook point and declaring
    the same hook name with conflicting owners. Plugin loading or reloading
    can catch this category to handle declaration failures without catching
    unrelated subsystem errors. Ordinary exceptions raised by hook handlers
    are not ``HookError``: dispatch propagates them and does not run later
    handlers; there is no fallback error hook.

    .. seealso:: :class:`UnknownHookPointError`,
        :class:`DuplicateHookPointError`, :class:`flowing.hooks.HookRegistry`.
    """


class UnknownHookPointError(HookError):
    """Raised when a hook point is accessed before it has been declared.

    Looking up ``self.hooks.<name>`` only finds an existing hook point; it
    does not create one. A name must be predeclared by the framework or by an
    extension using ``declare()``. This makes misspellings fail at
    registration time instead of silently creating a hook that is never
    dispatched. Accessing an extension hook on an Agent where that extension
    is not installed also raises this error; an unused extension has no
    runtime cost.

    ``name`` is the exact undeclared name, with no fuzzy-match suggestion.
    This is a programming error: correct the name, declare the hook, or enable
    its extension.

    .. rubric:: Example

    .. code-block:: python

        self.hooks.before_skill_load(handler)  # Raises if use_skill(self) was not installed.

    .. seealso:: :meth:`flowing.hooks.HookRegistry.declare`, :class:`HookError`.
    """
    name: str
    """Exact hook-point name that was accessed but not declared."""
    def __init__(self, name: str) -> None: ...


class DuplicateHookPointError(HookError):
    """Raised when ``declare()`` cannot distinguish two declarations of one hook name.

    A conflict occurs for the same name with different ``by`` owners, or when
    both declarations have ``by=None``. Repeating a declaration with the
    same owner is idempotent and returns the existing ``HookList`` instead.
    The ``by`` argument is required by ``declare()``; framework-predeclared
    hooks use ``by="core"``. The first declaration owns the hook semantics,
    including its value type and dispatch timing.

    ``name``, ``existing_by``, and ``new_by`` identify the conflicting hook
    and the two owner identifiers. This is an installation-time programming
    error and should not be caught.

    .. rubric:: Example

    .. code-block:: python

        self.hooks.declare("on_signal", by="comm", match_on="type")
        self.hooks.declare("on_signal", by="audit")  # Different owner: raises.
        self.hooks.declare("on_signal", by="comm", match_on="type")  # Idempotent.

    .. seealso:: :meth:`flowing.hooks.HookRegistry.declare`, :class:`HookError`.
    """
    name: str
    """Name of the conflicting hook point."""
    existing_by: str | None
    """Owner identifier on the existing declaration, if supplied."""
    new_by: str | None
    """Owner identifier on the new conflicting declaration, if supplied."""
    def __init__(self, name: str, existing_by: str | None, new_by: str | None) -> None: ...


class RegistryConflictError(FlowingError):
    """Category for key conflicts in the Tool, Agent-type, and Skill registries.

    Registering a duplicate fully qualified ``ns::name`` key raises a
    concrete subclass. The same resource name may exist in different
    namespaces. Registry conflicts are installation-time errors, are not
    caught by callers, and have no overwrite switch. This is a registry-wide
    conflict, not an Agent binding alias conflict; the latter is reported by
    :class:`EntryNameConflictError`.

    This class has no fields of its own. Catch it to handle conflicts across
    registries, or catch a concrete subclass for one registry.

    .. seealso:: :class:`AgentTypeConflictError`,
        :class:`SkillNameConflictError`, :class:`ToolNameConflictError`,
        :class:`RegistryNotFoundError`.
    """


class RegistryNotFoundError(FlowingError):
    """Category for failed lookup in the Tool, Agent-type, and Skill registries.

    A registry lookup or its associated file-candidate search raises a
    concrete subclass when it cannot resolve a canonical name, qualified
    name, or path-shaped reference. Failure is explicit rather than returning
    ``None``; registry contents do not change automatically at runtime, so
    the failure is not retryable. The unresolved reference is carried by the
    subclass's ``name`` field. Agent binding aliases are a separate lookup
    layer and use errors such as ``UnknownToolError`` instead.

    Catch this category for cross-registry probing, or catch a concrete
    subclass for one registry.

    .. seealso:: :class:`AgentTypeNotFoundError`,
        :class:`SkillNotFoundError`, :class:`ToolNotFoundError`.
    """


class AgentTypeNotFoundError(RegistryNotFoundError):
    """Raised when an Agent type reference cannot be resolved from registries or files.

    ``AgentRegistry.get()`` (the resolver behind
    ``Runtime.get_agent_class()``) accepts qualified names, bare names, and
    path-shaped references. Qualified names use an exact registry key; bare
    names use the bare-name registry view (``default::`` before
    ``builtin::``) and a directed file search; path-shaped references search
    candidate paths for ``.fya`` or hand-written ``.py`` definitions.
    Creation, recovery, and ``subagent-invoke`` type-resolution failures use
    this exception.

    ``name`` preserves the unresolved reference exactly as supplied. Lookup
    fails explicitly rather than returning ``None``; callers may catch the
    exception for probing before registration. It is not retryable while the
    registry remains unchanged.

    .. seealso:: :class:`flowing.agent_registry.AgentRegistry`,
        :meth:`flowing.runtime.Runtime.get_agent_class`,
        :class:`RegistryNotFoundError`.
    """
    name: str
    """Unresolved Agent-type reference, unchanged (qualified name, bare name, or path)."""
    def __init__(self, name: str) -> None: ...


class AgentTypeConflictError(RegistryConflictError):
    """Raised when an Agent type is registered under an existing ``ns::name`` key.

    ``AgentRegistry.register()`` (the registration implementation behind
    ``Runtime.register_agent_type()``) rejects a duplicate key. Silently
    replacing it could redirect existing references, including assembled
    entries that already recorded the ``registry_key``. Register a
    replacement under another key instead.

    This is a global registry-key conflict, unlike
    ``EntryNameConflictError``, which is per-Agent and concerns binding
    aliases. ``key`` stores the conflicting fully qualified key. Different
    namespaces may use the same bare type name. This installation-time error
    has no overwrite switch and should not be caught.

    .. seealso:: :meth:`flowing.agent_registry.AgentRegistry.register`,
        :class:`ToolNameConflictError`, :class:`EntryNameConflictError`.
    """
    key: str
    """Conflicting fully qualified registry key, such as ``default::worker``."""
    def __init__(self, key: str) -> None: ...


class SkillNameConflictError(RegistryConflictError):
    """Raised when a Skill is registered under an existing fully qualified key.

    ``SkillRegistry.register()`` rejects duplicate ``ns::name`` keys rather
    than silently replacing an implementation already referenced by another
    registration. Use a different key to register a replacement. Identical
    bare names in different namespaces may coexist. This installation-time
    error has no overwrite switch and should not be caught.

    ``key`` stores the conflicting fully qualified registry key.

    .. seealso:: :class:`flowing.plugins.skills.registry.SkillRegistry`,
        :class:`RegistryConflictError`.
    """
    key: str
    """Conflicting fully qualified registry key, such as ``default::review``."""
    def __init__(self, key: str) -> None: ...


class SkillNotFoundError(RegistryNotFoundError):
    """Raised when a Skill reference cannot be resolved through registry or file lookup.

    ``SkillRegistry.get()`` accepts qualified names, bare names, and
    path-shaped references. This error also covers a missing or malformed
    definition file named by an explicit path. It is used when Skill
    assembly through ``use_skill`` or resolution by ``SkillLoadTool`` fails.

    ``name`` preserves the unresolved reference. ``detail`` optionally
    carries diagnostics such as candidate paths tried, and is ``None`` when
    there are no additional details. The failure is not retryable while
    registry contents remain unchanged.

    .. seealso:: :class:`flowing.plugins.skills.registry.SkillRegistry`,
        :class:`RegistryNotFoundError`.
    """
    name: str
    """Unresolved Skill reference, unchanged (qualified name, bare name, or path)."""
    detail: str | None
    """Optional diagnostic details, such as candidate paths tried; otherwise ``None``."""
    def __init__(self, name: str, detail: str | None = None) -> None: ...


class ToolError(FlowingError):
    """Category for Tool definition, registration, and lookup errors.

    This category covers the ``script``, ``mcp``, ``cli``, and ``request``
    tool types. It is deliberately distinct from errors returned by a Tool
    while performing its business operation: those are normal
    ``ToolResult(status="error")`` values visible to the LLM, not exceptions
    in this hierarchy. Tool execution crashes are not wrapped as
    ``ToolError``; they propagate as ordinary exceptions.

    Catch this category for Tool-system errors; the framework raises concrete
    subclasses.

    .. seealso:: :class:`flowing.tool.Tool`,
        :class:`flowing.tool.ToolResult`, :class:`flowing.tool.ToolRegistry`.
    """


class MissingSchemaError(ToolError):
    """Raised when a Tool's parameter schema is missing and cannot be inferred.

    This occurs when a bare script-tool function lacks parameter type
    annotations, or when a ``.fya`` declaration for a ``cli`` or ``request``
    Tool omits the required ``args`` schema (those types have no inference
    source). An explicitly declared ``args`` schema has the highest priority,
    so missing annotations do not raise this error when that schema exists.

    ``name`` is the canonical Tool name. ``param`` is the parameter without
    a type annotation, or ``None`` when the entire schema is missing. This is
    a definition-time, fail-fast, non-retryable error.

    .. seealso:: :class:`flowing.tool.ScriptTool`, :mod:`flowing.params`,
        :class:`ToolError`.
    """
    name: str
    """Canonical name of the Tool whose schema is missing."""
    param: str | None
    """Unannotated parameter name, or ``None`` if the entire schema is absent."""
    def __init__(self, name: str, param: str | None = None) -> None: ...


class ToolNotFoundError(ToolError, RegistryNotFoundError):
    """Raised when a canonical Tool name is absent from the Tool registry.

    ``ToolRegistry.get()`` looks up the canonical registered name. This is a
    different namespace from the Agent binding alias (``ToolEntry``'s
    ``name_alias``); a missing alias raises :class:`UnknownToolError`.
    Multiple inheritance also makes this a ``RegistryNotFoundError``, so
    cross-registry lookup handling can catch it through that shared category.

    ``name`` is the missing canonical Tool name. Lookup fails explicitly
    rather than returning ``None``; callers may catch it for probing before
    registration. It is not retryable while the registry is unchanged.

    .. seealso:: :class:`flowing.tool.ToolRegistry`,
        :class:`UnknownToolError`, :class:`RegistryNotFoundError`.
    """
    name: str
    """Canonical Tool name that was not registered."""
    def __init__(self, name: str) -> None: ...


class ToolNameConflictError(ToolError, RegistryConflictError):
    """Raised when a canonical Tool name is registered more than once.

    ``ToolRegistry.register()`` rejects duplicates regardless of Tool type,
    including separate configurations for one MCP server; use distinct
    canonical names. Silent replacement could redirect every Agent binding
    that references the first registration. Reuse the existing registration
    when the same instance is intended. This is also a
    ``RegistryConflictError``, so it participates in cross-registry conflict
    handling.

    This registry-level canonical-name conflict is distinct from a duplicate
    Agent binding alias, which raises ``EntryNameConflictError``. The error
    has no overwrite switch, unlike provider-adapter registration, and should
    not be caught.

    ``name`` is the conflicting canonical Tool name.

    .. seealso:: :meth:`flowing.tool.ToolRegistry.register`,
        :class:`EntryNameConflictError`.
    """
    name: str
    """Canonical Tool name that conflicts with an existing registration."""
    def __init__(self, name: str) -> None: ...


class EntryNameConflictError(FlowingError):
    """Raised when entries in one Agent binding layer use the same alias.

    The Agent-level binding layer covers Tool, Skill, and subagent entries,
    whose keys are aliases. Duplicate aliases are rejected whether they come
    from repeated declarations in a ``.fya`` ``tools:``, ``skills:``, or
    ``subagents:`` list, or from programming calls such as
    ``Agent.add_tool()``. Duplicates are always invalid within one lifecycle;
    different Agents may bind the same alias independently.

    This is a per-Agent, LLM-facing binding conflict, unlike
    ``ToolNameConflictError`` for a global registry canonical name.
    ``alias`` is the duplicate alias and ``kind`` is ``"tool"``, ``"skill"``,
    or ``"subagent"``. This definition- or installation-time programming
    error should not be caught. A plugin may avoid it by checking first and
    skipping an existing user definition; only an unchecked duplicate write
    raises this error.

    .. rubric:: Example

    .. code-block:: python

        self.add_tool("make-payment", alias="pay")
        self.add_tool("other-payment", alias="pay")  # Raises: alias is already used.

    .. seealso:: :class:`ToolNameConflictError`,
        :meth:`flowing.agent.Agent.add_tool`.
    """
    alias: str
    """Alias shared by the conflicting entries."""
    kind: str
    """Binding category: ``"tool"``, ``"skill"``, or ``"subagent"``."""
    def __init__(self, alias: str, kind: str) -> None: ...


class UnknownToolError(ToolError):
    """Raised when ``tool_call()`` cannot find the supplied alias in an Agent's bindings.

    LLM-issued Tool calls are resolved only against the Agent-level binding
    table by alias; resolution does not fall back to the registry's canonical
    name. This is intentional because fallback would bypass the binding layer,
    where Agents may expose aliases or overrides. A typical case is an LLM
    inventing a name absent from ``Context.tools``.

    ``name`` is the unresolved alias supplied by the LLM. The core does not
    catch this exception; application code may validate before
    ``before_tool_call`` or catch it itself. It is not automatically
    retryable, though the LLM may correct its call on a later turn. The error
    is raised only when the alias does not exist; ``visible=False`` affects
    model visibility, not this lookup rule.

    .. seealso:: :meth:`flowing.agent.Agent.tool_call`,
        :class:`flowing.tool.ToolEntry`, :class:`ToolNotFoundError`.
    """
    name: str
    """Unresolved Agent binding alias supplied by the LLM."""
    def __init__(self, name: str) -> None: ...


class AmbiguousToolError(ToolError):
    """Raised when one script Tool file contains multiple incompatible definitions.

    Directed lookup of a script Tool raises this error if one ``.py`` file
    contains both an ``@flowing_tool``-decorated function and a ``Tool``
    subclass, or contains multiple decorated functions. These forms imply
    different Tool-definition paths; choosing one by source order or
    implicitly merging them would make behavior depend on file contents.

    ``path`` is the ambiguous ``.py`` file path as a string. This is a
    definition-time fail-fast error. A ``.tool.fya`` file alongside a
    same-named ``.py`` file is not ambiguous because ``.fya`` takes
    precedence. Using ``callable:`` to refer to an already decorated function
    violates the mutually exclusive declaration channels and instead raises
    ``FormatError``.

    .. seealso:: :class:`flowing.tool.ScriptTool`,
        :func:`flowing.tool.flowing_tool`, :class:`ToolError`.
    """
    path: str
    """Path, as a string, of the file with ambiguous Tool definitions."""
    def __init__(self, path: str) -> None: ...


class AmbiguousMcpSourceError(ToolError):
    """Raised when an MCP Tool declares both ``command`` and ``url`` as its source.

    An MCP Tool must declare exactly one connection source: ``command`` for a
    local stdio subprocess or ``url`` for a remote service. Declaring both
    leaves the connection mode ambiguous and raises this definition-time
    error. ``name`` is the canonical MCP Tool name with conflicting source
    declarations.

    .. seealso:: :class:`MissingMcpSourceError`, :class:`ToolError`.
    """
    name: str
    """Canonical MCP Tool name with conflicting source declarations."""
    def __init__(self, name: str) -> None: ...


class MissingMcpSourceError(ToolError):
    """Raised when an MCP Tool declares neither ``command`` nor ``url``.

    An MCP Tool must declare one connection source, and there is no default
    source the framework can infer. This definition-time error is the
    counterpart to ``AmbiguousMcpSourceError``, which is raised when both
    sources are declared. ``name`` is the canonical MCP Tool name missing a
    source declaration.

    .. seealso:: :class:`AmbiguousMcpSourceError`, :class:`ToolError`.
    """
    name: str
    """Canonical MCP Tool name with no source declaration."""
    def __init__(self, name: str) -> None: ...


class ResourceError(FlowingError):
    """Category for registering and accessing Runtime-owned shared Resources.

    A Resource is a shared instance, such as a database connection pool,
    available across Agents and tasks. The concrete errors are duplicate
    registration and lookup of an unregistered instance. Resources are
    orthogonal to provide-inject: use provide for values tied to an Agent
    lifecycle, and a Resource for values shared across multiple Agents or
    tasks. Resources do not use the inject chain and have their own error
    category.

    ``get_resource(name, type_hint=...)`` uses ``type_hint`` for IDE type
    inference only; it does not perform a runtime ``isinstance`` check, so
    there is no type-mismatch exception. Catch this category for Resource
    errors; the framework raises concrete subclasses.

    .. seealso:: :meth:`flowing.runtime.Runtime.register_resource`,
        :meth:`flowing.runtime.Runtime.get_resource`,
        :meth:`flowing.agent.Agent.get_resource`.
    """


class ResourceNameConflictError(ResourceError):
    """Raised when a Resource name is registered more than once.

    ``Runtime.register_resource(name, instance)`` rejects an existing name.
    A Resource is a globally shared instance: silently replacing it could
    leave Agents holding the old object while later lookups return a new one.
    Register Resources before mounting the root Agent. This startup-time
    programming error is not retryable and should not be caught.

    ``name`` is the conflicting Resource name.

    .. seealso:: :meth:`flowing.runtime.Runtime.register_resource`,
        :class:`ResourceError`.
    """
    name: str
    """Resource name that conflicts with an existing registration."""
    def __init__(self, name: str) -> None: ...


class ResourceNotFoundError(ResourceError):
    """Raised when a requested Resource has not been registered.

    ``Runtime.get_resource(name)`` and its ``Agent.get_resource`` convenience
    delegate raise this error when no instance is registered. Resource
    access is not declared in ``args`` or the inject chain, so a missing
    instance becomes visible at the read site. Raising explicitly avoids
    downstream secondary errors such as ``AttributeError`` from using
    ``None``.

    ``name`` is the missing Resource name. Callers may catch the exception
    for an optional capability fallback; the failure is not retryable while
    registration remains unchanged.

    .. seealso:: :meth:`flowing.runtime.Runtime.get_resource`,
        :class:`ResourceError`.
    """
    name: str
    """Resource name that could not be found."""
    def __init__(self, name: str) -> None: ...


class ProviderError(FlowingError):
    """Category and adapter-interoperability contract for provider failures.

    Provider adapters must convert native SDK and HTTP exceptions into a
    concrete subclass of this class. Turn-level error decisions through
    ``on_provider_error`` then depend on stable categories rather than on
    SDK-specific exception types. Adapters should raise a concrete subclass,
    not a bare ``ProviderError``.

    The optional structured fields ``provider``, ``model``, ``status_code``,
    ``request_id``, and ``retry_after`` default to ``None`` and are available
    for logging, billing, and policy. ``retry_after`` belongs on the base
    class because any response, including 503/529 overloads, may carry the
    ``Retry-After`` value; a retry handler can use ``retry_after or
    default_backoff`` without checking the exception subtype. The fields are
    diagnostic; retry decisions belong to policy.

    Rate limits, server errors, network errors, and provider timeouts are
    classified as retryable. Context-length, oversized-request, quota,
    authentication, invalid-request, and content-policy errors are not
    retryable as-is. The core does not retry: ``provider_gen()`` neither
    catches nor retries these errors. The logical-turn layer catches them and
    dispatches ``on_provider_error``. Messages are for people; fields are for
    code.

    .. seealso:: :class:`flowing.providers.Provider`,
        :mod:`flowing.composables.retry`.
    """
    provider: str | None
    """Provider entry name from ``providers.yaml``, when known; may be set on load-time errors."""
    model: str | None
    """Model ID used for the call, when known; may be ``None`` for load-time errors."""
    status_code: int | None
    """HTTP status code, or ``None`` for non-HTTP failures such as connection or load errors."""
    request_id: str | None
    """Request ID returned by the server, such as ``x-request-id``; otherwise ``None``."""
    retry_after: float | None
    """Server-suggested retry delay in seconds, or ``None``; policy supplies any fallback."""
    def __init__(self, message: str = "", *, provider: str | None = None,
                 model: str | None = None, status_code: int | None = None,
                 request_id: str | None = None, retry_after: float | None = None) -> None: ...


class ContextLengthError(ProviderError):
    """Provider call failed because the assembled context exceeds the model's token limit.

    The adapter raises this while processing the request or response. It is
    dispatched through ``on_provider_error`` like other provider-call
    failures. Repeating the same request predictably fails, so the default
    retry policy does not retry it. A handler may compress conversation
    history, select a model with a larger context window, or only record the
    failure; compression and truncation are policy, not automatic error-path
    behavior.

    The inherited ``provider`` and ``model`` fields identify the call. If the
    handler leaves ``can_continue=False``, the turn ends with an error outcome,
    ``after_turn`` still runs, already produced messages are persisted, and
    the Agent remains alive. After recovery, a handler may set
    ``can_continue=True`` to retry within that turn.

    This error concerns token count. :class:`RequestTooLargeError` concerns
    request-body bytes (HTTP 413) and needs a different recovery action.

    .. seealso:: :class:`RequestTooLargeError`, :class:`ProviderError`,
        :class:`flowing.context.Context`.
    """


class RequestTooLargeError(ProviderError):
    """Provider rejected a request body that exceeds its byte-size limit (HTTP 413).

    This is distinct from ``ContextLengthError``, which means the token count
    is too high. Compressing conversation history does not solve an oversized
    body; the relevant recovery is to remove media attachments before
    resending. Some providers, such as Vertex, also use HTTP 413 for an
    excessively long prompt, so adapters classify by the response semantics
    and message rather than status code alone.

    The error is dispatched through ``on_provider_error``. A handler may
    remove attachments and set ``can_continue=True``, or leave it false to
    end the turn. Repeating the identical request is not useful, so this
    category is classified as non-retryable; the recovery action is policy.
    It inherits the diagnostic fields from ``ProviderError``.

    .. seealso:: :class:`ContextLengthError`, :class:`ProviderError`.
    """


class RateLimitedError(ProviderError):
    """Provider applied transient rate limiting, commonly HTTP 429.

    Waiting may allow a later request to succeed. The core only classifies
    the error; ``use_retry()`` or a custom ``on_provider_error`` handler
    decides whether to retry and how long to back off. A server-provided
    ``Retry-After`` delay is normalized into ``retry_after`` so policy need
    not parse provider-specific response headers.

    This category is retryable, but has no built-in attempt count or backoff.
    Without a retry handler, the turn ends with an error outcome. It is
    distinct from :class:`QuotaExhaustedError`: both may be HTTP 429, but
    this means requests are temporarily too frequent, while quota exhaustion
    will not resolve by waiting.

    .. seealso:: :class:`QuotaExhaustedError`, :class:`ProviderError`,
        :mod:`flowing.composables.retry`.
    """


class QuotaExhaustedError(ProviderError):
    """Provider account quota or balance is exhausted (HTTP 429 or a provider-specific code).

    This differs from transient rate limiting: waiting does not restore
    exhausted quota. Repeated retries waste requests; human or application
    intervention is needed, such as adding credit or changing credentials or
    providers. This class deliberately does not subclass
    ``RateLimitedError``: doing so would cause ``except RateLimitedError`` or
    an ``isinstance`` retry check to misclassify exhausted quota. The type
    hierarchy itself is part of the retry classification.

    The error is dispatched through ``on_provider_error`` and is classified
    as non-retryable. Flowing does not inspect balances or initiate payment;
    recovery belongs to application policy. Diagnostic fields are inherited
    from ``ProviderError``.

    .. seealso:: :class:`RateLimitedError`, :class:`ProviderError`.
    """


class ServerError(ProviderError):
    """Provider returned a server-side failure, usually HTTP 5xx.

    A 5xx failure is classified as retryable because server-side faults are
    often temporary; policy still decides whether to retry. This category is
    separate from 4xx request failures, for which repeating the same request
    is generally ineffective. ``status_code`` stores the specific status,
    and overload responses such as 503 or 529 may also set ``retry_after``.

    The exception is dispatched through ``on_provider_error`` and inherits
    the common fields from ``ProviderError``.

    .. seealso:: :class:`InvalidRequestError`, :class:`ProviderError`.
    """


class NetworkError(ProviderError):
    """Network-layer failure prevented a provider request or response from completing.

    Connection, DNS, TLS, and similar transient network failures may occur
    before a request reaches the provider or while its response is in
    transit. An adapter maps its HTTP client's native connection errors to
    this category. Network failures differ from HTTP failures that include a
    response status, which may need different recovery policy.

    ``status_code`` is ``None`` because there was no HTTP response. The error
    is dispatched through ``on_provider_error`` and classified as retryable;
    policy decides whether and when to retry, usually immediately or after a
    short backoff.

    .. seealso:: :class:`ProviderTimeoutError`, :class:`ProviderError`.
    """


class ProviderTimeoutError(ProviderError):
    """Provider request exceeded its adapter's time budget.

    This provider-layer timeout is classified as retryable, with retry
    decisions made by ``on_provider_error`` policy. It deliberately does
    not inherit from Python's built-in ``TimeoutError``; an
    ``except TimeoutError`` clause does not catch this exception.

    .. seealso:: :class:`NetworkError`, :class:`ProviderError`.
    """


class AuthenticationError(ProviderError):
    """Provider rejected credentials, commonly with HTTP 401 or 403.

    Invalid or expired API keys and insufficient permissions do not resolve
    by waiting, so this category is non-retryable by default. The default
    ``use_retry()`` policy lets it pass without setting ``can_continue``.
    Credentials usually require human intervention, such as replacing a key
    or correcting configuration. An application handler may override the
    default classification after changing credentials.

    The exception is dispatched through ``on_provider_error``. Credential
    contents are not placed in its fields or message text, so API keys are
    not included in error messages or persisted by this contract.

    .. seealso:: :class:`MissingEnvironmentVariableError`,
        :class:`ProviderError`.
    """


class InvalidRequestError(ProviderError):
    """Provider rejected an invalid request, commonly with HTTP 400.

    Invalid fields or unsupported parameter combinations are classified as
    non-retryable because the same request will fail again. A model lacking
    a requested capability, such as vision support for an image, may also be
    reported this way; the framework core does not prevalidate model
    capabilities. The error is dispatched through ``on_provider_error`` and
    carries the common ``ProviderError`` fields.

    .. seealso:: :class:`ContextLengthError`, :class:`ProviderError`.
    """


class ContentPolicyError(ProviderError):
    """Provider refused generation under its content-safety policy.

    The provider may reject either input or output content. This is not a
    transient availability failure or rate limit: the content is disallowed
    rather than temporarily unavailable. The category is dispatched through
    ``on_provider_error`` and is classified as non-retryable. Rewriting the
    input or notifying the user is an application decision, not a framework
    retry behavior.

    .. seealso:: :class:`ProviderError`.
    """


class MissingEnvironmentVariableError(ProviderError):
    """A strict provider loader requires an environment variable that is unset.

    This is a load-time error for configuration that requires a variable to
    exist. The built-in ``providers.yaml`` loader does not raise it: while
    substituting ``{{env.VAR}}``, it replaces a missing variable with an
    empty string, emits ``warnings.warn``, and continues loading. This lets a
    configuration contain unused provider entries whose credentials are
    absent. The missing credential's actual consequence, such as HTTP 401,
    appears on the first call through ``on_provider_error``. Applications
    may raise this public exception from their own strict validation when
    fail-fast behavior is required. A ``{{`` sequence without the ``{{env.``
    prefix is left unchanged and does not raise this error.

    This load-time error is not dispatched through ``on_provider_error``.
    ``var_name`` is the missing variable without the ``{{env.`` prefix, and
    ``entry`` is the provider entry that references it. It is a deployment
    error; fix the environment and restart. Environment-variable references
    in ``ModelConfig`` are evaluated by ``Parsable`` before each
    ``provider_gen()`` call; failures there are ordinary evaluation errors,
    not this exception.

    .. rubric:: Example

    .. code-block:: python

        # Application-owned strict validation; the built-in loader is lenient.
        if required_var not in os.environ:
            raise MissingEnvironmentVariableError(required_var, entry)

    .. seealso:: :func:`flowing.providers.load_provider_candidates`,
        :class:`AuthenticationError`, :class:`ProviderError`,
        :class:`flowing.parsable.Parsable`.
    """
    var_name: str
    """Missing environment variable name, without the ``{{env.`` prefix."""
    entry: str
    """Provider configuration entry that references the variable."""
    def __init__(self, var_name: str, entry: str) -> None: ...


class ProviderNameConflictError(ProviderError):
    """Provider adapters share a canonical registration name without ``override=True``.

    ``register_provider()`` raises this when an adapter's ``name`` class
    attribute is already registered and the new registration did not request
    an override. It occurs during import-time discovery or Runtime
    initialization, including imports triggered by ``FLOWING_PROVIDER_MODULES``
    and ``flowing_provider_*`` scans; it is not a provider-call failure and
    does not pass through ``on_provider_error``. The named exception allows
    applications to identify a real integration conflict. Explicit
    ``override=True`` is the only supported replacement path; the later
    import wins and emits a warning.

    ``name`` is the conflicting adapter's ``Provider.name``. The inherited
    ``provider`` and ``model`` fields have no values at registration time.
    This deployment/integration error should not be caught; rename the
    adapter or explicitly override it. It differs from ``ToolNameConflictError``:
    provider adapters use a process-wide registry with an override path,
    while Tools are registered per Runtime without one. Invalid decorator
    targets or missing names remain built-in ``ValueError`` programming
    errors, not this collision error.

    .. seealso:: :class:`ProviderError`, :class:`ToolNameConflictError`,
        :func:`flowing.providers.register_provider`.
    """
    name: str
    """Conflicting adapter name from its ``Provider.name`` class attribute."""
    def __init__(self, name: str) -> None: ...


class DependencyError(FlowingError):
    """Raised when incremental plugin installation detects a dependency cycle.

    After each plugin is installed, ``Runtime.install()`` checks the
    dependency graph among plugins currently installed. A cycle in that
    installed subgraph raises this exception at the installation that closes
    the cycle. A declared dependency that is not installed only emits
    ``warnings.warn``; plugins may be installed in stages, and a declared but
    unused dependency is valid. If the missing dependency is actually needed
    at runtime, ``MissingProvideError`` is the fallback.

    ``plugin`` identifies the plugin whose installation closed the cycle;
    ``missing`` lists the dependency names forming the back edge. This is an
    installation-time error and should not be caught. It complements
    ``MissingProvideError``: static dependency validation at startup versus
    dynamic injection at runtime.

    .. rubric:: Example

    .. code-block:: python

        runtime.install(PluginA())  # Its missing "b" dependency only warns.
        runtime.install(PluginB())  # If it depends on "a", installation closes a cycle.

    .. seealso:: :meth:`flowing.runtime.Runtime.install`,
        :class:`MissingProvideError`.
    """
    plugin: str
    """Plugin whose installation closed the dependency cycle."""
    missing: list[str]
    """Dependency names forming the back edge that closes the cycle."""
    def __init__(self, plugin: str, missing: list[str]) -> None: ...


class CommError(FlowingError):
    """Category for errors from the in-process communication bus in ``CommPlugin``.

    The bus supports endpoint registration, signal delivery, and
    request-reply. It is an optional built-in extension and exists only after
    ``runtime.install(CommPlugin())``; its exception types nevertheless
    belong to the framework's common hierarchy.

    The bus provides fire-and-forget semantics: ``publish`` tolerates
    subscriber exceptions silently and does not produce a ``CommError``.
    Communication messages never enter an LLM context, and communication
    errors are not converted into EVENT messages. Catch this category for
    bus errors; the framework raises concrete subclasses.

    .. seealso:: :class:`flowing.plugins.comm.Communication`,
        :class:`flowing.plugins.comm.CommPlugin`.
    """


class DuplicateEndpointError(CommError):
    """Raised when a communication endpoint ID is registered more than once.

    ``Communication.register_endpoint(endpoint_id, handler)`` rejects a
    duplicate ID because silently replacing it would discard the previous
    handler's route. Registration is not idempotent. Unregistering a missing
    endpoint uses the ordinary ``KeyError`` and is not wrapped here.

    ``endpoint_id`` is the conflicting ID. This programming error should not
    be caught.

    .. seealso:: :meth:`flowing.plugins.comm.Communication.register_endpoint`,
        :class:`CommError`.
    """
    endpoint_id: str
    """Endpoint ID claimed by both registrations."""
    def __init__(self, endpoint_id: str) -> None: ...


class SignalDeliveryError(CommError):
    """Raised when ``send()`` or ``request()`` cannot route to a target endpoint.

    A point-to-point signal cannot be delivered when the target endpoint is
    unregistered or its ID is misspelled. Since ``send`` returns immediately,
    the missing route is detected synchronously at send time. This differs
    intentionally from ``publish`` broadcast behavior, which silently
    tolerates an individual subscriber's exception.

    ``target`` is the missing endpoint ID and ``signal_type`` is the signal
    message's ``type`` value. Callers may catch the error, for example to log
    a fallback; Flowing does not retry it, and retry policy belongs to the
    application.

    .. seealso:: :meth:`flowing.plugins.comm.Communication.send`,
        :class:`SignalTimeoutError`, :class:`CommError`.
    """
    target: str
    """Target endpoint ID that could not be found."""
    signal_type: str
    """Type of the signal that could not be delivered."""
    def __init__(self, target: str, signal_type: str) -> None: ...


class SignalTimeoutError(CommError):
    """Raised when ``request()`` receives no matching reply before its timeout.

    In request-reply mode, the pending request is removed and this error is
    raised if a reply with the matching ``correlation_id`` does not arrive
    within ``timeout`` seconds. In approval flows, a timeout is often a
    normal business branch, such as converting it into
    ``Intercepted("Approval timed out")`` to block a Tool call. A typed
    exception lets the handler catch that outcome instead of handling a bare
    ``asyncio.TimeoutError``.

    ``target`` stores the requested endpoint ID and ``timeout`` the duration
    actually used, in seconds. Callers commonly catch this as a business
    branch; retry decisions belong to the application. After
    ``CommHandle.destroy()``, a pending ``request()`` await receives built-in
    ``asyncio.CancelledError``, not this exception.

    .. rubric:: Example

    .. code-block:: python

        try:
            result = await self.comm_handler.request(
                target="ui-main", type="permission_request",
                payload={"tool_name": tool_call.name}, timeout=120.0,
            )
        except SignalTimeoutError:
            raise Intercepted("Approval timed out")

    .. seealso:: :meth:`flowing.plugins.comm.CommHandle.request`,
        :class:`Intercepted`, :class:`CommError`.
    """
    target: str
    """Endpoint to which the request was sent."""
    timeout: float
    """Timeout duration used for the request, in seconds."""
    def __init__(self, target: str, timeout: float) -> None: ...


class FormatError(FlowingError):
    """Category for declarative-file, creation-validation, and evaluation-format errors.

    This includes ``.fya`` parsing, ``PENDING`` checks in the creation
    pipeline, context requirements for ``Parsable`` evaluation, and reserved
    Agent attribute names, as well as mismatches between a declared name and
    the name inferred from its source. Declarative and imperative Agent
    definitions produce the same Python class model, so format errors must
    surface early during class generation or instantiation. Tools such as
    ``flowing compile`` can report this category uniformly.

    These are declaration errors and fail immediately; callers should not
    catch them. The framework raises concrete subclasses.

    .. seealso:: :class:`flowing.parsable.Parsable`,
        :meth:`flowing.runtime.Runtime.create_agent`.
    """


class MissingFieldError(FormatError):
    """A required field still contains the ``PENDING`` sentinel at creation validation.

    In ``.fya``, an underscore-marked deferred definition becomes the
    ``PENDING`` sentinel. After ``setup()`` and before ``after_create``, the
    pipeline checks for fields still set to ``PENDING``; for example,
    ``system_prompt`` may remain unset. A deferred definition is a promise
    that the pipeline must fulfill at this fixed checkpoint. Failing early
    is safer than entering the Turn loop with ``None``. Recovery performs the
    same check.

    ``field`` is the field still set to ``PENDING`` and ``agent_type`` is its
    Agent type. This declaration or ``setup()`` implementation error should
    not be caught. ``PENDING`` is distinct from ``_UNSET``, which is used to
    detect omitted parameter defaults and is not checked here.

    .. seealso:: :data:`flowing.parsable.PENDING`,
        :meth:`flowing.runtime.Runtime.create_agent`, :class:`FormatError`.
    """
    field: str
    """Field still set to ``PENDING`` at the validation checkpoint."""
    agent_type: str
    """Type name of the Agent being created."""
    def __init__(self, field: str, agent_type: str) -> None: ...


class MissingContextError(FormatError):
    """Raised when an unbound ``Parsable`` is forced to evaluate without its required context.

    The error occurs when evaluating a class-level ``Parsable`` or one on a
    manually created, unbound Agent: calling ``resolve()`` without context
    or reading its ``resolved`` property requires instance attributes,
    environment variables, or configuration. Flowing evaluates bound
    instances automatically at fixed points inside its evaluation boundary.
    Outside that boundary, callers must pass ``resolve(context)`` explicitly
    or bind the value first. Returning the unrendered template would leave
    ``{{ }}`` expressions in prompts and make failures harder to diagnose.

    This error has no structured fields and uses a fixed English message. It
    is a usage error: call ``resolve(context)`` explicitly and do not catch
    it. ``str()`` displays the template source without evaluating it, and
    ``repr()`` always shows the source; neither raises this error. An
    unbound ``FILE_REF`` also raises even if a context mapping is provided,
    because path resolution requires the bound instance's Runtime.

    .. rubric:: Example

    .. code-block:: python

        class MyAgent(Agent):
            system_prompt = Parsable("Hello, {{ user_name }}")

        MyAgent.system_prompt.resolved  # Unbound: raises this error.
        MyAgent.system_prompt.resolve({"user_name": "Alice"})  # Explicit context: succeeds.

    .. seealso:: :class:`flowing.parsable.Parsable`, :class:`FormatError`.
    """
    def __init__(self) -> None: ...


class ReservedAttributeError(FormatError):
    """Raised when an Agent instance attribute uses a name reserved by the framework.

    The framework injects four names into the ``Parsable`` rendering
    context: ``env`` (bound to ``os.environ``), ``config`` (Runtime
    configuration), and ``agent`` and ``self`` (the instance). An Agent
    instance attribute with one of these names would overwrite an injected
    value, so the framework detects the conflict before evaluation. An
    explicit reserved-name check avoids an unclear implicit rule about which
    value wins.

    ``name`` is the occupied name: ``"env"``, ``"config"``, ``"agent"``, or
    ``"self"``. This declaration error fails immediately and should not be
    caught.

    .. seealso:: :class:`flowing.parsable.Parsable`, :class:`FormatError`.
    """
    name: str
    """Occupied framework-reserved name: ``env``, ``config``, ``agent``, or ``self``."""
    def __init__(self, name: str) -> None: ...


class NameMismatchError(FormatError):
    """Declared ``name`` differs from the name inferred by the framework.

    ``name`` is not a mechanism for choosing an Agent's identity; identity is
    inferred from the path, registry name, or class name according to
    ``Agent.class_name`` rules. A ``.fya`` file or hand-written subclass may
    still declare ``name`` as an equality assertion. If it differs from the
    inferred value, this error reports both values and their source. This
    catches confusing mismatches such as a file named ``foo.fya`` declaring
    itself as ``payment``.

    ``declared`` is the user's value, ``inferred`` the value from the path or
    class-name rules, and ``source`` the ``.fya`` path or fully qualified
    subclass name. The check only asserts equality; when the names match,
    identity still comes from inference. This declaration error fails
    immediately and should not be caught.

    .. seealso:: :class:`flowing.agent.Agent`, :class:`FormatError`.
    """
    declared: str
    """Name explicitly declared by the user."""
    inferred: str
    """Name inferred from the path or class name."""
    source: str
    """Conflicting source: a ``.fya`` path or qualified hand-written subclass name."""
    def __init__(self, declared: str, inferred: str, source: str) -> None: ...


class CompileError(FlowingError):
    """Category for build-time failures from explicit ``.fya`` compilation.

    This class records category only and adds no behavior. The current
    concrete subclass is :class:`ArtifactModifiedError`; catch this category
    or that specific failure as appropriate.

    .. seealso:: :class:`ArtifactModifiedError`, :mod:`flowing.compiler`.
    """


class ArtifactModifiedError(CompileError):
    """Compilation refuses to overwrite an artifact modified outside the compiler.

    Explicit compilation compares the ``py_hash`` in the adjacent
    ``.flowing.meta.yaml`` file with the AST hash of the existing ``.py``
    artifact. A mismatch means the artifact was edited manually; formatting
    changes alone do not change the AST hash. Compilation stops instead of
    overwriting that file. Other files already generated by the same run are
    not rolled back. Resolve the conflict and compile again to continue.

    ``path`` is the conflicting compiled artifact. Callers should not catch
    this; ``flowing compile`` maps it to a runtime-error exit code.

    .. seealso:: :class:`CompileError`, :mod:`flowing.compiler`.
    """
    path: Path
    def __init__(self, path: Path) -> None: ...


class FormatVersionError(FlowingError):
    """Persistence file declares a JSON Lines format version newer than this framework supports.

    ``RecordStore.replay`` reads the first-line metadata object and raises
    this error if its ``format_version`` is greater than
    ``flowing.persistence.FORMAT_VERSION``. Quietly reading a file created by
    a newer framework risks data loss, such as after deploying an older
    version. Files at or below the supported version, including files with
    no version metadata (treated as version 0), are migrated to the current
    format while reading and do not raise this error.

    ``path`` is the persistence file, ``found`` its declared version, and
    ``supported`` the framework's current version. This deployment error
    fails immediately and should not be caught. It is distinct from
    :class:`CorruptionError`, which reports damaged data.

    .. seealso:: :mod:`flowing.persistence`, :class:`CorruptionError`.
    """
    path: Path
    """Persistence file with an unsupported format version."""
    found: int
    """Format version declared in the file's first-line metadata."""
    supported: int
    """Highest format version supported by this framework."""
    def __init__(self, path: Path, found: int, supported: int) -> None: ...


class CorruptionError(FlowingError):
    """A complete record line in a JSON Lines persistence file is malformed.

    ``RecordStore.replay`` raises this when a complete line contains invalid
    JSON, including a final line terminated by a newline. A torn final line
    without a terminating newline, which can result from a crash during a
    write, is tolerated and discarded. Corruption in a complete line means
    committed data is damaged and is treated as an incident, not as a normal
    crash window.

    ``path`` is the persistence file and ``lineno`` is the one-based line
    number. This data incident should not be caught; the framework logs
    ``path:lineno`` before raising so that a person can investigate. It is
    distinct from :class:`FormatVersionError`, which reports a version
    mismatch rather than damaged data.

    .. seealso:: :mod:`flowing.persistence`, :class:`FormatVersionError`.
    """
    path: Path
    """Persistence file containing the malformed line."""
    lineno: int
    """One-based line number of the malformed record."""
    def __init__(self, path: Path, lineno: int) -> None: ...


class Intercepted(Exception):
    """Intentional hard-stop signal raised by a hook; not a ``FlowingError``.

    Raising this signal is one of three valid hook-handler outcomes; the
    other two are returning a value and setting the ``shortcut`` field.
    ``Intercepted`` means intentional prevention, such as approval denial,
    security blocking, or a permission check, rather than an unexpected
    failure. Dispatch re-raises it unchanged, stops the handler chain, skips
    later handlers, and does not trigger the corresponding ``after_`` hook
    because the operation is invalidated. It is logged at INFO rather than
    ERROR level.

    At a ``tool_call()`` boundary, the signal becomes
    ``ToolResult.blocked(reason)``: the LLM receives a TOOL message with
    ``tool_status="blocked"`` and content ``[TextBlock(reason)]``. This is a
    hard stop, unlike the negotiated replacement represented by ``shortcut``,
    after which ``after_`` hooks still run.

    ``reason`` is human-readable and, on the ``tool_call()`` path, becomes
    visible in the blocked result. ``payload`` is optional structured data
    for auditing or logs; Flowing does not interpret it and defaults it to
    ``None``. Because this class does not inherit from ``FlowingError``,
    broad framework-error handling does not swallow it. It triggers no error
    hook, does not enter the ``on_provider_error`` decision path, and is
    unrelated to retry. At a hook point with no corresponding Tool call, such
    as ``before_turn``, presentation is determined by the operation's caller;
    Flowing does not guarantee a uniform LLM-visible form.

    .. rubric:: Example

    .. code-block:: python

        from flowing.errors import Intercepted

        async def approval(agent, tool_call):
            if not await approval_service.approve(tool_call):
                raise Intercepted("The user denied this action", payload={"tool": tool_call.name})
            return tool_call

        async def setup(self):
            self.hooks.before_tool_call(approval, by="approval")

    .. seealso:: :class:`flowing.hooks.HookList` for dispatch semantics,
        :class:`flowing.tool.ToolResult` for the blocked-result carrier.
    """
    reason: str
    """Human-readable reason; on ``tool_call()`` it becomes visible in the blocked result."""
    payload: Any
    """Optional structured audit/log context; Flowing does not interpret it. Defaults to ``None``."""
    def __init__(self, reason: str, payload: Any = None) -> None: ...


class UnpairedToolCallError(FlowingError):
    """The message-tree pairing invariant is broken: a tool call lacks its result message.

    Tool calls and their result messages are always paired in the tree.
    Cancellation closes a call with ``tool_status="cancelled"``; recovery
    after a crash closes it with a persisted ``synthetic=True`` placeholder.
    Context assembly in ``Agent._assemble_context`` only asserts this
    invariant and does not repair the tree while reading, so it raises this
    error when it finds an orphan call.

    An orphan may result from an explicit tree operation: ``MessageChain.remove``
    removed either the call or its result, or ``Agent.fork`` copied an
    unpaired call into a new branch. The operation that created the orphan
    must close it later, for example by inserting a result with
    ``MessageChain.insert``, or discard the branch.
    """
