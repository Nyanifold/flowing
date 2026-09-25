"""Cut, copy, and paste file segments through Agent-scoped clipboard state.

The tools are registered by ``ClipboardPlugin`` and require the calling
Agent to enable the clipboard with ``use_clipboard()``. Registration in the
Runtime does not make a tool visible to an Agent's LLM; the Agent must add it
explicitly. Cut removes its source segment only after writing the destination,
whereas copy leaves the source unchanged. A successful paste from the
clipboard clears the buffer; a failed paste leaves it intact.

Paths must be absolute unless an absolute ``cwd`` is provided as the base for
relative paths. Integer line positions start at one. The start must identify
an existing line, while the exclusive end may be one greater than the file's
line count. A ``"line,offset"`` position uses a one-based line number that
must identify an existing line and a zero-based character offset from the
start through the end of that line (excluding its newline). Start and end
must use the same form, and the start must not be after the end. For paste,
an integer position identifies the line before which to insert, and one more
than the line count appends. Tool invocation errors are returned as error
``ToolResult`` values by ``Tool.__call__()``.
"""
from flowing.agent import Agent
from flowing.tool import Tool

__all__: list[str] = ["ClipboardCopyTool", "ClipboardCutTool", "ClipboardPasteTool"]

def _check_enabled(caller: Agent) -> None: ...
class ClipboardCutTool(Tool):
    """Cut a selected file segment into the Agent's clipboard or a file.

    The selected range is removed from the source file after the destination
    write succeeds. Clipboard output is subject to both configured limits;
    file output has no size limit and overwrites its target after creating
    the parent directory. If a clipboard limit is exceeded, the tool leaves
    both the source file and buffer unchanged. Invalid paths, ranges, limits,
    or disabled clipboard state raise ``ValueError`` from ``execute()`` and
    become tool errors when invoked through ``Tool.__call__()``.
    """
    def __init__(self) -> None:
        """Create the tool definition and argument validator."""
        ...
    async def execute(self, *, path: str, start: int | str, end: int | str, output: str = "clipboard", cwd: str | None = None, caller: Agent) -> str:
        """Cut the selected range and return a receipt describing its destination.

        ``start`` and ``end`` must use the same form: integer line numbers, or
        ``"line,offset"`` positions. Integer ranges use one-based line numbers;
        the start must identify an existing line, and the exclusive end may
        be one greater than the file's line count. String positions use an
        existing one-based line and a zero-based character offset through that
        line's end, excluding its newline. The start is included and the end
        is excluded. The destination is either the Agent's clipboard buffer or
        a file. Path, range, clipboard-limit, and
        enablement errors raise ``ValueError`` when ``execute()`` is called
        directly; ``Tool.__call__()`` converts them to an error
        ``ToolResult``.

        :param path: Source file path, resolved relative to ``cwd`` when given.
        :param start: Inclusive start position, using one-based line numbers
            or a ``"line,offset"`` string with a zero-based character offset.
        :param end: Exclusive end position in the same form as ``start``.
        :param output: ``"clipboard"`` or a file path to receive the cut text.
        :param cwd: Optional absolute base directory for relative paths.
        :param caller: Calling Agent, which must have enabled the clipboard.
        :return: Receipt text identifying the destination and segment size.
        :raises ValueError: A path, range, size limit, or enablement check fails.
        """
        ...
class ClipboardCopyTool(Tool):
    """Copy a selected file segment into the Agent's clipboard or a file.

    Its destination, range, path, and clipboard-limit rules match
    ``ClipboardCutTool``. It never modifies the source file.
    """
    def __init__(self) -> None:
        """Create the tool definition and argument validator."""
        ...
    async def execute(self, *, path: str, start: int | str, end: int | str, output: str = "clipboard", cwd: str | None = None, caller: Agent) -> str:
        """Copy the selected range and return a receipt describing its destination.

        ``start`` and ``end`` must use the same form: integer line numbers, or
        ``"line,offset"`` positions. Integer ranges use one-based line numbers;
        the start must identify an existing line, and the exclusive end may
        be one greater than the file's line count. String positions use an
        existing one-based line and a zero-based character offset through that
        line's end, excluding its newline. The start is included and the end
        is excluded. Unlike cut, this operation leaves the source file
        unchanged. Errors raise ``ValueError`` when called
        directly and become an error ``ToolResult`` through
        ``Tool.__call__()``.

        :param path: Source file path, resolved relative to ``cwd`` when given.
        :param start: Inclusive start position, using one-based line numbers
            or a ``"line,offset"`` string with a zero-based character offset.
        :param end: Exclusive end position in the same form as ``start``.
        :param output: ``"clipboard"`` or a file path to receive the copied text.
        :param cwd: Optional absolute base directory for relative paths.
        :param caller: Calling Agent, which must have enabled the clipboard.
        :return: Receipt text identifying the destination and segment size.
        :raises ValueError: A path, range, size limit, or enablement check fails.
        """
        ...
class ClipboardPasteTool(Tool):
    """Insert clipboard or file content at a position in a target file.

        An integer ``pos`` must be in the range from one through one more than
        the file's line count. It inserts a whole-line block before that
        one-based line; using one more than the line count appends it. A
        ``"line,offset"`` position uses an existing one-based line and a
        zero-based character offset through that line's end, excluding its
        newline, without changing the surrounding line structure. Validation precedes file writes. A
    successful paste from the clipboard clears the buffer; a failed paste
    leaves it available for a corrected retry. Pasting from a file does not
    clear the clipboard buffer.
    """
    def __init__(self) -> None:
        """Create the tool definition and argument validator."""
        ...
    async def execute(self, *, path: str, source: str = "clipboard", pos: int | str, cwd: str | None = None, caller: Agent) -> str:
        """Insert the selected source text and return a receipt.

        Invalid paths or positions, an empty clipboard, and use before
        ``use_clipboard()`` raise ``ValueError`` when called directly and
        become an error ``ToolResult`` through ``Tool.__call__()``. Validation
        completes before writes; the clipboard is cleared only after a
        successful paste whose source is the clipboard. Reading from a file
        leaves the clipboard unchanged.

        :param path: Target file path, resolved relative to ``cwd`` when given.
        :param source: ``"clipboard"`` or a file path to read from.
        :param pos: Insertion position as a one-based line number from one
            through line count plus one, or a ``"line,offset"`` string whose
            line exists and whose zero-based character offset is within it.
        :param cwd: Optional absolute base directory for relative paths.
        :param caller: Calling Agent, which must have enabled the clipboard.
        :return: Receipt text identifying the target and inserted segment size.
        :raises ValueError: The path, position, clipboard state, or enablement is invalid.
        """
        ...
