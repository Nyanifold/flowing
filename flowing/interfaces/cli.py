"""``flowing.interfaces.cli`` —— CLI 进程入口（``main``）与 ``compile`` 子命令。

本模块只做两件事：解析 argv 并分发到各子命令（参数分层 / 退出码 /
信号处理的总约定见 ``flowing.interfaces`` 包 docstring），以及
``.fya`` 显式编译壳（:func:`cmd_compile`）。
"""

import sys
from pathlib import Path

from flowing.interfaces import (
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    EXIT_USAGE_ERROR,
    SUBCOMMANDS,
    parse_kv_args,
)
from flowing.interfaces.repl import cmd_repl
from flowing.interfaces.repl_debug import cmd_repl_debug
from flowing.interfaces.run import cmd_run, cmd_test
from flowing.interfaces.serve import cmd_serve
from flowing.interfaces.web import cmd_web


_USAGES: dict[str, str] = {
    "run": "flowing run <path> [-m <main file>] [--key value ...]",
    "repl": "flowing repl <path> [-m <main file>] [--key value ...] (alias: cli)",
    "repl-debug": "flowing repl-debug <path> [-m <main file>] [--key value ...]",
    "serve": "flowing serve <path> [-m <main file>] [-a <host>] [-p <port>] [--key value ...]",
    "web": "flowing web <path> [-m <main file>] [-a <host>] [-p <port>] [--key value ...]",
    "test": "flowing test <path> [-m <main file>] [--key value ...]",
    "compile": "flowing compile <path>",
}
"""各子命令的一行用法。``-h`` / ``--help`` 打印到 stdout；用法错误时
随错误提示打印到 stderr。``cli`` 归一化为 ``repl`` 后查本表。"""


def _print_general_usage(file) -> None:
    """总用法输出：子命令封闭集清单（空 argv / 未知子命令时的 stderr 输出）。"""
    print("usage: flowing <subcommand> [<path>] [args ...]", file=file)
    print("subcommands: " + ", ".join(SUBCOMMANDS) + " (cli is an alias for repl)", file=file)


def _usage_error(message: str, subcommand: str) -> int:
    """用法错误的统一出口：向 stderr 打印错误提示与该子命令用法，
    返回 :data:`EXIT_USAGE_ERROR`。"""
    print(f"usage error: {message}", file=sys.stderr)
    print(f"usage: {_USAGES[subcommand]}", file=sys.stderr)
    return EXIT_USAGE_ERROR


def _parse_flowing_args(subcommand: str, rest: list[str]):
    """剥离 flowing 级单横线参数并校验 ``--key value`` 形态（内部 API，
    不属稳定契约）。

    返回 ``(main_file, host, port, help_requested, kwargs)`` 五元组；
    任何解析失败（``-m`` / ``-a`` / ``-p`` 缺值、``-p`` 非数字、未知
    单横线参数、``--key=value`` 等号形式、``--`` 单独出现、裸位置
    参数）向 stderr 打印用法并返回 :data:`EXIT_USAGE_ERROR` （int 与
    五元组以返回值类型区分）。``--help`` 是 CLI 保留字，置位
    ``help_requested`` 而不进入 kwargs。
    """
    main_file: str | None = None   # -m <file>
    host: str = "127.0.0.1"        # -a <host>（serve/web 专属）
    port: int = 8000               # -p <port>（serve/web 专属）
    help_requested: bool = False   # -h / --help（--help 为保留字，不透传给 main）
    kv_argv: list[str] = []
    i: int = 0
    while i < len(rest):
        token: str = rest[i]
        if token in ("-h", "--help"):
            help_requested = True
            i += 1
        elif token in ("-m", "-a", "-p"):
            if i + 1 >= len(rest) or rest[i + 1].startswith("-"):
                return _usage_error(f"{token} is missing a value", subcommand)
            value: str = rest[i + 1]
            if token == "-m":
                main_file = value
            elif token == "-a":
                host = value
            else:
                try:
                    port = int(value)
                except ValueError:
                    return _usage_error(f"-p port must be a number, got {value!r}", subcommand)
            i += 2
        elif token.startswith("-") and not token.startswith("--"):
            # 未知单横线参数：flowing 级参数是封闭集，main 参数一律双横线
            return _usage_error(f"unknown argument {token!r} (main args must use the --key value form)", subcommand)
        else:
            kv_argv.append(token)
            i += 1
    # --key value 形态校验（parse_kv_args 只做机械收集，形态错误在 main 层报）：
    # 等号形式 / -- 单独出现 / 裸位置参数一律按用法错误处理
    j: int = 0
    while j < len(kv_argv):
        token = kv_argv[j]
        if not token.startswith("--"):
            return _usage_error(f"bare argument {token!r} (only the --key value form is supported)", subcommand)
        if token == "--" or "=" in token:
            return _usage_error(f"unsupported argument form {token!r} (only --key value separated by space)", subcommand)
        if j + 1 < len(kv_argv) and not kv_argv[j + 1].startswith("--"):
            j += 2   # 键值对（值以字符串原样传递，不做类型推断）
        else:
            j += 1   # 布尔 flag
    kwargs: dict[str, str | bool] = parse_kv_args(kv_argv)
    return (main_file, host, port, help_requested, kwargs)


def main(argv: list[str] | None = None) -> int:
    """CLI 进程入口：解析子命令并分发，返回进程退出码。

    ``flowing`` 可执行脚本的入口函数。解析 ``argv`` 的第一个位置参数
    为子命令名（``cli`` 归一化为 ``repl``），第二个位置参数为子项目
    路径 ``<path>`` （缺省 ``.``）；剥离 flowing 级单横线参数
    （``-h`` / ``--help`` / ``-m`` / ``-a`` / ``-p``，归属见包
    docstring「参数分层」），其余 ``--key value`` 参数经
    :func:`parse_kv_args` 收集，然后分发到对应的 ``cmd_*`` 协程并以
    ``asyncio.run`` 驱动（``compile`` 除外，它是同步函数，直接调用）。

    .. rubric:: 使用示例

    .. code-block:: console

        $ flowing run . --workspace_root /ws --debug
        $ flowing repl /path/to/project          # 等价 flowing cli ...
        $ flowing serve . -p 9000
        $ flowing web -a 0.0.0.0 -p 8080
        $ flowing test . -m ./tests/main_test.py --key val
        $ flowing compile .

    .. rubric:: 行为要点

    - ``argv is None`` 时取 ``sys.argv[1:]``。
    - 空 ``argv``、未知子命令 → 向 stderr 打印总
      用法（含子命令封闭集清单），返回 :data:`EXIT_USAGE_ERROR`。
      子命令名比较是精确小写匹配，不做前缀或模糊匹配。
    - ``<path>`` 指向不存在的目录 → 返回
      :data:`EXIT_USAGE_ERROR` （路径存在性在 CLI 层检查，先于
      ``launch``）。
    - ``-h`` / ``--help`` → 向 stdout 打印该子命令用法，返回
      :data:`EXIT_OK`，不执行任何分发；``--help`` 为 CLI 保留字，
      不透传给 ``main``。
    - flowing 级参数（``-m`` / ``-a`` / ``-p``）按子命令归属作为显式
      参数传给对应的 ``cmd_*`` （``-a`` / ``-p`` 只传给 ``serve`` /
      ``web``），不进入 ``main`` 的 kwargs；``--key value`` 收集结果
      经 ``cmd_*`` 的 ``**kwargs`` 原样透传给 ``launch`` 与子项目
      ``main``。
    - 子命令协程返回的退出码原样作为本函数的返回值。
    - ``launch`` 失败由各 ``cmd_*`` 内部处理（向 stderr 打印异常
      摘要并返回 :data:`EXIT_RUNTIME_ERROR`），本函数不捕获重试。

    :param argv: 命令行参数列表（不含程序名）；``None`` 时取
        ``sys.argv[1:]``。
    :return: 进程退出码（:data:`EXIT_OK` / :data:`EXIT_RUNTIME_ERROR` /
        :data:`EXIT_USAGE_ERROR` 之一）。

    .. seealso::

        :func:`flowing.interfaces.parse_kv_args` —— ``--key value``
            参数的收集规则。
        :func:`flowing.interfaces.run.cmd_run`、
        :func:`flowing.interfaces.repl.cmd_repl`、
        :func:`flowing.interfaces.repl_debug.cmd_repl_debug`、
        :func:`flowing.interfaces.serve.cmd_serve`、
        :func:`flowing.interfaces.web.cmd_web`、
        :func:`flowing.interfaces.run.cmd_test`、:func:`cmd_compile`
        —— 各子命令的行为契约。
    """
    import asyncio

    if argv is None:
        argv = sys.argv[1:]
    argv_list: list[str] = list(argv)
    if not argv_list:
        # 空 argv：打印总用法（含子命令封闭集清单）到 stderr
        _print_general_usage(sys.stderr)
        return EXIT_USAGE_ERROR
    subcommand: str = argv_list[0]
    if subcommand not in SUBCOMMANDS:
        # 未知子命令（精确小写匹配，无前缀/模糊匹配）：打印总用法到 stderr
        print(f"usage error: unknown subcommand {subcommand!r}", file=sys.stderr)
        _print_general_usage(sys.stderr)
        return EXIT_USAGE_ERROR
    if subcommand == "cli":
        subcommand = "repl"   # cli 归一化为 repl，同一代码路径
    if len(argv_list) > 1 and not argv_list[1].startswith("-"):
        path: str = argv_list[1]
        rest: list[str] = argv_list[2:]
    else:
        path = "."  # <path> 缺省
        rest = argv_list[1:]
    if not Path(path).is_dir():
        # <path> 不存在或不是目录：CLI 层先于 launch 拒绝
        return _usage_error(f"path {path!r} does not exist or is not a directory", subcommand)
    parsed = _parse_flowing_args(subcommand, rest)
    if isinstance(parsed, int):
        return parsed   # 单横线参数 / --key 形态错误（stderr 已打印用法）
    main_file, host, port, help_requested, kwargs = parsed
    if help_requested:
        # -h / --help：打印该子命令用法到 stdout（--help 为保留字，不透传给 main）
        print(f"usage: {_USAGES[subcommand]}")
        return EXIT_OK
    if subcommand == "run":
        return asyncio.run(cmd_run(path, main_file=main_file, **kwargs))
    if subcommand == "repl":
        return asyncio.run(cmd_repl(path, main_file=main_file, **kwargs))
    if subcommand == "repl-debug":
        return asyncio.run(cmd_repl_debug(path, main_file=main_file, **kwargs))
    if subcommand == "serve":
        return asyncio.run(cmd_serve(path, main_file=main_file, host=host, port=port, **kwargs))
    if subcommand == "web":
        return asyncio.run(cmd_web(path, main_file=main_file, host=host, port=port, **kwargs))
    if subcommand == "test":
        return asyncio.run(cmd_test(path, main_file=main_file, **kwargs))
    # compile：同步直调，不进入 asyncio 事件循环
    return cmd_compile(path)


def cmd_compile(path: str) -> int:
    """``flowing compile <path>``：把 ``.fya`` 显式编译为同目录 ``.py``。

    递归扫描 ``<path>`` 下全部 ``*.fya`` （工具与技能定义
    ——``TOOL.fya`` / ``*.tool.fya`` / ``*.skill.fya``——静默跳过，
    只编译 Agent 定义），逐文件编译为同目录同名
    ``.py`` （去 ``.fya`` 后缀，兼容 ``from payment import
    PaymentAgent`` 这类导入）。hash 元信息写在各产物同目录的
    ``.flowing.meta.yaml`` （三字段：源解析结构 hash / 产物 AST hash /
    编译器版本）。本命令不拉起 Runtime、不执行子项目 ``main``，是
    唯一不经 :func:`flowing.runtime.launch` 的子命令，因此是同步函数。

    .. rubric:: 使用示例

    .. code-block:: console

        $ flowing compile .
        # agents/payment/agent.fya -> agents/payment/agent.py
        # tools/submit.fya         -> tools/submit.py
        # meta 写入各产物同目录的 .flowing.meta.yaml（条目以 .fya 文件名为键）

    .. rubric:: 行为要点

    - 产物中的 Parsable 值以类体赋值形式写出（如
      ``system_prompt = Parsable('$./system-prompt.md')``）。
    - 重新编译条件：源 ``.fya`` 的解析结构 hash 变化，或
      ``compiler_version`` 与 meta 中记录的不同。
    - ``.py`` 产物被外部修改（AST hash 不匹配）→ 报错中止，不静默
      覆盖：向 stderr 打印冲突文件路径，返回
      :data:`EXIT_RUNTIME_ERROR`；本次已产出的其他文件不回滚（编译
      是幂等的，重跑可继续）。
    - 不删除无对应 ``.fya`` 的孤儿 ``.py`` （编译只覆盖 ``.fya``
      一侧，不清理产物目录）。
    - 项目内无 ``*.fya`` → 打印「无可编译文件」，返回
      :data:`EXIT_OK`。
    - 无变更时重复执行等价 no-op（全量命中 hash，产物不变）。

    :param path: 子项目目录（普通文件系统路径）。
    :return: :data:`EXIT_OK` （成功或无可编译文件）或
        :data:`EXIT_RUNTIME_ERROR` （产物被外部修改）。

    .. seealso::

        :func:`flowing.compiler.compile_project` —— 编译实现。
        :func:`main` —— 分发规则（``compile`` 不经事件循环，同步
        直调）。
    """
    from flowing.compiler import compile_project
    from flowing.errors import ArtifactModifiedError

    # 编译器入口（flowing.compiler.compile_project）；无 .fya 时
    # compile_project 返回空列表——打印「无可编译文件」并正常退出
    try:
        products = compile_project(Path(path))
    except ArtifactModifiedError as exc:
        # hash 冲突：指出冲突文件（异常消息含路径）到 stderr，报错中止；
        # 已产出的其他文件不回滚（编译幂等，重跑可继续）
        print(str(exc), file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    if not products:
        print("nothing to compile")
    return EXIT_OK
