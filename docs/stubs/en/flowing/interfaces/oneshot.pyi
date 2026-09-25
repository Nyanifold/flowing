"""``flowing.interfaces.oneshot`` — the one-shot conversation command (``cli``).

.. rubric:: Overview

``flowing cli <path> [-f <main file>] [-t <agent-id>] [-m <model-tag>] [-v] INPUT [--key value ...]``
starts a Runtime, loads or creates a root Agent, submits exactly one ``INPUT``
(either a message or one slash command), prints the result, and exits. The
process handles one message; ``query()`` waits for the turn to finish, so the
command blocks by default in shell scripts.

Unlike the interactive ``repl``, ``cli`` is intended for scripts and pipelines:
its standard output can be consumed by another program, while ``repl`` is for
interactive use. The two commands are separate interfaces, not aliases.

.. rubric:: Usage example

.. code-block:: console

    $ flowing cli . "Introduce yourself in one sentence."
    I am a concise English assistant.
    $ flowing cli . -t agent-3f2a "Continue"            # Restore the selected Agent if dormant.
    $ flowing cli . -m deepseek-flash "Hello"           # Use this model tag for this message.
    $ flowing cli . -v "Check today's weather"          # Print the full process, including tool calls.
    $ flowing cli . /messages                           # INPUT may also be a slash command.
    $ flowing cli . --workspace_root /ws "Hello"        # Pass other --key value pairs to main().

The output above is illustrative; the command does not prescribe an assistant
persona or response language.

.. rubric:: Behavior notes

1. The command obtains a Runtime with ``await launch(path, main_file=..., **kwargs)``.
   If launch fails, it prints a summary to stderr and returns
   :data:`flowing.interfaces.EXIT_RUNTIME_ERROR`.
2. The command resolves its target Agent through ``_resolve_agent``.

   - If ``-t <agent-id>`` is supplied, the command checks the live-agent table
     and the persisted pool. It returns an active Agent directly or restores a
     dormant one with ``await runtime.get_agent(agent_id)``. An unknown ID is
     reported to stderr and returns ``EXIT_RUNTIME_ERROR``.
   - If no ID is supplied, the command uses the only live root if exactly one
     exists. Otherwise, it restores the only dormant root if exactly one
     exists. If neither case is unambiguous, it creates an Agent of the
     project's default type. If the pool has no root record and no default
     Agent type can be determined, it reports that error to stderr and returns
     ``EXIT_RUNTIME_ERROR``.
3. If ``-m <model-tag>`` is supplied, the command assigns the tag to
   ``agent.model_tag`` before submitting a message. This changes only the
   in-memory Agent instance and is not persisted; the value is lost when the
   process exits. A resolution failure is reported to stderr and returns
   ``EXIT_RUNTIME_ERROR``. The option applies only to message input; slash
   commands ignore it.
4. The parser requires exactly one non-empty bare positional ``INPUT``.

   - If ``INPUT`` begins with ``/``, the command dispatches it through
     :func:`flowing.interfaces.controls.slash_lines` and prints each returned
     line. Runtime-level commands such as ``help``, ``agents``, and
     ``snapshot`` still work when no Agent can be resolved; Agent-level
     commands receive the prompt produced by ``slash_lines``. The command
     returns :data:`flowing.interfaces.EXIT_OK`.
   - Otherwise, the command submits the text to ``await agent.query(text)``.
     In verbose mode it first subscribes to ``on_provider_delta`` and
     ``on_turn_append``: streamed text is printed as it arrives, thinking
     output is dimmed on a TTY and printed normally otherwise, and tool calls,
     tool results, and injected STEER messages are shown. It does not print the
     final text twice if that text was already streamed. For a non-streaming
     provider or an error result, it prints ``result.final_text`` at turn end.
     Without verbose mode it subscribes to no hooks and writes only the final
     reply to stdout.
5. A result with ``status == "error"`` is written to stderr and returns
   :data:`flowing.interfaces.EXIT_RUNTIME_ERROR`. Results with ``completed``, ``blocked``, or
   ``cancelled`` status are normal command outcomes and return
   :data:`flowing.interfaces.EXIT_OK`; the final text is written to stdout. Before exiting, the
   command awaits ``runtime.shutdown()`` on every path, with a ``finally``
   block as a fallback.
6. If SIGINT arrives while a turn is running, the command calls
   ``abort_turn()`` on the target Agent. The query then finishes as cancelled
   and the command returns normally. SIGINT while idle and SIGTERM initiate a
   graceful shutdown through ``runtime.shutdown()``.

.. rubric:: Boundaries and edge cases

- A second bare positional argument, an unknown single-dash option, an
  ``--key=value`` argument, or a standalone ``--`` is a usage error and returns
  :data:`flowing.interfaces.EXIT_USAGE_ERROR`. ``INPUT`` cannot begin with ``-`` because the
  command uses a closed set of single-dash options.
- ``-t`` and ``-m`` are usage errors if their value is missing or the next
  argument begins with ``--``.
- Double-dash arguments have no reserved names. For example,
  ``--agent x`` is passed through :func:`flowing.interfaces.parse_kv_args` to
  the project's ``main`` function, even if its name resembles a ``flowing``
  option. ``cmd_cli`` collects Flowing-specific options in the single
  ``opts`` dictionary; only ``main_file`` and ``opts`` are reserved parameter
  names, so the meanings of ``-t``, ``-m``, ``-v``, and ``INPUT`` do not shadow
  keyword arguments passed to ``main``.
- Slash-command input does not submit a message or change the model. Commands
  such as ``/exit`` and ``/quit`` belong to the interactive shell and produce a
  shell-level notice when sent to this one-shot command.

.. seealso::

    :func:`flowing.interfaces.cli.main` parses and dispatches the command-line
    arguments, including ``-t``, ``-m``, and ``-v``.
    :func:`flowing.interfaces.repl.cmd_repl` provides the interactive
    counterpart.
    :func:`flowing.interfaces.controls.slash_lines` is the shared source of
    slash-command behavior.
"""
from flowing.agent import Agent
from flowing.runtime import Runtime

def _install_oneshot_signal_handlers(runtime: Runtime, flags: dict): ...
async def _resolve_agent(runtime: Runtime, agent_id: str | None) -> Agent | None: ...
def _make_verbose_printers(): ...
async def cmd_cli(path: str, main_file: str | None = None, *, opts: dict | None = None, **kwargs: str | bool) -> int:
    """Run ``flowing cli <path> ... INPUT`` once, then exit.

    The command-line contract is described in the module documentation.

    :param path: The project path, as for :func:`flowing.interfaces.run.cmd_run`.
    :param main_file: An optional replacement entry-point file, supplied by
        the ``-f`` option.
    :param opts: The ``cli``-specific Flowing options. Its keys are ``input``
        (required; a message or one slash command), ``agent_id`` (optional;
        the target Agent selected by ``-t``), ``model_tag`` (optional; the
        model tag for this message, selected by ``-m``; changed in memory but
        not persisted), and ``verbose`` (optional; print the full process,
        selected by ``-v``). These options are grouped in one dictionary so
        that their names do not occupy keyword names belonging to the
        project's ``main`` function. Double-dash arguments such as
        ``--agent-id``, ``--verbose``, and ``--input-text`` remain available
        for forwarding.
    :param kwargs: The ``--key value`` arguments forwarded to ``launch`` and
        the project's ``main`` function.
    :return: :data:`flowing.interfaces.EXIT_OK` for normal outcomes, including ``blocked`` and
        ``cancelled``; :data:`flowing.interfaces.EXIT_RUNTIME_ERROR` for launch, Agent resolution,
        model selection, or turn errors; or :data:`flowing.interfaces.EXIT_USAGE_ERROR` when a
        direct programmatic call supplies an empty input.

    .. seealso::

        :func:`flowing.interfaces.cli.main` parses and dispatches ``-t``,
        ``-m``, ``-v``, and ``INPUT``.
        ``_resolve_agent`` implements target-Agent selection.
    """
    ...
