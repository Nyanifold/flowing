"""剪贴板三件工具：``ClipboardCutTool`` / ``ClipboardCopyTool`` /
``ClipboardPasteTool``（主模块 :mod:`flowing.plugins.clipboard.clipboard`）。
"""

from __future__ import annotations

from pathlib import Path

from flowing.agent import Agent
from flowing.params import schema_to_model
from flowing.tool import Tool, ToolDefinition

__all__ = ["ClipboardCopyTool", "ClipboardCutTool", "ClipboardPasteTool"]


def _check_enabled(caller: Agent) -> None:
    """剪贴板启用门：未 ``use_clipboard`` 的 Agent 调用三件工具一律报错。

    判定口径（规约只承诺「未启用 Agent 零开销：无状态键、无阈值属性」，
    未具名检测符号）：``clipboard_buffer`` 状态键已声明（或已有持久值）
    且阈值实例属性在位，二者缺一即视为未启用。
    """
    if ("clipboard_buffer" not in caller.state
            or not hasattr(caller, "clipboard_max_lines")
            or not hasattr(caller, "clipboard_max_chars")):
        raise ValueError(
            "剪贴板未启用：请在该 Agent 的 setup() 中调用 use_clipboard(self)")


def _resolve_path(path: str, cwd: str | None, *, what: str) -> Path:
    """``cwd`` 基准制解析（N-01①）：``cwd=None`` 仅收绝对路径；``cwd``
    给则必须绝对，相对路径相对它解析。违规抛 ``ValueError``（经
    ``Tool.__call__`` 包装为 error ``ToolResult``，LLM 可见）。"""
    if cwd is not None and not Path(cwd).is_absolute():
        raise ValueError(f"cwd 必须是绝对路径: {cwd!r}")
    p = Path(path)
    if not p.is_absolute():
        if cwd is None:
            raise ValueError(f"{what} 为相对路径且未提供 cwd 基准: {path!r}")
        p = Path(cwd) / p
    return p


def _line_offsets(lines: list[str]) -> list[int]:
    """各行的绝对起始偏移（``offsets[i]`` 为第 i+1 行的起始字符位置）。"""
    offsets = [0] * (len(lines) + 1)
    for i, line in enumerate(lines):
        offsets[i + 1] = offsets[i] + len(line)
    return offsets


def _parse_line_offset(value: str, lines: list[str], offsets: list[int],
                       *, what: str) -> int:
    """``"line,offset"`` 形态（行 1 起、offset 0 起）→ 绝对字符位置。

    offset 取值为 ``[0, 行文本长度]``（= 行尾，不含换行符）；越界抛
    ``ValueError``。
    """
    parts = value.split(",")
    if len(parts) != 2:
        raise ValueError(f"{what} 的 \"line,offset\" 形态非法: {value!r}")
    try:
        line_no, offset = int(parts[0]), int(parts[1])
    except ValueError:
        raise ValueError(
            f"{what} 的 \"line,offset\" 形态非法: {value!r}") from None
    if not 1 <= line_no <= len(lines):
        raise ValueError(
            f"{what} 行号越界: {line_no}（文件共 {len(lines)} 行）")
    line_text = lines[line_no - 1].rstrip("\r\n")
    if not 0 <= offset <= len(line_text):
        raise ValueError(
            f"{what} offset 超行尾: {offset}（第 {line_no} 行长 "
            f"{len(line_text)} 字符）")
    return offsets[line_no - 1] + offset


def _segment_range(text: str, start: int | str, end: int | str) -> tuple[int, int]:
    """``start`` / ``end`` 双形态 → ``(abs_start, abs_end)`` 绝对字符区间。

    两种形态不得混用：① 两个 ``int`` = 行号（1 起，start 含 end 不含）；
    ② 两个 ``"line,offset"`` 字符串（行 1 起、offset 0 起，end 位置排除）。
    越界 / 混用 / 区间倒置一律 ``ValueError``（先校验后落盘）。
    """
    lines = text.splitlines(keepends=True)
    offsets = _line_offsets(lines)
    if isinstance(start, int) and isinstance(end, int):
        # 行号形态：start 必须是既有行（1..len(lines)）；end 排除式，
        # 合法上限为 行数+1（与 paste 的 pos=行数+1 追加语义同口径）
        if not 1 <= start <= len(lines):
            raise ValueError(
                f"start 行号越界: {start}（文件共 {len(lines)} 行）")
        if not start <= end <= len(lines) + 1:
            raise ValueError(
                f"end 行号越界或区间倒置: end={end}（start={start}，"
                f"文件共 {len(lines)} 行）")
        return offsets[start - 1], offsets[end - 1]
    if isinstance(start, str) and isinstance(end, str):
        abs_start = _parse_line_offset(start, lines, offsets, what="start")
        abs_end = _parse_line_offset(end, lines, offsets, what="end")
        if abs_start > abs_end:
            raise ValueError(
                f"字符区间倒置: start={start!r} 在 end={end!r} 之后")
        return abs_start, abs_end
    raise ValueError(
        f"start/end 两形态不得混用（int 行号 或 \"line,offset\" 字符串）: "
        f"start={start!r}, end={end!r}")


def _read_source(path: Path, *, what: str = "path") -> str:
    """读源文件文本；不存在 / 是目录 → ``ValueError``（LLM 可自纠正）。"""
    if path.is_dir():
        raise ValueError(f"{what} 是目录而非文件: {path}")
    if not path.exists():
        raise ValueError(f"{what} 不存在: {path}")
    return path.read_text(encoding="utf-8")


def _write_file(path: Path, content: str) -> None:
    """覆盖写目标文件；父目录自动创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _emit_segment(caller: Agent, segment: str, origin: Path,
                  output: str, cwd: str | None, *, verb: str) -> str:
    """cut/copy 共用的「区段落目标」步骤：剪贴板（阈值双限，先校验后
    落盘）或文件（无阈值、覆盖写、父目录自动创建），返回收据文本。"""
    lines = len(segment.splitlines())
    chars = len(segment)
    if output == "clipboard":
        # 阈值双限（N-01⑥）：先校验后落盘——超限时源文件与缓冲均不变
        if lines > caller.clipboard_max_lines or chars > caller.clipboard_max_chars:
            raise ValueError(
                f"区段过大（{lines} 行 / {chars} 字符，超过剪贴板上限 "
                f"{caller.clipboard_max_lines} 行 / {caller.clipboard_max_chars} "
                f"字符）：请改用 output 文件输出")
        caller.state.clipboard_buffer = {
            "content": segment, "origin_path": str(origin),
            "lines": lines, "chars": chars,
        }
        return f"已{verb} {lines} 行 / {chars} 字符到剪贴板（来源：{origin}）"
    target = _resolve_path(output, cwd, what="output")
    _write_file(target, segment)
    return f"已{verb} {lines} 行 / {chars} 字符到文件 {target}"


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
        _check_enabled(caller)
        src = _resolve_path(path, cwd, what="path")
        text = _read_source(src)
        abs_start, abs_end = _segment_range(text, start, end)
        segment = text[abs_start:abs_end]
        # 写目标（剪贴板阈值先校验后落盘 / 文件覆盖写）→ 源文件删段
        # （标准剪切语义 N-01③：写入目标后源文件立即删除该段）
        receipt = _emit_segment(caller, segment, src, output, cwd, verb="剪切")
        _write_file(src, text[:abs_start] + text[abs_end:])
        return receipt


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
        _check_enabled(caller)
        src = _resolve_path(path, cwd, what="path")
        text = _read_source(src)
        abs_start, abs_end = _segment_range(text, start, end)
        segment = text[abs_start:abs_end]
        return _emit_segment(caller, segment, src, output, cwd, verb="复制")


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
        _check_enabled(caller)
        # 全部校验先于任何落盘（失败保留缓冲，LLM 可修正 pos 重试）
        target = _resolve_path(path, cwd, what="path")
        text = _read_source(target)
        if source == "clipboard":
            buffer = caller.state.clipboard_buffer
            if buffer is None:
                raise ValueError("剪贴板为空：请先 cut/copy 再 paste")
            content = buffer["content"]
        else:
            content = _read_source(
                _resolve_path(source, cwd, what="source"), what="source")
        lines = text.splitlines(keepends=True)
        offsets = _line_offsets(lines)
        if isinstance(pos, int):
            # 整行块插入到第 N 行之前；N = 行数+1 即追加末尾。块形态归一：
            # 插入内容补齐结尾换行；末尾追加且原文件末行无换行时先补换行，
            # 保持「整行块」语义
            if not 1 <= pos <= len(lines) + 1:
                raise ValueError(
                    f"pos 行号越界: {pos}（文件共 {len(lines)} 行，"
                    f"合法区间 1..{len(lines) + 1}）")
            block = content if content.endswith("\n") else content + "\n"
            abs_pos = offsets[pos - 1]
            if abs_pos == len(text) and text and not text.endswith("\n"):
                text += "\n"
                abs_pos += 1
            new_text = text[:abs_pos] + block + text[abs_pos:]
        elif isinstance(pos, str):
            # 行内 offset 处原样插入，不做换行归一（不改动行结构）
            abs_pos = _parse_line_offset(pos, lines, offsets, what="pos")
            new_text = text[:abs_pos] + content + text[abs_pos:]
        else:
            raise ValueError(
                f"pos 形态非法（int 行号 或 \"line,offset\" 字符串）: {pos!r}")
        _write_file(target, new_text)
        if source == "clipboard":
            # 一次性语义（N-01⑦）：仅成功且来源为剪贴板才清空
            caller.state.clipboard_buffer = None
        return f"已粘贴 {len(content.splitlines())} 行 / {len(content)} 字符到 {target}"
