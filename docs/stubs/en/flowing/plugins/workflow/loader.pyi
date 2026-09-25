"""Resolve a workflow definition file to a ``Workflow`` subclass.

This module performs only path resolution and definition loading. It returns a
class without instantiating or running it; the caller supplies the ``caller``
and ``runtime`` when constructing the workflow.
"""

import ast
from pathlib import Path

from .workflow import Workflow


def resolve_workflow(path: str, *, project_root: Path | None = None) -> type[Workflow]:
    """Resolve a workflow definition file and return its ``Workflow`` subclass.

    .. rubric:: Overview

    Resolution happens when this function is called. It does not scan at
    startup and does not cache results, so a workflow file can live anywhere,
    including a file created for an immediate task. The function accepts either
    an absolute path or a project-root-relative path prefixed with ``@/``. The
    prefix is resolved against ``project_root``, which callers commonly obtain
    from their ``Runtime``.

    A path without a ``.py`` suffix is also accepted. For a kebab-case final
    path component, the loader first tries appending ``.py`` unchanged; if that
    file is absent, it tries replacing hyphens with underscores before adding
    ``.py``. If both candidates exist, resolution is ambiguous and raises
    :class:`flowing.errors.FlowingError`.

    Definition files have two mutually exclusive forms:

    1. **Class form.** The file defines exactly one subclass of
       :class:`Workflow` itself. Imported subclasses do not count. If a
       top-level ``run`` function is also present, the class form takes
       precedence and the function is ignored.
    2. **Function form.** The file defines no ``Workflow`` subclass and has a
       top-level ``async def run(prompt=None, ...)`` without a ``self``
       parameter. The loader compiles it into a generated ``Workflow``
    subclass. The loader injects ``self`` as the first parameter when it
    compiles ``run``. Within that function, direct bare calls to
    ``create_agent(...)``, ``agent(...)`` (a shorthand for
    ``create_agent(...)``), and ``tool_call(...)`` are rewritten to calls on
    ``self``. This is an AST call-expression rewrite, so it does not change
    strings or comments. Attribute calls are left alone, and the loader does
    not analyze whether a same-named local variable shadows a bare call; that
    case is not part of the documented contract. The generated class name is
    derived from the file stem in PascalCase; for example,
       ``verify-fix.py`` produces ``VerifyFix``.

    .. rubric:: Example

    .. code-block:: python

       # verify-fix.py: function form, with no imports, class, or self parameter
       async def run(prompt=None, max_rounds: int = 3):
           verifier = await create_agent("verifier-agent")
           result = await verifier.query("Run tsc --noEmit and list all errors")
           if result.status == "completed" and "error" not in result.final_text:
               return {"status": "passed"}
           fixer = await agent("fixer-agent")  # agent(...) abbreviates create_agent(...)
           fix_result = await fixer.query("Fix the errors above")
           await fixer.destroy()
           return {"status": "fixed"}

       # The caller drives the workflow directly; caller=None makes it a root node.
       wf_class = resolve_workflow("@/verify-fix.py")
       instance = wf_class(caller=None, runtime=runtime)  # runtime is a Runtime instance
       result = await instance.run(prompt="Check and fix")

    .. rubric:: Behavioral notes

    - The function either returns a valid ``Workflow`` subclass or raises an
      exception with location context. It never returns ``None`` or a
      placeholder. A direct caller can rely on exceptions rather than checking
      for a missing result. When resolution is initiated by the
      ``run-workflow`` tool, the exception is converted at the tool boundary
      into an LLM-visible error result.
    - A missing file, multiple subclasses defined by the file, or a file with
      neither a subclass nor a top-level ``run`` raises
      :class:`flowing.errors.FlowingError`. The error identifies the missing
      path, ambiguity, or absent definition form.
    - Top-level statements execute once whenever the file is loaded, as they do
      for ``.fya`` loading. Native exceptions raised during import propagate.
      Each call resolves the path afresh; no cache is used.
    - An ``@/`` path without ``project_root`` raises ``ValueError``. Other path
      forms can still use the current working directory as their base.

    :param path: Path to the workflow definition file; the ``@/`` prefix is
        supported.
    :param project_root: Base directory for ``@/`` paths. If omitted, an ``@/``
        path raises ``ValueError`` while other path forms remain valid.
    :return: The ``Workflow`` subclass defined by the file; the class is not
        instantiated.
    :raises flowing.errors.FlowingError: If the file is missing or its
        definition form is invalid or ambiguous.
    :raises ValueError: If ``path`` uses ``@/`` and ``project_root`` is omitted.

    .. seealso::

       - :class:`flowing.plugins.workflow.Workflow`
       - :class:`flowing.plugins.workflow.RunWorkflowTool`
       - :meth:`flowing.runtime.Runtime.resolve_path`
    """
    ...


class _BareCallRewriter(ast.NodeTransformer):
    """Rewrite direct workflow helper calls by adding a ``self.`` receiver.

    The AST transformer maps a bare ``agent(...)`` call to
    ``self.create_agent(...)`` and maps bare ``create_agent(...)`` and
    ``tool_call(...)`` calls to their corresponding ``self`` methods. AST-level
    rewriting leaves strings and comments untouched. It does not perform
    semantic analysis for attribute calls or local-name shadowing; those cases
    are outside the documented function-form contract.
    """

    def visit_Call(self, node: ast.Call) -> ast.AST:
        ...
