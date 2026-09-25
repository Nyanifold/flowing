"""``flowing.tool.script`` — the ``ScriptTool`` author base class and ``@flowing_tool`` marker.

These APIs let a tool author define Python-backed tools. The module provides
both a class-based declaration and a function-marking path.
"""
from collections.abc import Callable
from typing import Any
from pydantic import BaseModel
from flowing.tool.core import Tool

class ScriptTool(Tool):
    """Author base class for script tools declared with class attributes.

    .. rubric:: Overview

    A subclass declares ``name``, ``description``, and optionally
    ``args_model``; construction generates ``self.definition``. ``name`` is
    required for a hand-written subclass and is not inferred from the class
    name. A subclass may instead declare ``definition`` explicitly. When
    loaded from a ``.fya`` or ``.py`` file, the file identity (its file or
    directory name) supplies the canonical name; an explicit class ``name``
    that disagrees with that identity raises
    :class:`flowing.errors.NameMismatchError`. If ``args_model`` is omitted,
    Flowing builds a model from the ``execute()`` parameter annotations and
    defaults.

    Script tools have three equivalent definition paths: a hand-written
    ``ScriptTool`` subclass, a function marked with ``@flowing_tool`` and
    promoted to an equivalent subclass, or a bare function referenced by a
    ``callable:`` field in a ``.fya`` file. For file-based paths, the file or
    directory identity is authoritative. The ``description`` is selected in
    this order: explicit declaration, the entire class docstring, then the
    entire ``execute()`` docstring.

    .. rubric:: Example

    .. code-block:: python

        from flowing import Agent, ScriptTool
        from pydantic import BaseModel, Field

        class PayArgs(BaseModel):
            order_id: str = Field(description="Order ID")
            amount: float = Field(ge=0.01)

        class MakePayment(ScriptTool):
            '''Charge an order after the user confirms the payment.'''

            name = "make-payment"
            description = "Charge a payment."
            args_model = PayArgs

            async def execute(self, *, order_id: str, amount: float,
                              caller: Agent) -> dict:
                ...

    An asynchronous generator can implement a background tool. Its first
    ``yield`` signals that preparation is complete; ``Tool.__call__`` waits
    for it and returns it in the ``pending`` receipt, whose TOOL message
    carries that content. Each later ``yield`` is delivered as an ``EVENT``
    message. Keep work before the first ``yield`` lightweight; lengthy work
    delays the receipt. A regular asynchronous ``execute`` method can instead
    set the class attribute ``background = True`` when it should run in the
    background without intermediate reports. This flag applies only to
    ``ScriptTool`` subclasses.

    .. code-block:: python

        from flowing import Agent, ScriptTool

        class RunWorkflowTool(ScriptTool):
            name = "run-workflow"

            async def execute(self, *, path: str, caller: Agent, **args):
                workflow = resolve_workflow(path)  # Application-provided helper.
                yield {"status": "started", "workflow": path}
                await workflow.run(**args)
                yield {"status": "done", "workflow": path}

    An async generator cannot return a value; its final visible value is its
    last ``yield``. A function with no ``yield`` is not an async generator and
    follows the ordinary execution path. An async generator does not need the
    ``background`` flag.

    .. rubric:: Behavior

    - A subclass must not combine the class attributes ``name``,
      ``description``, or ``args_model`` with an explicit ``definition``.
      When both forms are present, Flowing logs a warning and uses
      ``definition``.
    - A function referenced through ``callable:`` must not also be marked by
      ``@flowing_tool``. Combining the explicit-pointer and auto-promotion
      paths raises :class:`flowing.errors.FormatError`.
    - Script tools are registered as Runtime-wide singletons shared by all
      Agents. The same user-defined tool should not be instantiated multiple
      times.
    - A ``.py`` file may contain at most one marked function. A marked function
      and a ``Tool`` subclass in the same file, or multiple marked functions,
      raise :class:`flowing.errors.AmbiguousToolError` during file resolution.
    - Class attributes are available before ``__init__`` runs.

    :raises flowing.errors.MissingSchemaError: No ``args_model`` is declared
        and one or more ``execute()`` parameters lack type annotations needed
        to build the schema.
    :raises flowing.errors.NameMismatchError: In a file-loading path, an
        explicit class ``name`` disagrees with the file or directory identity.
        Direct construction instead raises
        :class:`flowing.errors.MissingFieldError` if the required ``name`` is
        absent.

    .. seealso::

        - :func:`flowing.tool.flowing_tool` for the marked-function path.
        - :mod:`flowing.params` for parameter declarations and schema
          conversion.
    """
    name: str
    """The required canonical tool name in kebab case.

    A hand-written subclass declares it explicitly; file-based ``.fya`` and
    ``.py`` loading supplies the file or directory identity. A conflicting
    explicit value raises :class:`flowing.errors.NameMismatchError`. This
    attribute cannot be combined with an explicit ``definition``.
    """
    description: str
    """The tool description declared on the class.

    If it is omitted, Flowing uses the entire class docstring, then the entire
    ``execute()`` docstring.
    """
    args_model: type[BaseModel] | None
    """The Pydantic ``BaseModel`` subclass that declares tool arguments.

    The model defines both the LLM schema and execution-time validation. If it
    is ``None``, Flowing builds a model from ``execute()``'s signature.
    """

    def __init__(self) -> None:
        """Generate ``self.definition`` synchronously without registering or connecting.

        .. rubric:: Behavior

        - If the class explicitly defines ``definition``, construction uses it
          directly. If ``name``, ``description``, or ``args_model`` is also
          defined, Flowing logs a warning and still gives precedence to
          ``definition``.
        - Otherwise, the class must explicitly define ``name``; the class name
          is not used as a fallback. A missing name raises
          :class:`flowing.errors.MissingFieldError`. The description is chosen
          from the explicit attribute, the entire class docstring, or the
          entire ``execute()`` docstring, in that order; if all are absent, it
          is an empty string. If no argument model is declared,
          ``_infer_from_execute(self.execute)`` builds one.
        - Flowing then creates a :class:`flowing.tool.ToolDefinition` whose
          ``params_schema`` comes from
          ``args_model.model_json_schema()["properties"]``. On return,
          ``self.definition`` is set and the ``execute`` signature has been
          checked for a ``caller`` parameter.
        """
        ...

def flowing_tool(fn: Callable[..., Any] | None = None, *, name: str | None = None) -> Any:
    """Mark a bare function as a script tool without registering it.

    This is one of the two script-tool declaration paths; the other is a
    hand-written :class:`ScriptTool` subclass. The decorator records a marker
    on the function but does not instantiate or register a tool. The function
    is promoted and registered only when :meth:`flowing.tool.ToolRegistry.get`
    resolves it. Marking therefore does not require a Runtime at import time
    and works with multiple Runtime instances.

    Use ``@flowing_tool`` to infer the name from the file or directory, or
    ``@flowing_tool("make-payment")`` to provide a name assertion. The
    supplied name must match the inferred name or resolution raises
    :class:`flowing.errors.NameMismatchError`. Marking explicitly records the
    author's intent for the function to be a tool.

    .. rubric:: Behavior

    - A ``.py`` file may contain at most one marked function. A marked
      function alongside a ``Tool`` subclass, or multiple marked functions,
      raises :class:`flowing.errors.AmbiguousToolError`.
    - A function referenced by ``callable: {path}::{function}`` in a
      ``TOOL.fya`` file must not be marked. These are mutually exclusive
      declaration paths; combining them raises
      :class:`flowing.errors.FormatError`.
    - Name inference converts the filename without ``.py`` from snake case to
      kebab case. For generic names such as ``TOOL.py`` or ``tool.py``, the
      directory name is used.
    - The decorator returns the original function. It does not instantiate a
      tool or access a Runtime or registry.

    :param fn: The function being decorated. Python supplies it for the
        ``@flowing_tool`` form without arguments.
    :param name: An optional assertion against the inferred name. ``None``
        requests inference without an assertion.

    .. seealso::

        - :class:`flowing.tool.ScriptTool` for the class-based declaration.
        - :meth:`flowing.tool.ToolRegistry.get` for the resolution point.
        - :class:`flowing.errors.NameMismatchError` for a failed name
          assertion.
    """
    ...

def _auto_generate_tool(fn: Callable[..., Any]) -> Tool: ...
_FLOWING_TOOL_MARKS: set[Callable[..., Any]]
