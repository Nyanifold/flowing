"""``flowing.interfaces.repl`` —— 交互式 REPL 子命令（``repl`` / 别名 ``cli``）。

封闭观察窗口原则见 ``flowing.interfaces`` 包 docstring。
"""

import sys
import time
from collections.abc import Awaitable, Callable

from flowing.agent import Agent, TurnContext, TurnResult, build_turn_result
from flowing.message import (
    Message,
    MessageKind,
    MessagePriority,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
)
from flowing.providers import ProviderDelta
from flowing.runtime import Runtime, launch

from flowing.interfaces import (
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    _default_agent_type,
    _list_agent_records,
)
from flowing.interfaces.controls import slash_lines


def _install_repl_signal_handlers(runtime: Runtime, flags: dict) -> None:
    """repl 专用信号处理器（repl 子命令本地件，不属稳定契约）。

    - SIGINT（Ctrl-C）：回合进行中 → 对当前绑定 Agent ``abort_turn()``
      （协作式取消当轮，会话存活）；提示符空闲（阻塞在 ``input()``）→
      抛 ``KeyboardInterrupt`` 中断当前输入行（读行循环捕获后换行重
      提示）。退出走 ``/exit`` 或 Ctrl-D（EOF）。
    - SIGTERM：与 run/serve/web 同口径的优雅关闭桥（发起
      ``runtime.shutdown()``）。

    非主线程 / 不支持信号的平台为 no-op。返回安装前的 SIGINT 处理器，
    供 cmd_repl 退出时还原（测试进程内反复进入 repl 不串扰）。
    """
    import asyncio
    import signal

    def _sigint_handler(signum: int, frame: object) -> None:
        target = flags.get("bound_agent")
        if flags["query_active"] and target is not None:
            target.abort_turn()   # 协作式取消当前回合，repl 与会话存活
            return
        raise KeyboardInterrupt   # 空闲：中断 input() 当前行

    def _sigterm_handler(signum: int, frame: object) -> None:
        # call_soon_threadsafe 经 self-pipe 唤醒阻塞在 select 的事件循环
        #（ensure_future 不唤醒，shutdown 会挂起——见 run/serve/web 同件）
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(asyncio.ensure_future, runtime.shutdown())

    try:
        previous = signal.signal(signal.SIGINT, _sigint_handler)
        signal.signal(signal.SIGTERM, _sigterm_handler)
        return previous
    except (ValueError, OSError, RuntimeError):
        return None   # 非主线程等平台：no-op


def _install_readline():
    """启用行编辑与补全（repl 子命令本地件，不属稳定契约）。

    ``readline`` 可用（tty 交互）时：方向键 / 行编辑 / 会话内历史由
    ``input()`` 经 readline 天然获得；另装一个顶层 slash 命令的 Tab
    补全器——仅当当前词以 ``/`` 开头时从 :data:`SLASH_COMMANDS` 补全
    （命令参数不补全）。``readline`` 不可用（非 Unix / 非 tty 管道输入）
    时 no-op。返回安装前的 completer，供 cmd_repl 退出时还原（测试进程
    内反复进出 repl 不串扰）。
    """
    try:
        import readline
    except ImportError:
        return None

    def _complete(text: str, state: int):
        matches = ([c for c in SLASH_COMMANDS if c.startswith(text)]
                   if text.startswith("/") else [])
        return matches[state] if state < len(matches) else None

    previous = readline.get_completer()
    readline.set_completer(_complete)
    readline.parse_and_bind("tab: complete")
    return previous


SLASH_COMMANDS: tuple[str, ...] = (
    "/help", "/exit", "/quit", "/agent", "/agents", "/new", "/snapshot",
    "/messages", "/model", "/context", "/status", "/tasks", "/export",
    "/rewind", "/cancel", "/pause", "/resume",
)
"""repl 内 slash-command 的封闭集合：以 ``/`` 开头的输入是控制命令，
不是发给 LLM 的消息。固定集合保证默认 repl 这个冷启动观察窗口的
行为可预测，不接受运行时注册新命令。

行为要点：

- ``/help``：列出本集合的全部命令及一句话说明。
- ``/exit``：退出 repl（触发 ``runtime.shutdown()`` 后以
  :data:`EXIT_OK` 退出进程）。
- ``/quit``：``/exit`` 的别名，行为完全相同。
- ``/snapshot``：打印当前 Runtime 只读快照（``runtime.snapshot()``
  的人类可读渲染）。
- ``/messages``：打印当前绑定 Agent 的消息级树摘要（沿
  ``current_head_id`` 上溯；未绑定时打印提示）。
- ``/agents``：列出有记录的 Agent（``agent_id``、最后一次回复前缀、
  最后修改时间；数据源与懒读规则见 :func:`cmd_repl`）。
- ``/agent <id>``：切换绑定目标（未激活 id 经
  :meth:`flowing.runtime.Runtime.get_agent` 现场恢复；未知 id 提示，
  绑定不变）。其余命令（``messages`` / ``model`` / ``context`` /
  ``status`` / ``tasks`` / ``export`` / ``rewind`` / ``cancel`` /
  ``pause`` / ``resume``）经 ``slash_lines`` 执行，语义见
  :mod:`flowing.interfaces.controls`。

未识别的 ``/xxx`` 输入：打印「未知命令，/help 查看可用命令」，
不报错、不退出、不进消息流。带参命令按第一个空格分流参数。

.. seealso:: :func:`cmd_repl`、:meth:`flowing.runtime.Runtime.snapshot`
"""

_HELP_LINES: tuple[str, ...] = (
    "/help                list all commands (this help)",
    "/exit  /quit         exit repl (graceful shutdown, then exit with code 0)",
    "/agent <agent_id>    switch the foreground binding (dormant ids are restored)",
    "/agents              list recorded Agents (including dormant records)",
    "/new [agent_type]    create a new root Agent and bind it",
    "/snapshot            print a read-only snapshot of the current Runtime",
    "/messages            print the bound Agent's message chain (current head up)",
    "/model [tag]         show or switch the bound Agent's model_tag",
    "/context [v]         show context window usage estimate (v = per-part breakdown)",
    "/status              print a status rollup of the bound Agent",
    "/tasks [cancel <id>] list background tasks / cancel one",
    "/export [format]     export the bound Agent's message chain (md default)",
    "/rewind <msg_id>     move the current head to a historical message (fork)",
    "/cancel              cooperatively stop the bound Agent",
    "/pause  /resume      pause / resume the bound Agent",
)
"""``/help`` 的全部命令一句话说明（与 :data:`SLASH_COMMANDS` 一一对应）。"""


def _fold(text: str, limit: int = 60) -> str:
    """折叠为单行并截断（过程显示摘要行共用）。"""
    folded = " ".join(text.split())
    return folded[:limit] + ("…" if len(folded) > limit else "")


def _summarize_message(host: Agent, msg: Message, *,
                       omit_thinking: bool = False) -> str | None:
    """新挂树消息的一行摘要（``on_turn_append`` 观察 handler 的渲染）。

    TOOL 消息（工具结果）、STEER 注入消息、以及含 ThinkingBlock 或
    ToolCallBlock 的 PROVIDER 消息（多轮推理与工具调用可见）返回一行
    摘要，正文默认折叠；纯文本 PROVIDER 消息已由 ``on_provider_delta``
    流式显示，不重复摘要；其余 kind 不摘要（返回 ``None``）。

    ``omit_thinking=True`` 时跳过 ``[thinking]`` 段：REPL 已把思考按
    灰显增量流式上屏，挂树摘要不再重复（纯增量原则）。
    """
    if msg.kind is MessageKind.TOOL:
        name = msg.tool_call_id or ""
        for other in host._messages.values():   # 经配对锚反查工具名
            if other.kind is MessageKind.PROVIDER:
                for b in other.content:
                    if isinstance(b, ToolCallBlock) and b.id == msg.tool_call_id:
                        name = b.name
                        break
        text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
        return f"[tool:{msg.tool_status}] {name} -> {_fold(text)}"
    if msg.priority == MessagePriority.STEER:
        text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
        return f"[steer] {_fold(text)}"
    if msg.kind is MessageKind.PROVIDER:
        parts: list[str] = []
        thinking = "".join(
            b.thinking for b in msg.content if isinstance(b, ThinkingBlock))
        if thinking and not omit_thinking:
            parts.append(f"[thinking] {_fold(thinking)}")
        calls = [b.name for b in msg.content if isinstance(b, ToolCallBlock)]
        if calls:
            parts.append(f"[tool_call] {', '.join(calls)}")
        return "; ".join(parts) if parts else None
    return None


def _print_message_chain(agent: Agent) -> None:
    """沿 ``current_head_id`` 上溯打印消息链概览（``/messages`` 的渲染）。"""
    chain: list[Message] = []
    mid = agent.current_head_id
    while mid is not None:
        msg = agent._messages.get(mid)
        if msg is None:
            break   # 孤儿链断点：到断点即终止（与 _assemble_context 同口径）
        chain.append(msg)
        mid = msg.parent_id
    if not chain:
        print("(no messages)")
        return
    for msg in reversed(chain):
        text = _fold("".join(b.text for b in msg.content if isinstance(b, TextBlock)))
        print(f"{msg.id[:12]}  {msg.kind.value:<9} {text}")


def _print_export(agent: Agent, fmt: str = "md") -> None:
    """沿 current_head 上溯打印消息链导出（/export 的渲染；md 默认）。"""
    chain: list[Message] = []
    mid = agent.current_head_id
    while mid is not None:
        msg = agent._messages.get(mid)
        if msg is None:
            break   # 孤儿链断点：到断点即终止
        chain.append(msg)
        mid = msg.parent_id
    chain.reverse()
    for m in chain:
        text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
        if fmt == "jsonl":
            import json
            from flowing.message import to_record
            print(json.dumps(to_record(m), ensure_ascii=False))
        else:
            print(f"**{m.kind.value}**: {text}")


async def cmd_repl(
    path: str,
    main_file: str | None = None,
    *,
    extra_slash_handlers: dict[str, Callable[[str, Agent | None, Runtime], Awaitable[None]]] | None = None,
    pre_prompt_hook: Callable[[Agent | None, Runtime], Awaitable[None]] | None = None,
    extra_help_text: str | None = None,
    **kwargs: str | bool,
) -> int:
    """``flowing repl <path>`` （别名 ``flowing cli``）：交互式 REPL。

    .. rubric:: 功能介绍

    在 ``run`` 之上加一个交互壳：从 stdin 读行、按 ``/`` 前缀分流
    （slash-command vs USER 消息）、打印 Agent 回复。

    repl 内部有会话内绑定状态：

    - 已绑定：非 ``/`` 输入投递给当前 Agent；提示符为
      ``(<agent_id>)>>>``。
    - 未绑定：提示符为 ``(new agent)>>>``；非 ``/`` 输入先创建新
      Agent（类型来源见行为要点第 3 步）并绑定、再投递；``/agents``
      列出有记录的 Agent，``/use <agent_id>`` 切换绑定。

    多根项目不在启动时报错退出，而是进入未绑定状态由用户现场选择；
    Workflow 根天然不参与绑定候选。新建 vs 恢复是子项目 ``main()``
    的项目级策略（``launch`` 契约），repl 只在 ``main`` 产物之上做
    会话内绑定。

    .. rubric:: 使用示例

    .. code-block:: console

        $ flowing repl . --workspace_root /ws
        (new agent)>>> /agents
        agent-3f2a…  "好的，已为你创建…"  2026-08-20 21:14
        (new agent)>>> /use agent-3f2a…
        (agent-3f2a…)>>> 继续
        [Agent 回复...]
        (agent-3f2a…)>>> /exit
        $

    .. rubric:: 行为要点

    1. ``runtime = await launch(path, main_file=main_file, **kwargs)``
       拿 Runtime 并安装信号处理器；``launch`` 抛异常时向 stderr 打印
       异常摘要，返回 :data:`EXIT_RUNTIME_ERROR`。
    2. 启动绑定：先扫已激活根 Agent，恰好一个则绑定；没有激活根时，
       池中未激活根条目恰好一个 → ``await runtime.get_agent(agent_id)``
       现场恢复并绑定（身份连续）；零个或多个 → 未绑定（有记录时
       打印 ``/agents`` 提示）。
    3. 读行循环。提示符：已绑定 ``(<agent_id>)>>>``，未绑定
       ``(new agent)>>>``。输入分两类：

       - 非 ``/`` 开头：未绑定则先创建新 Agent——``agent_type`` 取
         池中根条目里 ``created_at`` 最新者的 ``agent_type``
         （:func:`_default_agent_type`，与 serve ``POST /agents``
         缺省类型同口径）；池无根条目 → 打印「无法确定 Agent 类型」
         提示，不创建。创建经 ``runtime.create_agent(agent_type)``
         （``parent_id=None`` 缺省即根）并绑定。然后以 ``str`` 调
         :meth:`flowing.agent.Agent.query` （打包 USER 消息在其内部
         完成），等待回合结果（``TurnResult``），打印最终文本，再
         显示下一个提示符。
       - ``/`` 开头：按 :data:`SLASH_COMMANDS` 解释，不进消息流；
         带参命令按第一个空格分流参数。
    4. 过程显示（绑定期间生效，``/use`` 切换时订阅随之迁移）：订阅
       该 Agent 的 ``on_provider_delta``——流式打印生成中的文本；
       ``on_turn_append``——新挂树的 TOOL 消息与 STEER 注入消息
       打印一行摘要（正文默认折叠）；``after_turn``——非 repl 的
       ``query()`` 驱动的回合（cron / comm 等触发源）收尾后打印最终
       文本。多轮推理（ThinkingBlock）在摘要行中可见。
    5. ``/exit``、``/quit`` 或 EOF（Ctrl-D）→ ``runtime.shutdown()``
       → 返回 :data:`EXIT_OK`。

    slash-command 语义：

    - ``/agents``：列出池名录全部 Agent（含休眠记录）——每行
      ``agent_id``、最后一次回复前缀、最后修改时间。名录来自池注册
      表；最后回复前缀懒读各 Agent session 目录 ``tree.jsonl`` 尾部
      最后一条 PROVIDER 消息的文本前缀（未激活 Agent 没有内存对象，
      只能读盘；前缀不回写池元数据）。
    - ``/use <agent_id>``：切换绑定目标。id 命中激活实例或池名录
      → ``await runtime.get_agent(agent_id)`` （已激活直接返回，否则
      走恢复管线现场恢复）；未知 id → 打印提示，绑定不变。只换投递
      目标，不 destroy 原 Agent（保持激活）。
    - ``/messages``：未绑定 → 打印提示（无会话可看）。

    扩展注入点（供 ``repl-debug`` 等继承者使用；默认 ``repl`` 不传）：

    - ``extra_slash_handlers``：在已识别 slash-command 之后、未知
      命令之前被调用；handler 签名
      ``async (arg: str, agent: Agent | None, runtime: Runtime) -> None``。
    - ``pre_prompt_hook``：每次打印提示符之前调用；签名
      ``async (agent: Agent | None, runtime: Runtime) -> None``。
    - ``extra_help_text``：追加到 ``/help`` 输出末尾。
    - 这三个注入点都不得改变绑定状态或消息流。

    边界与边缘情况：

    - 空行输入跳过，不投递。
    - 行编辑与补全（``readline`` 可用时）：方向键 / 行编辑 / 会话内历史
      由 ``input()`` 天然获得；Tab 补全顶层 slash 命令（仅行首 ``/``
      开头的词，参数不补全）。
    - 未绑定时收到非 ``/`` 输入且池无根条目：打印「无法确定 Agent
      类型」提示，名录保持为空，不退出。
    - 未识别的 ``/xxx``：打印「未知命令，/help 查看可用命令」，
      继续循环。
    - 回合进行中收到 SIGINT（Ctrl-C）：repl 专用处理器对当前绑定 Agent
      ``abort_turn()``——协作式取消当轮（回合以 cancelled 收尾），repl 与
      会话存活；提示符空闲时 SIGINT 中断当前输入行（换行重提示，不退出）。
      SIGTERM 走优雅关闭桥（``runtime.shutdown()``）。
    - ``query()`` 返回 ``status="error"`` 的 ``TurnResult``：照常
      打印错误文本，repl 不因此退出。
    - ``/use`` 或启动绑定恢复失败（记录损坏）：打印错误，绑定不变，
      可再经 ``/agents`` + ``/use`` 现场选择。

    :param path: 子项目路径（同 :func:`flowing.interfaces.run.cmd_run`）。
    :param main_file: 替代的入口 main 文件（可选，经 CLI ``-m`` 传入）。
    :param extra_slash_handlers: 附加 slash-command 表（命令名 → 异步
        handler），默认 ``None`` （不启用）。
    :param pre_prompt_hook: 每次打印提示符前调用的异步钩子，默认
        ``None``。
    :param extra_help_text: 追加到 ``/help`` 输出末尾的文本，默认
        ``None``。
    :param kwargs: 透传给 ``launch`` 与子项目 ``main`` 的 ``--key
        value`` 参数。
    :return: :data:`EXIT_OK` （正常退出）或
        :data:`EXIT_RUNTIME_ERROR` （``launch`` 失败）。

    .. seealso::

        :data:`SLASH_COMMANDS`
            封闭命令集。
        :meth:`flowing.agent.Agent.query`
            打包 + 入队 + 等待回合结果，repl 的唯一投递通道。
        :meth:`flowing.runtime.Runtime.get_agent`
            已激活直接返回、未激活走恢复管线现场恢复。
    """
    # 第 1 步：同 cmd_run——launch 拿 Runtime 并安装信号处理器；
    # launch 失败按包 docstring 退出码约定走 EXIT_RUNTIME_ERROR
    try:
        runtime = await launch(path, main_file=main_file, **kwargs)
    except Exception as exc:
        print(f"launch failed: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR

    # ---- 过程显示的共享状态与观察 handler ---------------------------------
    # mid_line：屏幕上有未换行的流式输出（delta 就地追加，摘要做换行补偿——
    # 终端交错策略为简单换行打印，不做光标控制）；
    # query_active：当前回合由 repl 自己的 query 驱动（其最终文本由 query
    # 返回路径打印，after_turn handler 不重复打印）
    flags = {"mid_line": False, "query_active": False,
             "thinking_line": False, "thinking_streamed": False,
             "bound_agent": None}   # SIGINT 处理器读：回合进行中取消当轮
    _HOOK_OWNER = "repl"   # 观察 handler 的统一 owner（/use 迁移时按 owner 摘除）
    # repl 专用信号语义（不与 run/serve/web 共享优雅关闭桥）：SIGINT 回合
    # 进行中取消当轮、空闲中断当前输入行；SIGTERM 优雅关闭。退出时还原
    _prev_sigint = _install_repl_signal_handlers(runtime, flags)
    # 行编辑（方向键 / 历史）与顶层 slash 命令 Tab 补全（readline 可用时）
    _prev_completer = _install_readline()
    # 思考按「灰显增量」上屏（纯增量、不与最终/折叠摘要重复）；非 tty
    # （管道/重定向）时不回填思考，保持 stdout 答案是干净正文，思考留给
    # 折叠摘要兜底显示。
    _TTY = sys.stdout.isatty()
    _GRAY = "\x1b[90m"
    _RESET = "\x1b[0m"

    def _end_thinking() -> None:
        # 结束当前灰显思考行：只重置颜色（换行由调用方按需补）
        if flags["thinking_line"]:
            print(_RESET, end="", flush=True)
            flags["thinking_line"] = False

    def _print_line(text: str) -> None:
        if flags["thinking_line"] or flags["mid_line"]:
            _end_thinking()
            print()   # 收起进行中的思考/正文行，再独占一行
            flags["mid_line"] = False
        print(text)

    def _on_delta(host: Agent, delta: ProviderDelta) -> ProviderDelta:
        # 纯增量观察：text 灰显? 否——正文正常色；思考用灰显。已流式的
        # 内容由 _summarize_message(omit_thinking) 抑制，绝不重复。
        if delta.kind == "text" and delta.text:
            if flags["thinking_line"]:
                _end_thinking()
                print()   # 思考行结束后正文另起一行
                flags["mid_line"] = False
            print(delta.text, end="", flush=True)
            flags["mid_line"] = True
        elif delta.kind == "thinking" and delta.text:
            if not _TTY:
                return delta   # 非 tty：不回填答案，交给折叠摘要兜底
            flags["thinking_streamed"] = True
            if flags["mid_line"]:
                print()   # 罕见交错（正文先行）：先收正文行
                flags["mid_line"] = False
            if not flags["thinking_line"]:
                print(_GRAY, end="", flush=True)
                flags["thinking_line"] = True
            print(delta.text, end="", flush=True)
        return delta

    def _on_append(host: Agent, msg: Message) -> Message:
        # 已流式上屏的思考，挂树摘要里不再重复；一行后复位供下一条消息用
        line = _summarize_message(host, msg,
                                  omit_thinking=flags["thinking_streamed"])
        flags["thinking_streamed"] = False
        if line is not None:
            _print_line(line)
        return msg

    def _after_turn(host: Agent, turn: TurnContext) -> TurnContext:
        # 后台回合（cron / comm 等无 repl 等待者的触发源）收尾后打印最终
        # 文本，保证观察窗口可见；已有增量（正文/思考）上屏则不重复最终
        # 文本（纯增量）。build_turn_result 在 after_turn 之后才组装
        # TurnResult，此处复用同一聚合函数现场取文本
        if not flags["query_active"]:
            if flags["thinking_line"] or flags["mid_line"]:
                _end_thinking()
                print()
                flags["mid_line"] = False
            else:
                result = build_turn_result(turn, host)
                if result.final_text:
                    _print_line(result.final_text)
        return turn

    def _subscribe(a: Agent) -> None:
        # 绑定期间订阅三件套；delta 经 pattern 过滤只看主 Turn
        # （"_turn"），副线（side_query，by="_side"）不进 repl 主流式显示
        a.hooks.on_provider_delta["_turn"](_on_delta, by=_HOOK_OWNER)
        a.hooks.on_turn_append(_on_append, by=_HOOK_OWNER)
        a.hooks.after_turn(_after_turn, by=_HOOK_OWNER)

    def _unsubscribe(a: Agent) -> None:
        a.hooks.on_provider_delta.remove_by_owner(_HOOK_OWNER)
        a.hooks.on_turn_append.remove_by_owner(_HOOK_OWNER)
        a.hooks.after_turn.remove_by_owner(_HOOK_OWNER)

    agent: Agent | None = None

    def _bind(target: Agent) -> None:
        nonlocal agent
        if agent is target:
            return   # 重复绑定同一对象：订阅已就位，不重复注册
        if agent is not None:
            _unsubscribe(agent)   # /use 切换：订阅随之迁移
        agent = target
        flags["bound_agent"] = target   # SIGINT 处理器的取消目标
        _subscribe(target)

    # 第 2 步：启动绑定——先扫已激活实例（同步），返回 None 时回退查池
    found: Agent | None = _default_agent(runtime)
    if found is None:
        pool_roots = [
            aid for aid, meta in runtime._agent_pool.items()
            if meta.get("parent_agent_id") == runtime.node_id and aid not in runtime._nodes
        ]
        if len(pool_roots) == 1:
            # 池恰好一个未激活根：现场恢复并绑定（身份连续，node_id = 已有 agent_id）
            try:
                found = await runtime.get_agent(pool_roots[0])
            except Exception as exc:
                # 记录损坏导致启动恢复失败：与 /use 同口径——打印错误，
                # 进未绑定态（用户可 /agents + /use 现场选择其他记录）
                print(f"failed to restore agent: {exc}")
    if found is not None:
        _bind(found)
    elif any(meta.get("parent_agent_id") == runtime.node_id
             for meta in runtime._agent_pool.values()):
        # 多根不再报错退出、唯一休眠根恢复失败同样落到此处：进未绑定态并
        # 提示选择路径（记录数不定，措辞保持中性）
        print("root agent records exist: see /agents, select with /use <id>")

    # 第 3 步：读行循环；提示符 = 已绑定 (agent_id)>>> / 未绑定 (new agent)>>>
    while True:
        if pre_prompt_hook is not None:
            await pre_prompt_hook(agent, runtime)
        prompt = f"({agent.node_id})>>>" if agent is not None else "(new agent)>>>"
        try:
            line: str = input(prompt)
        except EOFError:
            # EOF（Ctrl-D）：与 /exit 同路径；补一个换行，避免后续输出
            # 粘在提示符所在行
            print()
            break
        except KeyboardInterrupt:
            # Ctrl-C（空闲时）：repl 专用 SIGINT 处理器抛出的中断——取消当前
            # 输入行，换行重提示，不退出（退出走 /exit 或 Ctrl-D）
            print()
            continue
        if not line.strip():
            continue   # 空行不投递（空 USER 消息节点无意义），继续循环
        if line.startswith("/"):
            # slash-command 按 SLASH_COMMANDS 解释，不进消息流；带参命令按第一个空格分流
            cmd, _, arg = line.partition(" ")
            arg = arg.strip()
            if cmd in ("/exit", "/quit"):
                break  # 第 4 步：shutdown 后返回 EXIT_OK
            if cmd == "/help":
                # 打印命令目录全部命令及一句话说明
                for help_line in _HELP_LINES:
                    print(help_line)
                if extra_help_text:
                    print(extra_help_text)
            elif cmd in ("/agent", "/use"):
                # /use 为 /agent 的兼容别名。切换绑定：命中激活实例或池名录
                # → get_agent（已激活直接返回，未激活走 recover 管线现场恢复）；
                # 未知 id → 打印提示，绑定不变；只换投递目标，不 destroy 原 Agent
                if not arg:
                    print("usage: /agent <agent_id>")
                elif arg in runtime._nodes or arg in runtime._agent_pool:
                    try:
                        target = await runtime.get_agent(arg)
                    except Exception as exc:
                        print(f"failed to restore agent: {exc}")
                    else:
                        if isinstance(target, Agent):
                            _bind(target)
                        else:
                            print(f"{arg!r} is not an Agent; cannot bind")
                else:
                    print(f"unknown agent: {arg!r}; see /agents for recorded ids")
            elif cmd == "/new":
                # 新建一个根会话并绑定（缺省类型 = 项目默认主 Agent）
                agent_type = arg or _default_agent_type(runtime)
                if agent_type is None:
                    print("cannot determine agent type (no root record in the pool); "
                          "mount or create a root agent in main first")
                else:
                    try:
                        target = await runtime.create_agent(agent_type)
                    except Exception as exc:
                        print(f"failed to create agent: {exc}")
                    else:
                        _bind(target)
                        print(f"bound to new agent {target.node_id}")
            elif extra_slash_handlers is not None and cmd in extra_slash_handlers:
                # 扩展注入点（repl-debug 等继承者；默认 repl 不启用）：
                # 在已识别 slash-command 之后、未知命令之前调用
                await extra_slash_handlers[cmd](arg, agent, runtime)
            else:
                # 其余命令走共享命令目录（与 serve/web 同源），逐行打印
                for line_text in await slash_lines(cmd, arg, agent, runtime):
                    print(line_text)
        else:
            if agent is None:
                # 未绑定收到消息 → 先创建新 Agent：agent_type 取池中根条目
                # created_at 最新者（共享辅助 _default_agent_type，与 serve
                # POST /agents 缺省类型同口径）；池无根条目 → 打印
                # 「无法确定 Agent 类型」，不创建
                agent_type = _default_agent_type(runtime)
                if agent_type is None:
                    print("cannot determine agent type (no root record in the pool); "
                          "mount or create a root agent in main first")
                    continue
                try:
                    _bind(await runtime.create_agent(agent_type))  # parent_id=None 缺省即根
                except Exception as exc:
                    # 创建失败（如 agent_type 已不可解析）：打印错误，保持未绑定
                    print(f"failed to create agent: {exc}")
                    continue
            # 以 str 调 query()（打包 USER 消息在其内部完成），等待回合结果
            flags["query_active"] = True
            try:
                result: TurnResult = await agent.query(line)     # 返回 TurnResult；repl 不走副线
            finally:
                flags["query_active"] = False
            if flags["thinking_line"] or flags["mid_line"]:
                # 增量已流式上屏（思考灰显 / 正文）→ 收尾换行，不再重复最终文本
                _end_thinking()
                print()
                flags["mid_line"] = False
            else:
                # 无流式（error / blocked / 空回复 / 非 tty 思考留待摘要）：
                # 照常打印最终文本；status="error" 时照常打印（可能为空串）
                print(result.final_text)
    # 还原 SIGINT 处理器与 readline completer（测试进程内反复进出 repl
    # 不串扰）后优雅关闭
    if _prev_sigint is not None:
        import signal
        signal.signal(signal.SIGINT, _prev_sigint)
    if _prev_completer is not None:
        import readline
        readline.set_completer(_prev_completer)
    await runtime.shutdown()
    return EXIT_OK

def _default_agent(runtime: Runtime) -> Agent | None:
    """在已激活实例中定位 repl 的默认绑定目标（内部 API，不属稳定契约）。

    直扫活体表，匹配条件是节点为 ``Agent`` 实例且 ``_parent_id``
    等于 ``runtime.node_id``。``isinstance`` 过滤使 Workflow 根等
    非 Agent 节点天然不参与（它们不是 repl 的投递目标）。恰好一个 →
    返回它；零个或多个 → 返回 ``None`` （多根由 :func:`cmd_repl` 进入
    未绑定状态现场选择）。只读操作，无副作用；不触发池现场恢复。

    .. seealso:: :func:`cmd_repl`、:meth:`flowing.runtime.Runtime.get_agent`
    """
    # 直扫活体表：isinstance 过滤使 Workflow 根天然不参与；
    # _parent_id 存翻译后实际值，根 = 亲节点是 Runtime（create_agent 管线不变量）
    roots = [
        n for n in runtime._nodes.values()
        if isinstance(n, Agent) and n._parent_id == runtime.node_id
    ]
    # 恰好一个 → 默认绑定；零个/多个 → None（多根不再报错，由 cmd_repl
    # 进入未绑定状态现场选择）
    return roots[0] if len(roots) == 1 else None
