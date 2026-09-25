"""The ``run-workflow`` LLM trigger tool and the workflow extension plugin."""

from typing import Any, ClassVar

from flowing.agent import Agent
from flowing.plugins import Plugin
from flowing.runtime import Runtime
from flowing.tool import ScriptTool, ToolDefinition

from .workflow import Workflow


class RunWorkflowTool(ScriptTool):
    """Launch a workflow from an LLM-visible tool call.

    .. rubric:: Overview

    The LLM supplies a workflow definition path and any run parameters, which
    may include a natural-language prompt. This tool resolves the definition,
    instantiates the workflow, starts its ``run()`` as a background task, and
    returns a receipt immediately. ``WorkflowPlugin.install()`` registers the
    tool in the global tool registry; an Agent can then expose it to the LLM by
    binding ``run-workflow`` with ``add_tool()``.

    The implementation is an async generator, following the background-tool
    contract documented by :class:`flowing.tool.ScriptTool`. Its first yield is
    a receipt that ``Tool.__call__`` wraps as a ``pending`` result. The
    framework then drives the remaining generator in the background and posts
    subsequent yields as EVENT messages. The class-level ``definition`` uses
    ``strict=False``: only ``path`` is declared by the tool, while the accepted
    run parameters are determined by ``Workflow.run()`` and are not restricted
    by this tool's schema.

    .. rubric:: Behavioral notes

    - Before its first yield, the tool performs only preparation:
      ``resolve_workflow(path)`` followed by construction as
      ``wf_class(caller, caller.runtime)``. The first yield is
      ``{"status": "started", "workflow": path}``. The framework wraps it
      in a ``pending`` ``ToolResult`` and associates a background-task
      registration key (``background_task_id``); this receipt is returned
      before ``run()`` completes.
    - The background segment awaits ``instance.run(**args)``; ``path`` is not
      forwarded in ``args``. On successful completion, the final yield is
      ``{"status": "done", "workflow": path}``, which the framework posts
      as an LLM-visible EVENT message.
    - The tool does not deliver the workflow's data result. Orchestration code
      can send that result itself with ``caller.enqueue_message(...)``. The
      framework completion message and the workflow's result message may both
      exist: one communicates status and the other carries data.
    - A path-resolution error before the first yield is handled by
      ``Tool.__call__``: ``Intercepted`` becomes ``blocked`` and other
      exceptions become an LLM-visible result with ``status="error"``. An
      exception from ``run()`` in the background segment produces an EVENT
      error block and a diagnostic log, both visible through their respective
      channels.
    - The tool does not wait for completion, persist workflow state, or impose
      concurrency or node-count limits.

    .. seealso::

       - :class:`Workflow`, the object launched by this tool.
       - :func:`flowing.plugins.workflow.resolve_workflow`, which resolves its
         definition.
       - :class:`flowing.tool.ScriptTool`, the base class and background-tool
         contract.
    """

    definition: ToolDefinition = ToolDefinition(
        name="run-workflow",
        description=(
            "Launch an orchestration workflow. The path parameter is the workflow "
            "definition file path (relative to the @/ project root); remaining "
            "arguments are passed through to Workflow.run()."
        ),
        params_schema={
            "path": {
                "type": "string",
                "description": "path of the workflow definition file",
            }
        },
        strict=False,
    )
    """Class-level default definition for ``run-workflow``.

    The schema declares only ``path`` and sets ``strict=False``. Additional run
    parameters are determined by ``Workflow.run()`` and are not constrained at
    the tool boundary. This follows the thin-entry-point / inner-validation
    pattern also used by :class:`flowing.plugins.skills.SkillLoadTool`.
    """

    async def execute(self, *, path: str, caller: Agent, **args: Any):
        """Start the selected workflow as a background task.

        Before yielding, this method resolves ``path`` and constructs the
        workflow as ``wf_class(caller, caller.runtime)``. Preparation errors
        are dispatched by ``Tool.__call__``: ``Intercepted`` becomes
        ``blocked`` and other exceptions become an LLM-visible ``error``
        result. The first yield is the receipt
        ``{"status": "started", "workflow": path}``, which the framework
        wraps as ``pending`` and associates with a background-task registration
        key.

        The framework drives the remaining generator in the background. It
        awaits ``instance.run(**args)`` without forwarding ``path``; after
        successful completion, this method yields
        ``{"status": "done", "workflow": path}`` for delivery as an
        LLM-visible EVENT message. An exception during this background segment
        is reported as an EVENT error block and a diagnostic log. This method
        neither waits for completion nor places ``run()``'s return value in the
        receipt.

        :param path: Workflow definition file path, including an optional
            ``@/`` project-root prefix.
        :param caller: Calling Agent. The LLM-triggered path always supplies an
            Agent rather than ``None``.
        :param args: Run parameters forwarded to ``Workflow.run()``.

        .. seealso:: :func:`flowing.plugins.workflow.resolve_workflow`,
           :meth:`Workflow.run`
        """
        ...


class WorkflowPlugin(Plugin):
    """Register the workflow trigger and expose a root-workflow factory.

    .. rubric:: Overview

    When ``runtime.install(WorkflowPlugin())`` invokes ``install(runtime)``,
    the plugin registers :class:`RunWorkflowTool` in
    ``runtime.tool_registry`` and saves the runtime reference for
    :meth:`launch`. An Agent can then bind ``run-workflow`` with
    ``add_tool("run-workflow")`` so an LLM can launch definitions by path.
    Host or application code can create a root workflow with
    ``runtime.get_plugin("workflow").launch(path)``.

    The ``Workflow`` base class is usable as soon as it is imported; it does
    not require per-instance enablement. This plugin has two responsibilities:
    connecting the LLM-facing ``run-workflow`` entry point and providing
    ``launch`` for creating root workflows, which requires the plugin to hold a
    runtime. An application that needs neither the LLM entry point nor a root
    workflow can omit this plugin; ``Workflow`` and
    :func:`flowing.plugins.workflow.resolve_workflow` remain available for
    direct code-driven use.

    .. rubric:: Example

    .. code-block:: python

       runtime.install(WorkflowPlugin())  # Register the tool and retain the runtime.
       agent = await runtime.create_agent("main-agent")
       agent.add_tool("run-workflow")  # Make the tool available to this Agent's LLM.

       # Create a root workflow from application code.
       wf = runtime.get_plugin("workflow").launch("@/pipelines/release.py")

    .. rubric:: Behavioral notes

    - ``install()`` is synchronous. It registers the tool and saves the runtime;
      it does not instantiate workflows or scan directories. Definition files
      are discovered only when :func:`flowing.plugins.workflow.resolve_workflow`
      is called.
    - :meth:`launch` requires a runtime saved by installation. Calling it
      before installation raises ``ValueError``.
    - The plugin declares no hook points, registers no provide values, and
      declares no Agent state keys. It does not attach any hook handlers.
    - Installing another plugin with the same name raises ``ValueError`` due
      to ``Runtime.install()`` duplicate-name checking.
    - If the canonical tool name ``run-workflow`` is already registered,
      installation raises :class:`flowing.errors.ToolNameConflictError`.

    :raises flowing.errors.ToolNameConflictError: If a tool named
        ``run-workflow`` is already registered.

    .. seealso::

       - :class:`RunWorkflowTool` and :class:`Workflow`.
       - :class:`flowing.plugins.Plugin` for the plugin base-class contract.
    """

    name: ClassVar[str] = "workflow"
    """Explicit plugin registration name. The framework does not derive a
    default name. A common convention is to remove the ``Plugin`` suffix from
    the class name and convert the remainder to kebab-case; for example,
    ``WorkflowPlugin`` becomes ``"workflow"``.
    """

    dependencies: ClassVar[list[str]] = []
    """Plugin dependency metadata. This plugin has no dependencies, so the
    list is empty. Dependency validation occurs in
    :meth:`flowing.runtime.Runtime.install`.
    """

    _runtime: Runtime | None = None
    """The runtime retained at installation and used by :meth:`launch`.

    It is ``None`` before installation, which lets ``launch`` reject use of an
    unregistered plugin. Retaining the runtime supports the explicit factory
    call; it is not used for runtime callbacks and does not conflict with the
    plugin convention against retaining a runtime for such callbacks.
    """

    def install(self, runtime: Runtime) -> None:
        """Register ``run-workflow`` and retain ``runtime`` for :meth:`launch`.

        Installation is synchronous. It registers
        ``RunWorkflowTool()`` through ``runtime.register_tool()`` and stores
        the runtime reference. It creates no workflow instances and scans no
        directories; definition discovery occurs later in
        :func:`flowing.plugins.workflow.resolve_workflow`.

        .. seealso:: :class:`RunWorkflowTool`, :meth:`launch`
        """
        ...

    def launch(self, path: str) -> Workflow:
        """Resolve and construct a root workflow with ``caller=None``.

        This is the root-workflow creation entry point, corresponding to the
        former Workflow path through ``Runtime.mount``. It resolves the
        definition file at ``path``, which must contain exactly one
        ``Workflow`` subclass, and constructs that class with ``caller=None``
        and the runtime retained during installation. The
        returned workflow is already registered in the node tree and placed in
        the provide chain by its constructor.

        .. rubric:: Example

        .. code-block:: python

           runtime.install(WorkflowPlugin())
           wf = runtime.get_plugin("workflow").launch("@/pipelines/release.py")

        The plugin must have been installed first; otherwise this method raises
        ``ValueError``. A root workflow has ``caller=None`` and points to the
        Runtime in its parent chain. In contrast, a workflow launched by
        ``run-workflow`` uses the calling Agent as its caller. Construction is
        synchronous because it assigns the node ID and attaches the node to
        the object/provide trees. No additional constructor parameters are
        passed; subclasses carry their own initialization data in the
        definition file or defaults.

        :param path: Workflow definition file path, following the ``@/`` path
            rules when that prefix is used.
        :return: A constructed and registered root ``Workflow`` instance.
        :raises ValueError: If the plugin has not been installed.
        :raises flowing.errors.FlowingError: If the definition file is missing
            or its definition form is invalid.

        .. seealso::

           - :func:`flowing.plugins.workflow.resolve_workflow`
           - :class:`Workflow`
           - :class:`RunWorkflowTool`
        """
        ...
