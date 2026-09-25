"""``flowing.tool.registry`` — ``ToolRegistry`` and ``.fya`` / ``.py`` file loading.

This is the sole authority for tool registration and resolution, parallel in
structure to ``AgentRegistry`` and ``SkillRegistry``. Public symbols are
re-exported from ``flowing.tool``.
"""
from collections.abc import Iterator
from typing import Any
from pathlib import Path
from flowing.tool.core import Tool

def _dir_candidates(name: str) -> list[str]: ...
def _tool_glob_accept(path: Path) -> bool: ...
def _tool_from_fya(path: Path, identity: str, *, project_root: Path | None = None) -> Tool: ...
def _script_tool_from_fya(path: Path, identity: str, fields: dict[str, Any], params: dict[str, dict[str, Any]], *, project_root: Path | None = None) -> Tool: ...

class ToolRegistry:
    """The Runtime-wide registry that maps ``ns::name`` keys to tool instances.

    .. rubric:: Overview

    Each Runtime owns one registry at ``runtime.tool_registry``. The framework
    does not scan directories at startup. Core tools such as ``finish`` and
    ``subagent-invoke`` are registered during ``Runtime.__init__``; plugin
    tools are registered during ``install()``; a file-based tool is resolved
    and registered when a reference requests it; and MCP, CLI, and Request
    instances are created and registered while an Agent's ``.fya`` is
    assembled.

    Duplicate detection uses the fully qualified ``ns::name`` key. Registering
    the same key twice raises :class:`flowing.errors.ToolNameConflictError`,
    while tools with the same name in different namespaces can coexist. Use a
    different namespace or canonical name for separately configured MCP
    instances; reuse an existing registration when the same instance is
    intended. Bare-name lookup checks ``default::`` before ``builtin::`` so
    plugins can override a builtin. A custom namespace must be referenced by
    its fully qualified ``ns::name`` key. Aliases do not belong to the
    registry; they are local to an Agent's :class:`flowing.tool.ToolEntry`.

    .. rubric:: Example

    .. code-block:: python

        runtime.tool_registry.register(MakePayment())
        tool = runtime.tool_registry.get("make-payment")

    .. rubric:: Behavior

    - At most one tool instance is stored for any fully qualified key. The
      same name may be registered in different namespaces.
    - The registry does not resolve Agent-local aliases and does not provide
      an unregister operation. Registered instances live for the Runtime's
      lifetime.

    .. seealso::

        - :attr:`flowing.runtime.Runtime.tool_registry` for the Runtime entry
          point.
        - :class:`flowing.tool.ToolEntry` for lookup by ``name_ori``.
    """
    _tools: dict[str, Tool]
    def __init__(self, *, project_root: Path | None = None) -> None: ...

    def register(self, tool: Tool, *, name: str | None = None, namespace: str | None = None) -> None:
        """Register a tool instance under a fully qualified key.

        :param tool: The tool instance to register. A script tool should be
            registered as a Runtime-wide singleton.
        :param name: An override for the canonical name. ``None`` uses
            ``tool.definition.name``.
        :param namespace: The namespace. ``None`` selects ``default``. The
            registry key has the form ``ns::name``. Core builtins use
            ``builtin::``; bare-name lookup checks ``default::`` before
            ``builtin::``, allowing plugins to override builtins. A custom
            namespace must be referenced with its fully qualified name.
        :raises flowing.errors.ToolNameConflictError: The complete
            ``ns::name`` key is already registered. The same name in another
            namespace is allowed.
        """
        ...

    def get(self, name_or_path: str, *, source_dir: Path | None = None) -> Tool:
        """Resolve a tool reference through the registry and, when applicable, file lookup.

        Reference classification is delegated to
        :func:`flowing.paths.classify_ref`. The three reference forms resolve
        as follows:

        - A qualified name containing ``::`` (for example,
          ``myplugin::web-search``) performs an exact registry lookup. It does
          not search files because a namespace cannot be mapped back to a file.
        - A bare name (for example, ``payment``) first searches the directed
          file chain relative to ``source_dir`` when that directory is
          provided. A file match takes precedence over a registry match. The
          path-derived key reuses an already registered instance; otherwise the
          file is loaded and the instance is registered. With no
          ``source_dir``, file lookup is skipped. Registry lookup then checks
          the bare-name view in ``default::`` before ``builtin::``.
        - A path reference beginning with ``@/`` is anchored to
          ``project_root`` and does not require ``source_dir``. Without a
          launch context to anchor ``@/``, resolution raises ``ValueError``.
          ``./`` and ``../`` paths require ``source_dir``. Path references
          skip the bare-name registry view, resolve a candidate file, and
          register the result under a namespace derived from its location. A
          directory-form resource uses its parent directory for this
          namespace; paths under the project root use a root-relative
          namespace, while paths outside it use an absolute-path namespace.
          These keys serve as internal identity markers.

        For a name ``<name>``, where ``<name_snake>`` is its snake-case form,
        the directed candidate order is:

        .. code-block:: text

            Inside an existing <name>/ directory:
                TOOL.fya > <name>.tool.fya > <name>.fya
                > TOOL.py > tool.py > <name_snake>.py
            Outside that directory:
                <name>.tool.fya > <name>.fya > <name_snake>.py

        The first existing candidate wins. Multiple candidates in one chain
        are discouraged because the selected file depends on their precedence.

        .. rubric:: Behavior

        - The argument is a canonical name, qualified name, or path, not an
          Agent-local alias. Alias conversion belongs to ``ToolEntry``.
        - During bare-name lookup, a directory without a valid entry advances
          to the next candidate. An explicitly named path whose directory has
          no candidate raises an error because an empty, specifically named
          directory is likely a mistake.
        - If a bare name is not registered and ``source_dir`` is present, the
          file chain is checked before lookup fails. Without ``source_dir``,
          only the registry is checked. Use ``__contains__`` to test existence
          of a fully qualified key.
        - Once an Agent entry has been assembled, its tool is already
          registered. Calls from ``ToolEntry.llm_definition()`` and tool-call
          approval handlers use the registry fast path and do not perform file
          I/O at runtime.
        - MCP synthesized names of the form
          ``<declaration-name>--<server-tool-name>`` resolve through the
          registry after declaration-time expansion. ``.fya`` assembly does
          this automatically; programmatic callers must call
          ``expand_mcp`` first.
        - If a ``.fya`` file and a same-named ``.py`` file both match, the
          registry warns and prefers the ``.fya`` file.
        - A ``.py`` file must contain exactly one ``@flowing_tool`` function
          or one ``ScriptTool`` subclass. More than one definition, or both
          forms together, raises :class:`flowing.errors.AmbiguousToolError`.
          A file with neither form raises :class:`flowing.errors.FormatError`.
        - An explicit ``name`` in a ``.fya`` field, class attribute, or
          decorator argument must match the inferred ``<name>``. A mismatch
          raises :class:`flowing.errors.NameMismatchError`. Name inference is
          provided by :func:`flowing.paths.infer_name` using
          :data:`TOOL_NAMING`.
        - This method does not expand globs. The assembly layer expands globs
          in ``tools:`` entries, filters names, and then calls this method for
          each match.

        :param name_or_path: A bare canonical name, a qualified ``ns::name``,
            or a path-form reference.
        :param source_dir: The base directory for bare-name file lookup and
            relative ``./`` or ``../`` paths. If omitted, bare names only use
            the ``default::`` and ``builtin::`` registry views, and relative
            paths are rejected. Declaration-time callers should pass the
            referencing Agent's source directory; :meth:`flowing.agent.Agent.get_tool`
            supplies it automatically.
        :return: The resolved and registered tool instance.
        :raises flowing.errors.ToolNotFoundError: Neither the registry nor the
            file lookup chain finds a tool.
        :raises flowing.errors.FormatError: An explicit directory has no
            candidate, a ``.py`` file has no valid tool definition, or a
            ``callable:`` reference targets an already decorated function.
        :raises ValueError: An ``@/`` path cannot be anchored because there is
            no launch context.

        .. seealso::

            - :func:`flowing.tool.flowing_tool` for the function-marking path.
            - :meth:`flowing.runtime.Runtime.get_agent_class` for the analogous
              Agent resolution pipeline.
        """
        ...

    async def _expand_group(self, group: object) -> list[str]: ...

    def glob(self, pattern: str) -> list[str]:
        """Return registered keys that match a tool-name glob.

        This is the matching domain used by ``tools:`` entries. It delegates
        to :func:`flowing.paths.fnmatch_keys`. A bare-name pattern matches the
        bare-name portions of ``default::`` and ``builtin::`` entries. A
        qualified pattern containing ``::`` matches full keys. The result
        contains fully qualified keys in stable sorted order, or an empty list
        if nothing matches. Matching uses the registry state at call time, so
        MCP synthesized names must already have been expanded.
        """
        ...

    async def expand_mcp(self, name_or_pattern: str, *, source_dir: Path | None = None) -> bool:
        """Expand an MCP group declaration before resolving its synthesized tool names.

        A synthesized name has the form
        ``<declaration-name>--<server-tool-name>``; ``--`` separates the group
        from the server tool. If synchronous resolution of a bare name or
        group pattern such as ``demo--*`` does not find a match, this method
        splits at the first ``--`` and searches for the group declaration. It
        checks the file anchor first, using the same file-before-registry rule
        as :meth:`get`, then checks for a ``default::<group-name>`` MCP group
        in the registry. If found, it awaits ``group.list_tools()`` and
        registers the resulting proxies in ``default::`` under synthesized
        names. A key conflict raises :class:`flowing.errors.ToolNameConflictError`.
        The expanded names can then be resolved by :meth:`get`.

        ``get`` remains synchronous for runtime use. This method is the
        declaration-time asynchronous step: ``.fya`` assembly calls it, while
        programmatic callers must call it before using a synthesized name.
        Expansion is idempotent; an already expanded group is not connected to
        again.

        :param name_or_pattern: A bare synthesized name or group pattern.
            Qualified names and paths return ``False``. A value without
            ``--``, or a group segment containing pattern characters that
            prevent locating a group, also returns ``False``.
        :param source_dir: The base for bare-name file lookup, with the same
            meaning as in :meth:`get`.
        :return: ``True`` if the group was expanded or already registered and
            the target can be resolved. ``False`` if expansion is unnecessary
            or impossible; the caller's normal resolution path reports any
            resulting lookup failure.
        :raises flowing.errors.ToolNameConflictError: An expanded synthesized
            name conflicts with an existing registry entry.
        """
        ...

    def _probe_name_chain(self, name: str, source_dir: Path) -> tuple[Path, bool, Path, list[str]] | None: ...
    def _resolve_hit(self, hit: Path, folder_form: bool, base_dir: Path, candidates: list[str], *, ref: str) -> Tool: ...
    def _derived_namespace(self, ns_dir: Path) -> str: ...
    def _tool_from_py(self, path: Path, identity: str) -> Tool: ...

    def get_tool_class(self, name_or_path: str, *, source_dir: Path | None = None) -> type[Tool]:
        """Return the class of the tool resolved by :meth:`get`.

        This is a thin ``type(self.get(...))`` delegation, not a separate
        resolution path. A decorated script function returns the
        ``ScriptTool`` subclass generated for it. Agent resolution returns a
        class, while tool registration stores a singleton instance; callers
        such as compilers, subclass extensions, and ``issubclass`` checks need
        the class. Delegation keeps these class and instance views aligned.

        .. rubric:: Behavior

        - Resolution, registration, and caching follow :meth:`get`, including
          qualified-name lookup, namespace derivation, and fail-fast errors.
        - This method does not load files around the registry and does not
          instantiate another object. It returns the class of the registered
          singleton.

        :param name_or_path: The same reference forms accepted by :meth:`get`.
        :param source_dir: The same base directory used by :meth:`get`.
        :return: The class of the registered tool instance.
        :raises flowing.errors.ToolNotFoundError: The reference cannot be
            resolved, as for :meth:`get`.

        .. seealso:: :meth:`get` for the resolution path and
            :meth:`flowing.runtime.Runtime.get_agent_class` for the analogous
            Agent-side method.
        """
        ...

    def __contains__(self, name: str) -> bool:
        """Return whether a fully qualified registry key is present.

        The ``in`` operator checks only exact keys such as
        ``"builtin::read"``. A bare name such as ``"read"`` is always false,
        even when a builtin with that name is registered. Bare-name namespace
        resolution belongs exclusively to :meth:`get`; keeping membership
        checks exact avoids hiding lookup behavior behind a boolean result.

        .. rubric:: Example

        .. code-block:: python

            "builtin::read" in runtime.tool_registry  # True
            "read" in runtime.tool_registry           # False
            runtime.tool_registry.get("read")          # Resolves by namespace priority.
        """
        ...

    def __iter__(self) -> Iterator[Tool]:
        """Iterate over all registered tool instances; iteration order is not guaranteed."""
        ...
