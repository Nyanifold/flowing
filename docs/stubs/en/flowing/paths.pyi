"""Shared vocabulary and path rules for Flowing resource references.

This module centralizes the lexical rules for resource-reference strings used
by ``.fya`` assembly and registry lookup:

* :data:`PATH_PREFIXES` defines the relative prefixes ``./``, ``../``, and
  ``@/``. The same table is used by Parsable ``$`` references and
  ``{% include %}``, registry candidate chains, and
  :meth:`flowing.runtime.Runtime.resolve_path`.
* :func:`classify_ref` classifies a reference as a path, a qualified name
  (``ns::name``), or a bare name. It only describes the string's shape; it does
  not decide where to look it up.
* :func:`resolve_path` converts a path reference to a ``pathlib.Path``, while
  :func:`to_project_path` converts a path back to its public ``@/`` form.
* :func:`probe_candidates` checks a candidate-name sequence in the caller's
  order and returns the first match. Each resource type supplies its own
  candidate names and their order; the probe loop is shared.
* :class:`NamingRules` and :func:`infer_name` derive a canonical identity from
  a file or directory name by removing suffixes, mapping generic names to the
  containing directory, and converting snake case to kebab case.
* The naming helpers convert between kebab, snake, and Pascal case.

``GLOB_META`` is the shared regular expression for recognizing ``*``, ``?``,
and ``[`` as glob metacharacters in resource lists and registry-name patterns.

The module performs string and path operations only. It does not read files,
look up registry entries, or expand globs. Resource-specific candidate chains
and suffix policies remain with their owning modules.

.. rubric:: Behavior notes

- A reference with no ``::`` but containing ``/`` or a backslash is classified
  as a path, even if it has no explicit ``./`` prefix. Classification order is
  fixed: explicit path prefixes and absolute paths first; then values
  containing ``::`` are classified from the left segment (a plain identifier
  is a namespace, while a path-like segment or ``.py`` suffix is a file path,
  such as ``./agents.py::OrderAgent``); then other references containing path
  separators are paths; everything else is a bare name. In particular,
  ``a::b/c`` is qualified because the right segment does not affect
  classification.
- A reference such as ``agents/order-agent`` in an Agent's ``tools:``,
  ``subagents:``, or ``skills:`` list is therefore resolved relative to the
  directory containing that Agent's definition file.
- ``@/`` refers to the Flowing subproject root. :func:`resolve_path` receives
  it explicitly as ``project_root``; at runtime,
  :meth:`flowing.runtime.Runtime.resolve_path` supplies that value. The ``@``
  context is isolated by asyncio Task, so multiple Runtime instances in one
  process do not share the path root.

``PATH_PREFIXES`` is the single source of truth for the relative prefixes used
across the framework. Absolute paths and ``~`` home-directory forms are
recognized separately; the latter are expanded with ``os.path.expanduser``.
Backslashes are normalized to forward slashes before prefix detection.

.. rubric:: Example

.. code-block:: python

    from pathlib import Path
    from flowing.paths import classify_ref, resolve_path, to_project_path

    root = Path("/project")
    classify_ref("payment")              # "bare"; registries handle lookup.
    classify_ref("builtin::web-search")  # "qualified"
    classify_ref("./agents/order-agent") # "path"; resolve against source_dir.
    resolved = resolve_path("@/tools/search.py", project_root=root)
    # resolved == Path("/project/tools/search.py")
    to_project_path(resolved, project_root=root)  # "@/tools/search.py"

.. seealso:: :mod:`flowing.parser` for entry normalization and
    :meth:`flowing.runtime.Runtime.resolve_path` for runtime resolution.
"""

from collections.abc import Iterable
from pathlib import Path
from typing import Literal
import re

__all__ = [
    "PATH_PREFIXES",
    "GLOB_META",
    "NamingRules",
    "classify_ref",
    "fnmatch_keys",
    "resolve_path",
    "to_project_path",
    "probe_candidates",
    "infer_name",
    "kebab_to_snake",
    "snake_to_kebab",
    "kebab_to_pascal",
    "pascal_to_kebab",
    "path_to_module_name",
]

GLOB_META: re.Pattern[str]
"""Shared regular expression matching the glob metacharacters ``*``, ``?``, or
``[``. Resource-list glob detection and registry-key glob matching use this
common vocabulary.
"""

PATH_PREFIXES: tuple[str, ...]
"""Relative path prefixes recognized by Flowing: ``./``, ``../``, and ``@/``.

``./`` and ``../`` are based at the referring source directory; ``@/`` is
based at the project root. Absolute paths are also recognized: POSIX paths
beginning with ``/``, Windows drive paths such as ``C:/x``, and UNC paths
(normalized to a leading ``//``). The ``~`` and ``~/...`` forms are expanded
to the current user's home directory with ``os.path.expanduser`` (``$HOME`` on
POSIX and ``%USERPROFILE%`` on Windows). Backslashes are treated as path
separators during prefix detection. Although home-directory forms are
supported, ordinary references are clearer with ``./``, ``@/``, or an absolute
path.
"""


def fnmatch_keys(pattern: str, keys: Iterable[str]) -> list[str]:
    """Match registry keys with case-sensitive filename-style wildcards.

    A pattern containing ``::`` is matched against each complete key. Without
    ``::``, it is matched only against the unqualified portions of keys in the
    ``default::`` and ``builtin::`` namespaces. Results contain the full keys,
    are sorted, and are empty when nothing matches. A ``*`` can match across
    the ``::`` separator in a qualified pattern. Names are flat strings, so
    ``**`` has the same wildcard behavior as ``*``.

    :param pattern: Bare-name or qualified-key glob pattern.
    :param keys: Registry keys to examine.
    :return: Matching full keys in sorted order.
    """
    ...


def classify_ref(raw: str) -> Literal["path", "qualified", "bare"]:
    """Classify a resource reference as a path, qualified name, or bare name.

    This is lexical classification only; it does not select a registry or
    open a path. Backslashes are treated like forward slashes. Explicit path
    prefixes, home-directory forms, POSIX absolute paths, Windows drive paths,
    and UNC paths are classified as paths first. For references containing ``::``,
    the left segment determines whether the form is ``file.py::Class``
    (path) or ``namespace::name`` (qualified). A slash or backslash without
    ``::`` also indicates a path; all remaining strings are bare names.

    :param raw: Resource reference to classify.
    :return: ``"path"``, ``"qualified"``, or ``"bare"``.

    .. rubric:: Behavior

    - A path prefix takes precedence, so ``./a::b`` remains one path reference.
    - For an unprefixed reference with ``::``, a slash or a ``.py`` suffix on
      the left side makes it a path. Otherwise, the reference is qualified,
      even if its right side contains a slash.
    - Multiple ``::`` separators are not rejected here. This function returns
      ``"qualified"`` based on the left segment and leaves validation and
      lookup to the caller.
    - A bare ``@`` has no path prefix and is classified as a bare name.
    - Bare and qualified names are not checked against a character set here;
      resource assembly performs name validation.

    .. seealso:: :func:`resolve_path` for resolving references classified as paths.
    """
    ...


def resolve_path(
    path: str, *, project_root: Path, source_dir: Path | None = None
) -> Path:
    """Resolve a Flowing resource path against its project or source directory.

    ``@/`` resolves against ``project_root``. ``./``, ``../``, and other
    relative path references containing a separator resolve against
    ``source_dir``; each leading ``../`` moves up one directory, and multiple
    parent segments are applied in order. Absolute POSIX, Windows-drive, and
    UNC paths are returned as paths. ``~`` and ``~/...`` forms are expanded to
    the current user's home directory. Backslashes are normalized to forward
    slashes before these checks.

    :param path: Path reference: a supported prefix, an absolute path, a
        home-directory form, or a relative path containing a separator.
    :param project_root: Base directory for ``@/`` references.
    :param source_dir: Base directory for relative references. It is not
        needed for ``@/`` or absolute paths.
    :return: Resolved ``Path``. The result is not required to remain under
        ``project_root``; absolute paths and parent traversal may leave it.
    :raises ValueError: The reference is relative and ``source_dir`` is
        ``None``.

    .. rubric:: Behavior

    - ``@/`` by itself resolves to ``project_root``.
    - If ``source_dir`` is omitted, a relative path reference raises
      :class:`ValueError`.
    - Resolving a path does not check whether it exists, open it, or expand
      glob patterns.
    - The helper is for Flowing project-resource references, not host-level
      configuration files such as ``providers.yaml``.

    .. seealso:: :func:`to_project_path` for the reverse display conversion.
    """
    ...


def to_project_path(path: Path, *, project_root: Path) -> str:
    """Return a project-relative display form for a path inside the project.

    Paths lexically beneath ``project_root`` use the ``@/`` prefix; the root
    itself is represented as ``@/``. A path outside the root is returned as
    its original absolute-path string. No ``..``-relative form is generated
    for an outside path.

    :param path: Path to display.
    :param project_root: Root used to determine containment and compute the
        relative representation.
    :return: The ``@/`` form for an in-root path, or the original absolute
        path string for a path outside the root.

    .. rubric:: Behavior

    Paths embedded in messages, snapshots, logs, and error text use this
    representation: project content is shown relative to the root, while host
    environment paths are shown as they are. Cross-machine sharing applies
    only to project paths. The function does not resolve symbolic links and
    compares path components as supplied.

    .. seealso:: :func:`resolve_path` for the forward conversion.
    """
    ...


def path_to_module_name(
    path: Path, *, project_root: Path | None, prefix: str
) -> str:
    """Create a deterministic module label from a file path.

    When ``project_root`` is not ``None`` and contains ``path``, the path is
    first represented in the same ``@/`` form used by :func:`to_project_path`;
    otherwise the path string is used as-is. Every run of characters outside
    Unicode-aware ``\\w`` is replaced with an underscore, leading and trailing
    underscores are removed, and ``prefix`` is prepended. Unicode letters,
    including Chinese characters, are retained.

    :param path: Source file path.
    :param project_root: Optional project root used for an in-project relative
        representation. ``None`` uses the path string directly.
    :param prefix: Prefix to add to the generated module label.
    :return: A deterministic module name for the given path and prefix; the
        same path produces the same name across processes.

    .. rubric:: Behavior

    This helper is an internal API, not a stable cross-version contract.
    Cleaning is lossy: for example, ``@/a/b.py`` becomes ``a_b_py``, and
    distinct paths can theoretically produce the same label. The result is a
    human-readable label for ``importlib.spec_from_file_location``-based
    dynamic loading; it is not inserted into ``sys.modules``. A collision is
    therefore diagnostic rather than a public module-identity guarantee.

    .. seealso:: :func:`to_project_path` for the path representation used
        when a project root is supplied.
    """
    ...


def probe_candidates(base_dir: Path, candidates: Iterable[str]) -> Path | None:
    """Return the first existing path in a caller-ordered candidate sequence.

    Each candidate is joined to ``base_dir`` and checked in the order given.
    The candidate names and their priority are supplied by the caller.

    :param base_dir: Directory against which candidate names are resolved.
    :param candidates: Relative candidate names, ordered from highest to
        lowest priority.
    :return: The ``base_dir / candidate`` path for the first candidate whose
        joined path satisfies ``Path.exists()``, or ``None`` if none exists.

    The function does not distinguish files from directories and does not
    warn when different resource forms coexist.
    """
    ...


class NamingRules:
    """Rules used by :func:`infer_name` to derive a resource identity from a path.

    The immutable class stores generic basenames and an ordered list of
    suffixes; it does not perform inference itself. Resource-specific tables
    are defined beside their candidate chains, including ``AGENT_NAMING``,
    ``TOOL_NAMING``, and ``SKILL_NAMING``.

    .. rubric:: Example

    .. code-block:: python

        from flowing.paths import NamingRules

        TOOL_NAMING = NamingRules(
            suffixes=(".tool.fya", ".fya", ".py"),
            generic_names=frozenset({"TOOL.fya", "TOOL.py", "tool.fya", "tool.py"}),
        )
    """

    suffixes: tuple[str, ...]
    """Suffixes to try in order; the first matching suffix is removed."""
    generic_names: frozenset[str]
    """Exact basenames whose identity is taken from the containing directory."""

    def __init__(
        self, suffixes: tuple[str, ...], generic_names: frozenset[str]
    ) -> None: ...


def infer_name(path: str | Path, *, naming: NamingRules) -> str:
    """Infer a canonical kebab-style identity name from a file or directory path.

    An exact basename match in ``naming.generic_names`` uses the parent
    directory name. Otherwise, the first matching suffix in
    ``naming.suffixes`` is removed. Underscores in the remaining stem become
    hyphens, and the result must satisfy Flowing's kebab-case identity-name
    format. For example, ``agents/order-agent/agent.fya`` can infer
    ``order-agent``, while ``pay_agent.py`` can infer ``pay-agent``. A directory
    path can be passed directly; its basename is used without suffix removal.

    :param path: File or directory path whose basename supplies the name.
    :param naming: Resource-specific generic basenames and ordered suffixes.
    :return: The inferred identity name.
    :raises flowing.errors.FormatError: The resulting stem is empty or does
        not match Flowing's identity-name format.

    The function does not check whether ``path`` exists or classify the
    reference form. The caller should classify a reference first, and an
    explicit ``as`` alias takes precedence over inferred names.

    .. seealso:: :class:`NamingRules` and :func:`classify_ref`.
    """
    ...


def kebab_to_snake(name: str) -> str:
    """Convert kebab-case to snake_case (for example, ``payment-agent`` to
    ``payment_agent``) by replacing hyphens with underscores.

    :param name: A kebab-case name.
    :return: The same name with hyphens replaced by underscores.
    :raises flowing.errors.FormatError: ``name`` is not valid kebab-case
        (lowercase letters and digits separated by single hyphens).

    .. rubric:: Behavior

    The conversion is a mechanical replacement of ``-`` with ``_`` and applies
    no other rule.

    .. seealso:: :func:`snake_to_kebab` for the reverse conversion.
    """
    ...


def snake_to_kebab(name: str) -> str:
    """Convert snake_case to kebab-case (for example, ``payment_agent`` to
    ``payment-agent``) by replacing underscores with hyphens.

    :param name: Lowercase letters and digits separated by single underscores.
    :return: The same name with underscores replaced by hyphens.
    :raises flowing.errors.FormatError: ``name`` is not valid lowercase snake case.

    .. rubric:: Behavior

    The conversion is a mechanical replacement of ``_`` with ``-`` and applies
    no other rule.

    .. seealso:: :func:`kebab_to_snake` for the reverse conversion.
    """
    ...


def kebab_to_pascal(name: str) -> str:
    """Convert a valid kebab-style name to PascalCase.

    Each hyphen-separated segment is capitalized and concatenated. This is a
    format conversion only; resource-specific suffix rules are applied by the
    caller. For example, the compiler may add an ``Agent`` class-name suffix
    after this conversion; that suffixing is not performed here.

    :param name: A kebab-case name.
    :return: The PascalCase form.
    :raises flowing.errors.FormatError: ``name`` is not valid kebab-case; the
        validation rule is the same as for :func:`kebab_to_snake`.

    .. seealso:: :func:`pascal_to_kebab` for the reverse conversion.
    """
    ...


def pascal_to_kebab(name: str) -> str:
    """Convert a PascalCase name to kebab case, preserving acronym boundaries.

    A boundary is inserted between a lowercase letter or digit and an
    uppercase letter, and before the final uppercase letter when an acronym
    is followed by a capitalized word. For example, ``HTTPClient`` becomes
    ``http-client`` and ``PayAgent`` becomes ``pay-agent``. The result is
    lowercased.

    :param name: An ASCII PascalCase name beginning with an uppercase letter
        and containing no hyphens, underscores, or whitespace.
    :return: The corresponding kebab-case name.
    :raises flowing.errors.FormatError: ``name`` is not valid PascalCase.

    .. rubric:: Behavior

    This is a format conversion only; it does not remove resource suffixes.
    Thus, converting ``PayAgent`` retains the ``agent`` segment in
    ``pay-agent``. Suffix semantics belong to the resource layer.

    .. seealso:: :func:`kebab_to_pascal` for the reverse conversion.
    """
    ...
