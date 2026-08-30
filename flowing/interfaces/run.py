"""``flowing.interfaces.run`` —— 一次性执行（``run``）与冒烟测试（``test``）子命令。

统一原则与退出码约定见 ``flowing.interfaces`` 包 docstring。
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
    来源）与关闭信号。

    .. rubric:: 设计动机

    ``run`` 是其他所有长驻子命令的基准形态——``repl = run + 交互
    循环``、``serve = run + HTTP 骨架``。把它定义得最薄，其余命令
    的增量契约才清晰。

    .. rubric:: 使用示例

    .. code-block:: console

        $ flowing run . --workspace_root /ws --debug

    对应子项目入口：

    .. code-block:: python

        # @/main.py
        async def main(workspace_root: str, debug: bool = False) -> Runtime:
            runtime = Runtime()
            runtime.provide("workspace_root", workspace_root)
            await runtime.mount("@/root.fya")
            return runtime

    .. rubric:: 行为规约

    期待行为（严格时序）：

    1. ``runtime = await launch(path, main_file=main_file, **kwargs)``
       ——登记 ``@`` 上下文、import 入口 main 文件（缺省 ``@/main.py``，
       经 CLI ``-m`` 指定）、执行 ``main``。返回时 Agent 已激活、
       消息级树与队列已就绪，逻辑 Turn 循环在 ``await queue.get()``
       处挂起。
    2. :func:`_install_signal_handlers` 安装 SIGINT/SIGTERM 处理器。
    3. ``await runtime`` 阻塞直到 shutdown。
    4. 唤醒后返回 ``EXIT_OK``。

    非行为：不从 stdin 读任何输入（要交互用 ``repl``）；不打印
    Agent 的内部状态（要观察用 ``/snapshot`` 所在的 repl 或
    ``GET /snapshot`` 所在的 serve）。

    边缘情况：

    - 子项目未 ``mount``（无默认节点形态）：完全合法，Runtime 空转，
      ``await runtime`` 依然只等 ``_shutdown_event``。
    - ``launch`` 抛异常：异常摘要打印到 stderr，返回
      ``EXIT_RUNTIME_ERROR``，进程不再等待信号。
    - 若不做 ``await runtime``（实现遗漏第 3 步），进程会立即退出
      ——这是实现错误而非合法行为。

    .. rubric:: 测试案例

    - 前置：合法项目。→ 操作：启动 ``run`` 后发送 SIGTERM。→ 期望：
      优雅关闭顺序完整执行（destroy → 插件收尾 → 总线关闭 → 事件
      置位），退出码 ``EXIT_OK``。
    - 前置：``main`` 抛 ``ValueError``。→ 期望：退出码
      ``EXIT_RUNTIME_ERROR``，stderr 含异常信息。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.runtime.launch()``（时机：第 1 步，每次执行）、
      ``flowing.interfaces._install_signal_handlers()``（时机：第 2 步）、
      ``flowing.runtime.Runtime.__await__``（时机：第 3 步，阻塞至
      shutdown 后唤醒）
    - 被调：``flowing.interfaces.cli.main``（时机：子命令 ``run`` 分发，经
      ``asyncio.run`` 驱动）

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

    初版约定 ``test`` 的职责为**冒烟拉起**：``launch(path,
    main_file=main_file, **kwargs)``（``-m`` 可指定替代入口文件，
    如冒烟专用 ``main_test.py``）成功后取一次
    ``runtime.snapshot()`` 验证可观测面就绪，随后
    ``runtime.shutdown()`` 并返回 ``EXIT_OK``；``launch`` 或快照断言
    失败返回 ``EXIT_RUNTIME_ERROR``。

    .. rubric:: 设计动机

    「拿 Runtime 做断言后停」中**断言什么**是子项目的策略，不是
    框架机制。框架初版不提供测试发现/断言机制——项目级行为断言由
    用户经嵌入 API 自行编写（``launch`` + ``message()`` +
    ``snapshot()``，放进 pytest 等任意测试框架）。``test`` 子命令
    提供的是零配置的「项目能否拉起」冒烟检查，与 CI 中最常见的
    第一道工序对应。

    .. rubric:: 使用示例

    .. code-block:: console

        $ flowing test . && echo "project boots"
        $ flowing test . -m ./tests/main_test.py   # 指定替代入口文件

    项目级断言（用户自写，不经 ``flowing test``）：

    .. code-block:: python

        async def test_root_agent():
            runtime = await launch("/path/to/project")
            agent = await runtime.get_agent("root")   # get_agent 为 async（C-18 修正）
            result = await agent.query("你好")      # content 收 str | list[ContentBlock]，打包由 query() 完成（C-18 修正）
            assert result.status in ("completed", "error")
            await runtime.shutdown()

    .. rubric:: 行为规约

    期待行为（严格时序）：

    1. ``runtime = await launch(path, main_file=main_file, **kwargs)``。
    2. ``snap = runtime.snapshot()``；断言快照可正常产出（结构完整、
       不含异常）。
    3. ``await runtime.shutdown()`` 的完整关闭路径执行完毕后返回
       ``EXIT_OK``。

    非行为：不发现/执行项目里的测试文件；不 mock LLM（项目拉起
    不要求真实 Provider 可用——懒创建保证未调用的 Provider 不产生
    网络活动）。

    边缘情况：``main`` 抛异常 → ``EXIT_RUNTIME_ERROR``；快照断言
    失败 → ``EXIT_RUNTIME_ERROR``，且仍尝试 shutdown 后再退出。

    .. rubric:: 测试案例

    - 前置：合法项目。→ 操作：``flowing test .``。→ 期望：退出码
      ``0``，且进程在有限时间内退出（验证 shutdown 路径无悬挂）。
    - 前置：``main`` 中 ``mount("@/missing.fya")``。→ 期望：退出码
      ``1``，stderr 指出失败发生在 launch 阶段。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.runtime.launch()``（时机：第 1 步）、
      ``flowing.runtime.Runtime.snapshot()``（时机：第 2 步，冒烟
      断言）、``flowing.runtime.Runtime.shutdown()``（时机：第 3
      步，完整关闭路径后返回）
    - 被调：``flowing.interfaces.cli.main``（时机：子命令 ``test`` 分发）

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
