"""``flowing.interfaces.serve`` —— HTTP API 服务子命令（``serve``，纯 API 无前端）。

统一原则与封闭观察窗口原则见 ``flowing.interfaces`` 包 docstring。
"""

import asyncio
import dataclasses
import json
import sys
from datetime import datetime
from uuid import uuid4

from aiohttp import web

from flowing.agent import Agent, TurnContext, TurnResult, build_turn_result
from flowing.message import Message, to_record
from flowing.providers import ProviderDelta
from flowing.runtime import Runtime, launch

from flowing.interfaces import (
    EXIT_OK,
    EXIT_RUNTIME_ERROR,
    _default_agent_type,
    _install_signal_handlers,
    _list_agent_records,
)


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


_SERVE_HOOK_POINTS = ("on_provider_delta", "after_turn_append", "after_turn", "after_destroy")
"""SSE 连接订阅的钩子点集合（断开 / destroy 时按 owner 整组摘除）。"""

_SSE_HEARTBEAT_INTERVAL = 1.0
"""SSE 心跳间隔（秒）：无事件时写注释行保活并检测客户端断线。"""


def _json(data: object, status: int = 200) -> web.Response:
    """JSON 响应的统一出口（中文不转义；datetime 等非原生类型经 str 兜底）。"""
    return web.Response(
        text=json.dumps(data, ensure_ascii=False, default=str),
        status=status, content_type="application/json",
    )


def _error(status: int, message: str) -> web.Response:
    """错误响应的统一出口（body 一律 ``{"error": ...}``）。"""
    return _json({"error": message}, status=status)


@web.middleware
async def _error_middleware(request: web.Request, handler) -> web.Response:
    """未捕获异常一律映射 ``500 {"error": ...}``（含 ``before_cancel``
    的 ``Intercepted`` 等直挂 ``Exception`` 的信号类），不散落 HTML 错误页。"""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        return _error(500, f"{type(exc).__name__}: {exc}")


async def _resolve_agent(runtime: Runtime, agent_id: str) -> Agent | web.Response:
    """带 id 端点的统一目标解析：纯 ghost → ``404``；休眠记录经
    ``get_agent`` 现场恢复，recover 管线抛异常 → ``500``（含错误摘要，
    与 ``POST /agents`` 的 setup 失败同口径）；``_nodes`` 中的非 Agent
    节点（Workflow 等）按 ghost 同律 ``404``。

    成功返回 :class:`Agent` 实例；失败返回就绪的错误 :class:`web.Response`
    （调用方 ``isinstance`` 判别后直接返回）。
    """
    if agent_id not in runtime._nodes and agent_id not in runtime._agent_pool:
        return _error(404, "unknown agent")   # 纯 ghost（对 message/messages/cancel/stream 同律）
    try:
        node = await runtime.get_agent(agent_id)   # 休眠记录现场恢复
    except Exception as exc:
        # 有记录但 recover 抛异常（session 目录损坏等）：500 + 错误摘要
        return _error(500, f"recover 失败：{exc}")
    if not isinstance(node, Agent):
        return _error(404, "unknown agent")   # Workflow 根等：不是可投递目标
    return node


def _build_app(runtime: Runtime, extra_routes: tuple[tuple[str, str, object], ...] = ()) -> web.Application:
    """serve / web 共享的 HTTP 骨架（内部 API，不属稳定契约）。

    端点注册表 ``routes`` 的键与 :data:`SERVE_ENDPOINTS` **一一对应**
    （构造期断言，封闭集由此可机械化检验）；``extra_routes`` 是 web 的
    增量端点（``GET /`` / ``GET /assets/{name}``），serve 侧恒为空。
    ``(method, aiohttp 路径模板, handler)`` 三元组逐条注册。
    """

    async def _post_message(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _error(400, "请求体须为 JSON 对象")
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            return _error(400, "请求体缺 text 字段或 text 非字符串")
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        result: TurnResult = await agent.query(body["text"])   # 等待回合结果
        # message_id = 本回合首条挂树消息（query 驱动的回合即该 USER 消息）；
        # 空 turn（挂树前被阻断）无消息可指，退化为空串
        message_id = result.turn.message_ids[0] if result.turn.message_ids else ""
        return _json({"message_id": message_id, "final_text": result.final_text})

    async def _list_agents(request: web.Request) -> web.Response:
        # 名录懒读共享辅助（与 repl /agents 同口径）；mtime 转 ISO 字符串输出
        items = []
        for rec in _list_agent_records(runtime):
            items.append({
                "agent_id": rec["agent_id"],
                "agent_type": rec["agent_type"],
                "parent_agent_id": rec["parent_agent_id"],
                "created_at": rec["created_at"],
                "active": rec["active"],
                "last_reply": rec["last_reply"],
                "modified_at": (
                    datetime.fromtimestamp(rec["mtime"]).isoformat()
                    if rec["mtime"] is not None else None
                ),
            })
        return _json(items)

    async def _create_agent(request: web.Request) -> web.Response:
        if request.can_read_body and (await request.text()).strip():
            try:
                body = await request.json()
            except json.JSONDecodeError:
                return _error(400, "请求体须为 JSON 对象")
        else:
            body = {}   # 空 body 合法：缺省类型 = 项目默认主 Agent
        if not isinstance(body, dict):
            return _error(400, "请求体须为 JSON 对象")
        agent_type = body.get("agent_type")
        args = body.get("args", {})
        if agent_type is not None and not isinstance(agent_type, str):
            return _error(400, "agent_type 须为字符串")
        if not isinstance(args, dict):
            return _error(400, "args 须为对象")
        if agent_type is None:
            # 缺省类型 = 项目默认主 Agent（共享辅助，与 repl 未绑定态隐式创建同口径）；
            # 池无根记录 → 类型无法确定，视为创建参数错误映射 400
            agent_type = _default_agent_type(runtime)
            if agent_type is None:
                return _error(400, "无法确定默认 Agent 类型（池无根记录）")
        try:
            agent = await runtime.create_agent(agent_type, **args)
        except KeyError:
            return _error(400, f"未知 agent_type：{agent_type!r}")
        except Exception as exc:
            # setup 抛异常：创建管线在注册前先跑 setup，池无半注册实例
            return _error(500, f"{type(exc).__name__}: {exc}")
        return _json({"agent_id": agent.node_id}, status=201)

    async def _get_messages(request: web.Request) -> web.Response:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        # 当前 head 上溯链（渲染对话用），输出按时间正序（根 → head）
        chain: list[Message] = []
        mid = agent.current_head_id
        while mid is not None:
            msg = agent._messages.get(mid)
            if msg is None:
                break   # 孤儿链断点：到断点即终止（与 _assemble_context 同口径）
            chain.append(msg)
            mid = msg.parent_id
        return _json([to_record(m) for m in reversed(chain)])

    async def _stream(request: web.Request) -> web.StreamResponse:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        resp = web.StreamResponse(status=200, headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
        })
        await resp.prepare(request)
        # 钩子订阅 → SSE 事件的转发桥：handler 是同步纯观察（put_nowait 不
        # 阻塞回合），事件经队列串行化写出；owner 每连接唯一，断开时按
        # owner 精确摘除，不误删其他连接的订阅
        queue: asyncio.Queue[tuple[str, object] | None] = asyncio.Queue()
        owner = f"serve-sse:{uuid4().hex}"

        def _on_delta(host: Agent, delta: ProviderDelta) -> ProviderDelta:
            if delta.kind == "text" and delta.text:
                queue.put_nowait(("delta", delta.text))
            return delta

        def _on_append(host: Agent, msg: Message) -> Message:
            queue.put_nowait(("message", to_record(msg)))
            return msg

        def _on_turn(host: Agent, turn: TurnContext) -> TurnContext:
            # after_turn dispatch 时 TurnResult 尚未组装，复用同一聚合函数
            # 现场取 status
            queue.put_nowait(("turn_end", {"status": build_turn_result(turn, host).status}))
            return turn

        def _on_destroy(host: Agent, _value: object = None) -> None:
            queue.put_nowait(None)   # Agent destroy：收尾哨兵，正常结束本连接

        agent.hooks.on_provider_delta["_turn"](_on_delta, by=owner)   # 只看主 Turn 流式
        agent.hooks.after_turn_append(_on_append, by=owner)
        agent.hooks.after_turn(_on_turn, by=owner)
        agent.hooks.after_destroy(_on_destroy, by=owner)
        get_task = asyncio.ensure_future(queue.get())
        try:
            while True:
                done, _ = await asyncio.wait({get_task}, timeout=_SSE_HEARTBEAT_INTERVAL)
                if not done:
                    # 心跳注释行：SSE 无事件时的保活，同时承担断线检测
                    # （对等端关闭后 write 抛 ConnectionError → except 收尾）
                    await resp.write(b": keepalive\n\n")
                    continue
                item = get_task.result()
                if item is None:
                    break   # Agent destroy 收尾哨兵：正常结束本连接
                name, data = item
                payload = json.dumps(data, ensure_ascii=False, default=str)
                await resp.write(f"event: {name}\ndata: {payload}\n\n".encode("utf-8"))
                get_task = asyncio.ensure_future(queue.get())
        except (ConnectionError, asyncio.CancelledError):
            pass   # 客户端断开 / shutdown 清理：订阅移除走 finally
        finally:
            get_task.cancel()
            for hook_point in (
                agent.hooks.on_provider_delta, agent.hooks.after_turn_append,
                agent.hooks.after_turn, agent.hooks.after_destroy,
            ):
                hook_point.remove_by_owner(owner)
        return resp

    async def _cancel(request: web.Request) -> web.Response:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        if agent.current_turn is None:
            return _json({"status": "idle"})   # 无活跃回合：幂等
        await agent.cancel()   # 协作式取消当前回合
        return _json({"status": "cancelled"})

    async def _snapshot(request: web.Request) -> web.Response:
        # dataclasses.asdict 展开嵌套快照结构，datetime 等经 default=str 兜底
        return _json(dataclasses.asdict(runtime.snapshot()))

    async def _healthz(request: web.Request) -> web.Response:
        return _json({"status": "ok"})

    routes = {
        "POST /agents/<agent-id>/message": ("POST", "/agents/{agent_id}/message", _post_message),
        "GET /agents": ("GET", "/agents", _list_agents),
        "POST /agents": ("POST", "/agents", _create_agent),
        "GET /agents/<agent-id>/messages": ("GET", "/agents/{agent_id}/messages", _get_messages),
        "GET /agents/<agent-id>/stream": ("GET", "/agents/{agent_id}/stream", _stream),
        "POST /agents/<agent-id>/cancel": ("POST", "/agents/{agent_id}/cancel", _cancel),
        "GET /snapshot": ("GET", "/snapshot", _snapshot),
        "GET /healthz": ("GET", "/healthz", _healthz),
    }
    assert tuple(routes) == SERVE_ENDPOINTS, "端点注册表必须与 SERVE_ENDPOINTS 一一对应"
    app = web.Application(middlewares=[_error_middleware])
    for method, path, handler in (*routes.values(), *extra_routes):
        app.router.add_route(method, path, handler)
    return app


async def _serve_runtime(runtime: Runtime, app: web.Application, host: str, port: int) -> int:
    """绑定 ``host:port`` 并把 HTTP server 的生命周期挂在 Runtime 上
    （内部 API，不属稳定契约）。

    端口绑定失败 → stderr + 先 ``runtime.shutdown()`` 再返回
    ``EXIT_RUNTIME_ERROR``（此时信号处理器已装、Runtime 已活，shutdown
    不依赖 HTTP 骨架就绪）；正常路径 ``await runtime`` 阻塞至 shutdown，
    唤醒后 ``runner.cleanup()`` 取消残余连接（等待中的 POST / SSE 随之
    收尾，不承诺返回结果）再返回 ``EXIT_OK``。
    """
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, host, port).start()
    except OSError as exc:
        print(f"端口绑定失败（{host}:{port}）：{exc}", file=sys.stderr)
        await runner.cleanup()
        await runtime.shutdown()
        return EXIT_RUNTIME_ERROR
    await runtime   # 阻塞至 shutdown（HTTP server 生命周期挂在 Runtime 上）
    await runner.cleanup()
    return EXIT_OK


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
    定义的**封闭固定端点集**（八条，REST 复数风格：消息投递 /
    列举 / 创建 / 消息视图 / SSE 流 / 取消 / 快照 / 健康检查）。
    无前端，供程序调用。

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
      ``flowing.interfaces.web.cmd_web`` 经私有骨架 :func:`_build_app`
      复用本模块全部端点（web 增量端点经 ``extra_routes`` 传入）

    .. seealso::

        :data:`SERVE_ENDPOINTS`、:func:`cmd_web`
        :meth:`flowing.agent.Agent.enqueue_message`、
        :meth:`flowing.agent.Agent.query`
        :meth:`flowing.runtime.Runtime.snapshot`
    """
    # 第 1 步：launch 拿 Runtime 并安装信号处理器；launch 失败按包
    # docstring 退出码约定走 EXIT_RUNTIME_ERROR
    try:
        runtime = await launch(path, main_file=main_file, **kwargs)
    except Exception as exc:
        print(f"launch 阶段失败：{exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    _install_signal_handlers(runtime)
    # 第 2–10 步：端点注册表建 app（与 SERVE_ENDPOINTS 一一对应），绑定
    # host:port 开始服务，await runtime 阻塞至 shutdown
    return await _serve_runtime(runtime, _build_app(runtime), host, port)
