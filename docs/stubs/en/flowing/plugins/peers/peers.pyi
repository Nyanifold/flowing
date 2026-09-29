"""Peers plugin registration, routing, and per-agent catalog injection.

The plugin registers ``query-peer``, ``message-peer``, and ``steer-peer`` in
the default tool namespace and provides a stateless router to the Runtime.
Each Agent supplies its own allowlist as a non-empty ``peers`` mapping whose
keys are exact Agent IDs and whose values are non-empty descriptions.

The catalog is rendered as a dynamic system-prompt block. Descriptions are
inserted as literal configuration text and are not parsed as Parsable
templates. The Composable neither binds tools nor changes tool entries.

``use_peers()`` first injects the router, so a Runtime without
``PeersPlugin`` raises ``MissingProvideError``. It then validates the mapping
and adds a dynamic system-prompt block with ``by="peers"`` and the
``peers.catalog`` tag. Target lookup is deferred until a tool call; sleeping
Agents can be recovered through ``Runtime.get_agent()``.

.. rubric:: Example

.. code-block:: yaml

    tools:
      - message-peer
    peers:
      oracle: "Knows the answer and replies to questions."
    ---
    $script:
    from flowing.plugins.peers import use_peers

    async def setup(self):
        use_peers(self)
"""

from typing import ClassVar
from flowing.agent import Agent
from flowing.params import InjectionKey
from flowing.plugins import Plugin
from flowing.runtime import Runtime


class PeerRouter:
    """Validate declared peer IDs and resolve targets in the caller's Runtime."""

    def validate_peer(self, caller: Agent, peer_id: str) -> None:
        """Check that ``peer_id`` is declared and is not the caller itself.

        :raises ValueError: The catalog is invalid, the peer is undeclared, or
            the peer ID is the caller's own ID.
        """
        ...

    async def resolve_peer(self, caller: Agent, peer_id: str) -> Agent:
        """Validate the allowlist and resolve or recover the target Agent.

        :raises ValueError: The target is undeclared or absent from the Runtime.
        """
        ...


peers_router_key: InjectionKey[PeerRouter]
"""Provide/inject key for the Runtime-scoped peer router."""


class PeersPlugin(Plugin):
    """Register the three Peers tools and provide the Runtime peer router.

    Install this plugin before mounting Agents. Registered tools still need
    explicit per-agent bindings to become visible to an LLM.
    """

    name: ClassVar[str] = "peers"
    """Plugin registration name."""

    dependencies: ClassVar[list[str]] = []
    """Plugins required by Peers."""

    def install(self, runtime: Runtime) -> None:
        """Provide the router and register ``query-peer``, ``message-peer``, and ``steer-peer``."""
        ...


def use_peers(agent: Agent) -> None:
    """Validate ``agent.peers`` and add a dynamic peer-catalog prompt block.

    Call this from ``setup()``. The mapping must be non-empty, contain
    non-empty string IDs and descriptions, and exclude the caller's own ID.
    This function does not check whether targets are mounted or change tool
    entries.

    :raises flowing.errors.MissingProvideError: ``PeersPlugin`` is not installed.
    :raises ValueError: The peer catalog is invalid.
    """
    ...


class _PeerCatalogPrompt:
    """Render the declared peer catalog and currently visible Peers tools."""

    def resolve(self, agent: Agent) -> str:
        """Render the current peer catalog as literal prompt text."""
        ...
