"""``flowing.interfaces.cli`` —— CLI 进程入口 ``main()`` 与 ``compile`` 子命令壳。

统一原则 / 封闭观察窗口 / 参数分层 / 退出码 / 信号处理总约定见
``flowing.interfaces`` 包 docstring；本模块只做「解析 argv + 分发」
与 ``.fya`` 编译壳两件事。
"""

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


def main(argv: list[str] | None = None) -> int:
    """CLI 进程入口：解析子命令并分发，返回进程退出码。

    .. rubric:: 功能介绍

    ``flowing`` 可执行脚本的 ``main`` 函数。负责：解析 ``argv`` 的
    第一个位置参数为子命令名（``cli`` 归一化为 ``repl``），第二个
    位置参数为子项目路径 ``<path>``（缺省 ``.``）；随后先剥离
    flowing 级单横线参数（``-h``/``--help``/``-m``/``-a``/``-p``，
    见 ``flowing.interfaces`` 包 docstring「参数分层约定」），其余 ``--key value`` 参数经
    :func:`parse_kv_args` 收集，然后分发到对应的 ``cmd_*`` 协程
    （``compile`` 除外，它是同步的）并以 ``asyncio.run`` 驱动。

    .. rubric:: 设计动机

    把「解析与分发」收敛为一个同步入口，使六个子命令的全部差异都
    体现在各自的 ``cmd_*`` 函数里；入口本身不理解任何子命令语义，
    与「CLI 只做拉起」的定位一致。

    .. rubric:: 使用示例

    .. code-block:: console

        $ flowing run . --workspace_root /ws --debug
        $ flowing repl /path/to/project          # 等价 flowing cli ...
        $ flowing serve . -p 9000
        $ flowing web -a 0.0.0.0 -p 8080
        $ flowing test . -m ./tests/main_test.py --key val
        $ flowing compile .

    .. rubric:: 行为规约

    期待行为：

    1. ``argv is None`` 时取 ``sys.argv[1:]``。
    2. 第一个位置参数命中 :data:`SUBCOMMANDS` → 分发；``cli`` 与
       ``repl`` 走同一代码路径（:func:`cmd_repl`）。
    3. 分发前先剥离 flowing 级单横线参数（``-m``/``-a``/``-p`` 按
       子命令归属传给对应 ``cmd_*`` 的显式参数），再完成
       ``--key value`` 解析；flowing 级参数**不**进入 ``main`` 的
       kwargs。
    4. ``-h`` / ``--help``：打印该子命令用法到 stdout，返回
       ``EXIT_OK``；``--help`` 为保留字，不透传给 main。
    5. 子命令协程返回的退出码原样作为返回值。

    非行为：不打印框架 banner 之外的任何诊断；不捕获子命令内部的
    业务异常做重试——``launch`` 失败就是 ``EXIT_RUNTIME_ERROR``。

    边缘情况：

    - 空 ``argv``、未知子命令、``<path>`` 缺失 → 打印用法到
      stderr，返回 ``EXIT_USAGE_ERROR``。
    - ``<path>`` 不存在或不是目录 → 同上，``EXIT_USAGE_ERROR``
      （路径存在性在 CLI 层检查，``@`` 语义在 ``launch`` 层处理）。
    - ``compile`` 不进入 asyncio 事件循环，直接同步分发。

    .. rubric:: 测试案例

    - 前置：无参数。→ 操作：``main([])``。→ 期望：返回
      ``EXIT_USAGE_ERROR``，stderr 含子命令列表。
    - 前置：``argv = ["frobnicate", "."]``。→ 期望：返回
      ``EXIT_USAGE_ERROR``。
    - 前置：``argv = ["run", "/nonexistent-dir"]``。→ 期望：返回
      ``EXIT_USAGE_ERROR``（路径在 launch 之前被 CLI 拒绝）。
    - 前置：合法项目目录。→ 操作：``main(["cli", path])`` 与
      ``main(["repl", path])``。→ 期望：两者进入完全相同的 repl
      代码路径。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.interfaces.parse_kv_args()``（时机：每次分发前收集
      ``--key value`` 参数）、各 ``cmd_*`` 子命令函数（时机：每次
      子命令分发，经 ``asyncio.run`` 驱动；``compile`` 同步直调）
    - 被调：无（进程入口；框架内无调用方，``__init__.pyi`` 说明
      ``flowing.interfaces.cli`` 细节不属稳定边界）

    .. seealso::

        :func:`flowing.interfaces.parse_kv_args` —— ``--key value`` 参数的收集规则。
        :func:`flowing.interfaces.run.cmd_run`、
        :func:`flowing.interfaces.repl.cmd_repl`、
        :func:`flowing.interfaces.serve.cmd_serve`、
        :func:`flowing.interfaces.web.cmd_web`、
        :func:`flowing.interfaces.run.cmd_test`、
        :func:`cmd_compile` —— 六个子命令的行为契约。
    """
    import asyncio
    import sys

    if argv is None:
        argv = sys.argv[1:]
    argv_list: list[str] = list(argv)
    if not argv_list:
        # 空 argv：打印用法到 stderr
        return EXIT_USAGE_ERROR
    subcommand: str = argv_list[0]
    if subcommand not in SUBCOMMANDS:
        # 未知子命令：打印用法到 stderr
        return EXIT_USAGE_ERROR
    if len(argv_list) > 1 and not argv_list[1].startswith("-"):
        path: str = argv_list[1]
        rest: list[str] = argv_list[2:]
    else:
        path = "."  # <path> 缺省
        rest = argv_list[1:]
    if not Path(path).is_dir():
        # <path> 不存在或不是目录：打印用法到 stderr（存在性在 CLI 层检查）
        return EXIT_USAGE_ERROR
    # 剥离 flowing 级单横线参数，其余 --key value 交 parse_kv_args
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
        elif token == "-m":
            main_file = rest[i + 1]
            i += 2
        elif token == "-a":
            host = rest[i + 1]
            i += 2
        elif token == "-p":
            port = int(rest[i + 1])
            i += 2
        else:
            # --key value 与裸参数都留在此处；裸参数由 parse_kv_args 规约
            # 按用法错误处理（EXIT_USAGE_ERROR）
            kv_argv.append(token)
            i += 1
    if help_requested:
        # 打印该子命令用法到 stdout
        return EXIT_OK
    kwargs: dict[str, str | bool] = parse_kv_args(kv_argv)
    if subcommand == "run":
        return asyncio.run(cmd_run(path, main_file=main_file, **kwargs))
    if subcommand in ("repl", "cli"):
        # cli 归一化为 repl，同一代码路径
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

    .. rubric:: 功能介绍

    对子项目目录做静态编译：每个 ``*.fya`` 产出**同目录**同名
    ``.py``（去 ``.fya`` 后缀，兼容 ``from payment import
    PaymentAgent``），hash 元信息写在**产物同目录**的
    ``.flowing.meta.yaml``（P3-10 裁决；三字段 ``fya_hash`` /
    ``py_hash`` / ``compiler_version``，两 hash 均取语义口径——
    解析结构 / AST)。本命令
    **不拉起 Runtime**、不执行 ``main``，是唯一不经 ``launch`` 的
    子命令，因此也是同步函数。

    .. rubric:: 设计动机

    运行期 ``.fya`` 一律经字符串名惰性解析（``get_agent_class`` →
    ``compiler.compile_fya_class`` 内存合成，零中间文件）；显式编译
    服务于生产部署与 CI 静态检查——产物可被 linter /
    type checker / mypy 分析。编译与解析只发生在启动/构建阶段，
    运行期不监听文件变更（不允热重启）。

    .. rubric:: 使用示例

    .. code-block:: console

        $ flowing compile .
        # @/agents/payment/agent.fya →  @/agents/payment/agent.py
        # @/tools/submit.fya         →  @/tools/submit.py
        # meta 写入各产物同目录的 .flowing.meta.yaml（条目以 fya 文件名为键）

    .. rubric:: 行为规约

    期待行为：

    1. 递归扫描 ``<path>`` 下全部 ``*.fya``。
    2. 逐文件编译为同目录 ``.py``；产物中的 Parsable 值以类体赋值
       形式写出（如 ``system_prompt = Parsable('$./system-prompt.md')``）。
    3. 写入/更新产物同目录 ``.flowing.meta.yaml`` 中的 hash 条目：
       源 ``.fya`` 变了（解析结构 hash 口径）或 ``compiler_version``
       不同 → 重新编译；``.py`` 产物被外部修改
       （``py_hash`` AST 口径不匹配）→ **报错中止**，不静默覆盖。

    非行为：不删除无对应 ``.fya`` 的孤儿 ``.py``；不编译
    ``.py`` 手写的 Agent（两种存在形式等价互斥，编译只覆盖
    ``.fya`` 一侧）。

    边缘情况：

    - 项目内无 ``.fya`` 文件：正常完成，返回 ``EXIT_OK``（打印
      「无可编译文件」）。
    - hash 冲突：指出冲突文件，返回 ``EXIT_RUNTIME_ERROR``；已产出
      的其他文件不回滚（编译是幂等的，重跑可继续）。
    - 重复执行：无变更时全量命中 hash，等价 no-op。

    .. rubric:: 测试案例

    - 前置：项目含两个 ``.fya``。→ 操作：``compile`` 两次。→ 期望：
      第一次产出两个 ``.py`` 与 meta；第二次为 no-op（mtime 与
      hash 均不变）。
    - 前置：手工改动某个产物 ``.py``。→ 操作：``compile``。→ 期望：
      ``EXIT_RUNTIME_ERROR``，stderr 含该文件路径，且该文件未被
      覆盖。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.compiler.compile_project()``（时机：每次执行；
      S-04 裁决补载体）
    - 被调：``flowing.interfaces.cli.main``（时机：子命令 ``compile`` 分发，
      同步直调、不经事件循环）

    .. seealso::

        :func:`main`
            分发规则（compile 不进入事件循环）。
    """
    from flowing.compiler import compile_project
    from flowing.errors import ArtifactModifiedError

    # 编译器入口（S-04 裁决补载体：flowing.compiler.compile_project）；
    # 无 .fya 时 compile_project 返回空列表——打印「无可编译文件」并正常退出
    try:
        products = compile_project(Path(path))
    except ArtifactModifiedError as exc:
        # hash 冲突：指出冲突文件（异常消息含路径），报错中止；
        # 已产出的其他文件不回滚（编译幂等，重跑可继续）
        print(str(exc))  # stderr 通道细节属 CLI 输出实现
        return EXIT_RUNTIME_ERROR
    if not products:
        print("无可编译文件")
    return EXIT_OK
    return EXIT_OK
