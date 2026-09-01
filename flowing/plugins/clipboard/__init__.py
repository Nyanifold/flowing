"""``flowing.plugins.clipboard`` —— 剪贴板扩展包。

本包承载剪贴板扩展的全部公开符号：:class:`ClipboardPlugin`（阶段一
插件）、:func:`use_clipboard`（阶段二启用）以及三件工具类
（:class:`ClipboardCutTool` / :class:`ClipboardCopyTool` /
:class:`ClipboardPasteTool`）；对外 API 经本 ``__init__`` 统一再导出。

.. seealso:: :mod:`flowing.plugins.clipboard.clipboard`（启用方式与
    注册面清单）、:mod:`flowing.plugins.clipboard.tools`（三件工具）
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
