"""Parse declarative ``.fya`` text into a structured declaration document.

This module is the lexical front end in the three-stage ``.fya`` pipeline:
lexical parsing, assembly, and class synthesis. It converts text into a
:class:`FyaDocument` (or the intermediate :class:`RawFya`) and only performs
text-to-structure conversion. A :class:`FyaDocument` has three parts:

* ``fields`` is the top-level YAML mapping. Scalar ``_`` values have become
  :data:`flowing.parsable.PENDING`, and resource-list fields named by
  ``entry_fields`` have become ``list[EntryRef]``.
* ``blocks`` maps each named block's dotted path to its original body; the
  path is introduced in the source by a header such as ``$system_prompt:``.
  The parser does not merge blocks into ``fields``; merging is an assembly-
  layer responsibility.
* ``script`` contains the original body of the optional ``$script`` block.

The parser is resource-neutral and defines no keywords such as ``agent``,
``tool``, or ``skill``. Callers identify resource-list fields through
``entry_fields`` and supply :class:`flowing.paths.NamingRules` when path-form
entries need an inferred alias.

.. rubric:: Lexical rules

* A document consists of a top-level YAML section followed by zero or more
  blocks. A separator is a line containing only ``---``; the following line
  must be a block header of the form ``$<dotted.path>:``. Each path segment
  contains only ASCII letters, digits, underscores, and hyphens; list indices
  are not supported.
* ``$script`` is reserved. Its body is returned in ``script`` and is not
  included in ``blocks``.
* A YAML scalar whose string value is exactly ``_`` maps recursively to
  :data:`flowing.parsable.PENDING`. Other strings, such as ``$"_"``, remain
  unchanged, and non-string YAML values retain their native types. Wrapping
  values in ``Parsable`` belongs to the assembly layer.

.. rubric:: Boundaries

* The parser does not merge named blocks; block insertion and navigation rules
  belong to the assembly layer.
* It does not resolve references. :class:`EntryRef` retains the original
  reference text; reference classification is performed by
  :func:`flowing.paths.classify_ref`, and actual lookup is performed by the
  relevant registry.
* It does not recognize resource-specific field names such as ``tools`` or
  ``args``. The caller declares which fields are resource lists.

.. rubric:: Example

.. code-block:: python

    from flowing.parser import parse_fya

    document = parse_fya(
        "tools:\\n  - ./tools/review as reviewer\\n",
        entry_fields={"tools", "subagents"},
    )

.. seealso::

    * :mod:`flowing.paths` defines reference forms and naming rules.
    * :mod:`flowing.compiler` performs class synthesis and shares this parser.
    * :data:`flowing.parsable.PENDING` is the unset-value sentinel.
"""

from collections.abc import Collection, Mapping
from typing import Any

from flowing.paths import NamingRules

__all__ = [
    "EntryRef",
    "FyaDocument",
    "RawFya",
    "parse_fya",
    "split_fya",
    "normalize_entries",
    "split_as",
    "load_fya_yaml",
]


class EntryRef:
    """Literal normalized result for one entry in a declarative resource list.

    This is the common representation of items in ``tools:``, ``subagents:``,
    and ``skills:`` lists. It retains the reference text without resolving it,
    settles the alias, and holds an optional mapping body as-is. It records
    only three literal facts: what the entry refers to, what it is called, and
    any attached mapping body. Resolving the target through a registry lookup
    or candidate-path search belongs to a later layer.

    .. rubric:: Example

    The entry ``- ./payment as pay: {args: [...]}`` produces:

    .. code-block:: python

        EntryRef(raw="./payment", alias="pay", body={"args": [...]})

    .. rubric:: Behavior notes

    - This module never parses ``raw``. Path separators such as ``::`` inside
      a path and namespace forms such as ``ns::name`` are retained verbatim;
      consumers classify them with :func:`flowing.paths.classify_ref` when
      needed.
    - ``alias`` is always populated after normalization. It is the sole key
      used by later matching, including deep block navigation in the assembly
      layer, conflict checks, and names exposed to the LLM. See
      :func:`normalize_entries` for the inference order.

    .. seealso:: :func:`normalize_entries` is the only construction path.
    """

    raw: str
    """Reference text before ``as`` processing, with its path or namespace syntax intact."""
    alias: str
    """Final alias: explicit ``as`` value, qualified-name suffix, bare name,
    or a name inferred from a path-shaped reference."""
    body: Mapping[str, Any]
    """The value of a single-key mapping entry (for example, ``{args: [...]}``);
    string entries have an empty mapping. The body is opaque to this module.
    The neutral term “mapping body” does not imply override semantics; each
    resource assembly layer or consumer, such as ``Agent.add_tool``, defines
    how to interpret it."""


class RawFya:
    """Intermediate result from :func:`split_fya`, before YAML is loaded.

    This type is public for debugging and tests. Normal callers should use
    :func:`parse_fya` to run the complete parse pipeline.
    """

    yaml_text: str
    """Original top-level YAML text, or an empty string if there is none."""
    blocks: dict[str, str]
    """Named block paths and their original bodies, excluding ``$script``."""
    script: str | None
    """Original ``$script`` body, or ``None`` if the block is absent."""


class FyaDocument:
    """Literal parse result returned by :func:`parse_fya`, before assembly.

    This declaration is normalized but not evaluated or merged. In ``fields``,
    ``_`` has become :data:`flowing.parsable.PENDING`, and fields designated as
    resource lists have become ``list[EntryRef]``. ``blocks`` retains the
    original text until the assembly layer fills it in; ``script`` is waiting
    for that layer to compile it into class-body methods.

    All produced values remain raw: named blocks are source strings and YAML
    values retain their native types. This stage does not construct
    ``Parsable`` objects, look up files, or validate field semantics.
    ``blocks`` has unique keys because duplicate paths fail during splitting,
    and it never contains the reserved ``$script`` block.
    """

    fields: dict[str, Any]
    """Top-level YAML fields, with ``_`` mapped to ``PENDING`` and selected
    resource-list fields normalized to ``list[EntryRef]``."""
    blocks: dict[str, str]
    """Named block paths and their original bodies; no block has been merged into ``fields``."""
    script: str | None
    """Original ``$script`` body, or ``None`` if the block is absent."""


def parse_fya(
    text: str,
    *,
    entry_fields: Collection[str] = (),
    naming: NamingRules | None = None,
) -> FyaDocument:
    """Parse a complete ``.fya`` document and normalize selected list fields.

    Parsing proceeds in three steps: :func:`split_fya` separates the YAML,
    named blocks, and script; :func:`load_fya_yaml` loads the YAML and maps
    ``_`` values to ``PENDING``; then fields named in ``entry_fields`` are
    converted to lists of :class:`EntryRef`. Other YAML fields are left
    unchanged. Named blocks remain separate for the assembly layer to merge.

    :param text: Complete source text of the ``.fya`` document.
    :param entry_fields: Names of fields whose list entries should be
        normalized. A missing field is left absent.
    :param naming: Rules for inferring aliases from path-form entries. It is
        needed when normalization encounters a path-shaped entry and can be
        omitted for other reference forms.
    :return: Parsed document containing fields, named blocks, and script text.
    :raises flowing.errors.FormatError: The document has invalid block syntax,
        invalid YAML, a non-mapping top-level value, or a selected resource
        field that is not a list.

    .. rubric:: Example

    .. code-block:: python

        from flowing.runtime import AGENT_NAMING

        text = "name: order-agent"

        # The Agent assembly layer has resource-list fields.
        doc = parse_fya(
            text, entry_fields={"subagents", "tools"}, naming=AGENT_NAMING
        )
        # Tool and skill assembly layers have no resource-list fields.
        doc = parse_fya(text)

    Empty input, YAML without blocks, and blocks without YAML are valid.
    """
    ...


def split_fya(text: str) -> RawFya:
    """Separate top-level YAML, named blocks, and the optional ``$script`` block.

    This function splits text only; it does not interpret YAML. All text
    before the first line containing only ``---`` is the top-level YAML
    section. If the document starts with that separator, the YAML section is
    empty. After each separator, the next line must be a header of the form
    ``$dotted.path:``. Each path segment may contain ASCII letters, digits,
    underscores, or hyphens, but not list indices. A block body is retained
    verbatim from the line after its header through the next separator or end
    of input: indentation and a trailing newline are preserved. The reserved
    ``$script`` body is returned separately instead of appearing in
    ``blocks``.

    .. rubric:: Behavior notes

    - Duplicate block paths raise :class:`flowing.errors.FormatError`.
      Different capitalization counts as a different key; paths are not
      normalized, so an unresolved typo is reported later by the assembly
      layer. A second ``$script`` block also raises ``FormatError``.
    - A missing or invalid header after ``---`` raises ``FormatError``. This
      includes empty path segments, disallowed characters, and ``[n]`` index
      segments.
    - A block body may be an empty string. An empty body is still a declared
      value and is distinct from a block that was not declared.
    - This function neither parses YAML nor maps ``_`` to ``PENDING`` (that is
      :func:`load_fya_yaml`'s responsibility). It also does not check whether
      path segments name real fields; the assembly layer checks them while
      navigating the block paths.

    :param text: Complete source text of the ``.fya`` document.
    :return: Original YAML text and named block/script bodies.
    :raises flowing.errors.FormatError: A separator has no valid following
        header, a named block path is duplicated, or ``$script`` occurs more
        than once.

    .. rubric:: Example

    .. code-block:: python

        raw = split_fya("name: demo\\n---\\n$system_prompt:\\nHello")
        raw.yaml_text  # "name: demo\\n"
        raw.blocks     # {"system_prompt": "Hello"}
    """
    ...


def load_fya_yaml(text: str) -> dict[str, Any]:
    """Load a top-level YAML mapping and recursively map the scalar ``_`` to ``PENDING``.

    This loader is shared by the top-level section of ``.fya`` documents and
    skill ``.md`` front matter, so the ``_`` to ``PENDING`` mapping has one
    implementation across the framework.

    - A YAML scalar whose string value is exactly ``"_"`` becomes
      :data:`flowing.parsable.PENDING`, recursively inside mappings and lists.
      Other strings, including ``$"_"``, remain unchanged.
    - A list item that becomes ``PENDING`` is accepted at this layer;
      :func:`normalize_entries` rejects it later because list items have no
      alias by which the promise could be fulfilled.
    - Non-string YAML values keep their native types. Wrapping values in
      ``Parsable`` is an assembly-layer responsibility.
    - Empty text produces an empty mapping. A top-level scalar or list raises
      ``FormatError`` with the first non-comment, non-empty line number. YAML
      syntax errors are also wrapped in ``FormatError`` with the parser's line
      information.

    The loader does not expand environment variables or resolve ``$`` file
    references; those belong to Parsable evaluation.

    :param text: YAML text from an ``.fya`` declaration or compatible front matter.
    :return: The loaded mapping.
    :raises flowing.errors.FormatError: YAML syntax is invalid or the top-level
        YAML value is not a mapping. Syntax errors retain the YAML parser's
        line information in the wrapped error.

    .. rubric:: Example

    .. code-block:: python

        from flowing.parsable import PENDING

        load_fya_yaml("a: _")["a"] is PENDING  # True

    .. seealso:: :data:`flowing.parsable.PENDING` is the sentinel itself.
    """
    ...


def split_as(s: str) -> tuple[str, str | None]:
    """Split an entry at a whitespace-delimited ``as`` alias clause.

    This is the framework's single implementation of the ``as`` syntax. It is
    used when normalizing resource-list items and when identifying override
    parameter aliases such as ``working_dir as cwd``.

    The clause is recognized only when ``as`` has at least one whitespace
    character on both sides. Multiple clauses or an empty side raise
    ``FormatError``. Both returned parts are stripped at their ends; internal
    whitespace is preserved. Without a clause, the reference is returned
    unchanged apart from stripping and the alias is ``None``. The function
    does not validate reference or alias characters or forms; those checks
    belong to :func:`flowing.paths.classify_ref` and the assembly layer.

    .. rubric:: Examples

    .. code-block:: python

        split_as("payment as pay") == ("payment", "pay")
        split_as("payment") == ("payment", None)
        split_as("a  as  b") == ("a", "b")

    :param s: Reference text, optionally followed by ``as`` and an alias.
    :return: A ``(reference, alias)`` pair; the alias is ``None`` if omitted.
    :raises flowing.errors.FormatError: More than one ``as`` clause is
        present, or either side of the clause is empty.
    """
    ...


def normalize_entries(
    items: list[Any], *, naming: NamingRules | None = None
) -> list[EntryRef]:
    """Normalize resource-list items into :class:`EntryRef` objects.

    Each item is processed in order:

    1. Validate that it is a string reference or a one-key mapping from a
       reference to a mapping body. Other forms, including multi-key mappings
       and scalars, raise ``FormatError``.
    2. Split a whitespace-delimited ``as`` clause with :func:`split_as`.
    3. Classify the reference with :func:`flowing.paths.classify_ref`. A path
       prefix takes precedence, so ``::`` inside a path is not parsed as a
       namespace separator; otherwise, the first ``::`` identifies a
       qualified name.
    4. Infer an alias using the first matching rule: an explicit ``as`` value;
       the name segment after the first ``::``; the bare reference itself; or
       a path-derived name using ``naming``.

    Resource lookup and interpretation of the mapping body are left to
    consumers.

    .. rubric:: Behavior notes

    - A list item equal to ``PENDING`` (from YAML ``- _``) raises
      ``FormatError``. A list item has no alias for deep block navigation, so
      this promise could never be fulfilled. ``PENDING`` is only meaningful in
      positions addressable by name, such as scalar fields and mapping values.
    - A one-key mapping whose value is ``PENDING`` (for example,
      ``- payment: _``) becomes an empty mapping. This is an empty patch at a
      declared override position, allowing the original definition to fill
      the values back in. A scalar or list value instead raises ``FormatError``.
    - Duplicate aliases are retained here. The assembly layer checks binding
      conflicts because it needs the target identity to apply glob-specific
      exceptions; this layer does not know that identity.
    - This function does not parse ``raw``, read files, or consult a registry.
      It also does not interpret the mapping body's contents.

    .. rubric:: Example

    .. code-block:: python

        from flowing.runtime import AGENT_NAMING

        normalize_entries(
            [
                "payment as pay",
                "./a/payment",
                {"builtin::web-search": {"visible": True}},
            ],
            naming=AGENT_NAMING,
        )
        # -> [EntryRef("payment", "pay", {}),
        #     EntryRef("./a/payment", "payment", {}),
        #     EntryRef("builtin::web-search", "web-search", {"visible": True})]

    :param items: Resource-list values containing strings or one-key mappings.
    :param naming: Rules for inferring aliases from path-form entries.
    :return: Normalized entries in their original order.
    :raises flowing.errors.FormatError: An item has an unsupported shape, an
        override value is neither a mapping nor ``PENDING``, a list item is
        ``PENDING``, or a path-form entry needs missing naming rules.

    A ``PENDING`` override value becomes an empty mapping. Duplicate aliases
    are preserved here; the assembly layer performs binding-conflict checks.
    """
    ...
