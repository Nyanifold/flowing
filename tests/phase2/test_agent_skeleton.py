"""阶段 2：Agent 骨架与状态族测试（T27–T29、T34–T39、T42–T43、T123 等）。

覆盖：骨架期护栏（__setattr__/__getattr__）、register_state 冲突检测、
state 视图读写与写透、get/set/delete 三域路由、watch 通道、provide/inject、
destroy 的 pending 兜底与子树递归、snapshot 观测面。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

from flowing import on
from flowing.agent import Agent, Execution, TurnContext
from flowing.errors import Intercepted, MissingProvideError, StateKeyError
from flowing.message import Message, MessageKind, TextBlock
from flowing.parsable import Parsable
from flowing.providers import Usage

from conftest import SimpleAgent, script_provider, text_response


# ---------------------------------------------------------------------------
# T34：骨架期护栏——子类在 super().__init__() 前赋普通属性
# ---------------------------------------------------------------------------


async def test_t34_preskeleton_assignment_guard(runtime, provider):
    class EarlyBird(Agent):
        system_prompt = Parsable("你是测试助手。")

        def __init__(self) -> None:
            self.early = 1   # 骨架期赋值：落普通实例属性，不递归、不炸
            super().__init__()

    agent = await runtime.create_agent(EarlyBird)
    assert agent.early == 1   # 普通实例属性
    assert "early" not in agent.state   # 不进状态袋


# ---------------------------------------------------------------------------
# T35：register_state 冲突检测
# ---------------------------------------------------------------------------


async def test_t35_register_state_conflicts(runtime, provider):
    class StateAgent(SimpleAgent):
        async def setup(self) -> None:
            self.register_state("tracker_count", 0)
            with pytest.raises(ValueError):
                self.register_state("tracker_count", 1)   # 重复声明
            with pytest.raises(ValueError):
                self.register_state("message")   # 撞方法名
            with pytest.raises(ValueError):
                self.register_state("current_head_id")   # 撞核心保留键

    await runtime.create_agent(StateAgent)   # setup 内断言全部命中即通过


# ---------------------------------------------------------------------------
# T36：state 写透落盘 + recover 恢复 + watch 触发（对照 state-ok.jsonl 行格式）
# ---------------------------------------------------------------------------


async def test_t36_state_writethrough_and_recover(runtime, provider, fixtures_dir):
    observed: list[tuple[Any, Any]] = []

    class TrackerAgent(SimpleAgent):
        @on("after_turn")
        def _count(self, turn):
            self.state.tracker_count += 1
            return turn

        async def setup(self) -> None:
            self.register_state("tracker_count", 0)

    provider.generate_fn = lambda ctx, model: None  # 占位，下方 script 覆盖
    script_provider(provider, text_response("好的"))
    agent = await runtime.create_agent(TrackerAgent)
    result = await agent.query("你好")
    assert result.status == "completed"
    assert agent.state.tracker_count == 1   # after_turn 钩子写透

    # 落盘格式对照 state-ok.jsonl 的 op 行形态（{"op","key","value"}）
    await agent._state_bag._store.drain()
    lines = (agent._session_dir / "state.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in lines if line.strip()]
    data_rows = [r for r in rows if r.get("type") != "meta"]
    assert data_rows[-1] == {"op": "set", "key": "tracker_count", "value": 1}

    # 崩溃模拟：destroy（排空屏障关库）后新实例同 session 目录 _restore
    session_dir = agent._session_dir
    await agent.destroy()
    recovered = await runtime.create_agent(TrackerAgent, session_dir=session_dir)
    await recovered._restore()
    assert recovered.state.tracker_count == 1   # 落盘值覆盖 default

    # watch("tracker_count") 照常触发（StateView 写路径通知 watcher 通道）
    recovered.watch("tracker_count", lambda new, old: observed.append((new, old)))
    recovered.state.tracker_count = 7
    await asyncio.sleep(0)
    await asyncio.sleep(0)   # fire-and-forget watcher 任务让步两轮
    assert observed == [(7, 1)]


# ---------------------------------------------------------------------------
# T37：typo 键的读路径
# ---------------------------------------------------------------------------


async def test_t37_undeclared_key_reads(agent):
    agent.register_state("tracker", 0)
    with pytest.raises(KeyError):
        agent.state["tracke"]
    with pytest.raises(AttributeError):
        agent.tracke


# ---------------------------------------------------------------------------
# T38 / T39：get / set / delete 三域路由
# ---------------------------------------------------------------------------


async def test_t38_get_three_domains(agent):
    agent.register_state("count", 0)
    agent.mode = "x"
    assert agent.get("count") == 0
    assert agent.get("mode") == "x"
    assert agent.get("nope", -1) == -1


async def test_t39_set_and_state_key_guard(agent):
    calls: list[tuple[Any, Any]] = []
    agent.register_state("count", 0)
    agent.watch("count", lambda new, old: calls.append(("state", new, old)))
    agent.watch("mode", lambda new, old: calls.append(("attr", new, old)))

    agent.set("count", 5)
    assert agent.state.count == 5
    agent.set("mode", "y")
    assert agent.mode == "y"
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert ("state", 5, 0) in calls   # 状态键写透走 StateView 的 watcher 通知
    assert ("attr", "y", None) in calls   # 实例属性路径 watcher 照常

    with pytest.raises(StateKeyError):
        agent.count = 9   # 状态键经实例属性语法直写 -> 防遮蔽 fail-fast
    with pytest.raises(StateKeyError):
        del agent.count

    agent.delete("count")   # 删持久值，读回退 default
    assert agent.state.count == 0
    agent._extra["note"] = "a"
    agent.set("note", "b")   # _extra 原地更新，不触发 watcher
    assert agent._extra["note"] == "b"
    agent.delete("note")
    assert "note" not in agent._extra


# ---------------------------------------------------------------------------
# T27 / T28 / T29：watch 通道
# ---------------------------------------------------------------------------


async def test_t27_watch_fires_with_snapshot(agent):
    calls: list[tuple[Any, Any]] = []
    agent.locale = "zh"
    agent.watch("locale", lambda new, old: calls.append((new, old)))
    agent.locale = "en"
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert calls == [("en", "zh")]


async def test_t28_watcher_exception_isolated(agent, caplog):
    agent.hooks.watch("locale", lambda a, fu: 1 / 0)
    agent.locale = "zh"   # 赋值成功、无异常传播
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert agent.locale == "zh"
    assert any(r.levelname == "ERROR" for r in caplog.records)   # 异常记日志


def test_t29_watch_without_running_loop(tmp_path):
    # 无运行中 event loop：赋值成功、watcher 不触发（同步测试函数即该场景）
    from conftest import HarnessRuntime

    rt = HarnessRuntime(tmp_path)

    async def _build():
        return await rt.create_agent(SimpleAgent, start_loop=False)

    agent = asyncio.run(_build())
    calls: list = []
    agent.watch("locale", lambda new, old: calls.append(new))
    agent.locale = "zh"
    assert agent.locale == "zh"
    assert calls == []
    asyncio.run(agent.destroy())


# ---------------------------------------------------------------------------
# provide / inject / get 门面的补充覆盖（W16）
# ---------------------------------------------------------------------------


async def test_provide_inject_chain(runtime, provider):
    runtime.provide("locale", "zh")
    parent = await runtime.create_agent(SimpleAgent)
    child = await runtime.create_agent(SimpleAgent, parent_id=parent.node_id)
    parent._children[child.node_id] = child   # 手动接线生命周期子树（harness）

    assert child.inject("locale") == "zh"   # 穿透中间层命中 Runtime
    parent.provide("locale", "en")
    assert child.inject("locale") == "en"   # 就近命中父层
    with pytest.raises(MissingProvideError):
        child.inject("ghost")


# ---------------------------------------------------------------------------
# T42 / T43：destroy
# ---------------------------------------------------------------------------


async def test_t42_destroy_resolves_pending(runtime, provider):
    gate = asyncio.Event()

    async def _slow(context, model):
        await gate.wait()
        return text_response("不应到达")

    provider.generate_fn = _slow
    agent = await runtime.create_agent(SimpleAgent)
    t1 = asyncio.create_task(agent.query("一"))
    t2 = asyncio.create_task(agent.query("二"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await agent.destroy()
    r1, r2 = await asyncio.wait_for(asyncio.gather(t1, t2), 2)
    assert r1.status == "cancelled" and r2.status == "cancelled"   # 不挂起


async def test_t43_destroy_recursive(runtime, provider):
    parent = await runtime.create_agent(SimpleAgent)
    child = await runtime.create_agent(SimpleAgent, parent_id=parent.node_id)
    grand = await runtime.create_agent(SimpleAgent, parent_id=child.node_id)
    parent._children[child.node_id] = child
    child._children[grand.node_id] = grand
    ids = {parent.node_id, child.node_id, grand.node_id}

    await parent.destroy()   # 深度优先全销毁
    for nid in ids:
        assert nid not in runtime._nodes   # 活体表摘除
        assert nid in runtime._agent_pool   # 池 key 保留（destroy ≠ 删除）


# ---------------------------------------------------------------------------
# T123：snapshot 观测面
# ---------------------------------------------------------------------------


async def test_t123_snapshot_idle(agent):
    snap = agent.snapshot()
    assert snap.node_id == agent.node_id
    assert snap.parent_id == "runtime-0"
    assert snap.current_turn is None   # 新建未投递
    assert snap.messages.count == 0
    assert snap.message_queue.size == 0
    assert snap.executions == {}
    assert snap.paused is False
    json.dumps(dataclasses.asdict(snap))   # 全部字段 JSON 可序列化

    partial = agent.snapshot(keys={"messages"})
    assert partial.messages is not None and partial.messages.count == 0
    assert partial.current_turn is None and partial.model is None
    assert partial.tool_entries is None and partial.executions is None
