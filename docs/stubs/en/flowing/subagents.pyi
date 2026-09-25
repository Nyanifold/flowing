"""Binding and result types for the subagent subsystem.

.. rubric:: Overview

This module defines the Agent-level binding entry, hook values, and result
value used by the subagent subsystem:

- :class:`SubagentEntry` binds a subagent type to one parent Agent. It supplies
  the LLM-facing alias and description, parameter overrides, fixed values,
  and injected values. This is the binding layer of Flowing's three-layer
  capability description and is structured like ``ToolEntry``.
- :class:`SubagentInvocation` is passed to the
  ``on_subagent_invoke`` and ``on_subagent_returns`` hooks.
- :class:`SubagentResult` contains the result of one subagent invocation.
- ``DEFAULT_SUBAGENT_CATALOG_TEMPLATE`` is the built-in template for the
  ``<available_subagents>`` catalog section.

The invocation pipeline and catalog rendering remain in ``flowing.agent``.
This module defines the entries and result values used by that pipeline.

.. rubric:: Behavior notes

- Subagent invocation has a preparation phase and a run phase. Preparation
  resolves the entry, aggregates arguments, creates a
  :class:`SubagentInvocation`, and dispatches ``on_subagent_invoke`` on the
  parent Agent before creating or resuming the child. The run phase waits for
  the child, creates a :class:`SubagentResult`, and dispatches
  ``on_subagent_returns`` before delivering the result. Both hooks belong to
  the parent Agent.
- Results can be delivered in two ways. ``invoke_subagent`` returns a
  :class:`SubagentResult` directly and does not enqueue it on the parent; the
  caller decides what to do with it. The asynchronous ``subagent-invoke`` tool
  path instead enqueues a separate ``Message(kind=SUBAGENT)`` on the parent
  when the child finishes, so the LLM can see it in a later turn. Both paths
  use the result after ``on_subagent_returns`` handlers have had a chance to
  modify it.
- Subagents have no dedicated error hook. Invocation and execution failures
  are raised to the caller. On the ``subagent-invoke`` tool path, the failure
  is represented by ``ToolResult(status="error")``.

.. seealso::

    - ``flowing.agent`` for invocation and catalog rendering
    - ``flowing.hooks`` for the two subagent hook contracts
    - ``flowing.tool`` for the corresponding ``ToolEntry`` binding layer
"""

from typing import Any

from flowing.parsable import Parsable

DEFAULT_SUBAGENT_CATALOG_TEMPLATE: str
"""Built-in default template that wraps each ``<subagent>`` element in
``<available_subagents>``. The slot accepts a Jinja2 template string, not a
callable; customization replaces the whole template. It is public for
reference and derivation. An Agent can override it through
``Agent.subagent_catalog_template``.

The template context contains ``entries``, a list of dictionaries produced by
``SubagentEntry.catalog_view``. Each dictionary provides ``name`` (the alias),
``description`` (already resolved), and ``params_xml`` (the parameter XML
after overrides, aliases, and exclusions, or an empty string). The parent
Agent is available as ``agent``. Values are prepared in Python; the template
only arranges them. Includes resolve relative to the parent Agent's
``source_dir``. Rendering errors propagate. If there are no entries, the
template renders to an empty string and the catalog block is omitted.

.. rubric:: Usage example

The default template produces the following shape:

.. code-block:: xml

    <available_subagents>
    <subagent><name>pay</name><description>Submit a payment.</description><params>...</params></subagent>
    </available_subagents>

An Agent can replace the template with a Jinja2 source file:

.. code-block:: python

    class OrderAgent(Agent):
        subagent_catalog_template = "$./my-catalog.j2"

.. seealso:: ``Agent.subagent_catalog_template`` and
    ``SubagentEntry.catalog_view``
"""


class SubagentResult:
    """The result value returned by one subagent invocation.

    .. rubric:: Overview

    This is the return type of ``Agent.invoke_subagent`` and the delivery
    object that an ``on_subagent_returns`` handler can modify. The modified
    result is written back to ``SubagentInvocation.result`` before delivery,
    so it affects both the method's return value and the corresponding
    ``SUBAGENT`` message. This value contains JSON-serializable data and does
    not retain a live Agent instance.

    .. rubric:: Behavior notes

    - ``subagent_id`` is the child's ``node_id``. To obtain a live instance,
      call ``runtime.get_agent(subagent_id)``; this may recover an Agent whose
      pool entry exists but whose instance is not loaded. Do not retain an
      instance through this result object.
    - ``result`` is the child's final output. By default it is the text of the
      final reply. If the child uses ``FinishTool`` to return structured data,
      it is a dictionary of those fields. It is ``None`` when there is no
      completed output, such as cancellation before a response. The value is
      taken from ``Agent.last_result``.
    - ``subagent_status`` is the child's turn outcome and preserves
      ``TurnResult.status``. Status is separate from result content: on
      cancellation, a previously set finish payload or produced text remains
      in ``result`` while this field reports the turn outcome.
    - The result does not contain the child's full message history. That
      history belongs to the child's session and can be inspected through the
      child Agent or its snapshot.

    .. seealso:: ``flowing.agent.Agent.invoke_subagent``,
        :class:`SubagentInvocation`, and ``flowing.message.MessageKind``
    """

    name_alias: str
    """Caller-facing identifier for the invocation. It is selected from the
    resume name, semantic child name, or invocation alias, in that order. A
    semantic name is stored only by the parent Agent in ``child_ids``; the
    child does not hold its own name.
    """
    subagent_id: str
    """The child Agent's ``node_id``. This data field is JSON-serializable and
    does not retain a live instance. Use ``runtime.get_agent(subagent_id)`` to
    obtain the instance; a destroyed instance may be recovered from its
    retained pool entry and session.
    """
    result: Any
    """The child's final output, taken from ``Agent.last_result``. It is
    normally reply text, a field dictionary when the finish tool returned
    structured data, or ``None`` when the turn produced no output. A non-``None``
    ``finish_output`` takes precedence; otherwise finalization uses the text
    of the last Provider message, normalizing an empty string to ``None``.
    """
    subagent_status: str
    """Outcome of the child's turn, preserving ``TurnResult.status``:
    ``"completed"``, ``"blocked"``, ``"error"``, or ``"cancelled"``. This
    reports cancellation or failure separately from ``result``; output already
    produced remains unchanged.
    """


class SubagentInvocation:
    """The value passed to subagent invocation and return hooks.

    .. rubric:: Overview

    ``Agent.invoke_subagent`` constructs this value after
    ``SubagentEntry.resolve()`` and before creating or resuming the child. It
    dispatches ``on_subagent_invoke`` on the parent Agent. After the child
    produces a result, the framework stores it in ``result`` and dispatches
    ``on_subagent_returns`` before delivering the result.

    .. rubric:: Behavior notes

    - An ``on_subagent_invoke`` handler can modify ``args`` or ``prompt``. It
      can raise ``Intercepted`` to block invocation; the exception propagates
      synchronously and the child is not created.
    - ``on_subagent_returns`` is the final point where a handler can modify
      ``result``. Its changes are used both as the direct return value and in
      the ``SUBAGENT`` message. This hook does not use ``Intercepted`` as a
      delivery-blocking mechanism; blocking belongs to the before hook, while
      result handling is expressed by modifying the result. During the before
      hook, ``result`` is ``None``.
    - Creation, validation, or execution failures have no dedicated subagent
      error hook and propagate to ``invoke_subagent`` callers. On the
      ``subagent-invoke`` tool path, they are returned as
      ``ToolResult(status="error")``.

    .. seealso:: ``flowing.agent.Agent.invoke_subagent`` and
        :class:`SubagentEntry`
    """

    alias: str
    """Alias used for this invocation: the key of the corresponding entry in
    the parent Agent's binding table.
    """
    agent_type: str | None
    """Agent type name for a new child, taken from
    ``SubagentEntry.name_ori``. It is ``None`` when resuming an existing child;
    a new-child type may include a registry namespace such as ``ns::name``.
    """
    resume: str | None
    """Semantic name of the child to resume from the parent Agent's
    ``child_ids`` mapping. It is mutually exclusive with ``agent_type``;
    resuming keeps the child's existing type.
    """
    prompt: str | None
    """Natural-language task prompt, or ``None`` for an invocation based only
    on initialization arguments.
    """
    args: dict[str, Any]
    """Complete initialization keyword arguments produced by
    ``SubagentEntry.resolve``. Parameter aliases have been mapped back to
    canonical names, and specified values have been injected.
    """
    name: str | None
    """Semantic name assigned to a newly created child and stored in the
    parent Agent's ``child_ids`` mapping for later ``resume=...`` lookup. It is
    ``None`` for an unnamed child; the child instance does not store this name.
    """
    result: SubagentResult | None
    """Invocation result populated before ``on_subagent_returns`` runs. It is
    ``None`` during the before-invocation hook.
    """


class SubagentEntry:
    """An Agent-level declaration of how to use one subagent type.

    .. rubric:: Overview

    Like ``ToolEntry``, this entry describes how one parent Agent uses an
    executable subagent type: the alias and description visible to the LLM,
    parameter overrides, fixed values, and injected values. Each Agent
    instance owns its own entries, indexed by alias.

    The binding layer lets the same subagent type have different descriptions
    and parameter defaults on different parent Agents, without changing the
    subagent class. Subagents are not exposed as separate tool definitions;
    the framework provides one ``subagent-invoke`` tool and supplies each
    entry's parameter description in the ``<available_subagents>`` XML
    catalog.

    .. rubric:: Usage example

    A declarative ``.fya`` entry can override the description, alias a
    parameter, provide a fixed value, and inject a value:

    .. code-block:: yaml

        subagents:
          - payment as pay:
              description: "Submit a payment after the user confirms."
              args:
                amount as sum:
                  description: "The payment amount."
                currency: USD
                user_id: "{{ self.inject('user_id') }}"

    The same binding can be assembled from ``setup()``:

    .. code-block:: python

        entry = self.add_agent(
            "payment",
            alias="pay",
            body={
                "description": "Submit a payment.",
                "args": {
                    "amount as sum": {"description": "The payment amount."},
                    "currency": "USD",
                    "user_id": "{{ self.inject('user_id') }}",
                },
            },
        )

    .. rubric:: Behavior notes

    - When ``visible=False``, the entry is omitted from the catalog and hidden
      from the LLM, but it can still be invoked programmatically. Visibility
      and executability are independent.
    - Parameters declared in ``specified`` (fixed values or injection
      expressions) are omitted from the catalog and are not LLM-visible. An
      injected expression is evaluated to produce an initialization argument
      for child creation, such as ``create_subagent(user_id=...)``; it does
      not modify the child's internal behavior. This differs from Tool-side
      injection, whose result becomes an ``execute()`` argument.
    - The rules for classifying an overridden ``args:`` mapping into parameter
      overrides, specified values, and aliases match ``ToolEntry``. The
      evaluation context for specified values is the parent Agent, and an
      injection result becomes a child initialization argument.
    - ``override_system_prompt`` records a declared prompt override, but the
      current child-creation path still uses the child class's own
      ``system_prompt``; this field is not applied when creating the child.
    - The assembly layer can fill named nested blocks such as
      ``$subagents.<alias>.<field>:`` before binding. Entries are addressed by
      exact alias, not by list index. A missing alias raises ``FormatError``.
      Keys containing ``as`` must be addressed through their alias. A pending
      ``_`` slot can be filled; a block cannot replace a value that is already
      present or replace an entire entry instead of a mapping field. Supported
      fields include ``system_prompt``, ``description``, and ``args``.
    - If ``as`` is omitted, ``normalize_entries`` infers an alias from the
      reference: a bare name such as ``payment`` keeps that name, a qualified
      name such as ``ns::payment`` uses its final name segment, and a path
      uses ``infer_name``.
    - Duplicate aliases in a ``.fya`` subagent list raise
      ``EntryNameConflictError``. There is no later-entry-wins behavior.
      This includes different paths that infer the same name. The exception
      is a glob result that resolves to the same resource as an explicit or
      earlier entry: that duplicate resource is skipped. Distinct resources
      that yield the same alias remain an error.
    - Glob candidates are filtered by name and resource shape before this
      entry-binding check. Rejected candidates are skipped with a warning;
      explicitly named entries are not filtered this way and still report
      errors normally.
    - A reference may use ``ns::name`` to select a registered resource. The
      namespace is an internal identity and is not shown to the LLM; the
      catalog and invocation use the alias. If one Agent binds two resources
      with the same visible name, including resources from different
      namespaces, give at least one an explicit ``as`` alias. Different
      Agents can independently use the same alias.
    - The entry does not own a subagent instance. Creation and reuse are
      handled by ``invoke_subagent`` and the Agent pool.

    .. seealso:: ``flowing.tool.ToolEntry``,
        ``flowing.agent.Agent.add_agent``,
        ``flowing.agent.Agent.invoke_subagent``, and
        ``flowing.parsable.Parsable.resolve``
    """

    name_alias: str
    """Alias shown to the LLM in the catalog and used by
    ``invoke_subagent()`` to select this entry. The parent Agent indexes its
    entries by this alias, which is also used to address nested named blocks.
    """
    name_ori: str
    """Canonical subagent type name used for lazy Agent-class resolution. A
    registered resource may use the ``ns::name`` form. The namespace is an
    internal identity and is not exposed to the LLM; the catalog and
    invocation use the alias instead.
    """
    override_system_prompt: Parsable | None
    """Declared override for the child Agent's system prompt, or ``None`` when
    there is no override. A ``.fya`` ``system_prompt:`` field or a
    programmatic body can set it. A pending ``_`` value is normalized to
    ``None``, which means that the base prompt is left unchanged. The current
    creation path still uses the child class's own ``system_prompt`` and does
    not apply this stored override.
    """
    override_description: Parsable | None
    """Override for the description shown to the LLM. ``None`` uses the
    child class's original description. A supplied value is resolved in the
    parent Agent's context when ``catalog_view()`` renders the catalog.
    """
    override_params: dict[str, dict[str, Any]] | None
    """Sparse parameter overrides in the form
    ``{canonical_parameter: {JSON_Schema_keyword: value}}``. For example,
    ``{"currency": {"default": "CNY"}}`` changes only that schema field.
    ``catalog_view()`` applies the patch when building ``<params>``; omitted
    keywords retain their base values.
    """
    specified: dict[str, Parsable]
    """Fixed or injected initialization arguments hidden from the LLM and
    protected from LLM overrides. ``resolve()`` evaluates each value in the
    parent Agent's context and passes it to the child. Values may be fixed,
    such as ``Parsable("USD")``, or injection expressions, such as
    ``Parsable("{{ self.inject('user_id') }}")``. An injection with no
    matching provider raises ``MissingProvideError``. The mapping is empty by
    default.
    """
    param_aliases: dict[str, str]
    """Mapping from LLM-visible parameter names to canonical parameter names.
    ``catalog_view()`` emits the aliases; the mapping is empty by default.
    """
    visible: bool
    """Whether this entry appears in the LLM-visible catalog. When ``False``,
    the child remains callable through ``invoke_subagent()``. Assigning
    ``True`` or ``False`` changes visibility on the next context assembly;
    there is no container-level disable API for these bindings.
    """

    def resolve(self, parent: "Agent", args: dict[str, Any]) -> dict[str, Any]:
        """Combine caller arguments with this entry's fixed and injected values.

        .. rubric:: Overview

        This method is called by ``Agent.invoke_subagent``. It first maps
        LLM-visible argument names back to canonical names through
        ``param_aliases``, then evaluates values in ``specified`` using the
        parent Agent as context. Fixed values are passed through; injection
        expressions resolve through ``parent.inject(...)``. Specified values
        take precedence over caller arguments with the same canonical name.

        The result is passed directly as initialization arguments to
        ``create_subagent``. Arguments are passed to the child only; Flowing
        does not forward them to grandchildren. The current implementation
        does not validate the merged result against the child ``args_model``.

        .. rubric:: Usage example

        .. code-block:: python

            entry = SubagentEntry(
                name_alias="pay", name_ori="payment",
                param_aliases={"sum": "amount"},
                specified={"currency": Parsable("CNY"),
                           "user_id": Parsable("{{ self.inject('user_id') }}")},
            )
            resolved = entry.resolve(parent, {"sum": 100})
            # resolved includes amount=100, currency="CNY", and injected user_id
            # user_id is read through the parent provide-inject chain.

        .. rubric:: Behavior notes

        If an injection expression cannot find its key while walking the
        provide-inject chain, resolution raises ``MissingProvideError``.

        :param parent: The parent Agent used to evaluate injection expressions.
        :param args: Arguments supplied for this invocation.
        :return: The resolved initialization arguments for the child Agent.
        :raises flowing.errors.MissingProvideError: If an injection expression
            cannot find its key in the provide-inject chain.

        .. seealso:: ``flowing.tool.ToolEntry.resolve`` for the analogous
            Tool-side argument aggregation.
        """
        ...

    def catalog_view(self, parent: "Agent") -> dict[str, Any]:
        """Build this entry's LLM-visible catalog data.

        .. rubric:: Overview

        During context assembly, the Agent calls this method for each visible
        entry. The returned dictionary is added to the ``entries`` context
        used by ``DEFAULT_SUBAGENT_CATALOG_TEMPLATE`` or an Agent-level
        replacement template; Python prepares the values and the template
        controls their layout.

        The returned mapping contains ``name`` (the entry alias),
        ``description`` (the resolved description), and ``params_xml`` (the
        parameter XML after overrides, aliases, and exclusions are applied).

        .. rubric:: Behavior notes

        - If ``override_description`` is set, it is resolved in the parent
          Agent context. Otherwise the child class description is used; a
          missing or ``None`` class description becomes an empty string.
        - ``params_xml`` is derived from the child class ``args_model``.
          ``override_params`` is applied as a sparse patch first; an invalid
          schema keyword raises ``FormatError``, while an unknown parameter
          name is treated as a new parameter. Parameters in ``specified`` are
          then removed, and ``param_aliases`` renames the remaining fields.
          A name collision during renaming raises ``FormatError``. No visible
          parameters, or no child ``args_model``, produces an empty string.
        - The caller filters entries with ``visible=False``. This method does
          not check visibility, and its output for a hidden entry has no
          contract.
        - This entry does not make the ``subagent-invoke`` tool visible.
          Declare the tool through ``tools:`` or
          ``add_tool("subagent-invoke")``. The catalog XML and tool binding
          are separate parts of context assembly.

        :param parent: The parent Agent used to resolve entry values.
        :return: A mapping with the alias, resolved description, and rendered
            parameter XML.

        .. seealso:: :meth:`resolve`,
            :data:`DEFAULT_SUBAGENT_CATALOG_TEMPLATE`, and
            ``flowing.context.Context``
        """
        ...
