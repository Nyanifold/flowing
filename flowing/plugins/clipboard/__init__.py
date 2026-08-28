"""``flowing.plugins.clipboard`` —— 剪贴板扩展包（N-01 / N-02）。

对外 API 经本 ``__init__`` 再导出不变：``ClipboardPlugin`` /
``use_clipboard`` / 三件工具类。
"""

from flowing.plugins.clipboard.clipboard import ClipboardPlugin, use_clipboard
from flowing.plugins.clipboard.tools import (
    ClipboardCopyTool,
    ClipboardCutTool,
    ClipboardPasteTool,
)

__all__ = [
    "ClipboardCopyTool",
    "ClipboardCutTool",
    "ClipboardPasteTool",
    "ClipboardPlugin",
    "use_clipboard",
]
