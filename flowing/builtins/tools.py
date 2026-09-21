"""``flowing.builtins.tools`` —— 内置工具全集（``builtin::`` 命名空间）。

.. rubric:: 功能介绍

本模块定义框架出厂内置的八个工具，全部为普通 :class:`flowing.tool.Tool`
子类，经 :func:`flowing.builtins.register_builtins` 注册进 ``builtin::``
命名空间：

- 核心内置：:class:`SubagentInvokeTool` （``subagent-invoke``，子智能体
  唤起入口）与 :class:`FinishTool` （``finish``，子 Agent 可选交卷）；
- 标准文件 / shell 工具：:class:`ReadTool` / :class:`WriteTool` /
  :class:`BashTool` / :class:`EditTool` / :class:`GrepTool` /
  :class:`GlobTool`。

.. rubric:: 全局约定（跨符号、影响使用的约定）

注册不等于可见（安全边界）：任何工具对 LLM 可见都必须经 Agent 级显式
声明（``.fya`` ``tools:`` 或 ``add_tool``）——可写文件、执行命令的危险
工具（``write`` / ``bash`` / ``edit``）不会因为注册就进入任何 Agent 的
``Context.tools``。本模块只提供「能干什么」；「该不该批准」（Bash 任意
命令、Write/Edit 覆盖写）是策略，由 ``before_tool_call`` 钩子 / 审批
插件承担。

路径基准（``cwd``）口径：所有文件 / 目录参数（``path`` / ``pattern``）
默认只收绝对路径；相对路径当且仅当该次调用的 ``cwd`` 非 ``None`` 时
允许，相对 ``cwd`` 解析；``cwd`` 自身必须是绝对路径（默认 ``None``，
即不给基准）。相对路径无基准或 ``cwd`` 非绝对 → ``status="error"`` 的
``ToolResult`` （LLM 可见、可自纠正）。设计意图：基准必须由调用方显式
给出，LLM 视角下没有隐含的「当前目录」。

工具失败是正常产物：六个文件 / shell 工具的参数错误与 IO 错误（路径是
目录 / 文件不存在 / 非 UTF-8 编码 / ``rg`` 未安装等）一律以
``status="error"`` 的 ``ToolResult`` 呈现（LLM 可见、不触发错误钩子），
LLM 应看到错误文本并自行修正调用。

.. rubric:: 使用示例

``cwd`` 同时是「``.fya`` 定义期经智能体属性传参」的示范位：Agent 自己
有 ``cwd`` 属性（如 ``setup`` 中赋值 ``self.cwd = "/srv/proj"``）时，
``.fya`` 的 tools 条目把 ``cwd`` 参数覆写为引用该属性的 Parsable 模板
（:mod:`flowing.parsable`——声明层的模板值，调用期以调用方 Agent 为
上下文求值），实现调用期自动注入::

    tools:
      - read:
          args:
            cwd: "{{ cwd }}"      # Parsable 模板：渲染上下文含实例属性
    ---
    $script:
    async def setup(self, working_dir: str):
        self.cwd = working_dir

之后 LLM 调 ``read(path="src/main.py")`` 时 ``cwd`` 已由声明层固定为该
Agent 的 ``cwd``——LLM 只需给相对路径，基准管理是声明层的职责。

.. seealso:: :mod:`flowing.builtins.agents`、
    :mod:`flowing.tool`、:mod:`flowing.agent`
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
from pathlib import Path

from flowing.agent import Agent
from flowing.params import schema_to_model
from flowing.tool import TOOL_NAMING, Tool, ToolDefinition

__all__ = [
    "BashTool",
    "EditTool",
    "GlobTool",
    "GrepTool",
    "ReadTool",
    "WriteTool",
    "TOOL_NAMING",   # re-export（单一权威在 flowing.tool，见文件尾注）
]

_MAX_LINE_LENGTH = 2000
"""read 输出的单行截断长度（字符数上限，超长行截断的实现口径）。"""

_GREP_MAX_LINES = 250
"""grep 输出的匹配行截断上限（行数，默认 250）。"""

_GLOB_MAX_RESULTS = 100
"""glob 输出的结果数截断上限（个数，默认 100）。"""


def _resolve_under_cwd(path: str, cwd: str | None) -> Path:
    """路径参数 → 绝对 ``Path`` （``cwd`` 基准口径的唯一落点）。

    ``cwd`` 给则必须是绝对路径；``path`` 相对仅当 ``cwd`` 非 ``None``
    （相对 ``cwd`` 解析），``cwd=None`` 时仅收绝对路径。违反即
    ``ValueError``——经 ``Tool.__call__`` 包装为 ``status="error"`` 的
    ``ToolResult`` （LLM 可见、可自纠正），不向调用方抛。
    """
    if cwd is not None and not Path(cwd).is_absolute():
        raise ValueError(f"cwd must be an absolute path: {cwd!r}")
    p = Path(path)
    if p.is_absolute():
        return p
    if cwd is None:
        raise ValueError(f"relative paths require an explicit cwd baseline (only absolute paths are accepted when cwd=None): {path!r}")
    return Path(cwd) / p


class ReadTool(Tool):
    """``read`` —— 读 UTF-8 文本文件（只读）。

    .. rubric:: 功能介绍

    LLM 读取文本文件内容的工具，支持按行号窗口截取（``offset`` /
    ``limit``）。只读，不产生任何写操作。

    .. rubric:: 行为要点

    - 参数：``path`` （必填，遵循 ``cwd`` 基准口径——见模块 docstring）；
      ``cwd`` （路径基准，默认 ``None``）；``offset`` / ``limit`` （行号
      窗口，0 基；缺省从头读全文件）。
    - 返回：带行号前缀的文本，每行 ``<行号>\\t<内容>``，行号与
      ``offset`` 同一 0 基口径（传什么下标就见什么行号）；单行超过
      2000 字符时截断。
    - 边缘情况：路径是目录 / 文件不存在 / 非 UTF-8 编码 / 相对路径且
      无 ``cwd`` / ``cwd`` 非绝对 → ``status="error"`` 的 ``ToolResult``
      （工具失败是 LLM 可见的正常产物）。

    .. seealso:: :class:`flowing.builtins.GrepTool`、
        :class:`flowing.builtins.GlobTool` —— 同属只读工具族。
    """

    def __init__(self) -> None:
        """构造工具定义与内部校验模型（无参数，框架实例化时调用）。"""
        self.definition = ToolDefinition(
            name="read",
            description="Read a UTF-8 text file, with line-window support (offset/limit).",
            params_schema={
                "path": {"type": "string", "description": "File to read (absolute, or relative to cwd)."},
                "cwd": {"type": ["string", "null"], "default": None, "description": "Base directory for relative paths (absolute when given)."},
                "offset": {"type": "integer", "default": 0, "description": "0-based starting line of the window."},
                "limit": {"type": ["integer", "null"], "default": None,
                          "description": "Maximum number of lines to return (default: whole file)."},
            })
        self._has_caller = False
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, path: str, cwd: str | None = None,
                      offset: int = 0,
                      limit: int | None = None) -> str:
        """读文件并返回带行号文本（``<行号>\\t<内容>``，行窗由
        ``offset`` / ``limit`` 截取）。

        .. rubric:: 行为要点

        - 行号前缀与 ``offset`` 同一 0 基口径：传什么下标就见什么行号。
        - 参数 / IO 错误（路径是目录、文件不存在、非 UTF-8、相对路径无
          ``cwd`` 基准）抛 ``ValueError``——经 ``Tool.__call__`` 包装为
          ``status="error"`` 的 ``ToolResult`` （LLM 可见，可自纠正）。
        """
        p = _resolve_under_cwd(path, cwd)
        if p.is_dir():
            raise ValueError(f"path is a directory, not a text file: {p}")
        if not p.exists():
            raise ValueError(f"file does not exist: {p}")
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"not a UTF-8 text file (no encoding guessing): {p}") from exc
        lines = text.splitlines()
        window = lines[offset:] if limit is None else lines[offset:offset + limit]
        return "\n".join(
            f"{i}\t{line[:_MAX_LINE_LENGTH]}"
            for i, line in enumerate(window, start=offset))


class WriteTool(Tool):
    """``write`` —— 覆盖写 UTF-8 文本文件（可写工具）。

    .. rubric:: 功能介绍

    LLM 写入文件内容的工具：整文件覆盖（上级目录自动创建），不是追加。
    可写工具，危险面见行为要点。

    .. rubric:: 行为要点

    - 参数：``path`` / ``content`` （均必填；``path`` 遵循 ``cwd`` 基准
      口径——见模块 docstring）；``cwd`` （路径基准，默认 ``None``）。
    - 语义：整文件覆盖（上级目录自动创建），不是追加。
    - 危险面：任意路径覆盖写。审批 / 路径白名单属策略层
      （``before_tool_call`` 钩子），本工具不做。
    - 边缘情况：路径是目录 / 相对路径且无 ``cwd`` → ``status="error"``
      的 ``ToolResult``。
    """

    def __init__(self) -> None:
        """构造工具定义与内部校验模型（无参数，框架实例化时调用）。"""
        self.definition = ToolDefinition(
            name="write",
            description="Overwrite a UTF-8 text file (parent directories are created automatically).",
            params_schema={
                "path": {"type": "string",
                         "description": "File to overwrite (absolute, or relative to cwd); parent directories are created automatically."},
                "cwd": {"type": ["string", "null"], "default": None, "description": "Base directory for relative paths (absolute when given)."},
                "content": {"type": "string", "description": "Full text to write (the file is overwritten)."},
            })
        self._has_caller = False
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, path: str, content: str,
                      cwd: str | None = None) -> str:
        """覆盖写文件并返回路径收据文本（``已写入 <路径>（<字符数> 字符）``）。

        .. rubric:: 行为要点

        - 参数 / IO 错误（路径是目录、相对路径无 ``cwd`` 基准）抛
          ``ValueError``——经 ``Tool.__call__`` 包装为 ``status="error"``
          的 ``ToolResult`` （LLM 可见，可自纠正）。
        """
        p = _resolve_under_cwd(path, cwd)
        if p.is_dir():
            raise ValueError(f"path is a directory; cannot overwrite: {p}")
        p.parent.mkdir(parents=True, exist_ok=True)   # 上级目录自动创建
        p.write_text(content, encoding="utf-8")       # 整文件覆盖（非追加）
        return f"written {p} ({len(content)} chars)"


class BashTool(Tool):
    """``bash`` —— 经 ``/bin/bash`` 执行 shell 命令（高危可写工具）。

    .. rubric:: 功能介绍

    LLM 在宿主环境执行 shell 命令的工具：命令经 ``/bin/bash -c`` 运行，
    返回 ``stdout`` / ``stderr`` / ``exit_code`` 拼成的文本。高危工具——
    任意命令执行，继承 Agent 进程的全部权限。

    .. rubric:: 行为要点

    - 参数：``command`` （必填）；``timeout`` （秒，默认 120，超时杀进程
      组并返回 error）；``cwd`` （工作目录，可选；给则必须是绝对路径，
      相对 → error ``ToolResult``）。
    - 返回：``stdout`` / ``stderr`` / ``exit_code`` 拼成的裸文本；非零
      退出码不是异常——照常在 output 里返回（LLM 应看到）。
    - 危险面：任意命令执行，继承 Agent 进程全部权限。审批 / 命令过滤属
      策略层（``before_tool_call``），本工具不做。
    - 不支持交互式命令（stdin 关闭）；不保持会话状态（每次调用是新
      进程，工作目录经 ``cwd`` 参数显式给出）。

    .. seealso:: :class:`flowing.builtins.WriteTool`、
        :class:`flowing.builtins.EditTool` —— 同属可写工具族。
    """

    def __init__(self) -> None:
        """构造工具定义与内部校验模型（无参数，框架实例化时调用）。"""
        self.definition = ToolDefinition(
            name="bash",
            description="Run a shell command via /bin/bash and return stdout/stderr/exit_code.",
            params_schema={
                "command": {"type": "string", "description": "Shell command to run via /bin/bash."},
                "timeout": {"type": "integer", "default": 120, "description": "Timeout in seconds."},
                "cwd": {"type": ["string", "null"], "default": None, "description": "Base directory for relative paths (absolute when given)."},
            })
        self._has_caller = False
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, command: str, timeout: int = 120,
                      cwd: str | None = None) -> str:
        """执行命令并返回 ``stdout`` / ``stderr`` / ``exit_code`` 拼成的裸文本。

        .. rubric:: 行为要点

        - 超时杀整个进程组（子进程自立会话，含命令再拉起的孙进程），
          随后返回 ``status="error"`` 的 ``ToolResult``。
        - 取消中断（``Tool.__call__`` 竞速注入的 CancelledError）杀整个
          进程组并回收，返回已收集的部分输出 + 中断提示行（竞速层包装为
          ``status="cancelled"`` 的结果，LLM 可见中断前输出）；输出经
          增量泵流收集（``communicate`` 拿不到取消时的部分输出）。
        - ``cwd`` 非绝对 → ``ValueError``——经 ``Tool.__call__`` 包装为
          ``status="error"`` （LLM 可见，可自纠正）。
        """
        if cwd is not None and not Path(cwd).is_absolute():
            raise ValueError(f"cwd must be an absolute path: {cwd!r}")
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", command,
            stdin=asyncio.subprocess.DEVNULL,   # 不做交互式命令
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            start_new_session=True,   # 独立进程组：超时/取消杀整组
        )
        stdout_buf = bytearray()   # 增量收集（communicate 取消时拿不到部分
        stderr_buf = bytearray()   # 输出——自行泵流，中断时保留已读部分）

        async def _pump(stream: asyncio.StreamReader, buf: bytearray) -> None:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    break
                buf.extend(chunk)

        pumps = [asyncio.ensure_future(_pump(proc.stdout, stdout_buf)),
                 asyncio.ensure_future(_pump(proc.stderr, stderr_buf))]
        try:
            await asyncio.wait_for(proc.wait(), timeout)
        except TimeoutError:   # 3.11+ asyncio.TimeoutError 即内置 TimeoutError
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass   # 进程已自行退出
            await proc.wait()   # 回收僵尸
            await asyncio.gather(*pumps)   # 排空已产出
            raise ValueError(f"command timed out ({timeout}s); process group terminated") from None
        except asyncio.CancelledError:
            # 取消中断（__call__ 竞速注入）：杀进程组（不泄漏）、回收、排空，
            # 返回已收集的部分输出 + 中断提示（Ctrl-C 体验对齐）——竞速层
            # 包装为 status="cancelled" 的结果
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass   # 进程已自行退出
            await proc.wait()
            await asyncio.gather(*pumps)   # 杀后流 EOF，泵自然收尾
            out = stdout_buf.decode("utf-8", errors="replace")
            err = stderr_buf.decode("utf-8", errors="replace")
            hint = ("CancelledError: command interrupted by agent "
                    "cancellation; process group terminated")
            return (f"exit_code: <cancelled>\n"
                    f"--- stdout ---\n{out}--- stderr ---\n{err}{hint}\n")
        await asyncio.gather(*pumps)
        out = stdout_buf.decode("utf-8", errors="replace")
        err = stderr_buf.decode("utf-8", errors="replace")
        # 非零退出码不是异常——照常在 output 里返回（LLM 应看到）
        return (f"exit_code: {proc.returncode}\n"
                f"--- stdout ---\n{out}--- stderr ---\n{err}")


class EditTool(Tool):
    """``edit`` —— 精确字符串替换（可写工具）。

    .. rubric:: 功能介绍

    LLM 编辑文件内容的工具：把文件中与 ``old_string`` 精确相等的文本
    替换为 ``new_string``。默认要求唯一命中，避免误改。

    .. rubric:: 行为要点

    - 参数：``path`` / ``old_string`` / ``new_string`` （必填；``path``
      遵循 ``cwd`` 基准口径——见模块 docstring）；``cwd`` （路径基准，
      默认 ``None``）；``replace_all`` （默认 ``False``）。
    - 唯一性约束：``replace_all=False`` 时 ``old_string`` 命中次数
      不等于 1 → error（0 次命中即没找到，多次命中即歧义，均不改文件）。
    - 返回：收据文本（路径与替换次数）。
    - 危险面：任意路径改文件。审批属策略层（``before_tool_call``）。
    - 编辑不是原子操作：工具先读取文件、替换、再写回；同一文件并发
      编辑时后完成者覆盖先完成者（同一 Agent 串行执行，天然安全）。

    .. seealso:: :class:`flowing.builtins.WriteTool` —— 整文件覆盖写。
    """

    def __init__(self) -> None:
        """构造工具定义与内部校验模型（无参数，框架实例化时调用）。"""
        self.definition = ToolDefinition(
            name="edit",
            description="Edit a file with exact string replacement (unique match required by default).",
            params_schema={
                "path": {"type": "string", "description": "File to edit (absolute, or relative to cwd)."},
                "cwd": {"type": ["string", "null"], "default": None, "description": "Base directory for relative paths (absolute when given)."},
                "old_string": {"type": "string",
                               "description": "Text to replace; must match exactly once (unless replace_all)."},
                "new_string": {"type": "string", "description": "Replacement text."},
                "replace_all": {"type": "boolean", "default": False,
                                "description": "Replace every occurrence (default False = require a unique match)."},
            })
        self._has_caller = False
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, path: str, old_string: str, new_string: str,
                      replace_all: bool = False,
                      cwd: str | None = None) -> str:
        """替换并返回收据文本（``已编辑 <路径>：替换 <次数> 处``）。

        .. rubric:: 行为要点

        - 唯一性约束（见类 docstring）：0 次命中 / ``replace_all=False``
          时多次命中 → 抛 ``ValueError``，文件不变；经 ``Tool.__call__``
          包装为 ``status="error"`` 的 ``ToolResult`` （LLM 可见，可自
          纠正）。
        """
        p = _resolve_under_cwd(path, cwd)
        if p.is_dir():
            raise ValueError(f"path is a directory; cannot edit: {p}")
        if not p.exists():
            raise ValueError(f"file does not exist: {p}")
        text = p.read_text(encoding="utf-8")
        count = text.count(old_string)
        if count == 0:
            raise ValueError(f"string to replace not found (0 matches): {p}")
        if count > 1 and not replace_all:
            raise ValueError(
                f"old_string matched {count} places (ambiguous; file unchanged) —"
                "make old_string more specific or pass replace_all=True")
        replaced = text.replace(old_string, new_string) if replace_all \
            else text.replace(old_string, new_string, 1)
        p.write_text(replaced, encoding="utf-8")
        n = count if replace_all else 1
        return f"edited {p}: {n} replacement(s)"


class GrepTool(Tool):
    """``grep`` —— 内容搜索（只读），体内委托 ``rg``。

    .. rubric:: 功能介绍

    LLM 在目录中搜索文本的工具：按 ripgrep 正则匹配文件内容，返回带
    路径与行号的匹配行。要求宿主环境安装 ``rg`` （ripgrep）——未安装时
    工具报错，不做 Python 兜底扫描（行为一致性优先于可用性）。

    .. rubric:: 行为要点

    - 参数：``pattern`` （ripgrep 正则，必填）；``path`` （搜索根，必填，
      遵循 ``cwd`` 基准口径——见模块 docstring）；``cwd`` （路径基准，
      默认 ``None``）；``glob`` （文件名过滤，可选）。
    - 匹配语义沿用 ``rg`` 默认行为：尊重 ``.gitignore``、跳过隐藏文件。
    - 返回：匹配行文本，每行 ``<路径>:<行号>:<内容>``；路径一律以绝对
      形式呈现。无匹配返回「（无匹配）」；匹配行数超过
      250 行时截断并注明。
    - 边缘情况：``rg`` 未安装 / ``rg`` 执行失败（退出码非 0 或 1）→
      ``status="error"`` 的 ``ToolResult``；退出码 1（无匹配）不是错误。
    """

    def __init__(self) -> None:
        """构造工具定义与内部校验模型（无参数，框架实例化时调用）。"""
        self.definition = ToolDefinition(
            name="grep",
            description="Content search (ripgrep regex) with line numbers in the output.",
            params_schema={
                "pattern": {"type": "string", "description": "ripgrep regex to search for."},
                "path": {"type": "string",
                         "description": "File or directory to search (absolute, or relative to cwd)."},
                "cwd": {"type": ["string", "null"], "default": None, "description": "Base directory for relative paths (absolute when given)."},
                "glob": {"type": ["string", "null"], "default": None,
                         "description": "File filter passed to ripgrep (--glob), e.g. '*.ts'."},
            })
        self._has_caller = False
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, pattern: str, path: str,
                      cwd: str | None = None,
                      glob: str | None = None) -> str:
        """调 ``rg`` 并返回带行号匹配文本。

        .. rubric:: 行为要点

        - ``rg`` 未安装 / 执行失败（退出码非 0 或 1）→ 抛
          ``RuntimeError``——经 ``Tool.__call__`` 包装为
          ``status="error"`` 的 ``ToolResult`` （LLM 可见，可自纠正）；
          退出码 1（无匹配）不是错误，返回「（无匹配）」。
        - 匹配行数超过 250 行时截断并注明（见类 docstring）。
        """
        p = _resolve_under_cwd(path, cwd)
        rg = shutil.which("rg")
        if rg is None:
            raise RuntimeError(
                "rg (ripgrep) is not installed — this tool delegates to rg; "
                "install ripgrep first (no Python fallback scanning)")
        cmd = [rg, "--line-number", "--with-filename"]
        if glob is not None:
            cmd += ["--glob", glob]
        cmd += ["--", pattern, str(p)]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode not in (0, 1):   # 0=有匹配；1=无匹配（非错误）
            raise RuntimeError(
                f"rg failed (exit {proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace').strip()}")
        lines = stdout.decode("utf-8", errors="replace").splitlines()
        if not lines:
            return "(no matches)"
        if len(lines) > _GREP_MAX_LINES:
            lines = lines[:_GREP_MAX_LINES] + [
                f"…(truncated: {len(lines)} matching lines total, showing first {_GREP_MAX_LINES})"]
        return "\n".join(lines)


class GlobTool(Tool):
    """``glob`` —— 按模式找文件（只读）。

    .. rubric:: 功能介绍

    LLM 在目录中按 glob 模式枚举文件的工具：返回匹配文件的绝对路径
    列表，最近修改的在前。

    .. rubric:: 行为要点

    - 参数：``pattern`` （glob 模式，必填，``**`` 递归）；``path`` （基准
      目录，必填，遵循 ``cwd`` 基准口径——见模块 docstring）；``cwd``
      （路径基准，默认 ``None``）。
    - 返回：匹配文件的绝对路径文本（每行一条，按 mtime 倒序——最近
      修改在前）；只列文件不列目录。无匹配返回「（无匹配）」；结果数
      超过 100 个时截断并注明。
    - 只做文件系统枚举：不跟随 ``.gitignore`` （与 ``grep`` 的 ``rg``
      语义不同），也不做内容过滤（那是 ``grep`` 的职责）。
    - 边缘情况：基准目录不存在或不是目录 → ``status="error"`` 的
      ``ToolResult``。
    """

    def __init__(self) -> None:
        """构造工具定义与内部校验模型（无参数，框架实例化时调用）。"""
        self.definition = ToolDefinition(
            name="glob",
            description="Enumerate files by glob pattern (** recursive), newest mtime first.",
            params_schema={
                "pattern": {"type": "string", "description": "Glob pattern (** recursive) to enumerate files by."},
                "path": {"type": "string", "description": "Directory to search (absolute, or relative to cwd)."},
                "cwd": {"type": ["string", "null"], "default": None, "description": "Base directory for relative paths (absolute when given)."},
            })
        self._has_caller = False
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, pattern: str, path: str,
                      cwd: str | None = None) -> str:
        """枚举并返回绝对路径文本（每行一条，mtime 倒序）。

        .. rubric:: 行为要点

        - 基准目录不存在或不是目录 → 抛 ``ValueError``——经
          ``Tool.__call__`` 包装为 ``status="error"`` 的 ``ToolResult``
          （LLM 可见，可自纠正）。
        - 结果数超过 100 个时截断并注明；无匹配返回「（无匹配）」。
        """
        p = _resolve_under_cwd(path, cwd)
        if not p.is_dir():
            raise ValueError(f"base directory does not exist or is not a directory: {p}")
        matches = [f for f in p.glob(pattern) if f.is_file()]   # 只列文件不列目录
        matches.sort(key=lambda f: f.stat().st_mtime, reverse=True)   # mtime 倒序
        truncated = len(matches) > _GLOB_MAX_RESULTS
        lines = [str(f) for f in matches[:_GLOB_MAX_RESULTS]]
        if truncated:
            lines.append(
                f"…(truncated: {len(matches)} matches total, showing first {_GLOB_MAX_RESULTS})")
        return "\n".join(lines) if lines else "(no matches)"


class FinishTool(Tool):
    """``finish`` —— 子 Agent 结构化交卷工具（普通工具，显式绑定）。

    .. rubric:: 功能介绍

    LLM 主动调用 ``finish`` 以结束子 Agent 当前逻辑 Turn 并返回结构化
    结果。工具级 ``definition`` 只有骨架参数 ``summary``；真正的结构化
    返回字段来自 Agent ``tools:`` 条目中的 ``output:`` 声明——该声明
    展开为 ``finish`` 的额外参数（见使用示例）。

    不调用 ``finish`` 是子 Agent 的最常用法：回合自然结束时，其 plain
    文本回复（``last_result``）即作为结果回传亲代 Agent——``finish`` 只是
    给想结构化提前交卷的子 Agent 一个可选出口，不是必备能力。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # reviewer-agent.fya —— output 声明展开为 finish 的额外参数
        tools:
          - finish:
              output:
                score:
                  type: integer
                  minimum: 0
                  maximum: 100
                  description: 代码质量评分（0-100）
                pass:
                  type: boolean
                  description: 是否通过审查
                issues:
                  type: array
                  description: 发现的问题列表

    .. rubric:: 行为要点

    - 动态 schema 机制：``output`` 走 :class:`flowing.tool.ToolEntry` 的
      ``override_params``——框架核心不感知 ``output`` 的含义，它只是
      普通覆写字段；动态 schema 不修改 :class:`flowing.tool.ToolRegistry`
      原始定义（``clone_with_overrides`` 语义保证）。
    - ``output`` 字段与 ``finish`` 自身参数合并（``summary`` 与
      ``output`` 字段），对 ``llm_definition()`` / ``resolve()`` 透明；
      ``output`` 本身不是 LLM 可见参数。
    - 无 ``output`` 声明 → 只有 ``summary``，LLM 以自由文本结束。
    - ``output:`` 与 ``args:`` 在 ``ToolEntry`` 内合并进同一
      ``override_params``；``output`` 覆写是任意工具的通用字段（与
      ``args:`` 平级）——省略字段即移除，改变类型即覆写，无需完整
      重声明。
    - 可见性走通用通道：``finish`` 对 LLM 可见只经 ``.fya`` ``tools:``
      声明或显式 ``add_tool("finish")``。
    - 结束机制：调用后，本回合在其余并行工具调用照常执行完后自然结束，
      返回载荷写入 ``Agent.last_result`` 并作为结果回传亲代 Agent；配对
      TOOL 消息正常挂树，本回合的回合结束标记（``turn_end=True``）落在
      该消息上。

    .. seealso::

        - :class:`flowing.tool.ToolEntry` —— 覆写数据载体。
        - :meth:`flowing.agent.Agent.add_tool` —— 显式绑定入口。
    """

    def __init__(self) -> None:
        """构造骨架定义并编译内部校验模型（无参数，框架实例化时调用）。"""
        self.definition = ToolDefinition(
            name="finish",
            description="End the current task and return a structured result. After the call, the "
                        "subagent's current logical execution phase ends.",
            params_schema={"summary": {"type": "string", "default": "",
                                        "description": "Summary of the completed task (used as the final result text)."}})
        self._has_caller = True   # execute 声明 caller: Agent
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

    async def execute(self, *, summary: str = "", caller: Agent, **kwargs: Any) -> dict[str, Any]:
        """收集结构化返回字段并结束子 Agent 当前逻辑 Turn。

        .. rubric:: 行为要点

        - ``kwargs`` 接收 ``output:`` 声明展开的动态字段；返回
          ``{"summary": ..., **动态字段}``——无 ``output`` 声明时
          ``summary`` 的值即自由文本字符串，声明后值换为结构化字段集，
          返回结构本身不变。
        - 结束机制：同一载荷置位 ``caller.current_turn.finish_output``
          ——置位即请求本回合自然结束（视同 ``finish=True``，工具段照常
          执行完），载荷由子 Agent 收尾段写入 ``Agent.last_result`` 并
          作为结果回传亲代 Agent。
        - 前置：``caller.current_turn`` 非 ``None`` （工具只在回合内执行）。

        .. seealso:: :class:`flowing.agent.TurnContext` —— 逻辑 Turn
            执行期对象。
        """
        payload = {"summary": summary, **kwargs}
        # 置位即请求本回合自然结束：turn loop 工具段照常执行完，随后视同
        # finish=True 走子 Agent 自己的统一收尾段（写 last_result=本载荷
        # → TurnContext 结束 → resolve waiters）；配对 TOOL 消息正常挂树，
        # 本回合 turn_end=True 落在其上（_run_turn 置位转移检测）
        caller.current_turn.finish_output = payload
        return payload



class SubagentInvokeTool(Tool):
    """``subagent-invoke`` —— 子智能体唤起工具（核心内置）。

    .. rubric:: 功能介绍

    LLM 唤起子 Agent 的唯一工具入口：不需要为每个子 Agent 类型生成独立
    ``ToolDefinition``——工具只有这一个，各子 Agent 类型的参数描述经
    ``<available_subagents>`` XML catalog 注入 system prompt。骨架参数
    五个：``prompt`` （任务提示）**必填**——无任务的唤起只会产出一条
    空消息，没有意义；其余可选：``name`` （新建时命名，之后可续接）、
    ``agent_type`` （子 Agent 类型，与 ``resume`` 二选一）、
    ``resume`` （已存在实例名续接，与 ``agent_type`` 互斥）、
    ``asynchronized`` （异步模式开关）；各类型声明的其余 args 经 catalog
    让 LLM 感知、经 ``SubagentEntry.resolve()`` 聚合。

    本工具随 Runtime 构造期注册，永远在注册表在场；但可见性不自动——
    需用户显式声明（``.fya`` ``tools:`` 或 ``add_tool``）才进
    ``Context.tools`` （一切工具以用户声明为准，框架不隐式附加）。

    .. rubric:: 使用示例

    .. code-block:: text

        # 新建 + 命名
        subagent-invoke(name="my-reviewer", agent_type="coder", prompt="审查 auth 模块")
        # 之后续接同一实例
        subagent-invoke(resume="my-reviewer", prompt="继续审查 payment 模块")
        # 不等待：立即回收据，答卷后续以 SUBAGENT 消息到达
        subagent-invoke(agent_type="coder", prompt="后台跑全量测试", asynchronized=true)

    .. rubric:: 行为要点

    - 同步路径（``asynchronized=False``，默认）：调用公开同步 API
      ``invoke_subagent`` （该 API 不 enqueue），把 ``SubagentResult`` 的
      全部字段平铺进本工具的返回 dict（``status`` / ``name_alias`` /
      ``subagent_id`` / ``result`` / ``subagent_status``），不再另发
      SUBAGENT 消息，避免同源结果二次入队。
    - 异步路径（``asynchronized=True``）：不等待子 Agent 完成——创建段
      同步 await（失败照常产 ``status="error"`` 结果，LLM 可见），随后
      立即返回 ``{"invoked": ..., "status": "started"}`` 收据（pending
      状态）；运行段在后台执行，子 Agent 真实产出以
      ``Message(kind=SUBAGENT)`` 在完成时推入亲代队列，LLM 在后续回合
      感知。
    - 校验：``agent_type`` 与 ``resume`` 互斥且至少其一；违反 →
      ``status="error"`` 结果（LLM 可见的自我修正反馈）。
    - 本工具不直接创建 / 销毁 Agent（那是 ``invoke_subagent`` 管线的
      职责）；不感知 catalog 渲染。
    - 可见性：本工具由 Runtime 核心注册，但不自动出现在任何 Agent 的
      ``Context.tools``——需用户显式声明（``.fya`` ``tools:`` 或
      ``add_tool("subagent-invoke")``）。

    .. seealso::

        - :meth:`flowing.agent.Agent.invoke_subagent` —— 真正的唤起管线。
        - :class:`flowing.subagents.SubagentEntry` —— catalog 与参数聚合。
        - :class:`flowing.builtins.FinishTool` —— 子 Agent 侧的可选交卷工具。
    """

    def __init__(self) -> None:
        """构造骨架定义并编译内部校验模型（无参数，框架实例化时调用）。"""
        self.definition = ToolDefinition(
            name="subagent-invoke",
            description="Invoke a subagent: create one (agent_type + optional name) or resume one "
                        "(resume instance name). By default it waits for the subagent to finish and "
                        "returns its result directly. With asynchronized=True it returns a receipt "
                        "immediately, and the subagent's output arrives later as a message.",
            params_schema={
                "name": {"type": "string", "default": "",
                         "description": "Instance name to create for a new subagent (optional)."},
                "agent_type": {"type": "string", "default": "",
                               "description": "Subagent type to create (mutually exclusive with resume)."},
                "prompt": {"type": "string",
                           "description": "Task prompt for the subagent (required)."},
                "resume": {"type": "string", "default": "",
                           "description": "Name of an existing instance to resume (mutually exclusive with agent_type)."},
                "asynchronized": {"type": "boolean", "default": False,
                                  "description": "When true, return a receipt immediately and run the subagent in the background."},
            })
        self._has_caller = True   # execute 声明 caller: Agent
        self._execution = None
        self._args_model = schema_to_model(type(self).__name__ + "Args", self.definition.params_schema)

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
        """转发 ``caller.invoke_subagent()`` 并返回确认收据。

        .. rubric:: 行为要点

        - ``agent_type`` 与 ``resume`` 互斥且至少其一（空串视为未给）；
          违反时抛 ``ValueError``——经 ``Tool.__call__`` 包装为
          ``status="error"`` （LLM 可见，可自纠正）。
        - ``prompt`` 必填：schema 无默认值（LLM 视角必填校验，缺失时
          由 ``Agent.tool_call`` 包装为 LLM 可见 error）；空串在 execute
          守卫处抛 ``ValueError``（同通道）。
        - ``kwargs`` 为该子 Agent 类型声明的其余 args（catalog 中 LLM
          可见），原样透传给 ``invoke_subagent`` 经 ``SubagentEntry.
          resolve()`` 聚合。
        - ``asynchronized=True``：不等待子 Agent 完成——创建段同步 await
          （失败即上抛，经 ``Tool.__call__`` 的 except 顺序分派为
          ``status="error"`` / ``blocked``，LLM 可见），随后立即返回
          ``{"invoked": ..., "status": "started"}`` 收据；运行段在后台
          执行，结局以 ``Message(kind=SUBAGENT)`` 推入亲代队列。
        - 缺省 ``False``：同步等待子 Agent 完成——``invoke_subagent``
          同步交付结果、不 enqueue SUBAGENT 消息；随后把返回的
          ``SubagentResult`` 全字段平铺进本工具返回 dict，不再另发
          SUBAGENT 消息。
        """
        if bool(agent_type) == bool(resume):  # 互斥且至少其一
            raise ValueError("agent_type and resume are mutually exclusive; provide exactly one")
        if not prompt:   # 必填守卫：无任务的唤起只会给子 Agent 塞一条空消息
            raise ValueError("prompt is required and must be non-empty: "
                             "the task the subagent should work on")
        if asynchronized:
            # 嵌套形态：execute 保持 async def（同步路径须带值 return，
            # async generator 内禁止），asynchronized 分支返回本对象的
            # async gen；Tool.__call__ 识别后走与 execute 自身即 async gen
            # 同一条后台管线（首 yield 收据 → 注册表 → 驱动）
            return self._run_async(
                caller=caller, name=name, agent_type=agent_type,
                prompt=prompt, resume=resume, **kwargs)
        result = await caller.invoke_subagent(
            agent_type, prompt=prompt, name=name or None,
            resume=resume or None, **kwargs)
        # 同步路径：把 SubagentResult 全字段平铺进工具返回值；
        # 运行段已跳过 SUBAGENT 入队，避免同源结果二次入队。
        return {
            "status": "completed",
            "name_alias": result.name_alias,
            "subagent_id": result.subagent_id,
            "result": result.result,
            "subagent_status": result.subagent_status,
        }

    async def _run_async(
        self,
        *,
        caller: Agent,
        name: str,
        agent_type: str,
        prompt: str,
        resume: str,
        **kwargs: Any,
    ) -> None:
        """``asynchronized=True`` 的运行段 async generator（内部 API）。

        - 首 yield 前同步 await ``caller._prepare_subagent(...)``——失败
          （``Intercepted`` / 校验 / 创建抛错）经 ``Tool.__call__`` 的
          except 顺序分派（``Intercepted`` → ``blocked``；其余 →
          ``status="error"``，LLM 可见）；成功则收据字面成立（子 Agent
          已创建）。
        - 首 yield 收据 ``{"invoked": name or resume, "status": "started"}``
          （pending，带 ``background_task_id`` 注册键）。
        - 运行段 ``await caller._run_subagent(..., enqueue_result=True)``
          后台驱动——完成 / 失败投递由它内部经 SUBAGENT 消息完成，故无
          需末 yield；取消时 ``CancelledError`` 注入运行段 await 点，
          驱动方投递「已取消」后裸 raise。
        """
        child, invocation, execution = await caller._prepare_subagent(
            agent_type, prompt=prompt or None, name=name or None,
            resume=resume or None, kwargs=kwargs)
        yield {"invoked": name or resume, "status": "started"}   # 收据（pending）
        await caller._run_subagent(
            child, invocation, execution, enqueue_result=True)   # 运行段（后台驱动）


# TOOL_NAMING（Tool 资源的路径形态身份名推断规则表）的单一权威定义在
# flowing.tool（Registry 同文件、消费方最近）；本模块经头部
# import 再导出（见 __all__），不复制第二份常量（防双份漂移）。
