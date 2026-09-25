"""``flowing.tool`` — the tool subsystem and its three-layer capability description.

.. rubric:: Overview

This package re-exports the public types for all three tool layers:

- Executable objects: the :class:`Tool` and :class:`ScriptTool` author bases,
  plus the declaration-driven :class:`McpTool`, :class:`CliTool`, and
  :class:`RequestTool` implementations.
- LLM-visible declarations: :class:`ToolDefinition`.
- Agent-level bindings: :class:`ToolEntry`.
- Calls and results: :class:`ToolCall` and :class:`ToolResult`, with four
  result states described by :data:`ToolStatus`.
- Registry and type identifiers: :class:`ToolRegistry` and :data:`ToolType`.
- Function marking and result conversion: :func:`flowing_tool`,
  :func:`normalize_output`, and :func:`output_to_blocks`.
- Media carriers and converters: :class:`Image`, :class:`File`,
  :class:`Audio`, :class:`Video`, :class:`MediaConverter`, and
  :func:`register_media_converter`. These are defined in
  :mod:`flowing.media` and re-exported here.

Builtin tools such as ``SubagentInvokeTool`` and ``FinishTool`` are defined in
:mod:`flowing.builtins`, not in this package.

The same three-layer capability description is used for tools, subagents, and
skills. Each layer can evolve independently:

.. list-table::
   :header-rows: 1

   * - Layer
     - Tool
     - Subagent
     - Skill
   * - Executable object
     - :class:`Tool` with ``execute()``
     - Agent class
     - Skill content
   * - LLM-visible declaration
     - :class:`ToolDefinition`
     - Catalog XML entry
     - Catalog XML entry
   * - Agent-level binding
     - :class:`ToolEntry`
     - ``SubagentEntry``
     - ``SkillEntry`` (extension)

The binding layer lets the same executable tool have a different LLM-visible
view for each Agent. For example, different Agents can expose different
structured fields for ``FinishTool``. ``ToolEntry`` applies these overrides
without modifying the global registry or the executable object.

Public signatures in this package are stable across versions. Symbols with a
leading underscore are internal APIs and are not stable contracts.

.. rubric:: Shared call behavior

When the LLM emits a tool call during a logical turn, Flowing processes it in
this order:

1. ``Agent.tool_call`` dispatches ``before_tool_call``. A handler can modify
   the ``ToolCall`` arguments, place a ``ToolResult`` in ``shortcut`` to skip
   execution, or raise ``Intercepted`` to block execution.
2. Flowing looks up the Agent's binding by its alias only. If no binding
   exists, it raises ``UnknownToolError``. The binding then aggregates the
   arguments.
3. Flowing invokes the executable object through ``Tool.__call__``.
4. Flowing dispatches ``on_tool_yields`` for each non-blocked result produced
   by the tool.
   A synchronous result produces one dispatch. A background tool produces
   dispatches for its receipt, each streamed segment, and any identifiable
   terminal result or notification. Each value is a ``ToolResult`` carrying
   ``name``, ``tool_call_id``, and ``production`` metadata; handlers can
   modify its output value.
5. Flowing dispatches ``after_tool_call`` and then idempotently normalizes the
   final result before returning it.

See :mod:`flowing.agent` for the complete timing contract and
:mod:`flowing.hooks` for hook behavior.

.. rubric:: Namespace and visibility

Builtin tools are registered in ``builtin::``. Bare-name lookup checks
``default::`` before ``builtin::``. A plugin or application can register a
same-named tool in ``default::`` to override builtin behavior; the builtin can
still be referenced explicitly as ``builtin::name``. Registration does not
make a tool visible to an Agent. An Agent's tool catalog contains only entries
explicitly declared in its ``.fya`` file or added through ``add_tool``.
Writable-file and command-execution tools must be explicitly declared; the
framework does not expose them merely because they are registered.

.. rubric:: Parameter precedence and errors

Specified values, including injection expressions, take precedence over LLM
arguments; schema defaults have the lowest precedence. Specified values are
hidden from the LLM and cannot be overridden by it. They are suitable for
fixed values or host-provided context such as user and trace IDs. An injection
expression such as ``{{ self.inject('key') }}`` is evaluated at call time in
the calling Agent's context and searches the provide-inject chain. If no
provider supplies the key, Flowing raises ``MissingProvideError``.

An ordinary exception from ``execute`` becomes a ``ToolResult`` with
``status="error"``. The LLM can see that result, and it does not trigger an
error hook. Raising ``Intercepted`` from a hook or ``execute`` instead
produces a blocked result. Programming errors—such as missing parameter type
annotations, forbidden result blocks, or unsupported return values—use the
framework error channel and propagate as framework exceptions such as
``ValueError`` or ``FormatError`` rather than becoming LLM-visible results.

Approval is policy, not an automatic tool behavior. A ``before_tool_call``
handler can await approval, return the original or modified ``ToolCall`` when
approved, or raise ``Intercepted`` when rejected. Fields such as
``requires_approval`` in a ``.fya`` declaration are ordinary attributes; the
framework does not interpret them.

Tool definition file lookup, ``@/`` project-root anchoring, and namespace
derivation are described by :meth:`ToolRegistry.get`. The ``@/`` context is
isolated per asyncio Task; see :mod:`flowing.runtime`.

.. rubric:: Example

.. code-block:: python

    from flowing import ScriptTool
    from pydantic import BaseModel

    class PayArgs(BaseModel):
        order_id: str
        amount: float

    class MakePayment(ScriptTool):
        '''Charge the specified order after the user confirms the payment.'''

        name = "make-payment"
        args_model = PayArgs

        async def execute(self, *, order_id: str, amount: float) -> dict:
            return {"transaction": "example", "order_id": order_id,
                    "amount": amount}

    async def main() -> None:
        tool = MakePayment()
        result = await tool({"order_id": "o1", "amount": 9.9})
        assert result.status == "completed"

.. seealso::

    - :mod:`flowing.agent` for the complete ``Agent.tool_call`` sequence.
    - :mod:`flowing.hooks` for ``before_tool_call`` and ``after_tool_call``.
    - :mod:`flowing.builtins` for shipped builtin tools.
    - :mod:`flowing.params` for parameter declarations and schema conversion.
    - :mod:`flowing.message` for tool-call and tool-result messages.
"""
from flowing.media import Audio, File, Image, MediaConverter, Video, normalize_output, output_to_blocks, register_media_converter
from flowing.tool.cli import CliTool
from flowing.tool.core import Tool, ToolCall, ToolDefinition, ToolEntry, ToolResult, ToolStatus, ToolType
from flowing.tool.mcp import McpTool
from flowing.tool.registry import ToolRegistry
from flowing.tool.request import RequestTool
from flowing.tool.script import ScriptTool, flowing_tool

__all__: list[str]
