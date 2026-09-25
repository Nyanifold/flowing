"""The public package for Flowing's workflow orchestration extension.

This package re-exports the complete public surface for workflow orchestration:
:class:`Workflow`, the abstract orchestration base class;
:class:`WorkflowPlugin`, the optional plugin that registers the LLM-facing
trigger; :class:`RunWorkflowTool`, that trigger tool; and
:func:`resolve_workflow`, which resolves a workflow definition file to a
``Workflow`` subclass. Import these names from this package rather than from
their implementation modules.

.. seealso::

   - :mod:`flowing.plugins.workflow.workflow` for the ``Workflow`` contract.
   - :mod:`flowing.plugins.workflow.plugin` for the tool and plugin.
   - :mod:`flowing.plugins.workflow.loader` for definition-file resolution.
"""

from .workflow import Workflow
from .plugin import WorkflowPlugin, RunWorkflowTool
from .loader import resolve_workflow

__all__ = ["Workflow", "WorkflowPlugin", "RunWorkflowTool", "resolve_workflow"]
