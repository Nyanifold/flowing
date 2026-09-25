"""``flowing.interfaces`` provides Flowing's CLI, REPL, HTTP, and web entry points.

.. rubric:: Overview

This package is Flowing's process-facing interface layer. It provides the
``flowing`` command-line entry point and its subcommands. Its shared
responsibility is to obtain a ``Runtime`` through ``launch(path, **kwargs)``;
the subcommands differ in what they do after launch.

The closed subcommand set is defined by :data:`SUBCOMMANDS` and contains eight
names: ``run``, ``repl``, ``cli``, ``repl-debug``, ``serve``, ``web``, ``test``,
and ``compile``. ``cli`` sends one input and exits, while ``repl`` is
interactive; they are not aliases. ``compile`` is the only subcommand that
does not launch a ``Runtime`` or call :func:`flowing.runtime.launch`.

The interface layer does not parse project configuration such as
``providers.yaml`` or ``models.yaml``. Configuration is read by the framework
or by the project's ``main`` function. It does not inspect installed plugins
or add plugin-specific CLI options. It also treats ``<path>`` as an ordinary
filesystem path and passes it unchanged to ``launch``; the framework's
``@/`` project-root prefix is registered in the asynchronous task context by
``launch`` and is not interpreted by the CLI.

.. rubric:: Submodules

- ``flowing.interfaces.cli`` provides the process entry point ``main()`` and
  the ``compile`` subcommand.
- ``flowing.interfaces.oneshot`` implements the one-shot ``cli`` subcommand.
- ``flowing.interfaces.repl`` implements the interactive ``repl`` subcommand.
- ``flowing.interfaces.repl_debug`` extends ``repl`` with four debugging
  slash commands for ``repl-debug``.
- ``flowing.interfaces.run`` implements the long-running ``run`` command and
  the startup smoke check ``test``.
- ``flowing.interfaces.serve`` provides the API-only HTTP server for ``serve``.
- ``flowing.interfaces.web`` provides ``web`` and its bundled default frontend.
- The package-level shared API consists of ``SUBCOMMANDS``, the three exit
  codes, and :func:`parse_kv_args`.

.. rubric:: CLI conventions

The positional ``<path>`` identifies the project and defaults to ``.``. Thus
``flowing test --key val`` is equivalent to ``flowing test . --key val``.

Flowing-specific options use a single hyphen and come from a closed set:
``-f <file>`` selects the project's ``main`` file (default ``@/main.py``);
``-h`` and ``--help`` request help; ``-a <host>`` and ``-p <port>`` configure
the ``serve`` and ``web`` listeners (default ``127.0.0.1:8000``); and
``-t <agent-id>``, ``-m <model-tag>``, and ``-v`` are available only to
``cli``. These options are consumed by the command dispatcher and are not
passed to the project's ``main`` function. Any other single-hyphen option is
a usage error.

Project ``main`` arguments use two hyphens. ``--key value`` arguments are
collected by :func:`parse_kv_args` and passed unchanged through
``launch(path, **kwargs)`` to ``main(**kwargs)``. The CLI does not reserve
double-hyphen names such as ``--agent``, ``--model``, or ``--verbose``;
``--help`` is the exception and is consumed by the CLI. Bare positional
arguments are usage errors except for the one ``cli`` input, which must be
exactly one token (a sentence or a slash command).

All subcommands use the same exit codes: ``0`` means normal completion,
including ``/exit``, EOF (Ctrl-D), or a completed graceful shutdown after
SIGINT/SIGTERM; ``1`` means a runtime failure such as a failed ``launch``, a
``serve``/``web`` bind failure, or an externally modified compile artifact;
``2`` means invalid usage, such as an unknown subcommand, a missing project
directory, an unknown single-hyphen option, an equals-form option, or excess
bare arguments.

Each subcommand installs its own signal handlers. ``run``, ``serve``, and
``web`` bridge SIGINT/SIGTERM to ``runtime.shutdown()``. In ``repl``, SIGINT
cancels the active turn through ``abort_turn()`` or, while the prompt is idle,
interrupts the current input line; ``/exit`` or EOF exits the REPL. In ``cli``,
SIGINT cancels the active request, while an idle SIGINT or SIGTERM requests
graceful shutdown. The shutdown bridge returns immediately; after
``shutdown()`` finishes destroying nodes and plugins, the Runtime's wait
event wakes and the process exits with code ``0``. The framework does not
force-kill a process after a second signal; applications are responsible for
handling a shutdown that never completes.

The default REPL slash-command set and default ``serve`` endpoint set are
closed: callers cannot register additional commands or routes at runtime.
They provide a small observation surface for sending a message and inspecting
state, not an extensible interaction framework. To add commands or routes,
extend the built-in interface (as ``repl-debug`` extends ``repl``) or build a
custom REPL/server using public APIs such as ``query()``,
``enqueue_message()``, and ``snapshot()``.

``serve`` and ``web`` expose request/response endpoints, including
``POST /agents/<agent-id>/message`` to submit a message and wait for the turn,
and ``GET /agents/<agent-id>/stream`` for server-sent events (SSE) carrying
generation deltas. They do not use WebSockets.

.. rubric:: Usage example

.. code-block:: console

    $ flowing run . --workspace_root /ws --debug
    $ flowing repl /path/to/project
    $ flowing cli . -t root -m fast "Hello"
    $ flowing serve . -p 9000
    $ flowing web .
    $ flowing test .
    $ flowing compile .

.. rubric:: See also

:func:`flowing.runtime.launch` creates the Runtime used by each subcommand.
:meth:`flowing.runtime.Runtime.shutdown` performs non-blocking graceful shutdown.
:mod:`flowing.interfaces.oneshot` defines the one-shot ``cli`` behavior.
:mod:`flowing.interfaces.web` provides the frontend used by ``web``.
"""
from pathlib import Path
from typing import Any
from flowing.message import MessageKind, TextBlock
from flowing.runtime import Runtime

SUBCOMMANDS: tuple[str, ...]
"""The closed set of subcommand names accepted after ``flowing``.

The first positional argument after the executable must exactly match one of
these lowercase names. Prefix matches and fuzzy matching are not accepted.
The eight entries are independent commands with no aliases; ``cli`` is the
one-shot command in :mod:`flowing.interfaces.oneshot`, while ``repl`` is the
interactive command in :func:`flowing.interfaces.repl.cmd_repl`.

.. rubric:: See also

:func:`flowing.interfaces.cli.main` and :data:`EXIT_USAGE_ERROR`
"""
EXIT_OK: int
"""Exit code ``0`` indicates normal completion.

This includes ``/exit``, EOF (Ctrl-D), and a completed graceful shutdown
requested by SIGINT or SIGTERM. Signal-driven shutdown is treated as normal
completion rather than using a signal convention such as ``130``.

.. rubric:: See also

:data:`EXIT_RUNTIME_ERROR` and :data:`EXIT_USAGE_ERROR`
"""
EXIT_RUNTIME_ERROR: int
"""Exit code ``1`` indicates a runtime failure.

This includes a failed ``launch`` (for example, project ``main`` raises or
``main.py`` is missing or cannot be imported), a port-binding failure in
``serve`` or ``web``, and a compile artifact that was modified externally.
The command prints the error details to stderr.

.. rubric:: See also

:data:`EXIT_OK` and :data:`EXIT_USAGE_ERROR`
"""
EXIT_USAGE_ERROR: int
"""Exit code ``2`` indicates invalid command usage.

Examples include an unknown subcommand, a missing project directory, an
unsupported argument form (including an unknown single-hyphen option,
``--key=value``, or a standalone ``--``), a bare argument to a command other
than ``cli``, or a missing or excess ``cli`` input. Usage information is
printed to stderr.

.. rubric:: See also

:func:`flowing.interfaces.cli.main` and :func:`parse_kv_args`
"""

def parse_kv_args(argv: list[str]) -> dict[str, str | bool]:
    """Convert ``--key value`` arguments into keyword arguments.

    This is the only channel for passing project arguments from the CLI to
    ``main(**kwargs)``. The CLI does not interpret their meaning; the project's
    ``main`` signature defines each name's semantics and any type conversion.

    .. rubric:: Usage example

    .. code-block:: python

        parse_kv_args(["--a-b", "x", "--flag"])
        # {"a_b": "x", "flag": True}

        parse_kv_args(["--k", "1", "--k", "2"])
        # {"k": "2"}

        parse_kv_args([])
        # {}

    .. rubric:: Behavior notes

    - Each ``--key value`` pair becomes one dictionary entry. Every hyphen in
      the key becomes an underscore, and each value remains a string; the
      project's ``main`` function is responsible for converting its type.
    - If ``--key`` is followed by another double-hyphen option or by the end of
      the list, its value is ``True``.
    - If the same key appears more than once, the last value replaces the
      earlier one; values are not collected into a list.
    - Bare arguments and ``--key=value`` forms are ignored. The CLI rejects
      those forms as usage errors before calling this function.
    - An empty list produces an empty dictionary.

    .. rubric:: See also

    :func:`flowing.runtime.launch` passes the collected values to the
    project's ``main`` function.
    """

def _default_agent_type(runtime: Runtime) -> str | None:
    """Return the project-default Agent type, or ``None`` if none is recorded.

    Among pool entries whose ``parent_agent_id`` equals the Runtime's own id,
    this selects the entry with the newest ``created_at`` value. The REPL's
    first unbound message, ``POST /agents`` without an explicit type, and
    ``cli`` without ``-t`` use this same fallback.
    """

def _last_reply_prefix(session_dir: Path, *, limit: int = 40) -> str:
    """Read a short preview from the last live PROVIDER message in a session.

    This helper supplies the last-reply preview shown by the REPL's
    ``/agents`` command and the server's ``GET /agents`` response. It reads the
    session's ``tree.jsonl`` because an inactive Agent has no in-memory object;
    it does not write the preview back to pool metadata. A torn final line is
    discarded, malformed lines are skipped, and tombstones (records marking a
    message as deleted) remove the corresponding message. A missing file or a
    session without a live PROVIDER message produces an empty string. The
    preview is collapsed to one line and truncated when it exceeds ``limit``.
    """

def _session_mtime(session_dir: Path) -> float | None:
    """Return the newest modification time among files in a session directory.

    If the directory is missing or contains no files, this falls back to the
    directory's own modification time. It returns ``None`` if neither value
    is available.
    """

def _list_agent_records(runtime: Runtime) -> list[dict[str, Any]]:
    """Read all Agent pool records without activating inactive Agents.

    The REPL's ``/agents`` command and the server's ``GET /agents`` endpoint
    use this shared listing. Records include inactive sessions and remain in
    pool insertion order. Each record contains ``agent_id``, ``agent_type``,
    ``parent_agent_id``, ``created_at``, ``active`` (whether the Agent is in
    the live-instance table), ``last_reply`` (a preview from its last live
    PROVIDER message), and ``mtime`` (the session directory's modification
    time in seconds, or ``None`` when unavailable). This is a read-only
    operation; a damaged session directory does not prevent other records
    from being listed.
    """
