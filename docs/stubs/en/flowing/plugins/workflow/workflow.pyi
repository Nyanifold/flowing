"""Rule-based orchestration as a Flowing extension.

.. rubric:: Overview

This extension provides rule-based orchestration: Python code explicitly
specifies branches, loops, parallel work, and sequential steps. This is
orthogonal to goal mode, where an LLM chooses a path toward a goal. A workflow
fits a known process that should be deterministic and repeatable, such as an
approval flow, a verify-and-fix loop, or a fixed-step pipeline. Subclass
:class:`Workflow`, implement ``run()``, connect the child Agents, tools, and
messages needed by the orchestration, and then drive it directly from code or
launch it through the LLM-facing ``run-workflow`` tool.

The extension is not an Agent: it has no Turn loop, does not call an LLM, and
does not itself create a message-tree node, although it can create and drive
Agents. It is also not a declarative DSL. Workflow definitions are Python files
in class or function form, resolved by
:func:`flowing.plugins.workflow.resolve_workflow`; the loader does not parse
Mermaid or graph declarations.

.. rubric:: Registration surface

- Install ``WorkflowPlugin`` with ``runtime.install(WorkflowPlugin())`` to
  register ``run-workflow`` in the global tool registry. An Agent can then
  expose the tool with ``add_tool("run-workflow")`` so an LLM can launch a
  workflow by path. There is no per-instance ``use_workflow(self)`` step:
  importing ``Workflow`` and :func:`flowing.plugins.workflow.resolve_workflow`
  is sufficient for direct code-driven use. Installing a plugin with a
  duplicate name raises ``ValueError``.
- Installation registers :class:`flowing.plugins.workflow.RunWorkflowTool`, a
  :class:`flowing.tool.ScriptTool` named ``run-workflow`` in the
  ``default::`` namespace. The plugin does not register provide values,
  declare Agent state keys, or add prompt blocks.
- The extension declares no new hook points and the plugin attaches no
  handlers. Each ``Workflow`` instance has its own ``hooks`` registry, created
  at construction, and uses the framework's core ``before_tool_call`` and
  ``after_tool_call`` points. These hooks are independent of every Agent's
  hooks; handlers attached to an Agent do not run for workflow tool calls.
- Without the plugin, the ``run-workflow`` tool is unavailable to LLMs, but
  ``Workflow`` and ``resolve_workflow`` remain usable. Direct code-driven
  workflows therefore do not depend on enabling the plugin.

.. rubric:: Position and lifecycle in the object graph

A ``Workflow`` instance occupies two positions:

- In the node tree, it is registered in the shared ``runtime._nodes`` ID space
  at construction and receives a ``workflow-*`` ``node_id``. It can be a root
  node (``caller=None``, with ``_parent_id`` pointing to the Runtime) or a
  child of an Agent (with ``_parent_id = caller.node_id`` when launched by
  ``run-workflow``). Agents created by the workflow point their ``parent_id``
  to the workflow's ``node_id``. The workflow is their lifecycle parent, so
  ``destroy()`` archives them recursively, removing them from the pool and
  registry while retaining their session files.
- In the provide/inject chain, values provided by a workflow are visible to all
  Agents it creates and their descendants. ``inject()`` walks up
  ``_parent_id`` (workflow → caller → Runtime). Tool calls and child-Agent
  creation receive arguments supplied directly by orchestration code; they do
  not use inject-based argument filling.

Workflow run state is not persisted. Each run should use a new instance, and a
crash is not promised to be resumable. Workflow node records in ``_nodes``
become invalid when the Runtime exits.

.. rubric:: Driving Agents and calling tools

The only entry point a workflow uses to create a child Agent is
:meth:`Workflow.create_agent`, which takes a type name and delegates to
``runtime.create_agent``. The returned Agent can be driven with ``message()``
or ``query()``. A workflow has no ``invoke_subagent`` method: that Agent-side
wrapper combines LLM invocation, ``SubagentEntry`` resolution, hooks, and pool
lifecycle, whereas workflow orchestration code supplies the wrapper itself.
The workflow path does not trigger ``on_subagent_invoke`` or
``on_subagent_returns``; its creation path uses ``before_create`` and
``after_create``. Parallel work needs no dedicated API; ``asyncio.gather`` is
the primitive.

Workflows call tools with :meth:`Workflow.tool_call`, passing a canonical name
and keyword arguments. Calls use the workflow's own
``before_tool_call``/``after_tool_call`` hooks, independent of Agent hooks.
Approval, security, or audit handlers attached to an Agent do not run on this
path.

.. rubric:: Driving the caller and avoiding asynchronous deadlocks

A workflow can drive the Agent that launched it: ``await self.caller.query(...)``
sends a message into the caller's session and waits for its turn result. This
capability has an asynchronous constraint. A workflow launched through
``run-workflow`` always runs in the background and returns a receipt
immediately. The caller's work loop is serial and does not consume a new
message until its current Turn (which is executing ``run-workflow``) completes.
If the tool waited synchronously for the workflow, the caller and workflow
would wait on each other. Synchronous execution is therefore only suitable
when the workflow does not call back into its caller; the caller is responsible
for ensuring this.

.. rubric:: Example

.. code-block:: python

   from flowing.plugins.workflow import Workflow, WorkflowPlugin

   runtime.install(WorkflowPlugin())  # Register the run-workflow tool.

   class VerifyFixWorkflow(Workflow):
       '''Run a verifier, fix failures, and repeat until success or two rounds without progress.'''

       async def run(self, prompt: str | None = None, max_rounds: int = 3) -> dict:
           no_progress = 0
           for round_no in range(max_rounds):
               verifier = await self.create_agent("verifier-agent")
               result = await verifier.query("Run tsc --noEmit and list all errors")
               if result.status == "completed" and "error" not in result.final_text:
                   return {"status": "passed", "rounds": round_no + 1}
               fixer = await self.create_agent("fixer-agent")
               fix_result = await fixer.query("Fix the errors above")
               await fixer.destroy()
               if "no progress" in fix_result.final_text:
                   no_progress += 1
                   if no_progress >= 2:
                       return {"status": "stalled"}
           return {"status": "failed", "rounds": max_rounds}

   # Drive it directly from code as a root workflow (caller=None).
   wf = VerifyFixWorkflow(caller=None, runtime=runtime)
   result = await wf.run(prompt="Check and fix")

.. seealso::

   - :mod:`flowing.runtime` for ``create_agent``, ``_nodes``, and the
     provide/inject chain.
   - :mod:`flowing.agent` for ``query()``, ``side_query()``, ``destroy()``,
     and ``TurnResult``.
   - :mod:`flowing.tool` for ``Tool``, ``ToolCall``, ``ToolResult``, and
     ``ToolRegistry``.
   - :mod:`flowing.plugins.skills` for another plugin-provided capability.
   - :mod:`flowing.errors` for ``ToolNotFoundError`` and ``Intercepted``.
"""

from abc import ABC, abstractmethod
from typing import Any

from flowing.agent import Agent
from flowing.hooks import HookRegistry
from flowing.params import InjectionKey
from flowing.runtime import Runtime
from flowing.tool import ToolResult


class Workflow(ABC):
    """Base class for one orchestration run; subclasses implement ``run()``.

    A workflow instance is a single orchestration carrier. Subclass this class
    and implement ``run()`` with the control flow for creating, using, and
    destroying Agents, calling tools, driving the caller, or coordinating
    parallel branches. The module documentation describes the extension's
    registration model, object-graph placement, and asynchronous constraints.

    .. rubric:: Example

    .. code-block:: python

       class VerifyFixWorkflow(Workflow):
           async def run(self, prompt: str | None = None, max_rounds: int = 3) -> dict:
               no_progress = 0
               for round_no in range(max_rounds):
                   verifier = await self.create_agent("verifier-agent")
                   result = await verifier.query("Run tsc --noEmit and list all errors")
                   if result.status == "completed" and "error" not in result.final_text:
                       return {"status": "passed", "rounds": round_no + 1}
                   fixer = await self.create_agent("fixer-agent")
                   fix_result = await fixer.query("Fix the errors above")
                   await fixer.destroy()
                   if "no progress" in fix_result.final_text:
                       no_progress += 1
                       if no_progress >= 2:
                           return {"status": "stalled"}
               return {"status": "failed", "rounds": max_rounds}

       # Construct and drive it directly; caller=None makes it a root workflow.
       wf = VerifyFixWorkflow(caller=None, runtime=runtime)
       result = await wf.run(prompt="Check and fix")

    .. rubric:: Behavioral notes

    - Construct instances through a ``Workflow`` subclass with ``caller`` and
      ``runtime``. ``__init__`` is synchronous because node registration is a
      structural operation. Create a new object for each run: workflow state is
      not persisted across runs and is not resumable after a crash.
    - Construction assigns a stable ``workflow-*`` ``node_id`` and registers
      it in ``runtime._nodes``. Its ``_parent_id`` is ``caller.node_id``, or the
      Runtime node ID when ``caller=None``.
    - For a root workflow with ``caller=None``, accessing ``self.caller.*``
      raises ``AttributeError``. The framework does not guard these accesses;
      orchestration code must handle the root case.
    - Values provided by this workflow are visible to all Agents it creates
      and their descendants, but not to other workflow subtrees.
    - A workflow is not placed in a message queue, consumes no messages, has no
      Turn loop, and does not call an LLM itself.

    .. seealso::

       - :class:`flowing.plugins.workflow.RunWorkflowTool` for the LLM trigger
         and its asynchronous execution model.
       - :func:`flowing.plugins.workflow.resolve_workflow` for resolving a
         definition file.
       - :class:`flowing.runtime.ProvideNode` for the provide/inject protocol.
    """

    caller: Agent | None
    """The Agent that launched this workflow, or ``None`` for a root workflow.

    A non-root workflow can drive its caller with ``caller.query(...)`` (see
    the module's deadlock constraints) or call other public methods on it.
    """

    runtime: Runtime
    """The Runtime at the root of this object's graph.

    Child Agents and the tool registry are accessed through this object. When
    ``caller`` is not ``None``, ``runtime`` is the same object as
    ``caller.runtime``.
    """

    node_id: str
    """Stable node-tree ID with the ``workflow-*`` prefix.

    It is allocated and registered in ``runtime._nodes`` during construction.
    Agents created by this workflow use this ID as their ``parent_id``.
    """

    hooks: HookRegistry
    """The workflow's instance-local hook registry.

    It is independent of every Agent's ``hooks`` registry. Agent handlers do
    not run for workflow tool calls, and workflow handlers do not run for Agent
    calls. ``tool_call()`` uses the core ``before_tool_call`` and
    ``after_tool_call`` points.
    """

    _provided: dict[str, Any]
    """The workflow's table of provided values, forming one link in the
    provide chain. This is an internal, non-stable API.
    """

    _parent_id: str
    """The node ID to follow when walking up the provide chain: either
    ``caller.node_id`` or the Runtime node ID. This is an internal,
    non-stable API.
    """

    _agents: dict[str, Agent]
    """The child Agents created by this workflow that have not been destroyed.

    ``destroy()`` uses this table to archive its remaining child Agents. This
    is an internal, non-stable API.
    """

    def __init__(self, caller: Agent | None, runtime: Runtime) -> None:
        """Register a workflow instance in the node tree and provide chain.

        Construction allocates a ``workflow-*`` ``node_id``, creates an
        independent ``hooks`` registry, initializes the internal provide and
        child-Agent tables, and sets ``_parent_id`` to ``caller.node_id`` or
        the Runtime node ID. It then registers the workflow in
        ``runtime._nodes``. This synchronous structural operation starts no
        task and does not resolve any definition file; the instance is created
        from an already resolved class.

        If ``caller`` is ``None``, the instance is a root workflow. Otherwise,
        ``runtime`` should be the same object as ``caller.runtime``; a mismatch
        is a programming error that the framework does not enforce. Subclasses
        that override this method must call ``super().__init__(caller,
        runtime)`` and keep their override synchronous.

        :param caller: The Agent that launched the workflow, or ``None`` for
            a root workflow.
        :param runtime: The Runtime that owns the workflow; it should be the
            same instance as ``caller.runtime`` when a caller is supplied.

        .. seealso:: :meth:`run`, :meth:`destroy`
        """
        ...

    @abstractmethod
    async def run(
        self, prompt: str | None = None, **kwargs: Any
    ) -> dict[str, Any] | None:
        """Implement the orchestration logic for this workflow.

        Subclasses put all orchestration here, including creating, reusing, or
        destroying child Agents; calling tools; driving the caller; and
        coordinating parallel work, branches, or loops. The caller supplies
        the parameters, which can include a natural-language ``prompt`` that
        is usually forwarded as a child Agent's task prompt.

        Returning a ``dict`` provides a result; returning ``None`` means the
        run has orchestration side effects but no result. A direct caller
        receives that value from ``await run()``. When a workflow is launched
        by the tool, the return value is not included in the receipt or
        completion message; those are fixed by :class:`RunWorkflowTool`. To
        deliver a result to the caller, orchestration code should send it
        itself with ``caller.enqueue_message(...)``.

        The framework imposes no duration, node-count, or concurrency limits
        and does not catch exceptions from this method. An exception ends the
        run and propagates to its trigger; on the tool path it appears as a
        failed background task, with no automatic retry. Calling ``run()`` a
        second time on the same instance is a programming error; use a new
        workflow instance for each run. If the Runtime shuts down while this
        method is running, child Agents created here are archived recursively,
        and unfinished awaits receive cancellation.

        :param prompt: Optional natural-language task description, usually
            forwarded to a child Agent as its task prompt.
        :param kwargs: Other caller-supplied run parameters. When launched by
            ``run-workflow``, these are the additional LLM-supplied arguments.
        :return: A workflow result dictionary, or ``None`` when the run only
            performs orchestration side effects.

        .. seealso:: :meth:`create_agent`, :meth:`tool_call`
        """
        ...

    async def create_agent(self, agent_type: str, **kwargs: Any) -> Agent:
        """Create and register a child Agent; this is the workflow's sole
        Agent-creation entry point.

        This delegates to
        ``runtime.create_agent(agent_type, parent_id=self.node_id, **kwargs)``
        and follows the full creation pipeline: ``__init__`` →
        ``before_create`` → ``setup`` → the PENDING check → pool metadata write
        → ``_nodes`` registration → ``after_create`` → work-loop start. The
        Agent is added to the pool immediately, and the workflow records it so
        that ``destroy()`` can archive it recursively.

        .. rubric:: Example

        .. code-block:: python

           reviewer = await self.create_agent("reviewer-agent")
           r1 = await reviewer.query("Review the auth module")
           r2 = await reviewer.query("Review the payment module")  # Same instance; context continues.

        The returned Agent can immediately receive ``message()`` or
        ``query()``. Its ``inject()`` lookup walks through this workflow, so
        values provided here are visible to it. This path does not use
        ``SubagentEntry.resolve()`` and does not trigger
        ``on_subagent_invoke`` or ``on_subagent_returns``; creation uses only
        ``before_create`` and ``after_create``. ``kwargs`` are passed as-is to
        the child's ``setup()`` rather than being filled by injection. Failure
        to resolve ``agent_type`` propagates from the creation pipeline.
        Creating the same type more than once makes independent instances; the
        framework does not deduplicate by type name. Callers that want reuse
        must retain and reuse an Agent reference.

        :param agent_type: Agent type name, following the same contract as
            ``runtime.create_agent``.
        :param kwargs: Arguments passed to the child Agent's ``setup()``.
        :return: The fully created Agent instance.

        .. seealso::

           - :meth:`flowing.runtime.Runtime.create_agent` for the underlying
             creation path.
           - :meth:`flowing.agent.Agent.invoke_subagent` for the Agent-side LLM
             invocation wrapper, which workflows do not provide.
        """
        ...

    async def tool_call(self, tool_name: str, **args: Any) -> ToolResult:
        """Call a registered tool directly with this workflow's hooks.

        Unlike ``Agent.tool_call``, this method takes a canonical tool name
        and individual keyword arguments. It looks up that name directly in
        ``runtime.tool_registry``, creates a ``ToolCall``, dispatches this
        workflow's ``before_tool_call`` and ``after_tool_call`` hooks, and
        invokes the tool with ``caller=None``. Because orchestration code is
        making the call directly rather than following an LLM decision, this
        path has no aliases, ``ToolEntry`` overrides, argument aggregation, or
        injection-based filling. ``args`` are all the arguments supplied to
        the tool. Caller and Agent hooks do not run here.

        .. rubric:: Example

        .. code-block:: python

           async def run(self, prompt: str | None = None) -> dict:
               # _audit_call is a two-argument handler (workflow, call).
               self.hooks.before_tool_call(self._audit_call, by="wf-audit")
               diff = await self.tool_call("read-file", path="src/auth.py")
               return {"diff": diff.output}

        .. rubric:: Behavioral notes

        - The call first resolves ``tool_name`` in ``tool_registry`` and
          creates a ``ToolCall`` with a framework-generated
          ``workflow-<8 hex digits>`` ID for tracing. It dispatches
          ``before_tool_call`` with the ``ToolCall``; a handler can rewrite
          ``args``, set ``shortcut`` to supply a result directly, or raise
          ``Intercepted`` to prevent the tool from running and return a
          blocked ``ToolResult``. It then dispatches ``after_tool_call`` with
          the ``ToolResult``; a handler can modify it or raise ``Intercepted``
          to replace it with a blocked result. Before returning, output is
          normalized idempotently, covering both shortcut results and changes
          made by after-call handlers.
        - ``status`` has the same four values as the Agent path:
          ``completed``, ``pending``, ``blocked``, or ``error``. ``output`` is
          always normalized to one of the five supported forms, as it is for
          ``Agent.tool_call``.
        - The method does not look up aliases or ``_tool_entries`` and does
          not aggregate parameters. An unregistered name raises
          :class:`flowing.errors.ToolNotFoundError`.
        - If the tool declares a ``caller`` parameter, it receives ``None``.
          Tools that depend on a caller are therefore unavailable on this path;
          the framework does not pre-check this constraint.

        :param tool_name: Canonical tool name, used as the registry key.
        :param args: Tool parameters passed unchanged as ``ToolCall.args``.
        :return: The tool execution result.
        :raises flowing.errors.ToolNotFoundError: If ``tool_name`` is not
            registered.

        .. seealso::

           - :meth:`flowing.agent.Agent.tool_call` for the Agent-side method,
             which accepts a ``ToolCall``, resolves aliases, and aggregates
             parameters.
           - :class:`flowing.tool.ToolRegistry`
           - :class:`flowing.tool.ToolResult`
        """
        ...

    def provide(self, key: str | InjectionKey[Any], value: Any) -> None:
        """Provide a value to this workflow's child Agents and their
        descendants.

        The value is stored in this workflow's provide table. Child Agents
        created before or after the call can find it when ``inject(key)`` walks
        up the chain. Providing the same key again replaces the previous
        value, as with ``Agent.provide()``. Existing child Agents see updates
        too, because injection walks the chain at lookup time rather than
        copying values at creation. This does not affect the caller or other
        workflow subtrees, and it does not fill parameters for tool calls or
        child-Agent creation; orchestration code supplies those arguments
        directly.

        :param key: Injection key, either a string or an ``InjectionKey``;
            keys are normalized by name.
        :param value: Value to provide.

        .. seealso:: :meth:`inject`, :class:`flowing.runtime.ProvideNode`
        """
        ...

    def inject(self, key: str | InjectionKey[Any]) -> Any:
        """Look up a value by walking up the provide chain.

        Lookup starts in this workflow, then follows the caller's chain to the
        Runtime. If no node provides the key, it raises
        :class:`flowing.errors.MissingProvideError`. An ``InjectionKey`` is
        matched across nodes by its ``name``; its type information does not
        cross node boundaries.

        :param key: Injection key, either a string or an ``InjectionKey``;
            keys are normalized by name.
        :return: The value associated with the key.
        :raises flowing.errors.MissingProvideError: If the key is absent from
            the entire chain.

        .. seealso:: :meth:`provide`, :func:`flowing.runtime.inject_from`
        """
        ...

    async def destroy(self) -> None:
        """Archive remaining child Agents and unregister this workflow.

        The method awaits ``runtime.archive_agent(child_id)`` for each
        remaining child. Archiving removes a child from ``_nodes``,
        ``_agent_pool``, and the core registry, but retains its session file.
        It then clears the child table and unregisters this workflow node.
        Calling ``destroy()`` repeatedly is safe and has no effect after the
        first cleanup.

        This method does not delete child session files and does not cancel
        other workflow instances launched by :class:`RunWorkflowTool`. Each
        invocation creates an independent workflow object. If ``run()`` is
        still executing when destruction begins, its child Agents are archived
        and outstanding awaits may receive cancellation or an error because
        the Agent has been destroyed. Orchestration code is responsible for
        choosing a safe destruction time.

        .. seealso::

           - :meth:`flowing.agent.Agent.destroy`
           - :meth:`flowing.runtime.Runtime.archive_agent`
           - :meth:`create_agent`
        """
        ...
