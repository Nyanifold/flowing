"""``flowing.interfaces`` —— 暴露层子包：CLI / REPL / HTTP serve / Web 前端入口。

.. rubric:: 功能介绍

本子包是 Flowing 的暴露层：为 ``flowing`` 命令行工具提供进程入口与
各子命令实现。暴露层的唯一职责是「``launch(path, **kwargs)`` 拿
Runtime」——所有子命令拿到 Runtime 之后做什么，是本包各模块的全部
差异。

子命令封闭集由 :data:`SUBCOMMANDS` 定义，共八个名字：``run`` /
``repl`` / ``cli`` / ``repl-debug`` / ``serve`` / ``web`` / ``test`` /
``compile``。其中 ``cli`` 是 ``repl`` 的别名，二者走同一代码路径；
不接受插件或配置注册新子命令。``compile`` 不拉起 Runtime，是唯一
不经 :func:`flowing.runtime.launch` 的子命令。

边界（暴露层只做「拉起」这一件事）：

- 不解析配置文件（``providers.yaml`` / ``models.yaml`` 等）——配置
  读取是框架核心与子项目 ``main`` 执行期间的职责。
- 不认识插件——CLI 不知道 ``runtime.use(...)`` 装过什么，也不为任何
  插件提供命令行开关。
- 不感知 ``@``——``@/`` 是框架内部的项目根前缀，由
  :func:`flowing.runtime.launch` 登记到 asyncio Task 上下文；命令行的
  ``<path>`` 是普通文件系统路径（``.`` 或任意目录），CLI 原样交给
  ``launch``。

.. rubric:: 子模块划分

- ``flowing.interfaces.cli`` —— 进程入口 ``main()`` 与 ``compile``
  子命令。
- ``flowing.interfaces.repl`` —— 交互式 REPL（``repl`` / 别名
  ``cli``）。
- ``flowing.interfaces.repl_debug`` —— ``repl`` 的调试扩展版
  （``repl-debug``，增加 4 个调试 slash-command）。
- ``flowing.interfaces.run`` —— 一次性执行（``run``）与冒烟测试
  （``test``）。
- ``flowing.interfaces.serve`` —— HTTP API 服务（``serve``，纯 API
  无前端）。
- ``flowing.interfaces.web`` —— ``web`` 子命令与内置默认前端资产。
- 共享件（本包顶层）：``SUBCOMMANDS`` / ``EXIT_OK`` /
  ``EXIT_RUNTIME_ERROR`` / ``EXIT_USAGE_ERROR`` / ``parse_kv_args`` /
  ``_install_signal_handlers``。

.. rubric:: 全局约定（跨符号、影响使用的约定）

参数分层（横线数即归属）：

- 位置参数 ``<path>``：子项目路径，缺省 ``.`` （``flowing test
  --key val`` 等价于 ``flowing test . --key val``）。
- flowing 级参数一律单横线：``-m <file>`` （入口 main 文件，缺省
  ``@/main.py``）；``-a <host>`` / ``-p <port>`` （serve / web 专属
  监听地址，默认 ``127.0.0.1:8000``）；``-h`` / ``--help`` （帮助）。
  全部在 ``cmd_*`` 分发层剥离，不进入子项目 ``main`` 的 kwargs。
- 子项目 ``main`` 的参数一律双横线：``--key value`` 经
  :func:`parse_kv_args` 收集，经 ``launch(path, **kwargs)`` 原样透传
  给 ``main(**kwargs)``——即使代码里参数命名为单字母，CLI 上也写
  ``--x val``。``--help`` 是 CLI 保留字，不透传给 ``main``。

退出码（所有子命令统一，见 :data:`EXIT_OK` / :data:`EXIT_RUNTIME_ERROR` /
:data:`EXIT_USAGE_ERROR`）：

- ``0``：正常退出——包括 ``/exit``、EOF（Ctrl-D），以及收到
  SIGINT / SIGTERM 后走优雅关闭路径完成的退出（视为正常关闭）。
- ``1``：运行时错误——``launch`` 失败（子项目 ``main`` 抛异常、
  项目不可 import 等）、serve / web 端口绑定失败、compile 检测到
  产物被外部修改。
- ``2``：用法错误——未知子命令、``<path>`` 缺失或不存在、
  ``<path>`` 之后出现非 ``--key`` 形式的裸参数。

信号处理：CLI 进程对 SIGINT / SIGTERM 安装统一的信号处理器（见
:func:`_install_signal_handlers`）。处理器只发起 ``runtime.shutdown()``
（非阻塞，立即返回）；``shutdown()`` 完成递归 destroy 全部节点与插件
收尾后置位退出事件，``await runtime`` 处随即唤醒，进程以退出码 ``0``
退出。框架不实现「二次信号强制 kill」——关闭卡死时的兜底是应用层职责。

封闭观察窗口：默认 ``repl`` 的 slash-command 集合与默认 ``serve`` 的
HTTP 端点集合都是封闭的，不接受运行时注册新命令 / 新端点——它们是
「刚启动项目、想快速发条消息看看、查个快照」的最小观察窗口，不是可
扩展的交互框架。需要自定义命令或端点时，继承内置实现扩展（``repl-debug``
对 ``repl`` 的扩展方式即内置注入点的用法），或自己实现一个 repl /
HTTP server（经 ``message()`` / ``enqueue_message()`` / ``snapshot()``
等公开 API）。

流式：``serve`` / ``web`` 的 HTTP API 提供请求/响应模型
（``POST /agents/<agent-id>/message`` 投递消息并等待回合结果），另经
``GET /agents/<agent-id>/stream`` 提供 SSE 流式推送（生成过程的
delta 事件）。WebSocket 不引入。

.. rubric:: 使用示例

.. code-block:: console

    $ flowing run . --workspace_root /ws --debug     # 长驻进程
    $ flowing repl /path/to/project                  # 交互式 REPL（别名 cli）
    $ flowing serve . -p 9000                        # HTTP API（纯 API）
    $ flowing web .                                  # serve + 内置前端页面
    $ flowing test .                                 # 冒烟拉起后退出
    $ flowing compile .                              # 把 .fya 编译为同目录 .py

.. seealso::

    :func:`flowing.runtime.launch`
        Runtime 唯一创建入口，所有子命令的第一步。
    :meth:`flowing.runtime.Runtime.shutdown`
        非阻塞优雅关闭，信号处理与 ``/exit`` 的最终汇聚点。
    :mod:`flowing.interfaces.web`
        ``web`` 子命令的前端资产来源。
"""

import json
from pathlib import Path
from typing import Any

from flowing.message import MessageKind, TextBlock, from_record
from flowing.runtime import Runtime


SUBCOMMANDS: tuple[str, ...] = ("run", "repl", "cli", "repl-debug", "serve", "web", "test", "compile")
"""子命令封闭集：``flowing`` 命令行工具的全部子命令名字。

出现在 ``flowing`` 之后的第一个位置参数必须命中本集合，否则按用法
错误处理（向 stderr 打印用法并以 :data:`EXIT_USAGE_ERROR` 退出）。
名字比较是精确小写匹配，不做前缀匹配或模糊匹配。``cli`` 是 ``repl``
的别名，分发时归一化为 ``repl`` （同一代码路径）。

.. seealso:: :func:`flowing.interfaces.cli.main`、:data:`EXIT_USAGE_ERROR`
"""

EXIT_OK: int = 0
"""退出码：正常退出。

包括 ``/exit``、EOF（Ctrl-D），以及收到 SIGINT / SIGTERM 后走优雅关闭
路径完成的退出——信号驱动的关闭被视为正常关闭，不以 ``130`` 之类的
信号惯例码退出。

.. seealso:: :data:`EXIT_RUNTIME_ERROR`、:data:`EXIT_USAGE_ERROR`
"""

EXIT_RUNTIME_ERROR: int = 1
"""退出码：运行时错误。

``launch`` 失败（子项目 ``main`` 抛异常、``main.py`` 缺失或不可
import 等）、serve / web 的端口绑定失败、compile 检测到产物被外部
修改，均以此码退出；错误详情打印到 stderr。

.. seealso:: :data:`EXIT_OK`、:data:`EXIT_USAGE_ERROR`
"""

EXIT_USAGE_ERROR: int = 2
"""退出码：用法错误。

未知子命令、``<path>`` 缺失或指向不存在的目录、``<path>`` 之后出现
非 ``--key`` 形式的裸参数，均以此码退出；用法说明打印到 stderr。

.. seealso:: :func:`flowing.interfaces.cli.main`、:func:`parse_kv_args`
"""

def parse_kv_args(argv: list[str]) -> dict[str, str | bool]:
    """把 ``--key value`` 形式的参数列表收集为 ``**kwargs`` 字典。

    CLI 与子项目 ``main(**kwargs)`` 之间的唯一参数通道。CLI 不理解
    任何参数的语义，只做机械转换后原样转发——参数名与类型语义由
    子项目 ``main`` 的签名决定。

    .. rubric:: 使用示例

    .. code-block:: python

        parse_kv_args(["--a-b", "x", "--flag"])
        # -> {"a_b": "x", "flag": True}   （key 里的 "-" 转 "_"）

        parse_kv_args(["--k", "1", "--k", "2"])
        # -> {"k": "2"}                  （重复 key 后者覆盖）

        parse_kv_args([])
        # -> {}

    .. rubric:: 行为要点

    - ``--key value``：一个键值对；``key`` 中的 ``-`` 全部转 ``_``；
      ``value`` 以字符串原样传递，不做类型推断（``main`` 自行转换
      类型）。
    - ``--key`` 后随另一个 ``--`` 开头项或列表结尾：值为 ``True``
      （布尔 flag）。
    - 同一个 ``--key`` 出现多次：后者覆盖前者，不累积为列表。
    - 不以 ``--`` 开头的裸参数与 ``--key=value`` 等号形式：跳过，
      不收入结果——这些形态的用法错误由
      :func:`flowing.interfaces.cli.main` 在调用本函数之前报出。
    - 空列表返回空字典。

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
            # --key=value 等号形式：不支持，按用法错误处理（main 层报错）
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
    """安装 SIGINT / SIGTERM → ``runtime.shutdown()`` 的桥接（内部 API，
    不属稳定契约）。

    对 SIGINT 与 SIGTERM 各安装一次处理器；处理器只发起
    ``shutdown()`` （非阻塞，发信号后立即返回），真正的清理在事件
    循环内完成。重复调用幂等（后装覆盖先装，语义相同）。非主线程 /
    不支持信号的平台调用为 no-op，不抛异常。

    .. seealso:: :meth:`flowing.runtime.Runtime.shutdown`
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


def _default_agent_type(runtime: Runtime) -> str | None:
    """取项目默认主 Agent 类型（内部 API，不属稳定契约）。

    从池名录中根条目（``parent_agent_id`` 等于 Runtime 自身 id 的
    条目）里取 ``created_at`` 最新者的 ``agent_type``；池无根条目时
    返回 ``None``。repl 未绑定态隐式创建与 serve ``POST /agents``
    缺省类型共用本判定。
    """
    roots = [
        meta for meta in runtime._agent_pool.values()
        if meta.get("parent_agent_id") == runtime.node_id
    ]
    if not roots:
        return None
    return max(roots, key=lambda m: m.get("created_at") or "")["agent_type"]


def _last_reply_prefix(session_dir: Path, *, limit: int = 40) -> str:
    """懒读 session 目录 ``tree.jsonl`` 尾部最后一条 PROVIDER 消息的
    文本前缀（内部 API，不属稳定契约）。

    repl ``/agents`` 与 serve ``GET /agents`` 名录行的「最后一次回复
    前缀」数据源；未激活 Agent 没有内存对象，只能读盘，前缀不回写池
    元数据。解析容错：撕裂末行截断、损坏行跳过、墓碑行移除对应消息；
    ``tree.jsonl`` 缺失或无存活 PROVIDER 消息时返回空串。前缀折叠为
    单行，超长截断加省略号。
    """
    path = session_dir / "tree.jsonl"
    if not path.is_file():
        return ""
    alive: dict[str, str] = {}   # message id → 折叠前文本（仅 PROVIDER）
    order: list[str] = []        # PROVIDER 消息行序（尾部 = 最后一条）
    segments = path.read_bytes().split(b"\n")
    segments.pop()   # 撕裂末行截断（与 FileRecordStore 同口径）
    for seg in segments:
        if not seg:
            continue
        try:
            record = json.loads(seg)
        except json.JSONDecodeError:
            continue   # 损坏行跳过（名录提示不阻断）
        if not isinstance(record, dict):
            continue
        rtype = record.get("type")
        if rtype == "message" and record.get("kind") == MessageKind.PROVIDER.value:
            try:
                msg = from_record(record)   # 复用既有解析，不自带第二份序列化逻辑
            except Exception:
                continue
            text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
            alive[msg.id] = text
            order.append(msg.id)
        elif rtype == "tombstone":
            rid = record.get("id")
            alive.pop(rid, None)
            if rid in order:
                order.remove(rid)
    text = ""
    for mid in reversed(order):
        if mid in alive:
            text = alive[mid]
            break
    folded = " ".join(text.split())   # 折叠为单行
    return folded[:limit] + ("…" if len(folded) > limit else "")


def _session_mtime(session_dir: Path) -> float | None:
    """session 目录的最后修改时间：目录内文件 mtime 的最大值（内部
    API，不属稳定契约）。

    目录不存在或没有文件时回退到目录自身 mtime；均不可得时返回
    ``None``。
    """
    try:
        mtimes = [f.stat().st_mtime for f in session_dir.iterdir() if f.is_file()]
    except OSError:
        return None
    if mtimes:
        return max(mtimes)
    try:
        return session_dir.stat().st_mtime
    except OSError:
        return None


def _list_agent_records(runtime: Runtime) -> list[dict[str, Any]]:
    """池名录懒读共享辅助（内部 API，不属稳定契约）。

    repl ``/agents`` 与 serve ``GET /agents`` 名录输出的同一份实现，
    避免两处口径漂移。返回池名录全部条目（含未激活的休眠记录），
    顺序即池插入序；每条记录含 ``agent_id`` / ``agent_type`` /
    ``parent_agent_id`` / ``created_at`` / ``active`` （是否在活体表
    中）/ ``last_reply`` （最后一条 PROVIDER 文本前缀，见
    :func:`_last_reply_prefix`）/ ``mtime`` （session 目录最后修改
    时间，秒级时间戳，不可得为 ``None``，见 :func:`_session_mtime`）。
    纯读操作，无副作用；session 目录损坏不影响名录其余条目。
    """
    records: list[dict[str, Any]] = []
    for agent_id, meta in runtime._agent_pool.items():
        session_dir = runtime._load_session_dir(meta.get("session_dir"), agent_id)
        records.append({
            "agent_id": agent_id,
            "agent_type": meta.get("agent_type", ""),
            "parent_agent_id": meta.get("parent_agent_id", ""),
            "created_at": meta.get("created_at", ""),
            "active": agent_id in runtime._nodes,
            "last_reply": _last_reply_prefix(session_dir),
            "mtime": _session_mtime(session_dir),
        })
    return records
