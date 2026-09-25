"""Debugging extension of the interactive ``repl`` command.

.. rubric:: Overview

``flowing repl-debug <path>`` extends
:func:`flowing.interfaces.repl.cmd_repl` and retains its binding behavior,
prompt, message input, all built-in slash commands, and EOF/``/exit`` exit
behavior. It adds four debugging commands: ``/eval``, ``/watch``,
``/eval-runtime``, and ``/watch-runtime``. The default ``repl`` command keeps
its closed command set; these additions are available only in ``repl-debug``.

The extension uses ``cmd_repl``'s ``extra_slash_handlers``,
``pre_prompt_hook``, and ``extra_help_text`` injection points. The default
``repl`` passes none of them and is unchanged. Its ``/help`` output does not
list the debug commands; ``repl-debug`` adds all four.

.. rubric:: Command syntax

.. list-table::
   :widths: 20 30 50
   :header-rows: 1

   * - Command
     - Syntax
     - Description
   * - ``/eval <expr>``
     - ``/eval current_mode``
     - Evaluate and print an expression in the bound Agent's context.
   * - ``/watch <expr>``
     - ``/watch current_mode``
     - Evaluate an expression in the bound Agent's context and watch its value.
   * - ``/eval-runtime <expr>``
     - ``/eval-runtime self.snapshot().plugins``
     - Evaluate and print an expression in the Runtime context.
   * - ``/watch-runtime <expr>``
     - ``/watch-runtime self.snapshot().agents``
     - Evaluate an expression in the Runtime context and watch its value.

The expression is the body of a Jinja2 expression and must not include the
outer ``{{ }}``. The command wraps it as ``{{ <expr> }}`` before evaluation.
If the expression is empty, the command prints usage and does not evaluate it.

.. rubric:: Evaluation contexts

For ``/eval`` and ``/watch``, a bound Agent is required. If the REPL is
unbound and shows ``(new agent)>>>``, the command prints that the expression
cannot be evaluated without a bound Agent. Otherwise, it resolves
``agent.parsable("{{ <expr> }}").resolve(agent)`` and uses the same context
semantics as other Flowing ``Parsable`` values, including ``env``, ``config``,
instance attributes, and Parsable fields. After ``/use <id>``, these commands
use the newly bound Agent.

For ``/eval-runtime`` and ``/watch-runtime``, the context is the Runtime
instance and the variable name is ``self`` (``self = runtime``). Evaluation
uses ``Parsable("{{ <expr> }}").resolve({"self": runtime, "agent": runtime})``.
Both ``self`` and ``agent`` in the expression refer to the Runtime. These
commands do not require a bound Agent.

.. rubric:: Error handling

If evaluation raises an exception, the command prints ``str(exception)``
unchanged, without wrapping it, rewriting it, or appending a traceback. The
REPL remains active and returns to the prompt. When no Agent is bound,
``/eval`` and ``/watch`` print the unbound-Agent message and do not evaluate.

.. rubric:: Behavior notes

``/eval`` evaluates once and prints the result. It does not store the
expression or add a watch.

The first successful ``/watch`` evaluation prints ``[watch] <expr> =>
<value>`` and adds the expression and value to a process-local watch list. If
that initial evaluation fails, the error is printed and no watch is added.
Before each later prompt, the REPL evaluates each watch again. An unchanged
value produces no output; a changed value prints ``[watch] <expr> =>
<new value>``. If a later evaluation fails, the error is printed and the
existing watch remains for the next attempt. Values are compared with
``!=``. After switching bindings with
``/use``, Agent-context watch evaluation uses the newly bound Agent. Watch
state is process-local and is not persisted; it disappears when the process
exits or reaches EOF.

``/eval-runtime`` and ``/watch-runtime`` follow the same rules as their
Agent-context counterparts but use ``{"self": runtime, "agent": runtime}``. Runtime watches do
not depend on the binding state and are unaffected by switching Agents.

.. rubric:: See also

:func:`flowing.interfaces.repl.cmd_repl` provides the REPL and its
extension points.
"""
from typing import Any
from flowing.agent import Agent
from flowing.runtime import Runtime

def _wrap_expr(expr: str) -> str:
    """Wrap an expression body in the Jinja2 expression delimiters."""

def _eval_agent(agent: Agent, expr: str) -> tuple[bool, Any]:
    """Evaluate an expression with an Agent as its context.

    Return ``(success, value_or_exception)``.
    """

def _eval_runtime(runtime: Runtime, expr: str) -> tuple[bool, Any]:
    """Evaluate an expression with ``self`` bound to the Runtime.

    Return ``(success, value_or_exception)``.
    """

async def cmd_repl_debug(path: str, main_file: str | None = None, **kwargs: str | bool) -> int:
    """Run the interactive REPL with the four debugging expression commands.

    This command creates handlers for the debug commands, their watch state,
    and a hook that checks watched expressions before each prompt. It then
    delegates to :func:`flowing.interfaces.repl.cmd_repl`, passing those
    handlers through the REPL's extension points. See this module's docstring
    for the command semantics.

    :param path: Project path, as for :func:`flowing.interfaces.repl.cmd_repl`.
    :param main_file: Optional replacement ``main`` file, supplied by CLI
        option ``-f``.
    :param kwargs: ``--key value`` arguments passed through to ``launch`` and
        the project's ``main`` function.
    :return: The exit code returned by ``cmd_repl``: either
        :data:`flowing.interfaces.EXIT_OK` or
        :data:`flowing.interfaces.EXIT_RUNTIME_ERROR`.

    .. rubric:: See also

    :func:`flowing.interfaces.repl.cmd_repl`
    """
