"""Persist record streams and expose durable state through a state view.

.. rubric:: Overview

This module provides three parts of Flowing's persistence layer:

- :class:`RecordStore` specifies the five operations used by Agent and Runtime
  owners: ``submit``, ``replay``, ``drain``, ``close``, and ``sync``.
- :class:`FileRecordStore` stores one JSON record per line in a JSON Lines
  file. Submissions are queued synchronously and written by a background task.
- :class:`StateView` exposes persisted state through attribute and mapping
  access. Agents use ``agent.state`` for the default bag and
  ``agent.register_state(name)`` for named bags; Runtime uses
  ``runtime.register_state(namespace)`` and ``runtime.states`` for global
  namespaces.

An Agent's message tree is stored in ``tree.jsonl`` and its state in
``state.jsonl``. Runtime namespaces use ``<namespace>.jsonl``. Each file starts
with ``{"type": "meta", "format_version": <int>}``; every following line is
one JSON record. Message and tree-change records in ``tree.jsonl`` follow
``flowing.message.to_record`` and the five ``MessageChain`` operations. State
files use ``{"op": "set", "key": ..., "value": ...}`` and
``{"op": "delete", "key": ...}`` records. Each file has one owning
``FileRecordStore`` instance, owned by its Agent or Runtime namespace.

.. rubric:: Example

.. code-block:: python

    from pathlib import Path

    from flowing import Agent
    from flowing.persistence import FileRecordStore

    async def example(agent: Agent, session_dir: Path) -> None:
        agent.state.register("tracker_count", 0)
        agent.state.tracker_count += 1

        # FileRecordStore is also available to persistence integrations.
        store = FileRecordStore(session_dir / "tree.jsonl")
        store.submit({"op": "set", "key": "n", "value": 1})
        await store.close()

.. rubric:: Behavior

- ``submit`` immediately queues a record in memory; one background task performs
  the write. It does not report that the record has reached disk. Normal
  ``close`` drains pending records before stopping the writer. A crash can lose
  submitted but unwritten tail records; the in-memory state is also lost, so
  replay yields a self-consistent state as though those lost operations had not
  occurred.
- Owners close stores before Agent ``destroy`` or Runtime ``shutdown`` finishes.
  A complete-file rewrite must run after the queue has drained; compaction is
  performed by the background task when the queue is empty. If writing fails,
  the store becomes poisoned and every later ``submit`` synchronously re-raises
  the first write error rather than silently accepting records.
- Format versions apply to files, not individual records. Replay upgrades an
  older file one migration step at a time; records pass through unchanged for
  any step without a registered migration. It atomically rewrites the file in
  the current format after migration. A file from a newer version raises
  ``FormatVersionError`` rather than being read silently. A legacy file without
  a version header is treated as version ``0`` and follows the same migration
  chain.
- A torn final line without a terminating newline is discarded. Malformed JSON
  in an interior line raises ``CorruptionError`` and is treated as data damage.
- Last-line merging, tombstone compaction, and full state compaction preserve
  the logical result of replaying all operations in order. State files merge
  consecutive updates to the same key in place; this is enabled for
  ``state.jsonl`` and disabled for ``tree.jsonl``. Tree tombstones are
  compacted by an atomic rewrite when their count reaches the threshold
  (default ``256``), removing tombstones and the message records they mark for
  deletion. State files are fully compacted after the threshold is exceeded,
  after recovery replay, or during owner shutdown.
- Recovery replays files to rebuild in-memory state. ``TurnContext`` is not
  persisted, so there is no partial turn to resume. Ordering is guaranteed
  within one store, not across different Agent files or Runtime namespaces.

.. seealso:: :class:`flowing.agent.Agent`, :class:`flowing.runtime.Runtime`,
   :class:`flowing.message.MessageChain`, :mod:`flowing.errors`
"""
from __future__ import annotations
import asyncio
import contextlib
import json
import logging
import os
from collections import deque
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol
from flowing.errors import CorruptionError, FormatVersionError
logger: object
FORMAT_VERSION: int
"""Current version of the JSON Lines record format. New files and complete
rewrites store this value in their leading metadata record. Files without a
version record are treated as version ``0``.
"""
MIGRATIONS: dict[int, 'Any']
"""Adjacent-version migration functions. ``MIGRATIONS[v]`` upgrades a sequence
of records from version ``v`` to version ``v + 1``. Replay applies registered
steps until :data:`FORMAT_VERSION` is reached. The current table is empty.
"""
class RecordStore(Protocol):
    """Contract for a persistence backend used by Agent and Runtime owners.

    This typing protocol has no persistence runtime of its own. A backend
    implements five operations: ``submit`` queues one record,
    ``replay`` yields records in write order, ``drain`` waits for prior writes,
    ``close`` drains and stops the writer, and ``sync`` requests an atomic
    replacement with a supplied final record sequence. Records are dictionaries.

    The protocol specifies behavior, not physical layout. A backend may use a
    database or another store instead of files, but it must preserve the
    operation semantics, including atomicity for ``sync``.

    .. rubric:: Behavior

    - ``replay`` omits the leading format metadata record and yields records in
      write order. It discards an incomplete final JSON line. Version checking
      and migration happen before records are returned.
    - ``sync`` replaces the complete stored sequence with the supplied final
      sequence. The replacement is atomic: observers see either the old
      contents or the new contents, never a partial rewrite. The request may
      be queued and performed after earlier writes.

    .. seealso:: :class:`FileRecordStore`
    """
    def submit(self, record: dict) -> None:
        """Submit one record synchronously to the backend.

        Returning means the record has been accepted according to the backend's
        submission contract; a write-behind backend may not have written it yet.
        A poisoned backend re-raises its original write failure.

        :param record: A record represented as a dictionary.
        """
        ...
    def replay(self) -> Iterator[dict]:
        """Yield records in write order, omitting format metadata.

        An incomplete final JSON line is discarded. Interior corruption and
        unsupported file versions are reported by the concrete backend.

        :return: An iterator over the replayed records.
        """
        ...
    async def drain(self) -> None:
        """Wait until every record submitted before this call has been written.

        :raises: The original write error if the backend is poisoned and cannot
            satisfy the drain guarantee.
        """
        ...
    async def close(self) -> None:
        """Drain pending records and stop the writer.

        Repeated calls are safe. A concrete backend may suppress an already
        reported write failure while performing shutdown cleanup.
        """
        ...
    def sync(self, records: list[dict]) -> None:
        """Request an atomic replacement with the supplied final records.

        The caller supplies records exported from its in-memory authority; the
        backend does not reduce the existing file to derive a final state,
        because it may contain submitted but unwritten tail records and replay
        could therefore yield stale values. The request is queued like
        ``submit`` (enqueueing returns immediately)
        and is executed by the background task after the queue drains. The
        replacement is atomic: observers see either the old contents or the
        new contents, never a partially synchronized state. The backend does
        not interpret record contents. FileRecordStore's tombstone compaction
        is a separate backend operation that replays, reduces, and rewrites its
        own records.

        :param records: The final record sequence, excluding file metadata.
        """
        ...
class FileRecordStore:
    """Store a record stream as JSON Lines using queued, write-behind I/O.

    One instance manages one file. ``submit`` queues records in FIFO order, a
    single background task appends them, ``replay`` yields them in that order,
    and ``close`` drains the queue before stopping the task. ``sync`` queues an
    atomic rewrite to run after the queue drains.

    Agents and Runtime normally access state through :class:`StateView`; Agent
    message persistence also uses this store internally. Applications may use
    the class directly when integrating a persistence backend. This is the
    current concrete implementation of :class:`RecordStore`; its five public
    operations and constructor parameters form the stable integration surface.
    Agent constructs it during ``Agent.__init__`` through ``_open_stores``;
    Runtime constructs it through ``Runtime.register_state``.

    .. rubric:: Example

    .. code-block:: python

        from pathlib import Path

        from flowing.persistence import FileRecordStore

        async def example(session_dir: Path) -> None:
            store = FileRecordStore(session_dir / "tree.jsonl")
            store.submit({"op": "set", "key": "n", "value": 1})
            store.submit({"op": "delete", "key": "n"})
            await store.close()

            reader = FileRecordStore(session_dir / "tree.jsonl")
            for record in reader.replay():
                print(record)
            await reader.close()

    .. rubric:: Behavior

    - The writer starts lazily on the first ``submit``, ``sync``, or ``drain``
      call, so that first operation must run inside an active event loop.
    - Records within one instance are written in submission order. ``submit``
      returns after enqueueing, not after the disk write. ``drain`` is a
      barrier, and ``close`` normally drains before stopping the writer. The
      owner must close the store during shutdown; otherwise queued tail records
      may be silently lost.
    - After the first write failure, the store is poisoned and subsequent
      ``submit`` calls re-raise that failure. Records must be JSON-serializable;
      serialization is checked by the writer.
    - ``replay`` omits metadata. It discards a torn final line, raises
      ``CorruptionError`` for malformed interior lines, migrates older
      versions, and raises ``FormatVersionError`` for a newer version. A
      migration rewrite is deferred when replay is consumed without a running
      event loop; records are still yielded, a warning is logged, and a later
      replay can retry the rewrite.
    - When ``merge_last_line`` is enabled, consecutive ``set`` records for the
      same key may rewrite the final line in place. A ``set`` followed directly
      by a ``delete`` for that key may truncate the final line; the resulting
      record stream still preserves the same logical state. When the number of
      tree tombstones reaches ``tombstone_threshold``, the writer atomically
      removes tombstones and the message records they mark for deletion.
      Maintenance preserves the logical replay result. ``sync`` rewrites and
      tombstone compaction run through the single background task only after
      the queue is empty, so queued writes cannot race the rewrite; tombstone
      compaction is triggered by count, while the owner controls the idle-time
      conditions under which it is safe.
    - Stores do not promise ordering across separate files. The append path
      does not promise an ``fsync`` durability policy; ``sync`` uses the file
      synchronization required for an atomic replacement.

    :param path: Target file path. The owning setup pipeline ensures the parent
        directory exists.
    :param merge_last_line: Whether consecutive updates to one key can rewrite
        the last line in place. This is enabled for state files and disabled
        for ``tree.jsonl``.
    :param tombstone_threshold: Number of tree tombstones that triggers
        compaction. The default is ``256``; this parameter has no state-file
        meaning.

    .. seealso:: :class:`RecordStore`, :class:`StateView`
    """
    _path: Path
    """Target file path. Internal API."""
    _merge_last_line: bool
    """Last-line merge setting (enabled for state files and disabled for
    ``tree.jsonl``). Internal API.
    """
    _tombstone_threshold: int
    """Tombstone count that triggers compaction (default ``256``); meaningful
    only for ``tree.jsonl``. Internal API.
    """
    _poisoned: BaseException | None
    """The first write exception retained after entering the poisoned state;
    ``None`` means the store is healthy. Internal API.
    """
    def __init__(self, path: Path, *, merge_last_line: bool=False, tombstone_threshold: int=256) -> None:
        """Bind the store to a file and prepare its persistence backend.

        The writer task starts lazily when ``submit``, ``sync``, or ``drain``
        first needs it. Therefore, the first such operation must run inside an
        active event loop.

        :param path: Target file path. The owning setup pipeline ensures that
            its parent directory exists.
        :param merge_last_line: Whether consecutive updates to one key may
            rewrite the last line in place. State files enable this; tree files
            do not.
        :param tombstone_threshold: Tree tombstone count that triggers
            compaction. The default is ``256``.
        """
        ...
    def submit(self, record: dict) -> None:
        """Queue one record for writing and return without waiting for disk I/O.

        A poisoned store synchronously re-raises the first write failure. The
        writer checks that the record is JSON-serializable; a serialization
        failure poisons the store.

        :param record: A dictionary that can be serialized as JSON.
        :raises RuntimeError: The store has already been closed.
        """
        ...
    def replay(self) -> Iterator[dict]:
        """Yield records in write order, excluding metadata and a torn final line.

        Older file versions are upgraded through ``MIGRATIONS`` and then
        atomically rewritten to the current version. Files without a version
        record are treated as version ``0``. If this generator is consumed
        without a running event loop, migration records are still yielded but
        the rewrite is deferred and a warning is logged.

        :return: An iterator yielding records in write order.
        :raises flowing.errors.FormatVersionError: The file uses a newer format
            version than this release supports.
        :raises flowing.errors.CorruptionError: An interior JSON line is
            malformed. A torn final line is discarded instead.
        """
        ...
    async def drain(self) -> None:
        """Wait until all records submitted before this call have reached disk.

        The owning object uses this barrier during shutdown and before a
        complete-file rewrite. Tombstone compaction is performed by the
        background writer and does not require callers to invoke ``drain``
        directly.

        :raises: The original write failure if the store is poisoned and cannot
            guarantee that earlier records were written.
        """
        ...
    async def close(self) -> None:
        """Drain pending writes and stop the writer; repeated calls are safe.

        During normal shutdown, pending records are written before the task is
        stopped. If a write failure has already been reported through
        ``submit`` or ``drain``, close performs cleanup without re-raising that
        same failure.
        """
        ...
    def sync(self, records: list[dict]) -> None:
        """Queue an atomic rewrite of this file to the supplied final records.

        The final sequence comes from the caller's in-memory authority; this
        method does not derive it by replaying the file. The background writer
        performs the rewrite after earlier queued work and writes the current
        format metadata record at the beginning. A poisoned store synchronously
        re-raises its first write failure, just as ``submit`` does.

        :param records: Final records, excluding the metadata record.
        :raises RuntimeError: The store has already been closed.
        """
        ...
    def _ensure_drain_task(self) -> None:
        """Lazily start the single writer task; call inside a running event loop."""
        ...
    async def _drain_loop(self) -> None:
        """Single writer task that serially consumes the queue and performs
        maintenance or resolves barriers when the queue becomes empty.
        """
        ...
    def _read_records_with_version(self) -> tuple[list[dict], int]:
        """Read complete records and the file version, excluding the metadata
        header. Discard a torn final line; log an interior JSON error and raise
        ``CorruptionError``.
        """
        ...
    def _ensure_file(self):
        """Lazily open the file; write the metadata header first for a new or
        empty file.
        """
        ...
    def _write_meta(self) -> None:
        """Write the metadata header at the current file position, only when
        creating or rewriting a file.
        """
        ...
    def _write_record(self, record: dict) -> None:
        """Append one record, applying the internal last-line merge or
        truncation strategy when ``merge_last_line`` is enabled.
        """
        ...
    def _atomic_rewrite(self, records: list[dict]) -> None:
        """Atomically rewrite through a same-directory ``.tmp`` file, fsync,
        and ``os.replace``. The input excludes metadata; the current-version
        header is written automatically. Close the old file handle, reopen it
        lazily, and reset last-line tracking after replacement. Internal API.
        """
        ...
    def _maybe_compact_tombstones(self) -> None:
        """Atomically rewrite the file when tombstone count reaches the
        threshold; this is checked when the drain queue is empty or at a
        barrier. The trigger is count-only; the store does not know whether an
        Agent is idle. Internal API.
        """
        ...
class StateView:
    """Expose one persisted state bag through attributes and mapping access.

    A state bag is the durable state for one Agent or Runtime namespace. Each
    key is accessible as an attribute or through mapping syntax; attribute keys
    must be valid Python identifiers, while mapping keys may be any string.
    Set and delete operations update the in-memory view and synchronously queue
    a record for persistence. Agent exposes the default bag as ``agent.state``
    and named bags through ``agent.register_state(name)``. Runtime exposes
    ``runtime.register_state(namespace)`` and ``runtime.states``. These
    contracts apply to both owners.

    Writes made during ``setup()`` are valid: recovery replays persisted state
    before setup completes, so those writes are not subsequently overwritten
    by replay.

    .. rubric:: Example

    .. code-block:: python

        from flowing import Agent

        class MyAgent(Agent):
            async def setup(self, **args):
                self.state.register("tracker_count", 0)

        # In hook or tool code:
        agent.state.tracker_count += 1
        print(agent.state["tracker_count"])
        del agent.state.jobs

    .. rubric:: Behavior

    - Writes and deletes are queued to the backend and may return before disk
      I/O completes. Values must be JSON-serializable; serialization failure
      raises ``TypeError`` before a record is submitted. State writes do not
      trigger Agent attribute watchers; watchers observe ordinary instance
      attributes through the ``Agent.__setattr__`` path instead.
    - ``register(key, default)`` writes ``default`` only if the key has no
      persisted value. Later registrations return the persisted value and do
      not replace it with a changed default. Registration is idempotent.
    - Registration is optional. Direct assignment creates a persisted key,
      and replay restores every stored key even if the current code never
      registered it.
    - Reads of absent keys raise ``KeyError`` through mapping access and
      ``AttributeError`` through attribute access. ``get`` returns its default
      for an absent key. Deleting a key removes its registered value as well;
      there is no fallback to its original default.
    - Within one view, writes take effect in call order. Different Agent files
      and Runtime namespaces have no cross-store ordering guarantee. Compaction
      and last-line merging preserve the logical state.
    - State files are compacted to the current values when the number of
      appended records reaches the configured threshold (default ``256``),
      after recovery replay, and during owner shutdown. The view submits an
      atomic ``sync`` replacement containing one ``set`` record per current
      key; this changes the file layout, not the logical state.
    - The crash window is the tail of submitted but not yet written records
      (normally a matter of milliseconds). At most the final state change is
      lost, equivalent to losing power. A torn trailing row produced during an
      in-place rewrite is truncated and discarded during replay.
    - After replay, the view's logical contents match the pre-crash state:
      later writes to the same key replace earlier ones, and deletes remove
      the key. The only exception is the final change represented by a torn
      row at the crash point.
    - Persist only state needed for recovery; keep high-frequency telemetry in
      plugin memory. Plugin-owned keys should use a plugin-specific prefix,
      such as the cron plugin's ``cron_jobs``. The framework's core state bag is
      private and is not exposed to applications.

    .. seealso:: :class:`RecordStore`, :class:`FileRecordStore`,
       :attr:`flowing.agent.Agent.state`,
       :meth:`flowing.agent.Agent.register_state`,
       :meth:`flowing.runtime.Runtime.register_state`
    """
    _store: RecordStore
    """Embedded store, one per state bag: ``state.jsonl`` or ``<name>.jsonl``
    for an Agent and ``<namespace>.jsonl`` for Runtime. All use
    ``merge_last_line=True``. Internal API, not a stable contract.
    """
    _persisted: dict[str, Any]
    """In-memory table of persisted values, updated during writes and replay.
    Key membership distinguishes a stored ``None`` from an absent key. This is
    the sole source of truth; there is no separate defaults table. Internal API,
    not a stable contract.
    """
    _compact_threshold: int
    """Automatic-compaction line threshold (default ``256``; configurable at
    construction). Internal API.
    """
    _lines_since_compact: int
    """Number of appended lines since the last compaction. ``__setitem__`` and
    ``__delitem__`` each increment it; exceeding the threshold submits a
    compaction request and resets the counter. Internal API.
    """
    def __init__(self, store: RecordStore, compact_threshold: int=256) -> None:
        """Bind a state view to its persistence backend.

        This is an internal API, not a stable public contract. Agent constructs
        it during ``Agent.__init__`` through ``_open_stores``; Runtime
        constructs it through ``Runtime.register_state``. The view depends only
        on the five-method :class:`RecordStore` protocol, not on file-specific
        backend behavior.

        :param store: The backend implementing :class:`RecordStore`.
        :param compact_threshold: Number of appended state records that
            triggers compaction. The default is ``256``.
        """
        ...
    async def _close(self) -> None:
        """Close the embedded store during owner shutdown. Internal API."""
        ...
    def register(self, key: str, default: Any=None) -> Any:
        """Register a state key and return the value currently in persistence.

        If the key is absent, ``default`` is written and returned. If it is
        already present, the stored value is returned without being replaced.
        Repeated registration therefore does not reset existing state.

        :param key: The state key.
        :param default: Initial value for an absent key; defaults to ``None``.
        :return: The persisted value, or the default just written.
        """
        ...
    def _maybe_compact(self, *, force: bool=False) -> None:
        """Request a full-file rewrite after recovery replay, during owner
        ``destroy`` (both with ``force=True``), or when appended records exceed
        the threshold (with ``force=False``). The final records are one ``set``
        per current persisted value and are submitted through ``_store.sync``;
        the backend performs the physical rewrite. The hot path costs one
        threshold comparison. Internal API.
        """
        ...
    def __getattr__(self, key: str) -> Any:
        """Read a persisted value through attribute syntax.

        :param key: The state key, which must be a valid Python identifier for
            attribute access.
        :return: The value stored for ``key``.
        :raises AttributeError: No persisted value exists for the key.

        Mapping access through ``self[key]`` raises ``KeyError`` for the same
        missing key. Translating the missing-key error to ``AttributeError``
        preserves ordinary attribute-access semantics and is safe for
        introspection.
        """
        ...
    def __setattr__(self, key: str, value: Any) -> None:
        """Write a state value through attribute syntax.

        This has the same persistence and JSON-serializability behavior as
        ``self[key] = value``. The implementation initializes its own private
        fields through ``object.__setattr__``; ordinary attribute assignments
        handled here are state writes.

        :param key: The state key, which must be a valid Python identifier.
        :param value: A JSON-serializable value to persist.
        :raises TypeError: The value cannot be serialized as JSON.
        """
        ...
    def __delattr__(self, key: str) -> None:
        """Delete a state value through attribute syntax.

        This has the same behavior as ``del self[key]``.

        :param key: The state key, which must be a valid Python identifier.
        """
        ...
    def __getitem__(self, key: str) -> Any:
        """Return the persisted value for ``key``.

        :param key: The state key.
        :return: The current persisted value.
        :raises KeyError: The key has not been persisted or registered.
        """
        ...
    def __setitem__(self, key: str, value: Any) -> None:
        """Persist ``value`` under ``key`` and update the in-memory view.

        The value is checked for JSON serializability before the view is
        updated. The record is then synchronously queued to the backend; this
        does not mean that disk I/O has completed. State writes do not trigger
        Agent attribute watchers.

        :param key: The state key; any string is accepted.
        :param value: The value to persist.
        :raises TypeError: The value cannot be serialized as JSON. This native
            exception is not wrapped.
        """
        ...
    def __delitem__(self, key: str) -> None:
        """Delete the persisted value for ``key``.

        After deletion, mapping reads raise ``KeyError`` and attribute reads
        raise ``AttributeError``. The default previously supplied to
        ``register`` is not restored.

        :param key: The state key.
        """
        ...
    def __contains__(self, key: str) -> bool:
        """Return whether a persisted value exists for ``key``.

        :param key: The state key.
        :return: ``True`` if the key currently has a persisted value;
            otherwise ``False``.
        """
        ...
    def get(self, key: str, default: Any=None) -> Any:
        """Return a persisted value, or ``default`` when the key is absent.

        This is the non-raising form of ``__getitem__``.

        :param key: The state key.
        :param default: The value to return when the key is absent.
        :return: The persisted value or ``default``.
        """
        ...
