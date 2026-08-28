"""Flowing 暴露层子包（``flowing.interfaces``）规约。

本子包承载 CLI / REPL / HTTP serve / Web 前端四个暴露层实现，定义
``flowing`` 命令行工具的**六子命令封闭集**及其行为契约。暴露层的
定位是「拉起层」：只做 ``launch(path, **kwargs)`` 拿 Runtime 这一件事，
**不解析配置、不认识插件、不知道项目结构、不感知 ``@``**。所有子命令
的差异只在「拿到 Runtime 之后做什么」。

.. rubric:: 子模块划分（S-44 结构性裁决）

- ``flowing.interfaces.cli`` —— 进程入口 ``main()`` 与 ``compile`` 壳。
- ``flowing.interfaces.repl`` —— 交互循环（``repl`` / ``cli`` 别名）。
- ``flowing.interfaces.run`` —— 一次性执行（``run``）与冒烟（``test``）。
- ``flowing.interfaces.serve`` —— HTTP API 封闭端点集（``serve``）。
- ``flowing.interfaces.web`` —— ``web`` 子命令 + 内置默认前端资产。
- 共享件（本包顶层）：``SUBCOMMANDS`` / ``EXIT_*`` 退出码 /
  ``parse_kv_args`` / ``_install_signal_handlers``。
.. rubric:: 统一原则

``main()`` 返回 Runtime，调用方决定如何暴露。同一份 ``@/main.py``
不改一行，被 CLI / HTTP / Web / 测试 / 进程内嵌入复用；CLI 六个
子命令只是「拿 Runtime 之后做什么」的六种薄壳选择：

- ``run``     ：``await runtime``，长驻进程，等待消息与信号。
- ``repl``    ：进入交互循环（别名 ``cli``）；提示符随绑定状态变化
  ——已绑定 ``(<agent_id>)>>>``，未绑定 ``(new agent)>>>``。
- ``repl-debug``：在 ``repl`` 基础上增加 ``/eval`` / ``/watch`` /
  ``/eval-runtime`` / ``/watch-runtime`` 调试命令（见
  ``flowing.interfaces.repl_debug``）。
- ``serve``   ：挂 HTTP 服务，纯 API（封闭固定端点集），无前端。
- ``web``     ：``serve`` 之上额外 serve 前端（``GET /`` + ``/assets/*``）。
- ``test``    ：拉起即断言（验证项目可拉起），随后 shutdown 退出。
- ``compile`` ：把 ``.fya`` 显式编译为同目录 ``.py``（不拉起 Runtime）。

.. rubric:: CLI 的职责边界（非行为）

CLI **不做**以下任何事，违反其中任何一条都视为实现错误：

- 不解析 ``providers.yaml`` / ``models.yaml`` / ``model-tags.yaml``
  等任何配置文件——配置解析是框架核心在 ``main`` 执行期间的职责。
- 不感知插件——CLI 不知道 ``runtime.use(...)`` 装过什么，也不为任何
  插件提供命令行开关。
- 不感知 ``@``——``@/`` 是框架内部的项目根前缀，由 ``launch(path)``
  内部登记到 contextvar；命令行的 ``<path>`` 是普通文件系统路径
  （``.`` 或任意目录），CLI 原样交给 ``launch``。
- 不解释 ``--key value`` 参数的含义——参数名与语义由子项目
  ``main`` 的签名决定，CLI 只负责收集与转发。
- 不为默认 repl / serve 提供开放扩展点——两者都是**封闭的冷启动
  观察窗口**（见下）。

.. rubric:: 封闭观察窗口原则

默认 repl（slash-command 封闭集）与默认 serve（HTTP 端点封闭集）
都不是完整产品面，只是「刚启动项目、想快速发条消息看看、查个快照」
的最小观察窗口。它们封闭、最小、可预测：

- 默认 repl **不接受**扩展注册 slash-command；需要自定义命令时，
  继承内置 repl 实现并覆写命令分发，或自己写一个 repl（用
  ``message()`` / ``snapshot()`` 等公开 API）。
- 默认 serve **不接受**开放注册任意 endpoint；需要自定义端点时，
  继承内置 serve 实现，或自己写一个 HTTP server（用
  ``enqueue_message()`` / ``snapshot()`` 等公开 API）。

.. rubric:: 参数分层约定

命令行参数分两层，**横线数即归属**：

- 位置参数 ``<path>``：子项目路径，缺省 ``.``（``flowing test
  --key val`` 等价于 ``flowing test . --key val``）。
- flowing 级参数**一律单横线**：``-m <file>``（入口 main 文件，
  缺省 ``@/main.py``）；``-a <host>`` / ``-p <port>``（serve/web
  专属监听地址，默认 ``127.0.0.1:8000``）；``-h`` / ``--help``
  （帮助）。全部在 ``cmd_*`` 层剥离，**不进** ``main`` 的 kwargs。
- main 的参数**一律双横线**：``--key val`` 经 :func:`parse_kv_args`
  收集进 ``main(**kwargs)``——即使代码里参数命名为单字母，CLI 上
  也写 ``--x val``。``--help`` 是 CLI 保留字，不透传给 main。

.. rubric:: 退出码约定

所有子命令使用统一的退出码语义（见 :data:`EXIT_OK` /
:data:`EXIT_RUNTIME_ERROR` / :data:`EXIT_USAGE_ERROR`）：

- ``0``：正常退出——包括 ``/exit``、EOF、以及收到 SIGINT/SIGTERM
  后走优雅关闭路径完成的退出（视为正常关闭）。
- ``1``：运行时错误——``launch`` 失败（``main`` 抛异常、项目不可
  import 等）、serve/web 端口绑定失败、compile 发生 hash 冲突等。
- ``2``：用法错误——未知子命令、``<path>`` 缺失或不存在、
  裸参数（非 ``--key`` 形式）出现在 ``<path>`` 之后等。

.. rubric:: 信号处理总约定

CLI 进程对 SIGINT / SIGTERM 安装统一的信号处理器（见
:func:`_install_signal_handlers`）：收到信号 → ``await
runtime.shutdown()``（协程；善后流程在其内按「递归 destroy 所有
Agent → 插件收尾 → 通信总线关闭 → ``_shutdown_event.set()``」的
顺序完成，**不等待** ``await runtime`` 的 waiter 被唤醒；M-66 统一
口径）→ ``await runtime`` 处被唤醒 → 进程以退出码 ``0`` 退出。

框架**不实现**「二次信号强制 kill」：关闭卡死时的兜底是应用层职责，
信号处理器中禁止 ``sys.exit()`` / ``os._exit()``（那会跳过 Agent
destroy 与插件收尾，留下未落盘状态与未关闭连接）。

.. rubric:: 长连接与流式的边界

``serve`` / ``web`` 的 HTTP API 初版只提供「请求 → 入队 → 等待回合
结果 → 一次性响应」的请求/响应模型。长连接推送（SSE / WebSocket）
与流式输出**初版不由 CLI 层提供**——那是 UI/暴露层的策略，由 HTTP
扩展或下游产品实现；框架只保证「消息能入队、回合结果能等待」。

.. seealso::

    :func:`flowing.runtime.launch`
        Runtime 唯一创建入口，所有子命令的第一步。
    :meth:`flowing.runtime.Runtime.shutdown`
        非阻塞优雅关闭，信号处理与 ``/exit`` 的最终汇聚点。
    :class:`flowing.agent.Agent`
        ``message()`` / ``enqueue_message()`` / ``cancel_queued()``
        是 repl 与 serve 投递消息的唯一通道。
    :mod:`flowing.interfaces.web`
        ``web`` 子命令的前端资产来源。
"""

from flowing.runtime import Runtime


SUBCOMMANDS: tuple[str, ...] = ("run", "repl", "cli", "repl-debug", "serve", "web", "test", "compile")
"""子命令封闭集（功能 + 动机合并：CLI 只暴露这七个名字，其中 ``cli``
是 ``repl`` 的别名；不接受插件或配置注册新子命令，保证 CLI 行为
可预测、与「CLI 只做拉起」的定位一致）。

行为边界：出现在 ``flowing`` 之后的第一个位置参数必须命中本集合，
否则按用法错误处理（打印用法并以 ``EXIT_USAGE_ERROR`` 退出）；
集合成员的比较是精确小写匹配，不做前缀匹配或模糊匹配。

.. seealso:: :func:`main`、:data:`EXIT_USAGE_ERROR`
"""

EXIT_OK: int = 0
"""退出码：正常退出。包括 ``/exit``、EOF（Ctrl-D）、以及
SIGINT/SIGTERM 触发的优雅关闭完成后的退出——信号驱动的关闭被视为
正常关闭路径，不以 ``130`` 之类的信号惯例码退出。

.. seealso:: :data:`EXIT_RUNTIME_ERROR`、:data:`EXIT_USAGE_ERROR`
"""

EXIT_RUNTIME_ERROR: int = 1
"""退出码：运行时错误。``launch`` 失败（子项目 ``main`` 抛异常、
``main.py`` 缺失或不可 import、PENDING 检查失败等）、serve/web 的
端口绑定失败、compile 检测到产物 hash 冲突，均以此码退出，错误
详情打印到 stderr。

.. seealso:: :data:`EXIT_OK`、:data:`EXIT_USAGE_ERROR`
"""

EXIT_USAGE_ERROR: int = 2
"""退出码：用法错误。未知子命令、``<path>`` 缺失或指向不存在的目录、
``<path>`` 之后出现非 ``--key`` 形式的裸参数，均以此码退出，并
打印用法说明到 stderr。

.. seealso:: :func:`main`、:func:`parse_kv_args`
"""

def parse_kv_args(argv: list[str]) -> dict[str, str | bool]:
    """把 ``--key value`` 形式的参数列表收集为 ``**kwargs`` 字典。

    .. rubric:: 功能介绍

    CLI 与子项目 ``main(**kwargs)`` 之间的唯一参数通道。CLI 不理解
    任何参数的语义，只做机械转换后原样转发。

    .. rubric:: 设计动机

    「CLI 不解析配置、不认识插件」要求参数解析本身也保持无知：
    参数名和类型语义由子项目 ``main`` 的签名决定（例如
    ``main(resume: str | None = None, ...)`` 接收
    ``--resume agent-xxx``）。CLI 若做类型推断或白名单校验，就把
    应用层策略渗入了拉起层。

    .. rubric:: 使用示例

    .. code-block:: python

        parse_kv_args(["--workspace_root", "/ws", "--debug"])
        # {"workspace_root": "/ws", "debug": True}

        parse_kv_args(["--workspace-root", "/ws"])
        # {"workspace_root": "/ws"}   # key 里的 '-' 转 '_'

    .. rubric:: 行为规约

    期待行为：

    - ``--key value``：一个键值对；``key`` 中的 ``-`` 全部转 ``_``；
      ``value`` 以**字符串原样**传递（``main`` 自行转换类型）。
    - ``--key``（后随另一个 ``--`` 开头项或列表结尾）：值为 ``True``
      （布尔 flag）。
    - 同一个 ``--key`` 出现多次：后者覆盖前者（不累积为列表）。

    非行为：

    - 不做类型推断（``"123"`` 保持 ``str``，不转 ``int``）。
    - 不支持 ``--key=value`` 等号形式——初版只约定空格分隔形式；
      收到等号形式按用法错误处理（由 :func:`main` 报
      ``EXIT_USAGE_ERROR``）。
    - 不识别 ``--`` 之后的裸位置参数——裸参数是 ``EXIT_USAGE_ERROR``。

    边缘情况：空列表返回空 dict；``--`` 单独出现按用法错误处理。

    .. rubric:: 测试案例

    - 前置：``["--a-b", "x", "--flag"]``。→ 期望：
      ``{"a_b": "x", "flag": True}``。
    - 前置：``["--k", "1", "--k", "2"]``。→ 期望：``{"k": "2"}``。
    - 前置：``["--k=v"]``。→ 期望：:func:`main` 层返回
      ``EXIT_USAGE_ERROR``。

    .. rubric:: 调用关系（审计）

    - 调用：无
    - 被调：``flowing.interfaces.cli.main``（时机：每次子命令分发前；收集结果
      经 ``launch(path, **kwargs)`` 透传给子项目 ``main``）

    .. seealso::

        :func:`flowing.runtime.launch`
            收集结果经 ``launch(path, **kwargs)`` 透传给子项目
            ``main``。
    """
    result: dict[str, str | bool] = {}
    i: int = 0
    while i < len(argv):
        token: str = argv[i]
        if not token.startswith("--"):
            # 裸参数：按用法错误处理（由 main 报 EXIT_USAGE_ERROR）
            i += 1
            continue
        key: str = token[2:].replace("-", "_")  # key 里的 '-' 全部转 '_'
        if "=" in key:
            # --key=value 等号形式：初版不支持，按用法错误处理（main 层报错）
            i += 1
            continue
        if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
            result[key] = argv[i + 1]  # value 以字符串原样传递，不做类型推断
            i += 2
        else:
            # 后随另一个 -- 开头项或列表结尾：布尔 flag
            result[key] = True
            i += 1
        # 同一 --key 出现多次：后者覆盖前者（dict 赋值语义）
    return result

def _install_signal_handlers(runtime: Runtime) -> None:
    """安装 SIGINT/SIGTERM → ``runtime.shutdown()`` 的桥接（内部 API，
    不属稳定契约）。

    .. rubric:: 功能介绍与动机

    把 OS 信号翻译为唯一的优雅关闭路径：信号处理器中**只**发起
    ``shutdown()``（非阻塞，发信号后立刻返回），真正的清理
    （递归 destroy → 插件收尾 → 总线关闭 → ``_shutdown_event.set()``）
    在事件循环内完成。处理器中禁止 ``sys.exit()`` / ``os._exit()``。

    .. rubric:: 行为规约

    - 对 SIGINT 与 SIGTERM 各安装一次；重复调用幂等（后装覆盖先装
      但语义相同）。
    - 框架**不实现**二次信号强制 kill：关闭卡死的兜底是应用层职责。
    - 非主线程 / 不支持信号的平台（如嵌入某些宿主）调用为 no-op，
    不抛异常。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.runtime.Runtime.shutdown()``（时机：每次收到
      SIGINT / SIGTERM，仅发起、非阻塞，立即返回）
    - 被调：``flowing.interfaces.run.cmd_run``（时机：第 2 步）、
      ``flowing.interfaces.repl.cmd_repl``（时机：第 1 步，同 ``cmd_run``）、
      ``flowing.interfaces.serve.cmd_serve``（时机：第 1 步）

    .. seealso:: :meth:`flowing.runtime.Runtime.shutdown`、:func:`cmd_run`
    """
    import asyncio
    import signal

    def _handler(signum: int, frame: object) -> None:
        # 处理器中只发起 shutdown()（非阻塞，发信号后立刻返回）；
        # 禁止 sys.exit() / os._exit()；真正的清理由事件循环完成
        asyncio.ensure_future(runtime.shutdown())

    try:
        # 对 SIGINT 与 SIGTERM 各安装一次；重复调用幂等（后装覆盖先装）
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError, RuntimeError):
        # 非主线程 / 不支持信号的平台：no-op，不抛异常
        pass
