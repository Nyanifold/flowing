"""Directed agent-to-agent messaging within one Runtime.

Install :class:`PeersPlugin` before mounting Agents, then call
:func:`use_peers` from each participating Agent's ``setup()``. Declare peer
IDs and descriptions in the Agent's ``peers`` field, and bind the desired
Peers tools through ``tools:``. The tools route one message at a time through
the target Agent's message queue; peer messages enter its conversation and
message tree.

Peers is independent of subagent orchestration and ``CommPlugin``. It does
not copy either Agent's private conversation history, and tool registration
does not make a tool visible to every Agent.

``query-peer`` returns a pending receipt before waiting for the target turn,
then delivers its status, final text, and finish reason as an EVENT.
``message-peer`` queues a PEER message and returns its message ID without
waiting for the target turn. ``steer-peer`` queues a PEER message at STEER
priority without interrupting the target's current turn. Every tool call
revalidates the caller's allowlist and routes by exact Agent ID in the same
Runtime. Delivered text is prefixed with ``FROM PEER <caller node id>:\n``
so the target can identify the sender. An Agent in the Runtime pool can be
recovered on demand.

The ``peers`` mapping must be non-empty, use non-empty string IDs and
descriptions, and exclude the caller's own ID. Its descriptions are inserted
into a dynamic prompt block as literal text, without Parsable template
evaluation. ``use_peers()`` does not bind or modify tools.

.. rubric:: Example

.. code-block:: python

    from flowing import Runtime
    from flowing.plugins.peers import PeersPlugin

    runtime = Runtime(persist_dir="@/.flowing")
    runtime.set_model_tags("@/model-tags.yaml")
    runtime.install(PeersPlugin())
    await runtime.mount("@/oracle.fya", agent_id="oracle")
    await runtime.mount("@/guesser.fya", agent_id="guesser")
"""

from .peers import PeersPlugin, use_peers

__all__ = ["PeersPlugin", "use_peers"]
