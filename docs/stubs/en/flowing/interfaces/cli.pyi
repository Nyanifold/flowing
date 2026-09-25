"""CLI process entry point and the explicit ``compile`` command.

This module parses ``argv`` and dispatches to the selected subcommand. The
package docstring defines the shared option, exit-code, and signal-handling
conventions. This module also provides the ``.fya`` compilation command
through :func:`cmd_compile`.
"""
from typing import TextIO

_USAGES: dict[str, str]
"""One-line usage strings for each subcommand.

The CLI prints a selected usage string to stdout for ``-h`` or ``--help`` and
to stderr alongside an invalid-usage message.
"""

def _print_general_usage(file: TextIO) -> None:
    """Print the closed subcommand list to the supplied stream.

    The CLI uses this output on stderr for an empty argument list or an
    unknown subcommand.
    """

def _usage_error(message: str, subcommand: str) -> int:
    """Print an error and the selected command's usage to stderr.

    Return :data:`flowing.interfaces.EXIT_USAGE_ERROR`.
    """

def _parse_flowing_args(subcommand: str, rest: list[str]):
    """Parse Flowing's single-hyphen options and validate project arguments.

    On success, return the six-item tuple
    ``(main_file, host, port, help_requested, sub_opts, kwargs)``. On any parse
    error, print the command's usage to stderr and return
    :data:`flowing.interfaces.EXIT_USAGE_ERROR`; the integer error result is
    distinguishable from the success tuple.

    Errors include missing values for ``-f``, ``-a``, ``-p``, ``-t``, or
    ``-m``; a non-numeric ``-p`` value; an unknown single-hyphen option;
    ``--key=value``; a standalone ``--``; and disallowed bare positional
    arguments. ``--help`` sets ``help_requested`` and is not included in
    ``kwargs``.

    The shared Flowing options are ``-f`` for the project's ``main`` file and
    ``-a``/``-p`` for the ``serve`` and ``web`` listener. Only the ``cli``
    subcommand accepts ``-t <agent-id>``, ``-m <model-tag>``, and ``-v``;
    these are returned in ``sub_opts`` for ``cmd_cli`` and are not passed to
    the project's ``main``. Other subcommands reject them. ``cli`` alone also
    accepts one bare input token, stored as ``sub_opts["input"]``; a missing
    input or a second bare token is a usage error. All other subcommands reject
    bare arguments. ``main`` checks help before checking whether ``cli`` input
    is missing, so ``flowing cli --help`` displays help without an input.
    """

def main(argv: list[str] | None = None) -> int:
    """Parse the command line, dispatch one subcommand, and return its exit code.

    This is the entry point for the ``flowing`` executable. The first
    positional argument selects the subcommand; the next identifies the
    project directory and defaults to ``.``. Flowing-specific single-hyphen
    options are consumed by the dispatcher. Remaining ``--key value`` options
    are collected by :func:`flowing.interfaces.parse_kv_args` and passed to
    the selected command. Async subcommands run through ``asyncio.run``;
    ``compile`` is synchronous and is called directly.

    .. rubric:: Usage example

    .. code-block:: console

        $ flowing run . --workspace_root /ws --debug
        $ flowing repl /path/to/project
        $ flowing cli . -t root -m fast "Hello"
        $ flowing serve . -p 9000
        $ flowing web -a 0.0.0.0 -p 8080
        $ flowing test . -f ./tests/main_test.py --key val
        $ flowing compile .

    .. rubric:: Behavior notes

    - If ``argv`` is ``None``, the function uses ``sys.argv[1:]``.
    - An empty argument list or unknown subcommand prints the complete command
      list to stderr and returns :data:`flowing.interfaces.EXIT_USAGE_ERROR`.
      Subcommand names must match exactly in lowercase; prefixes and
      case-insensitive matches are not accepted.
    - If ``<path>`` is not an existing directory, the function returns
      :data:`flowing.interfaces.EXIT_USAGE_ERROR` before calling ``launch``.
    - ``-h`` or ``--help`` prints the selected command's usage to stdout and
      returns :data:`flowing.interfaces.EXIT_OK` without dispatching it.
      ``--help`` is consumed by the CLI and is never passed to project
      ``main``. This check precedes the missing-INPUT check for ``cli``, so
      ``flowing cli --help`` succeeds without an input; other argument-shape
      errors are still rejected during parsing.
    - Flowing-specific options are consumed only by their owning commands:
      ``-a`` and ``-p`` belong to ``serve`` and ``web``; ``-t``, ``-m``, and
      ``-v`` belong to ``cli``. They do not enter project ``main``'s keyword
      arguments. Collected ``--key value`` arguments are passed unchanged
      through the command's ``kwargs`` to ``launch`` and project ``main``.
    - ``cli`` dispatches to :func:`flowing.interfaces.oneshot.cmd_cli`. It
      accepts exactly one bare input token, while ``repl`` runs an interactive
      loop; the commands are not aliases.
    - The exit code returned by a subcommand is returned unchanged.
    - Each command handles its own ``launch`` failures by printing an error
      summary to stderr and returning :data:`flowing.interfaces.EXIT_RUNTIME_ERROR`.
      This function does not retry failed launches.

    :param argv: Arguments without the executable name. If ``None``, use
        ``sys.argv[1:]``.
    :return: One of :data:`flowing.interfaces.EXIT_OK`,
        :data:`flowing.interfaces.EXIT_RUNTIME_ERROR`, or
        :data:`flowing.interfaces.EXIT_USAGE_ERROR`.

    .. rubric:: See also

    :func:`flowing.interfaces.parse_kv_args` defines how project options
    are collected.
    :func:`flowing.interfaces.oneshot.cmd_cli`,
    :func:`flowing.interfaces.run.cmd_run`,
    :func:`flowing.interfaces.repl.cmd_repl`,
    :func:`flowing.interfaces.repl_debug.cmd_repl_debug`,
    :func:`flowing.interfaces.serve.cmd_serve`,
    :func:`flowing.interfaces.web.cmd_web`,
    :func:`flowing.interfaces.run.cmd_test`, and :func:`cmd_compile`
    define the individual command behaviors.
    """

def cmd_compile(path: str) -> int:
    """Compile Agent ``.fya`` files into neighboring Python modules.

    Recursively scan ``<path>`` for ``*.fya`` files. Tool and skill
    definitions (``TOOL.fya``, ``*.tool.fya``, and ``*.skill.fya``) are
    silently skipped; only Agent definitions are compiled. Each selected file
    becomes a same-directory ``.py`` file with the same basename, so imports
    such as ``from payment import PaymentAgent`` can resolve it. The command
    writes hash metadata to a ``.flowing.meta.yaml`` file beside each output.
    The metadata records the parsed source structure hash, output AST hash,
    and compiler version. This command is synchronous and does not launch a
    Runtime or execute project ``main``.

    .. rubric:: Usage example

    .. code-block:: console

        $ flowing compile .
        # agents/payment/agent.fya -> agents/payment/agent.py
        # tools/submit.fya is skipped
        # Each output directory receives .flowing.meta.yaml

    .. rubric:: Behavior notes

    - Parsable values in generated files are emitted as class-body
      assignments, for example ``system_prompt = Parsable('$./system-prompt.md')``.
    - A file is recompiled when its parsed ``.fya`` structure hash changes or
      the recorded compiler version differs from the current version.
    - If the output ``.py`` file was modified externally and its AST hash no
      longer matches the metadata, compilation stops instead of silently
      overwriting it. The command prints the conflicting path to stderr and
      returns :data:`flowing.interfaces.EXIT_RUNTIME_ERROR`. Other files
      already compiled in the same invocation are not rolled back; rerunning
      can continue the idempotent compilation.
    - The command does not remove orphan ``.py`` files that have no matching
      ``.fya`` source.
    - If the project contains no ``*.fya`` files, it prints that there is
      nothing to compile and returns :data:`flowing.interfaces.EXIT_OK`.
    - Running the command again when all hashes match leaves the outputs
      unchanged.

    :param path: Project directory as a filesystem path.
    :return: :data:`flowing.interfaces.EXIT_OK` on success or when no files
        need compilation, or :data:`flowing.interfaces.EXIT_RUNTIME_ERROR`
        when an output was modified externally.

    .. rubric:: See also

    :func:`flowing.compiler.compile_project` implements compilation.
    :func:`main` dispatches ``compile`` synchronously without an event loop.
    """
