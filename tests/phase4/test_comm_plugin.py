"""阶段 4 comm 插件面测试（W31）：测试清单 T74–T78。

双层启用（T74 真 Runtime 安装面 + 未装零开销）、双端信号（T75）、
fnmatch pattern 过滤（T76）、未启用 Agent 零开销不变量（T77）、
端点冲突与 destroy 清理（T78）。
"""

from __future__ import annotations

import asyncio

import pytest

from flowing.errors import (
    DuplicateEndpointError,
    MissingProvideError,
    SignalDeliveryError,
)
from flowing.plugins.comm import CommPlugin, Communication, communication_key

from comm_support import (
    CommAgent,
    HarnessRuntime,
    SimpleAgent,
    make_comm_harness,
    make_runtime,
)


async def test_t74_install_surface_and_zero_cost_without_plugin(tmp_path):
    """T74：新 Runtime use(CommPlugin()) → inject 得 Communication 实例且
    端点表为空；未安装时 use_comm(self) 抛 MissingProvideError。"""
    runtime = make_runtime(tmp_path)
    runtime.use(CommPlugin())
    try:
        bus = runtime.inject(communication_key)
        assert isinstance(bus, Communication)
        assert bus._endpoints == {}
    finally:
        await runtime.shutdown()

    # 未安装插件 → setup() 中的 use_comm 在 inject 处断（双层启用零开销）
    harness = HarnessRuntime(tmp_path / "no-plugin")
    harness.register_agent_type("comm-agent", CommAgent)
    with pytest.raises(MissingProvideError):
        await harness.create_agent("comm-agent", start_loop=False)


async def test_t75_two_agent_signal_roundtrip(tmp_path):
    """T75：A/B 均 use_comm（端点 "a"/"b"）→ A.comm_handler.send("b",
    "ping", {}) → B 的 on_signal["ping"] handler 被调用，信封 sender=="a"。"""
    runtime, _bus = make_comm_harness(tmp_path)
    a = await runtime.create_agent("comm-agent", comm_name="a")
    b = await runtime.create_agent("comm-agent", comm_name="b")
    try:
        got = []
        done = asyncio.Event()

        @b.hooks.on_signal["ping"]
        def _(agent, envelope):
            got.append(envelope)
            done.set()
            return envelope

        a.comm_handler.send("b", "ping", {"hello": 1})
        await asyncio.wait_for(done.wait(), 1.0)
        (env,) = got
        assert env.sender == "a" and env.type == "ping"
        assert env.payload == {"hello": 1}
    finally:
        await a.destroy()
        await b.destroy()


async def test_t76_fnmatch_pattern_filter(tmp_path):
    """T76：handler 注册为 on_signal["pi*"] → type="ping" 触发、
    type="other" 不触发（fnmatch 过滤）。

    （spec 测试案例字面的 pattern ``"pe*"`` 按 fnmatch 并不匹配
    ``"ping"``——过滤行为才是契约意图，此处取真实匹配的 pattern。）
    """
    runtime, _bus = make_comm_harness(tmp_path)
    a = await runtime.create_agent("comm-agent", comm_name="a")
    b = await runtime.create_agent("comm-agent", comm_name="b")
    try:
        got = []
        done = asyncio.Event()

        @b.hooks.on_signal["pi*"]
        def _(agent, envelope):
            got.append(envelope)
            done.set()
            return envelope

        a.comm_handler.send("b", "ping", {})
        await asyncio.wait_for(done.wait(), 1.0)
        a.comm_handler.send("b", "other", {})
        await asyncio.sleep(0.05)   # 给后台 dispatch 充分时间（不应再有捕获）
        assert [env.type for env in got] == ["ping"]
    finally:
        await a.destroy()
        await b.destroy()


async def test_t77_agent_without_use_comm_is_invisible(tmp_path):
    """T77：Agent 未调 use_comm → 无 comm_handler 属性；向其 node_id
    发信号抛 SignalDeliveryError（零开销不变量）。"""
    runtime, bus = make_comm_harness(tmp_path)
    runtime.register_agent_type("plain-agent", SimpleAgent)
    agent = await runtime.create_agent("plain-agent")
    try:
        assert not hasattr(agent, "comm_handler")
        assert "on_signal" not in agent.hooks._hook_points
        assert "on_event" not in agent.hooks._hook_points
        assert agent.node_id not in bus._endpoints
        with pytest.raises(SignalDeliveryError):
            bus.send(sender="test", target=agent.node_id, type="t", payload={})
    finally:
        await agent.destroy()


async def test_t78_endpoint_conflict_and_destroy_cleanup(tmp_path):
    """T78：端点名被其他 Agent 占用 → use_comm 在 setup() 期抛
    DuplicateEndpointError；agent.destroy() 后端点/订阅/pending futures
    全部清理（before_destroy handler）。"""
    runtime, bus = make_comm_harness(tmp_path)
    a = await runtime.create_agent("comm-agent", comm_name="taken")
    try:
        # 同名端点被占用 → 第二个 Agent 的 setup 期即暴露
        with pytest.raises(DuplicateEndpointError):
            await runtime.create_agent("comm-agent", comm_name="taken")
        assert bus._endpoints["taken"] is not None   # 原映射不变

        # 造一个 pending request 与一条订阅，验证 destroy 全清理
        h_b = bus.create_handle("b")   # 永不回复
        pending = asyncio.create_task(
            a.comm_handler.request("b", "q", {}, timeout=None))
        await asyncio.sleep(0)
        a.comm_handler.subscribe("topic", lambda env: None)

        await a.destroy()
        assert "taken" not in bus._endpoints
        assert bus._subscriptions == {}
        with pytest.raises(asyncio.CancelledError):
            await pending
        h_b.destroy()
    finally:
        if "taken" in bus._endpoints:   # 失败路径兜底清理
            await a.destroy()


async def test_use_comm_idempotent_handle_reuse(tmp_path):
    """use_comm 幂等防御条款（R14）：同实例同端点重复调用 → 复用已注册
    句柄，不重复注册端点、不重复挂 before_destroy 清理 handler。"""
    from flowing.plugins.comm import use_comm

    runtime, bus = make_comm_harness(tmp_path)
    a = await runtime.create_agent("comm-agent", comm_name="a")
    try:
        first_handle = a.comm_handler
        cleanup_before = [e for e in a.hooks.before_destroy if e.by == "comm"]
        use_comm(a, name="a")   # 同实例同端点二次启用
        assert a.comm_handler is first_handle            # 复用同一句柄
        assert list(bus._endpoints).count("a") == 1
        cleanup_after = [e for e in a.hooks.before_destroy if e.by == "comm"]
        assert len(cleanup_after) == len(cleanup_before)  # 清理 handler 不叠加
        assert "on_signal" in a.hooks._hook_points        # 钩子点仍在（幂等声明）
    finally:
        await a.destroy()
