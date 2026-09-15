"""阶段 5 T35–T46：``serve.py``（HTTP 骨架 + 端点族 + SSE）。

全部用例经真回环端口 + httpx 客户端驱动（验收标准 2 的「curl 固定脚本」
落实为逐端点状态码与 body 关键字段断言 + SSE 事件序列断言）。
"""

from __future__ import annotations

import asyncio
import json
import socket

import httpx
import pytest

from flowing.agent import Agent
from flowing.interfaces import EXIT_OK, EXIT_RUNTIME_ERROR, _list_agent_records
from flowing.interfaces import serve as serve_mod
from flowing.interfaces.serve import SERVE_ENDPOINTS, cmd_serve
from flowing.providers import ProviderDelta

from .support import (
    free_port,
    spy_launch,
    spy_recover_agent,
    start_http,
    stop_http,
    wait_until,
)


def _hook_count(agent: Agent, *names: str) -> int:
    """指定钩子点的 handler 总数（__iter__ 遍历启用条目）。"""
    return sum(len(list(getattr(agent.hooks, name))) for name in names)


async def test_t35_create_list_message(project_ok, persist_dir, monkeypatch):
    """POST /agents（空 body）→ 201 含 agent_id；GET /agents 含该 id；
    再 POST /agents/<新id>/message 正常投递。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        r = await handle.client.post("/agents", content="")
        assert r.status_code == 201
        agent_id = r.json()["agent_id"]
        assert agent_id

        listed = (await handle.client.get("/agents")).json()
        assert agent_id in {item["agent_id"] for item in listed}

        r = await handle.client.post(f"/agents/{agent_id}/message", json={"text": "你好"})
        assert r.status_code == 200
        body = r.json()
        assert body["message_id"] and body["final_text"] == "alpha-reply"
    finally:
        await stop_http(handle, EXIT_OK)


async def test_t36_message_id_in_chain(project_ok, persist_dir, monkeypatch):
    """POST /agents/root/message 响应的 message_id 可在
    GET /agents/root/messages 中找到对应消息节点。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        r = await handle.client.post("/agents/root/message", json={"text": "你好"})
        assert r.status_code == 200
        message_id = r.json()["message_id"]

        r = await handle.client.get("/agents/root/messages")
        assert r.status_code == 200
        nodes = r.json()
        by_id = {m["id"]: m for m in nodes}
        assert message_id in by_id
        assert by_id[message_id]["kind"] == "user"   # message_id 即本回合 USER 消息
        assert any(m["kind"] == "provider" for m in nodes)
    finally:
        await stop_http(handle, EXIT_OK)


async def test_t37_ghost_404_and_healthz(project_ok, persist_dir, monkeypatch):
    """纯 ghost id：message / messages / cancel / stream 同律 404
    {"error": "unknown agent"}；随后 GET /healthz 仍 200。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        for response in [
            await handle.client.post("/agents/ghost/message", json={"text": "x"}),
            await handle.client.get("/agents/ghost/messages"),
            await handle.client.post("/agents/ghost/cancel"),
            await handle.client.get("/agents/ghost/stream"),
        ]:
            assert response.status_code == 404
            assert response.json() == {"error": "unknown agent"}
        assert (await handle.client.get("/healthz")).status_code == 200
        # 顺带固定 GET /snapshot 契约（端点集第 8 条）：200 + 快照关键字段
        snap = await handle.client.get("/snapshot")
        assert snap.status_code == 200
        assert {"nodes", "agents", "plugins"} <= set(snap.json())
    finally:
        await stop_http(handle, EXIT_OK)


async def test_t38_sse_event_sequence_and_cleanup(project_ok, persist_dir, monkeypatch):
    """GET /agents/root/stream 建 SSE：delta 事件随生成推送，收尾收到
    turn_end（data 含 status）；断开后钩子订阅被移除（handler 数回落）。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        agent = await handle.runtime.get_agent("root")
        baseline = _hook_count(agent, "on_provider_delta", "on_turn_append",
                               "after_turn", "after_destroy")
        events: list[tuple[str, object]] = []
        async with handle.client.stream("GET", "/agents/root/stream") as resp:
            assert resp.status_code == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            # 等订阅注册就位（连接建立与钩子注册是两个事件，需同步点）
            await wait_until(lambda: _hook_count(
                agent, "on_provider_delta", "on_turn_append",
                "after_turn", "after_destroy") > baseline)
            post = asyncio.create_task(
                handle.client.post("/agents/root/message", json={"text": "你好"}))
            current = None
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    current = line[len("event: "):]
                elif line.startswith("data: "):
                    events.append((current, json.loads(line[len("data: "):])))
                    if current == "turn_end":
                        break
            r = await post
            assert r.status_code == 200
        # 事件序列：delta* →（message* 可选，夹杂其间）→ turn_end 收尾
        names = [name for name, _ in events]
        assert names[-1] == "turn_end"
        assert events[-1][1]["status"] == "completed"
        first_delta = names.index("delta")
        assert first_delta >= 0 and names.index("turn_end") > first_delta
        deltas = [data for name, data in events if name == "delta"]
        assert all(isinstance(d, dict) and "text" in d and "message_id" in d
                   for d in deltas)                       # delta 带 message_id + text
        assert "".join(d["text"] for d in deltas) == "alpha-reply"   # FakeProvider 两段 delta
        assert all(d["message_id"] for d in deltas)       # 预铸 id 已随事件透出
        # 断开连接后：订阅按 owner 摘除，handler 数回落到基线
        await wait_until(lambda: _hook_count(
            agent, "on_provider_delta", "on_turn_append",
            "after_turn", "after_destroy") == baseline)
        # 再触发一回合：无推送、无异常（订阅已移除的机械化证明是计数回落）
        r = await handle.client.post("/agents/root/message", json={"text": "再来"})
        assert r.status_code == 200
    finally:
        await stop_http(handle, EXIT_OK)


async def test_t39_unknown_path_404(project_ok, persist_dir, monkeypatch):
    """端点集之外的路径 → 404（封闭集）。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        assert (await handle.client.get("/unknown")).status_code == 404
        assert (await handle.client.post("/unknown")).status_code == 404
    finally:
        await stop_http(handle, EXIT_OK)


async def test_t40_bad_body_400(project_ok, persist_dir, monkeypatch):
    """请求体缺 text 或 text 非字符串 → 400。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        assert (await handle.client.post(
            "/agents/root/message", json={})).status_code == 400
        assert (await handle.client.post(
            "/agents/root/message", json={"text": 123})).status_code == 400
        assert (await handle.client.post(
            "/agents/root/message", content="not json",
            headers={"Content-Type": "application/json"})).status_code == 400
    finally:
        await stop_http(handle, EXIT_OK)


async def test_t41_create_failures(project_ok, persist_dir, monkeypatch):
    """POST /agents：agent_type 未注册 → 400；setup 抛异常 → 500 且池
    无半注册实例。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        runtime = handle.runtime
        pool_before = set(runtime._agent_pool)

        r = await handle.client.post("/agents", json={"agent_type": "nonexistent"})
        assert r.status_code == 400

        class Boom(Agent):
            async def setup(self, **kwargs) -> None:
                raise RuntimeError("setup boom")

        runtime.register_agent_type(Boom, name="boom")
        r = await handle.client.post("/agents", json={"agent_type": "boom"})
        assert r.status_code == 500
        assert "setup boom" in r.json()["error"]

        assert set(runtime._agent_pool) == pool_before   # 无半注册实例
        assert set(runtime._nodes) == {runtime.node_id, "root"}
    finally:
        await stop_http(handle, EXIT_OK)


async def test_t42_dormant_id_recovered(project_ok, persist_dir, monkeypatch):
    """休眠但有记录的 id：经 get_agent 现场恢复后正常服务，不 404。"""
    # 第一程：让 root 留下盘上记录后关闭
    first = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    r = await first.client.post("/agents/root/message", json={"text": "第一句"})
    assert r.status_code == 200
    await stop_http(first, EXIT_OK)
    # 第二程：同 persist 重拉（池有记录、main 不重建根 → root 休眠）
    recovered = spy_recover_agent(monkeypatch)
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        assert "root" not in handle.runtime._nodes   # 尚未实例化
        r = await handle.client.post("/agents/root/message", json={"text": "第二句"})
        assert r.status_code == 200
        assert r.json()["final_text"] == "alpha-reply"
        assert recovered == ["root"]   # 走了现场恢复管线
        # 恢复后上下文延续：消息链含第一程的历史
        nodes = (await handle.client.get("/agents/root/messages")).json()
        texts = [b.get("text", "") for m in nodes for b in m["content"]]
        assert "第一句" in texts and "第二句" in texts
    finally:
        await stop_http(handle, EXIT_OK)


def _gate_provider_stream(handle, gate: asyncio.Event) -> None:
    """把 fake-a 的流式生成换成受闸门外控的慢速版（活跃回合测试用）。"""
    provider = handle.runtime.provider_registry._instances["fake-a"]

    async def _slow_stream(context, model):
        await gate.wait()
        yield ProviderDelta(kind="text", text="gated-reply", content_index=0)

    provider.stream_fn = _slow_stream


async def test_t43_cancel_idle_and_active(project_ok, persist_dir, monkeypatch):
    """无活跃回合 → 幂等 200 {"status": "idle"}；有活跃回合 →
    200 {"status": "cancelled"} 且回合协作式取消。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        r = await handle.client.post("/agents/root/cancel")
        assert r.status_code == 200 and r.json() == {"status": "idle"}

        gate = asyncio.Event()
        _gate_provider_stream(handle, gate)
        agent = await handle.runtime.get_agent("root")
        post = asyncio.create_task(
            handle.client.post("/agents/root/message", json={"text": "你好"}))
        await wait_until(lambda: agent.current_turn is not None)

        r = await handle.client.post("/agents/root/cancel")
        assert r.status_code == 200 and r.json() == {"status": "cancelled"}
        gate.set()   # 放行 provider，回合以 cancelled 收尾
        r = await asyncio.wait_for(post, timeout=10)
        assert r.status_code == 200   # 等待中的 POST 随回合收尾 resolve（cancelled）
    finally:
        await stop_http(handle, EXIT_OK)


async def test_t44_port_bind_failure(project_ok, persist_dir, monkeypatch, capsys):
    """端口绑定失败 → stderr 报错，已 launch 的 Runtime 先 shutdown，
    返回 EXIT_RUNTIME_ERROR。"""
    captured = spy_launch(monkeypatch, serve_mod)
    with socket.socket() as blocker:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        rc = await asyncio.wait_for(cmd_serve(
            str(project_ok), persist=str(persist_dir), port=port), timeout=10)
    assert rc == EXIT_RUNTIME_ERROR
    err = capsys.readouterr().err
    assert "port binding failed" in err and str(port) in err
    assert captured["runtime"]._shutdown_event.is_set()   # 先 shutdown 再退出


async def test_t45_agents_matches_repl_helper(project_ok, persist_dir, monkeypatch):
    """GET /agents 与 repl /agents 同口径：同一份名录懒读辅助
    （_list_agent_records），含休眠记录。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        runtime = handle.runtime
        r = await handle.client.post("/agents/root/message", json={"text": "你好"})
        assert r.status_code == 200
        # 造一条休眠记录：新建后 destroy（池记录保留，实例摘除）
        second = await runtime.create_agent("root")
        await second.destroy()
        assert second.node_id in runtime._agent_pool
        assert second.node_id not in runtime._nodes

        listed = (await handle.client.get("/agents")).json()
        records = {rec["agent_id"]: rec for rec in _list_agent_records(runtime)}
        assert {item["agent_id"] for item in listed} == set(records) == {
            "root", second.node_id}
        for item in listed:
            rec = records[item["agent_id"]]
            # 同口径断言：id / 最后回复前缀 / 激活标记与辅助逐条一致
            assert item["last_reply"] == rec["last_reply"]
            assert item["active"] == rec["active"]
            assert "modified_at" in item
        root_item = next(i for i in listed if i["agent_id"] == "root")
        assert root_item["last_reply"] == "alpha-reply"   # 懒读 tree.jsonl 尾部
        dormant = next(i for i in listed if i["agent_id"] == second.node_id)
        assert dormant["active"] is False   # 休眠记录在列
    finally:
        await stop_http(handle, EXIT_OK)


async def test_t46_shutdown_cancels_pending_post(project_ok, persist_dir, monkeypatch):
    """回合进行中收到 shutdown：等待中的 POST 连接随 destroy 收尾
    （不承诺返回结果），进程仍以 EXIT_OK 退出。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    gate = asyncio.Event()   # 永不放行：回合挂起直到 destroy
    _gate_provider_stream(handle, gate)
    agent = await handle.runtime.get_agent("root")
    post = asyncio.create_task(
        handle.client.post("/agents/root/message", json={"text": "你好"}))
    await wait_until(lambda: agent.current_turn is not None)

    await handle.runtime.shutdown()
    rc = await asyncio.wait_for(handle.task, timeout=10)
    assert rc == EXIT_OK
    # POST 不悬挂：要么 resolve（cancelled 回合结果），要么连接被取消
    try:
        await asyncio.wait_for(asyncio.shield(post), timeout=10)
    except (httpx.HTTPError, asyncio.CancelledError, asyncio.TimeoutError):
        raise AssertionError("等待中的 POST 未随 shutdown 收尾") from None
    gate.set()   # 防御性放行，避免遗留 await 悬挂
    await handle.client.aclose()


def test_serve_endpoints_closed_set():
    """封闭集断言：注册表键与 SERVE_ENDPOINTS 一一对应（_build_app 构造期
    assert 的直接镜像，防漂移）。"""
    from flowing.interfaces.serve import SERVE_ENDPOINTS as SE
    assert SERVE_ENDPOINTS == SE   # 与模块常量同源（防本地手抄漂移）
    assert "POST /agents/<agent-id>/abort" in SERVE_ENDPOINTS
    assert "GET /agents/<agent-id>/status" in SERVE_ENDPOINTS


async def test_v3_endpoints_smoke(project_ok, persist_dir, monkeypatch):
    """v3 新增 unary 端点冒烟：status/model/model_tags/export/abort/pause/resume/
    rewind/tasks 命中项目 agent 返回合理形状（不 500）。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        # 先产生一轮消息，得到 USER message_id 供 rewind
        r = await handle.client.post("/agents/root/message", json={"text": "你好"})
        assert r.status_code == 200
        user_mid = r.json()["message_id"]

        st = await handle.client.get("/agents/root/status")
        assert st.status_code == 200
        assert "paused" in st.json() and "current_turn" in st.json()

        model = await handle.client.get("/agents/root/model")
        assert model.status_code == 200
        assert "model_tag" in model.json()

        mtags = await handle.client.get("/agents/root/models")
        assert mtags.status_code == 200 and "model_tags" in mtags.json()

        exp = await handle.client.get("/agents/root/export?format=md")
        assert exp.status_code == 200 and "alpha-reply" in exp.text

        ab = await handle.client.post("/agents/root/abort")
        assert ab.status_code == 200 and ab.json()["status"] == "aborted"

        pu = await handle.client.post("/agents/root/pause")
        assert pu.status_code == 200 and pu.json()["paused"] is True
        re = await handle.client.post("/agents/root/resume")
        assert re.status_code == 200 and re.json()["paused"] is False

        rw = await handle.client.post("/agents/root/rewind",
                                      json={"message_id": user_mid})
        assert rw.status_code == 200 and rw.json()["head"] == user_mid

        tasks = await handle.client.get("/agents/root/tasks")
        assert tasks.status_code == 200 and isinstance(tasks.json(), list)
    finally:
        await stop_http(handle, EXIT_OK)


def _walk(node):
    yield node
    for c in node.get("children", []):
        yield from _walk(c)


async def test_v3_more_endpoints_smoke(project_ok, persist_dir, monkeypatch):
    """tree / help / PATCH name / POST id-adopt 冒烟。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        r = await handle.client.post("/agents/root/message", json={"text": "你好"})
        user_mid = r.json()["message_id"]

        tr = await handle.client.get("/agents/root/tree")
        assert tr.status_code == 200
        body = tr.json()
        assert "head" in body and "roots" in body
        ids = {n["id"] for root in body["roots"] for n in _walk(root)}
        assert user_mid in ids

        hp = await handle.client.get("/help")
        assert hp.status_code == 200
        assert "GET /agents/<agent-id>/tree" in hp.json()["endpoints"]

        rn = await handle.client.patch("/agents/root", json={"name": "首席"})
        assert rn.status_code == 200 and rn.json()["name"] == "首席"
        listed = await handle.client.get("/agents")
        assert any(i["agent_id"] == "root" and i["name"] == "首席" for i in listed.json())

        ad = await handle.client.post("/agents", json={"id": "root"})
        assert ad.status_code == 200 and ad.json()["agent_id"] == "root"   # adopt
        miss = await handle.client.post("/agents", json={"id": "no-such"})
        assert miss.status_code == 404
    finally:
        await stop_http(handle, EXIT_OK)


async def test_command_endpoint_shared(project_ok, persist_dir, monkeypatch):
    """POST /agents/{id}/command：走共享命令目录，web 可输入全部 repl 指令。"""
    handle = await start_http(monkeypatch, serve_mod, cmd_serve, project_ok, persist_dir)
    try:
        await handle.client.post("/agents/root/message", json={"text": "你好"})
        r = await handle.client.post("/agents/root/command",
                                     json={"line": "/messages"})
        assert r.status_code == 200
        body = r.json()
        assert any("alpha-reply" in ln for ln in body["lines"])
        r2 = await handle.client.post("/agents/root/command", json={"line": "/model"})
        assert r2.status_code == 200 and r2.json()["lines"]
        # 非 slash 拒绝
        r3 = await handle.client.post("/agents/root/command", json={"line": "你好"})
        assert r3.status_code == 400
    finally:
        await stop_http(handle, EXIT_OK)
