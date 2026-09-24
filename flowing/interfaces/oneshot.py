"""``flowing.interfaces.oneshot`` —— 一次性对话子命令（``cli``）。

.. rubric:: 功能介绍

``flowing cli <path> [-f <main file>] [-t <agent-id>] [-m <model-tag>] [-v] INPUT [--key value ...]``
是一次性对话形态：拉起 Runtime、加载（或新建）根 Agent、投递一条
INPUT（一段话或一条 slash command）、打印结果后退出。进程生命周期
= 单条消息——``query()`` 天然阻塞至回合收尾，是“默认阻塞 bash”的
实现方式。

与 ``repl``（交互循环）互补：``cli`` 面向脚本化 / 管道化调用
（stdout 可被下游程序消费），``repl`` 面向人机交互。二者无别名关系。

.. rubric:: 使用示例

.. code-block:: console

    $ flowing cli . "用一句话介绍你自己。"          # 默认根 + 默认模型
    我是一个简洁的中文助手……
    $ flowing cli . -t agent-3f2a "继续"            # 指定目标 Agent（休眠自动恢复）
    $ flowing cli . -m deepseek-flash "你好"        # 本回合换模型标签
    $ flowing cli . -v "查一下今天的天气"           # 过程全量输出（思考/工具调用）
    $ flowing cli . /messages                        # INPUT 也可以是 slash command
    $ flowing cli . --workspace_root /ws "你好"      # 其余 --key value 照旧透传 main

.. rubric:: 行为要点

1. ``runtime = await launch(path, main_file=..., **kwargs)`` 拿 Runtime；
   失败 → stderr 摘要 + :data:`EXIT_RUNTIME_ERROR`。
2. **目标解析**（:func:`_resolve_agent`）：

   - ``-t <agent-id>`` 显式给出：命中活体表 / 池名录 →
     ``await runtime.get_agent(agent_id)``（激活直返、休眠现场恢复）；
     未知 id → stderr + ``EXIT_RUNTIME_ERROR``。
   - 未给出（repl 启动绑定的确定性版）：活体表恰好一个根 → 直用；
     否则池休眠根恰好一个 → 现场恢复（身份连续）；否则按项目默认根
     类型新建（池无根记录 → stderr“cannot determine agent type”+
     ``EXIT_RUNTIME_ERROR``）。
3. ``-m <model-tag>``：``agent.model_tag = model_tag`` 后投递——只改
   实例内存属性（:func:`flowing.agent.Agent.__setattr__` 拦截当场重
   解析 ``self.model``），不落盘，进程退出即消失；解析失败 → stderr +
   ``EXIT_RUNTIME_ERROR``。仅消息投递路径生效（slash INPUT 忽略）。
4. **INPUT 分流**（恰好一个裸位置参数，解析层保证非空）：

   - ``/`` 开头：按 :func:`flowing.interfaces.controls.slash_lines`
     执行并逐行打印（runtime 级命令 ``help`` / ``agents`` /
     ``snapshot`` 在解析不出 Agent 时以 ``agent=None`` 照常执行，
     agent 级命令由 ``slash_lines`` 自带提示行），退出码
     :data:`EXIT_OK`。
   - 否则：verbose 时先订阅该 Agent 的 ``on_provider_delta``
     （``"_turn"`` 主线：正文原样流式；思考 tty 灰显、非 tty 原样——
     ``-v`` 语义即全量过程输出）与 ``on_turn_append``（复用 repl 的
     :func:`flowing.interfaces.repl._summarize_message`，工具调用逐
     调用成行、工具结果与 STEER 注入打印全文），再 ``await agent.query(text)`` 等
     ``TurnResult``；回合内已流式上屏 → 收尾换行不重复最终文本，无
     流式（非流式 Provider / error 结局）→ 打印 ``result.final_text``。
     非 verbose 不订阅任何钩子，stdout 恰好是最终回复文本。
5. **退出码**：``status == "error"`` → 错误文本走 stderr +
   :data:`EXIT_RUNTIME_ERROR`；``completed`` / ``blocked`` /
   ``cancelled``（含 SIGINT 取消当轮）视为正常结局，最终文本走
   stdout + :data:`EXIT_OK`。任何路径退出前都 ``await
   runtime.shutdown()``（finally 兜底）。
6. **信号语义**：回合进行中 SIGINT → 对目标 Agent ``abort_turn()``
   （取消本次请求，命令以 ``cancelled`` 收尾退出）；空闲 SIGINT 与
   SIGTERM → 优雅关闭桥（``runtime.shutdown()``）。

.. rubric:: 边界与边缘情况

- INPUT 以外的第二个裸位置参数、未知单横线参数、``--key=value`` 等
  号形式、单独的 ``--``：一律用法错误（:data:`EXIT_USAGE_ERROR`，
  解析层在 :func:`flowing.interfaces.cli.main` 报出）；INPUT 不能以
  ``-`` 开头（封闭参数集的取舍）。
- ``-t`` / ``-m`` 缺值或后随 ``--`` 开头项 → 用法错误。
- 双横线参数没有任何保留字，且不占用任何名字：``--agent x`` 之类经
  :func:`flowing.interfaces.parse_kv_args` 无条件透传子项目 ``main``
  的 kwargs（即便叫 ``--agent-id`` / ``--verbose`` / ``--input-text``
  ）。``cmd_cli`` 的 flowing 级选项收在单字典参数 ``opts`` 里，签名
  上只对 ``main_file``（全子命令共有的 flowing 级参数）与 ``opts``
  两个名字保留，``-t`` / ``-m`` / ``-v`` / INPUT 的语义键不 shadows
  任何 main kwargs。
- slash INPUT 时不投递消息、不改模型；``/exit`` / ``/quit`` 这类
  交互壳命令在 ``slash_lines`` 层返回“shell-level”提示行。

.. seealso::

    :func:`flowing.interfaces.cli.main`
        参数解析与分发（``-t`` / ``-m`` / ``-v`` 的注册处）。
    :func:`flowing.interfaces.repl.cmd_repl`
        交互式对应形态（绑定状态机、过程显示三件套的来源）。
    :func:`flowing.interfaces.controls.slash_lines`
        slash command 的单一逻辑源。
"""

import sys

from flowing.agent import Agent, TurnResult
from flowing.message import Message
from flowing.providers import ProviderDelta
from flowing.runtime import Runtime, launch

from flowing.interfaces import (
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    EXIT_USAGE_ERROR,
    _default_agent_type,
)
from flowing.interfaces.controls import slash_lines
from flowing.interfaces.repl import _default_agent, _summarize_message


def _install_oneshot_signal_handlers(runtime: Runtime, flags: dict):
    """一发命令专用信号处理器（本地件，不属稳定契约）。

    - SIGINT：回合进行中（``flags["query_active"]`` 且已绑定目标）→
      对目标 Agent ``abort_turn()``（取消本次请求，命令以 cancelled
      收尾）；否则 → 优雅关闭桥。
    - SIGTERM：优雅关闭桥（``runtime.shutdown()``）。

    非主线程 / 不支持信号的平台为 no-op。返回安装前的 SIGINT 处理器，
    供 ``cmd_cli`` 退出时还原（测试进程内反复调用不串扰）。
    """
    import asyncio
    import signal

    def _shutdown_bridge() -> None:
        # 信号处理器跑在主线程、事件循环阻塞在 select 时 ensure_future
        # 不会唤醒循环（PEP 475 自动重试 select）→ call_soon_threadsafe
        # 经 self-pipe 显式唤醒
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(asyncio.ensure_future, runtime.shutdown())

    def _sigint_handler(signum: int, frame: object) -> None:
        target = flags.get("agent")
        if flags["query_active"] and target is not None:
            target.abort_turn()   # 取消本次请求；query() 以 cancelled 收尾
            return
        _shutdown_bridge()

    def _sigterm_handler(signum: int, frame: object) -> None:
        _shutdown_bridge()

    try:
        previous = signal.signal(signal.SIGINT, _sigint_handler)
        signal.signal(signal.SIGTERM, _sigterm_handler)
        return previous
    except (ValueError, OSError, RuntimeError):
        return None   # 非主线程等平台：no-op


async def _resolve_agent(runtime: Runtime, agent_id: str | None) -> Agent | None:
    """解析一发命令的目标 Agent（内部 API，不属稳定契约）。

    - ``agent_id`` 显式给出：命中活体表 / 池名录 → ``get_agent``
      （激活直返、休眠现场恢复）；未知 id → ``KeyError``。
    - 未给出（repl 启动绑定的确定性版）：活体表恰好一个根 → 直用；
      否则池休眠根恰好一个 → 现场恢复；否则按项目默认根类型新建；
      池无根记录 → 返回 ``None``（调用方报错）。
    """
    if agent_id is not None:
        if agent_id not in runtime._nodes and agent_id not in runtime._agent_pool:
            raise KeyError(agent_id)
        return await runtime.get_agent(agent_id)
    found = _default_agent(runtime)
    if found is not None:
        return found
    pool_roots = [
        aid for aid, meta in runtime._agent_pool.items()
        if meta.get("parent_agent_id") == runtime.node_id and aid not in runtime._nodes
    ]
    if len(pool_roots) == 1:
        return await runtime.get_agent(pool_roots[0])
    agent_type = _default_agent_type(runtime)
    if agent_type is None:
        return None
    return await runtime.create_agent(agent_type)


def _make_verbose_printers():
    """构造 verbose 过程显示三态（内部件）：返回
    ``(on_delta, on_append, flags)``。

    与 repl 的过程显示同源（纯增量：已流式上屏的内容摘要不重复），差
    异仅在思考段的非 tty 策略——repl 抑制思考以保管道答案是干净正文，
    ``cli -v`` 用户显式要求过程全量，非 tty 也输出思考原文。
    """
    flags = {"mid_line": False, "thinking_line": False, "thinking_streamed": False}
    _tty = sys.stdout.isatty()
    _gray = "\x1b[90m"
    _reset = "\x1b[0m"

    def _end_thinking() -> None:
        if flags["thinking_line"]:
            print(_reset, end="", flush=True)
            flags["thinking_line"] = False

    def _on_delta(host: Agent, delta: ProviderDelta) -> ProviderDelta:
        if delta.kind == "text" and delta.text:
            if flags["thinking_line"]:
                _end_thinking()
                print()   # 思考行结束后正文另起一行
                flags["mid_line"] = False
            print(delta.text, end="", flush=True)
            flags["mid_line"] = True
        elif delta.kind == "thinking" and delta.text:
            flags["thinking_streamed"] = True
            if flags["mid_line"]:
                print()   # 罕见交错（正文先行）：先收正文行
                flags["mid_line"] = False
            if _tty:
                if not flags["thinking_line"]:
                    print(_gray, end="", flush=True)
                    flags["thinking_line"] = True
                print(delta.text, end="", flush=True)
            else:
                # 非 tty：原样输出思考原文（-v 语义 = 全量过程）
                print(delta.text, end="", flush=True)
                flags["mid_line"] = True
        return delta

    def _on_append(host: Agent, msg: Message) -> Message:
        # 已流式上屏的思考，挂树摘要里不再重复；独占一行打印摘要
        line = _summarize_message(host, msg,
                                  omit_thinking=flags["thinking_streamed"])
        flags["thinking_streamed"] = False
        if line is not None:
            if flags["thinking_line"] or flags["mid_line"]:
                _end_thinking()
                print()
                flags["mid_line"] = False
            print(line)
        return msg

    return _on_delta, _on_append, flags


async def cmd_cli(
    path: str,
    main_file: str | None = None,
    *,
    opts: dict | None = None,
    **kwargs: str | bool,
) -> int:
    """``flowing cli <path> ... INPUT``：一次性对话（投递一条即退出）。

    语义与参数规约见本模块 docstring（:mod:`flowing.interfaces.oneshot`）。

    :param path: 子项目路径（同 :func:`flowing.interfaces.run.cmd_run`）。
    :param main_file: 替代的入口 main 文件（可选，经 CLI ``-f`` 传入）。
    :param opts: ``cli`` 子命令专属的 flowing 级选项字典，键集为
        ``"input"``（必填，一段话或一条 slash command；空/缺失按用法
        错误处理）、``"agent_id"``（可选，目标 Agent id，对应 ``-t``）、
        ``"model_tag"``（可选，本回合模型标签，对应 ``-m``；只改实例
        内存属性，不落盘）、``"verbose"``（可选，过程全量输出，对应
        ``-v``）。收进单字典而非独立参数，正是为了不让这些语义键占用
        子项目 ``main`` 的 kwargs 名——双横线 ``--agent-id`` /
        ``--verbose`` / ``--input-text`` 可自由透传。
    :param kwargs: 透传给 ``launch`` 与子项目 ``main`` 的 ``--key
        value`` 参数。
    :return: :data:`EXIT_OK`（正常结局含 blocked/cancelled）、
        :data:`EXIT_RUNTIME_ERROR`（launch / 解析 Agent / 换模型 /
        回合 error）、:data:`EXIT_USAGE_ERROR`（仅编程直调时空 input）。

    .. seealso::

        :func:`flowing.interfaces.cli.main`
            ``-t`` / ``-m`` / ``-v`` / INPUT 的解析与分发。
        :func:`_resolve_agent`
            目标 Agent 解析规则。
    """
    opts = opts or {}
    input_text: str = opts.get("input", "")
    agent_id: str | None = opts.get("agent_id")
    model_tag: str | None = opts.get("model_tag")
    verbose: bool = bool(opts.get("verbose", False))
    if not input_text.strip():
        # 编程直调兜底；CLI 路径由解析层拦截（missing INPUT）
        print("usage: flowing cli <path> [-f <main file>] [-t <agent-id>] "
              "[-m <model-tag>] [-v] INPUT [--key value ...]", file=sys.stderr)
        return EXIT_USAGE_ERROR
    try:
        runtime = await launch(path, main_file=main_file, **kwargs)
    except Exception as exc:
        print(f"launch failed: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    flags = {"query_active": False, "agent": None}   # SIGINT 处理器读
    _prev_sigint = _install_oneshot_signal_handlers(runtime, flags)
    _owner = "cli-oneshot"
    _subscribed: tuple[Agent, dict] | None = None
    try:
        text = input_text.strip()
        is_slash = text.startswith("/")
        # 目标解析：显式 -t 的失败一律致命；环境推导失败只在消息路径致命
        # （slash 的 runtime 级命令无 Agent 也要能跑）
        agent: Agent | None = None
        try:
            agent = await _resolve_agent(runtime, agent_id)
        except KeyError:
            print(f"unknown agent: {agent_id!r}; see /agents for recorded ids",
                  file=sys.stderr)
            return EXIT_RUNTIME_ERROR
        except Exception as exc:
            if agent_id is not None or not is_slash:
                print(f"failed to restore agent: {exc}", file=sys.stderr)
                return EXIT_RUNTIME_ERROR
        if agent is None and not is_slash:
            print("cannot determine agent type (no root record in the pool); "
                  "mount or create a root agent in main first", file=sys.stderr)
            return EXIT_RUNTIME_ERROR
        if agent is not None and model_tag is not None and not is_slash:
            try:
                agent.model_tag = model_tag
            except Exception as exc:
                print(f"failed to set model_tag: {exc}", file=sys.stderr)
                return EXIT_RUNTIME_ERROR
        if is_slash:
            # slash command：逐行打印 slash_lines 输出，不进消息流
            cmd, _, arg = text.partition(" ")
            for line in await slash_lines(cmd, arg.strip(), agent, runtime):
                print(line)
            return EXIT_OK
        # 消息投递路径
        flags["agent"] = agent   # SIGINT 的 abort_turn 目标
        if verbose:
            on_delta, on_append, vflags = _make_verbose_printers()
            agent.hooks.on_provider_delta["_turn"](on_delta, by=_owner)
            agent.hooks.on_turn_append(on_append, by=_owner)
            _subscribed = (agent, vflags)
        flags["query_active"] = True
        try:
            result: TurnResult = await agent.query(text)
        finally:
            flags["query_active"] = False
        if _subscribed is not None:
            _, vflags = _subscribed
            if vflags["thinking_line"] or vflags["mid_line"]:
                if vflags["thinking_line"]:
                    print("\x1b[0m", end="", flush=True)
                print()
                vflags["mid_line"] = False
        if result.status == "error":
            # 错误文本走 stderr（增量已上屏的部分留在 stdout）；退出码 1
            print(result.final_text or "turn finished with status=error",
                  file=sys.stderr)
            return EXIT_RUNTIME_ERROR
        if _subscribed is None:
            # 非 verbose：stdout 恰好是最终回复
            print(result.final_text)
        return EXIT_OK
    finally:
        # 无论哪条出口：摘订阅、还原信号处理器、优雅关闭（write-behind
        # 需要排空，不 shutdown 直接退进程丢尾部记录）
        if _subscribed is not None:
            a, _ = _subscribed
            a.hooks.on_provider_delta.remove_by_owner(_owner)
            a.hooks.on_turn_append.remove_by_owner(_owner)
        if _prev_sigint is not None:
            import signal
            signal.signal(signal.SIGINT, _prev_sigint)
        await runtime.shutdown()
