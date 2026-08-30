"""``flowing.interfaces.repl`` —— 交互式 REPL 子命令（``repl`` / 别名 ``cli``）。

统一原则与封闭观察窗口原则见 ``flowing.interfaces`` 包 docstring。
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
    _install_signal_handlers,
    _list_agent_records,
)


SLASH_COMMANDS: tuple[str, ...] = (
    "/help", "/exit", "/quit", "/snapshot", "/messages", "/agents", "/use",
)
"""repl 内 slash-command 的封闭集合（功能 + 动机合并：以 ``/`` 开头的
输入是控制命令而非发给 LLM 的消息；固定集合保证默认 repl 这个
冷启动观察窗口的行为可预测，**不提供**扩展注册机制）。

行为边界：

- ``/help``      列出本集合的全部命令及一句话说明。
- ``/exit``      退出 repl（触发 ``runtime.shutdown()`` 后以
``EXIT_OK`` 退出进程）。
- ``/quit``      ``/exit`` 的别名，行为完全相同。
- ``/snapshot``  打印当前 Runtime 只读快照
（``runtime.snapshot()`` 的人类可读渲染）。
- ``/messages``  打印当前绑定 Agent 的消息级树摘要（沿
``current_head_id`` 上溯；未绑定时打印提示）。
- ``/agents``    列出有记录的 Agent（``agent_id``、最后一次回复
前缀、最后修改时间；数据源与懒读规则见 :func:`cmd_repl`）。
- ``/use <id>``  切换绑定目标（未激活的 id 经
``Runtime.get_agent`` 现场恢复；未知 id 打印提示，绑定不变）。

未识别的 ``/xxx`` 输入：打印「未知命令，/help 查看可用命令」，
**不**报错、**不**退出、**不**进消息流。带参命令（``/use``）按
第一个空格分流参数。

.. seealso:: :func:`cmd_repl`、:meth:`flowing.runtime.Runtime.snapshot`
"""

_HELP_LINES: tuple[str, ...] = (
    "/help                列出全部命令（本说明）",
    "/exit  /quit         退出 repl（优雅 shutdown 后以退出码 0 退出）",
    "/snapshot            打印当前 Runtime 只读快照",
    "/messages            打印当前绑定 Agent 的消息链概览",
    "/agents              列出有记录的 Agent（含休眠记录）",
    "/use <agent_id>      切换绑定目标（休眠 id 经 get_agent 现场恢复）",
)
"""``/help`` 的全部命令一句话说明（与 :data:`SLASH_COMMANDS` 一一对应）。"""


def _fold(text: str, limit: int = 60) -> str:
    """折叠为单行并截断（过程显示摘要行共用）。"""
    folded = " ".join(text.split())
    return folded[:limit] + ("…" if len(folded) > limit else "")


def _summarize_message(host: Agent, msg: Message) -> str | None:
    """新挂树消息的一行摘要（``after_turn_append`` 观察 handler 的渲染，X3）。

    TOOL 消息（工具结果）/ STEER 注入消息 / 含 ThinkingBlock 或
    ToolCallBlock 的 PROVIDER 消息（多轮推理与工具调用可见）返回一行
    摘要，正文默认折叠；纯文本 PROVIDER 消息已由 ``on_provider_delta``
    流式显示，不重复摘要；其余 kind 不摘要（返回 ``None``）。
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
        thinking = "".join(b.text for b in msg.content if isinstance(b, ThinkingBlock))
        if thinking:
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
        print("（无消息）")
        return
    for msg in reversed(chain):
        text = _fold("".join(b.text for b in msg.content if isinstance(b, TextBlock)))
        print(f"{msg.id[:12]}  {msg.kind.value:<9} {text}")

async def cmd_repl(
    path: str,
    main_file: str | None = None,
    *,
    extra_slash_handlers: dict[str, Callable[[str, Agent | None, Runtime], Awaitable[None]]] | None = None,
    pre_prompt_hook: Callable[[Agent | None, Runtime], Awaitable[None]] | None = None,
    extra_help_text: str | None = None,
    **kwargs: str | bool,
) -> int:
    """``flowing repl <path>``（别名 ``flowing cli``）：交互式 REPL。

    .. rubric:: 功能介绍

    ``run`` 之上加一个最薄交互壳：从 stdin 读行、按 ``/`` 前缀分流
    （slash-command vs USER 消息）、打印 Agent 回复。

    repl 内部有两个状态（S-39/S-40 裁决引入的会话内绑定模型）：

    - **已绑定**：非 ``/`` 输入投递给当前 Agent；提示符为
      ``(<agent_id>)>>>``。
    - **未绑定**：提示符为 ``(new agent)>>>``；非 ``/`` 输入先**创建
      新 Agent**（类型来源见行为规约第 3 步）并绑定、再投递；
      ``/agents`` 列出有记录的 Agent，``/use <agent_id>`` 切换绑定。

    .. rubric:: 设计动机

    repl 的定位是**冷启动观察窗口**——「刚启动项目、想快速发条消息
    看看、查个快照」的最小交互面。封闭的 slash-command 集合
    （:data:`SLASH_COMMANDS`）保证行为可预测；它不是一个可扩展的
    交互框架，真正的交互产品由用户自己实现或继承内置实现扩展。
    交互层（含自建 repl / web UI）感知后台事件的标准做法是经
    ``agent.hooks`` 挂观察 handler——见 ``flowing.hooks`` 模块
    docstring「外部观测者接入」。
    repl 与 ``run`` 共用同一条消息路径（``message()`` → 入队 →
    逻辑 Turn 消费），**不绕过消息队列**。

    **不再要求仅有一个根**（用户裁决）：多根项目不在启动时报错退出，
    而是进入未绑定状态由用户现场选择——repl 从「唯一主 Agent 形态的
    观察窗口」改为「带会话内绑定状态的观察窗口」。Workflow 根天然不
    参与绑定候选（``_default_agent`` 的 ``isinstance`` 过滤，S-40）；
    「新建 vs 恢复」的项目级策略仍归子项目 ``main()``（``launch``
    契约不变），repl 只在 main 产物之上做会话内绑定。

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

    .. rubric:: 行为规约

    期待行为：

    1. 同 :func:`cmd_run` 第 1 步：``launch(path, main_file=main_file,
       **kwargs)`` 拿 Runtime 并安装信号处理器。
    2. 启动绑定：``_default_agent(runtime)`` 扫**已激活实例**；返回
       ``None`` 时回退查池——``_agent_pool`` 中
       ``parent_agent_id == runtime.node_id`` 且未激活的条目恰好一个
       → ``await runtime.get_agent(agent_id)`` 现场恢复并绑定（身份
       连续）；零个或多个 → 未绑定（有记录时打印 ``/agents`` 提示）。
       多根不再抛错（用户裁决，取代原 ``EXIT_USAGE_ERROR`` 路径）。
    3. 读行循环。提示符：已绑定 ``(<agent_id>)>>>``，未绑定
       ``(new agent)>>>``。输入分两类：

       - 非 ``/`` 开头：**未绑定则先创建新 Agent**——``agent_type``
         取池中根条目（``parent_agent_id == runtime.node_id``）里
         ``created_at`` 最新者的 ``agent_type``（mount 进来的根其
         agent_type 即 fya 路径字符串，可再解析；项目策略产物都在
         池里，无需 main 额外声明默认类型）；池无根条目 → 打印
         「无法确定 Agent 类型」提示，不创建。创建经
         ``runtime.create_agent(agent_type)``（``parent_id=None``
         缺省即根）并绑定。然后以 ``str`` 调
         :meth:`flowing.agent.Agent.query`（打包为
         ``Message(kind=USER, ...)`` 由其内部完成，C-18），
         **等待回合结果**（``TurnResult``）后打印最终文本，再显示
         下一个提示符。
       - **过程显示（X3 裁决）**：绑定期间 REPL 订阅该 Agent 的
         ``on_provider_delta``——流式打印生成中的文本（正在生成 /
         流式显示）；``after_turn_append``——新挂树的 TOOL 消息
         （工具调用与结果）与 STEER 注入消息打印一行摘要（默认折叠
         正文，可见「发生了什么」）；``after_turn`` 收尾后打印最终
         文本。多轮推理（ThinkingBlock）同样经 ``message`` 摘要行
         可见。切换绑定（``/use``）时订阅随之迁移。
       - ``/`` 开头：按 :data:`SLASH_COMMANDS` 解释，**不进消息流**；
         带参命令按第一个空格分流参数。
    4. ``/exit``、``/quit`` 或 EOF（Ctrl-D）→ ``runtime.shutdown()``
       → 返回 ``EXIT_OK``。
    - 扩展注入点（供 ``repl-debug`` 等继承者使用，默认 ``repl`` 不启用）：
      ``extra_slash_handlers`` 在已识别 slash-command 之后、未知命令之前
      被调用（handler 签名 ``async (arg: str, agent: Agent | None,
      runtime: Runtime) -> None``）；``pre_prompt_hook`` 在每次打印提示符
      之前调用（签名 ``async (agent, runtime) -> None``）；两者都不得
      改变绑定状态或消息流。

    slash-command 语义：

    - ``/agents``：列出池名录全部 Agent——每行 ``agent_id``、最后
      一次回复前缀、最后修改时间。数据源：名录来自
      ``runtime._agent_pool``（core 命名空间写透）；最后回复前缀
      **懒读**各 Agent session 目录（``_persist_dir / agent_id``）
      ``tree.jsonl`` 尾部最后一条 ASSISTANT 消息的文本前缀（未激活
      Agent 没有内存对象，只能读盘；前缀**不回写**池元数据——每回合
      回写中央名录是跨对象写放大，否决）；最后修改时间取 session
      目录内文件的 mtime。
    - ``/use <agent_id>``：切换绑定目标。id 命中激活实例或池名录
      → ``await runtime.get_agent(agent_id)``（已激活直接返回，
      否则走 recover 管线现场恢复）；未知 id → 打印提示，绑定不变。
      只换投递目标，**不 destroy 原 Agent**（保持激活）。
    - ``/messages``：未绑定 → 打印提示（无会话可看）。

    非行为：

    - slash-command 不接受扩展注册（封闭集）。
    - 不把用户输入写入任何持久化通道之外的地方；一条输入就是一个
      USER 消息节点，挂在消息级树上。
    - 不做多行输入、历史补全等交互增强（那是用户自建 repl 的自由）。

    边缘情况：

    - 未绑定收到非 ``/`` 输入且池无根条目：打印「无法确定 Agent
      类型」提示，名录保持为空，不退出。
    - 未识别的 ``/xxx``：打印「未知命令，/help 查看可用命令」，
      继续循环。
    - 回合进行中收到 SIGINT：走统一信号路径（shutdown 是协作式
      的，进行中的逻辑 Turn 随 destroy 取消）。
    - ``message()`` 返回 ``status="error"`` 的 ``TurnResult``：照常
      打印错误文本，repl 不因此退出。
    - ``/use`` 的 id 在名录中但 session 目录损坏：``get_agent`` /
      recover 管线的原生异常上抛，repl 打印错误，绑定不变。
    - 启动绑定的池回退恢复失败（记录损坏）：同 ``/use`` 口径——打印
      错误并进未绑定态（可再经 ``/agents`` + ``/use`` 现场选择）。
    - ``launch`` 抛异常：同 :func:`cmd_run`——异常摘要打印到 stderr，
      返回 ``EXIT_RUNTIME_ERROR``（包 docstring 退出码约定）。
    - 空行输入：跳过不投递（空 USER 消息节点无意义），继续循环。

    .. rubric:: 测试案例

    - 前置：合法项目、池恰好一个根 Agent。→ 操作：启动 repl，输入
      ``"你好"`` 后 ``/exit``。→ 期望：启动即绑定（提示符含
      agent_id），恰好产生一个 USER 消息节点与一个逻辑 Turn；退出码
      ``EXIT_OK``。
    - 前置：池有两个根 Agent。→ 操作：启动 repl。→ 期望：进入未
      绑定状态（提示符 ``(new agent)>>>``）并打印 ``/agents`` 提示；
      进程不退出。
    - 前置：全新项目（main 未 mount、池空）。→ 操作：输入
      ``"你好"``。→ 期望：打印「无法确定 Agent 类型」提示，名录
      保持为空。
    - 前置：池有一个根、重启进程（盘上记录完好）。→ 操作：启动
      repl。→ 期望：启动绑定阶段经 ``get_agent`` 现场恢复同一
      ``agent_id``（身份连续），提示符显示该 id。
    - 前置：任意项目。→ 操作：输入 ``/frobnicate``。→ 期望：
      打印未知命令提示，进程仍在 repl 循环中。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.runtime.launch()`` 与
      ``flowing.interfaces._install_signal_handlers()``（时机：第 1 步，
      同 ``cmd_run``）、``flowing.interfaces.repl._default_agent()``（时机：
      启动绑定第一步，扫激活实例）、
      ``flowing.runtime.Runtime.get_agent()``（时机：启动绑定的池
      回退恢复，以及 ``/use`` 命中未激活 id）、
      ``flowing.runtime.Runtime.create_agent()``（时机：未绑定收到
      非 ``/`` 输入且池有根条目）、``flowing.agent.Agent.query()``
      （时机：每条非 ``/`` 输入，等待回合结果）、
      ``flowing.runtime.Runtime.snapshot()``（时机：``/snapshot``
      命令）、``flowing.runtime.Runtime.shutdown()``（时机：
      ``/exit`` / ``/quit`` / EOF）
    - 被调：``flowing.interfaces.cli.main``（时机：子命令 ``repl`` / ``cli``
      分发，二者同一代码路径）

    .. seealso::

        :data:`SLASH_COMMANDS`
            封闭命令集。
        :meth:`flowing.agent.Agent.query`
            打包 + 入队 + 等待回合结果，repl 的唯一投递通道。
        :func:`_default_agent`
            已激活实例中的默认绑定定位（内部 API）。
        :meth:`flowing.runtime.Runtime.get_agent`
            已激活直接返回、未激活走 recover 管线现场恢复。
    """
    # 第 1 步：同 cmd_run——launch 拿 Runtime 并安装信号处理器；
    # launch 失败按包 docstring 退出码约定走 EXIT_RUNTIME_ERROR
    try:
        runtime = await launch(path, main_file=main_file, **kwargs)
    except Exception as exc:
        print(f"launch 阶段失败：{exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    _install_signal_handlers(runtime)

    # ---- 过程显示（X3）的共享状态与观察 handler -------------------------
    # mid_line：屏幕上有未换行的流式输出（delta 就地追加，摘要做换行补偿——
    # 终端交错策略为简单换行打印，不做光标控制）；
    # query_active：当前回合由 repl 自己的 query 驱动（其最终文本由 query
    # 返回路径打印，after_turn handler 不重复打印）
    flags = {"mid_line": False, "query_active": False}
    _HOOK_OWNER = "repl"   # 观察 handler 的统一 owner（/use 迁移时按 owner 摘除）

    def _print_line(text: str) -> None:
        if flags["mid_line"]:
            print()   # 流式行未收尾：先换行再独占一行
            flags["mid_line"] = False
        print(text)

    def _on_delta(host: Agent, delta: ProviderDelta) -> ProviderDelta:
        # 流式打印生成中的文本（纯观察；携带 value 的钩子点要求返回 value）
        if delta.kind == "text" and delta.text:
            print(delta.text, end="", flush=True)
            flags["mid_line"] = True
        return delta

    def _on_append(host: Agent, msg: Message) -> Message:
        line = _summarize_message(host, msg)
        if line is not None:
            _print_line(line)
        return msg

    def _after_turn(host: Agent, turn: TurnContext) -> TurnContext:
        # 后台回合（cron / comm 等无 repl 等待者的触发源）收尾后打印最终
        # 文本，保证观察窗口可见；repl 自己 query 驱动的回合由 query 返回
        # 路径打印，不重复。build_turn_result 在 after_turn 之后才组装
        # TurnResult，此处复用同一聚合函数现场取文本
        if not flags["query_active"]:
            result = build_turn_result(turn, host)
            if result.final_text:
                _print_line(result.final_text)
        return turn

    def _subscribe(a: Agent) -> None:
        # 绑定期间订阅三件套（X3）；delta 经 pattern 过滤只看主 Turn
        # （"_turn"），副线（side_query，by="_side"）不进 repl 主流式显示
        a.hooks.on_provider_delta["_turn"](_on_delta, by=_HOOK_OWNER)
        a.hooks.after_turn_append(_on_append, by=_HOOK_OWNER)
        a.hooks.after_turn(_after_turn, by=_HOOK_OWNER)

    def _unsubscribe(a: Agent) -> None:
        a.hooks.on_provider_delta.remove_by_owner(_HOOK_OWNER)
        a.hooks.after_turn_append.remove_by_owner(_HOOK_OWNER)
        a.hooks.after_turn.remove_by_owner(_HOOK_OWNER)

    agent: Agent | None = None

    def _bind(target: Agent) -> None:
        nonlocal agent
        if agent is target:
            return   # 重复绑定同一对象：订阅已就位，不重复注册
        if agent is not None:
            _unsubscribe(agent)   # /use 切换：订阅随之迁移（X3）
        agent = target
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
                print(f"恢复 Agent 失败：{exc}")
    if found is not None:
        _bind(found)
    elif any(meta.get("parent_agent_id") == runtime.node_id
             for meta in runtime._agent_pool.values()):
        # 多根（激活或休眠）不再报错退出：进未绑定态并提示选择路径
        print("存在多个根 Agent 记录：/agents 查看，/use <id> 选择")

    # 第 3 步：读行循环；提示符 = 已绑定 (agent_id)>>> / 未绑定 (new agent)>>>
    while True:
        if pre_prompt_hook is not None:
            await pre_prompt_hook(agent, runtime)
        prompt = f"({agent.node_id})>>>" if agent is not None else "(new agent)>>>"
        try:
            line: str = input(prompt)
        except EOFError:
            # EOF（Ctrl-D）：与 /exit 同路径
            break
        if not line.strip():
            continue   # 空行不投递（空 USER 消息节点无意义），继续循环
        if line.startswith("/"):
            # slash-command 按 SLASH_COMMANDS 解释，不进消息流；带参命令按第一个空格分流
            cmd, _, arg = line.partition(" ")
            arg = arg.strip()
            if cmd in ("/exit", "/quit"):
                break  # 第 4 步：shutdown 后返回 EXIT_OK
            if cmd == "/help":
                # 打印 SLASH_COMMANDS 全部命令及一句话说明
                for help_line in _HELP_LINES:
                    print(help_line)
                if extra_help_text:
                    print(extra_help_text)
            elif cmd == "/agents":
                # 列池名录：每行 agent_id + 最后回复前缀 + 最后修改时间。
                # 数据源与懒读规则由共享辅助 _list_agent_records 承载
                # （与 serve GET /agents 同口径；前缀懒读盘、不回写池元数据）
                records = _list_agent_records(runtime)
                if not records:
                    print("（无 Agent 记录）")
                for rec in records:
                    mtime_str = (
                        time.strftime("%Y-%m-%d %H:%M", time.localtime(rec["mtime"]))
                        if rec["mtime"] is not None else "-"
                    )
                    print(f'{rec["agent_id"]}  "{rec["last_reply"]}"  {mtime_str}')
            elif cmd == "/use":
                # 切换绑定：命中激活实例或池名录 → get_agent（已激活直接返回，
                # 未激活走 recover 管线现场恢复）；未知 id → 打印提示，绑定
                # 不变；只换投递目标，不 destroy 原 Agent（保持激活）
                if not arg:
                    print("用法：/use <agent_id>")
                elif arg in runtime._nodes or arg in runtime._agent_pool:
                    try:
                        target = await runtime.get_agent(arg)
                    except Exception as exc:
                        # 名录中但 session 目录损坏：recover 管线原生异常
                        # 上抛，打印错误，绑定不变，不退出
                        print(f"恢复 Agent 失败：{exc}")
                    else:
                        if isinstance(target, Agent):
                            _bind(target)
                        else:
                            print(f"{arg!r} 不是 Agent，无法绑定")
                else:
                    print(f"未知 Agent：{arg!r}，/agents 查看有记录的 id")
            elif cmd == "/snapshot":
                print(runtime.snapshot())  # 人类可读渲染
            elif cmd == "/messages":
                # 未绑定：打印提示（无会话可看）；已绑定：沿
                # agent.current_head_id 上溯的消息链概览
                if agent is None:
                    print("未绑定 Agent：无会话可看（/agents 查看，/use <id> 选择）")
                else:
                    _print_message_chain(agent)
            elif extra_slash_handlers is not None and cmd in extra_slash_handlers:
                # 扩展注入点（repl-debug 等继承者；默认 repl 不启用）：
                # 在已识别 slash-command 之后、未知命令之前调用
                await extra_slash_handlers[cmd](arg, agent, runtime)
            else:
                # 未识别 /xxx：打印提示，不进消息流、不退出
                print(f"未知命令：{cmd}，/help 查看可用命令")
        else:
            if agent is None:
                # 未绑定收到消息 → 先创建新 Agent：agent_type 取池中根条目
                # created_at 最新者（mount 根的 agent_type 即 fya 路径字符串，
                # 可再解析）；池无根条目 → 打印「无法确定 Agent 类型」，不创建
                roots = [
                    meta for meta in runtime._agent_pool.values()
                    if meta.get("parent_agent_id") == runtime.node_id
                ]
                if not roots:
                    print("无法确定 Agent 类型（池无根记录），请先在 main 中 mount / 创建根 Agent")
                    continue
                latest = max(roots, key=lambda m: m.get("created_at") or "")
                try:
                    _bind(await runtime.create_agent(latest["agent_type"]))  # parent_id=None 缺省即根
                except Exception as exc:
                    # 创建失败（如 agent_type 已不可解析）：打印错误，保持未绑定
                    print(f"创建 Agent 失败：{exc}")
                    continue
            # 以 str 调 query()（打包 USER 消息在其内部完成），等待回合结果
            flags["query_active"] = True
            try:
                result: TurnResult = await agent.query(line)     # 返回 TurnResult；repl 不走副线
            finally:
                flags["query_active"] = False
            if flags["mid_line"]:
                # 流式输出已在屏：换行收尾，不再重复打印最终文本
                print()
                flags["mid_line"] = False
            else:
                # 无流式文本（error / blocked / 空回复）：照常打印最终文本，
                # status="error" 时照常打印（可能为空串），不因此退出
                print(result.final_text)
    await runtime.shutdown()
    return EXIT_OK

def _default_agent(runtime: Runtime) -> Agent | None:
    """在**已激活实例**中定位 repl 的默认绑定目标（内部 API，不属稳定契约）。

    .. rubric:: 功能介绍与动机

    repl 启动绑定的第一步：直扫 ``runtime._nodes``，找父指针指向
    Runtime 的**已激活**根 Agent。同步直读活体表——不再绕
    ``snapshot()`` + ``get_node()``（S-39 裁决：快照是为一致性切片
    设计的观测通道，启动绑定只需要活体表的一次性遍历；``get_node``
    返回 ``ProvideNode`` 需强转，直扫 ``_nodes`` 配合 ``isinstance``
    过滤一步到位，也无需新增公开 API）。

    .. rubric:: 行为规约

    - 匹配条件：``isinstance(n, Agent) and n._parent_id ==
      runtime.node_id``。``isinstance`` 过滤使 Workflow 根**天然不
      参与**（S-40 裁决：Workflow 无消息队列、无工作循环，从不是 repl
      投递目标的候选——不是「检测到再报错」，而是「不计入」）。
      ``_parent_id`` 存的是翻译后实际值（根 = 父是 Runtime，
      ``create_agent`` 管线不变量），Runtime 自身无 ``_parent_id``
      字段，不会被误收。
    - 恰好一个 → 返回它；零个或多个 → 返回 ``None``。多根不再抛
      ``ValueError``（「不再要求仅有一个根」用户裁决）：多根由
      :func:`cmd_repl` 进入未绑定状态，用户经 ``/agents`` +
      ``/use`` 现场选择。
    - 只读操作，无副作用；**不触发池现场恢复**（只对已激活实例
      生效——投递目标必须是活着的工作循环；池回退恢复是
      :func:`cmd_repl` 启动段的协程逻辑）。

    .. rubric:: 调用关系（审计）

    - 调用：无（直读 ``runtime._nodes`` / ``runtime.node_id``）
    - 被调：``flowing.interfaces.repl.cmd_repl``（时机：启动绑定第一步，进入
      读行循环前）

    .. seealso:: :func:`cmd_repl`、:meth:`flowing.runtime.Runtime.get_agent`
    """
    # 直扫活体表：isinstance 过滤使 Workflow 根天然不参与（S-40）；
    # _parent_id 存翻译后实际值，根 = 父是 Runtime（create_agent 管线不变量）
    roots = [
        n for n in runtime._nodes.values()
        if isinstance(n, Agent) and n._parent_id == runtime.node_id
    ]
    # 恰好一个 → 默认绑定；零个/多个 → None（多根不再报错，由 cmd_repl
    # 进入未绑定状态现场选择）
    return roots[0] if len(roots) == 1 else None
