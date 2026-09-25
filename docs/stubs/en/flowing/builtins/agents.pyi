"""``flowing.builtins.agents`` — the standard subagents in ``builtin::``.

This module provides :class:`ExploreAgent`, the shipped read-only codebase
exploration subagent registered under the name ``explore-agent`` in the
``builtin::`` namespace.

Registration does not make a subagent callable. A parent Agent must explicitly
declare it under ``subagents:`` in its ``.fya`` file or add it with
``add_agent`` before it appears in the parent's catalog for the LLM.

.. rubric:: Example

.. code-block:: yaml

    # Parent Agent's .fya file
    subagents:
      - explore-agent

.. seealso:: :mod:`flowing.builtins.tools` and
    :class:`flowing.agent.Agent`.
"""

from flowing.agent import Agent


class ExploreAgent(Agent):
    """A read-only subagent for exploring a codebase.

    .. rubric:: Overview

    A parent Agent can delegate a code-understanding task to this subagent and
    receive a text conclusion while continuing its own work. The subagent is
    bound to exactly three read-only tools: ``read``, ``grep``, and ``glob``.
    It has no structural capability to modify files or run commands, so the
    parent does not need an approval policy merely to keep this toolset
    read-only.

    The read-only boundary comes from the tools made available to the Agent,
    not from relying on the system prompt to obey a restriction. Without
    ``write``, ``edit``, or ``bash``, the subagent has no write or command
    execution capability.

    .. rubric:: Example

    .. code-block:: yaml

        # Parent Agent's .fya file
        subagents:
          - explore-agent

    .. rubric:: Behavior

    - The default toolset is ``read``, ``grep``, and ``glob``. A subclass can
      add writable tools only by explicitly overriding :meth:`setup`; after
      that change, the subclass no longer has the read-only exploration
      safety boundary.
    - By default, the subagent returns its plain-text response to the parent.
      It does not bind ``finish``; calling ``finish`` is optional for a
      subagent.
    - ``setup()`` takes no initialization arguments. Its subagent entry should
      not declare ``args:`` overrides for it.

    .. seealso:: :mod:`flowing.builtins.tools` and
        :class:`flowing.builtins.SubagentInvokeTool`.
    """

    system_prompt: str

    async def setup(self) -> None:
        """Bind ``read``, ``grep``, and ``glob`` to this Agent.

        The creation and recovery pipelines each call this method once on a
        newly created instance. Each call starts with an empty tool-entry
        table, so assembly on one instance does not affect another. See
        :meth:`flowing.agent.Agent.setup` for the per-instance lifecycle
        contract.
        """
        ...
