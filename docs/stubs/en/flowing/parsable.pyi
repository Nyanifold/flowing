"""``flowing.parsable`` — unified lazy values and rendering.

.. rubric:: Overview

The module bridges declarative ``.fya`` files and runtime objects. Fields
whose values may be evaluated, such as ``system_prompt`` and ``description``,
are represented as :class:`Parsable` objects and evaluated when their
consumers need them. The module defines two module-level sentinels:
:data:`PENDING` for a deferred field definition and the internal ``_UNSET``
sentinel for an unset value.

``Parsable.type`` is inferred from ``source``. The first matching form below
determines how :meth:`Parsable.resolve` behaves:

.. list-table:: Parsable forms
   :header-rows: 1

   * - Form
     - Source shape
     - Result
     - Rendering
   * - ``RAW``
     - ``$"..."``; matching extends from the first ``$"`` to the last quote
     - ``str``
     - None; the enclosed text is returned directly
   * - ``FILE_REF``
     - A path beginning with ``$``, other than the raw form
     - ``str``
     - File expansion followed by template rendering
   * - ``EXPRESSION``
     - One complete ``{{ expression }}`` after surrounding whitespace is
       stripped
     - The expression's native value
     - File-expansion stage followed by expression evaluation
   * - ``TEMPLATE``
     - Mixed text containing ``{{ ... }}`` or ``{% ... %}``
     - ``str``
     - File-expansion stage followed by template rendering
   * - ``LITERAL``
     - Any other value, including non-string values
     - The original ``source`` value
     - None

The rendering forms use two ordered stages. First, a top-level ``FILE_REF``
reads its referenced file; that expansion is not recursive. Second, Jinja2
evaluates an expression or renders a template. ``{% include %}`` is handled in
the second stage and can be combined with template control structures.
``RAW`` and ``LITERAL`` bypass both stages. Template inheritance through
``{% extends %}`` and ``{% block %}`` is outside the supported contract.

The ``$`` file-reference form and Jinja2 ``include`` are distinct composition
mechanisms. A ``$`` reference is used only at the top level; ``include`` is
used inside template content. Both use ``./``, ``../``, ``@/``, and repeated
parent-directory prefixes, with the same source-directory basis supplied by
the bound Agent or the declaration file. A reference beginning with ``$``
has that marker removed before path resolution.

When an Agent is the rendering context, templates can access ``env`` (a
read-only environment mapping), ``config``, ``agent`` and its alias ``self``,
and flattened instance attributes. Class attributes and methods are accessed
through ``agent`` or ``self``. Agent-declared ``_extra`` values are also
present in their original form; a Parsable stored there is not automatically
resolved. The names ``env``, ``config``, ``agent``, and ``self`` are reserved
for the injected context values. Tool and Skill content may be evaluated
without an Agent; its consumer chooses the available context. The module
registers no template globals such as ``now()`` or ``datetime``; callable
access is through methods reachable from ``agent`` or ``self``. Environment
and configuration values are read during each evaluation rather than frozen
at parse time, and results are not cached.

``.fya`` parsing normally leaves non-string YAML values as their native
values. A field explicitly declared to carry a Parsable value may wrap one as
``LITERAL``. In a field position, ``_`` becomes :data:`PENDING` rather than a
Parsable; ``$"_"`` is instead a raw string containing ``_``. Double-brace
expressions in YAML must be quoted, because an unquoted ``{`` begins a YAML
flow mapping. Non-string YAML values are not normally wrapped; the explicit
Parsable-field case ensures that such a field still receives a Parsable
instance. Outside that case, a non-string ``LITERAL`` source is normally
created through the Python API. Missing required fields and explicit YAML
``null`` values follow the host field's required/optional contract.

The framework resolves only fields owned by its consumers. Plugins and other
extensions may wrap their own values in :class:`Parsable` and call
:meth:`Parsable.resolve` with an explicit context. Evaluation does not
automatically subscribe to changes in referenced attributes, and file changes
are observed only on a later evaluation; there is no filesystem watcher. The
framework does not implicitly unwrap Parsables: fields outside its evaluation
surface remain Parsable objects until their owner explicitly resolves them.
For plugin-owned fields, the plugin documents which values are Parsable,
wraps them when enabling its ``use_xxx()`` hook, and chooses when and with
what context to resolve them.

Evaluation timing depends on the consumer. Prompt content is rendered during
each context assembly; model tags are evaluated before each provider
generation; tool and subagent ``specified`` values are evaluated for each
call; subagent system-prompt overrides are evaluated when the subagent is
created; and tool, skill, or subagent description overrides are evaluated
when definitions, catalogs, or parent-agent routing decisions are rendered.
The framework does not automatically resolve arbitrary ``_extra`` fields.

The mechanism is lazy for correctness, not only performance. At parse time an
environment variable may not yet be loaded, an Agent instance may not exist,
and a referenced file may not yet be ready. File reads and Jinja2 evaluation
therefore occur when requested, and each evaluation observes the current
environment, configuration, instance values, and file contents.
``watch()`` observes property assignments, not logical changes in a
Parsable's rendered value. To react to dependencies, place a field on the
framework's evaluation surface or explicitly watch and assign the dependent
field. File changes are not monitored; they are visible only on a subsequent
evaluation.

``$`` file references and Jinja2 ``include`` are distinct composition
mechanisms. A file reference is expanded only at the Parsable top level
before template rendering; ``include`` is used inside template content and
may be combined with control structures. Both use ``./``, ``../``, ``@/``,
and repeated parent-directory prefixes, with a base supplied by the
declaration directory or bound Agent. For an inline template there is no
containing-file identity: includes still use the bound Agent's source
directory. The leading ``$`` is a form marker and is removed before path
resolution. Bare names are not path forms for this resolution, although
resource lookup may separately match them in a source directory before
consulting a registry.

The implementation uses Jinja2, but only the listed forms and ``include``
are part of this contract. Template inheritance through ``{% extends %}`` and
``{% block %}`` is not supported. Other unlisted Jinja2 constructs may happen
to render but are not guaranteed and may change. File contents are not
recursively expanded, and expression results are not recursively resolved
beyond the single explicit Parsable ``.resolved`` step.

The ``PENDING`` sentinel differs from ``None``, an empty string, an empty list,
and the internal ``_UNSET`` sentinel. A required field left as ``PENDING`` is
checked after ``setup()`` and before ``after_create``; the creation pipeline
raises ``MissingFieldError`` at that checkpoint. In supported override
positions, ``_`` means an empty patch that can inherit the base value, while
resource-list entries reject it. A named-block assignment may replace a
source field that is ``PENDING``; it conflicts with a source field that
already has a value. ``PENDING`` represents a promise for user code to
fulfill, whereas ``_UNSET`` means that no default or explicit value was
supplied.

The public class, its public methods, the five form constants, and
:data:`PENDING` are stable cross-version contracts. Underscore-prefixed
names are internal APIs.

.. rubric:: Example

.. code-block:: python

    from flowing import Agent, Parsable

    class OrderAgent(Agent):
        system_prompt = Parsable("$./system-prompt.md")
        max_turns = Parsable("{{ config.limits.turns }}")

        async def setup(self, user_id: int):
            self.user_id = user_id
            greeting = self.parsable("Hello {{ user_id }}")
            rendered = greeting.resolved

.. seealso::

    :class:`flowing.agent.Agent`
        The main host for Parsable values and its ``parsable()`` and ``watch()``
        methods.
    :mod:`flowing.parser`
        Declarative ``.fya`` parsing and field normalization.
    :mod:`flowing.errors`
        ``MissingContextError``, ``MissingFieldError``, and
        ``ReservedAttributeError``.
"""
from __future__ import annotations
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, Generic, Mapping, TypeVar, overload
import jinja2
from flowing.errors import MissingContextError, ReservedAttributeError

if TYPE_CHECKING:
    from flowing.agent import Agent

T = TypeVar("T")

LITERAL: Final[str]
"""Form marker for a value that does not match another Parsable form.

``resolve()`` returns the original ``source`` without rendering. A string
containing Jinja2 syntax is classified as ``TEMPLATE`` before it can be a
literal; use the raw form when that syntax must remain unchanged. The marker
is a module constant, so compare it by identity or compare ``Parsable.type``
to it rather than relying on its spelling.

The ``.fya`` parser normally leaves non-string YAML values as native Python
values. A host that explicitly declares a field as Parsable may wrap such a
value as ``LITERAL`` so the field consistently contains a Parsable instance.
Other non-string ``LITERAL`` sources normally come from direct Python API use,
such as ``Parsable(123)``. A string containing ``{{ ... }}`` is not kept as a
literal; it matches ``TEMPLATE`` unless it uses the raw form.
"""

FILE_REF: Final[str]
"""Form marker for a top-level file reference beginning with ``$``.

Resolution reads the referenced file at evaluation time and then renders its
contents as a template. The ``$`` marker is removed before path resolution,
and a reference found inside the loaded file is not expanded recursively.
Supported path prefixes include ``./``, ``../``, ``@/``, and repeated
parent-directory prefixes; the bound Agent supplies the source directory.
The file is read again on each evaluation, so edits are visible on the next
resolution without a filesystem watcher. A desired result that itself starts
with ``$`` (for example ``$200.00``) must use the raw form, such as
``$"$200.00"``, or it will be treated as a file reference.
"""

EXPRESSION: Final[str]
"""Form marker for exactly one complete ``{{ expression }}``.

After surrounding whitespace is stripped, the source must contain one whole
expression and no other text. Resolution returns the expression's native
value rather than converting it to a string. When that value is a Parsable,
its ``resolved`` property renders one additional level. Text mixed with the
expression, multiple expressions, or control syntax is classified as
``TEMPLATE`` instead. In ``.fya`` YAML, the expression must be quoted so the
YAML parser does not treat ``{`` as a flow-mapping delimiter. This is the only
form whose result may be a non-string value.
"""

TEMPLATE: Final[str]
"""Form marker for mixed text containing Jinja2 interpolation or control
syntax but not matching the pure-expression form. Resolution returns a
rendered string. Undefined variables follow Jinja2's configured behavior and
render as empty values, including undefined values in chained lookups. A
template may use ``{% include %}`` at any position; include paths use the same
prefix rules and source-directory basis as file references, and includes can
be combined with control structures. Template inheritance via ``{% extends
%}`` and ``{% block %}`` is outside the supported contract. Parsable values
must be explicitly rendered with ``{{ value.resolved }}``; implicit string
conversion displays the source text instead. The result is always ``str``;
use a pure expression when the native value is required.
"""

RAW: Final[str]
"""Form marker for source shaped as ``$"..."``.

Resolution greedily takes the text between the first ``$"`` and the last
quote, without evaluating Jinja2 syntax or reading files. The result is always
a string, even when the enclosed text contains quotes, ``{{ ... }}``, an
underscore, or a leading dollar sign. For example, ``$"quoted: "text""``
retains the inner quotes, ``$"_"`` is the ordinary string ``_`` rather than
:data:`PENDING`, and ``$"$200.00"`` yields ``$200.00`` instead of a file
reference. Raw resolution bypasses both rendering stages and does not require
a bound Agent or explicit context.
"""

class _MissingType:
    """Private singleton type used by :data:`PENDING`."""

    _instance: ClassVar["_MissingType | None"]

    def __new__(cls) -> "_MissingType":
        """Return the process-wide singleton instance."""
        ...

    def __repr__(self) -> str:
        """Return ``\"PENDING\"`` for debugging and error messages."""
        ...

PENDING: Final[_MissingType]
"""Singleton sentinel produced by ``_`` in a declarative field position.

It represents a promise to assign a value later, usually during ``setup()``
or in a named block. It is distinct from ``None``, an empty string, an empty
list, and a Parsable containing ``_``. Use identity checks against
``PENDING``; its truth value has no contract. Missing fields and explicit
``null`` map to ``PENDING`` for required fields and to ``None`` for optional
fields. The raw form ``$"_"`` resolves to the ordinary string ``_``.

The meaning of ``_`` depends on its position. In a field position it marks a
required deferred assignment. In supported entry or ``args`` override
positions it is an empty patch, so the base value can be retained. Resource
list entries reject it with ``FormatError``. A named-block assignment may
replace a source field marked ``PENDING``; it conflicts with a source field
that already has a value.

The creation pipeline checks field-position promises after ``setup()`` and
before ``after_create``. This allows ``before_create`` hooks and Composables
to assign values before the check, while ensuring ``after_create`` observers
see complete fields. Unfulfilled field-position sentinels raise
``MissingFieldError``; override-position sentinels are empty patches and are
not checked. There is one ``PENDING`` instance per process, so identity checks
are reliable across modules.
"""

class Parsable(Generic[T]):
    """A lazy value whose source form determines how it is evaluated.

    ``Parsable[T]`` represents declarative fields such as prompts,
    descriptions, or named content blocks. Its form is inferred from
    ``source`` as ``LITERAL``, ``FILE_REF``, ``EXPRESSION``, ``TEMPLATE``, or
    ``RAW``. A pure expression returns its native result; templates and file
    references return strings; literals return their original value.

    A Parsable can be stored as an Agent class attribute. Class access returns
    the unbound declaration; instance access returns a shallow copy bound to
    that Agent. Evaluation remains lazy: construction does not read files or
    render templates, and each resolution evaluates the current context rather
    than reusing a cached result.

    The ``source`` instruction and inferred ``type`` are declaration
    invariants and do not change after construction. Resolving a value does
    not change the Parsable's own state.

    .. rubric:: Example

    .. code-block:: yaml

        description: "Order lookup assistant"       # LITERAL
        system_prompt: $./system-prompt.md           # FILE_REF
        locale: en
        ---
        $system_prompt:
        You are an order assistant.
        {% if locale == "en" %}
        Reply in English.
        {% else %}
        Reply in the language indicated by locale {{ locale }}.
        {% endif %}
        ---
        $description:
        $"Text containing {{ braces }} as literal content"

    .. rubric:: Behavior

    ``source`` is the stored instruction and ``type`` is inferred when the
    object is constructed. Binding does not evaluate the value. The class is a
    non-data descriptor, so assigning an instance attribute with the same
    name as a class-level Parsable shadows that declaration. A field outside
    the framework's automatic evaluation points remains a Parsable object
    until the caller explicitly resolves it.

    .. seealso:: :data:`LITERAL`, :data:`FILE_REF`, :data:`EXPRESSION`,
        :data:`TEMPLATE`, :data:`RAW`, :data:`PENDING`.
    """
    source: Any
    """The value or instruction from which this Parsable is evaluated.

    The source is fixed when the Parsable is constructed and is shared by its
    bound shallow copies.
    """
    type: str
    """The inferred form marker. It is one of the five module constants.

    The form is inferred at construction and cannot be supplied manually or
    changed afterward.
    """
    _instance: Agent | None
    _source_dir: Path | None

    def __init__(self, source: Any, *, source_dir: Path | None = None) -> None:
        """Create a Parsable and infer its form without evaluating it.

        Construction is synchronous. It does not read referenced files,
        render Jinja2 templates, or access runtime state. A newly constructed
        value is unbound; use an Agent descriptor, ``Agent.parsable()``, or
        :meth:`bind` before resolving without an explicit context.

        :param source: Template text, a ``$`` file reference, a pure
            expression, or a literal value. ``PENDING`` is not a valid source.
        :param source_dir: Optional directory of the declaration file. When
            provided, it is used as the relative-path basis for ``$``
            references and Jinja2 includes, ahead of the bound Agent's source
            directory. This is an internal parameter, not a stable public
            contract.
        """
        ...

    @overload
    def __get__(self, instance: None, owner: type | None = ...) -> Parsable[T]: ...
    @overload
    def __get__(self, instance: Agent, owner: type | None = ...) -> Parsable[T]: ...
    def __get__(self, instance: Agent | None, owner: type | None = None) -> Parsable[T]:
        """Return the unbound declaration on class access or a bound shallow copy.

        Each instance access creates a new shallow copy that shares the
        declaration's ``source`` and ``type`` but carries its own Agent
        context. Class access returns the original object. Binding alone does
        not evaluate the value, and assigning to the instance attribute
        shadows the non-data descriptor.

        :param instance: Agent instance, or ``None`` for class access.
        :param owner: Declaring class supplied by Python's descriptor protocol.
        :return: The original unbound value for class access, or a new bound
            shallow copy for instance access.
        """
        ...

    def bind(self, instance: Agent) -> Parsable[T]:
        """Return a shallow copy bound to ``instance``.

        This is the explicit counterpart of instance-level descriptor access,
        useful when the Parsable was constructed before its consuming Agent
        was known. The original object is unchanged; binding again replaces
        the context on the newly returned copy. A declaration ``source_dir``
        remains the relative-path basis when one was supplied.

        :param instance: Agent to use as the default rendering context.
        :return: A new shallow copy bound to ``instance``.

        .. seealso:: :meth:`__get__`, :meth:`resolve`.
        """
        ...

    def _file_base(self) -> Path | None:
        """Return the base directory for relative file references.

        The declaration's ``source_dir`` takes precedence over the bound
        Agent's source directory. Return ``None`` when neither is available.
        This is an internal helper rather than a stable public API.
        """
        ...

    def resolve(self, context: Agent | Mapping[str, Any] | None = None) -> T:
        """Evaluate this value using the requested rendering context.

        ``RAW`` returns the enclosed text and ``LITERAL`` returns ``source``
        unchanged. ``FILE_REF`` reads the referenced file and renders it;
        ``EXPRESSION`` returns its native value; ``TEMPLATE`` returns a
        rendered string. File expansion precedes Jinja2 rendering, and file
        contents are not recursively expanded. Each call reads current
        environment/configuration/instance values and the current file
        contents; results are not cached.

        For an Agent context, its attributes are available as top-level
        template variables, while ``agent`` and ``self`` refer to the Agent
        itself. ``env`` and ``config`` are supplied by its Runtime. Those four
        names are reserved and cannot be shadowed by Agent instance
        attributes. For a mapping context, mapping entries take precedence
        over the injected ``env`` and ``config`` entries. An unbound mapping
        context can render expressions and templates, but cannot resolve a
        file reference because no Runtime is available. Without a bound Agent,
        ``config`` is not injected into a mapping context, so an undefined
        ``config`` reference follows Jinja2's default empty-value behavior.

        :param context: Agent or mapping to render against. ``None`` uses the
            bound Agent.
        :return: Result determined by the inferred Parsable form.
        :raises flowing.errors.MissingContextError: No bound Agent or explicit
            context is available, or an unbound ``FILE_REF`` needs a Runtime.
        :raises flowing.errors.ReservedAttributeError: An Agent instance uses
            one of the reserved template-context names.
        :raises OSError: A referenced file cannot be read. The filesystem
            exception is not wrapped.

        .. seealso:: :attr:`resolved`, :data:`EXPRESSION`, :data:`RAW`.
        """
        ...

    def _do_resolve(self, agent: Agent) -> T:
        """Build the Agent rendering context and delegate to ``_render``.

        This internal helper reads the Agent's current attributes, Runtime
        environment and configuration for each evaluation. It also supplies
        the include resolver and, for ``FILE_REF``, the resolved file path.
        """
        ...

    def _render(self, ctx: Mapping[str, Any]) -> T:
        """Expand a top-level file reference, then evaluate the Jinja2 form.

        This internal helper performs the two rendering stages in order.
        ``RAW`` and ``LITERAL`` values are short-circuited by ``resolve`` and
        do not enter this method.
        """
        ...

    @classmethod
    def _infer_type(cls, source: Any) -> str:
        """Infer the form marker from ``source``. This is an internal helper."""
        ...

    @property
    def resolved(self) -> T:
        """Evaluate a nested Parsable reference in the current Agent context.

        When a Parsable attribute on an Agent is flattened into the template's
        top-level variables, ``{{ greeting.resolved }}`` evaluates that child
        Parsable at render time. Its evaluation receives the same complete
        context as the outer render.

        Jinja2 normally calls ``str()`` when rendering an arbitrary object.
        For a Parsable, ``str()`` displays the template source without
        evaluating it (see :meth:`__str__`). Therefore, a Parsable referenced
        inside a template needs the explicit ``.resolved`` property to make
        evaluation visible rather than relying on implicit string conversion.
        An explicit ``{{ item.resolve() }}`` call with no arguments on a bound
        Parsable is equivalent, but ``.resolved`` is the idiomatic form. A
        complete nested-reference example appears in the “Parsable references
        in mixed content” section of :data:`TEMPLATE`.

        .. rubric:: Behavior notes

        - Each access evaluates the Parsable again; the result is not cached.
          If a referenced attribute changes between accesses, the later value
          is rendered.
        - The Parsable must be bound (``_instance`` is non-empty). Otherwise,
          this raises the same ``MissingContextError`` as a no-argument call
          to :meth:`resolve`.
        - This is not a reactive subscription and does not register a
          dependency.

        The Parsable must be bound to an Agent. An unbound value raises
        :class:`flowing.errors.MissingContextError` when accessed through this
        property.

        :raises flowing.errors.MissingContextError: The value requires a
            rendering context but is not bound to an Agent.

        .. seealso:: :meth:`resolve` and :meth:`__str__`.
        """
        ...

    def __str__(self) -> str:
        """Return the template source itself without evaluating it.

        This returns the unrendered ``source`` text. If ``source`` is not a
        string (a raw ``LITERAL`` value), it is converted with ``str()``.
        String concatenation, ``str()``, f-strings, and Jinja2's implicit
        string conversion therefore expose the template source, not its
        evaluated result. Displaying the declaration is separate from
        rendering it: evaluation has explicit entry points in
        :meth:`resolve` and :attr:`resolved`. A Parsable referenced inside a
        Jinja2 template must use ``{{ item.resolved }}``; implicit string
        conversion is for debugging and logs.

        ``__str__`` does not call ``resolve()``, read files, or access the
        Runtime, and it does not require the Parsable to be bound. Newlines and
        quotation marks in ``source`` remain unchanged here; escaped display
        is the responsibility of :meth:`__repr__`.

        .. seealso:: :meth:`__repr__`, :meth:`resolve`, and :attr:`resolved`.
        """
        ...

    def __repr__(self) -> str:
        """Return ``Parsable(source=..., type=...)`` for debugging.

        The representation always shows the unrendered ``source`` and its
        inferred ``type``, whether or not the Parsable is bound. This makes
        debugging output, logs, and tracebacks show the template declaration
        rather than a transient evaluation result. It has no side effects and
        does not require a bound instance.

        The format is fixed as ``Parsable(source=<repr>, type=<constant name>)``.
        It does not call ``resolve()``, read files, or access the Runtime.
        Newlines and quotation marks in ``source`` are escaped using its
        ``repr`` representation.

        .. seealso:: :meth:`__str__` returns unescaped source text without
            evaluating it.
        """
        ...


class _ResolvePathLoader(jinja2.BaseLoader):
    """Internal Jinja2 loader using the same path resolver as ``$`` references.

    Inline templates have no containing-file path; without a resolver, includes
    cannot be loaded.
    """

    def __init__(self, resolver: Any) -> None: ...

    def get_source(self, environment: Any, template: str) -> tuple[str, str, Any]:
        ...


class _SelfToAgentTransformer(jinja2.visitor.NodeTransformer):
    """Internal AST transformer that rewrites template ``self`` names to ``agent``."""

    def visit_Name(self, node: Any) -> Any:
        ...
