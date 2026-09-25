"""Registry and injection key for Skill definitions.

This module provides ``skill_registry_key`` and ``SkillRegistry``. The
registry maps fully qualified ``ns::name`` keys to parsed, shared ``Skill``
objects. It also resolves the supported Skill definition forms into the same
data model; its parsing helpers are internal implementation details.

.. seealso:: :mod:`flowing.plugins.skills` for the extension's enablement
    contract and :mod:`flowing.plugins.skills.models` for its data models.
"""

from pathlib import Path
from flowing.params import InjectionKey
from .models import CatalogTemplate, Skill

skill_registry_key: InjectionKey["SkillRegistry"]
"""Injection key for the Runtime-wide ``SkillRegistry``.

``SkillPlugin.install()`` provides the registry at the Runtime root under
the key name ``"skill_registry"``. ``use_skill()`` consumes it through
``agent.inject(skill_registry_key)``, which searches upward through the
provide-inject parent chain. Injecting this key before installing
``SkillPlugin`` raises :class:`flowing.errors.MissingProvideError`.

Providing the same key again replaces its previous value, following the
general provide-inject contract. Plugins avoid collisions by using distinct
key-name prefixes.

.. seealso:: :class:`flowing.params.InjectionKey`,
    :class:`flowing.plugins.skills.SkillPlugin`,
    :func:`flowing.plugins.skills.use_skill`,
    :meth:`flowing.agent.Agent.inject`
"""


class SkillRegistry:
    """Runtime-scoped cache and resolver for shared Skill definitions.

    ``SkillPlugin.install()`` creates one registry per Runtime and provides it
    through :data:`skill_registry_key`. During ``use_skill()``, every declared
    entry, including invisible entries, is resolved once into this registry.
    Catalog rendering and ``skill_load()`` then use in-memory lookups; reading
    definition files is eager at declaration time, while Parsable descriptions
    and bodies are rendered later in the calling Agent's context and are not
    cached in the shared ``Skill`` object.

    .. rubric:: Usage

    .. code-block:: python

        registry = agent.inject(skill_registry_key)
        skill = registry.get("summarize", source_dir=agent.source_dir())

    Catalog rendering and ``skill_load()`` normally use the registry
    internally, so applications rarely need to call it directly.

    .. rubric:: Behavior

    - A cached name returns its shared ``Skill`` instance. Otherwise, ``get``
      resolves a definition according to the directed lookup rules, parses it,
      caches it, and returns it. ``use_skill()`` normally pre-resolves all
      declared entries; the uncached path supports programmatic lookups that
      were not declared by an Agent.
    - The registry does not warm itself by scanning directories and does not
      watch files for changes. Once a definition is parsed, later file edits
      do not change the cached object.
    - Parsing is synchronous file I/O, so concurrent parsing of the same
      canonical name does not occur.
    - A parsing failure propagates and does not populate the cache; a later
      lookup may try again.
    - ``source_dir`` is the directory containing the referring Agent
      definition, not an implicit ``skills/`` subdirectory. Explicit paths
      and glob entries are normalized by ``use_skill()`` into a canonical
      name and lookup root at declaration time.
    - A fully qualified ``ns::name`` key identifies one shared object within
      a Runtime. Agents using that same key receive the same ``Skill``
      instance.

    .. seealso:: :class:`flowing.plugins.skills.models.Skill`, :meth:`get`,
        and :data:`skill_registry_key`
    """

    catalog_template: CatalogTemplate | None = None
    """Runtime-level default catalog template supplied by
    ``SkillPlugin.install()``.

    This is the middle value in the template precedence chain:
    ``use_skill()`` argument, then this attribute, then
    :data:`flowing.plugins.skills.skills.DEFAULT_CATALOG_TEMPLATE`. ``None``
    means that no Runtime-level default was configured. Treat the value as
    read-only after installation.
    """

    def __init__(
        self,
        *,
        catalog_template: CatalogTemplate | None = None,
        project_root: Path | None = None,
    ) -> None:
        """Create an empty registry with optional Runtime-level settings.

        :param catalog_template: Default template for this Runtime, or
            ``None`` when no Runtime-level default is configured.
        :param project_root: Fixed base directory for ``@/`` references.
            ``SkillPlugin.install()`` passes the Runtime's project root. A
            standalone registry without a project root cannot resolve ``@/``
            references; other reference forms use their normal resolution
            rules.
        """
        ...

    def register(self, skill: Skill, *, namespace: str | None = None) -> None:
        """Register a prebuilt Skill, primarily for plugin-supplied Skills.

        This is the programmatic counterpart to file resolution through
        :meth:`get`. The key is ``ns::skill.name``. If ``namespace`` is
        omitted, the key uses ``default::``; bare-name lookup checks
        ``default::`` before ``builtin::``, so the default namespace can
        override a builtin. Skills in other namespaces must be requested by
        their fully qualified key, which is looked up only in the registry.

        Registration rejects a duplicate fully qualified key with
        :class:`flowing.errors.SkillNameConflictError`. It performs no file
        I/O and does not validate the Skill's fields; those are the
        ``Skill`` constructor's responsibility. Registration occurs during
        plugin installation; the registry is not a Runtime-time add/remove
        surface.

        :param skill: A constructed ``Skill`` instance to register.
        :param namespace: Namespace for the key. ``None`` selects
            ``"default"``.

        .. seealso:: :meth:`get`
        """
        ...

    def glob(self, pattern: str) -> list[str]:
        """Return registered fully qualified keys matching a name glob.

        This defines the registry-name matching domain for ``skills:`` glob
        entries and delegates matching to
        :func:`flowing.paths.fnmatch_keys`. A bare pattern matches the bare
        names in the ``default::`` and ``builtin::`` views. A pattern
        containing ``::`` matches complete keys. Results are complete keys
        in stable sorted order; no matches produce an empty list. Matching
        uses the registry contents present when this method is called.
        """
        ...

    def get(self, name: str, source_dir: Path | None = None) -> Skill:
        """Resolve a canonical Skill name, parsing a definition on a cache miss.

        This is the registry's single lookup entry point. Without
        ``source_dir``, a bare name is a registry-only lookup through
        ``default::`` and then ``builtin::``. With ``source_dir``, a bare name
        first uses the directed file lookup chain, and a file definition takes
        precedence over a registered bare-name entry.

        For a name ``<name>``, the first existing candidate wins. Lookup checks
        ``<name>/`` using ``SKILL.fya``, ``skill.fya``,
        ``<name>.skill.fya``, ``<name>.fya``, ``SKILL.md``, and ``skill.md``;
        it then checks sibling files ``<name>.skill.fya``, ``<name>.fya``, and
        ``<name>.md``. If a matching ``.fya``-family file and ``.md`` file
        coexist, the ``.fya`` form wins and a warning is emitted. Generic
        filenames infer the canonical name from their directory. This order
        makes resolution deterministic; placing multiple candidates in one
        lookup chain is discouraged because it obscures which definition is
        selected.

        When a file is found, the registry first derives a namespace from the
        file's containing directory and reuses an existing entry for that
        derived key. If none exists, it parses the file, stores the resulting
        shared ``Skill``, and sets its ``registry_key``. File-derived
        namespaces are project-relative for paths under ``@/`` and absolute
        outside the project root; directory-form definitions derive the
        namespace from the directory above the definition folder. Canonical
        names are inferred using :data:`flowing.plugins.skills.models.SKILL_NAMING`.

        A name containing ``::`` is a fully qualified key: it is looked up
        exactly in the registry and never enters file lookup. The registry
        does not check whether an Agent declared the name; declaration and
        alias rules belong to ``SkillEntry`` and ``use_skill()``.

        .. rubric:: Behavior

        - Returned ``Skill`` objects are shared registry instances. Callers
          must not mutate them.
        - If no candidate file or registry entry exists, this method raises
          :class:`flowing.errors.SkillNotFoundError`. Its ``detail`` field
          includes attempted paths when file lookup was performed.
        - If a file exists but is invalid, its parse error propagates and the
          failed result is not cached. Missing required fields and malformed
          YAML therefore fail rather than being silently skipped.

        :param name: A bare canonical name, a qualified ``ns::name`` key, or
            a path reference.
        :param source_dir: Root for directed file lookup. ``None`` requests a
            registry-only lookup for bare names.
        :return: The shared resolved ``Skill`` instance.
        :raises flowing.errors.SkillNotFoundError: No valid definition is
            found under ``source_dir`` or the registry has no matching entry
            when ``source_dir`` is omitted.
        :raises flowing.errors.FormatError: A found definition is malformed
            or lacks a required field.

        .. seealso:: :class:`flowing.plugins.skills.models.Skill` and
            :func:`flowing.plugins.skills.use_skill`
        """
        ...
