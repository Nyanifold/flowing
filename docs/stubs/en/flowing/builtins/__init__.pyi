"""``flowing.builtins`` — the shipped tools and standard subagents in ``builtin::``.

.. rubric:: Overview

This package contains the capabilities shipped with Flowing. Runtime
construction registers them in ``builtin::`` through
:func:`register_builtins`:

- :mod:`flowing.builtins.tools` provides the core ``subagent-invoke`` entry
  point and the optional ``finish`` tool, plus the standard file and shell
  tools ``read``, ``write``, ``bash``, ``edit``, ``grep``, and ``glob``.
- :mod:`flowing.builtins.agents` provides ``ExploreAgent``, a read-only
  codebase exploration subagent.

.. rubric:: Shared behavior

Registration does not make a capability visible to the LLM. An Agent's tool
catalog (``Context.tools``) contains only tools explicitly declared in its
``.fya`` ``tools:`` list or added with ``add_tool``. In particular, tools that
write files or execute commands are exposed only when the user explicitly
declares them. This is a safety boundary, not an omission. Similarly, a parent
Agent must declare ``ExploreAgent`` under ``subagents:`` or add it with
``add_agent`` before it appears in the parent's catalog.

Bare-name lookup checks ``default::`` before ``builtin::``. Plugins and
applications can therefore register a same-named tool in ``default::`` to
override builtin behavior; the builtin remains available by its fully
qualified name, such as ``builtin::read``.

.. rubric:: Example

An Agent's ``.fya`` file explicitly lists the tools it may use:

.. code-block:: yaml

    tools:
      - read
      - grep
      - glob
      - bash

.. seealso::

    - :mod:`flowing.builtins.tools` for the shipped tools.
    - :mod:`flowing.builtins.agents` for the standard subagent.
    - :mod:`flowing.tool` for namespace and bare-name lookup rules.
"""

from typing import Any

from .agents import ExploreAgent
from .tools import (
    TOOL_NAMING,
    BashTool,
    EditTool,
    FinishTool,
    GlobTool,
    GrepTool,
    ReadTool,
    SubagentInvokeTool,
    WriteTool,
)


def register_builtins(runtime: Any) -> None:
    """Register all shipped builtin tools and the standard subagent.

    ``Runtime.__init__`` is the intended call site. This function registers
    eight tools through ``runtime.register_tool(...,
    namespace="builtin")`` and registers ``ExploreAgent`` through
    ``runtime.register_agent_type(ExploreAgent, namespace="builtin")``. When
    no name is provided, the Agent type name is inferred as ``explore-agent``.

    .. rubric:: Behavior

    - Registration updates registries only. It does not change any Agent
      instance or make a tool visible to an LLM; visibility requires an
      explicit Agent-level declaration.
    - The Runtime is expected to call this once during construction. Calling
      it again on the same Runtime raises
      :class:`flowing.errors.ToolNameConflictError` because the builtin tool
      names are already registered.

    .. seealso:: :mod:`flowing.builtins.tools`,
        :mod:`flowing.builtins.agents`, and
        :meth:`flowing.runtime.Runtime.register_tool`.
    """
    ...
