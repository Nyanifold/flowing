"""阶段 2：agent 池 / 归档 / 关闭与观测测试（T114–T123）。

覆盖：get_node/get_agent 的 strict 双形态与「有 key 无 value → 现场恢复」
（T114/T115）、mount 幂等（T116）、archive_agent 三档遗忘与父侧 _child_ids
清理（T117/T118）、archive_orphans 孤儿清理（T119）、shutdown/__await__
（T120/T121）、Runtime/Agent 快照（T122/T123）。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging

import pytest

from flowing.errors import FormatError

from conftest import (
    PluginStub,
    add_fake_provider,
    make_runtime,
    script_provider,
    text_response,
)


# ---------------------------------------------------------------------------
# T114–T115：池查询与现场恢复
# ---------------------------------------------------------------------------


async def test_t114_get_node(tmp_path):
    """T114：get_node 命中活实例；未注册默认 KeyError、strict=False 返回 None。"""
    runtime = make_runtime(tmp_path)
    agent = await runtime.create_agent("test-agent")
    assert runtime.get_node(agent.node_id) is agent
    assert runtime.get_node("runtime-0") is runtime
    with pytest.raises(KeyError):
        runtime.get_node("agent-ghost")
    assert runtime.get_node("agent-ghost", strict=False) is None
    await runtime.shutdown()


async def test_t115_get_agent_lazy_recover(tmp_path):
    """T115：destroy 后 get_agent 现场恢复（历史可见、node_id 不变）；
    主 recover 不递归子；完全不在池 → None / strict KeyError。"""
    runtime = make_runtime(tmp_path)
    provider = add_fake_provider(runtime)
    script_provider(provider, text_response("子代回复"))
    parent = await runtime.create_agent("test-agent")
    child = await parent.create_subagent("test-agent", name="c")
    r = await child.query("hi")
    assert r.status == "completed"
    child_id = child.node_id
    await child.destroy()
    assert child_id not in runtime._nodes
    # 「有 key 无 value → 现场恢复」
    got = await runtime.get_agent(child_id)
    assert got.node_id == child_id
    assert len(got._messages) == 2   # 历史消息可见（USER + PROVIDER）
    await parent.destroy()   # 级联销毁子实例；池 key 保留
    await runtime.recover_agent(parent.node_id)
    # 主 agent recover 不递归子：子此刻仍未实例化
    assert child_id not in runtime._nodes
    snap = runtime.snapshot()
    assert snap.agents[child_id].loaded is False
    instantiated = await runtime.get_agent(child_id)
    assert instantiated.node_id == child_id
    assert runtime.snapshot().agents[child_id].loaded is True
    # 完全不在池：默认 None；strict=True KeyError
    assert await runtime.get_agent("agent-ghost") is None
    with pytest.raises(KeyError):
        await runtime.get_agent("agent-ghost", strict=True)
    await runtime.shutdown()


# ---------------------------------------------------------------------------
# T116：mount 幂等
# ---------------------------------------------------------------------------


async def test_t116_mount_idempotent(tmp_path):
    """T116：指定 id 且在池中 → mount 走 recover 分支（不解析文件内容）、
    node_id 不变；agent_id=None 的 .fya 新建根经编译装配真实创建（阶段 3
    接缝已接通）。"""
    runtime = make_runtime(tmp_path)
    provider = add_fake_provider(runtime)
    script_provider(provider, text_response("挂载前"))
    agent = await runtime.create_agent("test-agent", agent_id="agent-main", tag="v1")
    await agent.query("hi")
    await agent.destroy()
    (tmp_path / "root.fya").write_text("name: root\n", encoding="utf-8")   # recover 分支不解析内容
    mounted = await runtime.mount("@/root.fya", agent_id="agent-main")
    assert mounted.node_id == "agent-main"
    assert len(mounted._messages) == 2   # 消息树完好
    # 路径不存在 → FileNotFoundError
    with pytest.raises(FileNotFoundError):
        await runtime.mount("@/nope.fya")
    # agent_id=None 的 .fya 新建根：编译装配现场合成 RootAgent 并真实创建
    new_root = await runtime.mount("@/root.fya")
    assert new_root.node_id != "agent-main" and new_root._parent_id == runtime.node_id
    assert type(new_root).__name__ == "RootAgent"
    assert new_root.node_id in runtime._nodes
    await runtime.shutdown()


# ---------------------------------------------------------------------------
# T117–T119：归档与孤儿清理
# ---------------------------------------------------------------------------


async def test_t117_archive_agent_subtree(tmp_path):
    """T117：三级子树归档——_nodes/池/core 名录均移除、session 留档、
    get_agent 返回 None；已 destroy 的池条目同样移除；双来源不命中 KeyError。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    a = await runtime.create_agent("test-agent")
    b = await a.create_subagent("test-agent", name="b")
    c = await b.create_subagent("test-agent", name="c")
    ids = {a.node_id, b.node_id, c.node_id}
    archived = await runtime.archive_agent(a.node_id)
    assert set(archived) == ids
    for nid in ids:
        assert nid not in runtime._nodes
        assert nid not in runtime._agent_pool
        assert (runtime._persist_dir / nid).exists()   # session 目录保留（留档）
    core_agents = runtime.states["core"].get("agents", [])
    assert not ids & set(core_agents)   # core 名录不含三者
    assert await runtime.get_agent(b.node_id) is None
    with pytest.raises(KeyError):
        await runtime.recover_agent(b.node_id)   # 归档后以池为准：池中无即不可恢复
    # 已 destroy 的 B 池条目同样被移除
    a2 = await runtime.create_agent("test-agent")
    b2 = await a2.create_subagent("test-agent", name="b2")
    await b2.destroy()
    archived2 = await runtime.archive_agent(a2.node_id)
    assert b2.node_id in archived2
    assert b2.node_id not in runtime._agent_pool
    # 双来源均不命中 → KeyError
    with pytest.raises(KeyError):
        await runtime.archive_agent("agent-ghost")
    await runtime.shutdown()


async def test_t118_archive_cleans_parent_child_ids(tmp_path):
    """T118：归档后父存活 → 父侧 _child_ids 条目移除并写透。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    parent = await runtime.create_agent("test-agent")
    child = await parent.create_subagent("test-agent", name="bee")
    assert parent.child_ids["bee"] == child.node_id
    await parent._core_state._store.drain()
    await runtime.archive_agent(child.node_id)
    assert "bee" not in parent.child_ids
    assert parent._core_state["child_ids"] == {}   # 写透整表（core 袋，D1）
    await parent._core_state._store.drain()
    # 落盘验证：core.jsonl 末条 child_ids 记录为空表
    lines = (parent._session_dir / "core.jsonl").read_text(encoding="utf-8").splitlines()
    child_ids_records = [
        json.loads(line) for line in lines
        if '"child_ids"' in line and json.loads(line).get("key") == "child_ids"
    ]
    assert child_ids_records[-1]["value"] == {}
    await runtime.shutdown()


async def test_t119_archive_orphans(tmp_path):
    """T119：parent 悬空的池条目被递归归档（session 保留）；无孤儿 → [] 幂等。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    root = await runtime.create_agent("test-agent")   # 正常根（父为 runtime-0）
    orphan = await runtime.create_agent("test-agent", parent_id="workflow-ghost")
    orphan_child = await orphan.create_subagent("test-agent", name="oc")
    await orphan.destroy()
    await orphan_child.destroy()
    archived = await runtime.archive_orphans()
    # 孤儿及其子树一并归档；正常根不受影响
    assert set(archived) == {orphan.node_id, orphan_child.node_id}
    assert orphan.node_id not in runtime._agent_pool
    assert orphan_child.node_id not in runtime._agent_pool
    assert root.node_id in runtime._agent_pool
    assert (runtime._persist_dir / orphan.node_id).exists()   # session 保留
    assert await runtime.archive_orphans() == []   # 幂等
    await runtime.shutdown()


# ---------------------------------------------------------------------------
# T120–T121：shutdown / __await__
# ---------------------------------------------------------------------------


async def test_bootstrap_materializes_persist_dir_for_plugin_state(tmp_path, monkeypatch):
    """疑虑①回归：默认 persist 路径 + 插件写全局状态 + 全程无 agent ——
    构造期只读引导不建目录；use() 引导解锁时建目录；写透不失败、store 不 poison。"""
    monkeypatch.chdir(tmp_path)   # 默认 persist 路径 = <cwd>/.flowing，隔离到 tmp
    runtime = make_runtime(tmp_path, persist=False, models=False,
                           register_default_type=False)
    assert runtime._persist_dir == tmp_path / ".flowing"
    assert not runtime._persist_dir.exists()   # __init__ 只读（注册即 replay 不建目录）
    runtime.use(PluginStub(
        "plug", on_install=lambda rt: rt.register_state("plug")))
    assert runtime._persist_dir.exists()   # use() 有插件即建持久化根
    runtime.states["plug"].k = 1   # 开启即 replay，写透不失败（此前这里会 drain poison）
    await runtime.states["plug"]._store.drain()
    assert runtime.states["plug"]._store._poisoned is None
    assert '"k"' in (runtime._persist_dir / "plug.jsonl").read_text(encoding="utf-8")
    await runtime.shutdown()


async def test_t120_shutdown_and_await(tmp_path):
    """T120：await runtime 阻塞至 shutdown；解除阻塞时 Agent 已 destroy；
    重复 shutdown 幂等；空 Runtime 同样可 await/shutdown。"""
    runtime = make_runtime(tmp_path)
    provider = add_fake_provider(runtime)
    script_provider(provider, text_response("在"))
    agent = await runtime.create_agent("test-agent")
    await agent.query("hi")

    async def _wait(rt):
        await rt

    waiter = asyncio.create_task(_wait(runtime))
    await asyncio.sleep(0)
    assert not waiter.done()
    await runtime.shutdown()
    await asyncio.wait_for(waiter, 2)   # 解除阻塞
    assert agent.node_id not in runtime._nodes   # destroy 完成于事件置位前
    await runtime.shutdown()   # 幂等
    # 空 Runtime（无 Agent）
    empty = make_runtime(tmp_path / "empty")
    waiter2 = asyncio.create_task(_wait(empty))
    await asyncio.sleep(0)
    await empty.shutdown()
    await asyncio.wait_for(waiter2, 2)


async def test_t121_shutdown_plugin_order_and_resilience(tmp_path, caplog):
    """T121：插件 shutdown() 中写全局状态仍生效（视图关闭在插件收尾之后）；
    单插件收尾异常记日志后继续后续收尾。"""
    events: list[str] = []
    holder: dict = {}

    def _install_writer(rt):
        rt.register_state("p1")
        holder["rt"] = rt

    def _shutdown_writer():
        holder["rt"].states["p1"].done = True   # 插件收尾中写全局状态（视图尚未关闭）
        events.append("p1")

    def _shutdown_bad():
        events.append("bad")
        raise RuntimeError("收尾炸了")

    def _shutdown_p2():
        events.append("p2")

    runtime = make_runtime(tmp_path)
    runtime.use(
        PluginStub("p1", on_install=_install_writer, on_shutdown=_shutdown_writer),
        PluginStub("bad", on_shutdown=_shutdown_bad),
        PluginStub("p2", on_shutdown=_shutdown_p2),
    )
    with caplog.at_level(logging.ERROR, logger="flowing.runtime"):
        await runtime.shutdown()
    assert events == ["p1", "bad", "p2"]   # 按 install 顺序逐个收尾，异常后继续
    assert "收尾异常" in caplog.text   # 单插件异常记日志
    assert runtime.states["p1"].done is True   # 收尾中的全局状态写生效
    # 持久化验证：p1.jsonl 落盘含 done=true
    text = (runtime._persist_dir / "p1.jsonl").read_text(encoding="utf-8")
    assert '"done"' in text and "true" in text


# ---------------------------------------------------------------------------
# T122–T123：快照
# ---------------------------------------------------------------------------


async def test_t122_runtime_snapshot(tmp_path):
    """T122：nodes 含 runtime-0（parent_id None）、根 Agent parent_id 为
    runtime-0；_provided 值内容不进快照；keys 过滤；JSON 可序列化。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    runtime.use(PluginStub("cron"))
    runtime.provide("api_secret", "sk-topsecret")   # 敏感值（测试用假值）
    agent = await runtime.create_agent("test-agent")
    snap = runtime.snapshot()
    assert snap.nodes["runtime-0"].parent_id is None
    assert snap.nodes["runtime-0"].type == "runtime"
    assert snap.nodes[agent.node_id].parent_id == "runtime-0"
    assert snap.agents[agent.node_id].loaded is True
    assert snap.agents[agent.node_id].agent_type == "test-agent"
    assert snap.plugins == ["cron"]
    blob = json.dumps(dataclasses.asdict(snap), default=str)   # datetime 字段经 default=str 序列化
    assert "sk-topsecret" not in blob   # _provided 值内容绝不进快照（凭证边界）
    partial = runtime.snapshot(keys={"nodes"})
    assert partial.nodes is not None
    assert partial.agents is None and partial.plugins is None
    await runtime.shutdown()


async def test_t123_agent_snapshot_via_runtime(tmp_path):
    """T123（runtime 侧驱动）：新建未投递 → current_turn None、messages.count 0；
    keys 过滤只收集指定切面；全部字段 JSON 可序列化。"""
    runtime = make_runtime(tmp_path)
    add_fake_provider(runtime)
    agent = await runtime.create_agent("test-agent")
    snap = agent.snapshot()
    assert snap.current_turn is None
    assert snap.messages.count == 0
    assert snap.parent_id == "runtime-0"
    partial = agent.snapshot(keys={"messages"})
    assert partial.messages is not None
    assert partial.current_turn is None and partial.executions is None
    json.dumps(dataclasses.asdict(snap))   # 全部字段 JSON 可序列化（空闲态无 datetime 叶子）
    await runtime.shutdown()
