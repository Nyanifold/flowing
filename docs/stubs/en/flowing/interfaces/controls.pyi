"""``flowing.interfaces.controls`` — the shared slash-command catalog for
``repl`` and ``serve``/web.

.. rubric:: Overview

Both the ``repl`` and web input fields accept control commands in the form
``/cmd [arg]``. This module is their single source of command behavior: it
returns text lines, which each interface renders in its own way.

- ``repl`` prints each line returned by :func:`slash_lines`. The interactive
  shell itself handles commands that change the foreground Agent binding
  (``/exit``, ``/quit``, ``/agent``, and ``/new``) and manage the session
  lifecycle.
- ``serve`` returns the lines as JSON from ``POST /agents/{id}/command``. The
  web client can then run a REPL command against any Agent it has selected.

Commands fall into two groups. Runtime-level commands do not require an Agent:
``help``, ``agents``, and ``snapshot``. Agent-level commands act on the
explicitly supplied Agent: ``messages``, ``model``, ``context``, ``status``,
``tasks``, ``export``, ``rewind``, ``cancel``, ``pause``, and ``resume``.

``messages`` accepts ``v`` or ``verbose`` to display complete serialized
message records and content blocks. In the default mode, tool results are
shown in full up to 500 rendered characters and truncated above that; other
message text keeps a short preview.

This module contains only commands that act on an explicit target and return
text. Changing the foreground binding (``agent`` and ``new``) and exiting the
process (``exit`` and ``quit``) remain responsibilities of the interactive
shell.

.. seealso::

    :func:`flowing.interfaces.repl.cmd_repl` is the interactive command loop.
    :mod:`flowing.interfaces.serve` exposes the command endpoint.
"""
from flowing.agent import Agent

HELP_LINES: tuple[str, ...]
"""One-line descriptions used by ``/help``, corresponding to the REPL command list."""

def available_model_tags(agent: Agent) -> list[str]:
    """Return the project's configured model tags in the order listed in the file.

    The function reads the model-tag source registered by the Agent's Runtime
    (``runtime._model_tags_path``) on each call through
    :func:`flowing.model.load_model_tags`; it does not cache the result. If no
    file is configured or loading fails, it returns an empty list so that
    displaying the command catalog does not fail. This is the same source used
    by the REPL's ``/model`` command and ``GET /agents/<id>/models``.
    """
    ...

def _fold(text: str, limit: int = 80) -> str: ...

async def slash_lines(cmd: str, arg: str, agent: Agent | None, runtime) -> list[str]:
    """Run a slash command and return the text lines to display.

    The function is pure with respect to output: it returns lines and does not
    print them. If ``cmd`` starts with ``/``, the leading slash is removed. The
    ``arg`` value is the text after the first space and may contain a subcommand.
    If a command is unknown or requires an Agent but ``agent`` is ``None``, the
    function returns a prompt line instead of raising an error.
    """
    ...
