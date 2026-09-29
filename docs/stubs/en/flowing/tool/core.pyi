"""``flowing.tool.core`` — the ``Tool`` base class and four tool-call data types.

This module contains the executable object (:class:`Tool`), the LLM-visible
declaration (:class:`ToolDefinition`), the Agent-level binding
(:class:`ToolEntry`), and the call and result values (:class:`ToolCall` and
:class:`ToolResult`). The five execution states are described by
:data:`ToolStatus`; :data:`ToolType` identifies the four tool forms. The module
also defines :data:`TOOL_NAMING`. Public symbols are re-exported from
``flowing.tool``. Shared call ordering, the ``builtin::`` namespace, parameter
precedence, and error handling are documented by the package module.
"""
from collections.abc import AsyncGenerator, Callable
from typing import Any, Literal
from pydantic import BaseModel
from flowing.message import Message, ToolCallBlock
from flowing.parsable import Parsable

ToolStatus = Literal["completed", "pending", "blocked", "cancelled", "error"]
"""The five possible states of a tool execution result.

- ``"completed"`` means execution completed normally, with its value in
  ``output``.
- ``"cancelled"`` means the cancellation race in ``Tool.__call__`` interrupted
  an in-flight ``execute`` call. A partial value returned before cancellation
  is handled remains in ``output``.
- ``"pending"`` is an asynchronous receipt. It is produced when ``execute``
  returns an ``asyncio.Task``, is an async generator whose first ``yield`` is
  the receipt, or is an ordinary async function on a tool marked
  ``background = True``. Flowing returns the receipt without blocking the
  logical turn; the eventual result arrives in a separate message.
- ``"blocked"`` means a hook deliberately blocked execution or ``execute``
  raised :class:`flowing.errors.Intercepted`. A ``before_tool_call`` block
  prevents execution; an ``after_tool_call`` block discards a result after
  execution; an ``Intercepted`` raised inside ``execute`` interrupts it.
  These results are created only through :meth:`ToolResult.blocked`.
- ``"error"`` means ordinary execution raised an exception. The error is
  returned to the LLM as a normal tool result and does not trigger an error
  hook. The core error hook is ``on_provider_error``; see :mod:`flowing.hooks`.
"""

ToolType = Literal["script", "mcp", "cli", "request"]
"""The four values accepted by a tool ``.fya`` file's ``type:`` field.

- ``"script"`` represents Python callables and is registered as a singleton
  shared by all Agents.
- ``"mcp"`` represents a local stdio process or a remote MCP endpoint.
- ``"cli"`` represents a command-line tool driven by a Jinja2 template.
- ``"request"`` represents an HTTP or HTTPS request tool.

Each MCP, CLI, or Request declaration creates a separate instance. Script
tools contain user-defined business logic and are not instantiated repeatedly.
"""

TOOL_NAMING: Any
"""The :class:`flowing.paths.NamingRules` for inferring a tool identity from its path.

Generic filenames such as ``TOOL.fya``, ``TOOL.py``, ``tool.fya``, and
``tool.py`` use the directory name. Other filenames use the name after suffix
removal; ``.tool.fya`` takes precedence over ``.fya``. The inferred identity is
normalized from snake case to kebab case. The parser and ``ToolRegistry.get``
use these rules.

.. seealso:: :data:`flowing.runtime.AGENT_NAMING` and
    :data:`flowing.plugins.skills.SKILL_NAMING`.
"""

def _strip_unsupported_background(tool: Tool) -> None: ...
def _infer_from_execute(execute: Callable[..., Any]) -> type[BaseModel]: ...
def _whole_doc(doc: str | None) -> str | None: ...
def _first_paragraph(doc: str | None) -> str | None: ...
def _apply_param_aliases(definition: ToolDefinition, param_aliases: dict[str, str], renamed: dict[str, dict[str, Any]] = {}) -> ToolDefinition: ...
def _has_forbidden_block(value: Any) -> bool: ...

class ToolCall:
    """A single tool-call request from the LLM and the value passed to ``before_tool_call``.

    ``ToolCall`` is a data object describing what the LLM wants to invoke. It
    is parsed from a provider message, passed through the ``before_tool_call``
    hook chain, then unpacked into keyword arguments for ``Tool.execute()``.
    It is an invocation request, not an executable object; ``execute()`` does
    not receive a ``ToolCall`` instance.

    .. rubric:: Example

    A hook receives ``(agent, value)``; in a method handler, ``self`` is the
    Agent that owns the hook.

    .. code-block:: python

        from flowing import Intercepted, ToolResult, on

        @on("before_tool_call")
        async def approve(self, tool_call):
            tool_call.args["request_id"] = self.node_id
            if tool_call.name == "delete-file":
                raise Intercepted("This operation requires approval.")
            if hit := self._cache.get(tool_call.name):
                tool_call.shortcut = ToolResult(status="completed", output=hit)
            return tool_call

    .. rubric:: Behavior

    - A handler can return the (possibly modified) ``ToolCall``, put a
      ``ToolResult`` in ``shortcut`` to skip execution, or raise
      :class:`flowing.errors.Intercepted` to block the call. An ordinary
      exception propagates; no fallback hook catches it.
    - ``ToolCall`` does not validate arguments or resolve specified values.
      Parameter aggregation happens later during Agent argument normalization.
    - ``args`` contains the values supplied by the LLM, with keys that may be
      aliases. A handler can modify this dictionary. During ``resolve()``,
      specified values take precedence over matching hook-written values.

    .. seealso:: :class:`flowing.tool.ToolResult`,
        :class:`flowing.tool.ToolEntry`, and :mod:`flowing.hooks`.
    """
    id: str
    """Provider-side call ID, such as OpenAI's ``tool_call_id``.

    This pairs the call with the ``tool_call_id`` on its result ``kind=TOOL``
    message and is also used to match orphaned calls during recovery. Code
    constructing a call programmatically should generate a synthetic string;
    a descriptive prefix such as ``workflow-`` or ``cron-`` followed by random
    hexadecimal characters is recommended. The prefix aids tracing and does
    not affect pairing.
    """
    name: str
    """The name shown to the LLM: ``ToolEntry.name_alias``, not the canonical name.

    ``Agent.tool_call()`` resolves this alias only and does not fall back to
    the canonical name.
    """
    args: dict[str, Any]
    """The original arguments supplied by the LLM, keyed by LLM-visible names.

    Keys may be aliases from ``param_aliases``. A ``before_tool_call`` handler
    can modify this dictionary. Alias mapping and specified-value aggregation
    happen later during normalization.
    """
    shortcut: ToolResult | None = None
    """An optional result supplied by a hook to replace normal tool execution.

    The default is ``None``. A non-``None`` value stops the hook chain and
    replaces execution; ``after_tool_call`` still runs. This is normal
    completion, unlike ``Intercepted``, which produces a ``blocked`` result
    and skips ``after_tool_call``. A shortcut bypasses ``Tool.__call__``, so
    its output may not yet be normalized; ``Agent.tool_call`` normalizes it
    idempotently before returning.
    """

    @classmethod
    def from_block(cls, block: ToolCallBlock) -> ToolCall:
        """Convert a message-layer ``ToolCallBlock`` to a ``ToolCall``.

        The conversion removes the generic ``type`` field and creates
        ``ToolCall(id, name, args, shortcut=None)``. The turn loop uses it to
        extract each tool call from a provider response before calling
        ``Agent.tool_call()``.

        .. rubric:: Behavior

        - The method does not modify the input block or produce side effects.
        - The returned ``shortcut`` is ``None`` because shortcuts are created
          by hooks at runtime rather than loaded from a message.

        .. seealso:: :class:`flowing.message.ToolCallBlock` and
            :meth:`flowing.tool.ToolResult.as_message`.
        """
        ...

class ToolResult:
    """The LLM-visible receipt for a tool invocation.

    The same structure represents successful, failed, asynchronous, and
    blocked calls. Its core fields are ``status``, ``output``, and ``error``.
    ``output`` is the sole result payload; it accepts primitive values, media
    carriers, raw ``bytes``, ``Path`` objects, and supported third-party
    objects before :func:`flowing.tool.normalize_output` normalizes them. The
    ``name``, ``tool_call_id``, and ``production`` fields identify which call
    produced a value and in what form. ``Agent.tool_call`` and the background
    delivery path populate these fields for ``on_tool_yields`` handlers.

    .. rubric:: Example

    Tool authors normally return a value from ``execute`` and let Flowing
    wrap it in ``ToolResult``.

    .. rubric:: Return a structured value

    .. code-block:: python

        from flowing.agent import Agent

        async def execute(self, *, order_id: str, caller: Agent) -> dict:
            return {"transaction": "abc"}

    .. rubric:: Return media with text

    .. code-block:: python

        from pathlib import Path
        from flowing.media import Image

        png_path = Path("receipt.png")  # An image path supplied by the application.

        async def execute(self, *, url: str):
            return [f"Captured {url}", Image(path=png_path)]

    .. rubric:: Behavior

    - ``status="error"`` is a normal result, not an exception. The LLM can
      see the failure and decide whether to revise its request, use another
      tool, or report the failure. Error results do not trigger error hooks;
      the core error hook is ``on_provider_error``.
    - ``blocked`` and ``error`` are distinct. ``blocked`` means a hook or
      explicit ``Intercepted`` signal prevented or interrupted the operation;
      ``error`` means ordinary execution failed. A ``before_tool_call`` block
      means the tool did not run; an ``after_tool_call`` block means it ran
      but its result was discarded; an ``Intercepted`` signal in ``execute``
      interrupts execution. Blocked results are created through
      :meth:`blocked`.
    - ``cancelled`` represents in-flight work interrupted by the cancellation
      race in ``Tool.__call__``. Its output is ``None`` or a partial value
      returned before the tool handled cancellation.
    - ``pending`` represents asynchronous work: ``execute`` returned an
      ``asyncio.Task``, yielded its first value as an async generator, or used
      ``background = True``. Flowing returns a receipt without waiting for the
      logical turn to finish.
    - After ``Agent.tool_call``, ``output`` has one of five forms: ``None``, a
      primitive, one content block, a list of primitives, or a mixed list
      containing only ``TextBlock``, ``StructBlock``, and ``MediaBlock``
      values. ``Tool.__call__`` and the end of ``Agent.tool_call`` normalize
      it; normalization is idempotent and covers shortcuts and hook-modified
      results.
    - Pairing metadata such as ``tool_call_id`` and ``tool_status`` belongs to
      the message rather than this object; see :meth:`as_message`.
    - Traces and other observation data belong to hooks and snapshots, not
      this LLM-visible result. ``duration`` is reserved and currently remains
      ``None``; it is omitted from ``as_message`` even if supplied.
    - A deeply nested non-JSON object can pass shallow normalization but fail
      ``StructBlock`` validation while shaping a message, raising
      ``ValueError``.

    .. seealso:: :class:`flowing.tool.ToolCall`,
        :func:`flowing.tool.normalize_output`,
        :func:`flowing.tool.output_to_blocks`,
        :meth:`flowing.tool.Tool.__call__`, and :mod:`flowing.errors`.
    """
    status: ToolStatus
    """One of the five execution states described by :data:`ToolStatus`."""
    output: Any = None
    """The only result payload.

    After normalization, the value leaving ``Agent.tool_call`` has one of the
    supported content forms. A completed result carries its return value. A
    pending result usually has ``None`` unless an async generator yielded
    receipt content. A cancelled result has ``None`` or partial output. An
    error result may have ``None``. A blocked result has ``None`` or a text
    reason.
    """
    error: str | None = None
    """An error description, set only for ``status="error"``.

    It is visible to the LLM to support self-correction and does not trigger an
    error hook.
    """
    duration: float | None = None
    """Reserved execution duration in seconds.

    The current implementation leaves this as ``None``. The value is omitted
    from :meth:`as_message` and is not visible to the LLM.
    """
    background_task_id: str | None = None
    """The key of a registered background task.

    It is non-``None`` for a ``pending`` result registered through
    ``Agent.track_background_task`` and ``None`` for other states. A caller
    can use the ID to cancel or query the task.
    """
    name: str | None = None
    """The LLM-visible alias of the tool associated with this result.

    ``Agent.tool_call`` sets it before ``on_tool_yields`` for produced results
    and before ``after_tool_call`` for LLM-facing argument-validation errors.
    Both hooks can filter results with ``match_on="name"``. Results that
    bypass that pipeline have ``None`` here.
    """
    tool_call_id: str | None = None
    """The ID of the ``ToolCall`` associated with this result.

    ``Agent.tool_call`` sets it before ``on_tool_yields`` for produced results
    and before ``after_tool_call`` for LLM-facing argument-validation errors.
    Background segments and terminal values carry the same ID for correlation;
    :meth:`as_message` receives the message's paired ID separately.
    """
    production: str | None = None
    """The form in which the result was produced.

    The values are ``"sync"`` for a synchronous result, ``"receipt"`` for a
    background receipt, ``"segment"`` for a streamed segment, and
    ``"final"`` for a Task result or terminal notification such as an
    exception or cancellation. ``"final"`` is used only for identifiable
    terminal events. Normal exhaustion of an async generator does not produce
    ``"final"`` because its last ``yield`` was already emitted as a segment;
    a tool that needs an explicit completion marker should yield one itself.
    """

    @classmethod
    def blocked(cls, reason: str | None = None) -> ToolResult:
        """Create the canonical result for a hook's hard block.

        The framework calls this factory when a handler in
        ``before_tool_call`` or ``after_tool_call``, or the tool's ``execute``,
        raises :class:`flowing.errors.Intercepted`. A ``before_tool_call``
        block prevents execution; an ``after_tool_call`` block discards a
        completed result; and a signal raised inside ``execute`` interrupts
        execution. The factory produces the same shape for every path, allowing
        the LLM to distinguish an intentional block from an execution failure.

        :param reason: The blocking reason, usually the exception message. It
            is visible to the LLM.
        :return: A result with ``status="blocked"`` and ``error=None``.

        .. rubric:: Behavior

        - The returned result always has ``status == "blocked"`` and
          ``error is None``. A non-empty reason becomes its string ``output``
          and is shaped as a ``TextBlock`` by :meth:`as_message`.
        - The factory does not write an audit log; hooks or snapshots handle
          auditing.

        .. seealso:: :class:`flowing.errors.Intercepted` for the signal.
        """
        ...

    def as_message(self, tool_call_id: str, *, source: str | None = None) -> Message:
        """Shape this result as a ``kind=TOOL`` message for the message tree.

        At logical-turn completion, Flowing converts each result into a
        ``Message(kind=TOOL, ...)``. The caller later links and appends the
        message to the message-level tree. Pairing metadata
        (``tool_call_id`` and ``tool_status``) goes in message fields;
        ``content`` contains only content blocks. Shaping delegates to
        ``output_to_blocks``, which is also used for background delivery and
        subagent result delivery.

        ``Agent.tool_call`` passes the originating ``ToolCall.id`` here to
        pair the result with ``ToolCallBlock.id``. The result's own
        ``tool_call_id`` field stores the same ID for result-level hook
        handlers.

        :param tool_call_id: The paired call ID, matching ``ToolCall.id`` and
            ``ToolCallBlock.id``.
        :param source: The message's ``source`` value. The default is
            ``"tool_result"``, also used for asynchronous tool-result EVENT
            messages. This field does not identify the tool.
        :return: A new ``kind=TOOL`` message with paired metadata and content
            from ``output_to_blocks``. Its content has no protocol blocks, and
            ``synthetic`` and ``turn_end`` are false.

        .. rubric:: Behavior

        - ``self.output`` must already have one of the normalized forms. A
          deeply nested non-JSON value can fail ``StructBlock`` validation
          here and raise ``ValueError`` through the framework error channel.
        - ``ToolResult.error`` is non-``None`` only for an error result. It is
          passed to ``output_to_blocks``, which appends it as a ``TextBlock``.
        - This method does not append the message or set ``parent_id``;
          ``Agent._append_message`` performs those operations.
        - A pending result also produces a receipt message. A Task receipt
          with ``output=None`` has empty content. An async-generator receipt
          contains its first yielded value and, when registered, an additional
          text block containing the background task ID. Flowing does not alter
          the yielded content. Later results arrive as separate EVENT messages
          and do not replace the receipt.

        .. seealso:: :func:`flowing.tool.output_to_blocks`,
            :class:`flowing.message.Message`, and
            :meth:`flowing.agent.Agent._append_message`.
        """
        ...

class ToolDefinition:
    """Serializable, LLM-visible tool declaration with no execution logic.

    This is the LLM-visible layer of the three-layer capability description.
    Its fields are ``name``, ``description``, ``params_schema``,
    ``output_schema``, and ``strict``. It appears in ``Context.tools`` and is
    the source from which provider adapters build function-calling schemas.
    Adapters map only recognized fields; extra fields are not automatically
    forwarded.

    Tool authors usually declare a ``ScriptTool`` subclass and let Flowing
    generate this value. ``ToolDefinition`` also allows one executable tool to
    have different LLM-visible views for different Agents without coupling
    those views to the executable object.

    .. rubric:: Example

    .. code-block:: python

        definition = ToolDefinition(
            name="finish",
            description="Finish the task and return a structured result.",
            params_schema={
                "summary": {"type": "string", "default": "",
                            "description": "A short task summary"},
            },
        )

    .. rubric:: Behavior

    - The value is JSON-serializable data. ``params_schema`` is a JSON Schema
      properties mapping; it must not hold runtime objects such as callables
      or connections.
    - This class does not validate arguments. LLM-facing validation occurs in
      ``Agent._normalize``; internal validation occurs in ``Tool.__call__``
      using a model compiled when the tool is created.
    - With ``strict=True`` (the default), the tool layer rejects LLM arguments
      not declared in ``params_schema``. With ``strict=False``, unknown keys
      pass through for downstream validation.

    .. seealso:: :meth:`clone_with_overrides`, :class:`ToolEntry`, and
        :mod:`flowing.params`.
    """
    name: str
    """The canonical tool name in kebab case.

    The LLM sees ``ToolEntry.name_alias`` instead. The alias becomes the
    visible name when ``clone_with_overrides`` applies it.
    """
    description: str
    """The LLM-facing description, after ``Parsable`` rendering."""
    params_schema: dict[str, dict[str, Any]]
    """A mapping from canonical parameter names to JSON Schema property objects.

    Requiredness is derived from the presence of ``default``: a property with
    a default is optional. The schema can come from a Python ``BaseModel`` or
    an expanded ``.fya`` declaration, and Flowing bridges it to an execution
    model with :func:`flowing.params.schema_to_model`. Defaults must be
    JSON-compatible. Obtain complex resources such as clients through an
    identifier and ``caller`` instead.
    """
    output_schema: dict[str, Any] | None = None
    """A JSON Schema fragment describing output, or ``None`` when undeclared.

    A tool's ``.fya`` ``output:`` field supplies this value. An MCP server's
    ``outputSchema`` is copied here automatically. The value travels with the
    ``llm_definition()`` result but is not mapped into provider function-call
    schemas. ``RequestTool`` uses its properties to select fields from JSON
    responses. The current implementation does not validate tool results
    against it; an MCP ``outputSchema`` is stored without validation too.
    Agent-level ``output:`` overrides are stored in
    ``ToolEntry.override_params`` rather than in this field.
    """
    strict: bool = True
    """Whether the tool layer strictly limits LLM arguments to declared parameters.

    Use ``False`` when downstream code is responsible for validating
    arguments.
    """

    def clone_with_overrides(self, name: str | None = None, override_params: dict[str, dict[str, Any]] | None = None, override_description: str | None = None, *, specified_params: set[str] | None = None) -> ToolDefinition:
        """Return an overridden copy while leaving the original definition unchanged.

        This is the override mechanism used by ``ToolEntry.llm_definition``.
        It applies the alias, sparse parameter overrides, description override,
        and removal of hidden parameters in one operation. Returning a new
        object prevents an Agent-level view from mutating the global registry's
        definition.

        :param name: Replacement name, usually ``ToolEntry.name_alias``.
            ``None`` retains the current name.
        :param override_params: Sparse patches in the form
            ``{canonical_parameter: {JSON_Schema_keyword: value}}``. Only
            supplied keywords change; omitted values retain their base values.
            Unsupported keywords raise
            :class:`flowing.errors.FormatError`. Requiredness is not inferred
            again: omitting ``default`` retains the base value, while an
            explicit ``default`` makes the parameter optional.
        :param override_description: Replacement description, or ``None`` to
            retain the existing description.
        :param specified_params: Parameter names to remove from the LLM view.
            The caller passes the keys in ``specified``; injected expressions
            are specified values as well.
        :return: A new :class:`ToolDefinition`; ``self`` is not modified.

        .. rubric:: Behavior

        - The returned object is distinct from ``self``, and the original
          ``params_schema`` remains unchanged.
        - A key not present in the original schema is treated as a new
          parameter. ``FinishTool`` relies on this for dynamic output fields.
          Unknown names in ``specified_params`` are silently ignored.
        - Parameter aliases are not applied here. ``ToolEntry.llm_definition``
          performs that rename after this method returns.

        .. seealso:: :meth:`flowing.tool.ToolEntry.llm_definition` for the
            framework call site.
        """
        ...

class ToolEntry:
    """An Agent's binding that describes how that Agent uses a tool.

    ``ToolEntry`` defines the LLM-visible alias, parameter overrides,
    specified values, and parameter aliases. Each Agent owns its own entries,
    so one tool can have different LLM-visible schemas for different Agents.
    These overrides belong to the Agent binding, not the global registry.

    .. rubric:: Example

    .. code-block:: python

        from flowing import ToolEntry
        from flowing.parsable import Parsable

        entry = ToolEntry(
            name_alias="pay",
            name_ori="make-payment",
            override_params={
                "amount": {"description": "Payment amount, up to 50,000."},
                "currency": {"default": "USD"},
            },
            specified={
                "currency": Parsable("USD"),
                "user_id": Parsable("{{ self.inject('user_id') }}"),
            },
        )

    The equivalent ``.fya`` declaration is:

    .. code-block:: yaml

        tools:
          - make-payment as pay:
              description: "Charge a payment."
              args:
                amount: {description: "Payment amount, up to 50,000."}
                currency: USD
                user_id: "{{ self.inject('user_id') }}"
                working_dir as cwd: _
        ---
        $tools.pay.args.cwd.description:
          Working directory for this order.

    .. rubric:: Behavior

    - The ``args:`` mapping follows the same classification in ``.fya`` and
      programmatic ``Agent.add_tool`` declarations. A dictionary value is a
      sparse ``override_params`` patch. A key written as
      ``<name> as <alias>`` defines a parameter alias and can be combined with
      a patch or specified value. Any other value, including an injection
      expression, becomes ``specified``: the LLM cannot see it, and Flowing
      evaluates it at call time in the calling Agent's context. An ``_`` value
      creates an empty patch that nested fields can fill; if none do, the base
      schema remains in effect.
    - With ``visible=False``, the entry is omitted from ``Context.tools`` but
      can still be called programmatically through the registry. Visibility
      and executability are separate.
    - An entry does not hold a ``Tool`` instance. Execution looks up
      ``name_ori`` in ``ToolRegistry``.
    - Specified parameters are hidden from the LLM and have the highest
      precedence, preventing the LLM from replacing fixed or injected values.

    .. seealso:: :class:`flowing.tool.ToolRegistry`,
        :class:`flowing.tool.ToolDefinition`, and
        :meth:`flowing.agent.Agent.add_tool`.
    """
    name_alias: str
    """The name shown to the LLM.

    Calls resolve by this alias only and do not fall back to the canonical
    name, preventing one Agent's call from bypassing its own binding.
    """
    name_ori: str
    """The canonical name and the key used to find the executable tool in ``ToolRegistry``."""
    override_description: Parsable | None = None
    """An override for the LLM-visible description.

    ``None`` retains the registered description. Otherwise,
    ``llm_definition()`` evaluates this ``Parsable`` value in the calling
    Agent's context to produce the description string.
    """
    override_params: dict[str, dict[str, Any]] | None = None
    """Sparse overrides in the form ``{canonical_name: {field: value}}``.

    Only changed fields need to be specified. Agent-level ``output:``
    overrides are also stored here and handled by ``llm_definition()`` and
    ``resolve()``.
    """
    specified: dict[str, Parsable]
    """Values fixed by the Agent and hidden from the LLM.

    Values are ``Parsable`` objects evaluated lazily in the calling Agent's
    context by ``resolve()``. They can be literals such as ``Parsable("USD")``
    or injection expressions such as
    ``Parsable("{{ self.inject('user_id') }}")``. Injection searches the
    provide chain and raises ``MissingProvideError`` if no provider is found.
    """
    param_aliases: dict[str, str]
    """A mapping from LLM-visible parameter names to canonical names.

    ``resolve()`` maps aliases to canonical names first.
    """
    visible: bool = True
    """Whether this entry appears in the LLM-visible ``Context.tools`` list.

    An invisible entry can still be called programmatically.
    """

    def llm_definition(self, runtime: Any, agent: Any) -> ToolDefinition:
        """Build the ``ToolDefinition`` visible to this Agent's LLM.

        Context assembly calls this for each visible entry and adds its result
        to ``Context.tools``. The method reevaluates on every call and does not
        cache results. It retrieves the registered definition by ``name_ori``,
        removes specified parameters from the LLM view, evaluates the optional
        description override in the Agent context, clones the definition with
        the alias and sparse parameter overrides, and then applies parameter
        aliases to the visible schema.

        :param runtime: The active Runtime, used to access ``tool_registry``.
        :param agent: The calling Agent, used to render the description
            override.
        :return: A new overridden :class:`ToolDefinition`; the registry's
            original definition remains unchanged.
        :raises flowing.errors.ToolNotFoundError: ``name_ori`` is absent from
            the registry. Assembly should prevent this; if it occurs, the
            registry may have been changed externally.

        .. seealso:: :meth:`flowing.tool.ToolDefinition.clone_with_overrides`.
        """
        ...

    def resolve(self, agent: Any, args: dict[str, Any], params_schema: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """Combine LLM arguments and specified values into arguments for ``execute()``.

        ``Agent._normalize()`` calls this after ``before_tool_call``. The
        returned dictionary uses canonical parameter names. The method accepts
        the parameter schema rather than a ``Tool`` instance, keeping
        ``ToolEntry`` in the binding/declaration layer.

        .. rubric:: Behavior

        1. Map each LLM argument name back to its canonical name through
           ``param_aliases``.
        2. Evaluate each specified ``Parsable`` in the calling Agent's
           context. Injection expressions search the provide chain. If a
           specified key exists in ``params_schema``, convert its value using
           the compatible schema conversion, then let it override an LLM value
           with the same canonical name. If the schema lacks that key, retain
           the value without conversion so ``Tool.__call__`` can report it
           during internal validation.

        This method does not fill schema defaults; ``Agent._normalize()`` does
        that in a later step.

        :param agent: The calling Agent, used for provide lookup and
            ``Parsable`` evaluation.
        :param args: The original LLM arguments; keys may be aliases.
        :param params_schema: Canonical names mapped to JSON Schema properties
            from ``tool.definition.params_schema``. Use the registered schema,
            not the overridden LLM-visible view.
        :return: The complete argument mapping keyed by canonical names for
            signature-based dispatch by ``Tool.__call__``.
        :raises flowing.errors.MissingProvideError: An injection key cannot
            be found along the provide chain.

        .. seealso:: :meth:`flowing.agent.Agent.inject`,
            :meth:`flowing.parsable.Parsable.resolve`, and
            :func:`flowing.params._coerce`.
        """
        ...

class Tool:
    """Base class for executable tools, the execution layer of the capability description.

    A ``Tool`` holds a default :class:`ToolDefinition` and exposes
    ``execute()``. The framework invokes it through ``__call__``. Script, MCP,
    CLI, and Request tools are instances of this class or its subclasses.
    ``__call__`` centralizes execution-shape detection, Task handling, caller
    injection, and result wrapping, so ``execute()`` can accept ordinary
    parameters and return ordinary values. Tool authors need not construct
    ``ToolResult`` themselves.

    .. rubric:: Example

    Direct subclassing is mainly used for builtin tools; application code
    should generally use :class:`flowing.tool.ScriptTool`.

    .. code-block:: python

        from flowing.agent import Agent
        from flowing.tool import Tool, ToolDefinition

        class FinishTool(Tool):
            definition = ToolDefinition(
                name="finish",
                description="Finish the task and return a structured result.",
                params_schema={
                    "summary": {
                        "type": "string",
                        "default": "",
                        "description": "A brief summary of the completed task.",
                    },
                },
            )

            async def execute(self, *, summary: str = "", caller: Agent) -> dict:
                return {"summary": summary}

    .. rubric:: Behavior

    - ``Tool`` does not dispatch hooks, query the registry, or aggregate
      parameters. Those responsibilities belong to ``Agent.tool_call()`` and
      its normalization path.
    - Non-reserved fields in a ``.fya`` declaration, such as
      ``requires_approval``, become ordinary attributes on the tool object.
      The framework does not interpret them or trigger behavior from them.
    - ``self._execution`` is non-``None`` only while ``__call__`` is
      dispatching. A tool can inspect its cancellation signal for cooperative
      cancellation; see :mod:`flowing.agent`.

    .. seealso:: :class:`flowing.tool.ScriptTool` and
        :class:`flowing.tool.ToolRegistry`.
    """
    definition: ToolDefinition
    """The default LLM-visible declaration.

    It may be a class or instance attribute. ``ScriptTool.__init__`` creates
    it automatically. Agent-level overrides do not mutate it; see
    :class:`ToolEntry`.
    """
    registry_key: str | None = None
    """The fully qualified ``ns::name`` key written by ``ToolRegistry.register``.

    It is ``None`` before registration. During Agent-entry assembly, a
    file-resolved tool records its location-derived qualified key in
    ``name_ori`` (including a directory-derived namespace), while an ordinary
    registry hit records the bare name. This is an internal attribute.
    """
    _has_caller: bool
    _execution: Any
    _args_model: type[BaseModel]

    async def execute(self, **kwargs: Any) -> Any:
        """Implement the tool operation with ordinary arguments and an optional caller.

        Subclasses override this method. Its arguments come from
        :meth:`ToolEntry.resolve`; ``__call__`` dispatches them according to
        the method signature. If ``execute`` declares ``caller: Agent``,
        Flowing passes the calling Agent so the tool can use its injection
        context, resource lookup, or state. Synchronous implementations are
        also allowed; ``__call__`` detects whether the returned value is
        awaitable.

        .. rubric:: Example

        .. code-block:: python

            async def execute(self, *, order_id: str, amount: float,
                              caller: Agent) -> dict:
                return await payment_service.charge(
                    order_id=order_id, amount=amount)

        .. rubric:: Behavior

        - ``__call__`` wraps ordinary values as completed results, ordinary
          exceptions as error results, and returned ``asyncio.Task`` objects
          as pending receipts that do not wait for completion.
        - Do not construct ``ToolResult`` just to return a value, and do not
          resolve specified values here. The dispatcher has already aggregated
          them. Pass identifiers for complex resources such as clients, then
          retrieve the resource through ``caller``.
        - Long-running work should periodically check the execution
          cancellation signal. Background forms (async generators and
          returned Tasks) are not interrupted by the foreground cancellation
          race, so their implementation must check that signal. Foreground
          awaited work is interrupted by the race and does not need to poll.

        .. seealso:: :meth:`Tool.__call__` for dispatch and
            :class:`ToolResult` for the wrapped result.
        """
        ...

    async def __call__(self, resolved_args: dict[str, Any], *, caller: Any = None, execution: Any = None, tool_call: ToolCall | None = None) -> ToolResult:
        """Run a tool through the framework dispatch path; subclasses must not override this method.

        ``Agent.tool_call()`` invokes this after argument normalization. The
        method handles five responsibilities:

        1. It detects whether ``execute`` is an async generator, another
           awaitable, or synchronous. For an async generator, it waits for the
           first ``yield`` as the receipt (which may be empty) and sends the
           remaining generator to the background driver. Other awaitables are
           awaited, and synchronous return values are used directly.
        2. It wraps asynchronous execution in a Task and applies the
           cancellation race to foreground work. If cancellation wins,
           Flowing interrupts the in-flight await and returns a ``cancelled``
           result, not an error. Background forms (a background flag, returned
           Task, or async generator) do not use this race; cancellation sets an
           Execution signal for the task to handle cooperatively.
        3. It passes ``caller=`` only when signature inspection found that
           ``execute`` declares a caller parameter.
        4. After caller injection and before ``execute``, it validates the
           resolved arguments against the Pydantic model compiled for the
           tool. Failure indicates a framework or host configuration error,
           such as a bad specified value or default. The exception is raised
           and logged through the framework error channel, not converted to
           LLM-visible text, so hidden parameters are not disclosed.
        5. It normalizes returned values, including the ``output`` of a
           returned ``ToolResult``, and wraps the result. A forbidden block
           such as ``ToolCallBlock`` or ``ThinkingBlock`` raises ``ValueError``
           through the framework error channel. An ordinary exception from
           ``execute`` becomes an error result and does not trigger an error
           hook; ``Intercepted`` becomes a blocked result. Async generators,
           ordinary async methods marked ``background = True`` on script
           tools, and returned Tasks produce pending receipts. The caller's
           background driver registers tasks, supports cancellation/query by
           ID and destruction, normalizes each segment or terminal value,
           dispatches yield hooks, and queues EVENT messages. With
           ``caller=None``, there is no background driver; an async generator
           is closed and only its pending receipt is returned.

        :param resolved_args: Canonical arguments that have passed LLM-facing
            validation and had defaults filled, but have not yet passed this
            method's internal validation.
        :param caller: The calling Agent. It is passed to ``execute`` only if
            the signature declares a ``caller`` parameter.
        :param execution: The execution-tracking object attached to
            ``self._execution`` during dispatch.
        :param tool_call: The originating call, when available. The background
            driver uses it to associate results with the tool name and call ID.
        :return: The normalized :class:`ToolResult`.
        :raises pydantic.ValidationError: Internal validation fails because a
            specified value or default is misconfigured. This uses the
            framework error channel and is not LLM-visible.

        .. rubric:: Behavior

        - ``resolved_args`` must already have passed LLM-facing validation and
          received schema defaults in the normalization path.
        - An ordinary async ``execute`` runs to completion and returns its
          value to ``Agent.tool_call`` without enqueueing a message. The three
          background forms return a pending receipt; subsequent yields and
          Task results are queued as EVENT messages with
          ``source="tool_result"``. The ``background`` flag uses the Task
          branch. Queueing depends on the return form, not the calling context.
        - This method does not retry, provide a timeout fallback, or request
          approval. Optional retries come from ``use_retry()``; approval
          belongs in ``before_tool_call``.
        - If ``execution.cancel`` or the caller's turn-abort signal is set
          during foreground execution, the cancellation race interrupts the
          Task and returns a ``cancelled`` result. Background forms do not use
          this race; their cancellation is signaled through the Execution
          registry and their implementation must respond to that signal.

        .. seealso:: :meth:`flowing.agent.Agent.tool_call` and
            :class:`flowing.agent.Execution`.
        """
        ...
