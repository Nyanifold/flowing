"""``flowing.interfaces.run`` —— 一次性执行（``run``）与冒烟测试（``test``）子命令。

退出码约定见 ``flowing.interfaces`` 包 docstring。
"""

import dataclasses
import json
import sys

from flowing.runtime import launch

from flowing.interfaces import EXIT_OK, EXIT_RUNTIME_ERROR, _install_signal_handlers


async def cmd_run(
    path: str,
    main_file: str | None = None,
    **kwargs: str | bool,
) -> int:
    """``flowing run <path>``：launch 后 ``await runtime``，长驻进程。

    .. rubric:: 功能介绍

    最朴素的长驻形态：拉起子项目后阻塞在 ``await runtime`` 上，等待
    消息（来自 Cron、通信扩展、嵌入方等任意 ``enqueue_message()``
    来源）与关闭信号。``repl`` 与 ``serve`` 都是在本形态之上加交互
    层或 HTTP 骨架。

    .. rubric:: 使用示例

    .. code-block:: console

        $ flowing run . --workspace_root /ws --debug

    .. rubric:: 行为要点

    1. ``runtime = await launch(path, main_file=main_file, **kwargs)``
       ——登记 ``@`` 上下文、import 子项目入口 main 文件（缺省
       ``@/main.py``，经 CLI ``-m`` 指定）、执行 ``main``。返回时
       Agent 已激活、消息级树与队列已就绪，逻辑 Turn 循环挂起等待
       消息。
    2. 安装 SIGINT / SIGTERM 信号处理器（:func:`_install_signal_handlers`）。
    3. ``await runtime`` 阻塞直到 shutdown。
    4. 唤醒后返回 :data:`EXIT_OK`。

    边界与边缘情况：

    - 不从 stdin 读任何输入（要交互用 ``repl``）。
    - 子项目未 ``mount`` （无默认节点形态）：完全合法，Runtime 空转，
      ``await runtime`` 依然只等 shutdown 事件。
    - ``launch`` 抛异常：异常摘要打印到 stderr，返回
      :data:`EXIT_RUNTIME_ERROR`，进程不再等待信号。

    :param path: 子项目路径（普通文件系统路径）。
    :param main_file: 替代的入口 main 文件（可选，经 CLI ``-m`` 传入）。
    :param kwargs: 透传给 ``launch`` 与子项目 ``main`` 的 ``--key
        value`` 参数。
    :return: :data:`EXIT_OK` （正常关闭）或
        :data:`EXIT_RUNTIME_ERROR` （``launch`` 失败）。

    .. seealso::

        :func:`flowing.runtime.launch`、:meth:`flowing.runtime.Runtime.shutdown`
        :func:`_install_signal_handlers` —— 信号到 shutdown 的桥接。
    """
    # launch 抛异常：异常摘要打印到 stderr，返回 EXIT_RUNTIME_ERROR，
    # 进程不再等待信号（包 docstring 退出码约定：launch 失败 = 运行时错误）
    try:
        runtime = await launch(path, main_file=main_file, **kwargs)
    except Exception as exc:
        print(f"launch 阶段失败：{exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    _install_signal_handlers(runtime)
    await runtime
    # 唤醒后（shutdown 完成）
    return EXIT_OK

async def cmd_test(
    path: str,
    main_file: str | None = None,
    **kwargs: str | bool,
) -> int:
    """``flowing test <path>``：拉起即断言，随后停。

    .. rubric:: 功能介绍

    ``test`` 子命令的职责是冒烟拉起：``launch(path,
    main_file=main_file, **kwargs)``（``-m`` 可指定替代入口文件，如
    冒烟专用 ``main_test.py``）成功后取一次 ``runtime.snapshot()``
    验证可观测面就绪，随后 ``runtime.shutdown()`` 并返回
    :data:`EXIT_OK`；``launch`` 或快照断言失败返回
    :data:`EXIT_RUNTIME_ERROR`。

    「拿 Runtime 做断言后停」中断言什么（项目级行为断言）是子项目的
    策略，不是框架机制：由用户经嵌入 API 自行编写（``launch`` +
    ``message()`` + ``snapshot()``，放进 pytest 等任意测试框架）。
    ``test`` 子命令只提供零配置的「项目能否拉起」冒烟检查。

    .. rubric:: 使用示例

    .. code-block:: console

        $ flowing test . && echo "project boots"
        $ flowing test . -m ./tests/main_test.py   # 指定替代入口文件

    项目级断言（用户自写，不经 ``flowing test``）：

    .. code-block:: python

        async def test_root_agent():
            runtime = await launch("/path/to/project")
            agent = await runtime.get_agent("root")
            result = await agent.query("你好")
            assert result.status in ("completed", "error")
            await runtime.shutdown()

    .. rubric:: 行为要点

    1. ``runtime = await launch(path, main_file=main_file, **kwargs)``；
       ``launch`` 失败（``main`` 抛异常、项目不可 import 等）→ 向
       stderr 打印异常摘要，返回 :data:`EXIT_RUNTIME_ERROR`。
    2. ``snap = runtime.snapshot()``；冒烟断言口径：快照调用不抛异常
       且结果可 JSON 序列化即通过，不断言业务字段。
    3. ``await runtime.shutdown()`` 完整关闭后返回
       :data:`EXIT_OK`；快照断言失败仍尝试 shutdown 后再退出。

    边界：不发现 / 不执行项目里的测试文件；不 mock LLM（项目拉起不
    要求真实 Provider 可用——懒创建保证未调用的 Provider 不产生网络
    活动）。

    :param path: 子项目路径（普通文件系统路径）。
    :param main_file: 替代的入口 main 文件（可选，经 CLI ``-m`` 传入）。
    :param kwargs: 透传给 ``launch`` 与子项目 ``main`` 的 ``--key
        value`` 参数。
    :return: :data:`EXIT_OK` （拉起成功并正常关闭）或
        :data:`EXIT_RUNTIME_ERROR` （``launch`` 或快照断言失败）。

    .. seealso::

        :func:`flowing.runtime.launch`、
        :meth:`flowing.runtime.Runtime.snapshot`、
        :meth:`flowing.agent.Agent.query`
    """
    try:
        runtime = await launch(path, main_file=main_file, **kwargs)
    except Exception as exc:
        # main 抛异常 / 项目不可 import / PENDING 检查失败等：stderr 指明
        # 失败发生在 launch 阶段，返回 EXIT_RUNTIME_ERROR
        print(f"launch 阶段失败：{exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    try:
        snap = runtime.snapshot()
        # 冒烟断言口径：快照调用不抛 + 结果可 JSON 序列化即通过；
        # 不断言业务字段（项目级断言归用户自写测试）。dataclasses.asdict
        # 展开嵌套快照结构，datetime 等经 default=str 兜底
        json.dumps(dataclasses.asdict(snap), default=str)
    except Exception as exc:
        print(f"快照冒烟断言失败：{exc}", file=sys.stderr)
        # 失败路径仍尝试完整 shutdown 后再退出
        try:
            await runtime.shutdown()
        except Exception as shutdown_exc:
            print(f"shutdown 失败：{shutdown_exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    await runtime.shutdown()
    return EXIT_OK
