"""Manage ordered groups of entries that can be enabled, disabled, and removed.

.. rubric:: Overview

This module provides :class:`Togglable`, a structural contract for managed
entries, and :class:`ManagedList`, a container for grouping those entries by
tag or owner. Disabling an entry leaves it in place but excludes it from
iteration; removing an entry deletes it from the container.

The framework uses this container for hook handlers and prompt blocks.
Applications can also use it for their own entry groups. It manages only the
entry fields ``enabled``, ``by``, and ``tags``; it does not interpret other
fields.

.. rubric:: Example

.. code-block:: python

    from flowing.lists import ManagedList

    class Item:
        def __init__(self, by=None, tags=None, enabled=True):
            self.by = by
            self.tags = tags or []
            self.enabled = enabled

    entries = ManagedList()
    entries.append(Item(by="rule-a", tags=["guardrail"]))
    entries.append(Item(by="rule-b", tags=["guardrail"]))
    entries.disable_by_tag("guardrail")
    list(entries)                         # [] while both items are disabled.
    entries.enable_by_tag("guardrail")   # Both items participate again.

.. rubric:: Behavior notes

- Disabling an entry sets its ``enabled`` field to ``False`` but leaves it in
  the container, in the same registration order, so it can be restored.
  Removal permanently deletes it.
- Entries retain their relative registration order when disabled and
  re-enabled.
- Owner matching uses exact equality (``item.by == owner``), including
  ``None`` matching ``None``. Tag matching succeeds when any element in an
  entry's ``tags`` list equals the requested tag.

.. seealso:: :class:`Togglable`, :class:`ManagedList`,
   :class:`flowing.hooks.HookList`, :class:`flowing.context.PromptBlockList`
"""
from typing import Any, Generic, Protocol, TypeVar
T = TypeVar("T")
class Togglable(Protocol):
    """Structural contract for entries managed by :class:`ManagedList`.

    An entry satisfies this protocol by exposing ``enabled``, ``by``, and
    ``tags`` attributes. Explicit inheritance is not required. The protocol is
    not decorated with ``@runtime_checkable``, so ``isinstance`` cannot be used
    to test conformance.

    .. rubric:: Attributes

    ``enabled`` is the activation state. ``by`` is an optional owner or source
    identifier. ``tags`` is the list of group labels.

    Batch operations match an owner by exact equality, including ``None``.
    Tag operations match when any item in ``tags`` equals the requested tag.

    ``ToolEntry`` and ``SkillEntry`` use ``visible`` rather than ``enabled``;
    they are Agent-level binding entries stored in alias-indexed dictionaries,
    not ``ManagedList`` entries. They do not satisfy this protocol and cannot
    use its grouped ``by`` / ``tags`` management.
    """
    enabled: bool
    """Whether the entry participates in iteration. Disabling sets this value
    to ``False`` without moving or removing the entry; enabling it sets the
    value to ``True`` and restores it to iteration.
    """
    by: str | None
    """Optional source or owner identifier used for exact-match batch
    operations. ``None`` is allowed and matches another ``None`` value.
    """
    tags: list[str]
    """Zero or more labels used by tag-based batch operations. An entry
    matches a tag when any item in this list equals the requested value.
    """
Tg = TypeVar("Tg", bound=Togglable)
class ManagedList(Generic[Tg]):
    """Manage an ordered group of toggleable entries by tag or owner.

    The container preserves the order established by :meth:`append` and
    :meth:`insert`. Iteration yields enabled entries in that order. Batch
    methods can disable, re-enable, or permanently remove matching entries.
    Each instance manages its own entries and state.

    .. rubric:: Example

    .. code-block:: python

        class Item:
            def __init__(self, by=None, tags=None, enabled=True):
                self.by = by
                self.tags = tags or []
                self.enabled = enabled

        entries = ManagedList()
        entries.append(Item(by="guard-a", tags=["guardrail"]))
        entries.append(Item(by="guard-b", tags=["guardrail"]))
        entries.disable_by_tag("guardrail")  # Returns 2.
        entries.disable_by_tag("guardrail")  # Returns 0; neither item changed.
        entries.enable_by_tag("guardrail")   # Returns 2.
        entries.remove_by_owner("guard-a")   # Returns 1.

    .. rubric:: Behavior

    - Disabling changes an entry's ``enabled`` value to ``False`` without
      removing or moving it. The matching ``enable_*`` method restores it at
      its existing position.
    - Removing deletes matching entries permanently, whether they are enabled
      or disabled.
    - Tag matching succeeds when any value in an entry's ``tags`` list equals
      the requested tag. Owner matching uses exact equality, so
      ``remove_by_owner(None)`` matches entries whose ``by`` value is
      ``None``.
    - Enable and disable methods count only entries whose state changes.
      Repeating an operation when all matches are already in the target state
      returns ``0``. Remove methods count all matching entries and return
      ``0`` when there are no matches.
    - Disabling and enabling do not change order. Removing an entry changes
      the positions of later entries.

    .. seealso:: :class:`Togglable`, :class:`flowing.hooks.HookList`,
       :class:`flowing.context.PromptBlockList`
    """
    _items: list[Tg]
    """Internal storage for all entries, including disabled ones. This
    underscore-prefixed attribute is not a stable contract; use iteration to
    access active entries because it skips disabled ones.
    """
    def __init__(self) -> None:
        """Create an empty container."""
        ...
    def append(self, item: Tg) -> Tg:
        """Append an entry and return that same entry.

        The appended entry participates in later batch operations and
        iteration. Iteration follows the container's entry order.

        :param item: The entry to append.
        :return: The appended entry, allowing the call to be chained.

        .. rubric:: Behavior

        The method does not deduplicate entries; the same object can be
        appended more than once and each occurrence is processed separately.
        Subclasses may specialize this method to construct and append entries;
        in such a subclass, this base ``append(item)`` form may not be
        available.

        .. seealso:: :meth:`insert`, :class:`flowing.hooks.HookList`,
           :class:`flowing.context.PromptBlockList`
        """
        ...
    def insert(self, index: int, item: Tg) -> Tg:
        """Insert an entry at ``index`` and return that same entry.

        Index handling follows ``list.insert``: negative indexes count from
        the end, and indexes outside the bounds are clamped to the beginning
        or end. Entries after the insertion point move one position to the
        right.

        :param index: The insertion position, which may be negative.
        :param item: The entry to insert.
        :return: The inserted entry.

        .. rubric:: Behavior

        The inserted entry participates in batch operations and iteration.
        The container does not deduplicate entries. Subclasses may specialize
        this method to construct and insert entries, in which case the base
        ``insert(index, item)`` form may not be available.

        .. seealso:: :meth:`append`
        """
        ...
    def disable_by_tag(self, tag: str) -> int:
        """Disable enabled entries whose ``tags`` contain ``tag``.

        :param tag: The tag to match. An entry matches if any item in its
            ``tags`` list equals this value.
        :return: The number of entries changed from enabled to disabled; this
            is ``0`` when no matching entry changes state.

        .. rubric:: Behavior

        Disabling sets ``enabled`` to ``False`` but keeps the entry in the
        container at the same position. A later call to
        :meth:`enable_by_tag` can restore it. Entries already disabled are
        neither changed nor counted, so repeating the same call returns ``0``.

        .. seealso:: :meth:`enable_by_tag`, :meth:`remove_by_tag`
        """
        ...
    def enable_by_tag(self, tag: str) -> int:
        """Enable disabled entries whose ``tags`` contain ``tag``.

        :param tag: The tag to match. An entry matches if any item in its
            ``tags`` list equals this value.
        :return: The number of entries changed from disabled to enabled; this
            is ``0`` when no matching entry changes state.

        .. rubric:: Behavior

        Enabling sets ``enabled`` to ``True`` without moving the entry.
        Entries already enabled are not changed or counted, so repeating the
        same call returns ``0``.

        .. seealso:: :meth:`disable_by_tag`, :meth:`remove_by_tag`
        """
        ...
    def remove_by_tag(self, tag: str) -> int:
        """Permanently remove entries whose ``tags`` contain ``tag``.

        :param tag: The tag to match. An entry matches if any item in its
            ``tags`` list equals this value.
        :return: The number of removed entries, or ``0`` if there are no
            matches.

        .. rubric:: Behavior

        Removal is permanent; removed entries cannot be restored with
        :meth:`enable_by_tag`. The method removes every match regardless of
        whether it is enabled or disabled. If there are no matches, the
        container is unchanged.

        .. seealso:: :meth:`disable_by_tag`
        """
        ...
    def disable_by_owner(self, owner: str | None) -> int:
        """Disable enabled entries whose ``by`` value equals ``owner``.

        :param owner: The owner or source identifier to match. Matching uses
            exact equality; ``None`` matches entries whose ``by`` is ``None``.
        :return: The number of entries changed from enabled to disabled; this
            is ``0`` when no matching entry changes state.

        .. rubric:: Behavior

        Disabling sets ``enabled`` to ``False`` but keeps each entry in the
        container at its current position. A later call to
        :meth:`enable_by_owner` can restore it. Already-disabled matches are
        not changed or counted, so repeating the call returns ``0``.

        .. seealso:: :meth:`enable_by_owner`, :meth:`remove_by_owner`
        """
        ...
    def enable_by_owner(self, owner: str | None) -> int:
        """Enable disabled entries whose ``by`` value equals ``owner``.

        :param owner: The owner or source identifier to match. Matching uses
            exact equality; ``None`` matches entries whose ``by`` is ``None``.
        :return: The number of entries changed from disabled to enabled; this
            is ``0`` when no matching entry changes state.

        .. rubric:: Behavior

        Enabling sets ``enabled`` to ``True`` without moving each entry.
        Already-enabled matches are not changed or counted, so repeating the
        call returns ``0``.

        .. seealso:: :meth:`disable_by_owner`, :meth:`remove_by_owner`
        """
        ...
    def remove_by_owner(self, owner: str | None) -> int:
        """Permanently remove entries whose ``by`` value equals ``owner``.

        :param owner: The owner or source identifier to match. Matching uses
            exact equality; ``None`` matches entries whose ``by`` is ``None``.
        :return: The number of removed entries, or ``0`` if there are no
            matches.

        .. rubric:: Behavior

        Removal is permanent; removed entries cannot be restored with
        :meth:`enable_by_owner`. The method removes every match regardless of
        its enabled state. If there are no matches, the container is unchanged.

        .. seealso:: :meth:`disable_by_owner`
        """
        ...
    def __iter__(self):
        """Iterate over enabled entries in their existing order.

        Disabled entries remain in the container but are skipped. Consumers
        such as hook dispatch and context assembly use this iteration behavior
        to read active entries. Reading the internal ``_items`` storage directly
        bypasses this filtering; extensions should use this iterator to access
        active entries.

        :return: An iterator yielding entries whose ``enabled`` value is
            ``True``.
        """
        ...
    def __len__(self) -> int: ...
