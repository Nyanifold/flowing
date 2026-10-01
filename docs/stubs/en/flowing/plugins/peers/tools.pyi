"""LLM tools for one-to-one peer messaging.

Each tool validates the caller's current peer allowlist and routes through
``Runtime.get_agent()``. ``query-peer`` yields a pending receipt and later an
EVENT result; ``message-peer`` and ``steer-peer`` return after enqueueing a
PEER message without waiting for the target turn. Delivered text is prefixed
with ``FROM PEER <caller node id>:\n`` so the target can identify the sender;
the prefix coexists with the ``source`` metadata.

Catalog errors occur before the query receipt. Errors raised after the
receipt, such as a missing target or a failed target turn, are delivered by
the background task as a terminal event. Tool outputs are JSON-compatible
dictionaries; ``TurnResult`` is projected to status, final text, and finish
reason before it reaches the output pipeline.
"""

from collections.abc import AsyncGenerator
from typing import Any
from flowing.agent import Agent
from flowing.tool import Tool, ToolDefinition


class QueryPeerTool(Tool):
    """Send a prompt to one declared peer and deliver its turn result later."""

    definition: ToolDefinition

    async def execute(
        self, peer_id: str, prompt: str, *, caller: Agent,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Yield an acceptance receipt, then the peer turn result."""
        ...


class MessagePeerTool(Tool):
    """Queue a message to one declared peer without waiting for its reply."""

    definition: ToolDefinition

    async def execute(self, peer_id: str, message: str, *, caller: Agent) -> dict[str, str]:
        """Return the target peer ID and queued message ID."""
        ...


class SteerPeerTool(Tool):
    """Queue a steering instruction for one declared peer."""

    definition: ToolDefinition

    async def execute(
        self, peer_id: str, instruction: str, *, caller: Agent,
    ) -> dict[str, str]:
        """Return the target peer ID and queued message ID."""
        ...
