"""``flowing.tool.cli`` — ``CliTool``: a command-line tool driven by a Jinja2 command template.

This is one of the zero-code tool forms, declared with ``type: cli`` in a
``.fya`` file. The public type is re-exported from ``flowing.tool``.
"""
from typing import Any, Literal
from flowing.tool.core import Tool, ToolDefinition

class _ShellRaw(str): ...
def _shell_raw_filter(value: Any) -> _ShellRaw: ...
def _shell_finalize(value: Any) -> str: ...

class CliTool(Tool):
    """A CLI tool that renders a Jinja2 command template and runs it through a shell.

    .. rubric:: Overview

    A ``type: cli`` declaration creates this tool. The declaration must provide
    an ``args`` schema; the tool does not infer parameters. The ``command``
    value is a Jinja2 template rendered with the arguments supplied by the
    LLM. By default, inserted values are escaped. A template must explicitly
    use ``{{ arg | raw }}`` to insert an unescaped value, and Flowing logs a
    warning when that filter is used.

    .. rubric:: Example

    .. code-block:: yaml

        # run-tests/TOOL.fya
        name: run-tests
        type: cli
        description: "Run the project test suite when test results are needed."
        shell: sh
        command: |
          cd {{ working_dir }} && python -m pytest {{ test_path }} --json-report
        args:
          working_dir:
            type: string
            description: Absolute path to the project root.
            # Full form; without a default, this argument is required.
          test_path:
            type: string
            default: src/
            description: Path to test.
            # Full form with a default; this argument is optional.
        output:
          type: object
          properties:
            exit_code:
              type: integer
            stdout:
              type: string
            stderr:
              type: string

    .. rubric:: Behavior

    - ``shell`` accepts ``sh`` (the default), ``bash``, ``ps``, ``powershell``,
      or ``cmd``. An unsupported value raises
      :class:`flowing.errors.FormatError` during construction, so the invalid
      declaration fails before execution.
    - The return value is always a dictionary with ``exit_code``, ``stdout``,
      and ``stderr`` keys. An ``output:`` declaration is stored in
      :attr:`ToolDefinition.output_schema`; it does not extract or reshape
      these fields.
    - A non-zero process exit code is ordinary result data, not a tool error.
      A framework-level failure, such as failure to start the process, produces
      an ``error`` result.

    :raises flowing.errors.MissingSchemaError: The ``.fya`` declaration omits
        ``args``.

    .. seealso:: :class:`flowing.tool.RequestTool` for another zero-code tool.
    """
    def __init__(self, *, definition: ToolDefinition, command: str, shell: Literal["sh", "bash", "ps", "powershell", "cmd"] = "sh") -> None:
        """Create a CLI tool.

        :param definition: The tool declaration. Its parameter schema must come
            from the ``args:`` field in the ``.fya`` file.
        :param command: The Jinja2 command template. Inserted values are escaped
            automatically; ``| raw`` bypasses escaping and logs a warning.
        :param shell: The shell used to execute the command. The default is
            ``sh``. An unsupported value raises
            :class:`flowing.errors.FormatError` during construction.
        """
        ...

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Render the command template, run it, and return process details.

        The result has exactly three keys: ``exit_code``, ``stdout``, and
        ``stderr``.

        .. rubric:: Behavior

        - The template finalizer escapes inserted values with ``shlex.quote``.
          Explicit ``{{ arg | raw }}`` bypasses that escaping and emits a
          warning each time rendering uses the filter.
        - A non-zero exit code is included as normal result data. A framework
          failure, such as failure to start the subprocess, raises an exception
          that ``Tool.__call__`` wraps in a ``ToolResult`` with
          ``status="error"``.
        - Standard output and standard error are decoded as UTF-8, replacing
          undecodable bytes.
        """
        ...
