"""``flowing.tool.cli`` —— ``CliTool``：Jinja2 命令模板驱动的命令行工具。

零代码工具形态之一（``.fya`` 声明 ``type: cli``）。公开符号经
``flowing.tool`` re-export。
"""

from __future__ import annotations   # 注解延迟求值，配合 TYPE_CHECKING 破注解级循环边

import asyncio
import logging
import shlex

from typing import Any, Literal

import jinja2

from flowing.errors import FormatError, MissingSchemaError
from flowing.params import schema_to_model

from flowing.tool.core import Tool, ToolDefinition, _strip_unsupported_background

_logger = logging.getLogger("flowing.tool")

class _ShellRaw(str):
    """``| raw`` 旁路标记类型（finalize 识别后不再转义）。内部 API。"""


def _shell_raw_filter(value: Any) -> _ShellRaw:
    """``{{ arg | raw }}`` 旁路自动转义；每次渲染命中即告警。内部 API。"""
    _logger.warning("CliTool command template uses the | raw bypass of auto-escaping — "
                    "ensure raw concatenation values are trusted (command-injection risk is on you)")
    return _ShellRaw(str(value))


def _shell_finalize(value: Any) -> str:
    """CliTool 命令模板的 finalize：插入值一律 ``shlex.quote`` 转义；
    ``_ShellRaw`` 原样放行。内部 API。"""
    if isinstance(value, _ShellRaw):
        return str(value)
    return shlex.quote(str(value))


_CLI_JINJA = jinja2.Environment(
    autoescape=False, finalize=_shell_finalize,
    undefined=jinja2.StrictUndefined)   # 模板变量缺失 = 声明笔误，渲染期暴露
_CLI_JINJA.filters["raw"] = _shell_raw_filter

_SHELL_EXECUTABLES: dict[str, "str | None"] = {
    "sh": None,          # create_subprocess_shell 默认 /bin/sh
    "bash": "bash",
    "ps": "pwsh",
    "powershell": "powershell",
    "cmd": "cmd",
}
"""CliTool ``shell`` 声明 → 子进程 executable 映射（``None`` = 默认 sh）。

``ps`` 取 PowerShell 7 的 ``pwsh``。可执行文件不存在 → 子进程启动失败，
由 ``__call__`` 包装为 ``status="error"`` 结果。内部 API。
"""



class CliTool(Tool):
    
    """CLI 工具实例——Jinja2 命令模板 + shell 执行。

    .. rubric:: 功能介绍

    ``type: cli`` 的实例类。``args`` （参数 schema）必填，无自动推断来源；
    命令体 ``command`` 为 Jinja2 模板，渲染上下文为 LLM 传入的 args。
    把“跑个命令”类能力零代码化；安全默认值：框架自动转义模板插入值，
    原始拼接必须显式 ``{{ arg | raw }}`` 且框架输出警告。

    .. rubric:: 使用示例

    .. code-block:: yaml

        # run-tests/TOOL.fya
        name: run-tests
        type: cli
        description: 运行项目测试套件。在需要验证代码正确性时调用。
        shell: sh
        command: |
          cd {{ working_dir }} && python -m pytest {{ test_path }} --json-report
        args:
          working_dir:                    # 完整写法（无 default → 必填）
            type: string
            description: 项目根目录的绝对路径
          test_path:                      # 完整写法 + default（可选）
            type: string
            default: src/
            description: 测试路径
        output:
          type: object
          properties:
            exit_code:
              type: integer
            stdout:
              type: string
            stderr:
              type: string

    .. rubric:: 行为要点

    - ``shell`` 可选值：``sh`` （默认）/ ``bash`` / ``ps`` / ``powershell`` /
      ``cmd``；非法值在构造期抛 :class:`flowing.errors.FormatError`
      （声明期尽早报错，不留到执行期）。
    - 返回值恒为 ``{exit_code, stdout, stderr}`` 三字段字典（``output:``
      声明进入 `ToolDefinition.output_schema`，但不做字段提取）。
    - 非零退出码不等于 ``status="error"``——exit_code 是正常输出数据；
      仅进程无法启动等框架级失败才产生 ``error``。

    :raises flowing.errors.MissingSchemaError: 未声明 ``args`` 时（解析
      ``.fya`` 阶段）。

    .. seealso::

        - :class:`flowing.tool.RequestTool` —— 另一类零代码工具。
    """


    def __init__(
        self,
        *,
        definition: ToolDefinition,
        command: str,
        shell: Literal["sh", "bash", "ps", "powershell", "cmd"] = "sh",
    ) -> None:
        """构造 CLI 工具实例。

        :param definition: 工具声明；``params`` 必填（来自 ``.fya``
          ``args:``）。
        :param command: Jinja2 命令模板；插入值自动转义，``| raw`` 旁路并
          告警。
        :param shell: 执行 shell，默认 ``sh``；非法值构造期抛
          :class:`flowing.errors.FormatError` （fail fast，不留到执行期）。
        """
        if not definition.params_schema:
            raise MissingSchemaError("cli tools must declare args (no automatic inference source)")
        if shell not in _SHELL_EXECUTABLES:
            # 加载期 fail-fast（声明笔误），不留到执行期 KeyError
            raise FormatError(
                f"invalid shell declaration: {shell!r} (allowed values: "
                f"{', '.join(sorted(_SHELL_EXECUTABLES))})")
        self.definition = definition
        self.command = command
        self.shell = shell
        self._execution = None
        # 创建时编译一次模板（不在每次调用时编译）；
        # 模板语法错误（声明笔误）在构造期暴露
        self._template = _CLI_JINJA.from_string(command)
        _strip_unsupported_background(self)   # background 仅 script 型受支持：告警 + 强制 False
        # 创建时定内部校验模型（fya 声明经 params.schema_to_model
        # 桥接——definition.params_schema 即桥接产物 schema，再建最终模型）
        self._args_model = schema_to_model("Args", self.definition.params_schema)

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """渲染命令模板 → shell 执行 → ``{exit_code, stdout, stderr}``。

        .. rubric:: 行为要点

        - 插入值自动经 ``shlex.quote`` 转义（finalize 单点）；显式
          ``{{ arg | raw }}`` 旁路并输出告警日志（每次渲染命中）。
        - 非零退出码不是 error——exit_code 是正常输出数据；仅子进程无法
          启动等框架级失败抛异常（由 ``__call__`` 包装为
          ``status="error"`` 结果）。
        - stdout / stderr 按 UTF-8 解码（``errors="replace"`` 容错）。
        """
        command = self._template.render(**kwargs)   # 渲染上下文 = LLM args
        executable = _SHELL_EXECUTABLES[self.shell]
        popen_kwargs: dict[str, Any] = {}
        if executable is not None:
            popen_kwargs["executable"] = executable
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, **popen_kwargs)
        stdout, stderr = await proc.communicate()
        return {
            "exit_code": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }


