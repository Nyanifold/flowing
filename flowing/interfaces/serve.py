"""``flowing.interfaces.serve`` —— HTTP API 服务子命令（``serve``，纯 API 无前端）。

统一原则与封闭观察窗口原则见 ``flowing.interfaces`` 包 docstring。
"""

from flowing.runtime import launch

from flowing.interfaces import EXIT_OK, _install_signal_handlers


SERVE_ENDPOINTS: tuple[str, ...] = (
    "POST /agents/<agent-id>/message",
    "GET /agents",
    "POST /agents",
    "GET /agents/<agent-id>/messages",
    "GET /agents/<agent-id>/stream",
    "POST /agents/<agent-id>/cancel",
    "GET /snapshot",
    "GET /healthz",
)
"""默认 serve 暴露的 HTTP 端点封闭集（功能 + 动机合并：serve 是纯 API
的冷启动观察窗口，固定端点集保证行为可预测；**不接受**开放注册
任意 endpoint）。

风格约定（P3-15 裁决）：REST 集合资源式，一律**复数** ``/agents``——
集合（``GET /agents`` 列出 / ``POST /agents`` 创建，id 由服务端分配
故创建走 POST 集合）+ 成员（``/agents/<id>/...`` 子资源动作）。

行为边界：

- ``POST /agents/<agent-id>/message``  向指定 Agent 投递一条 USER
  消息并等待回合结果（见 :func:`cmd_serve` 的端点契约）。
- ``GET /agents``  列出池名录全部 Agent（含休眠记录；每项
  ``agent_id`` + 最后一次回复前缀 + 最后修改时间；数据源与懒读
  规则同 REPL ``/agents``）。
- ``POST /agents``  创建根 Agent（body ``{"agent_type"?: str,
  "args"?: dict}``，缺省类型 = 项目默认主 Agent，同 REPL 未绑定态
  发消息的隐式创建）。
- ``GET /agents/<agent-id>/messages``  当前 head 上溯链的消息视图
  （渲染对话用；休眠 id 经 ``get_agent`` 现场恢复后读）。
- ``POST /agents/<agent-id>/cancel``  协作式取消当前回合
  （``agent.cancel()``）；无活跃回合幂等。
- ``GET /snapshot``  返回 ``runtime.snapshot()`` 的 JSON 序列化，
  只读、无副作用。
- ``GET /healthz``   健康检查，返回 ``{"status": "ok"}``；只表示
  HTTP 进程与 Runtime 存活，不做深度检查。

**根选取无状态**（P3-15 裁决）：API 层**没有**「默认主 Agent」或
「当前选中」概念——一切目标选择由客户端基于 ``GET /agents`` /
``GET /snapshot`` 自行完成，「切换 Agent」是纯客户端动作（改自己
请求里的 id），无服务端对应端点。

集合之外的任何路径返回 ``404``。

.. seealso:: :func:`cmd_serve`、:data:`WEB_EXTRA_ENDPOINTS`
"""

async def cmd_serve(
    path: str,
    main_file: str | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    **kwargs: str | bool,
) -> int:
    """``flowing serve <path>``：把 Runtime 挂到 HTTP 服务（纯 API）。

    .. rubric:: 功能介绍

    拉起 Runtime 后启动 HTTP 服务，暴露 :data:`SERVE_ENDPOINTS`
    定义的**封闭固定端点集**（七条，REST 复数风格：消息投递 /
    列举 / 创建 / 消息视图 / 取消 / 快照 / 健康检查）。无前端，
    供程序调用。

    ``-a <host>`` / ``-p <port>`` 是本命令专属的 CLI 参数（在 CLI 层
    剥离为 ``host`` / ``port``，**不进入** ``main`` 的 kwargs；
    两者严格分立，不合并 ``host:port`` 形式）；源文档未规定监听地址
    的缺省，本规约定稿为 ``127.0.0.1:8000``。

    .. rubric:: 设计动机

    HTTP 层只是消息入口之一：请求 → ``enqueue_message()`` / ``message()``
    → 逻辑 Turn 循环消费。HTTP 层**不直接触碰 Agent 内部**，与 CLI
    stdin、Cron 定时器、通信扩展的 peer message 完全等价。端点集
    封闭是因为默认 serve 是冷启动观察窗口，不是开放 API 面——需要
    自定义端点时继承内置实现或自写 server（全部走公开 API）。
    服务进程内感知后台事件（其它 Agent 的回合、钩子触发等）经
    ``agent.hooks`` 挂观察 handler 实现——端点集本身不含推送通道，
    见 ``flowing.hooks`` 模块 docstring「外部观测者接入」。

    .. rubric:: 使用示例

    .. code-block:: console

        $ flowing serve . -p 9000

    .. code-block:: console

        $ curl -X POST localhost:9000/agents -d '{}'
        {"agent_id": "agent-..."}
        $ curl localhost:9000/agents
        [{"agent_id": "agent-...", "last_reply": "...", "modified_at": "..."}]
        $ curl -X POST localhost:9000/agents/agent-.../message -d '{"text": "你好"}'
        {"message_id": "msg-...", "final_text": "..."}
        $ curl localhost:9000/agents/agent-.../messages
        [{"id": "msg-...", "kind": "user", ...}, ...]
        $ curl -X POST localhost:9000/agents/agent-.../cancel
        {"status": "cancelled"}
        $ curl localhost:9000/snapshot
        {...}
        $ curl localhost:9000/healthz
        {"status": "ok"}

    .. rubric:: 行为规约

    期待行为（严格时序与端点契约）：

    1. ``runtime = await launch(path, main_file=main_file, **kwargs)``，
       安装信号处理器。
    2. 绑定 ``host:port`` 并开始服务；随后 ``await runtime`` 直到
       shutdown（HTTP server 的生命周期挂在 Runtime 上）。
    3. ``POST /agents/<agent-id>/message``：请求体 ``{"text": str}`` →
       以 ``str`` 调用 ``message()`` 投递给 **agent-id 显式指定的
       Agent**（打包 ``Message(kind=USER, ...)`` 由 ``message()`` 内部
       完成，C-18）并**等待回合结果** → ``200 {"message_id": str,
       "final_text": str}``。休眠 / 已销毁但有记录的 id 经
       ``get_agent`` 现场恢复后投递。API 层没有「默认主 Agent」概念——
       「主 Agent」只是子项目的设计概念，HTTP 端点上目标必须显式。
    4. ``GET /agents``：→ ``200``，body 为池名录全部 Agent 的列表
       （含休眠记录；每项 ``{"agent_id", "last_reply", "modified_at"}``；
       数据源 = core 名录 + session 懒读，口径同 REPL ``/agents``）。
    5. ``POST /agents``：请求体 ``{"agent_type"?: str, "args"?: dict}``
       → ``runtime.create_agent()``（缺省类型 = 项目默认主 Agent，
       同 REPL 未绑定态隐式创建）→ ``201 {"agent_id": str}``。
       创建失败（类型未知 / setup 抛异常）不残留半注册实例。
    6. ``GET /agents/<agent-id>/messages``：→ ``200``，body 为当前
       head 上溯链的消息列表（渲染对话用；休眠 id 同样经
       ``get_agent`` 恢复）。
    7b. ``GET /agents/<agent-id>/stream``（SSE 长连接，X2 裁决）：
       订阅指定 Agent 的钩子并逐事件推送：``on_provider_delta`` →
       ``event: delta``（流式生成显示，data 为 delta 文本块）；
       ``after_turn_append`` → ``event: message``（新消息挂树——
       工具调用与结果、steer 注入等对前端可见，data 为消息 JSON）；
       ``after_turn`` → ``event: turn_end``（回合收尾，data 含
       ``TurnResult.status``）。连接断开 / Agent destroy → 订阅清理。
       一个 POST /message 的等待期与 SSE 流并存：「正在生成（delta
       流）→ 完整生成（POST 响应 / turn_end）」两段式展示由此成立。
    7. ``POST /agents/<agent-id>/cancel``：→ ``agent.cancel()``
       协作式取消当前回合 → ``200 {"status": "cancelled"}``；
       无活跃回合时幂等 ``200 {"status": "idle"}``。
    8. ``GET /snapshot``：→ ``200``，body 为
       ``runtime.snapshot()`` 的 JSON 序列化（只读视图，不含控制
       信号与可变内部对象）。
    9. ``GET /healthz``：→ ``200 {"status": "ok"}``。
    10. shutdown 路径同 :func:`cmd_run`。

    非行为：

    - 不在 HTTP handler 里直接 ``agent.query(...)`` 或直接改消息级
      树——绕过消息队列是被明令禁止的反模式。
    - 不开放注册任意 endpoint。
    - 流式推送经 SSE 端点提供（``GET /agents/<id>/stream``，见时序
      第 7b 步）——实现为对钩子点的订阅转发，不改变任何核心语义；
      WebSocket 不引入（SSE 单向推送已覆盖「正在生成 / 流式显示 /
      完整生成」的展示需求）。（X2 裁决：初版提供流式。）

    边缘情况：

    - ``<agent-id>`` 在名录中不存在（纯 ghost）→ ``404``，body
      ``{"error": "unknown agent"}``（对 message / messages / cancel
      三个带 id 端点同律；休眠但有记录的 id **不**走 404——
      ``get_agent`` 现场恢复）。
    - 请求体缺 ``text`` 字段或 ``text`` 非字符串 → ``400``。
    - ``POST /agents`` 的 ``agent_type`` 未注册 → ``400``；setup
      抛异常 → ``500``（实例不残留，池无半注册）。
    - 端点集之外的路径 → ``404``。
    - 端口绑定失败 → 打印错误到 stderr，返回 ``EXIT_RUNTIME_ERROR``
      （Runtime 已 launch 成功的也要先 shutdown 再退出）。
    - 回合进行中服务收到 shutdown：等待中的 ``POST`` 连接
      随 destroy 被取消，不承诺返回结果。

    .. rubric:: 测试案例

    - 前置：合法项目。→ 操作：``POST /agents``（空 body）。→ 期望：
      ``201`` 含 ``agent_id``；随后 ``GET /agents`` 列表含该 id；
      再 ``POST /agents/<新id>/message`` 正常投递。
    - 前置：合法项目（含 id 为 ``root`` 的 Agent）。→ 操作：
      ``POST /agents/root/message``。→ 期望：响应含 ``message_id``，
      且该 id 可在 ``GET /agents/root/messages`` 中找到对应消息
      节点。
    - 前置：合法项目。→ 操作：``POST /agents/ghost/message``（id 不
      存在）。→ 期望：``404 {"error": "unknown agent"}``；随后
      ``GET /healthz`` 仍 ``200``。
    - 前置：合法项目（Agent 回合进行中）。→ 操作：``GET
      /agents/<id>/stream`` 建立 SSE。→ 期望：delta 事件随生成推送；
      回合收尾收到 ``turn_end``；断开连接后钩子订阅被移除。
    - 前置：合法项目。→ 操作：``GET /unknown``。→ 期望：``404``。

    .. rubric:: 调用关系（审计）

    - 调用：``flowing.agent.Agent.hooks`` 的 ``on_provider_delta`` /
      ``after_turn_append`` / ``after_turn`` 订阅（时机：stream 端点
      每个 SSE 连接建立时注册、断开时移除）；
      ``flowing.runtime.launch()`` 与
      ``flowing.interfaces._install_signal_handlers()``（时机：第 1 步）、
      ``flowing.runtime.Runtime.__await__``（时机：第 2 步，阻塞至
      shutdown）、``flowing.agent.Agent.query()``（时机：
      ``POST /agents/<agent-id>/message`` 每次请求，等待回合结果）、
      ``flowing.runtime.Runtime.create_agent()``（时机：
      ``POST /agents`` 每次请求）、``flowing.runtime.Runtime.get_agent()``
      （时机：带 id 端点命中休眠记录时现场恢复）、
      ``flowing.agent.Agent.cancel()``（时机：
      ``POST /agents/<agent-id>/cancel`` 每次请求）、
      ``flowing.runtime.Runtime.snapshot()``（时机：``GET /snapshot``
      每次请求）
    - 被调：``flowing.interfaces.cli.main``（时机：子命令 ``serve`` 分发）；
      ``flowing.interfaces.web.cmd_web`` 共享其 HTTP 骨架（复用形式：未见规约）

    .. seealso::

        :data:`SERVE_ENDPOINTS`、:func:`cmd_web`
        :meth:`flowing.agent.Agent.enqueue_message`、
        :meth:`flowing.agent.Agent.query`
        :meth:`flowing.runtime.Runtime.snapshot`
    """
    # 第 1 步
    runtime = await launch(path, main_file=main_file, **kwargs)
    _install_signal_handlers(runtime)
    # 第 2 步：绑定 host:port 并开始服务（HTTP 服务器实现与端点注册 API
    # 未见具名符号，见 facts/cli.md v1 [存疑]；绑定失败 → stderr +
    # 先 shutdown 再以 EXIT_RUNTIME_ERROR 退出）
    #
    # 第 3 步：POST /agents/<agent-id>/message 每次请求——
    #   # 请求体缺 text 或 text 非 str → 400
    #   text: str = "..."                        # 请求体 {"text": str}
    #   try:
    #       agent = await runtime.get_agent(agent_id)   # 休眠记录现场恢复
    #   except <agent 不存在>:
    #       ...  # 404 {"error": "unknown agent"}
    #   result: TurnResult = await agent.query(text)      # 等待回合结果
    #   # 200 {"message_id": ..., "final_text": result.final_text}
    #
    # 第 3b 步：GET /agents——core 名录 + session 懒读（口径同 REPL /agents）
    # 第 3c 步：POST /agents——runtime.create_agent(agent_type or 默认主 Agent,
    #   **args)；类型未知 → 400；setup 失败 → 500 且无半注册；成功 → 201
    # 第 3d 步：GET /agents/<id>/messages——get_agent 恢复后读当前 head 上溯链
    # 第 3e 步：POST /agents/<id>/cancel——agent.cancel()；无活跃回合幂等 200
    #
    # 第 4 步：GET /snapshot 每次请求——
    #   # 200，body 为 runtime.snapshot() 的 JSON 序列化（只读）
    #
    # 第 5 步：GET /healthz → 200 {"status": "ok"}
    # 端点集之外的路径 → 404
    await runtime  # 阻塞至 shutdown（HTTP server 生命周期挂在 Runtime 上）
    return EXIT_OK
