"""Compile declarative ``.fya`` Agent definitions into in-memory classes or sibling ``.py`` files.

This module is the synthesis layer in the three-stage ``.fya`` pipeline:
lexical parsing, assembly, and class synthesis. The two public compilation
forms share the same source-generation path:

* :func:`compile_fya_class` creates the in-memory Agent subclass. It is the
  runtime's sole synthesis point; path-based ``Runtime.get_agent_class``
  resolution, ``mount()``, and assembly-layer resource references all pass
  through it.
* :func:`compile_fya_file` writes that generated source as a sibling ``.py``
  file, replacing the ``.fya`` suffix. The result supports ordinary imports
  such as ``from payment import PaymentAgent`` and can be inspected by
  linters, type checkers, and mypy. This form is recommended for production
  deployment and CI.

Runtime resolution does not require an import-time hook: Agent classes are
resolved lazily by string name through ``Runtime.get_agent_class``. Python
code that directly imports a class declared in ``.fya`` uses the explicitly
generated file.

.. rubric:: Shared conventions

* ``payment.fya`` generates ``payment.py`` in the same directory, so the
  standard import chain works without a hook.
* File compilation stores metadata in one ``.flowing.meta.yaml`` file beside
  the generated artifacts, with one entry per ``.fya`` filename. The entry has
  three fields:

  .. list-table:: ``.flowing.meta.yaml`` entry
     :header-rows: 1

     * - Field
       - Meaning
       - On mismatch
     * - ``fya_hash``
       - Hash of the parsed ``.fya`` structure; comment and blank-line changes
         do not affect it.
       - A different current parse result triggers recompilation.
     * - ``py_hash``
       - AST hash of the generated ``.py`` file; formatting changes do not
         affect it.
       - A mismatch with the actual artifact raises
         :class:`flowing.errors.ArtifactModifiedError`; the file is not
         overwritten.
     * - ``compiler_version``
       - The value of :data:`COMPILER_VERSION`.
       - A different version forces recompilation.

* Compilation is idempotent. If ``fya_hash`` and ``compiler_version`` are
  unchanged, a verified artifact is left untouched. After a conflict stops a
  batch, a later run can continue; already generated files are not rolled back.
* Compilation occurs during build or startup, not as a hot-reload mechanism;
  the compiler does not watch for runtime file changes.
* ``.fya`` declarations and equivalent hand-written ``.py`` subclasses are
  two declaration forms. When same-named forms coexist, ``.fya`` takes
  precedence and emits a warning; the precedence decision belongs to
  ``Runtime.get_agent_class``.
* This module only compiles declarations. It does not delete orphaned ``.py``
  files, compile hand-written Python Agent classes, start a Runtime, or run
  ``main``.

.. rubric:: Example

.. code-block:: python

    from pathlib import Path
    from flowing.compiler import compile_fya_file

    product = compile_fya_file(Path("agents/payment.fya"))

.. seealso::

    * :func:`flowing.interfaces.cli.cmd_compile` is the CLI wrapper for this
      module.
    * :mod:`flowing.parser` is the lexical front end and supplies
      :class:`flowing.parser.FyaDocument`.
    * :mod:`flowing.errors` defines ``CompileError`` and
      ``ArtifactModifiedError``.
"""

from pathlib import Path

COMPILER_VERSION: str
"""Compiler version stored in the ``compiler_version`` metadata field.

The version is part of the cache key. A value different from the one recorded
in metadata forces recompilation even when the parsed ``.fya`` structure is
unchanged, preventing an output-format change from silently reusing stale
artifacts. After a framework upgrade, the first compilation of the project's
``.fya`` files recompiles them as a batch; subsequent unchanged runs are
idempotent and inexpensive.
"""


class _Assembly:
    """Data returned by the shared declaration assembly step."""

    source: str
    """Generated Python source code."""
    class_name: str
    """Name of the generated Agent subclass."""
    fya_hash: str
    """Hash of the parsed declaration structure, prefixed with its algorithm name."""


def textwrap_indent(body_lines: list[str]) -> str:
    """Indent source lines as a Python class-body block.

    Blank lines remain empty instead of receiving trailing indentation.
    """
    ...


def compile_fya_class(
    fya_path: Path, *, project_root: Path | None = None
) -> type:
    """Synthesize an Agent subclass from one ``.fya`` declaration in memory.

    This is the runtime's sole class-synthesis point and does not write files.
    The pipeline parses the text into :class:`flowing.parser.FyaDocument`,
    assembles named blocks and resource entries, normalizes ``args`` schemas
    with :func:`flowing.params.expand_args_schema`, bridges them to
    ``args_model``, and creates an ordinary :class:`flowing.agent.Agent`
    subclass. Class attributes include ``source_file``, ``description``, and
    ``system_prompt``.

    In the optional ``$script`` section, top-level functions whose first
    positional parameter is ``self`` (including ``setup``, ``@on`` handlers,
    and instance methods) become class methods. All other top-level
    statements, including imports, functions without ``self``, class
    declarations, and assignments, are emitted in their original order at
    module scope. This makes those definitions visible to method bodies,
    which cannot resolve statements left at class-body scope as module globals.

    :param fya_path: Path to the source ``.fya`` file.
    :param project_root: Base directory for ``@/`` references and the generated
        class's source-path metadata. If omitted, the current launch context is
        used when available.
    :return: The generated Agent subclass.
    :raises flowing.errors.FormatError: A declaration error in the
        ``FormatError`` family.
    :raises flowing.errors.CompileError: Synthesis fails for another reason.

    .. rubric:: Behavior

    - The function is synchronous, operates in memory, and does not cache the
      generated class. Registry and lazy-resolution callers handle reuse.
    - If a same-named hand-written ``.py`` file exists, precedence is decided
      by the caller that resolves the Agent type; this function does not
      perform that selection.

    .. seealso:: :func:`compile_fya_file` for generated-file compilation.
    """
    ...


def compile_fya_file(
    fya_path: Path, *, project_root: Path | None = None
) -> Path:
    """Compile one ``.fya`` declaration into a sibling ``.py`` file with metadata hash checks.

    The generated path uses the same directory and basename as the source,
    with the ``.fya`` suffix replaced by ``.py``. It uses the same assembly
    and source generation as :func:`compile_fya_class`. ``Parsable`` values
    are emitted as class-body assignments, and ``$script`` statements are
    split between class and module scope according to the ``self`` parameter.

    The sibling ``.flowing.meta.yaml`` file has one entry per source filename
    and records ``fya_hash`` (the parsed declaration structure), ``py_hash``
    (the generated source's AST), and ``compiler_version``.

    :param fya_path: Path to the source ``.fya`` file.
    :param project_root: Base directory for ``@/`` references and source-path
        metadata. If omitted, the current launch context is used when available.
    :return: The generated ``.py`` path, whether or not this call rewrote it.
    :raises flowing.errors.ArtifactModifiedError: The existing generated file
        differs from its recorded hash, or exists without a matching metadata
        record. Compilation stops without overwriting it.
    :raises flowing.errors.CompileError: Parsing or emitting the artifact
        fails for another reason.

    .. rubric:: Behavior

    - If the declaration hash and compiler version are unchanged, the
      existing verified artifact is returned without rewriting it.
    - A changed declaration or compiler version causes a rewrite only after
      the existing generated file passes the modification check.

    .. seealso:: :func:`compile_fya_class` for in-memory compilation and
        :func:`compile_project` for project-wide compilation.
    """
    ...


def compile_project(path: Path) -> list[Path]:
    """Compile eligible Agent ``.fya`` declarations below a project directory.

    This is the project-level entry point used by ``flowing compile <path>``.
    The function recursively scans for ``.fya`` files, sorts the paths, and
    compiles each eligible declaration to a sibling ``.py`` file. Each
    artifact's hash entry is stored in the ``.flowing.meta.yaml`` file in its
    own directory. A conflict stops the scan; files already generated are not
    rolled back, so an idempotent rerun can continue.

    Files named ``TOOL.fya`` or ``tool.fya`` and files ending in ``.tool.fya``
    or ``.skill.fya`` are excluded by name. Files with other names are not
    classified by inspecting their contents.

    :param path: Project directory to scan.
    :return: Generated file paths in sorted source-path order. The list is
        empty when no eligible Agent declarations are found.
    :raises flowing.errors.ArtifactModifiedError: The first generated artifact
        that fails its modification check stops the scan. Earlier files remain
        written and are not rolled back.

    .. rubric:: Behavior

    - If there are no ``.fya`` files, the result is an empty list; the CLI
      reports that there are no compilable files and exits normally.
    - Repeating the operation without changes is a no-op for verified outputs.
    - Tool and skill declarations are not compiled by this module.
    """
    ...
