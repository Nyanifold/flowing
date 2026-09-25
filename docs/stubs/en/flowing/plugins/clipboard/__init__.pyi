"""Clipboard extension package.

The package exports ``ClipboardPlugin`` for Runtime-level registration,
``use_clipboard()`` for per-Agent enablement, and the cut, copy, and paste
tools. Installing the plugin registers the tools globally; an Agent must
still add each desired tool explicitly. The Agent-local buffer and limits are
declared by ``use_clipboard()`` during ``setup()``.
"""
from .clipboard import ClipboardPlugin, use_clipboard
from .tools import ClipboardCopyTool, ClipboardCutTool, ClipboardPasteTool

__all__: list[str] = [
    "ClipboardCopyTool",
    "ClipboardCutTool",
    "ClipboardPasteTool",
    "ClipboardPlugin",
    "use_clipboard",
]
