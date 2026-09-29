"""Interactive REPL subcommand, ``repl``.

The default REPL is a closed observation surface, as described in the
``flowing.interfaces`` package. The one-shot ``cli`` command is documented in
``flowing.interfaces.oneshot``; it is not an alias for this interactive loop.
"""
from typing import Awaitable, Callable
from flowing.agent import Agent
from flowing.message import Message
from flowing.runtime import Runtime

SLASH_COMMANDS: tuple[str, ...]
"""The closed set of slash commands recognized by the default REPL.

Input beginning with ``/`` is treated as a control command, not as a message
to the LLM. The fixed set keeps the startup observation interface predictable
and does not accept runtime command registration.

- ``/help`` lists these commands and a short description of each.
- ``/exit`` shuts down the Runtime and exits with
  :data:`flowing.interfaces.EXIT_OK`; ``/quit`` is an alias.
- ``/snapshot`` prints a human-readable rendering of the Runtime's read-only
  snapshot.
- ``/messages [v|verbose]`` prints the bound Agent's message-chain summary
  from the current head; ``v`` or ``verbose`` displays complete serialized
  messages and content blocks. With no bound Agent, it prints a hint.
- ``/agents`` lists recorded Agents with their IDs, last-reply previews, and
  modification times.
- ``/agent <id>`` changes the bound Agent, restoring an inactive recorded
  Agent if needed. Unknown IDs leave the binding unchanged. ``/use <id>`` is
  also accepted as a compatibility alias.
- ``/new [agent_type]`` creates a root Agent and binds it. If the type is
  omitted, the project-default type is used; if no root record is available,
  the REPL prints an error and creates nothing.
- ``/model``, ``/context``, ``/status``, ``/tasks``, ``/export``, ``/rewind``,
  ``/cancel``, ``/pause``, and ``/resume`` are handled by the shared command
  dispatcher in :mod:`flowing.interfaces.controls`.

An unrecognized ``/xxx`` prints a hint to use ``/help``. It does not raise an
error, exit the REPL, or enter the message stream. For commands with
arguments, the first space separates the command from its argument.

.. rubric:: See also

:func:`cmd_repl` and :meth:`flowing.runtime.Runtime.snapshot`
"""
_HELP_LINES: tuple[str, ...]
"""The one-line descriptions printed by ``/help``, aligned with
:data:`SLASH_COMMANDS`.
"""

def _install_repl_signal_handlers(runtime: Runtime, flags: dict) -> None:
    """Install the signal behavior used by the interactive REPL.

    During a turn, SIGINT (Ctrl-C) calls ``abort_turn()`` on the currently
    bound Agent. This cooperatively cancels that turn while keeping the
    At an idle prompt, SIGINT raises ``KeyboardInterrupt`` to interrupt the
    current line; the input loop catches it, prints a newline, and shows the
    prompt again. Exit with ``/exit`` or Ctrl-D (EOF). SIGTERM starts the same
    graceful-shutdown path used by ``run``, ``serve``, and ``web`` through
    ``runtime.shutdown()``.

    Signal handling is a no-op outside the main thread or on platforms that
    do not support it. The function returns the previous SIGINT handler so
    ``cmd_repl`` can restore it on exit and repeated REPL invocations in one
    process do not interfere with each other.
    """

def _summarize_message(host: Agent, msg: Message, *, omit_thinking: bool = False) -> str | None:
    """Render a newly appended message for the REPL's turn-progress display.

    Rendered content is not collapsed or truncated. A TOOL result and a STEER
    message each produce one prefixed line followed by the full body. A
    PROVIDER message containing a ThinkingBlock includes the full
    ``[thinking]`` section. Each ToolCallBlock in a PROVIDER message gets its
    own ``[tool_call] <name> <arguments>`` line. Non-empty arguments are
    serialized as JSON; an empty argument mapping is omitted. Calls are
    neither merged nor omitted. The returned string may
    contain multiple lines, which the caller prints once. Pure-text PROVIDER
    messages are already displayed incrementally by ``on_provider_delta``
    and are not repeated here. Other message kinds return ``None``.

    When ``omit_thinking`` is ``True``, the ``[thinking]`` section is omitted
    because the REPL has already displayed thinking deltas in gray. This avoids
    showing the same content twice.
    """

async def cmd_repl(path: str, main_file: str | None = None, *, extra_slash_handlers: dict[str, Callable[[str, Agent | None, Runtime], Awaitable[None]]] | None = None, pre_prompt_hook: Callable[[Agent | None, Runtime], Awaitable[None]] | None = None, extra_help_text: str | None = None, **kwargs: str | bool) -> int:
    """Run the interactive ``flowing repl <path>`` command.

    The REPL reads lines from stdin. Lines beginning with ``/`` are dispatched
    as slash commands; other lines become USER messages, and Agent replies are
    printed to stdout.

    The REPL keeps a session-local binding to an Agent. When bound, ordinary
    input is sent to that Agent and the prompt is ``(<agent_id>)>>>``. When
    unbound, the prompt is ``(new agent)>>>``; the first ordinary input creates
    and binds an Agent before sending the message. ``/agents`` lists recorded
    Agents and ``/agent <agent_id>`` changes the binding; ``/use`` is also
    accepted as an alias. A project with multiple
    root Agents does not fail during startup; the user can choose one in the
    REPL. Workflow roots are not binding candidates. Whether project startup
    creates or restores Agents is the project's ``main`` policy; the REPL
    manages only its session-local binding after ``launch``.

    .. rubric:: Usage example

    .. code-block:: console

        $ flowing repl . --workspace_root /ws
        (new agent)>>> /agents
        agent-3f2a…  "Previous reply preview…"  2026-08-20 21:14
        (new agent)>>> /use agent-3f2a…
        (agent-3f2a…)>>> Continue
        [Agent response]
        (agent-3f2a…)>>> /exit
        $

    .. rubric:: Behavior notes

    1. The command awaits ``launch(path, main_file=main_file, **kwargs)`` and
       installs its signal handlers. If ``launch`` raises, it prints an error
       summary to stderr and returns
       :data:`flowing.interfaces.EXIT_RUNTIME_ERROR`.
    2. At startup, the REPL scans active root Agents. If exactly one exists,
       it binds to it. Otherwise, if exactly one inactive root record exists
       in the pool, it restores that Agent with
       ``await runtime.get_agent(agent_id)`` and binds to it. With no unique
       candidate, it remains unbound and, when root records exist, prints a
       hint to use ``/agents``. The fallback restoration is attempted whenever
       no sole active root was selected.
    3. The prompt is ``(<agent_id>)>>>`` when bound and
       ``(new agent)>>>`` otherwise. Input is handled as follows:

       - A line not beginning with ``/`` is sent to the bound Agent. If no
         Agent is bound, the REPL first selects the default type from the root
         pool entry with the newest ``created_at`` value, matching the default
         used by ``POST /agents``. If the pool has no root entry, it prints a
         message that the Agent type cannot be determined and does not create
         an Agent. Otherwise it calls ``runtime.create_agent(agent_type)``
         with the default ``parent_id=None``, binds the new root Agent, and
         sends the input as a string to :meth:`flowing.agent.Agent.query`.
         ``query`` packages the USER message, enqueues it, and waits for the
         turn result. The REPL prints the final text and then shows the next
         prompt.
       - A line beginning with ``/`` is dispatched using
         :data:`SLASH_COMMANDS` and is not added to the message stream. For a
         command that takes an argument, the first space separates the
         command from its argument.

    4. While an Agent is bound, the REPL subscribes to that Agent's hooks to
       display progress. ``on_provider_delta`` streams generated text from
       the main turn; side queries are excluded;
       ``on_turn_append`` prints full TOOL results and STEER messages, and
       prints each tool call in a PROVIDER message on its own line with its
       complete non-empty arguments as JSON. These messages are not collapsed
       or truncated. ``after_turn`` prints the final text for turns initiated
       by sources other than the REPL's own ``query()`` call, such as Cron or
       communication extensions, when no text or thinking delta is already
       visible. If deltas have been shown, the handler closes the current line
       and does not print the final text again. ThinkingBlock content is shown
       in full. On ``/agent`` or ``/use``, subscriptions move to the newly
       bound Agent.
    5. ``/exit``, ``/quit``, or EOF (Ctrl-D) calls
       ``runtime.shutdown()`` and returns :data:`flowing.interfaces.EXIT_OK`.

    The built-in slash commands include:

    - ``/agents`` lists every Agent in the pool, including inactive sessions.
      Each row shows the Agent ID, a one-line preview of the last live
      PROVIDER message, and the session's last modification time. For inactive
      Agents the preview is read from the session's ``tree.jsonl`` file; it is
      not written back to pool metadata.
    - ``/agent <agent_id>`` changes the binding; ``/use <agent_id>`` is an
      accepted alias. If the ID refers to an active instance or a pool record,
      ``runtime.get_agent(agent_id)`` returns the active node or restores the
      inactive record. The binding changes only if the result is an ``Agent``.
      Unknown IDs and active non-Agent nodes print a message and leave the
      current binding unchanged. Switching does not destroy the previous
      Agent; it remains active.
    - ``/new [agent_type]`` creates a new root Agent and binds it. If the type
      is omitted, the project-default type is used. If no default can be
      determined, the REPL prints a message and does not create an Agent.
      Creation failures are printed and leave the current binding unchanged.
    - ``/messages`` prints a message when the REPL is unbound because there is
      no current conversation to show. ``/messages v`` and
      ``/messages verbose`` display every message record, including complete
      thinking blocks, tool calls, arguments, and results. In normal mode, a
      tool result is shown in full up to 500 rendered characters and truncated
      above that; other message text keeps the short preview.

    The following optional injection points support extensions such as
    ``repl-debug``; the default ``repl`` does not pass them:

    - ``extra_slash_handlers`` is called after built-in commands have been
      checked and before an unrecognized command is reported. Each handler
      has the signature ``async (arg: str, agent: Agent | None,
      runtime: Runtime) -> None``.
    - ``pre_prompt_hook`` is called before every prompt and has the signature
      ``async (agent: Agent | None, runtime: Runtime) -> None``.
    - ``extra_help_text`` is appended to the ``/help`` output.

    These injection points must not change the Agent binding or message flow.

    Empty input lines are ignored. Interactive terminals support arrow keys,
    line editing, and session-local history; Tab completes top-level slash
    commands but not their arguments. Waiting for input does not block the
    asyncio event loop, and output from background tasks is displayed without
    obscuring the prompt. An unrecognized ``/xxx`` prints a hint to use
    ``/help`` and the loop continues. A ``TurnResult`` with ``status="error"``
    does not terminate the REPL. Its final text is printed only when no
    streamed output has already been shown; streamed output is not printed a
    second time. If restoring an Agent at startup or after ``/agent``/``/use``
    fails because its record is damaged, the error is printed and the binding
    is unchanged; the user can inspect ``/agents`` and choose another Agent.
    During a turn, SIGINT cooperatively cancels that turn with ``abort_turn()``
    and leaves the REPL active. At an idle prompt, SIGINT interrupts only the
    current input line and the prompt is shown again. SIGTERM requests graceful
    shutdown through ``runtime.shutdown()``.

    :param path: Project path, as for :func:`flowing.interfaces.run.cmd_run`.
    :param main_file: Optional replacement ``main`` file, supplied by CLI
        option ``-f``.
    :param extra_slash_handlers: Optional mapping from additional slash
        command names to async handlers; defaults to ``None``.
    :param pre_prompt_hook: Optional async hook called before each prompt;
        defaults to ``None``.
    :param extra_help_text: Optional text appended to ``/help``; defaults to
        ``None``.
    :param kwargs: ``--key value`` arguments passed through to ``launch`` and
        the project's ``main`` function.
    :return: :data:`flowing.interfaces.EXIT_OK` after normal exit, or
        :data:`flowing.interfaces.EXIT_RUNTIME_ERROR` if ``launch`` fails.

    .. rubric:: See also

    :data:`SLASH_COMMANDS` lists the closed built-in command set.
    :meth:`flowing.agent.Agent.query` is the REPL's only message-submission
    path.
    :meth:`flowing.runtime.Runtime.get_agent` returns an active Agent or
    restores an inactive one.
    """

def _default_agent(runtime: Runtime) -> Agent | None:
    """Return the sole active root Agent, or ``None`` if there is not exactly one.

    The helper scans the live-instance table and selects nodes that are
    ``Agent`` instances whose ``_parent_id`` equals ``runtime.node_id``.
    Non-Agent roots, such as Workflow nodes, are excluded. It returns the
    Agent only when exactly one match exists; zero or multiple matches leave
    the REPL unbound so the user can choose an Agent. This is read-only and
    does not restore inactive pool records.

    .. rubric:: See also

    :func:`cmd_repl` and :meth:`flowing.runtime.Runtime.get_agent`
    """
