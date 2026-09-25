"""Clipboard extension with Runtime-level registration and per-Agent state.

Install ``ClipboardPlugin`` to register the three tools in the Runtime's
global tool registry. This does not make them visible to an Agent's LLM; the
Agent must add each tool explicitly. Call ``use_clipboard()`` from each
Agent's ``setup()`` to declare its own persistent buffer and configure its
limits. The buffer is JSON-serializable Agent state and is restored with that
Agent.

The per-Agent ``clipboard_buffer`` value is either ``None`` or a JSON object
with ``content``, ``origin_path``, ``lines``, and ``chars`` fields. Each Agent
has an independent buffer; moving content between Agents requires using a
file as an intermediary. A successful paste from the buffer clears it, so the
same buffered segment cannot be pasted again without another cut or copy.

Cut and copy can write a selected segment either to the Agent's buffer or to
a file. Buffer writes are rejected when the segment exceeds either configured
limit; file output has no size limit. A successful paste from the buffer
clears it, while a failed paste leaves it available. File paths must be
absolute unless an absolute ``cwd`` is supplied as the base for relative
paths. Line positions are one-based; ``"line,offset"`` positions use a
zero-based character offset from the beginning of an existing line through
its end, excluding the newline. Cut and copy ranges include ``start`` and
exclude ``end``. Integer paste positions insert a whole-line block before the
selected line, and one past the final line appends it; a ``"line,offset"``
paste inserts within a line without changing its surrounding line structure.

.. rubric:: Registration surface

``runtime.install(ClipboardPlugin())`` registers ``clipboard-cut``,
``clipboard-copy``, and ``clipboard-paste`` globally. ``use_clipboard()``
declares the Agent-local ``clipboard_buffer`` state key and sets the
``clipboard_max_lines`` and ``clipboard_max_chars`` attributes. The extension
declares no hook points and registers no handlers.

.. seealso:: :mod:`flowing.plugins.clipboard.tools`,
    :class:`ClipboardPlugin`, :func:`use_clipboard`
"""
from typing import ClassVar
from flowing.agent import Agent
from flowing.plugins import Plugin
from flowing.runtime import Runtime

__all__: list[str] = ["ClipboardPlugin", "use_clipboard"]

class ClipboardPlugin(Plugin):
    """Register the three clipboard tools with a Runtime.

    Installation is synchronous registration only. It does not declare
    per-Agent state or read other plugin state; ``use_clipboard()`` declares
    each Agent's buffer and limits separately. Constructing the plugin has no
    registration side effects.
    """
    name: ClassVar[str]
    """Runtime-local plugin registration name, set to ``"clipboard"``."""
    dependencies: ClassVar[list[str]]
    """Declared plugin dependencies. This plugin has no dependencies."""
    def install(self, runtime: Runtime) -> None:
        """Register the cut, copy, and paste tools in the Runtime's global
        tool registry.

        The call registers all three tools synchronously. It does not declare
        Agent state; ``use_clipboard()`` does that separately during Agent
        setup.

        :param runtime: Runtime currently installing the plugin.
        :return: ``None``.
        """
        ...
def use_clipboard(agent: Agent, *, max_lines: int = 500, max_chars: int = 10_000) -> None:
    """Enable clipboard state and limits for one Agent, normally in setup().

    This declares the Agent-local ``clipboard_buffer`` state key and assigns
    the ``clipboard_max_lines`` and ``clipboard_max_chars`` instance
    attributes read by the tools. The buffer is JSON-serializable state,
    persisted with the Agent and restored during recovery. It does not add
    tools to the Agent; tool visibility remains an explicit ``add_tool()``
    decision.

    The buffer defaults to ``None`` (empty); a stored value contains
    ``content``, ``origin_path``, ``lines``, and ``chars``. Each Agent has an
    independent buffer, so moving content between Agents requires using a
    file as an intermediary. The tools read the configured limits through
    their injected ``caller``.

    Both limits must be positive integers. Validation happens before state
    registration or attribute assignment, so invalid input has no
    registration side effects. Repeated calls are allowed: state registration
    reuses persisted state, and the latest limits apply. Calling this during
    setup of a recovered Agent is also supported. This function registers no
    hooks. Before it is called, an Agent has no clipboard state key or limit
    attributes; calling one of the clipboard tools then returns a tool error
    indicating that clipboard support is not enabled.

    .. rubric:: Usage example

    .. code-block:: python

        async def setup(self):
            use_clipboard(self)  # Defaults to 500 lines and 10,000 characters.
            self.add_tool("clipboard-cut")
            self.add_tool("clipboard-copy")
            self.add_tool("clipboard-paste")

    :param agent: Agent whose clipboard capability is being enabled.
    :param max_lines: Maximum number of lines retained in the buffer; defaults
        to 500.
    :param max_chars: Maximum number of characters retained in the buffer;
        defaults to 10,000.
    :raises ValueError: Either limit is not a positive integer.
    """
