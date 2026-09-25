"""Data models, hook values, and declaration rules for Skills.

The Skills extension resolves supported definition files into shared
``Skill`` objects and binds those objects to individual Agents through
``SkillEntry`` records. Catalog rendering and body rendering use the calling
Agent as their context; rendered values are not cached on the shared Skill.
The hook values in this module let handlers inspect or modify a load before
rendering and the rendered body before it is delivered.
"""
from collections.abc import Callable
from typing import Any, TypeAlias
from flowing.agent import Agent
from flowing.parsable import Parsable
from flowing.paths import NamingRules
SKILL_NAMING: NamingRules
"""Filename and directory naming rules used to infer canonical Skill names.

Names are inferred from ``<name>.skill.fya``, ``<name>.fya``, and
``<name>.md`` filenames. Generic names such as ``SKILL.fya``, ``skill.fya``,
``SKILL.md``, and ``skill.md`` use the containing directory name instead.
"""
CatalogTemplate: TypeAlias = str
"""Jinja2 template source used to render a Skill catalog.

The template receives ``entries`` (visible ``(SkillEntry, Skill)`` pairs in
declaration order) and ``agent`` (the calling Agent). Parsable values are
resolved for that Agent each time the catalog is rendered. A template may be
an inline string or a ``$`` file reference; it is not a callable renderer.
An empty entry list should render as an empty string. Template and Parsable
errors propagate to the caller. Rendering follows Parsable template semantics:
``$`` file references are expanded before Jinja2 rendering, and Jinja2
``include`` paths are relative to the calling Agent's ``source_dir``. Rendering
does not read Skill definition files; a ``$`` reference used for the template
source itself is read when the template is resolved.
"""
class Skill:
    """Shared executable definition resolved from a Skill file.

    ``SkillRegistry`` resolves Markdown files with YAML front matter,
    ``.skill.fya`` declarations, and directory-based definitions to this data
    model. The file name or directory determines the canonical ``name``;
    an explicitly supplied name is an assertion and a mismatch is an error.
    ``description`` and the Skill body remain Parsable values so each Agent's
    local context is used when they are rendered.

    A registry shares one ``Skill`` instance for each fully qualified name
    across Agents. Callers must not mutate that shared instance. This object
    describes a Skill but does not load it: loading is performed by
    ``skill_load()``. Its optional ``on_load`` callback runs after the
    ``before_skill_load`` hook and before body rendering. It is not a hook and
    cannot intercept loading with ``Intercepted``; an ordinary exception from
    it aborts loading and propagates.

    The ``.md`` form has no argument schema and no ``on_load`` callback, and
    requires a description. Every form requires a body. Skill arguments are
    not supplied by the LLM: each schema parameter must be covered by a
    declaration-time value or a schema default.

    A Markdown definition can make its response-language requirement
    conditional on the supplied ``locale``. Keep the requested language and
    the branch's instructions aligned, as in this example:

    .. code-block:: markdown

        ---
        name: summarize
        description: Summarize long text while preserving key figures, conclusions, and action items.
        ---

        {% if locale == 'zh' %}
        ## Execution instructions
        Extract the key conclusions, important figures, and action items. Reply in Simplified Chinese.
        {% else %}
        ## Execution instructions
        Extract the key conclusions, important figures, and action items. Reply in English.
        {% endif %}

    A block-based definition can supply parameters and a load callback:

    .. code-block:: yaml

        name: summarize
        description: Summarize long text in a structured form.
        content: $./summarize-body.md
        args:
          max_length:
            type: integer
            default: 1000
            description: Maximum summary length in characters.
          work_directory: ""

        $script:
        def on_load(self, agent, args):
            '''Record that this Skill was loaded.'''
            agent.state.register("last_skill", self.name)
    """
    name: str
    """Canonical kebab-case name inferred from the matching filename or
    directory. ``<name>.skill.fya``, ``<name>.fya``, and ``<name>.md`` use the
    filename stem; generic names such as ``SKILL.fya``, ``skill.fya``,
    ``SKILL.md``, and ``skill.md`` use the containing directory name. A
    declared ``name`` is only an assertion: a mismatch raises
    :class:`flowing.errors.NameMismatchError`.
    """
    description: Parsable[str]
    """Parsable description rendered for the calling Agent when a catalog is
    built. The shared Skill object stores the unresolved value; it does not
    cache a rendered description.
    """
    content: Parsable[str]
    """Unresolved Skill instructions. Loading resolves file references and
    then renders the template with the calling Agent's local context and
    merged arguments. For directory definitions, ``$`` references and Jinja2
    includes are based at the directory containing the Skill definition, not
    the referring Agent's ``source_dir``.
    """
    args_schema: dict[str, dict[str, Any]]
    """Normalized parameter schema. Markdown definitions have an empty
    schema. Declaration-time validation requires each parameter to be present
    in the Agent entry's ``specified`` values or have a schema default;
    ``skill_load()`` then uses these defaults as the initial values before
    applying ``specified`` overrides. An empty schema therefore has no
    coverage requirements.
    """
    on_load: Callable[[Agent, dict[str, Any]], Any] | None
    """Optional callback defined in the ``$script`` block. It runs after
    ``before_skill_load`` and before body rendering. It is not a hook, its
    return value is ignored, and an exception aborts loading.
    """
    _extra_fields: dict[str, Any]
    """Unrecognized definition fields retained for attribute fallback.
    This is an internal field, not a stable extension surface.
    """
    registry_key: str | None
    """Fully qualified registry key assigned when this Skill is registered;
    it is ``None`` before registration. This field is internal and not a
    stable extension surface.
    """
    def __init__(self, name: str, description: str | Parsable[str] = "", content: str | Parsable[str] = "", *, args_schema: dict[str, dict[str, Any]] | None = None, on_load: Callable[[Agent, dict[str, Any]], Any] | None = None, _extra_fields: dict[str, Any] | None = None, registry_key: str | None = None) -> None:
        """Create a Skill definition without reading files or registering it.

        String descriptions and bodies are wrapped as ``Parsable`` values;
        existing ``Parsable`` instances are retained. The argument schema
        defaults to an empty mapping, and ``registry_key`` defaults to
        ``None``. File parsing belongs to ``SkillRegistry``.

        :param name: Canonical Skill name.
        :param description: Description as text or a Parsable value.
        :param content: Body as text or a Parsable value.
        :param args_schema: JSON Schema properties for Skill parameters.
        :param on_load: Optional callback run during loading.
        :param _extra_fields: Additional parsed fields.
        :param registry_key: Fully qualified registry key, if already known.
        """
        ...

    def __getattr__(self, name: str) -> Any:
        """Fall back to looking up an undefined attribute in ``_extra_fields``.

        If ``name`` is present in ``_extra_fields``, its value is returned
        unchanged; Parsable values are not resolved. Otherwise this raises
        ``AttributeError``, preserving normal ``getattr(obj, name, default)``
        behavior. Before ``_extra_fields`` has been initialized, the fallback
        is skipped and ``AttributeError`` is raised without recursive lookup.

        :param name: Attribute name to look up.
        """
        ...
class SkillEntry:
    """Per-Agent binding between a Skill definition and the Agent that uses it.

    A ``SkillEntry`` stores the Agent-specific alias, fixed parameter values,
    optional description override, and catalog visibility without mutating
    the registry's shared ``Skill``. The alias is the name shown in the
    catalog and the name accepted by the ``skill-load`` tool and
    ``agent.skill_load()``.

    Values in ``specified`` are resolved against the calling Agent when the
    Skill loads. Parameters are not supplied by the LLM; every schema
    parameter must be covered by ``specified`` or have a schema default.
    ``visible=False`` hides the entry from the catalog and rejects loading
    through the LLM tool, but does not prevent programmatic
    ``agent.skill_load()``. Duplicate aliases within one Agent are rejected.

    The declaration body accepts only ``description``, ``args``, and
    ``visible``. An ``args`` mapping cannot rename a parameter with ``as``.
    A value of ``_`` (PENDING) is treated as if that parameter were not
    specified, so it does not satisfy coverage validation. Injection is
    expressed in an ``args`` value, for example
    ``"{{ self.inject('work_directory') }}"``; it is evaluated at load time
    against the calling Agent's provide chain, and a missing value raises
    :class:`flowing.errors.MissingProvideError`. A description override is
    catalog-only text and does not change the loaded Skill body.

    .. rubric:: Example

    .. code-block:: yaml

        # agent.fya
        skills:
          - summarize as sum:
              args:
                max_length: 500
                work_directory: "{{ self.inject('work_directory') }}"
              description: "Long-form summary for this Agent"
          - translate
          - audit:
              visible: false
    """
    name_alias: str
    """Agent-local alias used in the catalog and both Skill-loading APIs."""
    name_ori: str
    """Canonical Skill name used to locate the shared definition. For
    file-derived definitions, declaration setup may replace this with the
    derived fully qualified registry key so later loads use the exact cached
    object without repeating file lookup.
    """
    specified: dict[str, Parsable]
    """Agent-local fixed values for Skill parameters. Parsable values are
    resolved against the calling Agent during loading and override schema
    defaults; the LLM does not pass Skill arguments. A PENDING ``_`` value is
    omitted rather than stored here. Values containing
    ``{{ self.inject('key') }}`` resolve through the Agent's provide chain.
    """
    override_description: Parsable[str] | None = None
    """Optional Agent-local catalog description override. When absent, the
    Skill's own description is rendered instead. Parsable values resolve in
    the calling Agent's context when the catalog is rendered. This changes
    only the LLM-visible catalog description, not Skill loading or body
    rendering.
    """
    visible: bool = True
    """Whether this entry appears in the LLM-facing catalog. Hiding an entry
    also blocks the LLM tool path, but not programmatic loading.
    """
class SkillLoadContext:
    """Mutable value passed to ``before_skill_load`` before Skill loading.

    It contains the calling Agent, the requested Agent-local alias, and the
    merged arguments after schema defaults and declaration values are
    combined. Handlers may edit the argument mapping or return a replacement
    context; the next handler sees the updated value. Raising ``Intercepted``
    terminates loading: ``on_load``, body rendering, ``after_skill_load``,
    message enqueueing, and the ``SkillResult`` return do not occur. Any other
    exception propagates as a load failure. The hook is not called when no
    handlers are registered.

    .. rubric:: Example

    .. code-block:: python

        async def _scan(self, agent, ctx: SkillLoadContext):
            if ctx.name in self.blocked_skills:
                raise Intercepted(f"Skill {ctx.name} is disabled by policy")
            ctx.args.setdefault("max_length", 500)
            return ctx

        self.hooks.before_skill_load(_scan, by="guardrail")
    """
    agent: Agent
    """Agent that requested the load and the root of its rendering context."""
    name: str
    """Agent-local Skill alias requested by the caller."""
    args: dict[str, Any]
    """Merged parameters used by ``on_load`` and body rendering. Handlers may
    modify this mapping before those steps run.
    """
class SkillContent:
    """Rendered Skill body passed to ``after_skill_load`` for final adjustment.

    This hook value is the result of ``content.resolve()`` at step four of
    Skill loading. A handler may edit ``body`` (for example, append an audit
    marker or remove sensitive sections); the edited body is used in both the
    ``EVENT`` message and :class:`SkillResult`.

    .. rubric:: Example

    .. code-block:: python

        def log_usage(self, agent, content: SkillContent):
            self.audit.log(
                "skill_loaded", skill=content.name, bytes=len(content.body)
            )
            return content

        self.hooks.after_skill_load(log_usage, by="audit")

    .. rubric:: Behavior notes

    Handlers may return the (possibly edited) value for the next handler.
    Raising ``Intercepted`` here means that rendering has completed but the
    result is discarded: no ``EVENT`` message is enqueued, ``skill_load()``
    does not return, and the exception propagates to the caller. Use this
    behavior with care. Any other exception also propagates and fails the
    load.

    An empty string in ``body`` is valid. An ``EVENT`` message is still
    enqueued with an empty text block.

    .. seealso:: :class:`SkillLoadContext`, :class:`SkillResult`.
    """
    name: str
    """Agent-local alias of the loaded Skill."""
    body: str
    """Rendered body after ``on_load`` and template resolution. Handlers may
    change it; the final text is shared by the Plugin message and
    ``SkillResult``.
    """
class SkillResult:
    """Programmatic result of loading a Skill.

    ``content`` contains the final body after ``after_skill_load`` handlers.
    The same text is placed in a separate ``EVENT`` message for the Agent's
    later context. The ``skill-load`` tool returns only a short receipt; it
    does not place the body in its ``ToolResult``.
    """
    content: str
    """Final rendered body, identical to the text in the queued ``EVENT``
    message.
    """
