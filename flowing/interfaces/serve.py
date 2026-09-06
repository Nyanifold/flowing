"""``flowing.interfaces.serve`` —— HTTP API 服务子命令（``serve``，纯 API 无前端）。

参数分层、退出码与信号处理的总约定见 ``flowing.interfaces`` 包 docstring。
"""

import asyncio
import dataclasses
import json
import sys
from datetime import datetime
from uuid import uuid4

from aiohttp import web

from flowing.agent import Agent, TurnContext, TurnResult, build_turn_result
from flowing.interfaces.controls import slash_lines
from flowing.message import Message, TextBlock, to_record
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
    "POST /agents/<agent-id>/abort",
    "POST /agents/<agent-id>/pause",
    "POST /agents/<agent-id>/resume",
    "POST /agents/<agent-id>/rewind",
    "GET /agents/<agent-id>/model",
    "PATCH /agents/<agent-id>/model",
    "GET /agents/<agent-id>/models",
    "GET /agents/<agent-id>/status",
    "GET /agents/<agent-id>/tasks",
    "POST /agents/<agent-id>/tasks/<task-id>/cancel",
    "GET /agents/<agent-id>/export",
    "GET /agents/<agent-id>/tree",
    "POST /agents/<agent-id>/command",
    "PATCH /agents/<agent-id>",
    "GET /help",
    "GET /snapshot",
    "GET /healthz",
)
"""默认 serve 暴露的 HTTP 端点封闭集（共 :data:`SERVE_ENDPOINTS` 所列条数）：
serve 是纯 API 的冷启动
观察窗口，固定端点集保证行为可预测，不接受开放注册任意 endpoint；
集合之外的任何路径返回 ``404``。

风格约定：REST 集合资源式，集合一律复数 ``/agents``——集合
（``GET /agents`` 列出 / ``POST /agents`` 创建，id 由服务端分配故创建
走 POST 集合）+ 成员（``/agents/<id>/...`` 子资源动作）。

端点语义：

- ``POST /agents/<agent-id>/message``：向指定 Agent 投递一条 USER
  消息并等待回合结果（见 :func:`cmd_serve` 的端点契约）。
- ``GET /agents``：列出池名录全部 Agent（含休眠记录；每项
  ``agent_id`` + 最后一次回复前缀 + 最后修改时间；数据源与懒读规则
  同 REPL ``/agents``）。
- ``POST /agents``：创建根 Agent（body ``{"agent_type"?: str,
  "args"?: dict}``，缺省类型 = 项目默认主 Agent，同 REPL 未绑定态
  发消息的隐式创建）。
- ``GET /agents/<agent-id>/messages``：当前 head 上溯链的消息视图
  （渲染对话用；休眠 id 经 ``get_agent`` 现场恢复后读）。
- ``GET /agents/<agent-id>/stream``：SSE 长连接（生成过程流式推送，
  事件序列见 :func:`cmd_serve` 的端点契约）。
- ``POST /agents/<agent-id>/cancel``：协作式取消当前回合
  （``agent.cancel()``）；无活跃回合幂等。
- ``GET /snapshot``：返回 ``runtime.snapshot()`` 的 JSON 序列化，
  只读、无副作用。
- ``GET /healthz``：健康检查，返回 ``{"status": "ok"}``；只表示
  HTTP 进程与 Runtime 存活，不做深度检查。

根选取无状态：API 层没有「默认主 Agent」或「当前选中」概念——一切
目标选择由客户端基于 ``GET /agents`` / ``GET /snapshot`` 自行完成，
「切换 Agent」是纯客户端动作（改自己请求里的 id），无服务端对应
端点。

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
    """未捕获异常一律映射 ``500 {"error": ...}`` （含 ``before_cancel``
    的 ``Intercepted`` 等直挂 ``Exception`` 的信号类），不散落 HTML
    错误页。``web.HTTPException`` 原样上抛（由 aiohttp 处理）。"""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        return _error(500, f"{type(exc).__name__}: {exc}")


async def _resolve_agent(runtime: Runtime, agent_id: str) -> Agent | web.Response:
    """带 id 端点的统一目标解析（内部 API，不属稳定契约）。

    纯 ghost（不在活体表也不在池名录）→ ``404``；休眠记录经
    ``get_agent`` 现场恢复，恢复管线抛异常 → ``500`` （含错误摘要）；
    活体表中非 Agent 节点（Workflow 等）按 ghost 同律 ``404``。

    成功返回 :class:`Agent` 实例；失败返回就绪的错误
    :class:`web.Response` （调用方 ``isinstance`` 判别后直接返回）。
    """
    if agent_id not in runtime._nodes and agent_id not in runtime._agent_pool:
        return _error(404, "unknown agent")   # 纯 ghost（对 message/messages/cancel/stream 同律）
    try:
        node = await runtime.get_agent(agent_id)   # 休眠记录现场恢复
    except Exception as exc:
        # 有记录但 recover 抛异常（session 目录损坏等）：500 + 错误摘要
        return _error(500, f"recover failed: {exc}")
    if not isinstance(node, Agent):
        return _error(404, "unknown agent")   # Workflow 根等：不是可投递目标
    return node


def _build_app(runtime: Runtime, extra_routes: tuple[tuple[str, str, object], ...] = ()) -> web.Application:
    """serve / web 共享的 HTTP 骨架（内部 API，不属稳定契约）。

    端点注册表 ``routes`` 的键与 :data:`SERVE_ENDPOINTS` 一一对应
    （构造期断言，封闭集由此可机械化检验）；``extra_routes`` 是 web 的
    增量端点（``GET /`` / ``GET /assets/{name}``），serve 侧恒为空。
    ``(method, aiohttp 路径模板, handler)`` 三元组逐条注册。
    """

    async def _post_message(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _error(400, "request body must be a JSON object")
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            return _error(400, "request body is missing a text field or text is not a string")
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
            pool_meta = runtime._agent_pool.get(rec["agent_id"], {})
            items.append({
                "agent_id": rec["agent_id"],
                "agent_type": rec["agent_type"],
                "parent_agent_id": rec["parent_agent_id"],
                "name": pool_meta.get("name"),
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
                return _error(400, "request body must be a JSON object")
        else:
            body = {}   # 空 body 合法：缺省类型 = 项目默认主 Agent
        if not isinstance(body, dict):
            return _error(400, "request body must be a JSON object")
        # id-adopt：body 带 id 且该会话已存在 → 现场恢复(get_agent)并返回 200；
        # 框架分配 id，不接受带未知 id 新建
        requested_id = body.get("id")
        if requested_id is not None:
            if not isinstance(requested_id, str) or not requested_id:
                return _error(400, "id must be a non-empty string")
            if requested_id not in runtime._nodes and requested_id not in runtime._agent_pool:
                return _error(404, "unknown agent to adopt")
            try:
                node = await runtime.get_agent(requested_id)
            except Exception as exc:
                return _error(500, f"recover failed: {exc}")
            if not isinstance(node, Agent):
                return _error(404, "unknown agent")
            return _json({"agent_id": node.node_id}, status=200)   # adopt/resume
        agent_type = body.get("agent_type")
        args = body.get("args", {})
        if agent_type is not None and not isinstance(agent_type, str):
            return _error(400, "agent_type must be a string")
        if not isinstance(args, dict):
            return _error(400, "args must be an object")
        if agent_type is None:
            # 缺省类型 = 项目默认主 Agent（共享辅助，与 repl 未绑定态隐式创建同口径）；
            # 池无根记录 → 类型无法确定，视为创建参数错误映射 400
            agent_type = _default_agent_type(runtime)
            if agent_type is None:
                return _error(400, "cannot determine the default agent type (no root record in the pool)")
        try:
            agent = await runtime.create_agent(agent_type, **args)
        except KeyError:
            return _error(400, f"unknown agent_type: {agent_type!r}")
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
            # 正文与思考各自成事件流：text → event:delta，thinking → event:thinking；
            # 事件负载带 message_id（agent 预铸的本 assistant 消息 id）供前端按行归位
            if delta.kind == "text" and delta.text:
                queue.put_nowait(("delta", {"message_id": delta.message_id,
                                            "text": delta.text}))
            elif delta.kind == "thinking" and delta.text:
                queue.put_nowait(("thinking", {"message_id": delta.message_id,
                                               "text": delta.text}))
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

    async def _abort(request: web.Request) -> web.Response:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        agent.abort_turn()   # 仅终止当前逻辑 Turn；Agent 继续消费队列
        return _json({"status": "aborted"})

    async def _pause(request: web.Request) -> web.Response:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        recursive = request.query.get("recursive", "false").lower() == "true"
        (agent.pause_recursive if recursive else agent.pause)()
        return _json({"paused": True})

    async def _resume(request: web.Request) -> web.Response:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        recursive = request.query.get("recursive", "false").lower() == "true"
        (agent.resume_recursive if recursive else agent.resume)()
        return _json({"paused": False})

    async def _rewind(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _error(400, "request body must be a JSON object")
        mid = body.get("message_id") if isinstance(body, dict) else None
        if not isinstance(mid, str) or not mid:
            return _error(400, "message_id must be a string")
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        if mid not in agent._messages:
            return _error(400, "unknown message_id")
        await agent.fork(mid)   # 消息级 fork：把 current_head 切到该历史消息
        return _json({"head": agent.current_head_id})

    async def _model_get(request: web.Request) -> web.Response:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        return _json({
            "model_tag": getattr(agent, "model_tag", None),
            "model": getattr(agent.model, "model", None),
            "provider": getattr(agent.model, "provider", None),
            "context_window": getattr(agent.model, "context_window", None),
            "max_output_tokens": getattr(agent.model, "max_output_tokens", None),
        })

    async def _model_set(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _error(400, "request body must be a JSON object")
        tag = body.get("model_tag") if isinstance(body, dict) else None
        if not isinstance(tag, str) or not tag:
            return _error(400, "model_tag must be a non-empty string")
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        try:
            agent.model_tag = tag   # 运行时切换模型 tag（下轮 provider_gen 重新解析）
        except Exception as exc:
            return _error(400, f"unknown model_tag: {tag!r} ({exc})")
        return _json({"model_tag": tag})

    async def _models(request: web.Request) -> web.Response:
        # 渲染后的可用 model tags（+ providers）；来源：runtime 已登记的 model-tags 表。
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        table = getattr(runtime, "_model_tags", None)
        tag_map = {}
        if table is not None:
            try:
                tag_map = table.tags   # {tag: model} 或模型条目表
            except Exception:
                tag_map = {}
        names = list(tag_map) if isinstance(tag_map, dict) else []
        return _json({"current": getattr(agent, "model_tag", None),
                      "model_tags": names})

    async def _status(request: web.Request) -> web.Response:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        snap = agent.snapshot(keys={"model", "paused", "message_queue", "current_turn",
                                    "executions", "context_usage"})
        return _json(dataclasses.asdict(snap))

    async def _tasks(request: web.Request) -> web.Response:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        tasks = agent.get_background_tasks()
        return _json([{"task_id": tid, "done": t.done(),
                       "cancelled": t.cancelled()}
                      for tid, t in tasks.items()])

    async def _task_cancel(request: web.Request) -> web.Response:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        ok = agent.cancel_background_task(request.match_info["task_id"])
        return _json({"status": "cancelled" if ok else "not_found"})

    async def _export(request: web.Request) -> web.Response:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        fmt = request.query.get("format", "jsonl")
        chain: list[Message] = []
        mid = agent.current_head_id
        while mid is not None:
            msg = agent._messages.get(mid)
            if msg is None:
                break
            chain.append(msg)
            mid = msg.parent_id
        chain.reverse()
        if fmt == "md":
            lines = []
            for m in chain:
                text = "".join(b.text for b in m.content if isinstance(b, TextBlock))
                lines.append(f"**{m.kind.value}**: {text}")
            return web.Response(text="\n\n".join(lines), content_type="text/markdown")
        return _json([to_record(m) for m in chain])

    async def _tree(request: web.Request) -> web.Response:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        # 整棵消息树（含分支/多根/孤儿断点），head 标记当前头
        children: dict[str | None, list[str]] = {}
        for mid in agent._messages:
            parent = agent._messages[mid].parent_id
            children.setdefault(parent, []).append(mid)

        def build(mid: str) -> dict:
            m = agent._messages[mid]
            return {"id": mid, "kind": m.kind.value,
                    "head": mid == agent.current_head_id,
                    "children": [build(c) for c in children.get(mid, [])]}

        roots = [mid for mid in agent._messages
                 if agent._messages[mid].parent_id is None
                 or agent._messages[mid].parent_id not in agent._messages]
        return _json({"head": agent.current_head_id,
                      "roots": [build(r) for r in roots]})

    async def _agent_patch(request: web.Request) -> web.Response:
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _error(400, "request body must be a JSON object")
        name = body.get("name") if isinstance(body, dict) else None
        if name is not None and not isinstance(name, str):
            return _error(400, "name must be a string")
        meta = runtime._agent_pool.setdefault(agent.node_id, {})
        meta["name"] = name
        return _json({"agent_id": agent.node_id, "name": meta.get("name")})

    async def _help(request: web.Request) -> web.Response:
        return _json({"endpoints": list(SERVE_ENDPOINTS)})

    async def _command(request: web.Request) -> web.Response:
        # 共享命令目录执行：web 输入框经此运行任意 repl 指令（与 repl 同源）
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return _error(400, "request body must be a JSON object")
        line = body.get("line") if isinstance(body, dict) else None
        if not isinstance(line, str) or not line.startswith("/"):
            return _error(400, "line must be a slash command (starting with '/')")
        agent = await _resolve_agent(runtime, request.match_info["agent_id"])
        if isinstance(agent, web.Response):
            return agent
        cmd, _, arg = line.partition(" ")
        lines = await slash_lines(cmd, arg.strip(), agent, runtime)
        return _json({"lines": lines})

    routes = {
        "POST /agents/<agent-id>/message": ("POST", "/agents/{agent_id}/message", _post_message),
        "GET /agents": ("GET", "/agents", _list_agents),
        "POST /agents": ("POST", "/agents", _create_agent),
        "GET /agents/<agent-id>/messages": ("GET", "/agents/{agent_id}/messages", _get_messages),
        "GET /agents/<agent-id>/stream": ("GET", "/agents/{agent_id}/stream", _stream),
        "POST /agents/<agent-id>/cancel": ("POST", "/agents/{agent_id}/cancel", _cancel),
        "POST /agents/<agent-id>/abort": ("POST", "/agents/{agent_id}/abort", _abort),
        "POST /agents/<agent-id>/pause": ("POST", "/agents/{agent_id}/pause", _pause),
        "POST /agents/<agent-id>/resume": ("POST", "/agents/{agent_id}/resume", _resume),
        "POST /agents/<agent-id>/rewind": ("POST", "/agents/{agent_id}/rewind", _rewind),
        "GET /agents/<agent-id>/model": ("GET", "/agents/{agent_id}/model", _model_get),
        "PATCH /agents/<agent-id>/model": ("PATCH", "/agents/{agent_id}/model", _model_set),
        "GET /agents/<agent-id>/models": ("GET", "/agents/{agent_id}/models", _models),
        "GET /agents/<agent-id>/status": ("GET", "/agents/{agent_id}/status", _status),
        "GET /agents/<agent-id>/tasks": ("GET", "/agents/{agent_id}/tasks", _tasks),
        "POST /agents/<agent-id>/tasks/<task-id>/cancel": ("POST", "/agents/{agent_id}/tasks/{task_id}/cancel", _task_cancel),
        "GET /agents/<agent-id>/export": ("GET", "/agents/{agent_id}/export", _export),
        "GET /agents/<agent-id>/tree": ("GET", "/agents/{agent_id}/tree", _tree),
        "POST /agents/<agent-id>/command": ("POST", "/agents/{agent_id}/command", _command),
        "PATCH /agents/<agent-id>": ("PATCH", "/agents/{agent_id}", _agent_patch),
        "GET /help": ("GET", "/help", _help),
        "GET /snapshot": ("GET", "/snapshot", _snapshot),
        "GET /healthz": ("GET", "/healthz", _healthz),
    }
    assert tuple(routes) == SERVE_ENDPOINTS, "endpoint registry must correspond one-to-one with SERVE_ENDPOINTS"
    app = web.Application(middlewares=[_error_middleware])
    for method, path, handler in (*routes.values(), *extra_routes):
        app.router.add_route(method, path, handler)
    return app


async def _serve_runtime(runtime: Runtime, app: web.Application, host: str, port: int) -> int:
    """绑定 ``host:port`` 并把 HTTP server 的生命周期挂在 Runtime 上
    （内部 API，不属稳定契约）。

    端口绑定失败 → stderr 报错，先 ``runtime.shutdown()`` 再返回
    :data:`EXIT_RUNTIME_ERROR` （此时信号处理器已装、Runtime 已活，
    shutdown 不依赖 HTTP 骨架就绪）；正常路径 ``await runtime`` 阻塞
    至 shutdown，唤醒后 ``runner.cleanup()`` 取消残余连接（等待中的
    POST / SSE 随之收尾，不承诺返回结果）再返回 :data:`EXIT_OK`。
    """
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, host, port).start()
    except OSError as exc:
        print(f"port binding failed ({host}:{port}): {exc}", file=sys.stderr)
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

    拉起 Runtime 后启动 HTTP 服务，暴露 :data:`SERVE_ENDPOINTS` 定义的
    封闭固定端点集（REST 复数风格：消息投递 / 列举创建 / 消息视图 /
    SSE 流 / 回合与模型控制 / 任务 / 导出 / 树 / help / 快照 / 健康检查；
    完整清单以 ``GET /help`` 为权威）。无前端，供程序调用。

    ``-a <host>`` / ``-p <port>`` 是本命令专属的 CLI 参数（在 CLI 层
    剥离为 ``host`` / ``port``，不进入子项目 ``main`` 的 kwargs；
    默认 ``127.0.0.1:8000``）。

    HTTP 层只是消息入口之一：请求经 ``agent.query()`` （打包 + 入队 +
    等待回合）与 ``runtime.create_agent()`` 等公开 API 与 Agent 交互，
    与 CLI stdin、Cron 定时器、通信扩展的 peer message 完全等价。
    服务进程内感知后台事件（其它 Agent 的回合、钩子触发等）经
    ``agent.hooks`` 挂观察 handler 实现——端点集本身不含推送通道。

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
        $ curl localhost:9000/agents/agent-.../cancel
        {"status": "cancelled"}
        $ curl localhost:9000/snapshot
        {...}
        $ curl localhost:9000/healthz
        {"status": "ok"}

    .. rubric:: 行为要点

    时序：

    1. ``runtime = await launch(path, main_file=main_file, **kwargs)``，
       安装信号处理器；``launch`` 失败 → stderr 异常摘要 +
       :data:`EXIT_RUNTIME_ERROR`。
    2. 绑定 ``host:port`` 并开始服务；随后 ``await runtime`` 直到
       shutdown（HTTP server 的生命周期挂在 Runtime 上）。
    3. shutdown 路径同 :func:`flowing.interfaces.run.cmd_run`。

    端点契约：

    - ``POST /agents/<agent-id>/message``：请求体 ``{"text": str}`` →
      以 ``str`` 调 :meth:`flowing.agent.Agent.query` （打包 USER 消息、
      入队、等待回合结果在其内部完成）→ ``200 {"message_id": str,
      "final_text": str}``。休眠 / 已销毁但有记录的 id 经
      ``get_agent`` 现场恢复后投递。API 层没有「默认主 Agent」概念，
      目标必须显式指定。
    - ``GET /agents``：→ ``200``，body 为池名录全部 Agent 的列表
      （含休眠记录；每项含 ``agent_id`` / ``agent_type`` /
      ``parent_agent_id`` / ``created_at`` / ``active`` / ``last_reply`` /
      ``modified_at``；名录与懒读规则同 REPL ``/agents``）。
    - ``POST /agents``：请求体 ``{"agent_type"?: str, "args"?: dict}``
      （空 body 合法）→ ``runtime.create_agent()`` （缺省类型 = 项目
      默认主 Agent，同 REPL 未绑定态隐式创建；池无根记录 → ``400``）
      → ``201 {"agent_id": str}``。创建失败不残留半注册实例。
    - ``GET /agents/<agent-id>/messages``：→ ``200``，body 为当前
      head 上溯链的消息列表（渲染对话用；休眠 id 同样经
      ``get_agent`` 恢复）。
    - ``GET /agents/<agent-id>/stream`` （SSE 长连接）：订阅指定
      Agent 的钩子并逐事件推送——``on_provider_delta`` 的正文 →
      ``event: delta`` （流式正文），思考增量 → ``event: thinking``
      （adapter 真流式时逐段推送）；``after_turn_append`` →
      ``event: message`` （新消息挂树，工具调用与结果、steer 注入等
      对前端可见，data 为消息 JSON）；``after_turn`` →
      ``event: turn_end`` （回合收尾，data 含 ``TurnResult.status``）。
      连接断开 / Agent destroy → 订阅清理。一个 POST /message 的等待
      期与 SSE 流可以并存。
    - ``POST /agents/<agent-id>/cancel``：→ ``agent.cancel()``
      协作式取消当前回合 → ``200 {"status": "cancelled"}``；无活跃
      回合时幂等 ``200 {"status": "idle"}``。
    - ``GET /snapshot``：→ ``200``，body 为 ``runtime.snapshot()``
      的 JSON 序列化（只读视图）。
    - ``GET /healthz``：→ ``200 {"status": "ok"}``。

    错误映射与边缘情况：

    - 纯 ghost id（不在活体表也不在池名录）→ ``404``，body
      ``{"error": "unknown agent"}`` （对 message / messages / cancel /
      stream 四个带 id 端点同律；休眠但有记录的 id 不走 ``404``——
      ``get_agent`` 现场恢复）。活体表中非 Agent 节点（Workflow 等）
      按 ghost 同律 ``404``。
    - 请求体缺 ``text`` 字段或 ``text`` 非字符串 → ``400``。
    - ``POST /agents`` 的 ``agent_type`` 未注册 → ``400``；setup 抛
      异常 → ``500`` （实例不残留，池无半注册）。
    - 端点集之外的路径 → ``404``。
    - 端口绑定失败 → 打印错误到 stderr，返回
      :data:`EXIT_RUNTIME_ERROR` （Runtime 已 launch 成功的也要先
      shutdown 再退出）。
    - 回合进行中服务收到 shutdown：等待中的 POST 连接随 destroy 被
      取消，不承诺返回结果。

    :param path: 子项目路径（普通文件系统路径）。
    :param main_file: 替代的入口 main 文件（可选，经 CLI ``-m`` 传入）。
    :param host: 监听地址，默认 ``127.0.0.1``。
    :param port: 监听端口，默认 ``8000``。
    :param kwargs: 透传给 ``launch`` 与子项目 ``main`` 的 ``--key
        value`` 参数。
    :return: :data:`EXIT_OK` （正常关闭）或
        :data:`EXIT_RUNTIME_ERROR` （``launch`` 失败或端口绑定失败）。

    .. seealso::

        :data:`SERVE_ENDPOINTS`、:func:`flowing.interfaces.web.cmd_web`
        :meth:`flowing.agent.Agent.query`
        :meth:`flowing.runtime.Runtime.snapshot`
    """
    # 第 1 步：launch 拿 Runtime 并安装信号处理器；launch 失败按包
    # docstring 退出码约定走 EXIT_RUNTIME_ERROR
    try:
        runtime = await launch(path, main_file=main_file, **kwargs)
    except Exception as exc:
        print(f"launch failed: {exc}", file=sys.stderr)
        return EXIT_RUNTIME_ERROR
    _install_signal_handlers(runtime)
    # 第 2–10 步：端点注册表建 app（与 SERVE_ENDPOINTS 一一对应），绑定
    # host:port 开始服务，await runtime 阻塞至 shutdown
    return await _serve_runtime(runtime, _build_app(runtime), host, port)
