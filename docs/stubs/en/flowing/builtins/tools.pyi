"""``flowing.builtins.tools`` — the builtin tools registered in ``builtin::``.

This module defines eight builtin tools as ordinary :class:`flowing.tool.Tool`
subclasses. :func:`flowing.builtins.register_builtins` registers them in
``builtin::``:

- The core tools are :class:`SubagentInvokeTool` (``subagent-invoke``), which
  invokes a subagent, and :class:`FinishTool` (``finish``), which a subagent
  can use to return an optional structured result.
- The standard file and shell tools are :class:`ReadTool`,
  :class:`WriteTool`, :class:`BashTool`, :class:`EditTool`, :class:`GrepTool`,
  and :class:`GlobTool`.

.. rubric:: Shared behavior

Registration does not make a tool visible to the LLM. An Agent must explicitly
declare tools in its ``.fya`` ``tools:`` list or call ``add_tool`` before they
appear in ``Context.tools``. In particular, registering ``write``, ``bash``,
or ``edit`` does not expose their file-writing or command-execution
capabilities. The tools provide capabilities; approval policy belongs in a
``before_tool_call`` hook or an approval plugin.

File and directory path arguments use an explicit ``cwd`` base. By default,
paths must be absolute. A relative path is accepted only when that call
supplies a non-``None`` absolute ``cwd``; the path is then resolved against
that directory. A relative ``cwd`` is invalid. With no base, the LLM-visible
current directory is not inferred. A relative path without a base or a
non-absolute ``cwd`` produces a ``ToolResult`` with ``status="error"``.
Pattern arguments remain patterns: ``GrepTool.pattern`` is a ripgrep regular
expression, and ``GlobTool.pattern`` is a filesystem glob.

For the six file and shell tools, invalid arguments and I/O failures—such as a
directory passed where a file is expected, a missing file, invalid UTF-8, or a
missing ``rg`` executable—are returned as LLM-visible ``error`` results. They
do not trigger error hooks; the LLM can use the message to correct its call.

.. rubric:: Example

An Agent can provide its own working directory to a declared tool through a
``Parsable`` template:

.. code-block:: yaml

    tools:
      - read:
          args:
            cwd: "{{ cwd }}"
    ---
    $script:
    async def setup(self, working_dir: str):
        self.cwd = working_dir

When the LLM calls ``read(path="src/main.py")``, the declaration supplies the
Agent's ``cwd`` as the base, so the LLM only needs to provide a relative path.

.. seealso:: :mod:`flowing.builtins.agents`, :mod:`flowing.tool`, and
    :mod:`flowing.agent`.
"""

from typing import Any

from flowing.agent import Agent
from flowing.paths import NamingRules
from flowing.tool import Tool

__all__ = [
    "BashTool",
    "EditTool",
    "GlobTool",
    "GrepTool",
    "ReadTool",
    "WriteTool",
    "TOOL_NAMING",
]

TOOL_NAMING: NamingRules


class ReadTool(Tool):
    """Read a UTF-8 text file without modifying it.

    The tool can return a line window using the zero-based ``offset`` and
    optional ``limit`` arguments. File reads do not perform write operations.
    A single line longer than 2,000 characters is truncated.

    The ``path`` argument follows the module's explicit ``cwd`` path-base
    rules. Directory paths, missing files, invalid UTF-8, relative paths
    without ``cwd``, and a non-absolute ``cwd`` become LLM-visible error
    results.

    .. seealso:: :class:`flowing.builtins.GrepTool` and
        :class:`flowing.builtins.GlobTool` for related read-only tools.
    """

    async def execute(
        self,
        *,
        path: str,
        cwd: str | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> str:
        """Read a file and return the selected lines with line-number prefixes.

        Each returned line has the form ``<line-number>\t<content>``. The
        ``offset`` and ``limit`` values select the line window.

        .. rubric:: Behavior

        - Line numbers use the same zero-based indexing as ``offset``.
        - A directory path, a missing file, invalid UTF-8, a relative path
          without a ``cwd`` base, or a non-absolute ``cwd`` raises
          ``ValueError``. Other filesystem errors can propagate from this
          method; ``Tool.__call__`` converts ordinary execution exceptions to
          a ``ToolResult`` with ``status="error"`` for the LLM to see.
        """
        ...


class WriteTool(Tool):
    """Overwrite a UTF-8 text file, creating missing parent directories.

    This is a write-capable tool: it replaces the whole file rather than
    appending. It can overwrite an arbitrary path. Approval and path allowlists
    belong to policy code, such as a ``before_tool_call`` hook; this tool does
    not impose them itself. ``path`` follows the module's explicit ``cwd``
    rules.
    """

    async def execute(
        self, *, path: str, content: str, cwd: str | None = None
    ) -> str:
        """Overwrite the file and return a receipt containing the path and character count.

        A directory path, a relative path without a ``cwd`` base, or a
        non-absolute ``cwd`` raises ``ValueError``. Other filesystem errors
        can propagate from this method; ``Tool.__call__`` converts ordinary
        execution exceptions to a ``ToolResult`` with ``status="error"`` for
        the LLM to see.
        """
        ...


class BashTool(Tool):
    """Run a shell command with ``/bin/bash``; this tool can modify the host environment.

    The command runs as ``/bin/bash -c`` and returns text containing
    ``stdout``, ``stderr``, and the ``exit_code``. It can run arbitrary
    commands with the permissions of the Agent process, so it is a high-risk
    write-capable tool. Approval and command filtering belong to policy code,
    such as a ``before_tool_call`` hook.

    A non-zero exit code is returned as normal output rather than treated as
    an exception. ``timeout`` is measured in seconds and defaults to 120; on
    timeout, the process group is killed and an error result is returned.
    ``cwd`` is optional but, when supplied, must be absolute. Commands are
    non-interactive because stdin is closed, and each call starts a fresh
    process rather than retaining session state.

    .. seealso:: :class:`flowing.builtins.WriteTool` and
        :class:`flowing.builtins.EditTool` for file-writing tools.
    """

    async def execute(
        self,
        *,
        command: str,
        timeout: int = 120,
        cwd: str | None = None,
    ) -> str:
        """Run the command and return text containing its output and exit code.

        .. rubric:: Behavior

        - If the timeout expires, the whole process group is killed, including
          child processes in its session. The call returns an error result.
        - If cancellation interrupts the call, the whole process group is
          killed and reaped. The returned text contains output collected so
          far and an interruption notice; ``Tool.__call__`` wraps it as a
          ``cancelled`` result. Output is collected incrementally so partial
          output remains available.
        - A non-absolute ``cwd`` raises ``ValueError``. ``Tool.__call__`` wraps
          it as a ``status="error"`` result for the LLM to see.
        """
        ...


class EditTool(Tool):
    """Replace an exact string in a UTF-8 text file.

    By default, the old string must occur exactly once; this avoids silently
    editing an ambiguous location. ``replace_all=True`` replaces every
    occurrence. The operation can modify an arbitrary path, so approval
    belongs to policy code such as a ``before_tool_call`` hook. The edit is
    not atomic: the tool reads the file, edits its contents, and writes it
    back. Concurrent edits to one file can overwrite one another, although a
    single Agent executes its tools serially.

    The ``path`` argument follows the module's explicit ``cwd`` rules.
    """

    async def execute(
        self,
        *,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        cwd: str | None = None,
    ) -> str:
        """Replace the requested text and return a receipt with the path and replacement count.

        If the old string is absent, or occurs more than once while
        ``replace_all`` is false, the method raises ``ValueError`` and leaves
        the file unchanged. Other filesystem errors may propagate from this
        method; ``Tool.__call__`` converts ordinary execution exceptions to
        an LLM-visible ``status="error"`` result.
        """
        ...


class GrepTool(Tool):
    """Search file contents with ripgrep's regular-expression engine.

    The tool delegates to the ``rg`` executable and returns matching lines
    with paths and line numbers. The host must have ripgrep installed; the
    tool does not fall back to a Python scan. It follows ripgrep's default
    behavior: it respects ``.gitignore`` and skips hidden files.

    ``pattern`` is a ripgrep regular expression. ``path`` and optional ``cwd``
    follow the module's explicit path-base rules. Results use absolute paths,
    one line per match in the form ``<path>:<line-number>:<content>``. No
    matches produce ``(no matches)``. Results are limited to 250 matching
    lines and indicate when they are truncated. Ripgrep exit code 1 means no
    match and is not an error; a missing executable or another non-zero exit
    code produces an LLM-visible error result.
    """

    async def execute(
        self,
        *,
        pattern: str,
        path: str,
        cwd: str | None = None,
        glob: str | None = None,
    ) -> str:
        """Run ``rg`` and return matching lines with their paths and line numbers.

        A missing ``rg`` executable or an execution failure with an exit code
        other than 0 or 1 raises ``RuntimeError``. ``Tool.__call__`` wraps it
        as an LLM-visible ``status="error"`` result. Exit code 1 means no
        matches and returns ``(no matches)``. Results are truncated with a
        notice after 250 matching lines.
        """
        ...


class GlobTool(Tool):
    """List files matching a glob pattern, with the most recently modified first.

    ``**`` in the pattern matches recursively. The result contains absolute
    file paths, one per line; directories are not included. No matches produce
    ``(no matches)``. Results are limited to 100 and indicate when they are
    truncated. This tool enumerates the filesystem directly: it does not
    follow ``.gitignore`` and does not filter file contents. Those semantics
    differ from :class:`flowing.builtins.GrepTool`.

    ``path`` and optional ``cwd`` follow the module's explicit path-base
    rules. A missing base directory or a path that is not a directory becomes
    an LLM-visible error result.
    """

    async def execute(
        self, *, pattern: str, path: str, cwd: str | None = None
    ) -> str:
        """Enumerate matching files and return their absolute paths, newest first.

        A missing base directory or a path that is not a directory raises
        ``ValueError``. ``Tool.__call__`` wraps it as an LLM-visible
        ``status="error"`` result. Results are limited to 100 paths; no
        matches return ``(no matches)``.
        """
        ...


class FinishTool(Tool):
    """Optional structured-result tool for ending a subagent's logical turn.

    The tool's base ``definition`` contains only the ``summary`` parameter.
    Structured result fields come from the Agent's ``output:`` declaration in
    its ``tools:`` entry, which expands those fields into additional
    parameters for ``finish``. A subagent most often does not call ``finish``:
    when its turn ends naturally, its plain-text ``last_result`` is returned
    to the parent. ``finish`` is an optional way to submit a structured result
    early.

    .. rubric:: Example

    .. code-block:: yaml

        # reviewer-agent.fya
        tools:
          - finish:
              output:
                score:
                  type: integer
                  minimum: 0
                  maximum: 100
                  description: Code quality score.
                pass:
                  type: boolean
                  description: Whether the review passed.
                issues:
                  type: array
                  description: List of findings.

    .. rubric:: Behavior

    - The ``output`` fields are carried through
      :class:`flowing.tool.ToolEntry`'s ``override_params``. The core does not
      assign special meaning to the ``output`` key; it is an ordinary
      override. The registry's original definition is not modified.
    - The declared fields merge with ``finish``'s ``summary`` parameter for
      ``llm_definition()`` and ``resolve()``. ``output`` itself is not an
      argument visible to the LLM.
    - Without an ``output`` declaration, the LLM can finish with free text in
      ``summary``. With a declaration, the returned value contains the
      structured fields along with ``summary``.
    - ``output:`` and ``args:`` are combined in the same
      ``ToolEntry.override_params`` mapping. An omitted field is removed, a
      changed type replaces the prior type, and a full schema redeclaration
      is not required.
    - The tool becomes visible only when the Agent explicitly lists it under
      ``tools:`` or calls ``add_tool("finish")``.
    - After the call, remaining parallel tool calls in the turn finish
      normally. The payload is stored in ``Agent.last_result`` and returned to
      the parent. The paired TOOL message is added normally, and that message
      carries ``turn_end=True``.

    .. seealso:: :class:`flowing.tool.ToolEntry` and
        :meth:`flowing.agent.Agent.add_tool`.
    """

    async def execute(
        self, *, summary: str = "", caller: Agent, **kwargs: Any
    ) -> dict[str, Any]:
        """Collect the structured result fields and request the current subagent turn to finish.

        ``kwargs`` contains the dynamic fields expanded from the Agent's
        ``output:`` declaration. The return value has the form
        ``{"summary": ..., **dynamic_fields}``. Without an ``output:``
        declaration, ``summary`` is the free-text result; with one, the same
        return structure contains the declared result fields.

        .. rubric:: Behavior

        - The payload is assigned to ``caller.current_turn.finish_output``.
          This requests natural turn completion after the remaining tool
          calls finish. The subagent's completion path stores the payload in
          ``Agent.last_result`` and returns it to the parent.
        - ``caller.current_turn`` must not be ``None``; the tool runs only
          inside a logical turn.

        .. seealso:: :class:`flowing.agent.TurnContext` for the object that
            exists during a logical turn.
        """
        ...


class SubagentInvokeTool(Tool):
    """Core builtin that invokes a declared subagent.

    This is the single tool used to invoke every subagent type; Flowing does
    not create a separate ``ToolDefinition`` per type. Descriptions of
    available subagent types are provided to the LLM in an
    ``<available_subagents>`` XML catalog. The tool has five common parameters:
    ``prompt`` (required), ``name`` (optional name for a new instance, usable
    later to resume it), ``agent_type`` (type for a new instance), ``resume``
    (name of an existing instance), and ``asynchronized`` (background mode).
    ``agent_type`` and ``resume`` are mutually exclusive. Additional
    type-specific arguments are described in the catalog and aggregated by
    ``SubagentEntry.resolve()``.

    Runtime construction registers this tool, but does not make it visible.
    The Agent must explicitly declare it in ``.fya`` ``tools:`` or with
    ``add_tool`` before it appears in ``Context.tools``.

    .. rubric:: Example

    .. code-block:: text

        # Create and name a subagent.
        subagent-invoke(name="my-reviewer", agent_type="coder", prompt="Review the auth module")
        # Resume that instance later.
        subagent-invoke(resume="my-reviewer", prompt="Continue with the payment module")
        # Return a receipt without waiting; the result arrives later as a SUBAGENT message.
        subagent-invoke(agent_type="coder", prompt="Run the full test suite", asynchronized=true)

    .. rubric:: Behavior

    - With ``asynchronized=False`` (the default), the tool calls
      ``invoke_subagent`` synchronously, returns all ``SubagentResult`` fields
      flattened into its result dictionary (``status``, ``name_alias``,
      ``subagent_id``, ``result``, and ``subagent_status``), and does not send a
      second SUBAGENT message for the same result.
    - With ``asynchronized=True``, creation is awaited first. A creation
      failure becomes an LLM-visible ``error`` result. After successful
      creation, the tool immediately returns a pending receipt of the form
      ``{"invoked": ..., "status": "started"}`` without waiting for the
      subagent to finish. The subagent runs in the background; its result is
      later queued for the parent as ``Message(kind=SUBAGENT)`` and becomes
      available to the LLM in a later turn.
    - ``agent_type`` and ``resume`` are mutually exclusive, and at least one
      must be supplied. Invalid combinations produce an LLM-visible error
      result.
    - This tool delegates creation and destruction to
      ``invoke_subagent``; it does not create or destroy Agent instances
      directly and does not render the catalog itself.
    - Runtime registration does not add this tool to any Agent's
      ``Context.tools``. The Agent must declare it explicitly.

    .. seealso::

        - :meth:`flowing.agent.Agent.invoke_subagent` for the invocation
          pipeline.
        - :class:`flowing.subagents.SubagentEntry` for the catalog and
          argument aggregation.
        - :class:`flowing.builtins.FinishTool` for optional structured
          subagent results.
    """

    async def execute(
        self,
        *,
        caller: Agent,
        name: str = "",
        agent_type: str = "",
        prompt: str = "",
        resume: str = "",
        asynchronized: bool = False,
        **kwargs: Any,
    ) -> Any:
        """Call ``caller.invoke_subagent()`` and return a result or start receipt.

        .. rubric:: Behavior

        - ``agent_type`` and ``resume`` are mutually exclusive, and at least
          one must be supplied; an empty string counts as omitted. An invalid
          combination raises ``ValueError``, which ``Tool.__call__`` wraps as
          an LLM-visible ``status="error"`` result.
        - ``prompt`` is required by the tool schema. If omitted, the
          Agent-level argument validation returns an LLM-visible error. An
          empty string is rejected by this method with ``ValueError`` and is
          returned through the same error-result path.
        - ``kwargs`` contains the other arguments declared for the chosen
          subagent type. They are passed to ``invoke_subagent()`` for
          aggregation by ``SubagentEntry.resolve()``.
        - With ``asynchronized=True``, this method waits for subagent creation
          but not completion. Creation failures are wrapped as ``error`` or
          ``blocked`` results by ``Tool.__call__``. On success, it returns a
          receipt with ``status="started"``; the background result is later
          queued to the parent as ``Message(kind=SUBAGENT)``.
        - With the default ``False``, this method waits for the subagent and
          flattens all fields from the returned ``SubagentResult`` into its
          result dictionary. It does not enqueue an additional SUBAGENT
          message.
        """
        ...
