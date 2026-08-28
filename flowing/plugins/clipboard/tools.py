"""剪贴板三件工具：``ClipboardCutTool`` / ``ClipboardCopyTool`` /
``ClipboardPasteTool``（主模块 :mod:`flowing.plugins.clipboard.clipboard`）。
"""

from __future__ import annotations

from flowing.agent import Agent
from flowing.params import schema_to_model
from flowing.tool import Tool, ToolDefinition

__all__ = ["ClipboardCopyTool", "ClipboardCutTool", "ClipboardPasteTool"]


class ClipboardCutTool(Tool):
    """``clipboard-cut``——剪切文件区段到剪贴板或文件（**可写工具**）。

    .. rubric:: 行为规约

    - 参数：``path``（目标文件，必填，遵循 ``cwd`` 基准口径——
      ``cwd=None`` 时仅绝对路径，N-01① 修订）；``cwd``（路径基准，
      默认 ``None``，给则必须绝对路径，对 ``path`` 与文件形态的
      ``output`` 同时生效）；
      ``start`` / ``end``（必填，两种形态**不得混用**——① 两个
      ``int`` = 行号，**1 起**，start 包含、end 排除；② 两个
      ``"line,offset"`` 形式字符串，行 1 起、offset 0 起，end 位置
      排除）；``output``（``"clipboard"``（默认）或绝对路径文件）。
    - **标准剪切语义（N-01③）**：区段写入目标后，源文件**立即删除
      该段**（落盘副作用，与编辑器 cut 一致）。
    - ``output="clipboard"``：写入 ``caller`` 的
      ``state.clipboard_buffer``（写透落盘）；**阈值双限**——内容
      行数 > ``clipboard_max_lines``（默认 500）**或**字符数 >
      ``clipboard_max_chars``（默认 10k）→ error ``ToolResult``
      （提示改用文件输出，N-01⑥）。
    - ``output`` 为文件：绝对路径、覆盖写、父目录自动创建；**无阈值
      限制**（大段内容走文件正是阈值的存在理由）。
    - 边缘情况：相对路径且无 ``cwd`` / ``cwd`` 非绝对 / 两形态混用 /
      行号或 offset 越界（行号
      超文件行数、offset 超行尾）/ 路径是目录 → error
      ``ToolResult``（LLM 可见、可自纠正，不抛异常）。
    - **危险面**：删改源文件。审批属策略层（``before_tool_call``）。

    .. rubric:: 测试案例

    - 前置：10 行文件 → 操作：``cut(path, 3, 6)`` → 期望：缓冲含第
      3-5 行、源文件剩 7 行；``cut(path, "2,0", "4,5")`` → 期望：按
      字符区间剪切。
    - 前置：600 行区段 → 操作：``cut(..., output="clipboard")`` →
      期望：阈值 error，源文件**不变**（先校验后落盘）。

    .. seealso:: :class:`ClipboardCopyTool`、:class:`ClipboardPasteTool`、
       :func:`flowing.plugins.clipboard.use_clipboard`
    """

    def __init__(self) -> None:
        """构造定义与内部校验模型（S-33 契约，构造期一次）。"""
        self.definition = ToolDefinition(
            name="clipboard-cut",
            description="剪切文件区段到剪贴板或文件（源文件删除该段）。",
            params_schema={
                "path": {"type": "string"},
                "cwd": {"type": ["string", "null"], "default": None},
                "start": {"type": ["integer", "string"]},
                "end": {"type": ["integer", "string"]},
                "output": {"type": "string", "default": "clipboard"},
            })
        self._has_caller = True   # 剪贴板缓冲与阈值经 caller 读取
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, path: str, start: int | str, end: int | str,
                      output: str = "clipboard", cwd: str | None = None,
                      caller: Agent) -> str:
        """剪切区段并返回收据文本（目标、行数/字符数）。

        .. rubric:: 调用关系（审计）

        - 调用：``caller.state.clipboard_buffer`` 写（时机：
          ``output="clipboard"`` 且阈值校验通过）
        - 被调：``flowing.tool.Tool.__call__`` 调度层
        """
        # 校验绝对路径/形态一致/越界 -> 读源文件切出区段 ->
        # output == "clipboard": 阈值校验（先校验后落盘）->
        # caller.state.clipboard_buffer = {"content", "origin_path",
        # "lines", "chars"}；否则覆盖写 output 文件 -> 源文件删段 ->
        # 收据文本
        ...


class ClipboardCopyTool(Tool):
    """``clipboard-copy``——复制文件区段到剪贴板或文件（只读源文件）。

    .. rubric:: 行为规约

    与 :class:`ClipboardCutTool` 全同，唯一差异：**不删除源文件区段**
    （无落盘副作用，源文件只读）。阈值双限与 ``output`` 语义一致。

    .. rubric:: 测试案例

    - 前置：10 行文件 → 操作：``copy(path, 3, 6)`` → 期望：缓冲含第
      3-5 行、源文件不变。
    """

    def __init__(self) -> None:
        """构造定义与内部校验模型（S-33 契约）。"""
        self.definition = ToolDefinition(
            name="clipboard-copy",
            description="复制文件区段到剪贴板或文件（源文件不变）。",
            params_schema={
                "path": {"type": "string"},
                "cwd": {"type": ["string", "null"], "default": None},
                "start": {"type": ["integer", "string"]},
                "end": {"type": ["integer", "string"]},
                "output": {"type": "string", "default": "clipboard"},
            })
        self._has_caller = True
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, path: str, start: int | str, end: int | str,
                      output: str = "clipboard", cwd: str | None = None,
                      caller: Agent) -> str:
        """复制区段并返回收据文本（源文件不变）。

        .. rubric:: 调用关系（审计）

        - 调用：``caller.state.clipboard_buffer`` 写（同 cut 的阈值路径）
        - 被调：``flowing.tool.Tool.__call__`` 调度层
        """
        # 校验 -> 读源文件切出区段 -> 写剪贴板（阈值）或文件 -> 收据
        ...


class ClipboardPasteTool(Tool):
    """``clipboard-paste``——把剪贴板或文件内容插入目标文件（**可写工具**）。

    .. rubric:: 行为规约

    - 参数：``path``（**目标文件**，必填，遵循 ``cwd`` 基准口径——
      ``cwd=None`` 时仅绝对路径，N-01① 修订）；``cwd``（路径基准，
      默认 ``None``，给则必须绝对路径，对 ``path`` 与文件形态的
      ``source`` 同时生效）；``source``
      （``"clipboard"``（默认）或绝对路径文件）；``pos``（必填，插入
      位置——``int`` = 行号（1 起，内容作为整行块插入到第 N 行
      **之前**，N = 行数+1 即追加末尾）；``"line,offset"`` = 行内
      offset 处插入，不改动行结构）。
    - **一次性语义（N-01⑦）**：``source="clipboard"`` 且粘贴成功后，
      **清空内存剪贴板**（``state.clipboard_buffer = None``）——防止
      剪贴板内容长期占用 state.jsonl 体积；连续粘贴需重新 cut/copy。
    - 边缘情况：剪贴板为空 / 相对路径且无 ``cwd`` / ``cwd`` 非绝对 /
      pos 越界 / 路径是目录 →
      error ``ToolResult``。清空只发生在**成功**粘贴后（失败保留
      缓冲，LLM 可修正 pos 重试）。
    - **危险面**：改目标文件。审批属策略层（``before_tool_call``）。

    .. rubric:: 测试案例

    - 前置：缓冲含 3 行、目标 5 行 → 操作：``paste(path, pos=3)`` →
      期望：内容插入为新的第 3-5 行、缓冲清空；再次 ``paste`` →
      期望：「剪贴板为空」error。
    - 前置：缓冲含文本 → 操作：``paste(path, pos="2,4")`` → 期望：
      插入第 2 行 offset 4 处、行数不变。

    .. seealso:: :class:`ClipboardCutTool`、:class:`ClipboardCopyTool`
    """

    def __init__(self) -> None:
        """构造定义与内部校验模型（S-33 契约）。"""
        self.definition = ToolDefinition(
            name="clipboard-paste",
            description="把剪贴板或文件内容插入目标文件指定位置。",
            params_schema={
                "path": {"type": "string"},
                "cwd": {"type": ["string", "null"], "default": None},
                "source": {"type": "string", "default": "clipboard"},
                "pos": {"type": ["integer", "string"]},
            })
        self._has_caller = True
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, path: str, source: str = "clipboard",
                      pos: int | str, cwd: str | None = None,
                      caller: Agent) -> str:
        """插入内容并返回收据文本；剪贴板来源成功后清空缓冲。

        .. rubric:: 调用关系（审计）

        - 调用：``caller.state.clipboard_buffer`` 读（时机：
          ``source="clipboard"``）与清空写（时机：粘贴成功收尾）
        - 被调：``flowing.tool.Tool.__call__`` 调度层
        """
        # 校验 -> 取内容（clipboard 读 caller.state.clipboard_buffer，
        # 空 -> error；文件则读 source）-> 按 pos 形态插入目标文件 ->
        # source == "clipboard" -> caller.state.clipboard_buffer = None
        # -> 收据文本
        ...
