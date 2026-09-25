"""Define provide-inject nodes and lookup through their parent scopes.

.. rubric:: Overview

This module defines :class:`ProvideNode`, the protocol shared by
``Runtime``, ``Agent``, and ``Workflow``, and :func:`inject_from`, the lookup
operation those node implementations use. Each node stores values in its own
scope. Lookup starts at the requesting node, then checks parent scopes in
order until it finds the key or reaches the ``Runtime`` endpoint.

Application code normally calls ``node.provide(key, value)`` and
``node.inject(key)``. Re-providing a key replaces its value, and subsequent
lookups see the replacement. Values can be read by descendants, so this API
must not be used to store credentials or other sensitive data.

.. seealso:: :class:`ProvideNode`, :func:`inject_from`,
   :mod:`flowing.runtime`, :class:`flowing.params.InjectionKey`,
   :class:`flowing.errors.MissingProvideError`
"""
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from flowing.runtime import Runtime

__all__ = ["ProvideNode", "inject_from"]


@runtime_checkable
class ProvideNode(Protocol):
    """Protocol implemented by nodes in the provide-inject scope chain.

    ``Runtime`` is the chain endpoint; ``Agent`` and ``Workflow`` are scoped
    nodes. The protocol requires ``node_id``, ``_provided``, and ``runtime``
    attributes, as well as ``provide`` and ``inject`` methods. Because the
    protocol is runtime-checkable, ``isinstance(node, ProvideNode)`` can check
    whether an object exposes those members.

    .. rubric:: Example

    .. code-block:: python

        def configure(node: ProvideNode) -> None:
            node.provide("locale", "en")
            assert isinstance(node, ProvideNode)

    .. rubric:: Behavior

    - ``provide`` writes to the current node's scope. Providing the same key
      again replaces its previous value, and later calls to ``inject`` see the
      replacement because lookup is not cached.
    - ``inject`` searches from the current node toward the ``Runtime``
      endpoint and raises :exc:`flowing.errors.MissingProvideError` if no node
      provides the key.
    - The protocol does not include lifecycle methods such as ``destroy``;
      those methods depend on the concrete node type.

    .. seealso:: :func:`inject_from`, :class:`flowing.runtime.Runtime`,
       :class:`flowing.agent.Agent`, ``flowing.plugins.workflow.Workflow``
    """
    node_id: str
    """Unique identifier in the shared node-ID space. IDs are prefixed by node
    type, such as ``runtime-0``, ``workflow-<8 hex digits>``, or
    ``agent-<6 hex digits>``. The Runtime ID is always ``runtime-0``. Use
    :meth:`flowing.runtime.Runtime.get_node` to resolve an ID to its node.
    """
    _provided: dict[str, Any]
    """Storage for values provided at this node. Keys are strings;
    ``InjectionKey[T]`` is a static annotation whose type parameter is not
    carried across nodes. Implementations must provide this protocol member. Use
    ``provide`` and ``inject`` rather than reading or writing this attribute
    directly.
    """
    runtime: "Runtime"
    """The Runtime that terminates this node's provide-inject chain. An Agent
    or Workflow uses it to resolve parent nodes; the Runtime's own ``runtime``
    attribute refers to itself.
    """

    def provide(self, key: str, value: Any) -> None:
        """Register or replace a value in this node's scope.

        The current node and descendants that search through it can retrieve
        the value.

        :param key: String key used to retrieve the value.
        :param value: Object to make available through injection.

        .. rubric:: Behavior

        Providing an existing key replaces its value. Each lookup is performed
        afresh, so a subsequent ``inject(key)`` immediately sees the new value.
        Do not store API keys or other credentials here because descendants can
        read values exposed along their scope chain.

        .. seealso:: :meth:`inject`, :func:`inject_from`
        """
        ...

    def inject(self, key: str) -> Any:
        """Find and return ``key`` from the nearest scope that provides it.

        :param key: String key to look up.
        :return: The value registered at the nearest matching node.
        :raises flowing.errors.MissingProvideError: No node in the chain,
            including the Runtime endpoint, provides the key.

        Lookup starts at this node and proceeds toward its parents, so a value
        in a nearer scope takes precedence over a value with the same key in a
        farther scope. Every call performs a fresh lookup and sees recent
        ``provide`` updates. If a parent ID cannot be resolved, lookup treats
        the broken chain as a missing key; a value already registered at this
        node can still be returned.

        This runtime lookup error is distinct from
        :exc:`flowing.errors.DependencyError`, which reports a static plugin
        dependency failure during setup.

        .. seealso:: :func:`inject_from`
        """
        ...


def inject_from(runtime: "Runtime", node: ProvideNode, key: str) -> Any:
    """Look up ``key`` by following the provide-inject chain from ``node``.

    This shared operation checks each scope from nearest to farthest and stops
    at the first match. Node implementations use it to implement
    ``node.inject(key)``; application code normally calls that method instead
    of invoking ``inject_from`` directly.

    :param runtime: Runtime at the end of the scope chain, used to resolve
        parent node IDs.
    :param node: Node where lookup begins.
    :param key: String key to find.
    :return: The value registered at the nearest matching node.
    :raises flowing.errors.MissingProvideError: The chain reaches its Runtime
        endpoint without a match, or a parent link cannot be resolved before a
        match is found.

    .. rubric:: Behavior

    Lookup is performed on every call, so updates made with ``provide`` are
    visible immediately. If a parent link is broken, the lookup stops and
    raises :exc:`flowing.errors.MissingProvideError`; a value provided at the
    starting node is returned before any parent lookup is needed.

    .. seealso:: :class:`ProvideNode`,
       :exc:`flowing.errors.MissingProvideError`,
       :meth:`flowing.runtime.Runtime.get_node`
    """
    ...
