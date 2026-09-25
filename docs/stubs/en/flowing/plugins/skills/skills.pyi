"""Enablement, catalog rendering, and loading for the Skills extension.

The Skills extension is shipped with Flowing but is not enabled by default.
It lets an Agent declare named Skills and lets the LLM load a Skill's
instructions on demand through the ordinary ``skill-load`` tool mechanism.
The framework core does not interpret the Agent's ``skills:`` field; the
``use_skill(agent)`` composable reads that extension data during ``setup()``.

Enablement has two stages. ``runtime.install(SkillPlugin(...))`` registers
the Runtime-wide ``SkillRegistry`` and ``skill-load`` tool. Calling
``use_skill(self)`` during an Agent's ``setup()`` declares its Skill-loading
hooks, resolves its entries, adds a dynamic catalog prompt block, and binds
the Agent-level Skill methods. Installing the tool does not make it visible
to the LLM: the Agent must also declare ``skill-load`` in ``tools:`` or call
``agent.add_tool("skill-load")``.

.. rubric:: Definition forms and lookup

Each Skill is resolved to the same :class:`flowing.plugins.skills.models.Skill`
data model. Definitions can use Markdown with YAML front matter, a
``.skill.fya`` declaration with an optional ``$script`` block, or a directory
containing a definition and related material. Markdown definitions require a
description and use their body as content. A ``.skill.fya`` ``$script`` block
defines ``on_load``; it does not define Agent ``setup()`` methods or hooks.
Directory-based content can include files from the definition directory.

For a bare ``<name>``, lookup first examines a ``<name>/`` directory for
``SKILL.fya``, ``skill.fya``, ``<name>.skill.fya``, ``<name>.fya``,
``SKILL.md``, then ``skill.md``. It next checks sibling files
``<name>.skill.fya``, ``<name>.fya``, and ``<name>.md``. The first existing
candidate wins. A matching ``.fya``-family definition takes precedence over
Markdown and emits a warning when both forms coexist. Generic filenames infer
the canonical name from their directory. In the Agent declaration flow,
``SkillRegistry.get(name, source_dir=...)`` checks the directed file chain
before its ``default::`` and ``builtin::`` bare-name views; without
``source_dir``, it performs a registry-only lookup. An exact registered key
is reused before file lookup, and a fully qualified ``ns::name`` checks only
that exact registry key. An empty directory reached through a bare name falls
through to the next candidate, while an explicitly referenced directory with
no valid definition raises :class:`flowing.errors.FormatError`. There is no
automatic directory scan: Skill references, explicit paths, and globs drive
targeted resolution. A declared ``skills: _`` is a pending value that must
be resolved before ``use_skill()`` runs; it does not trigger a scan. Globs
combine matching path entries and registry names. Explicit declarations are
processed first, and duplicate resources are removed by resolved identity. A
registry-name glob hit that would claim an alias already used by a different
resource is skipped with a warning.

.. rubric:: Catalog and loading

``use_skill()`` installs ``LazySkillsPrompt`` as a dynamic prompt block.
Definition files are read when declarations are assembled, including
invisible entries. The catalog is rendered at each context assembly using
the current visible entries and the calling Agent's context; rendered values
are not cached. Only visible entries appear in the catalog. Hiding a Skill
blocks the LLM tool path but does not block programmatic
``agent.skill_load(name)``.

The LLM-facing tool and the programmatic method share one loading path:

1. ``before_skill_load`` receives a
   :class:`flowing.plugins.skills.models.SkillLoadContext`;
   handlers may adjust arguments or raise ``Intercepted`` to stop loading.
   Interception here prevents ``on_load``, rendering, ``after_skill_load``,
   message enqueueing, and the ``SkillResult`` return. Other exceptions
   propagate as load failures.
2. The Skill's optional ``on_load`` callback runs.
3. The content is resolved in the calling Agent's context: ``$`` references
   are expanded before Jinja2 template rendering.
4. ``after_skill_load`` receives a
   :class:`flowing.plugins.skills.models.SkillContent` and may adjust the
   rendered body. If it raises ``Intercepted``, the rendered result is
   discarded: no message is enqueued, no result is returned, and the
   exception propagates.
5. The final body is enqueued as a separate ``MessageKind.EVENT`` message
   with source ``skill:<alias>`` and normal priority. The programmatic method
   returns a ``SkillResult``; the ``skill-load`` tool returns only a short
   receipt, never the body in its ``ToolResult``.

The LLM does not provide Skill arguments. Each schema parameter must have a
declaration-time value or a schema default. Declared values take precedence
over defaults and Parsable values are resolved in the calling Agent's
context. A declaration value of ``_`` is treated as unspecified and does not
satisfy coverage validation. Injection expressions such as
``{{ self.inject('key') }}`` are evaluated at load time through the Agent's
provide chain; a missing value raises
:class:`flowing.errors.MissingProvideError`. Missing parameter coverage fails
during declaration rather than being deferred to a Skill load. An operation
that needs the LLM to choose argument values should be modeled as a Tool.

.. rubric:: Usage

Install the plugin once per Runtime, then enable the composable for each
Agent. Adding the tool to the Agent's tool list is a separate visibility
choice:

.. code-block:: python

    runtime.install(SkillPlugin())

    async def setup(self):
        use_skill(self)
        self.add_tool("skill-load")
        # Define these handlers in your Agent.
        self.hooks.before_skill_load(self._scan_content, by="guardrail")
        self.hooks.after_skill_load(self._log_usage, by="audit")

An Agent definition can declare fixed Skill arguments and enable the
composable in its ``$script`` block:

.. code-block:: yaml

    # assistant.fya
    skills:
      - summarize as sum:
          args:
            max_length: 500   # Fixed at declaration time; the LLM does not pass it.
            work_directory: "{{ self.inject('work_directory') }}"
    ---
    $script:
    from flowing.plugins.skills import use_skill

    async def setup(self):
        use_skill(self)

.. rubric:: Behavioral constraints

- ``use_skill()`` declares ``before_skill_load`` and ``after_skill_load`` for
  the ``skill`` owner and dispatches those hooks during loading. The extension
  itself attaches no handlers; other extensions can register handlers after
  the hook points are declared.
- Hook declaration and the ``skill_add``, ``skill_load``, and ``skill_get``
  method bindings are idempotent or preserve an existing user definition.
  Calling the complete ``use_skill()`` function twice on the same Agent is
  not idempotent: the duplicate entry alias raises
  :class:`flowing.errors.EntryNameConflictError`. Recovery runs ``setup()``
  on a new Agent instance and does not repeat that same-instance path.
- The catalog template is a Jinja2 source string, including ``$`` file
  references; it is not a callable renderer. The Agent-level argument to
  ``use_skill()`` overrides the Runtime-level template passed to
  ``SkillPlugin``; if neither is supplied,
  :data:`DEFAULT_CATALOG_TEMPLATE` is used. The template context contains
  ``entries`` and ``agent``.
- If the plugin is not installed or the composable is not used, the extension
  adds no catalog, tool entry, Skill hook points, or ``agent.skill_load``
  method. Calling ``use_skill()`` without the installed registry raises
  :class:`flowing.errors.MissingProvideError`.

.. seealso:: :mod:`flowing.tool` for Tool declarations and invocation,
    :mod:`flowing.hooks` for hook registration and dispatch,
    :mod:`flowing.parsable` for the two-stage rendering rules,
    :mod:`flowing.message` for Plugin messages and the message tree, and
    :mod:`flowing.errors` for ``Intercepted`` and extension errors.
"""

from typing import Any, ClassVar
from flowing.agent import Agent
from flowing.errors import EntryNameConflictError
from flowing.plugins import Plugin
from flowing.runtime import Runtime
from flowing.tool import Tool, ToolDefinition
from .models import (
    CatalogTemplate,
    Skill,
    SkillContent,
    SkillEntry,
    SkillLoadContext,
    SkillResult,
)
from .registry import SkillRegistry, skill_registry_key

__all__ = [
    "SkillPlugin",
    "LazySkillsPrompt",
    "SkillLoadTool",
    "DEFAULT_CATALOG_TEMPLATE",
    "use_skill",
]


class LazySkillsPrompt:
    """Dynamic prompt block that renders the Skill catalog during context assembly.

    ``use_skill()`` registers an instance in ``agent.prompt_blocks`` with
    ``cache="dynamic"``, owner ``"skill"``, and tag ``"skill.catalog"``.
    Each ``resolve()`` call renders the current catalog, so changes to entry
    visibility or Agent-local context are reflected immediately. Only
    rendering is lazy: ``use_skill()`` has already parsed the declared
    definition files into ``SkillRegistry``. Resolving the catalog evaluates
    Parsable values and joins text without file I/O.

    Only visible entries are included, in declaration order. No visible
    entries produce an empty string, and the wrapper installed by
    ``use_skill()`` omits the entire prompt block in that case. Disabling or
    removing the block affects LLM visibility, not programmatic Skill loading.
    The rendered text is not cached, and resolving the prompt does not mutate
    entry state.

    .. seealso:: :class:`flowing.context.PromptBlock`,
        :data:`CatalogTemplate`, :data:`DEFAULT_CATALOG_TEMPLATE`
    """

    def __init__(
        self,
        entries: dict[str, SkillEntry],
        registry: SkillRegistry,
        *,
        catalog_template: CatalogTemplate,
    ) -> None:
        """Create a dynamic catalog block using an already selected template.

        The ``entries`` mapping is retained by reference rather than copied,
        so later visibility changes and additions or removals are reflected by
        the next ``resolve()`` call. The template has already been selected by
        ``use_skill()`` using Agent-level, Runtime-level, then builtin
        precedence; this object performs no template fallback. Resolved Skill
        keys have already been recorded, so looking them up through
        ``registry`` requires no file I/O or ``source_dir``.

        :param entries: Mapping from Agent-local aliases to Skill entries.
        :param registry: The injected Runtime-wide Skill registry.
        :param catalog_template: The selected Jinja2 catalog template.

        .. seealso:: :func:`use_skill`, :meth:`resolve`
        """
        ...

    def resolve(self, agent: Agent) -> str:
        """Render the catalog synchronously for the calling Agent.

        Visible entries are collected in declaration order and passed to the
        configured template as ``(entry, skill)`` pairs with ``agent`` in the
        template context. An empty visible set returns ``""``. This method
        only evaluates Parsable values and joins rendered text; all declared
        definition files were read during ``use_skill()``.

        If a newly added declaration cannot resolve its Skill, or template
        rendering fails, the exception propagates and the current context
        assembly fails. The method does not silently omit broken entries or
        degrade to a partial catalog.

        :param agent: Agent used as the template and Parsable evaluation
            context.
        :return: Rendered catalog text, or an empty string when no entry is
            visible.

        .. seealso:: :class:`SkillRegistry`,
            :meth:`flowing.agent.Agent._assemble_context`
        """
        ...


class SkillLoadTool(Tool):
    """Builtin ``skill-load`` tool that exposes Skill loading to the LLM.

    The LLM selects a name from the available-skills catalog and calls this
    tool. Its schema accepts only ``name``; Skill arguments are fixed by the
    Agent declaration or supplied by schema defaults. The tool delegates to
    ``caller.skill_load(name)`` and therefore uses the same loading behavior
    as the programmatic API. To make the registered tool visible to an Agent,
    declare it in ``tools:`` or call ``agent.add_tool("skill-load")`` after
    enabling Skills.

    A successful call returns ``{"loaded": <alias>}`` as a receipt. The Skill
    body is delivered separately in a Plugin message, never in the tool
    result. An undeclared or invisible name raises ``FlowingError`` and is
    surfaced by ``Tool.__call__`` as an error result. Programmatic loading
    does not apply this LLM-entry visibility check.

    .. seealso:: :class:`flowing.tool.Tool`, :class:`SkillResult`,
        :class:`SkillLoadContext`
    """

    definition: ToolDefinition
    """Class-level declaration for ``skill-load``. Its only parameter is the
    Skill's Agent-local ``name`` alias; the LLM does not supply Skill
    arguments, which are merged by ``skill_load()``.
    """

    async def execute(self, *, name: str, caller: Agent) -> dict[str, Any]:
        """Load the named Skill through the caller Agent and return a receipt.

        This is equivalent to ``await caller.skill_load(name)`` followed by
        wrapping the result as ``{"loaded": name}``. ``Tool.__call__`` wraps
        the returned dictionary in a completed ``ToolResult``. This method
        does not construct the Plugin message, validate Skill arguments, or
        apply ``ToolEntry`` overrides; those concerns belong to the common
        ``skill_load()`` path.

        :param name: Agent-local alias shown in the catalog.
        :param caller: Agent instance enabled with ``use_skill()``.
        :return: A receipt mapping containing the loaded alias. The Skill
            body is delivered in a separate Plugin message.
        :raises flowing.errors.FlowingError: The alias is undeclared or its
            entry is not visible to the LLM tool path. ``Tool.__call__``
            converts this into an error result.

        .. seealso:: :meth:`flowing.tool.Tool.execute`,
            :class:`SkillResult`
        """
        ...


class SkillPlugin(Plugin):
    """Stage-one plugin that registers the Skill tool and shared registry.

    Installing ``SkillPlugin`` registers :class:`SkillLoadTool` in the
    Runtime's tool registry and provides a Runtime-wide
    :class:`SkillRegistry` under :data:`skill_registry_key`. It also exposes
    the registry's ``register()`` method as ``runtime.register_skill`` when
    that name is not already defined. The constructor can supply a
    Runtime-level default catalog template, which an Agent may override in
    ``use_skill()``.

    Installation only registers services: it does not scan directories,
    parse Skill definitions, query other plugins, or modify Runtime state for
    a particular Agent. Call ``use_skill()`` separately for every Agent that
    should use Skills. Each plugin instance is installed once; installing the
    same instance more than once is a programming error.

    .. rubric:: Usage

    .. code-block:: python

        runtime.install(SkillPlugin())
        # Or set a Runtime-level default template:
        runtime.install(SkillPlugin(catalog_template="$./catalog.md.j2"))

    .. seealso:: :func:`use_skill`, :class:`flowing.plugins.Plugin`,
        :meth:`flowing.runtime.Runtime.install`
    """

    name: ClassVar[str]
    """Plugin registration name: ``"skill"``.
    """

    dependencies: ClassVar[list[str]]
    """Plugin dependency metadata. This plugin has no dependencies, so the
    list is empty.

    .. seealso:: :meth:`flowing.runtime.Runtime._check_dependencies`
    """

    def __init__(self, *, catalog_template: CatalogTemplate | None = None) -> None:
        """Create the plugin, optionally with a Runtime-level catalog template.

        Passing ``None`` leaves the Runtime without a configured default. The
        final precedence is the ``use_skill()`` argument, this constructor's
        value, then :data:`DEFAULT_CATALOG_TEMPLATE`. Construction itself
        does not register anything; registration occurs in ``install()``.

        :param catalog_template: Runtime-level default template, or ``None``.

        .. seealso:: :data:`CatalogTemplate`, :data:`DEFAULT_CATALOG_TEMPLATE`
        """
        ...

    def install(self, runtime: Runtime) -> None:
        """Register the ``skill-load`` tool and provide a Skill registry.

        Installation registers ``SkillLoadTool``, provides a new
        ``SkillRegistry`` configured with this plugin's template and the
        Runtime's project root, and binds ``registry.register`` as
        ``runtime.register_skill`` if that attribute is not already defined.
        The operation completes synchronously. Providing the registry key
        again follows the general provide-inject rule that the later value
        replaces the earlier one.

        :param runtime: Runtime receiving this plugin's registrations.
        :raises flowing.errors.ToolNameConflictError: The canonical
            ``skill-load`` tool name is already registered.

        .. seealso:: :class:`SkillLoadTool`, :class:`SkillRegistry`
        """
        ...


def use_skill(
    agent: Agent,
    *,
    catalog_template: CatalogTemplate | None = None,
) -> None:
    """Enable Skill support for one Agent instance during ``setup()``.

    This is the stage-two composable. It adds a dynamic catalog prompt block,
    declares the ``before_skill_load`` and ``after_skill_load`` hook points,
    resolves every declared Skill definition into the Runtime registry
    (including invisible entries), and binds the Agent-level Skill methods.
    Definition reads and validation happen during this call; failures do not
    wait until a later load.

    .. rubric:: Usage

    A handwritten Agent can enable Skills and attach policy handlers as
    follows:

    .. code-block:: python

        async def setup(self):
            use_skill(self, catalog_template=DEFAULT_CATALOG_TEMPLATE)
            self.add_tool("skill-load")
            # Define these handlers in your Agent.
            self.hooks.before_skill_load(self._scan_content, by="guardrail")
            self.hooks.after_skill_load(self._log_usage, by="audit")

    A declarative Agent can supply fixed arguments and enable the composable
    from its ``$script`` block. The ``work_directory`` injection below assumes
    an ancestor provides a value under that key; otherwise loading the Skill
    raises :class:`flowing.errors.MissingProvideError`:

    .. code-block:: yaml

        # assistant.fya
        skills:
          - summarize as sum:
              args:
                max_length: 500   # Fixed at declaration time; the LLM does not pass it.
                work_directory: "{{ self.inject('work_directory') }}"
        ---
        $script:
        from flowing.plugins.skills import use_skill

        async def setup(self):
            use_skill(self)

    .. rubric:: Setup sequence

    The operation follows this order:

    1. It injects the registry from ``skill_registry_key``. If
       ``SkillPlugin`` has not been installed, injection raises
       :class:`flowing.errors.MissingProvideError`.
    2. It declares ``before_skill_load`` and ``after_skill_load`` for owner
       ``"skill"``. Repeating the same declaration is idempotent; reusing a
       hook-point name with a different owner raises
       :class:`flowing.errors.DuplicateHookPointError`.
    3. It binds ``agent.skill_add`` if no such method already exists. This
       method is the single entry-construction path used by the next step.
    4. It reads ``agent.skills`` (the raw ``skills:`` field retained from an
       Agent definition; an absent field means an empty list). An unresolved
       ``PENDING`` declaration or an invalid entry shape raises
       :class:`flowing.errors.FormatError`. Bare names, aliases, explicit
       paths, and globs are normalized using ``SKILL_NAMING``. Path and
       registry-name glob matches are combined after explicit entries and
       deduplicated by resolved resource identity; a registry-name hit that
       would claim an alias already used by a different resource is skipped
       with a warning. Each entry is passed to ``agent.skill_add()``, which
       constructs its binding, resolves its definition into the registry,
       and records the canonical lookup key. Invisible entries are resolved
       too.
    5. It selects the catalog template in this order: this function's
       ``catalog_template`` argument, the value supplied to ``SkillPlugin``,
       then :data:`DEFAULT_CATALOG_TEMPLATE`. It registers a
       :class:`LazySkillsPrompt` in ``agent.prompt_blocks`` with dynamic
       caching, owner ``"skill"``, and tag ``"skill.catalog"``.
    6. It binds ``agent.skill_load`` only if no method with that name already
       exists, preserving a user-defined method.
    7. It binds ``agent.skill_get`` only if no method with that name already
       exists. This is a context-aware thin delegate to
       ``SkillRegistry.get(name, source_dir=agent.source_dir())``.

    ``use_skill()`` does not itself add ``skill-load`` to the Agent's visible
    tool list. The registered tool must be declared in the Agent's
    ``tools:`` field or added explicitly with ``agent.add_tool("skill-load")``.

    Hook declaration and the method bindings are individually safe to repeat
    on one instance, but the complete call is not idempotent: the second call
    encounters an existing entry alias and raises
    :class:`flowing.errors.EntryNameConflictError`. Recovery calls ``setup()``
    on a new Agent instance and does not hit this same-instance conflict.

    .. rubric:: Bound ``skill_load()`` contract

    The bound asynchronous method has the signature
    ``skill_load(name: str) -> SkillResult`` and follows this sequence:

    1. It looks up ``name`` as an Agent-local alias. An undeclared alias raises
       ``KeyError`` as a caller programming error. This programmatic entry
       point does not check ``visible``.
    2. It obtains the shared Skill from the registry using the canonical name
       already recorded during declaration.
    3. It merges schema defaults first and declaration-time ``specified``
       values second. Values are resolved with the calling Agent as context;
       an injection expression such as ``{{ self.inject('key') }}`` searches
       up the provide chain and raises ``MissingProvideError`` if absent.
       Coverage was validated during declaration, so this step has no
       missing-argument branch. The LLM supplies no Skill arguments.
    4. It dispatches ``before_skill_load`` with a
       :class:`flowing.plugins.skills.models.SkillLoadContext`. A handler may
       edit the arguments; raising ``Intercepted`` stops the remaining steps.
    5. It calls the Skill's optional ``on_load(agent, args)`` callback.
    6. It resolves the content synchronously using the Agent's local context
       and the merged arguments, expanding ``$`` references before rendering
       Jinja2 templates.
    7. It dispatches ``after_skill_load`` with a
       :class:`flowing.plugins.skills.models.SkillContent`, which may edit
       the rendered body.
    8. It enqueues a separate message with
       ``kind=MessageKind.EVENT``, ``source=f"skill:{name}"``, the body in a
       ``TextBlock``, and ``MessagePriority.NORMAL``.
    9. It returns ``SkillResult(content=body)``. The body is not merged into
       any ``ToolResult``; no check is made that the LLM has actually seen the
       catalog, and no load-count limit is imposed.

    .. rubric:: Bound ``skill_add()`` contract

    The bound synchronous method has the signature
    ``skill_add(name: str | EntryRef, *, alias: str | None = None,
    body: dict | None = None) -> SkillEntry``.

    - For an ``EntryRef``, ``alias`` and ``body`` must be omitted; otherwise
      the call raises ``FormatError``. A string is normalized into an
      ``EntryRef`` and may be a bare name, ``ns::name``, relative or absolute
      path, or path followed by a bare name.
    - The optional body accepts only ``description``, ``args``, and
      ``visible``. Unknown keys raise ``FormatError``; ``args`` must be a
      mapping unless its value is ``_``. ``description`` becomes a Parsable
      catalog override (``_`` means no override). ``args: _`` is an empty
      patch; other ``args`` values become fixed ``specified`` values,
      resolved in the calling Agent's context when loaded (individual ``_``
      values leave parameters unspecified). Injection is expressed inside an
      ``args`` value with ``{{ self.inject('key') }}``; there is no ``inject``
      body key. An ``args`` mapping cannot use ``as`` to rename arguments.
    - A duplicate Agent-local alias raises
      :class:`flowing.errors.EntryNameConflictError`.
    - The definition is resolved immediately, including when the entry is
      invisible. For each schema parameter, either ``specified`` or a schema
      default must provide a value; a PENDING ``_`` does not count as
      coverage. Missing coverage raises ``FormatError`` at declaration time.
      The completed entry is stored under its alias and is available to the
      next catalog render.
    - Deep values from named ``$skills.<alias>.xxx`` blocks are merged before
      ``skill_add()`` is called.

    :param agent: Agent instance to enable.
    :param catalog_template: Agent-level catalog template, which overrides
        the Runtime-level template. ``None`` selects the next precedence
        source.
    :raises flowing.errors.MissingProvideError: ``SkillPlugin`` has not been
        installed and the registry cannot be injected.
    :raises flowing.errors.FormatError: The ``agent.skills`` declaration is
        invalid or unresolved, an entry body has an unknown key, ``args``
        attempts to rename a parameter, or parameter coverage is incomplete.
    :raises flowing.errors.EntryNameConflictError: This function is called a
        second time for the same Agent and an alias is already registered.

    .. seealso:: :class:`SkillPlugin` for stage-one registration,
        :class:`SkillLoadTool` for the LLM entry point, and
        :class:`flowing.plugins.skills.models.SkillEntry` for the binding
        value.
    """
    ...


DEFAULT_CATALOG_TEMPLATE: str
"""Builtin template for rendering the available-Skills catalog.

This is the default for the catalog's single rendering slot. The slot accepts
a Jinja2 template string, including the ``$./file.j2`` reference form, rather
than a callable renderer. Customizing the presentation replaces the whole
template.

The template receives ``entries`` as ``(entry, skill)`` pairs in the order
declared under ``skills:`` and ``agent`` as the calling Agent. The entry's
``override_description`` takes precedence when present; otherwise the
Skill's ``description`` is resolved in the template against that Agent on
each render. An empty ``entries`` list renders as ``""`` and causes the
dynamic prompt wrapper to omit the whole block. The catalog exposes names and
descriptions only; it does not include a ``<params>`` section because the LLM
does not provide Skill arguments.

.. rubric:: Rendered shape

.. code-block:: xml

    <available_skills>
    <skill>
      <name>sum</name>
      <description>Condense long text into a structured summary.</description>
    </skill>
    </available_skills>

To replace the template, pass a string or file reference to ``use_skill()``:

.. code-block:: python

    use_skill(self, catalog_template="$./my-catalog.md.j2")

Rendering uses Parsable template semantics, with includes resolved relative
to the Agent's ``source_dir``. Rendering errors propagate; they are not
silently downgraded.

.. seealso:: :data:`CatalogTemplate`, :class:`LazySkillsPrompt`
"""
