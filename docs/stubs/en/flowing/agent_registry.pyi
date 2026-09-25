"""Runtime-wide registry and resolver for Agent types keyed by ``ns::name``.

This core module provides the Agent type registry and the path-based identity
rules in :data:`AGENT_NAMING`. Each Runtime owns one :class:`AgentRegistry` at
``runtime.agent_registry``. It is the sole authority for Agent type
registration and resolution, corresponding to :class:`flowing.tool.ToolRegistry`
and :class:`flowing.plugins.skills.registry.SkillRegistry`. The framework
does not scan directories at startup.

Registration follows this lifecycle: builtin types are registered during
``Runtime.__init__``, plugin types are registered during phase-one
``install()``, and file-based types are resolved and registered lazily when
referenced. Laziness has two stages: a parent Agent instance can first record
the reference metadata, and the class is loaded only when the child is created
or invoked.

``AGENT_NAMING`` defines how Agent resource paths determine identity names.

.. seealso::

   :class:`flowing.tool.ToolRegistry` is the corresponding Tool registry.
   :class:`flowing.plugins.skills.registry.SkillRegistry` follows the same
   three-registry pattern.
   :meth:`flowing.runtime.Runtime.get_agent_class` is the public resolution
   entry point that delegates to this registry.
   :meth:`flowing.runtime.Runtime.register_agent_type` is the public
   registration entry point that delegates to this registry.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from flowing.paths import NamingRules

if TYPE_CHECKING:
    from flowing.agent import Agent

AGENT_NAMING: NamingRules
"""Naming rules for inferring Agent identity names from resource paths.

The recognized suffixes are ``.agent.fya``, ``.fya``, and ``.py``. For the
generic filenames ``agent.fya`` and ``AGENT.fya``, the identity is the
containing directory's name. For other filenames, the first matching suffix
is removed and the remaining stem is converted from snake case to kebab case.
The Agent assembly layer passes these rules to :func:`flowing.parser.parse_fya`
for path-form resource entries and uses them when checking inferred names.
``flowing.runtime.AGENT_NAMING`` is a compatibility re-export of this
constant.
"""


def _agent_glob_accept(path: Path) -> bool:
    """Filter paths matched by a ``subagents:`` glob; this is an internal API.

    This performs name-and-shape checks only; it does not inspect whether a
    candidate is semantically a valid Agent resource.

    * A directory is accepted only if it contains an entry from the declarative
      candidate chain ``AGENT.fya`` → ``agent.fya`` → ``<name>.agent.fya`` →
      ``<name>.fya``. Directories without an entry are skipped as unrelated.
    * Files explicitly marked for another resource type, including
      ``*.tool.fya``, are rejected. The explicitly marked Agent form is
      ``*.agent.fya``.
    * Other ``.fya`` files, including bare or generic names, and ``.py`` files
      are accepted for eager parsing during Agent creation. Invalid resources
      fail at that stage.
    * Files with other suffixes, such as ``.md``, are rejected.

    Generic-name files are intended for directory-shaped resources; a glob
    matching such a resource normally matches the directory itself.
    """
    ...


class AgentRegistry:
    """Runtime-wide Agent type registry keyed by ``ns::name``.

    Each Runtime owns one instance at ``runtime.agent_registry``. A fully
    qualified key is unique, while the same name may exist in different
    namespaces. Bare-name lookup exposes the ``default::`` view before the
    ``builtin::`` view, so a default registration can override a builtin for
    bare-name references. Types in custom namespaces require fully qualified
    references.

    Identity names are inferred from the resource path, registration name, or
    class ``__name__``. A class-body ``name`` is only a consistency assertion;
    a mismatch raises :class:`flowing.errors.NameMismatchError`.

    .. rubric:: Example

    .. code-block:: python

        from flowing import Agent, Runtime

        class PaymentAgent(Agent):
            ...

        # This project entry point is called by flowing.launch().
        async def main() -> Runtime:
            runtime = Runtime()
            runtime.agent_registry.register(PaymentAgent)
            cls = runtime.agent_registry.get("payment-agent")
            "default::payment-agent" in runtime.agent_registry
            return runtime

    .. rubric:: Behavior

    * At most one Agent class is stored under any fully qualified key.
    * The registry has no ``unregister`` operation; its contents share the
      Runtime's lifetime.

    .. seealso::

       :attr:`flowing.runtime.Runtime.agent_registry` is the owning Runtime's
       registry attribute.
       :class:`flowing.tool.ToolRegistry` follows the same registry pattern.
    """

    _agents: dict[str, type[Agent]]
    """Map fully qualified ``ns::name`` keys to Agent classes.

    This is an internal implementation detail, not a stable API. See
    :meth:`get` for the namespace and lookup rules.
    """

    def __init__(self, *, project_root: Path | None = None) -> None:
        ...

    def register(
        self,
        agent_class: type[Agent],
        *,
        name: str | None = None,
        namespace: str | None = None,
    ) -> None:
        """Register an Agent class under a fully qualified key.

        The key is ``<namespace>::<name>``. If ``name`` is omitted, it is
        inferred from ``agent_class.__name__`` by converting PascalCase to
        kebab case; a class name that is not valid PascalCase raises
        :class:`flowing.errors.FormatError`. If ``namespace`` is omitted, the
        namespace is ``default``. Builtin types use ``builtin``; custom
        namespaces can be resolved only by fully qualified name.

        If the class body declares ``name``, it must equal the explicit or
        inferred registration name. This attribute is a consistency assertion,
        not a mechanism for choosing the registry name.

        :param agent_class: An Agent subclass to register.
        :param name: An optional registration name; otherwise it is inferred
            from the class name.
        :param namespace: An optional namespace. The documented default is
            ``default``.
        :raises flowing.errors.AgentTypeConflictError: The complete key,
            including its namespace, is already registered. The same name in a
            different namespace is allowed.
        :raises flowing.errors.NameMismatchError: The class-body ``name`` does
            not match the explicit or inferred registration name.
        :raises flowing.errors.FormatError: The class name cannot be converted
            from valid PascalCase when no explicit ``name`` is supplied.
        """
        ...

    def glob(self, pattern: str) -> list[str]:
        """Return registered keys matching a registry-name glob.

        A pattern without ``::`` matches the bare-name portions of the
        ``default::`` and ``builtin::`` views. A pattern containing ``::`` is
        matched against complete keys. Results are complete keys in sorted,
        stable order; no matches produce an empty list.

        Matching uses the registry contents at call time. File-based Agent
        types that have not yet been referenced and registered are absent from
        this result; path globs in ``subagents:`` cover those resources.
        """
        ...

    def get(
        self,
        agent_type: str,
        *,
        source_dir: Path | None = None,
    ) -> type[Agent]:
        """Resolve an Agent type from a registered name or a file reference.

        Reference syntax is classified by :func:`flowing.paths.classify_ref`.

        The three reference forms use different lookup routes:

        * **Qualified name** (``namespace::name``) checks the exact registry
          key only. It does not search files and does not fall back to another
          namespace.
        * **Bare name** (for example, ``payment-agent``) first searches the
          file chain relative to ``source_dir`` when one is provided. A found
          file takes precedence over a registry entry. It then checks
          ``default::name`` followed by ``builtin::name``. Without
          ``source_dir``, the file chain is skipped. A custom namespace is not
          part of this bare-name view. Call
          :meth:`flowing.agent.Agent.get_agent_class` when resolution should
          automatically use an Agent's ``source_dir``.
        * **Path reference** (``./``, ``../``, ``@/``, an absolute path, or
          ``path::ClassName``) is resolved to a declarative ``.fya`` resource
          or a handwritten ``.py`` file. ``@/`` is anchored to
          ``project_root`` and needs no ``source_dir``; ``./`` and ``../``
          references require ``source_dir``. The ``path::ClassName`` form
          disambiguates a handwritten Python module with multiple Agent
          subclasses. Globs are not expanded here: the assembly layer expands
          ``subagents:`` globs, filters their matches with
          :func:`_agent_glob_accept`, and passes each match to this method.

        For a directory-shaped path, the first existing declarative entry in
        this order is selected:

        ``AGENT.fya`` → ``agent.fya`` → ``<name>.agent.fya`` → ``<name>.fya``

        For a bare name with ``source_dir``, the directory-shaped chain above
        is tried first. If that directory exists but has no candidate, lookup
        continues with the flat-file chain relative to ``source_dir``:

        ``<name>.agent.fya`` → ``<name>.fya`` → ``<name_snake>.py``

        Thus, a valid entry inside a same-named directory wins over flat-file
        candidates. Within the flat-file chain, a matching ``.fya`` candidate
        wins over the handwritten ``.py`` candidate and emits a warning when
        both exist. Candidate order is a deterministic ambiguity rule, not a
        recommendation to place multiple candidates in the same chain.
        If none is found, lookup falls through to the bare-name registry view.
        By contrast, an explicitly referenced directory with no valid entry
        fails immediately with :class:`flowing.errors.AgentTypeNotFoundError`.

        A directory candidate chain contains only ``.fya`` files; handwritten
        Agent classes in directories use ordinary Python package organization
        and :meth:`register`. For a handwritten ``.py`` file, the module must
        define exactly one Agent subclass of its own unless
        ``path::ClassName`` selects one explicitly. Imported Agent subclasses
        do not count toward that total. A ``::ClassName`` suffix on a ``.fya``
        resource does not select among classes; it asserts the one synthesized
        class's name.

        File-derived identities are inferred from their resource paths. Their
        namespaces are derived from the containing directory: paths under the
        project root use a project-relative namespace, while paths outside it
        use an absolute namespace. A class-body ``name`` remains only a
        consistency assertion. File-based resolution is lazy: an Agent may
        record the reference first and load the class only when it is created
        or invoked. The ``class_name`` inference rules are described by
        :class:`flowing.agent.Agent`.

        :param agent_type: A qualified name, bare name, or path reference.
        :param source_dir: The base directory for bare-name lookup and
            ``./`` / ``../`` path references.
        :return: The resolved Agent class.
        :raises flowing.errors.AgentTypeNotFoundError: No matching registry
            entry or file candidate can be resolved.
        :raises flowing.errors.FormatError: A handwritten Python module does
            not define exactly one local Agent subclass and no class selector
            was given, or a ``.fya`` class-name assertion does not match its
            synthesized class.
        :raises flowing.errors.NameMismatchError: A declared class ``name``
            does not match the identity inferred from the registration name or
            resource path.
        :raises ValueError: An ``@/`` reference is used by a registry without
            a project-root context.

        .. seealso::

           :meth:`flowing.runtime.Runtime.get_agent_class` is the public
           resolver that delegates to this method.
           :meth:`flowing.tool.ToolRegistry.get` follows the corresponding
           Tool resolution pattern.
        """
        ...

    def __contains__(self, key: str) -> bool:
        """Check whether an exact fully qualified key is registered.

        This is a registry-only existence check. It accepts keys such as
        ``default::payment-agent`` and does not inspect or load file resources;
        bare names are not resolved through the ``default`` / ``builtin`` view.
        """
        ...

    def _load_agent_from_name_chain(
        self, name: str, source_dir: Path
    ) -> type[Agent] | None:
        """Search the file candidate chain for a bare name; this is internal.

        The search is relative to ``source_dir``. It first probes a directory
        named ``<name>`` for ``AGENT.fya`` → ``agent.fya`` →
        ``<name>.agent.fya`` → ``<name>.fya``. It then checks the flat-file
        chain ``<name>.agent.fya`` → ``<name>.fya`` → ``<name_snake>.py``.
        The first existing candidate wins. If the directory exists but has no
        entry, the flat-file chain is still tried.

        Declarative files are compiled and registered. If a matching
        handwritten Python file coexists with a selected ``.fya`` file, the
        ``.fya`` file wins and a warning is emitted. Return ``None`` when no
        file matches so the caller can check the bare-name registry view.
        """
        ...

    def _load_agent_from_path(
        self, resolved: Path, class_name: str | None, *, ref: str
    ) -> type[Agent]:
        """Compile or load a resolved path; this is an internal API.

        Directories use the declarative candidate chain and raise
        :class:`flowing.errors.AgentTypeNotFoundError` if it has no entry.
        ``.fya`` files are compiled and assembled; handwritten ``.py`` files
        are loaded. Missing paths and paths with other suffixes also raise
        :class:`flowing.errors.AgentTypeNotFoundError`.
        """
        ...

    def _load_agent_from_fya(
        self,
        path: Path,
        class_name: str | None = None,
        *,
        ref: str,
    ) -> type[Agent]:
        """Compile a declarative Agent file and register its derived type.

        Compilation synthesizes exactly one Agent subclass. Its identity name
        is inferred from the resource path, with generic filenames taking the
        containing directory's name. The derived registry key uses the
        resource's derived namespace plus that identity name. Once registered,
        a later lookup reuses the same class rather than compiling it again.

        An optional ``class_name`` is a consistency assertion against the
        synthesized class name; it cannot select another class. A mismatch
        raises :class:`flowing.errors.FormatError`. A declared ``name`` that
        disagrees with the inferred identity raises
        :class:`flowing.errors.NameMismatchError`.
        """
        ...

    def _load_agent_from_py(
        self, path: Path, class_name: str | None, *, ref: str
    ) -> type[Agent]:
        """Load a handwritten Agent module and register its derived type.

        Without ``class_name``, the module must define exactly one Agent
        subclass of its own; imported subclasses are excluded. No local
        subclass or multiple local subclasses raises
        :class:`flowing.errors.FormatError`. With ``class_name``, lookup uses
        the named attribute and bypasses the exactly-one rule.

        The derived namespace comes from the containing directory, using a
        project-relative form for paths under ``project_root`` and an absolute
        form for paths outside it. The identity name is inferred from the
        filename. A declared class ``name`` that differs from this identity
        raises :class:`flowing.errors.NameMismatchError`.

        The derived key is cached in the registry. For a multi-class module,
        each explicitly selected class has its own key so that loading one
        class cannot cause a later request for another class to return it.
        """
        ...

    def _derived_namespace(self, ns_dir: Path) -> str:
        """Return the internal namespace derived from a resource directory.

        With ``project_root``, use the path relative to the project root when
        possible and the absolute path otherwise. Without a project root, use
        the absolute path. This is an internal identity mechanism, not a stable
        API.
        """
        ...
